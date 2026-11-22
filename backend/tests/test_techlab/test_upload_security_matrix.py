"""WC-W005-R1 P1 - upload endpoint security-combination matrix.

Pins the FULL switch matrix by code, not by operator discipline:

    EXPERIMENTAL x AUTH x client reachability (loopback / non-loopback)

The non-loopback client is a REAL ASGI request whose scope carries a
remote peer address (hand-built scope, no httpx async limitation) -
proving the gate fires on network identity, not on test scaffolding.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os

import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import (
    Encoding, PublicFormat)
from fastapi.testclient import TestClient

from app.main import app

loop = TestClient(app)     # scope client host = "testclient" (exempt)


def asgi_request(method: str, path: str, *, client_host: str,
                 json_body: dict | None = None, raw_body: bytes = b"",
                 headers: dict | None = None) -> tuple[int, dict]:
    """One real ASGI request with a FORGED peer address. Building the
    scope by hand is the only way to prove the gate reacts to the
    network identity itself (httpx 0.28's ASGITransport is async-only,
    and starlette's TestClient hardcodes its own host)."""
    scope = {
        "type": "http", "http_version": "1.1", "method": method.upper(),
        "scheme": "http", "path": path, "raw_path": path.encode(),
        "query_string": b"", "headers": [], "root_path": "",
        "client": (client_host, 51000), "server": ("testserver", 80),
    }
    body = b""
    if json_body is not None:
        body = json.dumps(json_body).encode()
        scope["headers"].append((b"content-type", b"application/json"))
    elif raw_body:
        body = raw_body
    scope["headers"].append((b"content-length", str(len(body)).encode()))
    for k, v in (headers or {}).items():
        scope["headers"].append((k.lower().encode(), str(v).encode()))

    status: dict = {}
    parts: list[bytes] = []

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message):
        if message["type"] == "http.response.start":
            status["code"] = message["status"]
        elif message["type"] == "http.response.body":
            parts.append(message.get("body", b""))

    asyncio.run(app(scope, receive, send))
    raw = b"".join(parts)
    try:
        parsed = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        parsed = {"_raw": raw.decode("utf-8", "replace")}
    return status.get("code", 500), parsed


REMOTE = "192.168.1.50"
_REG_BODY = {"total_bytes": 100, "chunk_size": 50,
             "total_sha256": "ab" * 32}


@pytest.fixture()
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKCAM_UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.delenv("WORKCAM_UPLOAD_EXPERIMENTAL", raising=False)
    monkeypatch.delenv("WORKCAM_UPLOAD_AUTH", raising=False)
    monkeypatch.delenv("WORKCAM_ENV", raising=False)
    monkeypatch.setenv("WORKCAM_DEVICE_REGISTRY",
                       str(tmp_path / "devices.jsonl"))
    monkeypatch.setenv("WORKCAM_UPLOAD_AUDIT", str(tmp_path / "audit.jsonl"))
    import app.api.upload as up
    import app.api.upload_auth as ua
    monkeypatch.setattr(up, "_STORAGE_ROOT", tmp_path / "uploads")
    ua._batch_owners.clear(); ua._buckets.clear(); ua._challenges.clear()
    yield tmp_path


# --------------------------------------------- EXPERIMENTAL unset: 全矩阵关


def test_experimental_unset_refuses_everyone(isolated):
    r = loop.post("/upload/experimental/batches", json=_REG_BODY)
    assert r.status_code == 503
    code, _ = asgi_request("POST", "/upload/experimental/batches",
                           client_host=REMOTE, json_body=_REG_BODY)
    assert code == 503


# ------------------------------- EXPERIMENTAL=1, AUTH unset: 回环可用/远程拒


def test_auth_unset_loopback_still_works(isolated, monkeypatch):
    """Local single-machine joint debugging keeps working (the exempted
    TestClient host proves the pre-existing behaviour is unchanged)."""
    monkeypatch.setenv("WORKCAM_UPLOAD_EXPERIMENTAL", "1")
    r = loop.post("/upload/experimental/batches", json=_REG_BODY)
    assert r.status_code == 200
    assert r.json()["status"] == "registered"
    # loopback peer via raw ASGI scope too (not just the test client):
    code, body = asgi_request("POST", "/upload/experimental/batches",
                              client_host="127.0.0.1", json_body=_REG_BODY)
    assert code == 200


def test_auth_unset_remote_refused_by_code(isolated, monkeypatch):
    """THE R1 REQUIREMENT: a non-loopback client without device auth is
    refused BY CODE (503) - never merely by operator discipline. Every
    data route refuses, not just the first one reached."""
    monkeypatch.setenv("WORKCAM_UPLOAD_EXPERIMENTAL", "1")
    routes = [
        ("POST", "/upload/experimental/batches", _REG_BODY),
        ("PUT", "/upload/experimental/batches/abc/chunks/0", None),
        ("GET", "/upload/experimental/batches/abc", None),
        ("POST", "/upload/experimental/batches/abc/complete", None),
        ("GET", "/upload/experimental/batches/abc/content", None),
    ]
    for method, path, body in routes:
        code, resp = asgi_request(method, path, client_host=REMOTE,
                                 json_body=body)
        assert code == 503, f"{method} {path}: {code}"
        assert "NON-LOOPBACK" in resp.get("detail", "")


def test_remote_refusal_covers_private_ranges_loopback_maps_allowed(
        isolated, monkeypatch):
    """Non-loopback includes LAN/Docker ranges; the IPv4-mapped loopback
    (::ffff:127.0.0.1) is still loopback and stays allowed."""
    monkeypatch.setenv("WORKCAM_UPLOAD_EXPERIMENTAL", "1")
    for host in ("10.0.0.7", "172.17.0.9", "8.8.8.8"):
        code, _ = asgi_request("POST", "/upload/experimental/batches",
                               client_host=host, json_body=_REG_BODY)
        assert code == 503, f"{host} must be refused"
    code, _ = asgi_request("POST", "/upload/experimental/batches",
                           client_host="::ffff:127.0.0.1",
                           json_body=_REG_BODY)
    assert code == 200


