"""B02-W026 contract tests.

Purpose: keep docs/api/* from drifting away from the real code.

  1. The repo copy of openapi.json must match the LIVE export of
     app.main:app path-for-path and method-for-method.
  2. The recorded mock data (docs/api/b02-w026-mock-data.json) must be
     structurally reproducible: replaying the same real request
     sequence on a fresh DB yields the same statuses, the same key
     sets, the same list lengths and the same scalar types.
  3. Baseline-specific facts (event-stream coverage, payload contract,
     pagination clamp, trust-note literals, error envelope, legacy
     token shutdown) are pinned with real requests.

Nothing here asserts hand-written JSON: the oracle is always either the
live app or the fixture file, and the fixture file itself was produced
by real calls (backend/scripts/b02_w026_generate_fixtures.py).
"""

from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import re
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import (
    Encoding, PublicFormat)
from fastapi.testclient import TestClient

from app.main import app

REPO_ROOT = Path(__file__).resolve().parents[2]
OPENAPI_REPO = REPO_ROOT / "docs" / "api" / "openapi.json"
MOCK_REPO = REPO_ROOT / "docs" / "api" / "b02-w026-mock-data.json"

METHODS = ("get", "post", "put", "patch", "delete")


def _load_generator():
    spec = importlib.util.spec_from_file_location(
        "b02_w026_generate_fixtures",
        Path(__file__).resolve().parents[1] / "scripts"
        / "b02_w026_generate_fixtures.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def replay_env(tmp_path_factory, monkeypatch_module):
    """Isolated business DB + registries for the module-wide replay."""
    root = tmp_path_factory.mktemp("b02w026-replay")
    monkeypatch_module.setenv("WORKCAM_BUSINESS_DB", str(root / "biz.db"))
    monkeypatch_module.setenv("WORKCAM_ORIGINAL_STORE",
                              str(root / "originals"))
    monkeypatch_module.setenv("WORKCAM_ENROLLMENT_CODE",
                              "MOCK-ENROLL-CODE-2626")
    monkeypatch_module.setenv("WORKCAM_DEVICE_REGISTRY",
                              str(root / "devices.jsonl"))
    monkeypatch_module.setenv("WORKCAM_KEY_REGISTRY",
                              str(root / "keys.jsonl"))
    monkeypatch_module.setenv("WORKCAM_RECORD_STORE",
                              str(root / "records.jsonl"))
    monkeypatch_module.setenv("WORKCAM_TIME_ANCHOR_STORE",
                              str(root / "anchors"))
    import app.db as dbm
    dbm.migrate(root / "biz.db")
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture(scope="module")
def monkeypatch_module(request):
    from _pytest.monkeypatch import MonkeyPatch
    mp = MonkeyPatch()
    request.addfinalizer(mp.undo)
    return mp


@pytest.fixture(scope="module")
def replayed(replay_env):
    """One full replay of the recorded F1-F6 + ERRORS sequence."""
    mod = _load_generator()
    return mod.run_all(replay_env)


def _fresh_device(client) -> tuple[dict, str]:
    """Enroll a NEW device (fresh rate-limit budget); returns headers
    and the enrolled public key b64."""
    priv = ec.generate_private_key(ec.SECP256R1())
    pub_b64 = base64.b64encode(priv.public_key().public_bytes(
        Encoding.X962, PublicFormat.UncompressedPoint)).decode()
    r = client.post("/api/v1/devices/enroll", json={
        "public_key_b64": pub_b64, "device_id": "contract-fresh",
        "enrollment_code": "MOCK-ENROLL-CODE-2626"})
    assert r.status_code == 201, r.text
    return {"X-Device-Token": r.json()["device_token"]}, pub_b64


@pytest.fixture(scope="module")
def repo_openapi() -> dict:
    return json.loads(OPENAPI_REPO.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def repo_mock() -> dict:
    return json.loads(MOCK_REPO.read_text(encoding="utf-8"))


# ------------------------------------------------------------ openapi


def test_openapi_paths_match_repo_copy(repo_openapi):
    live = app.openapi()
    assert set(live["paths"]) == set(repo_openapi["paths"])
    for path, item in repo_openapi["paths"].items():
        live_methods = {m for m in live["paths"][path] if m in METHODS}
        repo_methods = {m for m in item if m in METHODS}
        assert live_methods == repo_methods, path


def test_endpoint_inventory_counts(repo_openapi):
    paths = repo_openapi["paths"]
    ops = sum(1 for p in paths.values() for m in p if m in METHODS)
    assert len(paths) == 47
    assert ops == 58

    def surface(path: str) -> str:
        if path.startswith("/api/v1/sync"):
            return "sync"
        if path.startswith("/api/v1/devices"):
            return "devices"
        if path.startswith("/api/v1"):
            return "api-v1-business"
        if path.startswith("/upload/experimental"):
            return "upload-experimental"
        if path.startswith("/time-anchor"):
            return "time-anchor"
        return "verify-core"

    counts: dict[str, list[int]] = {}
    for p, item in paths.items():
        c = counts.setdefault(surface(p), [0, 0])
        c[0] += 1
        c[1] += sum(1 for m in item if m in METHODS)
    assert counts["verify-core"] == [5, 5]
    assert counts["time-anchor"] == [5, 5]
    assert counts["upload-experimental"] == [7, 7]
    assert counts["sync"] == [2, 2]
    assert counts["devices"] == [5, 5]
    assert counts["api-v1-business"] == [23, 34]


def test_openapi_enrichment_marker(repo_openapi):
    marker = repo_openapi.get("x-b02-w026-enrichment")
    assert marker, "enrichment marker must stay in the repo copy"
    assert marker["baseline"] == "31917e3"
    assert "manually_enriched_fields" in marker
    assert "securitySchemes" in repo_openapi["components"]


def test_openapi_security_annotations(repo_openapi):
    """Every /api/v1 operation must declare the deviceToken scheme."""
    for path, item in repo_openapi["paths"].items():
        if not path.startswith("/api/v1"):
            continue
        for method, op in item.items():
            if method not in METHODS:
                continue
            if "{device_token}" in path and method in ("get", "post") \
                    and "revoke" in path or (
                    "{device_token}" in path and method == "get"):
                assert op.get("security") == [{"adminCode": []}], \
                    f"{method} {path} must stay admin-gated"
                continue
            if path == "/api/v1/devices/enroll":
                continue
            assert op.get("security") == [{"deviceToken": []}], \
                f"{method} {path} must require X-Device-Token"


# ------------------------------------------------------- fixture replay


def _assert_same_shape(actual, recorded, where: str,
                       dynamic_key_dicts: bool = False):
    """Structure equality: same key sets, same list lengths, same
    scalar types. Values (uuids/timestamps/hashes) may differ."""
    if isinstance(recorded, dict):
        assert isinstance(actual, dict), f"{where}: dict expected"
        if not dynamic_key_dicts:
            assert set(actual) == set(recorded), \
                f"{where}: key drift {set(actual) ^ set(recorded)}"
            for k in recorded:
                _assert_same_shape(actual[k], recorded[k], f"{where}.{k}")
        else:
            # dynamic keys (photo ids inside report snapshots): same
            # size; values compared pairwise in insertion order, which
            # is deterministic (both sides are built from photo_ids
            # in request order)
            assert len(actual) == len(recorded), f"{where}: dict size"
            for (ka, va), (kr, vr) in zip(actual.items(),
                                          recorded.items()):
                _assert_same_shape(va, vr, f"{where}[{ka}]")
    elif isinstance(recorded, list):
        assert isinstance(actual, list), f"{where}: list expected"
        assert len(actual) == len(recorded), f"{where}: list length"
        for i, (a, r) in enumerate(zip(actual, recorded)):
            _assert_same_shape(a, r, f"{where}[{i}]")
    elif isinstance(recorded, bool):
        assert isinstance(actual, bool), f"{where}: bool expected"
    elif isinstance(recorded, int):
        assert isinstance(actual, int) and not isinstance(actual, bool), \
            f"{where}: int expected"
    elif isinstance(recorded, float):
        assert isinstance(actual, (int, float)) \
            and not isinstance(actual, bool), f"{where}: number expected"
    elif recorded is None:
        assert actual is None, f"{where}: null expected"
    else:
        assert isinstance(actual, str), f"{where}: str expected"


def _assert_binary_shape(actual, recorded, where: str):
    assert isinstance(actual, dict) and actual.get("__binary__") is True, \
        where
    assert isinstance(actual["bytes"], int), where
    assert isinstance(actual["sha256"], str) \
        and len(actual["sha256"]) == 64, where
    assert isinstance(actual["media_type"], str), where
    assert actual["media_type"] == recorded["media_type"], where


def _norm_path(p: str) -> str:
    """Normalize dynamic ids (uuids / 64-hex tokens / base64 keys) in
    recorded paths so replay responses recorded with DIFFERENT random
    values compare."""
    p = re.sub(r"public_key_b64=[^&]+", "public_key_b64=<b64>", p)
    p = re.sub(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
               r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}", "<uuid>", p)
    p = re.sub(r"\b[0-9a-f]{32,}\b", "<hex>", p)
    return p


def test_mock_data_integrity(repo_mock):
    assert "real JSON/binary outputs" in repo_mock["how_produced"]
    assert repo_mock["baseline"] == "31917e3"
    ids = [s["id"] for s in repo_mock["scenarios"]]
    assert ids == ["F1", "F2", "F3", "F4", "F5", "F6", "ERRORS"]
    total = sum(len(s["steps"]) for s in repo_mock["scenarios"])
    assert total == 70
    for s in repo_mock["scenarios"]:
        for step in s["steps"]:
            assert {"step", "request", "response"} <= set(step)
            assert {"method", "path"} <= set(step["request"])
            assert {"status", "body"} <= set(step["response"])


def test_replay_matches_recorded_fixtures(replayed, repo_mock):
    """The heart of the contract suite: replay the full F1-F6 + ERRORS
    sequence on a FRESH database and require every response to match
    the recorded fixtures structurally (statuses, key sets, list
    lengths, scalar types)."""
    live = replayed
    assert [s["id"] for s in live["scenarios"]] == \
        [s["id"] for s in repo_mock["scenarios"]]
    for live_sc, rec_sc in zip(live["scenarios"], repo_mock["scenarios"]):
        assert len(live_sc["steps"]) == len(rec_sc["steps"]), live_sc["id"]
        for live_step, rec_step in zip(live_sc["steps"], rec_sc["steps"]):
            where = f"{live_sc['id']}/{live_step['step']}"
            assert live_step["step"] == rec_step["step"], where
            assert live_step["request"]["method"] == \
                rec_step["request"]["method"], where
            assert _norm_path(live_step["request"]["path"]) == \
                _norm_path(rec_step["request"]["path"]), where
            assert live_step["response"]["status"] == \
                rec_step["response"]["status"], where
            rec_body = rec_step["response"]["body"]
            live_body = live_step["response"]["body"]
            if isinstance(rec_body, dict) and rec_body.get("__binary__"):
                _assert_binary_shape(live_body, rec_body, where)
                continue
            if where == "F6/report-detail":
                # snapshot keys are per-photo uuids (dynamic); compare it
                # separately with dynamic keys, everything else strictly
                live_rest = {k: v for k, v in live_body.items()
                             if k != "snapshot"}
                rec_rest = {k: v for k, v in rec_body.items()
                            if k != "snapshot"}
                _assert_same_shape(live_rest, rec_rest, where)
                _assert_same_shape(live_body["snapshot"],
                                   rec_body["snapshot"],
                                   where + ".snapshot",
                                   dynamic_key_dicts=True)
                continue
            _assert_same_shape(live_body, rec_body, where)


def test_trust_semantics_pinned(replayed):
    """Literal strings the API contract promises verbatim (trust
    notes, human-record disclaimers) - pinned with real calls."""
    steps = {(s["id"], st["step"]): st["response"]["body"]
             for s in replayed["scenarios"] for st in s["steps"]}

    photo = steps[("F2", "register-photo-1")]
    assert photo["trust_notes"] == {
        "captured_at": "client-claimed, untrusted",
        "device_id": "client-claimed, untrusted"}
    annotation = steps[("F5", "create-annotation")]
    assert "human-added annotation content" in annotation["note"]
    issue = steps[("F5", "create-issue")]
    assert issue["note"] == ("severity/status are HUMAN records; "
                             "no AI assessment implied")
    link = steps[("F5", "link-before-photo")]
    assert "does not prove the photos show the same location" in link["note"]
    integrity = steps[("F2", "integrity-view")]
    assert integrity["claims"]["photo_authenticity_proven"] is False
    report = steps[("F6", "generate-report")]
    assert "SNAPSHOT at generation time" in report["note"]
    derivative = steps[("F4", "watermark-render")]
    assert "not the trusted original evidence" in derivative["note"]
    ev = steps[("F3", "verify-evidence-link")]
    assert ev["verification_status"] == "cryptographically_valid"
    anchor_ev = steps[("F3", "verify-anchor-evidence-link")]
    assert anchor_ev["verification_status"] == "server_anchored"
    anchor_verify = steps[("F3", "anchor-verify")]
    assert anchor_verify["valid"] is True
    assert anchor_verify["trust_level"] == "server_anchored"


def test_error_envelope_pinned(replayed):
    """Every recorded typed error uses the unified envelope; legacy
    HTTPException paths (upload gates, time-anchor) use plain detail
    strings - both facts are part of the contract."""
    codes: dict[str, str | None] = {}
    err = [s for s in replayed["scenarios"] if s["id"] == "ERRORS"][0]
    for st in err["steps"]:
        body = st["response"]["body"]
        status = st["response"]["status"]
        if status in (400, 401, 404, 409, 429) and isinstance(body, dict):
            if "detail" in body and isinstance(body["detail"], dict):
                d = body["detail"]
                assert set(d) == {"code", "message", "details"}, st["step"]
                codes[st["step"]] = d["code"]
            else:
                codes[st["step"]] = None     # plain HTTPException detail
    assert codes["missing-token"] == "unauthorized"
    assert codes["unregistered-token"] == "unauthorized"
    assert codes["legacy-derived-token-refused"] == \
        "legacy_token_must_re_enroll"
    assert codes["invalid-uuid"] == "invalid_uuid"
    assert codes["cross-owner-404"] == "not_found"
    assert codes["empty-patch"] == "empty_patch"
    assert codes["rate-limited-429"] == "rate_limited"
    # plain HTTPException (no typed code) on the legacy surfaces:
    upload = [st for st in err["steps"]
              if st["step"] == "upload-experimental-default-closed"][0]
    assert upload["response"]["status"] == 503
    assert isinstance(upload["response"]["body"]["detail"], str)
    anchor = [st for st in err["steps"]
              if st["step"] == "time-anchor-invalid-batch-id"][0]
    assert anchor["response"]["status"] == 400
    assert isinstance(anchor["response"]["body"]["detail"], str)


# -------------------------------------------------- baseline pinpoints


def test_event_stream_emits_only_project_and_photo(replayed):
    """BASELINE FACT (31917e3): only project_create/project_update/
    photo_register emit sync events; annotations and issues are synced
    via batch ops but their lifecycle does NOT enter the event log
    (that is B02-W014's delivered-but-unmerged v9 rebuild). The
    entity_type filter still advertises evidence_link/original,
    which no baseline code path emits."""
    events = None
    for s in replayed["scenarios"]:
        for st in s["steps"]:
            if s["id"] == "F6" and st["step"] == "sync-events-pull":
                events = st["response"]["body"]
    assert events is not None
    kinds = {(e["entity_type"], e["action"]) for e in events["items"]}
    assert kinds == {("project", "created"), ("photo", "created")}
    assert all(isinstance(e["seq"], int) for e in events["items"])
    assert events["cursor"] >= max(e["seq"] for e in events["items"])


def test_sync_payload_must_carry_operation_id(replay_env):
    """BASELINE CONTRACT: executors validate payload.operation_id
    (required, 1..128). Omitting it is NOT a typed rejection but a
    500 - a KNOWN GAP recorded in the mismatch report; this test pins
    the current behavior so any change is a deliberate contract event."""
    H, _ = _fresh_device(replay_env)
    r = replay_env.post("/api/v1/sync/batch", headers=H, json={
        "operations": [{"op": "project_create",
                        "operation_id": "op-x",
                        "payload": {"local_id": "lx",
                                    "name": "No operation id"}}]})
    assert r.status_code == 500, \
        "gap closed? update the docs + fixtures + this test"
    r = replay_env.post("/api/v1/sync/batch", headers=H, json={
        "operations": [{"op": "project_create",
                        "operation_id": "op-y",
                        "payload": {"operation_id": "op-y",
                                    "local_id": "ly",
                                    "name": "With operation id"}}]})
    assert r.status_code == 200
    assert r.json()["receipts"][0]["status"] == "applied"


def test_offset_pagination_and_clamp(replay_env):
    """limit is clamped to 100 (NOT a 422), offset >= 0; the list
    envelope is {items, total, limit, offset} everywhere (offset
    pagination). W017's keyset cursor is a different, UNMERGED
    surface (see the API reference, Not-in-baseline section)."""
    H, _ = _fresh_device(replay_env)
    for n in (1, 2, 3):
        replay_env.post("/api/v1/projects", headers=H,
                        json={"name": f"Pagination check {n}"})
    r = replay_env.get("/api/v1/projects?limit=99999&offset=0", headers=H)
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"items", "total", "limit", "offset"}
    assert body["limit"] == 100          # clamped, documented behavior
    assert body["offset"] == 0
    assert body["total"] == len(body["items"]) == 3
    r = replay_env.get("/api/v1/projects?limit=2&offset=1", headers=H)
    assert r.status_code == 200
    assert r.json()["limit"] == 2 and r.json()["offset"] == 1
    assert len(r.json()["items"]) == 2
    r = replay_env.get("/api/v1/projects?offset=-1", headers=H)
    assert r.status_code == 422          # pydantic ge=0 violation


def test_rate_limit_shape(replayed):
    body = None
    for s in replayed["scenarios"]:
        for st in s["steps"]:
            if st["step"] == "rate-limited-429":
                body = st["response"]["body"]
    assert body["detail"]["code"] == "rate_limited"
    assert "60/60s window" in body["detail"]["message"]


# -------------------------------------------- legacy token shutdown


def test_legacy_derived_token_rejected_401(replay_env, tmp_path):
    """W013-R1 P0: a token computed as sha256(public_key_b64) - the
    pre-W012 derived-credential design - is refused with
    legacy_token_must_re_enroll WHEN it matches a legacy devices.jsonl
    record (that is how pre-W012 devices reach this branch); an
    unknown token is plain unauthorized (no oracle either way)."""
    H, pub_b64 = _fresh_device(replay_env)
    assert replay_env.get("/api/v1/projects", headers=H).status_code == 200
    derived = hashlib.sha256(pub_b64.encode("ascii")).hexdigest()
    # no legacy record: plain unauthorized
    r = replay_env.get("/api/v1/projects",
                       headers={"X-Device-Token": derived})
    assert r.status_code == 401
    assert r.json()["detail"]["code"] == "unauthorized"
    # write the legacy record the way pre-W012 enrollment did
    import os
    legacy_reg = Path(os.environ["WORKCAM_DEVICE_REGISTRY"])
    with open(legacy_reg, "a", encoding="utf-8") as f:
        f.write(json.dumps({"device_id": "legacy-pre-w012-device",
                            "token": derived,
                            "public_key_b64": pub_b64}) + "\n")
    r = replay_env.get("/api/v1/projects",
                       headers={"X-Device-Token": derived})
    assert r.status_code == 401
    assert r.json()["detail"]["code"] == "legacy_token_must_re_enroll"
    r = replay_env.get("/api/v1/projects",
                       headers={"X-Device-Token": "cd" * 32})
    assert r.status_code == 401
    assert r.json()["detail"]["code"] == "unauthorized"
