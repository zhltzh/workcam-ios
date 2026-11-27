import base64
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from app.db import ApiError, db, migrate
from app.services import device_identity as identity
from tests.test_mvp_sync import _batch, _pc, env


def public_key():
    key = ec.generate_private_key(ec.SECP256R1())
    return base64.b64encode(key.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)).decode()


def test_enrollment_atomic_audit_concurrency_and_retry(tmp_path, monkeypatch):
    db_path = tmp_path / "hotfix.db"
    monkeypatch.setenv("WORKCAM_BUSINESS_DB", str(db_path))
    monkeypatch.setenv("WORKCAM_ENROLLMENT_CODE", "test-only-hotfix")
    migrate(db_path)

    def enroll(public):
        return identity.enroll_device(public, "synthetic fixture", "test-only-hotfix")

    key = public_key()
    with ThreadPoolExecutor(max_workers=8) as threads:
        result = list(threads.map(enroll, [key] * 16))
    assert len(set(result)) == 1
    token = result[0]
    assert enroll(public_key()) != token
    with db() as conn:
        events = conn.execute(
            "SELECT event_type, device_token FROM mvp_audit_log WHERE device_token = ?",
            (identity.token_fingerprint(token),)).fetchall()
        assert [e["event_type"] for e in events].count("device_enrolled") == 1
        assert [e["event_type"] for e in events].count("device_enroll_replay") == 15
        assert all(e["device_token"] != token for e in events)

    identity.suspend_device(token, "test-only-hotfix")
    with pytest.raises(ApiError) as failure:
        enroll(key)
    assert failure.value.detail["code"] == "device_suspended"
    identity.resume_device(token, "test-only-hotfix")
    assert enroll(key) == token

    other = public_key()
    original_audit = identity._write_audit
    with monkeypatch.context() as patch:
        def failing_audit(*_):
            raise sqlite3.OperationalError("audit fault")
        patch.setattr(identity, "_write_audit", failing_audit)
        with pytest.raises(sqlite3.OperationalError):
            enroll(other)
    assert identity._write_audit is original_audit
    with db() as conn:
        assert conn.execute("SELECT 1 FROM mvp_devices WHERE public_key_b64=?", (other,)).fetchone() is None
    assert enroll(other)


def test_archive_conflict_replay_and_owner_cursor(env):
    project = _batch(env, [_pc()]).json()["id_map"]["loc-p1"]
    for i in range(3):
        _batch(env, [_pc(f"other-{i}", f"other-local-{i}")], headers=env.B)
    archive = {"op": "project_archive", "operation_id": "archive-one", "payload": {
        "operation_id": "archive-one", "server_project_id": project, "expected_revision": 1}}
    result = _batch(env, [archive]).json()["receipts"][0]
    assert result["result"]["status"] == "archived"
    assert result["result"]["revision"] == 2
    assert _batch(env, [archive]).json()["receipts"][0]["status"] == "duplicate"
    stale = {"op": "project_update", "operation_id": "rename-stale", "payload": {
        "operation_id": "rename-stale", "server_project_id": project, "expected_revision": 1, "name": "lost edit"}}
    first = _batch(env, [stale]).json()["receipts"][0]
    assert first["error"]["code"] == "project_archived"
    replay = _batch(env, [stale]).json()["receipts"][0]
    assert replay["status"] == "duplicate" and replay["result"] == {"status": "rejected"}
    assert replay["error"] == first["error"]
    first_page = env.client.get("/api/v1/sync/events?limit=1", headers=env.H).json()
    second_page = env.client.get(f"/api/v1/sync/events?cursor={first_page['cursor']}&limit=1&include_state=true",
                                 headers=env.H).json()
    assert len(first_page["items"]) == 1 and first_page["has_more"]
    assert second_page["items"][0]["action"] == "archived"
    assert second_page["items"][0]["state"]["status"] == "archived"
    assert "owner_token" not in second_page["items"][0]["state"]
    assert not second_page["has_more"]
    future = env.client.get("/api/v1/sync/events?cursor=10000", headers=env.H).json()
    assert future["cursor"] == 10000 and not future["items"]


def test_role_compare_and_swap_unlink_and_return(env):
    from tests.test_mvp_sync import _pr

    project = _batch(env, [_pc()]).json()["id_map"]["loc-p1"]
    photos = _batch(env, [_pr("pa", "a", project), _pr("pb", "b", project)]).json()["id_map"]
    issue = _batch(env, [{"op": "issue_create", "operation_id": "ticket", "payload": {
        "operation_id": "ticket", "local_id": "issue", "server_project_id": project,
        "title": "Fixture", "severity": "high"}}]).json()["id_map"]["issue"]

    def role(operation, key, target, previous=None):
        payload = {"operation_id": key, "server_issue_id": issue, "server_photo_id": photos[target],
                   "role": "before"}
        if previous:
            payload["expected_previous_photo_id"] = photos[previous]
        return _batch(env, [{"op": operation, "operation_id": key, "payload": payload}]).json()["receipts"][0]

    assert role("issue_photo_link", "link-a", "a")["status"] == "applied"
    assert role("issue_photo_link", "stale-b", "b")["error"]["code"] == "photo_role_conflict"
    assert role("issue_photo_link", "link-b", "b", "a")["status"] == "applied"
    assert role("issue_photo_unlink", "unlink-stale", "a")["status"] == "rejected"
    assert role("issue_photo_unlink", "unlink-b", "b")["status"] == "applied"
    assert role("issue_photo_link", "relink-a", "a")["status"] == "applied"
    assert role("issue_photo_link", "link-b", "b", "a")["status"] == "duplicate"
    events = env.client.get("/api/v1/sync/events?entity_type=issue&include_state=true", headers=env.H).json()
    assert events["items"][-1]["state"]["photos"] == [{"photo_id": photos["a"], "role": "before"}]


def test_rejected_operation_rolls_back_partial_mutation(env, monkeypatch):
    from app.api import sync

    actual = sync._EXECUTORS["project_create"]
    def partial(conn, owner, payload):
        actual(conn, owner, payload)
        raise ApiError(409, "injected_after_write", "rollback fixture")

    monkeypatch.setitem(sync._EXECUTORS, "project_create", partial)
    operation = _pc("rollback-write", "rolled-back")
    first = _batch(env, [operation]).json()["receipts"][0]
    assert first["error"]["code"] == "injected_after_write"
    assert env.client.get("/api/v1/sync/events", headers=env.H).json()["items"] == []
    monkeypatch.setitem(sync._EXECUTORS, "project_create", actual)
    replay = _batch(env, [operation]).json()["receipts"][0]
    assert replay["result"]["status"] == "rejected"
    assert replay["error"] == first["error"]
    assert _batch(env, [_pc("retry-new-id", "rolled-back")]).json()["receipts"][0]["status"] == "applied"
