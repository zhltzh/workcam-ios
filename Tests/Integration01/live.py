"""Loopback integration for the pinned Windows backend; use its disposable venv."""
import base64
import hashlib
import os
import secrets
import socket
import subprocess
import sys
import tempfile
import time

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec


def main(backend):
    with tempfile.TemporaryDirectory(prefix="workcam-live-") as root, socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()
        code = secrets.token_urlsafe(24)
        env = dict(os.environ, WORKCAM_ENV="development", WORKCAM_ENROLLMENT_CODE=code,
                   WORKCAM_BUSINESS_DB=os.path.join(root, "workcam.db"),
                   WORKCAM_DEVICE_REGISTRY=os.path.join(root, "devices.jsonl"),
                   WORKCAM_ORIGINALS_DIR=os.path.join(root, "originals"))
        with httpx.Client(base_url=f"http://127.0.0.1:{port}", trust_env=False) as http:
            server = None

            def start():
                process = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app",
                                            "--host", "127.0.0.1", "--port", str(port), "--no-access-log"],
                                           cwd=backend, env=env, stdout=subprocess.DEVNULL)
                for _ in range(80):
                    if process.poll() is not None:
                        break
                    try:
                        if http.get("/health").status_code == 200:
                            return process
                    except httpx.TransportError:
                        pass
                    time.sleep(0.1)
                process.terminate()
                process.wait(timeout=5)
                raise RuntimeError("Fixed backend did not start")

            def call(method, path, status, **kwargs):
                response = http.request(method, path, **kwargs)
                assert response.status_code == status, (method, path, response.status_code)
                return response.json()

            try:
                server = start()
                key = ec.generate_private_key(ec.SECP256R1())
                pub = key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
                public = base64.b64encode(pub).decode()
                station = call("POST", "/api/v1/devices/enroll", 201, json={
                    "public_key_b64": public, "device_id": "synthetic-loopback", "enrollment_code": code})
                token = station["device_token"]
                assert token and token != hashlib.sha256(pub).hexdigest()
                http.headers["X-Device-Token"] = token
                nonce = call("POST", "/api/v1/devices/challenge", 200)["nonce"]
                der = key.sign(nonce.encode("ascii"), ec.ECDSA(hashes.SHA256()))
                assert call("POST", "/api/v1/devices/challenge/verify", 200, json={
                    "device_token": token, "nonce": nonce, "signature_b64": base64.b64encode(der).decode()})["verified"]
                ops = {"operations": [{"op": "project_create", "operation_id": "int01-project",
                       "payload": {"operation_id": "int01-project", "local_id": "fixture", "name": "Synthetic site"}}]}
                call("POST", "/api/v1/sync/batch", 200, json=ops)
                call("POST", "/api/v1/sync/batch", 200, json=ops)
                project = call("POST", "/api/v1/projects", 201, json={"name": "Upload site"})["project_id"]
                original = bytes(range(256)) * 1025
                sha = hashlib.sha256(original).hexdigest()
                photo = call("POST", f"/api/v1/projects/{project}/photos", 201, json={
                    "idempotency_key": "test-photo", "original_sha256": sha, "original_filename": "fixture.jpg",
                    "mime_type": "image/jpeg", "byte_size": len(original), "captured_at": "2026-11-26T08:00:00Z",
                    "device_id": "synthetic-loopback"})["photo_id"]
                chunk_size = 128 * 1024
                chunks = [original[i:i + chunk_size] for i in range(0, len(original), chunk_size)]
                session = f"/api/v1/photos/{photo}/original-session"
                upload = call("POST", session, 200, json={"claimed_sha256": sha, "chunk_size": chunk_size,
                              "total_chunks": len(chunks), "total_bytes": len(original)})["upload_id"]
                session += f"/{upload}"

                def put(index):
                    chunk = chunks[index]
                    return call("PUT", f"{session}/chunk/{index}", 200,
                                headers={"X-Chunk-SHA256": hashlib.sha256(chunk).hexdigest()}, content=chunk)

                assert put(0)["stored"]
                call("POST", f"{session}/complete", 409)
                server.terminate()
                server.wait(timeout=10)
                server = start()
                assert call("GET", session, 200)["next_chunk"] == 1
                assert put(0)["duplicate"]
                for i in range(1, len(chunks)):
                    put(i)
                assert call("POST", f"{session}/complete", 200)["bound"]
                integrity = call("GET", f"/api/v1/photos/{photo}/original/integrity", 200)
                assert integrity["claims"]["file_integrity_verified"]
                issue = call("POST", f"/api/v1/projects/{project}/issues", 201,
                             json={"title": "Fixture", "description": "Synthetic", "severity": "high"})["issue_id"]
                call("PATCH", f"/api/v1/issues/{issue}", 200, json={"status": "in_progress", "expected_revision": 1})
                call("PATCH", f"/api/v1/issues/{issue}", 409, json={"status": "resolved", "expected_revision": 1})
                print("PASS live HTTP: enrollment/token, challenge, project batch replay, chunk restart/resume, integrity, issue conflict")
            finally:
                if server and server.poll() is None:
                    server.terminate()
                    server.wait(timeout=10)


if __name__ == "__main__":
    main(sys.argv[1])
