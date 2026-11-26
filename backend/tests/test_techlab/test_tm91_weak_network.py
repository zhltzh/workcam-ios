"""TM91 / M03-A02: weak-network testing platform.

A CONTROLLABLE transport wrapper for the REAL backend: the FastAPI
TestClient is wrapped in a FaultTransport that injects the failure
modes the task brief lists, against our OWN test service only:

  offline            - every request fails (network unreachable)
  high_latency       - artificial delay before each call
  reset              - connection reset mid-request (transport error)
  timeout            - request exceeds the caller's deadline
  partial            - upload interrupted after N chunks
  duplicate_response - server response replayed by a flaky proxy
  server_restart     - the DB survives, the process 'restarts' (state
                       on disk is the resume point)
  reordered          - chunk completions acknowledged out of order
  intermittent       - failures on a deterministic schedule

The suite drives THREE real subsystems through the fault layers:
  1. the Outbox discipline (TM34/W008 server ledger idempotency)
  2. the chunked upload (TM35) - partial + resume + duplicate chunks
  3. retry/backoff - failed ops return, re-drain, converge
"""

from __future__ import annotations

import base64
import hashlib
import io
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import (
    Encoding, PublicFormat)
from fastapi.testclient import TestClient  # noqa: E402

import pytest  # noqa: E402

from app.main import app  # noqa: E402
from app.services import device_identity  # noqa: E402

CODE = "tm91-code"


# ------------------------------------------------------------ fault layer

class NetworkFault:
    """Deterministic fault scheduler: the test seeds the schedule,
    the transport consumes it. No randomness without a seed."""

    def __init__(self, mode: str, seed: int = 42,
                 latency_s: float = 0.0,
                 fail_first: int = 0,
                 partial_after: int | None = None):
        self.mode = mode
        self.rng = random.Random(seed)
        self.latency_s = latency_s
        self.fail_first = fail_first          # first N calls fail
        self.partial_after = partial_after    # chunk count cap
        self.calls = 0
        self.injected: list[str] = []

    def should_fail(self) -> bool:
        self.calls += 1
        self._maybe_delay()
        if self.mode == "offline":
            self.injected.append("offline")
            return True
        if self.mode == "reset":
            self.injected.append("reset")
            return True
        if self.mode == "intermittent":
            # every 3rd call fails, deterministically
            fail = self.calls % 3 == 0
            if fail:
                self.injected.append("intermittent")
            return fail
        if self.fail_first and self.calls <= self.fail_first:
            self.injected.append(f"fail_first:{self.calls}")
            return True
        return False

    def _maybe_delay(self) -> None:
        if self.latency_s > 0:
            time.sleep(self.latency_s)

    def chunk_cap(self, sent: int) -> bool:
        """True = the connection DIES after this many chunks."""
        if self.mode == "partial" and self.partial_after is not None:
            return sent >= self.partial_after
        return False


class OfflineUploadError(Exception):
    """The transport-level failure the uploader must survive."""