# --------------------------------------- EXPERIMENTAL=1, AUTH=1: 远程需令牌


def _enroll(isolated) -> str:
    import app.api.upload_auth as ua
    key = ec.generate_private_key(ec.SECP256R1())       # TEST key
    pub_b64 = base64.b64encode(key.public_key().public_bytes(
        Encoding.X962, PublicFormat.UncompressedPoint)).decode()
    registry = ua.DeviceRegistry(isolated / "devices.jsonl")
    return registry.enroll(pub_b64, "matrix-device")


def test_auth_on_remote_requires_token(isolated, monkeypatch):
    monkeypatch.setenv("WORKCAM_UPLOAD_EXPERIMENTAL", "1")
    monkeypatch.setenv("WORKCAM_UPLOAD_AUTH", "1")
    _enroll(isolated)
    code, resp = asgi_request("POST", "/upload/experimental/batches",
                              client_host=REMOTE, json_body=_REG_BODY)
    assert code == 401                  # auth now REQUIRED for remote
    assert "X-Device-Token" in resp.get("detail", "")


def test_auth_on_remote_with_token_succeeds(isolated, monkeypatch):
    """AUTH=1 is the SANCTIONED way to serve non-loopback clients: with
    an enrolled device token the remote path works end-to-end."""
    monkeypatch.setenv("WORKCAM_UPLOAD_EXPERIMENTAL", "1")
    monkeypatch.setenv("WORKCAM_UPLOAD_AUTH", "1")
    token = _enroll(isolated)
    hdr = {"X-Device-Token": token}
    code, resp = asgi_request("POST", "/upload/experimental/batches",
                             client_host=REMOTE, json_body=_REG_BODY,
                             headers=hdr)
    assert code == 200
    bid = resp["batch_id"]
    code2, _ = asgi_request("PUT",
                            f"/upload/experimental/batches/{bid}/chunks/0",
                            client_host=REMOTE,
                            raw_body=b"a" * 50,
                            headers={**hdr, "Content-Length": "50"})
    assert code2 == 200
    code3, _ = asgi_request("GET",
                            f"/upload/experimental/batches/{bid}",
                            client_host=REMOTE, headers=hdr)
    assert code3 == 200


def test_auth_routes_refuse_remote_when_auth_off(isolated, monkeypatch):
    """/auth/* under a non-loopback client with AUTH unset must 503 -
    there is no unauthenticated remote path into ANY upload route."""
    monkeypatch.setenv("WORKCAM_UPLOAD_EXPERIMENTAL", "1")
    code, _ = asgi_request("POST", "/upload/experimental/auth/challenge",
                           client_host=REMOTE)
    assert code == 503


# ------------------------------------------------ production hard veto


def test_production_veto_wins_everything(isolated, monkeypatch):
    monkeypatch.setenv("WORKCAM_UPLOAD_EXPERIMENTAL", "1")
    monkeypatch.setenv("WORKCAM_UPLOAD_AUTH", "1")
    monkeypatch.setenv("WORKCAM_ENV", "production")
    r = loop.post("/upload/experimental/batches", json=_REG_BODY)
    assert r.status_code == 503 and "production" in r.json()["detail"]
    code, resp = asgi_request("POST", "/upload/experimental/batches",
                             client_host=REMOTE, json_body=_REG_BODY)
    assert code == 503 and "production" in resp.get("detail", "")


def test_full_matrix_documented_combinations(isolated):
    """The documented switch matrix, asserted end-to-end: loopback
    (TestClient scope + raw 127.0.0.1 scope) and a remote peer."""
    token = _enroll(isolated)
    hdr = {"X-Device-Token": token}
    cases = [
        # (EXPERIMENTAL, AUTH, PRODUCTION, peer, use_token, expected)
        (None,  None,  None,        "127.0.0.1", False, 503),  # default: closed
        (None,  None,  None,        REMOTE,     False, 503),
        ("1",   None,  None,        "127.0.0.1", False, 200),  # loopback debug
        ("1",   None,  None,        REMOTE,     False, 503),   # remote: refused
        ("1",   "1",   None,        "127.0.0.1", True,  200),  # loopback + token
        ("1",   "1",   None,        REMOTE,     True,  200),   # remote + token
        ("1",   "1",   "production", "127.0.0.1", True, 503), # prod veto
        ("1",   "1",   "production", REMOTE,     True, 503),
    ]
    for exp, auth, prod, peer, use_token, expected in cases:
        for var in ("WORKCAM_UPLOAD_EXPERIMENTAL", "WORKCAM_UPLOAD_AUTH",
                    "WORKCAM_ENV"):
            os.environ.pop(var, None)
        if exp:
            os.environ["WORKCAM_UPLOAD_EXPERIMENTAL"] = exp
        if auth:
            os.environ["WORKCAM_UPLOAD_AUTH"] = auth
        if prod:
            os.environ["WORKCAM_ENV"] = prod
        code, _ = asgi_request("POST", "/upload/experimental/batches",
                               client_host=peer, json_body=_REG_BODY,
                               headers=hdr if use_token else None)
        assert code == expected, (
            f"matrix EXP={exp} AUTH={auth} PROD={prod} peer={peer}: "
            f"got {code}, want {expected}")
    for var in ("WORKCAM_UPLOAD_EXPERIMENTAL", "WORKCAM_UPLOAD_AUTH",
                "WORKCAM_ENV"):
        os.environ.pop(var, None)
