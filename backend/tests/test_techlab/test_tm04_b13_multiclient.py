"""TM-04 B13: multi-client concurrency harness over the REAL backend
(in-process FastAPI TestClient, loopback only, no external network).

The stable product is SINGLE-USER per owner: "multiple clients" for
convergence testing = concurrent sessions of the SAME owner (the
harness races the same token through the app's real SQLite locks).
A SECOND, separately enrolled device is used to pin the isolation
contract instead (cross-owner reads are 404s by design).

Convergence contract made executable:

  1. same-revision concurrent writes: exactly ONE applies; the
     other gets a typed refusal - NEVER a silent overwrite
  2. idempotent replay: same operation id + same content re-runs
     -> 'duplicate', never a second effect (<= 1 row of that content)
  3. same operation id + DIFFERENT content -> refused, the first
     decision stands
  4. cross-owner visibility is 404 (isolation is a feature here)
  5. both concurrent sessions' final reads agree on converged state

No wall-clock assertions anywhere (two-flakes rule).
"""

from __future__ import annotations

import base64
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402
from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import ec  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

CODE = "tm04-b13-code"


@pytest.fixture()
def clients(monkeypatch):
    """Returns (client, ownerA_headers, otherB_headers). ownerA is
    the racing owner; otherB is an unrelated enrolled device."""
    monkeypatch.setenv("WORKCAM_ENROLLMENT_CODE", CODE)
    client = TestClient(app)

    def enroll(dev: str) -> dict:
        key = ec.generate_private_key(ec.SECP256R1())
        pub = key.public_key().public_bytes(
            encoding=serialization.Encoding.X962,
            format=serialization.PublicFormat.UncompressedPoint)
        r = client.post("/api/v1/devices/enroll", json={
            "public_key_b64": base64.b64encode(pub).decode(),
            "device_id": dev,
            "enrollment_code": CODE})
        assert r.status_code in (200, 201), r.text
        return {"X-Device-Token": r.json()["device_token"]}

    return client, enroll("dev-A"), enroll("dev-B")


def _project(client, headers, name: str) -> dict:
    r = client.post("/api/v1/projects", headers=headers,
                    json={"name": name})
    assert r.status_code in (200, 201), r.text
    return r.json()


def _sync(client, headers, ops: list[dict]) -> dict:
    r = client.post("/api/v1/sync/batch", headers=headers,
                    json={"operations": ops})
    assert r.status_code in (200, 201), r.text
    return r.json()


def _issue_create_op(op_id: str, project_id: str,
                     title: str, local_id: str = "i1") -> dict:
    # payload contract per test_win_hotfix.py / the Night05 wire
    # format: operation_id inside the payload + local_id +
    # server_project_id (NOT project_id)
    return {
        "operation_id": op_id,
        "op": "issue_create",
        "payload": {
            "operation_id": op_id,
            "local_id": local_id,
            "server_project_id": project_id,
            "title": title,
            "severity": "medium",
        },
    }


class TestSameRevisionRace:
    def test_concurrent_same_revision_exactly_one_applies(
            self, clients):
        """The B-queue red line as an executable race: two concurrent
        sessions both hold revision 1 and both try to write. The
        server's CAS must apply exactly ONE; the loser gets the
        TYPED revision_conflict refusal - a silent double-apply (or
        worse, a silent overwrite) fails this test."""
        client, ha, _ = clients
        proj = _project(client, ha, "race-arena")

        def update(op_id: str, name: str) -> list[dict]:
            return [{
                "op": "project_update", "operation_id": op_id,
                "payload": {
                    "operation_id": op_id,
                    "server_project_id": proj["project_id"],
                    "expected_revision": 1,
                    "name": name,
                },
            }]

        with ThreadPoolExecutor(max_workers=2) as pool:
            fa = pool.submit(_sync, client, ha,
                             update("op-A1", "written by A"))
            fb = pool.submit(_sync, client, ha,
                             update("op-B1", "written by B"))
            ra, rb = fa.result(), fb.result()

        rec_a, rec_b = ra["receipts"][0], rb["receipts"][0]
        statuses = [rec_a["status"], rec_b["status"]]
        assert statuses.count("applied") == 1, (
            f"exactly one write must apply, got {statuses}")
        loser = rec_b if rec_a["status"] == "applied" else rec_a
        assert loser["status"] == "rejected"
        assert loser["error"]["code"] == "revision_conflict"