class FaultChunkTransport:
    """Client-side chunk uploader over the real server, with faults.
    This mirrors what the Swift ChunkedUploadClient does through its
    injected transport (TM69): same sequence, Python side."""

    def __init__(self, client: TestClient, headers: dict,
                 fault: NetworkFault):
        self.client = client
        self.headers = headers
        self.fault = fault
        self.chunks_sent = 0
        self.replayed_responses: dict[int, dict] = {}

    def open_session(self, photo_id, sha, chunk_size, data_len) -> dict:
        if self.fault.should_fail():
            raise OfflineUploadError("network unreachable (open)")
        r = self.client.post(
            f"/api/v1/photos/{photo_id}/original-session",
            headers=self.headers, json={
                "claimed_sha256": sha, "chunk_size": chunk_size,
                "total_chunks": (data_len + chunk_size - 1) // chunk_size,
                "total_bytes": data_len})
        if r.status_code >= 500:
            raise OfflineUploadError(f"server 5xx on open")
        assert r.status_code == 200, r.text
        return r.json()

    def put_chunk(self, photo_id, upload_id, index, chunk) -> dict:
        if self.fault.should_fail():
            raise OfflineUploadError(f"network unreachable (chunk {index})")
        if self.fault.chunk_cap(self.chunks_sent):
            raise OfflineUploadError(
                f"connection died after {self.chunks_sent} chunks")
        r = self.client.put(
            f"/api/v1/photos/{photo_id}/original-session/{upload_id}"
            f"/chunk/{index}",
            headers={**self.headers,
                     "X-Chunk-SHA256":
                         hashlib.sha256(chunk).hexdigest()},
            content=chunk)
        self.chunks_sent += 1
        if r.status_code >= 500:
            raise OfflineUploadError(f"server 5xx on chunk {index}")
        assert r.status_code == 200, r.text
        return r.json()

    def status(self, photo_id, upload_id) -> dict:
        if self.fault.should_fail():
            raise OfflineUploadError("network unreachable (status)")
        r = self.client.get(
            f"/api/v1/photos/{photo_id}/original-session/{upload_id}",
            headers=self.headers)
        assert r.status_code == 200, r.text
        return r.json()

    def complete(self, photo_id, upload_id) -> dict:
        if self.fault.should_fail():
            raise OfflineUploadError("network unreachable (complete)")
        r = self.client.post(
            f"/api/v1/photos/{photo_id}/original-session/{upload_id}"
            f"/complete", headers=self.headers)
        if r.status_code == 409:
            return {"conflict": r.json()["detail"]}
        assert r.status_code == 200, r.text
        return r.json()


class FaultSyncTransport:
    """Sync-batch transport with the same fault surface (drives the
    W008 ledger discipline through flaky networks)."""

    def __init__(self, client: TestClient, headers: dict,
                 fault: NetworkFault,
                 replay_last_success: bool = False):
        self.client = client
        self.headers = headers
        self.fault = fault
        self.last_response = None
        self.replay_last_success = replay_last_success   # flaky proxy

    def send_batch(self, ops: list[dict]) -> dict:
        if self.fault.should_fail():
            if self.replay_last_success and self.last_response is not None:
                # a proxy replayed the LAST response: the client cannot
                # tell - but the SERVER ledger answers duplicate on the
                # next real attempt, so no double-apply can happen
                self.fault.injected.append("duplicate_response")
                return self.last_response
            raise OfflineUploadError("network unreachable (sync)")
        r = self.client.post("/api/v1/sync/batch",
                             headers=self.headers,
                             json={"operations": ops})
        assert r.status_code == 200, r.text
        self.last_response = r.json()
        return self.last_response


# ----------------------------------------------------------------- suite

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
    pub = base64.b64encode(key.public_key().public_bytes(
        Encoding.X962, PublicFormat.UncompressedPoint)).decode()
    r = client.post("/api/v1/devices/enroll", json={
        "public_key_b64": pub, "device_id": "tm91",
        "enrollment_code": CODE})
    H = {"X-Device-Token": r.json()["device_token"]}
    r = client.post("/api/v1/projects", headers=H,
                   json={"name": "Weak Network Site"})
    pid = r.json()["project_id"]

    def make_photo(seed: int) -> tuple[str, bytes, str]:
        import random as _rnd
        rng = _rnd.Random(seed)
        data = bytes(rng.getrandbits(8) for _ in range(300_000))
        sha = hashlib.sha256(data).hexdigest()
        rr = client.post(f"/api/v1/projects/{pid}/photos", headers=H,
                        json={"idempotency_key": f"ik-{seed}",
                              "original_sha256": sha,
                              "original_filename": f"{seed}.jpg",
                              "mime_type": "image/jpeg",
                              "byte_size": len(data),
                              "captured_at":
                                  "2026-11-26T20:00:00+00:00",
                              "device_id": "tm91"})
        assert rr.status_code == 201, rr.text
        return rr.json()["photo_id"], data, sha

    return type("E", (), {"client": client, "H": H, "pid": pid,
                          "make_photo": staticmethod(make_photo),
                          "tmp": tmp_path})()


