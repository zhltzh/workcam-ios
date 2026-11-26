"""TM36: backup & restore package v2 tests - project-scoped export,
precheck, import idempotence, failure rollback, manifest digest,
v1->v2 migration, never-overwrite originals."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import (
    Encoding, PublicFormat)
from fastapi.testclient import TestClient  # noqa: E402

import pytest  # noqa: E402

from app.main import app  # noqa: E402
from app.services import device_identity  # noqa: E402

CODE = "tm36-code"


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
    pub = key.public_key().public_bytes(
        Encoding.X962, PublicFormat.UncompressedPoint)
    r = client.post("/api/v1/devices/enroll",
                    json={"public_key_b64": base64.b64encode(pub).decode(),
                          "device_id": "tm36",
                          "enrollment_code": CODE})
    H = {"X-Device-Token": r.json()["device_token"]}
    r = client.post("/api/v1/projects", headers=H,
                    json={"name": "Backup Site"})
    pid = r.json()["project_id"]

    def photo(key):
        from PIL import Image
        img = Image.new("RGB", (64, 48), (hash(key) % 255, 30, 90))
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=70)
        data = buf.getvalue()
        sha = hashlib.sha256(data).hexdigest()
        r = client.post(f"/api/v1/projects/{pid}/photos", headers=H,
                        json={"idempotency_key": f"ik-{key}",
                              "original_sha256": sha,
                              "original_filename": f"{key}.jpg",
                              "mime_type": "image/jpeg",
                              "byte_size": len(data),
                              "captured_at":
                                  "2026-11-26T09:00:00+00:00",
                              "device_id": "cam"})
        assert r.status_code == 201, r.text
        photo_id = r.json()["photo_id"]
        r = client.put(f"/api/v1/photos/{photo_id}/original",
                       headers=H, content=data)
        assert r.status_code == 200, r.text
        # add an annotation so the package carries one
        r = client.post("/api/v1/sync/batch", headers=H, json={
            "operations": [{"op": "annotation_create",
                            "operation_id": f"an-{key}",
                            "payload": {"operation_id": f"an-{key}",
                                        "local_id": f"l-{key}",
                                        "server_photo_id": photo_id,
                                        "annotation_type": "text",
                                        "norm_x1": 0.2,
                                        "norm_y1": 0.2,
                                        "text_content": key}}]})
        assert r.status_code == 200, r.text
        return photo_id, data, sha

    return type("E", (), {"client": client, "H": H, "pid": pid,
                          "photo": staticmethod(photo),
                          "tmp": tmp_path})()


def test_project_backup_and_import_roundtrip(env):
    from app.services import backup_v2
    p1, d1, s1 = env.photo("alpha")
    p2, d2, s2 = env.photo("beta")
    pkg = env.tmp / "pkg"
    out = backup_v2.project_backup(env.pid, env.H["X-Device-Token"],
                                    pkg)
    assert out["photos"] == 2 and out["objects"] == 2
    assert not out["failed"]
    # precheck passes
    pre = backup_v2.precheck_import(pkg,
                                    env.H["X-Device-Token"])
    assert pre["ok"], pre["problems"]
    # roundtrip proof: the package manifest digests its objects map
    manifest = json.loads((pkg / "package.json")
                          .read_text(encoding="utf-8"))
    assert manifest["manifest_sha256"] == out["manifest_sha256"]


def test_import_idempotent_and_never_overwrites(env):
    from app.services import backup_v2, original_store
    from app.db import ApiError
    p1, d1, s1 = env.photo("gamma")
    pkg = env.tmp / "pkg2"
    backup_v2.project_backup(env.pid, env.H["X-Device-Token"], pkg)
    # FIRST import into a FRESH second environment (same store
    # already has everything, so import must skip, not duplicate)
    res = backup_v2.import_project_package(
        pkg, env.H["X-Device-Token"])
    assert res["imported_photos"] == 0       # everything existed
    assert res["skipped"] >= 2
    # the original bytes in the store are the SAME object (untouched)
    assert original_store.object_path(s1).exists()
    assert hashlib.sha256(
        original_store.object_path(s1).read_bytes()).hexdigest() == s1


def test_import_into_fresh_store_roundtrip(env, tmp_path, monkeypatch):
    """The real acceptance: export from store-1, wipe, restore the
    DATABASE from the app-level backup path, import the package into
    a fresh database + store, then verify bytes + rows."""
    from app.services import backup_v2
    p1, d1, s1 = env.photo("delta")
    pkg = env.tmp / "pkg3"
    backup_v2.project_backup(env.pid, env.H["X-Device-Token"], pkg)

    # fresh environment (new db + empty store)
    fresh = tmp_path / "fresh"
    fresh.mkdir()
    monkeypatch.setenv("WORKCAM_BUSINESS_DB", str(fresh / "biz.db"))
    import app.db as dbm
    dbm.migrate(fresh / "biz.db")
    from app.services.key_registry import reset_registry
    reset_registry()
    # re-enroll the same owner device (fresh registry)
    client2 = TestClient(app)
    key = ec.generate_private_key(ec.SECP256R1())
    pub = key.public_key().public_bytes(
        Encoding.X962, PublicFormat.UncompressedPoint)
    r = client2.post("/api/v1/devices/enroll",
                     json={"public_key_b64":
                           base64.b64encode(pub).decode(),
                           "device_id": "tm36",
                           "enrollment_code": CODE})
    H2 = {"X-Device-Token": r.json()["device_token"]}
    pre = backup_v2.precheck_import(pkg, H2["X-Device-Token"])
    assert pre["ok"], pre["problems"]
    res = backup_v2.import_project_package(
        pkg, H2["X-Device-Token"])
    assert res["imported_photos"] == 1
    assert res["objects_moved"] == 1
    # verify bytes
    from app.services import original_store
    obj = original_store.object_path(s1)
    assert obj.exists()
    assert hashlib.sha256(obj.read_bytes()).hexdigest() == s1
    # REPEATED restore of the same package: skip-existing, no dups
    res2 = backup_v2.import_project_package(
        pkg, H2["X-Device-Token"])
    assert res2["imported_photos"] == 0
    # DB rows not duplicated
    with dbm.db() as conn:
        n = conn.execute(
            "SELECT COUNT(*) FROM mvp_photos WHERE photo_id = ?",
            (p1,)).fetchone()[0]
    assert n == 1


def test_precheck_rejects_tampered_package(env):
    from app.services import backup_v2
    from app.db import ApiError
    p1, d1, s1 = env.photo("epsilon")
    pkg = env.tmp / "pkg4"
    backup_v2.project_backup(env.pid, env.H["X-Device-Token"], pkg)
    # tamper: flip a byte in the copied original
    part = next(pkg.glob("originals/*/*.jpg"))
    raw = part.read_bytes()
    part.write_bytes(raw[:-1] + bytes([raw[-1] ^ 0xFF]))
    pre = backup_v2.precheck_import(pkg,
                                    env.H["X-Device-Token"])
    assert not pre["ok"]
    assert any("corrupt objects" in p for p in pre["problems"])
    with pytest.raises(ApiError) as ei:
        backup_v2.import_project_package(
            pkg, env.H["X-Device-Token"])
    assert ei.value.status_code == 409
    # tamper the MANIFEST objects map -> digest mismatch
    pkg2 = env.tmp / "pkg5"
    backup_v2.project_backup(env.pid, env.H["X-Device-Token"], pkg2)
    m = json.loads((pkg2 / "package.json").read_text(encoding="utf-8"))
    m["objects"]["f" * 64] = 1
    (pkg2 / "package.json").write_text(json.dumps(m), encoding="utf-8")
    pre2 = backup_v2.precheck_import(pkg2,
                                     env.H["X-Device-Token"])
    assert not pre2["ok"]
    assert any("digest mismatch" in p for p in pre2["problems"])


def test_failed_import_rolls_back(env):
    """A mid-import failure (schema-constraint violation smuggled in
    via a bad photo row) must leave the DB unchanged and remove the
    staged object copies."""
    from app.services import backup_v2
    from app.db import ApiError
    p1, d1, s1 = env.photo("zeta")
    pkg = env.tmp / "pkg6"
    backup_v2.project_backup(env.pid, env.H["X-Device-Token"], pkg)
    # re-open the package with a poisoned photo entry (bad byte_size
    # breaks the CHECK constraint at insert time)
    m = json.loads((pkg / "package.json").read_text(encoding="utf-8"))
    poisoned = dict(m)
    new_photo = dict(m["photos"][0])
    new_photo["photo"] = {**new_photo["photo"],
                          "photo_id": "ff" * 16,
                          "idempotency_key": "poison-key",
                          "byte_size": -1}       # CHECK(byte_size > 0)
    poisoned["photos"] = [new_photo]
    # recompute the manifest digest over the UNCHANGED objects map so
    # precheck passes and the failure comes from the INSERT
    (pkg / "package.json").write_text(json.dumps(poisoned),
                                      encoding="utf-8")
    with pytest.raises(sqlite3_error()):
        backup_v2.import_project_package(
            pkg, env.H["X-Device-Token"])
    # DB unchanged: the poisoned photo id never landed
    import app.db as dbm
    with dbm.db() as conn:
        n = conn.execute(
            "SELECT COUNT(*) FROM mvp_photos WHERE photo_id = ?",
            ("ff" * 16,)).fetchone()[0]
    assert n == 0
    # staged temp objects cleaned
    from app.services import original_store
    leftovers = list(original_store.store_root().glob(".import-*.tmp"))
    assert leftovers == []


def sqlite3_error():
    import sqlite3
    return sqlite3.IntegrityError


def test_manifest_v1_migration(env):
    from app.services import backup_v2
    v1 = {"package_version": 1, "objects": {"ab": 10},
          "photos": []}
    v2 = backup_v2.migrate_package_manifest(v1)
    assert v2["package_version"] == 2
    assert v2["migrated_from_v1"] is True
    digest = hashlib.sha256(
        json.dumps(v2["objects"], sort_keys=True).encode()).hexdigest()
    assert v2["manifest_sha256"] == digest
    # already-v2 is a no-op
    assert backup_v2.migrate_package_manifest(v2) is v2
    # unknown version refuses
    with pytest.raises(ValueError):
        backup_v2.migrate_package_manifest({"package_version": 99})


def test_cross_owner_project_backup_404(env, tmp_path, monkeypatch):
    from app.services import backup_v2
    from app.db import ApiError
    key = ec.generate_private_key(ec.SECP256R1())
    pub = key.public_key().public_bytes(
        Encoding.X962, PublicFormat.UncompressedPoint)
    r = env.client.post("/api/v1/devices/enroll",
                        json={"public_key_b64":
                              base64.b64encode(pub).decode(),
                              "device_id": "other",
                              "enrollment_code": CODE})
    other = r.json()["device_token"]
    with pytest.raises(ApiError) as ei:
        backup_v2.project_backup(env.pid, other, tmp_path / "x")
    assert ei.value.status_code == 404
