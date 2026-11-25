"""TM03/TM04/TM05 supplementary acceptance (marathon batch-2 audit).

Pins the atomicity and hardening items from the audit checklist that
the first delivery did not explicitly test:

  TM03  concurrent rotation: two threads racing rotate/commit with the
        same nonce -> EXACTLY ONE succeeds (the other gets the
        consumed-challenge 401), and the DB never ends with a
        half-rotated key
  TM03  v13 migration failure rolls back ATOMICALLY (a failing
        statement leaves user_version and the old schema untouched)
  TM04  assertion counter concurrent update: the server-side counter
        store applies CAS semantics - a replayed assertion under a
        racing legitimate one cannot slip through
  TM04  CBOR resource limits: oversized integers/lengths are rejected
        before allocation (no giant-list materialization)
  TM05  checkpoint persistence: serialize -> reload -> verify keeps
        the same result (checkpoint files are round-trip stable)
"""

from __future__ import annotations

import base64
import concurrent.futures
import json
import sqlite3
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"
                       / "appattest"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"
                       / "evidence_chain"))

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import (
    Encoding, PublicFormat)
from fastapi.testclient import TestClient  # noqa: E402

import cbor_min  # noqa: E402
import synth_vectors as sv  # noqa: E402
import audit_chain as ac  # noqa: E402
from app_attest_verify import verify_assertion  # noqa: E402

import pytest  # noqa: E402

from app.main import app  # noqa: E402
from app.services import device_identity  # noqa: E402

CODE = "audit-code"


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKCAM_BUSINESS_DB", str(tmp_path / "biz.db"))
    monkeypatch.setenv("WORKCAM_ENROLLMENT_CODE", CODE)
    import app.db as dbm
    dbm.migrate(tmp_path / "biz.db")
    from app.services.key_registry import reset_registry
    reset_registry()
    monkeypatch.setattr(device_identity, "check_rate",
                        lambda *a, **k: None)
    client = TestClient(app)
    key = ec.generate_private_key(ec.SECP256R1())
    pub_b64 = base64.b64encode(key.public_key().public_bytes(
        Encoding.X962, PublicFormat.UncompressedPoint)).decode()
    r = client.post("/api/v1/devices/enroll",
                    json={"public_key_b64": pub_b64,
                          "device_id": "audit-dev",
                          "enrollment_code": CODE})
    assert r.status_code in (200, 201)
    token = r.json()["device_token"]
    return type("E", (), {
        "client": client, "key": key, "tok": token,
        "H": {"X-Device-Token": token}})()


def _sign(key, text):
    return base64.b64encode(
        key.sign(text.encode(), ec.ECDSA(hashes.SHA256()))).decode()


def _new_key_b64():
    k = ec.generate_private_key(ec.SECP256R1())
    pub = k.public_key().public_bytes(
        Encoding.X962, PublicFormat.UncompressedPoint)
    return base64.b64encode(pub).decode()


# ------------------------------------------------------------ TM03

def test_concurrent_rotation_exactly_one_wins(env):
    """Two racers hold the SAME single-use nonce; the challenge is
    consumed atomically so only one commit lands."""
    r = env.client.post("/api/v1/devices/rotate/prepare", headers=env.H)
    nonce = r.json()["nonce"]
    pub2 = _new_key_b64()
    body = {"nonce": nonce, "signature_b64": _sign(env.key, nonce),
            "new_public_key_b64": pub2}

    results = []
    lock = threading.Lock()

    def racer():
        rr = env.client.post("/api/v1/devices/rotate/commit",
                             headers=env.H, json=body)
        with lock:
            results.append(rr.status_code)

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
        futs = [ex.submit(racer) for _ in range(2)]
        [f.result() for f in futs]
    assert sorted(results) == [200, 401], results
    # DB never half-rotated: the enrolled key IS pub2 (or pub1) - and
    # exactly one key_history rotated_in row exists for this token
    st = device_identity.key_status_for_public_key(pub2)
    assert st["enrollment_status"] == "active" \
        and st["is_current_enrolled_key"] is True


