"""TM07: explainable multi-dimension evidence trust engine.

DESIGN RULES (from the task brief, non-negotiable):
  1. NO composite score - one number must never hide different
     verification dimensions. Output is a per-dimension ledger.
  2. Every dimension carries: status, reason, evidence, and the
     CONDITION THAT WOULD FAIL IT (so a reader knows what was and
     was NOT checked).
  3. Provable-but-absent capabilities are UNKNOWN / UNVERIFIED /
     NOT_ATTESTED - never silently upgraded.
  4. CLIENT-CLAIMED values never promote a dimension. Only
     server-resolvable facts (e.g. key registration state from
     TM03, chain reports from TM05) count as evidence.

Dimensions (10):
  file_integrity        - do the bytes still hash to photo_hash?
  signature_validity    - does the ECDSA math verify?
  device_registration   - TM03 key_status_for_public_key
  hardware_attestation  - App Attest credential state (TM04); the
                          photo-key binding is its own sub-finding
  time_source           - server anchor vs extrapolated vs unknown
  location_source       - GNSS claimed / manual / none / spoof-risk
  chain_integrity       - TM05 chain report
  external_timestamp    - RFC3161 / checkpoint presence (TM38/TM05)
  derivative_integrity  - derivatives still hash to manifest values
  verification_env      - which environment produced THIS report
"""

from __future__ import annotations

from dataclasses import dataclass, field

VERIFIED = "verified"
UNVERIFIED = "unverified"
NOT_ATTESTED = "not_attested"
UNKNOWN = "unknown"
FAILED = "failed"


@dataclass
class Dimension:
    name: str
    status: str
    reason: str
    evidence: str
    failure_condition: str


@dataclass
class TrustReport:
    dimensions: list[Dimension] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "dimensions": [
                {"name": d.name, "status": d.status, "reason": d.reason,
                 "evidence": d.evidence,
                 "failure_condition": d.failure_condition}
                for d in self.dimensions],
            "notes": self.notes,
        }

    def by_name(self, name: str) -> Dimension:
        for d in self.dimensions:
            if d.name == name:
                return d
        raise KeyError(name)

    def any_failed(self) -> bool:
        return any(d.status == FAILED for d in self.dimensions)


