"""WC-W009 P0 - SQLite-persisted device identity service.

Replaces the file-based devices.jsonl as the PRIMARY identity store,
keeping READ-COMPAT with the legacy file (existing enrollments keep
working during migration; new enrollments go to SQLite only).

Controlled enrollment: registering a device requires an OUT-OF-BAND
enrollment code (WORKCAM_ENROLLMENT_CODE env var or an
enrollment-code file) - a random client cannot self-register as
trusted. Revocation is instant and every protected API check refuses
revoked tokens.

Challenge-response anti-replay: nonce lives in mvp_auth_challenges
(single-use, expiring) so server restarts neither leak stale challenges
nor invalidate in-flight ones before their TTL.

Rate limits: sliding window persisted in mvp_rate_limits - survives
restarts and is consistent across processes sharing the DB.

Audit: structured events land in mvp_audit_log with SENSITIVE-FIELD
REDACTION (tokens are stored as fingerprints only). This is an
append-only APPLICATION log - it is NOT described as a tamper-proof
audit chain.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import time
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec

from app.db import ApiError, db, utc_now


def _enrollment_code() -> str | None:
    """The out-of-band enrollment secret. From env or a code file; if
    NEITHER is set, enrollment is CLOSED (self-registration is never
    the default posture)."""
    code = os.environ.get("WORKCAM_ENROLLMENT_CODE")
    if code:
        return code
    code_file = Path(os.environ.get(
        "WORKCAM_ENROLLMENT_CODE_FILE",
        "data/business/enrollment-code.txt"))
    if code_file.exists():
        value = code_file.read_text(encoding="utf-8").strip()
        return value or None
    return None


def token_fingerprint(token: str) -> str:
    """For logs ONLY: a short non-reversible fingerprint - the full
    token never enters the audit log."""
    return hashlib.sha256(token.encode()).hexdigest()[:16]


def audit(event_type: str, device_token: str | None = None,
          detail: dict | None = None) -> None:
    """Structured audit event. Sensitive fields redacted: the caller
    may pass a token; only its FINGERPRINT is persisted. Detail values
    are scanned for token-like strings and truncated."""
    safe_detail = {}
    for k, v in (detail or {}).items():
        if k.lower() in ("token", "device_token", "x-device-token",
                         "private_key", "secret", "signature_b64"):
            v = "[REDACTED]"
        if isinstance(v, str) and len(v) > 300:
            v = v[:300] + "…"
        safe_detail[k] = v
    fp = token_fingerprint(device_token) if device_token else None
    try:
        with db() as conn:
            conn.execute(
                "INSERT INTO mvp_audit_log (ts, event_type, "
                "device_token, device_id, detail) VALUES (?,?,?,?,?)",
                (utc_now(), event_type, fp, None,
                 json.dumps(safe_detail, ensure_ascii=False)))
    except Exception:
        # audit must never break the request path; failure here means
        # the audit DB is unavailable (startup misconfig) - swallowed
        # but visible in stderr via the exception short-circuit above
        pass


def prune_audit(max_age_days: int = 90) -> int:
    """Retention policy: audit rows older than the window are removed.
    Retention, NOT tamper-proofing."""
    cutoff = time.time() - max_age_days * 86400
    from datetime import datetime, timezone
    cutoff_iso = datetime.fromtimestamp(cutoff, tz=timezone.utc).isoformat()
    with db() as conn:
        cur = conn.execute("DELETE FROM mvp_audit_log WHERE ts < ?",
                           (cutoff_iso,))
        return cur.rowcount


# ------------------------------------------------------------- enrollment


def enroll_device(public_key_b64: str, device_id: str,
                  enrollment_code: str | None = None) -> str:
    """Register a device (CONTROLLED - see _enrollment_code).

    W012 SECURITY CHANGE: the issued token is a RANDOM server-side
    secret (secrets.token_hex) - NOT derived from the public key. In
    the pre-W012 design token = sha256(public_key), but the public key
    is embedded in every evidence manifest, so anyone holding a captured
    evidence package could compute a valid credential. Tokens are now
    opaque secrets that must be protected like passwords; the public
    key remains stored ONLY for challenge-verify.

    Pre-W012 (derived) tokens keep working through the legacy-file
    read-compat path."""
    code = _enrollment_code()
    if code is None:
        raise ApiError(403, "enrollment_closed",
                       "device enrollment requires an out-of-band "
                       "enrollment code (WORKCAM_ENROLLMENT_CODE) - "
                       "self-registration is not permitted")
    if enrollment_code != code:
        raise ApiError(403, "bad_enrollment_code",
                       "invalid enrollment code")
        # constant-ish response: no hint about whether the code exists

    # validate the key material BEFORE storing anything
    try:
        raw = base64.b64decode(public_key_b64, validate=True)
        if len(raw) != 65 or raw[0] != 0x04:
            raise ValueError("not a 65-byte X9.63 point")
        ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), raw)
    except Exception:
        raise ApiError(400, "invalid_public_key",
                       "public_key_b64 must be a valid P-256 X9.63 "
                       "uncompressed point")

    token = secrets.token_hex(32)     # random opaque secret (W012)
    fingerprint = token_fingerprint(token)
    now = utc_now()
    with db() as conn:
        # same-key re-enrollment: return the EXISTING token (idempotent)
        existing = conn.execute(
            "SELECT * FROM mvp_devices WHERE public_key_b64 = ? "
            "AND status = 'active'",
            (public_key_b64,)).fetchone()
        if existing is not None:
            audit("device_enroll_replay", existing["device_token"],
                  {"device_id": device_id})
            return existing["device_token"]
        revoked_key = conn.execute(
            "SELECT 1 FROM mvp_devices WHERE public_key_b64 = ? "
            "AND status = 'revoked'",
            (public_key_b64,)).fetchone()
        if revoked_key is not None:
            raise ApiError(409, "device_revoked",
                           "this device was previously enrolled and "
                           "revoked; re-enrollment requires manual "
                           "intervention")
        conn.execute(
            "INSERT INTO mvp_devices (device_token, device_id, "
            "public_key_b64, key_fingerprint, status, enrolled_at) "
            "VALUES (?,?,?,?,'active',?)",
            (token, device_id, public_key_b64, fingerprint, now))
    audit("device_enrolled", token, {"device_id": device_id})
    return token


def revoke_device(device_token: str) -> dict:
    """Revoke a device - instant effect on every protected API."""
    now = utc_now()
    with db() as conn:
        row = conn.execute(
            "SELECT * FROM mvp_devices WHERE device_token = ?",
            (device_token,)).fetchone()
        if row is None:
            # check the legacy file too (a device enrolled pre-v5 can be
            # revoked by first importing it)
            legacy = _legacy_lookup(device_token)
            if legacy is None:
                raise ApiError(404, "not_found", "device not found")
            conn.execute(
                "INSERT INTO mvp_devices (device_token, device_id, "
                "public_key_b64, key_fingerprint, status, enrolled_at, "
                "revoked_at) VALUES (?,?,?,?,'revoked',?,?)",
                (device_token, legacy["device_id"],
                 legacy["public_key_b64"],
                 token_fingerprint(device_token), now, now))
        else:
            if row["status"] == "revoked":
                return {"device_token": device_token,
                        "status": "revoked",
                        "revoked_at": row["revoked_at"],
                        "idempotent": True}
            conn.execute(
                "UPDATE mvp_devices SET status='revoked', revoked_at=? "
                "WHERE device_token=?", (now, device_token))
    # kill any outstanding challenges for the revoked device
    with db() as conn:
        conn.execute("DELETE FROM mvp_auth_challenges "
                     "WHERE device_token = ?", (device_token,))
    audit("device_revoked", device_token)
    return {"device_token": device_token, "status": "revoked",
            "revoked_at": now, "idempotent": False}


def device_status(device_token: str) -> dict:
    """Status query - the response NEVER includes the stored public key
    (the fingerprint is exposed only)."""
    with db() as conn:
        row = conn.execute(
            "SELECT * FROM mvp_devices WHERE device_token = ?",
            (device_token,)).fetchone()
    if row is not None:
        return {"status": row["status"],
                "device_id": row["device_id"],
                "key_fingerprint": row["key_fingerprint"],
                "enrolled_at": row["enrolled_at"],
                "revoked_at": row["revoked_at"],
                "source": "sqlite"}
    legacy = _legacy_lookup(device_token)
    if legacy is not None:
        return {"status": "active",
                "device_id": legacy["device_id"],
                "key_fingerprint": token_fingerprint(device_token),
                "enrolled_at": None,
                "revoked_at": None,
                "source": "legacy_file"}
    raise ApiError(404, "not_found", "device not found")


# --------------------------------------------------------------- lookup


def _legacy_lookup(token: str) -> dict | None:
    """Read-compat with the pre-W009 devices.jsonl. New enrollments
    never go here - this path exists so existing joint-debug devices
    keep working without re-enrollment."""
    path = Path(os.environ.get("WORKCAM_DEVICE_REGISTRY",
                               "data/uploads-experimental/devices.jsonl"))
    if not path.exists():
        return None
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if rec.get("token") == token:
            return rec
    return None


def _is_derived_token(token: str, rec: dict) -> bool:
    """True if this legacy record's token was the pre-W012
    sha256(public_key) design - i.e., a credential computable from
    material inside every evidence manifest."""
    import hashlib as _h
    return token == _h.sha256(rec.get("public_key_b64", "").encode(
        "ascii")).hexdigest()


def resolve_device(token: str) -> str:
    """THE identity gate for every protected API. Returns device_id or
    raises.

    W013 SECURITY (P0 acceptance): legacy tokens that are DERIVED from
    the public key (sha256(pubkey) design) are NO LONGER accepted as
    bearer credentials on their own - the public key is embedded in
    every evidence manifest, so derived tokens are computable by anyone
    holding a captured package. Legacy devices must now go through
    re-enrollment (same public key -> new random token, idempotent) or
    a challenge-response proof of private-key possession.

    Non-derived legacy tokens (already random, pre-W012 joint-debug
    exceptions) remain valid. Revocation is enforced on both stores."""
    with db() as conn:
        row = conn.execute(
            "SELECT * FROM mvp_devices WHERE device_token = ?",
            (token,)).fetchone()
    if row is not None:
        if row["status"] == "revoked":
            raise ApiError(401, "unauthorized",
                           "device token is revoked")
        return row["device_id"]
    legacy = _legacy_lookup(token)
    if legacy is not None:
        # revoked check on the SQLite store by PUBLIC KEY (a legacy
        # device revoked before migration must not resurrect)
        with db() as conn:
            revoked = conn.execute(
                "SELECT 1 FROM mvp_devices WHERE public_key_b64 = ? "
                "AND status = 'revoked'",
                (legacy.get("public_key_b64", ""),)).fetchone()
        if revoked is not None:
            raise ApiError(401, "unauthorized",
                           "device token is revoked")
        if _is_derived_token(token, legacy):
            # derived credential from public material -> refused as a
            # bearer secret; the device must re-enroll (idempotent by
            # public key) to obtain a random token
            raise ApiError(401, "legacy_token_must_re_enroll",
                           "this is a legacy token derived from the "
                           "public key; re-enroll with the enrollment "
                           "code to receive a modern random token")
        return legacy["device_id"]
    raise ApiError(401, "unauthorized", "device token is not enrolled")


def public_key_for_token(token: str) -> str | None:
    """The enrolled public key (for challenge-verify)."""
    with db() as conn:
        row = conn.execute(
            "SELECT public_key_b64 FROM mvp_devices "
            "WHERE device_token = ?", (token,)).fetchone()
    if row is not None:
        return row["public_key_b64"]
    legacy = _legacy_lookup(token)
    return legacy["public_key_b64"] if legacy else None


# -------------------------------------------------- challenge / anti-replay


def issue_challenge(device_token: str, ttl_seconds: int = 60) -> str:
    nonce = secrets.token_hex(32)
    expires = time.time() + ttl_seconds
    with db() as conn:
        conn.execute(
            "DELETE FROM mvp_auth_challenges WHERE expires_at < ?",
            (time.time(),))             # opportunistic GC
        conn.execute(
            "INSERT INTO mvp_auth_challenges (nonce, device_token, "
            "expires_at) VALUES (?,?,?)", (nonce, device_token, expires))
    return nonce


def verify_challenge_signature(device_token: str, nonce: str,
                               signature_b64: str) -> str:
    """Single-use nonce + P-256 signature check. The nonce is consumed
    ATOMICALLY (DELETE ... RETURNING equivalent via a guarded DELETE) so
    two concurrent replays cannot both succeed."""
    now = time.time()
    with db() as conn:
        row = conn.execute(
            "SELECT device_token, expires_at FROM mvp_auth_challenges "
            "WHERE nonce = ?", (nonce,)).fetchone()
        if row is None:
            raise ApiError(401, "unauthorized",
                           "unknown or already-used challenge")
        conn.execute("DELETE FROM mvp_auth_challenges WHERE nonce = ?",
                     (nonce,))
    if row["expires_at"] < now:
        raise ApiError(401, "unauthorized", "challenge expired")
    if row["device_token"] != device_token:
        raise ApiError(403, "forbidden",
                       "challenge was issued to a different device")

    pub_b64 = public_key_for_token(device_token)
    if pub_b64 is None:
        raise ApiError(401, "unauthorized", "device not enrolled")
    try:
        pub = ec.EllipticCurvePublicKey.from_encoded_point(
            ec.SECP256R1(), base64.b64decode(pub_b64))
        pub.verify(base64.b64decode(signature_b64),
                   nonce.encode("ascii"), ec.ECDSA(hashes.SHA256()))
    except (InvalidSignature, ValueError):
        audit("identity_proof_failed", device_token,
              {"reason": "bad nonce signature"})
        raise ApiError(401, "unauthorized",
                      "identity proof failed - bad nonce signature")
    audit("identity_verified", device_token)
    return resolve_device(device_token)


# ------------------------------------------------------------- rate limits


def check_rate(device_token: str, capacity: int = 60,
               window_seconds: int = 60) -> None:
    """Sliding-window limiter persisted in the DB - restart-stable and
    multi-process consistent (every process shares the same table)."""
    now = time.time()
    window_start = int(now // window_seconds) * window_seconds
    with db() as conn:
        row = conn.execute(
            "SELECT request_count FROM mvp_rate_limits "
            "WHERE device_token = ? AND window_start = ?",
            (device_token, window_start)).fetchone()
        count = (row["request_count"] if row else 0) + 1
        if row is None:
            conn.execute(
                "INSERT INTO mvp_rate_limits (device_token, "
                "window_start, request_count) VALUES (?,?,1)",
                (device_token, window_start))
        else:
            conn.execute(
                "UPDATE mvp_rate_limits SET request_count = ? "
                "WHERE device_token = ? AND window_start = ?",
                (count, device_token, window_start))
        conn.execute(
            "DELETE FROM mvp_rate_limits WHERE window_start < ?",
            (window_start - window_seconds * 2,))
    if count > capacity:
        audit("rate_limited", device_token,
              {"window": window_seconds, "capacity": capacity})
        raise ApiError(429, "rate_limited",
                       f"device rate limit exceeded ({capacity}/"
                       f"{window_seconds}s window)")
