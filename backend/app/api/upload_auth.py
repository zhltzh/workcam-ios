"""WC-W005 P2 - device auth & upload authorization (T12 security layer).

Frozen protocol semantics (unchanged): register / put_chunk / status /
complete / content behave EXACTLY as before. This layer WRAPS them with:

  1. DEVICE IDENTITY: pre-registered device tokens (P-256 public key
     fingerprint). Token issuance is an offline trust-list operation
     (admin registers a device by its public key; the token is the
     sha256 of the key). Devices prove identity by signing a server
     nonce - private keys NEVER travel.
  2. BATCH ISOLATION: a batch belongs to the device that created it;
     every chunk/status/complete/content call checks ownership.
  3. RATE LIMITS: per-device request caps (token bucket, in-memory).
  4. AUDIT LOG: who did what to which batch, when; append-only JSONL.

Deployment posture (unchanged): the WHOLE experimental prefix stays
default-closed (opt-in + production veto). Auth does NOT loosen that -
it adds defense for trusted-network joint debugging.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import time
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import load_pem_public_key


class AuthError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


# ----------------------------------------------------------------- registry

_AUDIT_PATH = Path(os.environ.get("WORKCAM_UPLOAD_AUDIT",
                                  "data/uploads-experimental/audit.jsonl"))
_RATE = {"capacity": 60, "refill_per_min": 30}     # gentle joint-debug caps
_challenges: dict[str, tuple[float, str]] = {}       # nonce -> (expiry, device)
_buckets: dict[str, list[float]] = {}                # device -> request stamps


def _audit(event: dict) -> None:
    """Append-only audit line. Never throws into the request path.
    Path resolved PER CALL (env may be set after import - the TestClient
    sets it via monkeypatch per test)."""
    try:
        audit_path = Path(os.environ.get("WORKCAM_UPLOAD_AUDIT",
                                         "data/uploads-experimental/audit.jsonl"))
        audit_path.parent.mkdir(parents=True, exist_ok=True)
        event = {"ts": time.time(), **event}
        with open(audit_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
    except OSError:
        pass


class DeviceRegistry:
    """Pre-registered device trust list. The 'admin' operation (register
    a device public key) is deliberately OUT of band - there is no
    self-service endpoint, mirroring how production device enrollment
    should work."""

    def __init__(self, path: Path):
        self.path = path
        self._devices: dict[str, str] = {}   # token -> device_id
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                rec = json.loads(line)
                self._devices[rec["token"]] = rec["device_id"]

    @staticmethod
    def token_for(public_key_b64: str) -> str:
        """Device token = sha256 of the registered public key (the SAME
        key the device uses for evidence signatures - one identity, no
        second credential to leak)."""
        return hashlib.sha256(public_key_b64.encode("ascii")).hexdigest()

    def enroll(self, public_key_b64: str, device_id: str) -> str:
        token = self.token_for(public_key_b64)
        rec = {"device_id": device_id, "token": token,
               "public_key_b64": public_key_b64,
               "enrolled_at": time.time()}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")
        self._devices[token] = device_id
        return token

    def device_id_for_token(self, token: str) -> str | None:
        return self._devices.get(token)

    def public_key_for_token(self, token: str) -> str | None:
        if not self.path.exists():
            return None
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            rec = json.loads(line)
            if rec["token"] == token:
                return rec["public_key_b64"]
        return None


# ------------------------------------------------------------- rate limiter

def _check_rate(device_id: str) -> None:
    now = time.time()
    stamps = [t for t in _buckets.get(device_id, []) if now - t < 60]
    if len(stamps) >= _RATE["capacity"]:
        raise AuthError(429, "device rate limit exceeded - retry in a minute")
    stamps.append(now)
    _buckets[device_id] = stamps


# ------------------------------------------------------------------- challenge

def issue_challenge(device_token: str) -> str:
    """Server nonce for prove-identity. Single-use, 60 s expiry."""
    for n, (exp, dev) in list(_challenges.items()):
        if time.time() > exp:
            _challenges.pop(n, None)
    nonce = secrets.token_hex(32)
    _challenges[nonce] = (time.time() + 60, device_token)
    return nonce


def verify_identity(device_token: str, nonce: str,
                    signature_b64: str, now: float | None = None) -> str:
    """Verify the device owns the enrolled private key: the device signs
    the nonce; we check with the ENROLLED public key. Returns device_id.
    PRIVATE KEYS NEVER LEAVE THE DEVICE."""
    now = now if now is not None else time.time()
    entry = _challenges.pop(nonce, None)
    if entry is None:
        raise AuthError(401, "unknown or already-used challenge")
    exp, expected_token = entry
    if now > exp:
        raise AuthError(401, "challenge expired")
    if expected_token != device_token:
        raise AuthError(403, "challenge was issued to a different device")

    registry = DeviceRegistry(_registry_path())
    pub_b64 = registry.public_key_for_token(device_token)
    if pub_b64 is None:
        raise AuthError(401, "device not enrolled")
    try:
        import base64
        # enrolled format = base64(X9.63 uncompressed point bytes),
        # NOT PEM - decode raw bytes then parse as a point
        from cryptography.hazmat.primitives.asymmetric import ec as _ec
        pub = _ec.EllipticCurvePublicKey.from_encoded_point(
            _ec.SECP256R1(), base64.b64decode(pub_b64))
        pub.verify(base64.b64decode(signature_b64), nonce.encode("ascii"),
                   _ec.ECDSA(hashes.SHA256()))
    except (InvalidSignature, ValueError):
        raise AuthError(401, "identity proof failed - bad nonce signature")

    device_id = registry.device_id_for_token(device_token)
    _audit({"event": "identity_verified", "device_id": device_id})
    return device_id


def _registry_path() -> Path:
    return Path(os.environ.get("WORKCAM_DEVICE_REGISTRY",
                                "data/uploads-experimental/devices.jsonl"))


# --------------------------------------------------------------- batch owner

_batch_owners: dict[str, str] = {}   # batch_id -> device_id


def claim_batch(batch_id: str, device_id: str) -> None:
    if batch_id in _batch_owners and _batch_owners[batch_id] != device_id:
        raise AuthError(403, f"batch {batch_id} belongs to another device")
    _batch_owners[batch_id] = device_id


def require_batch_owner(batch_id: str, device_id: str) -> None:
    owner = _batch_owners.get(batch_id)
    if owner is None:
        raise AuthError(404, "unknown batch for this device")
    if owner != device_id:
        # never reveal whose batch it is
        raise AuthError(404, "unknown batch for this device")
