"""M03-D03: long-term evidence verification policy (TM39).

THE PROBLEM: an ECDSA signature verified in 2026 may be unverifiable
in 2036 - the signing key was rotated, the certificate chain lapsed,
the hash algorithm was deprecated. What does the verifier SAY then?

THE MODEL (implemented as a decision function over verification
MATERIALS, not over trust):
  LongTermStatus:
    fully_verifiable   - key resolvable + signature math valid
    math_valid_key_unknown - signature verifies against the
                        embedded key; the key's CURRENT registration
                        is unknown (archive lookup pending)
    key_retired        - key resolvable but rotated out/revoked:
                        the MATH is still checked and reported;
                        CURRENT authorization claims are NOT made
    material_missing   - the archived materials (key/cert) are gone:
                        cannot verify AT ALL - never guess
    algorithm_deprecated - the hash curve/size is below policy:
                        reported as a RISK, math still shown
    expired_timestamp  - an RFC 3161 token whose TSA chain expired:
                        the token still proves existence-at-genTime
                        if the signature verifies against the
                        ARCHIVED TSA cert (the standard long-term
                        verification path)

POLICY RULES (task brief):
  - 'certificate status unknown' vs 'certificate expired' vs
    'signature math valid' are DIFFERENT statements - never merged
  - verification materials (public keys, certs, TSA chains, policy)
    must be RETAINED with the archive - the caller supplies them
  - an upgrade path exists: re-anchoring an old record under a new
    timestamp / key (renewal) is an EXPLICIT action with an audit
    entry, never a background rewrite
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class LongTermStatus(str, Enum):
    fully_verifiable = "fully_verifiable"
    math_valid_key_unknown = "math_valid_key_unknown"
    key_retired = "key_retired"
    material_missing = "material_missing"
    algorithm_deprecated = "algorithm_deprecated"
    expired_timestamp = "expired_timestamp"


class LongTermPolicyError(ValueError):
    pass


@dataclass(frozen=True)
class ArchivedMaterials:
    """What the archive retained for one record. None = not
    retained (the honest 'material_missing' path)."""
    embedded_public_key: str | None = None
    key_registration_state: str | None = None   # active/rotated_out/
    #                                             revoked/unknown/None
    signature_algorithm: str = "ecdsa-p256-sha256"
    tsa_certificate: str | None = None          # archived TSA chain
    tsa_token_gen_time: str | None = None
    tsa_chain_expired: bool = False


@dataclass(frozen=True)
class LongTermVerdict:
    status: LongTermStatus
    signature_math: str          # 'valid' | 'not_checked' | ...
    key_statement: str           # exact claim about the key
    timestamp_statement: str     # exact claim about the time proof
    risks: list[str]
    renewal_available: bool = False

    def as_dict(self) -> dict:
        return {
            "status": self.status.value,
            "signature_math": self.signature_math,
            "key_statement": self.key_statement,
            "timestamp_statement": self.timestamp_statement,
            "risks": self.risks,
            "renewal_available": self.renewal_available,
        }


# policy: what counts as deprecated (extendable config, not law)
_DEPRECATED_ALGORITHMS = {
    # none of WorkCam's current stack; entries appear here as policy
    # evolves (e.g. sha1-anything, rsa-1024, ecdsa-p192-*)
    "ecdsa-p192-sha256",
    "rsa-1024-sha256",
    "any-sha1",
}


def assess_long_term(materials: ArchivedMaterials,
                     signature_math_valid: bool | None
                     ) -> LongTermVerdict:
    """The decision function. signature_math_valid: None = the math
    could not be checked (no key); True/False = checked."""
    if materials.signature_algorithm in _DEPRECATED_ALGORITHMS:
        return LongTermVerdict(
            status=LongTermStatus.algorithm_deprecated,
            signature_math=("valid (checked)" if signature_math_valid
                            is True else
                            "not checked" if signature_math_valid
                            is None else "invalid"),
            key_statement="algorithm below current policy - see risks",
            timestamp_statement=_ts_statement(materials),
            risks=[
                f"signature algorithm "
                f"{materials.signature_algorithm} is deprecated by "
                f"current policy",
                "the signature is still REPORTED as-is; policy is a "
                "risk statement, not a validity claim"],
            renewal_available=True)

    if materials.embedded_public_key is None:
        return LongTermVerdict(
            status=LongTermStatus.material_missing,
            signature_math="not checked",
            key_statement="archived public key NOT retained - "
                          "verification impossible; never guessed",
            timestamp_statement=_ts_statement(materials),
            risks=["archive is incomplete: without the retained key "
                   "the signature cannot be verified at all"],
            renewal_available=False)

    if signature_math_valid is None:
        # the caller had the key but did not run the math - refuse
        # to guess (this is a caller bug; the verdict says so)
        raise LongTermPolicyError(
            "signature_math_valid=None requires embedded_public_key"
            "=None; with a retained key the math MUST be executed")

    if not signature_math_valid:
        return LongTermVerdict(
            status=LongTermStatus.material_missing,
            signature_math="invalid (checked and FAILED)",
            key_statement=_key_statement(materials),
            timestamp_statement=_ts_statement(materials),
            risks=["the signature does NOT verify - the record is "
                   "not trustworthy regardless of key state"],
            renewal_available=False)

    # math is VALID - the statements differ only by key state
    reg = materials.key_registration_state
    if reg is None:
        return LongTermVerdict(
            status=LongTermStatus.math_valid_key_unknown,
            signature_math="valid (checked)",
            key_statement="signature math verifies against the "
                          "ARCHIVED key; the key's CURRENT "
                          "registration is unknown (no registry "
                          "material retained)",
            timestamp_statement=_ts_statement(materials),
            risks=["cannot attribute the signature to a currently "
                   "authorized device; historical attribution only"],
            renewal_available=True)
    if reg == "active":
        return LongTermVerdict(
            status=LongTermStatus.fully_verifiable,
            signature_math="valid (checked)",
            key_statement="key currently registered and active",
            timestamp_statement=_ts_statement(materials),
            risks=[],
            renewal_available=False)
    # rotated_out / revoked / suspended: math valid, key NOT current
    return LongTermVerdict(
        status=LongTermStatus.key_retired,
        signature_math="valid (checked)",
        key_statement=f"signature math verifies against the archived "
                      f"key; the key is {reg.upper()} - the record "
                      f"is HISTORICAL evidence, not currently "
                      f"authorized material",
        timestamp_statement=_ts_statement(materials),
        risks=["current-authorization claims must NOT be made for "
               "retired-key evidence"],
        renewal_available=True)


def _key_statement(m: ArchivedMaterials) -> str:
    if m.key_registration_state:
        return f"key state: {m.key_registration_state}"
    return "key state unknown"


def _ts_statement(m: ArchivedMaterials) -> str:
    if m.tsa_token_gen_time is None:
        return ("no RFC 3161 token retained - the record has no "
                "external time anchor")
    if m.tsa_chain_expired:
        if m.tsa_certificate is None:
            return ("timestamp token retained but the TSA "
                    "certificate was NOT archived - the token "
                    "cannot be re-verified")
        return (f"RFC 3161 token (genTime "
                f"{m.tsa_token_gen_time}): the TSA chain has "
                f"EXPIRED, but verification against the ARCHIVED "
                f"TSA certificate still proves the data existed at "
                f"genTime - the proof does not lapse with the "
                f"chain")
    return (f"RFC 3161 token (genTime {m.tsa_token_gen_time}) "
            f"verifiable against the retained TSA chain")


def renewal_path(verdict: LongTermVerdict,
                 record_sha256: str) -> dict:
    """The EXPLICIT renewal action: re-anchor an old record under a
    fresh timestamp / current key policy. Returns the audit entry
    the caller MUST persist - renewals are events, not rewrites."""
    if not verdict.renewal_available:
        raise LongTermPolicyError(
            "renewal is only offered for math-valid records with "
            "known gaps (unknown key state / deprecated algorithm "
            "/ expired timestamp) - never for failed math or "
            "missing materials")
    return {
        "action": "renewal",
        "record_sha256": record_sha256,
        "prior_status": verdict.status.value,
        "steps": [
            "1. obtain a fresh RFC 3161 timestamp over the CURRENT "
            "record bytes (existence now)",
            "2. if the signing key was rotated: re-verify the "
            "signature against the archived key AND record the "
            "current registration state beside it",
            "3. persist this audit entry - the original record "
            "bytes are NEVER rewritten by renewal"],
        "note": "renewal strengthens FUTURE verification; it never "
                "changes what the original signature said",
    }
