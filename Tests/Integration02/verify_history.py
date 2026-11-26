"""Read two historical exports locally; verify over loopback and with W018."""
import base64
import json
import pathlib
import subprocess
import sys
import tempfile

import httpx
from techlab.evidence_package import build_package

backend, source = map(pathlib.Path, sys.argv[1:3])
endpoint = sys.argv[3]
assert endpoint in ("http://127.0.0.1:18765", "http://localhost:18765")
records = []
with httpx.Client(base_url=endpoint, trust_env=False) as client:
    for photo in sorted(source.rglob("photo.jpg")):
        original = photo.read_bytes()
        metadata = json.loads(photo.with_name("record.json").read_text())
        manifest = metadata["manifest"]
        assert manifest["record_id"] == metadata["photoID"]
        response = client.post("/verify", json={
            "manifest": manifest, "photo_base64": base64.b64encode(original).decode()})
        response.raise_for_status()
        status = response.json()
        assert status["cryptographic_signature_valid"] is True
        assert status["photo_hash_valid"] is True
        records.append(dict(manifest=manifest, photo_bytes=original, location_meta=None))
assert len(records) == 2
with tempfile.TemporaryDirectory(prefix="int02-w018-") as temp:
    package = pathlib.Path(temp) / "package"
    build_package(package, records, device_id="historical-export", created_at="2026-11-27T00:00:00Z")
    result = subprocess.run(
        [sys.executable, str(backend / "tools/evidence-verifier/verify_evidence.py"), str(package)],
        capture_output=True, check=False)
    assert result.returncode == 0, result.returncode
print("PASS historical originals: backend HTTP and independent W018 both verified 2/2")
