"""TM32: crash-recovery state machine for the photo evidence pipeline.

Addresses the Mac Night 04 finding (project reference written BEFORE
the evidence survived) by modeling the WHOLE pipeline as derived
states over FACTS (DB rows + FS objects), never a stored status that
can drift from reality.

FACTS (each independently observable after a crash):
  F1 photo metadata row exists          (mvp_photos)
  F2 original bytes exist on disk       (originals/ab/<sha>.jpg)
  F3 binding row exists                 (mvp_photo_originals)
  F4 evidence-link row exists           (mvp_evidence_links)
  F5 link status == 'verified'          (server-executed signature check)

STATES (derived - the state machine NEVER stores its own copy):
  S0 EMPTY               nothing yet
  S1 REGISTERED          F1 only - metadata landed, bytes never did
  S2 ORPHAN_BYTES        F2 without F1 - bytes with no owner row
  S3 STORED_UNBOUND      F1+F2, no F3 - bytes stored, binding lost in
                         the crash window
  S4 BOUND               F1+F2+F3 - the metadata-then-bytes contract
                         is COMPLETE
  S5 EVIDENCE_PENDING    S4 + F4 pending verification
  S6 EVIDENCE_FAILED     S4 + F4 explicitly failed (server ran the
                         math; it said no)
  S7 TRUSTED_SIGNED      S4 + F5 - the ONLY state that may be
                         presented as a verified signature

INVARIANTS (recovery must uphold all of them):
  I1 strict ordering: S7 => F5 => F4 => F3 => F2 => F1. A record that
     claims verified WITHOUT the underlying facts is a VIOLATION, and
     recovery reports it - it never silently repairs a trust upgrade.
  I2 bytes are sacred: recovery NEVER deletes or overwrites originals
     (an orphan is REPORTED, the operator decides).
  I3 idempotence: recover(recover(x)) == recover(x).
  I4 no upgrade without proof: a photo whose bytes are absent NEVER
     advances past S1, and a photo without a server-executed
     verification NEVER reaches S7.

The module is deliberately dependency-free and pure: the same state
derivation ports to Swift verbatim for the client-side mirror.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class PipelineState(str, Enum):
    EMPTY = "empty"
    REGISTERED = "registered"
    ORPHAN_BYTES = "orphan_bytes"
    STORED_UNBOUND = "stored_unbound"
    BOUND = "bound"
    EVIDENCE_PENDING = "evidence_pending"
    EVIDENCE_FAILED = "evidence_failed"
    TRUSTED_SIGNED = "trusted_signed"
    VIOLATION = "violation"


@dataclass(frozen=True)
class PipelineFacts:
    """The raw, independently-observable truth about one photo."""
    metadata_row: bool = False        # F1
    original_bytes_on_disk: bool = False   # F2
    bytes_hash_matches: bool | None = None  # None = not re-checked yet
    binding_row: bool = False        # F3
    evidence_link_row: bool = False   # F4
    link_verified: bool = False      # F5
    link_failed: bool = False        # F4 explicit failure


def derive_state(f: PipelineFacts) -> PipelineState:
    """Pure state derivation - the single source of truth. A VIOLATION
    is any configuration that implies a trust claim without its facts
    (checked FIRST: recovery must never paper over a real violation
    by classifying it as some benign state)."""
    # --- violations of the strict ordering (I1) ----------------------
    if f.link_verified:
        if not f.evidence_link_row or not f.binding_row:
            return PipelineState.VIOLATION
    if f.link_failed and not f.evidence_link_row:
        return PipelineState.VIOLATION
    if f.evidence_link_row and not f.binding_row:
        return PipelineState.VIOLATION
    if f.binding_row and not (f.metadata_row and f.original_bytes_on_disk):
        return PipelineState.VIOLATION

    # --- clean ladder (I4: verified needs everything under it) --------
    if f.link_verified:
        return PipelineState.TRUSTED_SIGNED
    if f.link_failed:
        return PipelineState.EVIDENCE_FAILED
    if f.evidence_link_row:
        return PipelineState.EVIDENCE_PENDING
    if f.binding_row:
        return PipelineState.BOUND
    if f.metadata_row and f.original_bytes_on_disk:
        return PipelineState.STORED_UNBOUND
    if f.original_bytes_on_disk:
        return PipelineState.ORPHAN_BYTES
    if f.metadata_row:
        return PipelineState.REGISTERED
    return PipelineState.EMPTY


@dataclass
class RecoveryAction:
    code: str            # machine-readable
    description: str
    destructive: bool = False     # recovery NEVER issues destructive acts


ACTIONS: dict[PipelineState, RecoveryAction] = {
    PipelineState.EMPTY:
        RecoveryAction("none", "nothing exists - no action"),
    PipelineState.REGISTERED:
        RecoveryAction("await_upload",
                       "metadata survived without bytes - the client "
                       "re-PUTs the original; server holds the record "
                       "open (retry is the protocol's own recovery)"),
    PipelineState.ORPHAN_BYTES:
        RecoveryAction("report_orphan_bytes",
                       "bytes with no metadata row - REPORTED to the "
                       "operator; never deleted, never rebound "
                       "blindly (content-addressed dedupe keeps them "
                       "harmless)"),
    PipelineState.STORED_UNBOUND:
        RecoveryAction("rebind",
                       "bytes verified on disk but the binding row was "
                       "lost in the crash window - recovery re-runs the "
                       "BIND step (idempotent: re-hash bytes, then "
                       "INSERT OR IGNORE the binding)"),
    PipelineState.BOUND:
        RecoveryAction("none",
                       "metadata-then-bytes contract complete - "
                       "awaiting the client's evidence-link step"),
    PipelineState.EVIDENCE_PENDING:
        RecoveryAction("reverify",
                       "link row exists with a pending status - "
                       "recovery re-runs the server-side signature "
                       "verification (idempotent, verdict overwrites "
                       "only pending)"),
    PipelineState.EVIDENCE_FAILED:
        RecoveryAction("keep_failed",
                       "the server already ran the math and it FAILED "
                       "- recovery keeps the verdict; only a NEW "
                       "client submission with corrected evidence may "
                       "clear it"),
    PipelineState.TRUSTED_SIGNED:
        RecoveryAction("none",
                       "verified signed photo - untouched by recovery"),
    PipelineState.VIOLATION:
        RecoveryAction("report_violation",
                       "trust claim without underlying facts - "
                       "OPERATOR escalation; recovery never repairs a "
                       "violation by inventing the missing rows "
                       "(that would be forging evidence)"),
}


def plan_recovery(f: PipelineFacts) -> dict:
    """The full recovery verdict for one photo: state + action. Pure -
    callers apply the actions with real side effects."""
    state = derive_state(f)
    action = ACTIONS[state]
    return {
        "state": state.value,
        "action": action.code,
        "action_description": action.description,
        "destructive": action.destructive,   # always False today, by
        "invariants": [                      # construction
            "I1 strict fact ordering (violations escalate, never patched)",
            "I2 originals never deleted or overwritten",
            "I3 idempotent: re-running yields the same plan",
            "I4 no trust upgrade without server-executed proof",
        ],
    }


# ------------------------------------------------------- Swift port spec

SWIFT_PORTABLE_STATE_MACHINE = """\
// TM32 - Swift port of the pipeline state machine (Mac mirrors the
// client-side OUTBOX half of this machine; the server half runs on
// the backend). Any divergence from the Python derivation is a bug.