def compute_trust(inputs: dict) -> TrustReport:
    """inputs (ALL optional unless noted) - server-side facts only:

    photo_sha256_matches : bool | None   - recomputed vs recorded
    signature_verifies   : bool | None   - server-executed ECDSA
    key_registration     : dict | None   - TM03 key_status_for_public_key
    app_attest          : dict | None    - TM04 result {verified, ...}
    photo_key_binding    : dict | None   - same-key challenge result
    time_anchor         : dict | None    - {state, anchored_at?}
    location             : dict | None   - {source, spoof_risk?}
    chain_report         : dict | None   - TM05 verify_chain().as_dict()
    external_checkpoint  : dict | None   - TM05 verify_checkpoint result
    derivatives          : dict | None   - {all_match: bool, checked: int}
    verification_env     : str           - REQUIRED: 'desktop' / 'device'
    """
    r = TrustReport()

    def dim(name, status, reason, evidence, failure_condition):
        r.dimensions.append(Dimension(name, status, reason, evidence,
                                      failure_condition))

    # ---- 1. file integrity ------------------------------------------
    v = inputs.get("photo_sha256_matches")
    if v is None:
        dim("file_integrity", UNKNOWN,
            "bytes were not re-hashed by this verification run",
            "no hash comparison performed",
            "fails when SHA256(original bytes) != recorded photo_hash")
    elif v is True:
        dim("file_integrity", VERIFIED,
            "stored bytes hash to the recorded photo_hash",
            "SHA-256 comparison recomputed during verification",
            "fails when any byte of the original changes")
    else:
        dim("file_integrity", FAILED,
            "stored bytes DO NOT hash to the recorded photo_hash",
            "SHA-256 comparison recomputed during verification",
            "(this failure fired)")

    # ---- 2. signature validity ---------------------------------------
    v = inputs.get("signature_verifies")
    if v is None:
        dim("signature_validity", UNVERIFIED,
            "no signature was checked by this verification run",
            "ECDSA verification not executed",
            "fails when the DER signature does not verify against "
            "the recorded public key over the canonical payload")
    elif v is True:
        dim("signature_validity", VERIFIED,
            "ECDSA P-256 signature verifies over the canonical payload",
            "signature math executed by the verifier",
            "fails when payload, key or signature bytes are altered")
    else:
        dim("signature_validity", FAILED,
            "signature does NOT verify - the record's math is broken",
            "signature math executed by the verifier",
            "(this failure fired)")

    # ---- 3. device registration (TM03) --------------------------------
    ks = inputs.get("key_registration")
    if ks is None:
        dim("device_registration", UNKNOWN,
            "the signing key's registration state was not looked up",
            "no key registry query performed",
            "fails/diminishes when the key is unknown, rotated-out, "
            "suspended or revoked in the registry")
    else:
        status_map = {
            "active": (VERIFIED, "the signing key is currently enrolled "
                       "and active"),
            "rotated_out": (UNVERIFIED, "the signing key was rotated "
                            "out AFTER this evidence; historical "
                            "evidence remains math-checkable, new use "
                            "is not authorized"),
            "suspended": (UNVERIFIED, "the device holding this key is "
                          "suspended (reversible hold)"),
            "revoked": (FAILED, "the device holding this key is "
                       "REVOKED (final)"),
            "unknown": (UNKNOWN, "this key was never enrolled in the "
                        "registry (or the registry is unavailable)"),
        }
        st, reason = status_map.get(
            ks.get("enrollment_status", "unknown"),
            (UNKNOWN, f"registry reported "
             f"{ks.get('enrollment_status')!r}"))
        dim("device_registration", st, reason,
            f"key registry lookup: fingerprint {ks.get('key_fingerprint')}",
            "fails when the key's registry state cannot support "
            "attribution to an authorized device")

    # ---- 4. hardware attestation (TM04) ------------------------------
    aa = inputs.get("app_attest")
    bnd = inputs.get("photo_key_binding")
    if aa is None:
        dim("hardware_attestation", NOT_ATTESTED,
            "no App Attest credential was presented",
            "no attestation object processed",
            "improves only when a valid App Attest credential chain "
            "to the platform root is verified")
    elif aa.get("verified"):
        if bnd is None:
            dim("hardware_attestation", NOT_ATTESTED,
                "an App Attest credential VERIFIED, but it was NOT "
                "bound to the photo-signing key (different keys are "
                "different verification problems)",
                "attestation verified; binding challenge absent",
                "binding fails unless the app signs a WorkCam "
                "challenge with the SAME credential")
        elif bnd.get("verified"):
            dim("hardware_attestation", VERIFIED,
                "App Attest credential verified AND bound to the "
                "photo-signing key via a signed challenge",
                "attestation + same-key challenge both verified",
                "fails when the binding challenge signature is absent "
                "or signed by a different key")
        else:
            dim("hardware_attestation", FAILED,
                "App Attest verified but the same-key binding "
                "challenge FAILED",
                "binding challenge rejected",
                "(this failure fired)")
    else:
        dim("hardware_attestation", FAILED,
            f"App Attest failed: {aa.get('failures') or 'rejected'}",
            "attestation object processed and rejected",
            "(this failure fired)")

    # ---- 5. time source ------------------------------------------------
    ta = inputs.get("time_anchor")
    if ta is None:
        dim("time_source", UNKNOWN,
            "no server time-anchor receipt covers this record; the "
            "capture time remains a client claim",
            "no anchor receipt presented",
            "verified only when a server-signed anchor receipt binds "
            "the record BEFORE capture-time drift grows unbounded")
    else:
        st = ta.get("state")
        mapping = {
            "server_anchored": (VERIFIED, "a server-signed time anchor "
                                f"bounds this record (anchored at "
                                f"{ta.get('anchored_at', '?')})"),
            "extrapolated": (UNVERIFIED, "capture time is EXTRAPOLATED "
                             "from an earlier anchor - a bounded "
                             "estimate, NOT a server-certified "
                             "capture time"),
            "unverifiable": (UNKNOWN, "time cannot be bounded "
                             "(anchor expired / clock reset)"),
        }
        st2, reason = mapping.get(st, (UNKNOWN,
                                       f"unknown anchor state {st!r}"))
        dim("time_source", st2, reason,
            f"anchor state {st!r}",
            "fails when the anchor signature is invalid or the "
            "extrapolation window is exceeded")

    # ---- 6. location source -------------------------------------------
    loc = inputs.get("location")
    if loc is None:
        dim("location_source", NOT_ATTESTED,
            "no location data is attached to this record",
            "no location payload",
            "verified (as a CLAIM, never a proof) only when a fix "
            "with plausible quality metrics accompanies the record")
    else:
        src = loc.get("source")
        if src == "gnss":
            st, reason = (UNVERIFIED,
                          "device-claimed GNSS fix; a plausible claim, "
                          "NOT an unforgeable position proof "
                          f"(spoof risk: {loc.get('spoof_risk', 'not assessed')})")
        elif src == "manual":
            st, reason = (UNVERIFIED, "HUMAN-ENTERED location - by "
                          "definition not sensor-derived")
        elif src == "none":
            st, reason = (NOT_ATTESTED, "location explicitly recorded "
                          "as absent")
        else:
            st, reason = (UNKNOWN, f"location source {src!r} not "
                          "recognized")
        dim("location_source", st, reason,
            f"source={src!r}",
            "never reports 'verified' - no consumer-GNSS fix can be "
            "proven genuine; only plausibility can be assessed")

    # ---- 7. chain integrity (TM05) --------------------------------------
    ch = inputs.get("chain_report")
    if ch is None:
        dim("chain_integrity", UNKNOWN,
            "the record's audit chain was not verified by this run",
            "no chain report presented",
            "fails on forks, interior deletions, insertions or "
            "link mismatches (TM05 codes)")
    elif ch.get("valid"):
        dim("chain_integrity", VERIFIED,
            "the local chain is internally consistent "
            f"({ch.get('records', '?')} records)",
            "chain verification executed",
            "see TM05: LOCAL consistency does NOT prove the tail was "
            "never truncated")
        if "tail" in str(ch.get("tail_truncation_risk", "")):
            r.notes.append(ch["tail_truncation_risk"])
    else:
        dim("chain_integrity", FAILED,
            f"chain problems: {ch.get('problems')}",
            "chain verification executed",
            "(this failure fired)")

    # ---- 8. external timestamp / checkpoint ------------------------------
    ck = inputs.get("external_checkpoint")
    if ck is None:
        dim("external_timestamp", NOT_ATTESTED,
            "no external checkpoint or RFC 3161 timestamp anchors "
            "this record",
            "no checkpoint presented",
            "verified only when a valid external checkpoint (or TSA "
            "token) covers the chain head / record digest")
    elif ck.get("ok"):
        dim("external_timestamp", VERIFIED,
            f"external checkpoint verified ({ck.get('records', '')} "
            "records anchored)",
            "checkpoint signature + count/hash binding checked",
            "fails when the checkpoint signature is invalid or the "
            "local chain diverges from the anchored head")
    else:
        dim("external_timestamp", FAILED,
            f"checkpoint mismatch: {ck.get('problems')}",
            "checkpoint verification executed",
            "(this failure fired)")

    # ---- 9. derivative integrity -----------------------------------------
    dv = inputs.get("derivatives")
    if dv is None:
        dim("derivative_integrity", UNKNOWN,
            "derivative files were not re-checked by this run",
            "no derivative comparison performed",
            "fails when any declared derivative's bytes no longer "
            "hash to its manifest entry")
    elif dv.get("all_match"):
        dim("derivative_integrity", VERIFIED,
            f"all {dv.get('checked', '?')} declared derivatives "
            "hash to their manifest values",
            "per-file SHA-256 comparison",
            "fails when a derivative byte changes (originals are "
            "never in scope here - they are dimension 1)")
    else:
        dim("derivative_integrity", FAILED,
            "one or more derivatives do NOT match their manifest "
            "hashes",
            "per-file SHA-256 comparison",
            "(this failure fired)")

    # ---- 10. verification environment -------------------------------------
    env = inputs.get("verification_env")
    if env not in ("desktop", "device", "server"):
        raise ValueError(
            "verification_env is REQUIRED and must be one of "
            "'desktop' | 'device' | 'server' - trust claims without "
            "an environment are meaningless")
    dim("verification_environment",
        VERIFIED if env != "desktop" else UNVERIFIED,
        f"this report was produced in a '{env}' verification "
        f"environment",
        "engine-declared environment of this run",
        "desktop reports are DESKTOP_SIMULATION and never substitute "
        "for device verification")

    r.notes.append(
        "No composite score exists by design: dimensions are "
        "independent claims. Any consumer decision must read the "
        "per-dimension ledger above.")
    return r