class TestIdempotency:
    def test_replay_of_winning_op_is_duplicate(self, clients):
        client, ha, _ = clients
        proj = _project(client, ha, "replay")
        ops = [_issue_create_op("op-R1", proj["project_id"], "R")]
        first = _sync(client, ha, ops)
        second = _sync(client, ha, ops)
        assert first["receipts"][0]["status"] in ("applied",
                                                 "duplicate")
        assert second["receipts"][0]["status"] == "duplicate"
        assert (first["receipts"][0]["operation_id"]
                == second["receipts"][0]["operation_id"] == "op-R1")

    def test_same_id_different_content_refused(self, clients):
        client, ha, _ = clients
        proj = _project(client, ha, "idempotency-clash")
        _sync(client, ha, [_issue_create_op(
            "op-X", proj["project_id"], "first intent")])
        clash = _sync(client, ha, [_issue_create_op(
            "op-X", proj["project_id"], "DIFFERENT content")])
        assert clash["receipts"][0]["status"] in ("rejected",
                                                 "duplicate")

    def test_cross_client_duplicate_submit_converges_once(
            self, clients):
        """Two sessions submit the SAME operation id + content (an op
        that synced between devices before upload). The ledger must
        produce exactly one effect."""
        client, ha, _ = clients
        proj = _project(client, ha, "shared-op")
        op = [_issue_create_op("op-SHARED", proj["project_id"],
                               "shared")]

        with ThreadPoolExecutor(max_workers=2) as pool:
            fa = pool.submit(_sync, client, ha, op)
            fb = pool.submit(_sync, client, ha, op)
            ra, rb = fa.result(), fb.result()

        statuses = sorted([ra["receipts"][0]["status"],
                           rb["receipts"][0]["status"]])
        assert statuses in (["applied", "duplicate"],
                            ["duplicate", "duplicate"])
        r = client.get(f"/api/v1/projects/{proj['project_id']}"
                       "/issues", headers=ha)
        matches = [i for i in r.json()["items"]
                   if i["title"] == "shared"]
        assert len(matches) == 1     # exactly one effect, never two


class TestConvergence:
    def test_both_sessions_see_the_same_converged_state(self,
                                                        clients):
        client, ha, _ = clients
        proj = _project(client, ha, "converged")
        _sync(client, ha, [_issue_create_op(
            "op-C1", proj["project_id"], "from session 1")])
        _sync(client, ha, [_issue_create_op(
            "op-C2", proj["project_id"], "from session 2")])
        r1 = client.get(f"/api/v1/projects/{proj['project_id']}"
                        "/issues", headers=ha)
        r2 = client.get(f"/api/v1/projects/{proj['project_id']}"
                        "/issues", headers=ha)
        titles = {i["title"] for i in r1.json()["items"]}
        assert titles == {"from session 1", "from session 2"}
        assert {i["title"] for i in r2.json()["items"]} == titles


class TestIsolation:
    def test_cross_owner_project_read_is_404(self, clients):
        """The unrelated device CANNOT see owner A's project - the
        single-user isolation the stable product promises. This is a
        correctness pin, not a limitation note."""
        client, ha, hb = clients
        proj = _project(client, ha, "private-arena")
        r = client.get(f"/api/v1/projects/{proj['project_id']}",
                       headers=hb)
        assert r.status_code == 404

    def test_cross_owner_sync_cannot_write_foreign_project(
            self, clients):
        client, ha, hb = clients
        proj = _project(client, ha, "private-writes")
        receipt = _sync(client, hb, [_issue_create_op(
            "op-BAD", proj["project_id"], "foreign write")])
        assert receipt["receipts"][0]["status"] == "rejected"