enum PipelineState: String {
    case empty, registered, orphanBytes, storedUnbound, bound
    case evidencePending, evidenceFailed, trustedSigned, violation
}

struct PipelineFacts {
    let metadataRow: Bool            // F1
    let originalBytesOnDisk: Bool    // F2
    let bindingRow: Bool             // F3
    let evidenceLinkRow: Bool        // F4
    let linkVerified: Bool           // F5
    let linkFailed: Bool
}

func deriveState(_ f: PipelineFacts) -> PipelineState {
    // violations FIRST - a trust claim without facts is never benign
    if f.linkVerified && (!f.evidenceLinkRow || !f.bindingRow) {
        return .violation
    }
    if (f.linkFailed || f.evidenceLinkRow) && !f.evidenceLinkRow {
        return .violation
    }
    if f.evidenceLinkRow && !f.bindingRow { return .violation }
    if f.bindingRow && !(f.metadataRow && f.originalBytesOnDisk) {
        return .violation
    }
    if f.linkVerified { return .trustedSigned }
    if f.linkFailed { return .evidenceFailed }
    if f.evidenceLinkRow { return .evidencePending }
    if f.bindingRow { return .bound }
    if f.metadataRow && f.originalBytesOnDisk { return .storedUnbound }
    if f.originalBytesOnDisk { return .orphanBytes }
    if f.metadataRow { return .registered }
    return .empty
}

// rules to preserve in the port:
// 1. NEVER store a state that duplicates these facts - derive always
// 2. recovery actions are idempotent and never destructive
// 3. a violation is escalated, never repaired by inventing rows
"""