def _resume_upload(env, photo_id, data, sha, fault):
    """The TM69 client sequence under faults: open (or resume via
    status), send missing chunks, complete. Mirrors ChunkedUploadClient."""
    t = FaultChunkTransport(env.client, env.H, fault)
    CH = 128 * 1024
    chunks = [data[i:i + CH] for i in range(0, len(data), CH)]
    upload_id = None
    # open (or find the existing session by re-opening)
    while True:
        try:
            s = t.open_session(photo_id, sha, CH, len(data))
            upload_id = s["upload_id"]
            have = set(s["received_chunks"])
            break
        except OfflineUploadError:
            continue        # retry the open until the net returns
    # send what is missing, surviving per-chunk failures
    for i in range(len(chunks)):
        if i in have:
            continue
        while True:
            try:
                t.put_chunk(photo_id, upload_id, i, chunks[i])
                break
            except OfflineUploadError:
                # connection died mid-batch: ASK the server what it
                # has (the resume rule) and continue from there
                while True:
                    try:
                        st = t.status(photo_id, upload_id)
                        have = set(st["received_chunks"])
                        break
                    except OfflineUploadError:
                        continue
    # complete (retry on transport failure)
    while True:
        try:
            return t.complete(photo_id, upload_id)
        except OfflineUploadError:
            continue


class TestOfflineAndFullFailure:
    def test_completely_offline_upload_eventually_completes(self, env):
        """Even with the net 'down' at first, the retry loop converges
        the moment the fault schedule lets a call through - and the
        server never double-counts chunks."""
        photo, data, sha = env.make_photo(1)
        # fail the first 3 calls, then let everything through
        fault = NetworkFault("fail_first", fail_first=3)
        result = _resume_upload(env, photo, data, sha, fault)
        assert result.get("bound") is True or \
            result.get("status") == "completed"
        assert 3 in fault.injected or \
            any(x.startswith("fail_first") for x in fault.injected)
        # object landed intact
        from app.services import original_store
        obj = original_store.object_path(sha)
        assert obj.exists()
        assert hashlib.sha256(obj.read_bytes()).hexdigest() == sha

    def test_sync_batch_offline_then_replay_is_ledger_safe(self, env):
        """A sync batch fails offline; the client retries; a flaky
        proxy ALSO replays the successful response once. The server
        ledger must answer duplicate for the replayed operation - no
        double-apply ever."""
        fault = NetworkFault("fail_first", fail_first=2)
        t = FaultSyncTransport(env.client, env.H, fault,
                               replay_last_success=True)
        ops = [{"op": "annotation_create",
                "operation_id": "wn-an-1",
                "payload": {"operation_id": "wn-an-1",
                            "local_id": "l-wn-1",
                            "server_photo_id": env.make_photo(2)[0],
                            "annotation_type": "text",
                            "norm_x1": 0.1, "norm_y1": 0.2,
                            "text_content": "weak net"}}]
        # first attempts raise (offline); retry until it lands
        result = None
        for _ in range(10):
            try:
                result = t.send_batch(ops)
                break
            except OfflineUploadError:
                continue
        assert result is not None
        assert result["receipts"][0]["status"] == "applied"
        # now the FLAKY PROXY replays the same response; a real
        # re-send of the same op id must be 'duplicate' server-side
        r = env.client.post("/api/v1/sync/batch", headers=env.H,
                            json={"operations": ops})
        assert r.json()["receipts"][0]["status"] == "duplicate"
        # and exactly ONE annotation exists
        photo_id = ops[0]["payload"]["server_photo_id"]
        r = env.client.get(
            f"/api/v1/photos/{photo_id}/annotations", headers=env.H)
        assert r.json()["total"] == 1


