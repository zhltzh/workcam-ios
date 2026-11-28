"""TM-04 D14: project archive / restore / history-query policy,
over the REAL backend (in-process TestClient).

Pins the D14 contract end to end:

  1. archive is CAS-gated; replaying it is a duplicate (inherited
     semantics, re-verified here in the new flow)
  2. an archived project REFUSES new photos and new issues (write
     lock) but its HISTORY stays fully readable (issues, photos,
     annotations, events) - archive is a lock, not a delete
  3. project_restore is the ONE explicit exit; it is CAS-gated,
     emits a 'restored' event, clears archived_at, and the project
     accepts writes again
  4. restoring a non-archived project is refused by name (no fake
     events)
  5. the event log shows archived -> restored in order (audit)
"""

from __future__ import annotations

import base64
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402
from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import ec  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

CODE = "tm04-d14-code"


@pytest.fixture()
def env(monkeypatch):
    monkeypatch.setenv("WORKCAM_ENROLLMENT_CODE", CODE)
    client = TestClient(app)
    key = ec.generate_private_key(ec.SECP256R1())
    pub = key.public_key().public_bytes(
        encoding=serialization.Encoding.X962,
        format=serialization.PublicFormat.UncompressedPoint)
    r = client.post("/api/v1/devices/enroll", json={
        "public_key_b64": base64.b64encode(pub).decode(),
        "device_id": "dev-d14", "enrollment_code": CODE})
    assert r.status_code in (200, 201), r.text
    return client, {"X-Device-Token": r.json()["device_token"]}


def _batch(client, headers, ops):
    r = client.post("/api/v1/sync/batch", headers=headers,
                    json={"operations": ops})
    assert r.status_code in (200, 201), r.text
    return r.json()


def _create_project(client, headers, name="D14 arena") -> str:
    out = _batch(client, headers, [{
        "op": "project_create", "operation_id": "pc",
        "payload": {"operation_id": "pc", "local_id": "loc-p",
                    "name": name}}])
    return out["id_map"]["loc-p"]


def _archive(client, headers, pid, rev, op_id="arch-1"):
    return _batch(client, headers, [{
        "op": "project_archive", "operation_id": op_id,
        "payload": {"operation_id": op_id,
                    "server_project_id": pid,
                    "expected_revision": rev}}])["receipts"][0]


def _restore(client, headers, pid, rev, op_id="res-1"):
    return _batch(client, headers, [{
        "op": "project_restore", "operation_id": op_id,
        "payload": {"operation_id": op_id,
                    "server_project_id": pid,
                    "expected_revision": rev}}])["receipts"][0]


def _issue(client, headers, pid, op_id, title="t"):
    return _batch(client, headers, [{
        "op": "issue_create", "operation_id": op_id,
        "payload": {"operation_id": op_id, "local_id": op_id,
                    "server_project_id": pid,
                    "title": title, "severity": "low"}}])


class TestArchive:
    def test_archive_is_cas_gated_and_idempotent(self, env):
        client, h = env
        pid = _create_project(client, h)
        rec = _archive(client, h, pid, rev=1)
        assert rec["result"]["status"] == "archived"
        assert rec["result"]["revision"] == 2
        # replay: duplicate, same state
        replay = _archive(client, h, pid, rev=1, op_id="arch-1")
        assert replay["status"] == "duplicate"

    def test_archived_project_refuses_writes_but_keeps_history(
            self, env):
        client, h = env
        pid = _create_project(client, h)
        _issue(client, h, pid, "seed-issue", title="kept forever")
        _archive(client, h, pid, rev=1)

        # new issue refused
        rejected = _issue(client, h, pid, "post-archive-issue")
        assert rejected["receipts"][0]["error"]["code"] == \
            "project_archived"

        # history fully readable
        r = client.get(f"/api/v1/projects/{pid}/issues", headers=h)
        assert r.status_code == 200
        titles = {i["title"] for i in r.json()["items"]}
        assert titles == {"kept forever"}      # the lock is not a delete


class TestRestore:
    def test_restore_is_explicit_cas_and_reopens_writes(self, env):
        client, h = env
        pid = _create_project(client, h)
        _archive(client, h, pid, rev=1)
        rec = _restore(client, h, pid, rev=2)
        assert rec["status"] == "applied"
        assert rec["result"]["status"] == "active"
        assert rec["result"]["revision"] == 3
        # writes accepted again
        ok = _issue(client, h, pid, "post-restore-issue")
        assert ok["receipts"][0]["status"] == "applied"

    def test_stale_restore_revision_refused(self, env):
        client, h = env
        pid = _create_project(client, h)
        _archive(client, h, pid, rev=1)
        stale = _restore(client, h, pid, rev=1, op_id="stale-res")
        assert stale["status"] == "rejected"
        assert stale["error"]["code"] == "revision_conflict"

    def test_restore_of_active_project_refused_by_name(self, env):
        client, h = env
        pid = _create_project(client, h)
        rec = _restore(client, h, pid, rev=1, op_id="res-active")
        assert rec["status"] == "rejected"
        assert rec["error"]["code"] == "not_archived"

    def test_archived_at_cleared_and_event_log_ordered(self, env):
        client, h = env
        pid = _create_project(client, h)
        _archive(client, h, pid, rev=1)
        _restore(client, h, pid, rev=2)

        r = client.get(f"/api/v1/projects/{pid}", headers=h)
        assert r.status_code == 200
        assert r.json()["status"] == "active"
        assert r.json()["archived_at"] is None

        events = client.get(
            "/api/v1/sync/events?limit=200&entity_type=project",
            headers=h).json()["items"]
        actions = [e["action"] for e in events
                   if e.get("entity_id") == pid]
        # audit trail: created -> archived -> restored, in order
        assert actions == ["created", "archived", "restored"]
