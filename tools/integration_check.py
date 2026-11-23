"""
WorkCam integration check (WC-W005-4).

Runs a full happy-path + negative-path loop against a locally running
backend (default http://127.0.0.1:8000):

  1. health check
  2. register a TEST public key (software key, clearly marked)
  3. submit valid v1.1 evidence (photo + manifest + DER signature)
  4. verify endpoint -> cryptographically_valid
  5. store record -> stored; retry -> duplicate (idempotent)
  6. tampered photo / tampered signature / unregistered key -> rejected
  7. time anchor request -> signed receipt -> receipt verifies
  8. duplicate anchor batch -> idempotent; conflicting batch -> 409

Every step prints PASS/FAIL with the reason. Exit code 0 = all passed.

TEST KEYS ONLY - software generated, never Secure Enclave, never production.
"""

from __future__ import annotations

import base64
import hashlib
import os
import sys
import uuid
from datetime import datetime, timezone

import httpx
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.hashes import SHA256
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

BASE_URL = os.environ.get("WORKCAM_BASE_URL", "http://127.0.0.1:8000")
if len(sys.argv) > 1:
    BASE_URL = sys.argv[1].rstrip("/")

PASS = 0
FAIL = 0


def step(name: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  [PASS] {name}" + (f" - {detail}" if detail else ""))
    else:
        FAIL += 1
        print(f"  [FAIL] {name}" + (f" - {detail}" if detail else ""))


def canonical_payload(manifest: dict) -> bytes:
    """v1.1 canonical payload - matches backend FIELD_ORDER exactly."""
    lines = [
        "WorkCam-Sig-v1.1",
        f"version={manifest['version']}",
        f"record_id={manifest['record_id']}",
        f"sequence_number={manifest['sequence_number']}",
        f"previous_record_hash={manifest['previous_record_hash'] or 'none'}",
        f"photo_hash={manifest['photo_hash']}",
        f"capture_timestamp={manifest['capture_timestamp']}",
        f"device_reported_time={manifest['device_reported_time']}",
        f"device_id={manifest['device_id']}",
        "location=none",
    ]
    return ("\n".join(lines) + "\n").encode("utf-8")


def build_evidence(private_key, photo: bytes, device_id: str, seq: int = 0):
    pub_b64 = base64.b64encode(
        private_key.public_key().public_bytes(
            Encoding.X962, PublicFormat.UncompressedPoint
        )
    ).decode()
    now = datetime.now(timezone.utc).isoformat()
    manifest = {
        "version": "1.1",
        "record_id": str(uuid.uuid4()),
        "sequence_number": seq,
        "previous_record_hash": None,
        "photo_hash": hashlib.sha256(photo).hexdigest(),
        "capture_timestamp": now,
        "device_reported_time": now,
        "device_id": device_id,
        "location": None,
        "public_key": pub_b64,
        "signature": "",
    }
    signature = private_key.sign(canonical_payload(manifest), ec.ECDSA(SHA256()))
    manifest["signature"] = base64.b64encode(signature).decode()
    return manifest


def main() -> int:
    print(f"WorkCam integration check against {BASE_URL}")
    print("All keys are SOFTWARE TEST KEYS - not Secure Enclave, not production.\n")

    try:
        client = httpx.Client(base_url=BASE_URL, timeout=15.0)
    except Exception as e:
        print(f"cannot create http client: {e}")
        return 2

    # ---- 1. health --------------------------------------------------------
    print("[1/8] health check")
    try:
        r = client.get("/health")
        step("GET /health", r.status_code == 200 and r.json().get("status") == "ok",
             f"status={r.status_code}")
    except httpx.ConnectError:
        print("  [FAIL] backend not reachable.")
        print(f"  Start it first:  powershell -File tools\\start_backend.ps1")
        return 2

    # ---- 2. register test key --------------------------------------------
    print("[2/8] register TEST public key")
    key = ec.generate_private_key(ec.SECP256R1())          # software TEST key
    pub_b64 = base64.b64encode(
        key.public_key().public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
    ).decode()
    device_id = f"windows-test-{uuid.uuid4().hex[:8]}"
    r = client.post(
        "/register-key",
        # NOTE: the backend declares scalar params, so FastAPI binds them
        # from the QUERY STRING (not a form body). httpx percent-encodes
        # '+', '=', '/' inside the Base64 value automatically.
        params={
            "public_key_b64": pub_b64,
            "device_id": device_id,
            "allow_test_registration": "true",
        },
    )
    ok = r.status_code == 200 and r.json().get("registration_status") == "registered"
    step("POST /register-key (query params, Base64 URL-encoded)", ok,
         f"status={r.status_code} body={r.text[:120]}")

    # ---- 3/4. valid evidence -> cryptographically_valid -------------------
    print("[3/8] submit valid v1.1 evidence")
    photo = b"\x42" * 2048
    manifest = build_evidence(key, photo, device_id)
    body = {"manifest": manifest, "photo_base64": base64.b64encode(photo).decode()}
    r = client.post("/verify", json=body)
    data = r.json()
    ok = (
        r.status_code == 200
        and data["cryptographic_signature_valid"] is True
        and data["photo_hash_valid"] is True
        and data["key_registration_status"] == "registered"
        and data["device_identity_consistent"] is True
        and data["evidence_status"] == "cryptographically_valid"
    )
    step("POST /verify valid evidence", ok, f"evidence_status={data.get('evidence_status')}")

    print("[4/8] trust level must NOT be fully_attested (no App Attest)")
    step("evidence_status != fully_attested", data.get("evidence_status") != "fully_attested",
         f"attestation={data.get('device_attestation_status')}")

    # ---- 5. store record + idempotent retry ------------------------------
    print("[5/8] store record, then retry the same submission")
    r1 = client.post("/store-record", json=body).json()
    step("POST /store-record", r1.get("stored") is True, str(r1)[:120])
    r2 = client.post("/store-record", json=body).json()
    step("retry identical submission -> duplicate, not error",
         r2.get("stored") is False and "duplicate" in r2.get("message", "").lower(),
         str(r2)[:120])

    # ---- 6. negative paths -------------------------------------------------
    print("[6/8] negative cases must all be rejected")

    bad_photo = photo[:-1] + bytes([photo[-1] ^ 0xFF])
    r = client.post("/verify", json={
        "manifest": manifest, "photo_base64": base64.b64encode(bad_photo).decode()
    }).json()
    step("tampered photo -> invalid",
         r["photo_hash_valid"] is False and r["evidence_status"] == "invalid")

    tampered = dict(manifest)
    tampered["record_id"] = manifest["record_id"] + "x"
    r = client.post("/verify", json={"manifest": tampered}).json()
    step("tampered signed field -> signature invalid",
         r["cryptographic_signature_valid"] is False)

    other_key = ec.generate_private_key(ec.SECP256R1())  # another TEST key
    unreg = build_evidence(other_key, photo, "unregistered-device")
    r = client.post("/verify", json={
        "manifest": unreg, "photo_base64": base64.b64encode(photo).decode()
    }).json()
    step("unregistered key -> at most signature_valid",
         r["key_registration_status"] == "unregistered"
         and r["evidence_status"] == "signature_valid"
         and r["evidence_status"] != "cryptographically_valid")

    # ---- 7. time anchor ----------------------------------------------------
    print("[7/8] time anchor request and receipt verification")
    head = hashlib.sha256(b"chain-head").hexdigest()
    tail = hashlib.sha256(b"chain-tail").hexdigest()
    anchor_req = {
        "batch_id": f"batch-{uuid.uuid4().hex[:12]}",
        "chain_head_hash": head,
        "chain_tail_hash": tail,
        "record_count": 1,
        "device_reported_start_time": manifest["capture_timestamp"],
        "device_reported_end_time": manifest["capture_timestamp"],
    }
    r = client.post("/time-anchor/anchor", json=anchor_req)
    anchor = r.json()
    step("POST /time-anchor/anchor", r.status_code == 200 and "server_signature" in anchor,
         f"status={r.status_code}")

    receipt = dict(anchor)
    receipt["device_reported_start_time"] = anchor_req["device_reported_start_time"]
    receipt["device_reported_end_time"] = anchor_req["device_reported_end_time"]
    receipt["stored_at"] = anchor["server_received_at"]
    r = client.post("/time-anchor/verify", json=receipt)
    v = r.json()
    step("POST /time-anchor/verify receipt",
         r.status_code == 200 and v.get("valid") is True and v.get("signature_valid") is True,
         f"trust_level={v.get('trust_level')}")

    # ---- 8. anchor duplicate + conflict ------------------------------------
    print("[8/8] anchor duplicate (idempotent) and conflict (409)")
    r = client.post("/time-anchor/anchor", json=anchor_req)
    same = r.json()
    step("resubmit identical batch -> same anchor_id",
         r.status_code == 200 and same.get("anchor_id") == anchor.get("anchor_id"))

    conflict_req = dict(anchor_req, chain_head_hash=hashlib.sha256(b"other-head").hexdigest())
    r = client.post("/time-anchor/anchor", json=conflict_req)
    step("same batch_id, different content -> 409 conflict", r.status_code == 409,
         f"status={r.status_code}")

    # ---- summary -------------------------------------------------------------
    print(f"\n{'=' * 60}")
    print(f"RESULT: {PASS} passed, {FAIL} failed")
    if FAIL == 0:
        print("Backend is READY for Mac iOS joint debugging.")
    else:
        print("Backend is NOT ready - fix failures above.")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
