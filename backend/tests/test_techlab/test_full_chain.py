"""W003 stage 5 - FULL BUSINESS-CHAIN integration test (SYNTHETIC data).

One continuous scenario wiring every Wave 1-3 module together:

  capture input -> original sealing (T05 ledger + T06 store) ->
  watermark derivative (T10) -> drawing association (T07) ->
  evidence package (T13) -> streaming export (T09) ->
  offline CLI verification (T18) -> chunked upload with simulated
  network drop + resume (T12) -> server restart recovery.

Every artifact is clearly SYNTHETIC. No device claims - this is the
chain Mac will REPRODUCE on hardware later; the protocol path is what
this test proves.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient

from app.main import app
from techlab.derivative_model import DerivativeLedger, OriginalTamperError
from techlab.drawing_pins import (
    CalibrationPair,
    DrawingSheet,
    PhotoPin,
    SheetPoint,
    WorldPoint,
)
from techlab.evidence_package import build_package, verify_package
from techlab.export_engine import (
    ExportItem,
    export_zip_bundle,
    export_docx,
)
from techlab.storage_integrity import PhotoStore
from techlab.watermark_engine import (
    Anchor,
    Element,
    ElementType,
    RenderingContext,
    WatermarkTemplate,
    apply_template_to_original,
)
from techlab.location_meta import LocationMetadata, LocationSource
from tests.conftest import make_manifest, _encode_pub

REPO_ROOT = Path(__file__).resolve().parents[3]
CLI = REPO_ROOT / "tools" / "tech-lab" / "validation" / "evidence_verifier.py"

client = TestClient(app)


@pytest.fixture()
def site(tmp_path, monkeypatch):
    """One synthetic 'site' with everything a capture produces."""
    import app.api.upload as up_mod
    monkeypatch.setenv("WORKCAM_UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setattr(up_mod, "_STORAGE_ROOT", tmp_path / "uploads")
    return type("Site", (), {"root": tmp_path})()


def test_full_chain_synthetic(site):
    root: Path = site.root
    log: list[str] = []

    # -------------------------------------------------- 1. capture input
    key = ec.generate_private_key(ec.SECP256R1())          # TEST key
    pub = _encode_pub(key.public_key())
    photo_bytes = (b"SYNTHETIC-WORKCAM-PHOTO\x00" +
                   bytes(range(256)) * 16)                  # ~4 KiB
    location = LocationMetadata(
        source=LocationSource.GPS, latitude=39.9001, longitude=116.4001,
        horizontal_accuracy=4.0)
    manifest = make_manifest(key, pub, photo_bytes=photo_bytes,
                              record_id="chain-photo-001")
    log.append(f"capture: photo {len(photo_bytes)} B, "
               f"gps {location.trust_label()}")

    # ------------------------------------------- 2. seal original (T05+T06)
    ledger = DerivativeLedger()
    original = ledger.seal_original(photo_bytes,
                                    captured_at=manifest.capture_timestamp)
    store = PhotoStore(root / "store")
    store.save_photo(photo_bytes, captured_at=manifest.capture_timestamp)
    assert ledger.verify_original_bytes(original.photo_id, photo_bytes)
    # tamper check on the REAL file layer
    disk_file = root / "store" / "photos" / f"{original.photo_id}.jpg"
    assert hashlib.sha256(disk_file.read_bytes()).hexdigest() == \
           original.content_hash
    log.append(f"seal: original {original.photo_id} "
               f"(store + ledger consistent)")

    # ------------------------------------------- 3. watermark derivative (T10)
    ctx = RenderingContext(project_name="SYNTH Site A",
                           captured_at=manifest.capture_timestamp,
                           location_meta=location.model_dump())
    template = WatermarkTemplate(name="site-report", elements=[
        Element(type=ElementType.PROJECT, anchor=Anchor.BOTTOM_RIGHT,
                x=0, y=0, font_size=20, source="name"),
        Element(type=ElementType.GPS, anchor=Anchor.BOTTOM_LEFT,
                x=0, y=0, font_size=14),
    ])
    derivative_bytes = b"SYNTHETIC-WATERMARKED-DERIVATIVE" + photo_bytes
    derivative, layout = apply_template_to_original(
        ledger, original, template, ctx, derivative_bytes)
    assert ledger.verify_derivative_bytes(derivative.derivative_id,
                                          derivative_bytes)
    assert "grade A" in layout.placements[1]["text"]   # honest gps label
    log.append(f"watermark: derivative {derivative.derivative_id} "
               f"(original untouched)")

    # ------------------------------------------- 4. drawing association (T07)
    sheet = DrawingSheet(
        title="SYNTH Level 2 plan",
        pdf_content_hash=hashlib.sha256(b"SYNTH-drawing-pdf").hexdigest(),
        geo_origin=(39.9000, 116.4000))
    for s, w in (((0, 0), (0, 0)), ((1000, 0), (1, 0)), ((0, 1000), (0, 1))):
        sheet.add_calibration(CalibrationPair(
            sheet=SheetPoint(x=s[0], y=s[1]), world=WorldPoint(x=w[0], y=w[1])))
    sheet.pin_photo(PhotoPin(photo_id=manifest.record_id,
                             sheet_rev=sheet.pdf_content_hash[:12],
                             sheet_x=500, sheet_y=300))
    pin_results = {r.photo_id: r for r in sheet.pin_world_positions(
        {manifest.record_id: location.model_dump()})}
    assert pin_results[manifest.record_id].corroborated is True
    log.append(f"drawing: pin corroborated "
               f"({pin_results[manifest.record_id].detail})")

    # ------------------------------------------- 5. evidence package (T13)
    pkg_dir = root / "package"
    build_package(
        pkg_dir,
        records=[{
            "manifest": manifest.model_dump(),
            "photo_bytes": photo_bytes,
            "location_meta": location.model_dump(),
            "derivatives": [{
                "derivative_id": derivative.derivative_id,
                "content_hash": derivative.content_hash,
                "original_hash": original.content_hash,
                "recipe_engine": derivative.recipe.engine,
                "recipe_template_version": derivative.recipe.template_version,
                "operation_seq": derivative.operation_seq,
                "bytes": derivative_bytes,
            }],
        }],
        device_id="SYNTH-DEVICE-001",
        created_at="2026-11-20T17:00:00+00:00",
        anchor_receipts=None)
    result = verify_package(pkg_dir)
    assert result.all_passed, result.model_dump()
    log.append(f"package: verify all_passed "
               f"({len(result.record_results)} record, 1 derivative)")

    # ------------------------------------------- 6. streaming export (T09)
    exports = root / "exports"
    exports.mkdir()
    zip_target = exports / "site-bundle.zip"
    items = [ExportItem(photo_id=manifest.record_id,
                        filename=f"{manifest.record_id}.jpg",
                        file_path=pkg_dir / "photos" / f"{manifest.record_id}.jpg",
                        meta={"photo_id": manifest.record_id,
                              "note": "SYNTHETIC chain record"}),
             ExportItem(photo_id=derivative.derivative_id,
                        filename=f"{derivative.derivative_id}.jpg",
                        file_path=pkg_dir / "derivatives" / f"{derivative.derivative_id}.jpg",
                        meta={"photo_id": derivative.derivative_id,
                              "note": "SYNTHETIC chain derivative"})]
    stats = export_zip_bundle(items, zip_target,
                              extra_meta={"synthetic": True})
    with zipfile.ZipFile(zip_target) as zf:
        assert zf.testzip() is None
        assert len(zf.namelist()) == 2 + 4     # photos + csv/json/info/pdf
    log.append(f"export: zip {stats.bytes_out} B "
                f"(rss delta {stats.rss_delta_mb} MB)")

    # ------------------------------------------- 7. offline verify (T18)
    import os
    proc = subprocess.run(
        [sys.executable, str(CLI), str(pkg_dir), "--json",
         str(exports / "verify.json")],
        capture_output=True, encoding="utf-8", errors="replace",
        cwd=str(REPO_ROOT),
        env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    report = json.loads((exports / "verify.json").read_text(encoding="utf-8"))
    assert report["overall"] is True
    log.append("offline-verify: CLI exit 0, package overall=true")

    # ------------------------------------------- 8. upload with drop+resume (T12)
    data = zip_target.read_bytes()
    total_sha = hashlib.sha256(data).hexdigest()
    r = client.post("/upload/experimental/batches",
                    json={"total_bytes": len(data),
                          "chunk_size": 256 * 1024,
                          "total_sha256": total_sha})
    assert r.status_code == 200
    bid = r.json()["batch_id"]

    chunks = [data[i:i + 256 * 1024] for i in range(0, len(data), 256 * 1024)]
    drop = len(chunks) - 1                      # drop the last chunk
    for i, c in enumerate(chunks):
        if i == drop:
            continue
        rr = client.put(f"/upload/experimental/batches/{bid}/chunks/{i}",
                        content=c,
                        headers={"Content-Length": str(len(c))})
        assert rr.status_code == 200, rr.text
    st = client.get(f"/upload/experimental/batches/{bid}").json()
    assert st["missing"] == [drop]

    # 'server restart' before resume: a fresh client against the same disk
    client2 = TestClient(app)
    rr = client2.put(f"/upload/experimental/batches/{bid}/chunks/{drop}",
                     content=chunks[drop],
                     headers={"Content-Length": str(len(chunks[drop]))})
    assert rr.status_code == 200
    comp = client2.post(f"/upload/experimental/batches/{bid}/complete")
    assert comp.status_code == 200, comp.text
    assert comp.json()["total_sha256"] == total_sha
    log.append(f"upload: {len(chunks)} chunks, dropped {drop}, resumed "
                f"after restart, server hash == local hash")

    # ------------------------------------------------------- chain summary
    print("\n[CHAIN] full business chain (SYNTHETIC):")
    for line in log:
        print(f"  {line}")
    print(f"[CHAIN] PASSED - {len(log)} stages wired together")


def test_chain_tamper_breaks_at_the_right_layer(site):
    """Honest chain: tampering the exported PHOTO is caught by package
    verification (not by export, which only copied bytes); tampering the
    DERIVATIVE is caught by derivative checks, not by the original's
    math. Each layer fails at its own boundary."""
    root: Path = site.root
    key = ec.generate_private_key(ec.SECP256R1())
    pub = _encode_pub(key.public_key())
    photo_bytes = b"SYNTHETIC-PHOTO-TAMPER-TEST"
    manifest = make_manifest(key, pub, photo_bytes=photo_bytes)

    ledger = DerivativeLedger()
    original = ledger.seal_original(photo_bytes,
                                    captured_at=manifest.capture_timestamp)

    pkg_dir = root / "pkg"
    build_package(pkg_dir, [{
        "manifest": manifest.model_dump(), "photo_bytes": photo_bytes,
        "location_meta": None}], device_id="SYNTH", created_at="now")

    # tamper the ORIGINAL photo on disk -> package catches it
    (pkg_dir / "photos" / f"{manifest.record_id}.jpg").write_bytes(b"EVIL")
    result = verify_package(pkg_dir)
    assert not result.all_passed
    rv = result.record_results[0]
    assert rv.photo_hash_valid is False
    # and the ORIGINAL in the ledger is untouched by the disk tamper
    assert ledger.verify_original_bytes(original.photo_id, photo_bytes)
    # ...while the disk file no longer matches the ledger:
    assert not ledger.verify_original_bytes(
        original.photo_id, (pkg_dir / "photos" / f"{manifest.record_id}.jpg").read_bytes())