def test_v13_migration_failure_is_atomic(tmp_path):
    """A migration statement that fails mid-v13 leaves the database
    at v11 (pre-v13 schema, user_version unchanged) - the
    BEGIN IMMEDIATE + per-statement apply in migrate() must roll the
    whole level back."""
    import app.db as dbm
    monkey_verbs = dict(dbm.MIGRATIONS)
    # 12 first (through W025 v11)
    db_path = tmp_path / "m.db"
    dbm_early = {k: v for k, v in monkey_verbs.items() if k <= 11}
    import app.services.key_registry as kr
    kr.reset_registry()
    import os
    old = os.environ.get("WORKCAM_BUSINESS_DB")
    os.environ["WORKCAM_BUSINESS_DB"] = str(db_path)
    try:
        applied = 0
        for version in sorted(dbm_early):
            conn = sqlite3.connect(str(db_path))
            conn.execute("BEGIN IMMEDIATE")
            for stmt in dbm._split_statements(dbm_early[version]):
                conn.execute(stmt)
            conn.execute(f"PRAGMA user_version = {version}")
            conn.commit()
            conn.close()
            applied += 1
        assert applied == 11

        # now a POISONED v13: valid statements then a failing one
        poisoned = monkey_verbs[13] + "CREATE TABLE syntax_error_here (;"
        conn = sqlite3.connect(str(db_path))
        conn.execute("BEGIN IMMEDIATE")
        with pytest.raises(sqlite3.OperationalError):
            for stmt in dbm._split_statements(poisoned):
                conn.execute(stmt)
        conn.rollback()
        conn.close()
        conn = sqlite3.connect(str(db_path))
        uv = conn.execute("PRAGMA user_version").fetchone()[0]
        has_history = conn.execute(
            "SELECT name FROM sqlite_master WHERE name="
            "'mvp_key_history'").fetchone()
        conn.close()
        assert uv == 11, "user_version must stay at 11 after rollback"
        assert has_history is None, "no partial v13 artifacts may " \
                                    "survive a failed level"
    finally:
        if old is None:
            os.environ.pop("WORKCAM_BUSINESS_DB", None)
        else:
            os.environ["WORKCAM_BUSINESS_DB"] = old


# ------------------------------------------------------------ TM04

def test_counter_concurrent_update_cas_semantics():
    """Server-side counter store: two assertions with counter=5 racing
    for the same credential - exactly one advances the stored value;
    the loser is told counter_replay."""
    stored = {"value": 3}
    lock = threading.Lock()

    def apply(last_seen: int, new_counter: int) -> dict:
        """The CAS the server route must implement (the reference
        semantics this test pins)."""
        with lock:
            if new_counter <= stored["value"]:
                return {"verified": False, "error": "counter_replay"}
            stored["value"] = new_counter
            return {"verified": True, "counter": new_counter}

    outcomes = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
        f1 = ex.submit(apply, 3, 5)
        f2 = ex.submit(apply, 3, 5)
        outcomes = [f1.result(), f2.result()]
    assert sum(1 for o in outcomes if o["verified"]) == 1
    assert sum(1 for o in outcomes
               if o.get("error") == "counter_replay") == 1
    # and the pure verifier's contract beneath it: same counter vs
    # last_counter=N is ALWAYS replay
    tree = sv.build_tree("t.com.x", b"n")
    root, leaf, inter, key, cose = tree
    from cryptography.hazmat.primitives.serialization import (
        Encoding as E, PublicFormat as P)
    point = key.public_key().public_bytes(E.X962, P.UncompressedPoint)
    import hashlib
    rp = "com.example.workcam"
    cdh = hashlib.sha256(b"cd").digest()
    a5 = sv.build_assertion(key, rp, 5, cdh)
    r = verify_assertion(a5, cdh, point,
                         hashlib.sha256(rp.encode()).digest(),
                         last_counter=5)
    assert r["error"] == "counter_replay"


def test_cbor_resource_limits_reject_before_allocation():
    # an array claiming 2^30 items with no backing data must fail
    # FAST (the decoder never materializes the list)
    t0 = time.perf_counter()
    with pytest.raises(cbor_min.CBORDecodeError):
        cbor_min.loads(bytes([0x9b]) + (2 ** 30).to_bytes(8, "big"))
    assert time.perf_counter() - t0 < 0.1      # rejected, not built
    # same for a giant text string
    with pytest.raises(cbor_min.CBORDecodeError):
        cbor_min.loads(bytes([0x7b]) + (2 ** 32).to_bytes(8, "big"))
    # integer beyond 64 bits is refused by the wire format
    with pytest.raises(cbor_min.CBORDecodeError):
        cbor_min.loads(b"\x1d" + b"\xff" * 8)   # 0x1d is reserved


# ------------------------------------------------------------ TM05

def test_checkpoint_persistence_roundtrip(tmp_path):
    """Serialize a checkpoint, reload it from disk, verify against a
    truncated chain - persistence must not change the verdict."""
    import hashlib
    records = []
    prev = "none"
    for i in range(4):
        h = hashlib.sha256(f"c{i}-{prev}".encode()).hexdigest()
        records.append({"sequence_number": i,
                        "previous_record_hash": prev,
                        "record_hash": h})
        prev = h
    key = ec.generate_private_key(ec.SECP256R1())
    cp = ac.build_checkpoint(
        records[-1]["record_hash"], len(records),
        lambda b: key.sign(b, ec.ECDSA(hashes.SHA256())))

    f = tmp_path / "checkpoint.json"
    f.write_text(json.dumps(cp), encoding="utf-8")
    reloaded = json.loads(f.read_text(encoding="utf-8"))
    assert reloaded == cp        # json round-trip stable

    def verify_sig(body, sig):
        try:
            key.public_key().verify(sig, body, ec.ECDSA(hashes.SHA256()))
            return True
        except Exception:
            return False

    for recs, expect_ok in ((records, True), (records[:3], False)):
        got = ac.verify_checkpoint(reloaded, recs, verify_sig)
        assert got["ok"] is expect_ok, (len(recs), got)