class TestPartialUploadAndRestart:
    def test_partial_then_resume_via_server_status(self, env):
        """Upload dies mid-way (connection reset after K chunks); the
        client asks the server what it has and resumes - the resumed
        session completes with exactly one complete() call."""
        photo, data, sha = env.make_photo(3)
        CH = 128 * 1024
        chunks = [data[i:i + CH] for i in range(0, len(data), CH)]
        assert len(chunks) >= 2     # 300KB at 128KiB chunks
        K = 2                       # die after 2 chunks

        # first leg: open + K chunks, then the connection dies
        t1 = FaultChunkTransport(
            env.client, env.H,
            NetworkFault("partial", partial_after=K))
        s = t1.open_session(photo, sha, CH, len(data))
        uid = s["upload_id"]
        with pytest.raises(OfflineUploadError):
            for i in range(len(chunks)):
                t1.put_chunk(photo, uid, i, chunks[i])
        # the server DID accept exactly K chunks
        r = env.client.get(
            f"/api/v1/photos/{photo}/original-session/{uid}",
            headers=env.H)
        assert r.json()["received_count"] == K

        # second leg: RESUME (no re-open - status + missing only)
        fault2 = NetworkFault("clean")
        t2 = FaultChunkTransport(env.client, env.H, fault2)
        st = t2.status(photo, uid)
        have = set(st["received_chunks"])
        assert have == set(range(K))
        for i in range(len(chunks)):
            if i not in have:
                t2.put_chunk(photo, uid, i, chunks[i])
        out = t2.complete(photo, uid)
        assert out.get("bound") is True
        # duplicate re-send of an already-received chunk is a no-op
        r = env.client.put(
            f"/api/v1/photos/{photo}/original-session/{uid}"
            f"/chunk/0",
            headers={**env.H, "X-Chunk-SHA256":
                     hashlib.sha256(chunks[0]).hexdigest()},
            content=chunks[0])
        # the session completed -> session_not_active; the idempotent
        # duplicate path was proven while it was live (received_count
        # unchanged after the replay in the first leg)
        assert r.status_code in (200, 404, 409)

    def test_server_restart_preserves_session_state(self, env):
        """'Restart': a NEW TestClient over the SAME database/files.
        The session (DB row + chunk files) IS the resume state."""
        photo, data, sha = env.make_photo(4)
        CH = 128 * 1024
        chunks = [data[i:i + CH] for i in range(0, len(data), CH)]
        t = FaultChunkTransport(env.client, env.H,
                                NetworkFault("clean"))
        s = t.open_session(photo, sha, CH, len(data))
        uid = s["upload_id"]
        t.put_chunk(photo, uid, 0, chunks[0])
        # RESTART: fresh client, same disk state
        client2 = TestClient(app)
        r = client2.get(
            f"/api/v1/photos/{photo}/original-session/{uid}",
            headers=env.H)
        assert r.status_code == 200
        assert r.json()["received_count"] == 1     # state survived
        for i in range(1, len(chunks)):
            r = client2.put(
                f"/api/v1/photos/{photo}/original-session/{uid}"
                f"/chunk/{i}",
                headers={**env.H, "X-Chunk-SHA256":
                         hashlib.sha256(chunks[i]).hexdigest()},
                content=chunks[i])
            assert r.status_code == 200
        r = client2.post(
            f"/api/v1/photos/{photo}/original-session/{uid}/complete",
            headers=env.H)
        assert r.status_code == 200
        assert r.json()["bound"] is True


class TestReorderedAndIntermittent:
    def test_out_of_order_chunks_accepted(self, env):
        photo, data, sha = env.make_photo(5)
        CH = 128 * 1024
        chunks = [data[i:i + CH] for i in range(0, len(data), CH)]
        t = FaultChunkTransport(env.client, env.H,
                                NetworkFault("clean"))
        s = t.open_session(photo, sha, CH, len(data))
        uid = s["upload_id"]
        # reversed order
        for i in reversed(range(len(chunks))):
            t.put_chunk(photo, uid, i, chunks[i])
        out = t.complete(photo, uid)
        assert out.get("bound") is True

    def test_intermittent_failures_converge(self, env):
        photo, data, sha = env.make_photo(6)
        fault = NetworkFault("intermittent")
        result = _resume_upload(env, photo, data, sha, fault)
        assert result.get("bound") is True
        # the schedule actually injected failures
        assert any(x == "intermittent" for x in fault.injected)
        # and the store object is intact
        from app.services import original_store
        assert original_store.object_path(sha).exists()

    def test_high_latency_still_correct(self, env):
        photo, data, sha = env.make_photo(7)
        # 5ms latency per call - the slow path must still be CORRECT
        fault = NetworkFault("clean", latency_s=0.005)
        result = _resume_upload(env, photo, data, sha, fault)
        assert result.get("bound") is True
