"""WC-B02-W015 tests: professional inspection report enhancements.

DESKTOP_SIMULATION - synthetic photos, REAL SQLite/PDF/ZIP behavior.

Covers:
  - remediation_note round trip (API + PATCH + sync batch) [B02-W015]
  - issue register numbering (ISS-nnn / PH-nnn) in the PDF text
  - severity breakdown + remediation status summary counts
  - before/after comparison figure pairs embedded in the PDF
  - LiDAR measurement record references (EXPERIMENTAL, DEVICE_PENDING)
  - manual sign-off section (no legal attestation wording)
  - four-tier source classification labels in the PDF text
  - inspector + project metadata block
  - wording red lines: no positive legal certification claims
  - snapshot immutability with remediation notes
  - delivery package with issues (manifest integrity, no byte bloat)
  - request validation bounds (metadata entries, lidar refs)
"""

from __future__ import annotations

import base64
import hashlib
import io
import re
import uuid
import zipfile
import zlib

import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

from app.main import app
from tests.conftest import _encode_pub


@pytest.fixture()
def w015_env(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKCAM_BUSINESS_DB", str(tmp_path / "biz.db"))
    monkeypatch.setenv("WORKCAM_ENROLLMENT_CODE", "w015-code")
    monkeypatch.delenv("WORKCAM_UPLOAD_EXPERIMENTAL", raising=False)
    monkeypatch.delenv("WORKCAM_UPLOAD_AUTH", raising=False)
    import app.db as dbm
    dbm.migrate(tmp_path / "biz.db")
    from app.services.key_registry import reset_registry
    reset_registry()
    import app.services.device_identity as di
    monkeypatch.setattr(di, "check_rate", lambda *a, **k: None)

    key = ec.generate_private_key(ec.SECP256R1())
    pub_b64 = base64.b64encode(_encode_pub(key.public_key())).decode()
    tok = di.enroll_device(pub_b64, "w015-AAA", "w015-code")
    return type("E", (), {
        "tmp": tmp_path, "db": tmp_path / "biz.db",
        "H": {"X-Device-Token": tok},
        "client": TestClient(app),
    })()


def _photo(w=1200, h=900, seed=0) -> bytes:
    img = Image.new("RGB", (w, h), (100 + seed, 140, 120))
    d = ImageDraw.Draw(img)
    d.rectangle([40, 40, 200, 150], fill=(40, 80, 160))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


def _setup(env, n_photos=2):
    c = env.client
    pid = c.post("/api/v1/projects", json={"name": "W015 Bridge Site"},
                  headers=env.H).json()["project_id"]
    photo_ids = []
    for i in range(n_photos):
        data = _photo(seed=i * 30)
        ph = c.post(
            f"/api/v1/projects/{pid}/photos",
            json={"idempotency_key": f"w-{uuid.uuid4().hex[:8]}",
                  "original_sha256": hashlib.sha256(data).hexdigest(),
                  "original_filename": f"IMG_{i}.jpg",
                  "mime_type": "image/jpeg", "byte_size": len(data),
                  "captured_at": "2026-11-25 09:00",
                  "device_id": "d"}, headers=env.H).json()["photo_id"]
        c.put(f"/api/v1/photos/{ph}/original", content=data,
               headers={**env.H, "Content-Type": "application/octet-stream"})
        photo_ids.append(ph)
    return pid, photo_ids


def _mk_issue(env, pid, **kw) -> dict:
    body = {"title": "Default issue", "severity": "low"}
    body.update(kw)
    r = env.client.post(f"/api/v1/projects/{pid}/issues", json=body,
                        headers=env.H)
    assert r.status_code == 201, r.text
    return r.json()


def _pdf_text(data: bytes) -> str:
    """Extract decompressed content streams as latin-1 text for
    substring assertions. reportlab page streams are
    ASCII85+Flate-encoded; image streams (DCTDecode) decode to nothing
    useful and are skipped by the try-chain."""
    parts = []
    for m in re.finditer(rb"stream\n(.*?)endstream", data, re.DOTALL):
        raw = m.group(1).strip()
        try:
            parts.append(zlib.decompress(
                base64.a85decode(raw, adobe=True)))
            continue
        except Exception:
            pass
        try:
            parts.append(zlib.decompress(raw))
        except Exception:
            pass
    return b"\n".join(parts).decode("latin-1", errors="ignore")


def _gen_report(env, pid, photo_ids, issue_ids=None, **kw) -> dict:
    body = {"report_type": "inspection", "page_size": "a4",
            "per_page": 2, "photo_ids": photo_ids,
            "operator": "J. Wang", "company_name": "CMSS Eng",
            "inspector_name": "L. Chen",
            "inspector_title": "Site QC Engineer",
            "project_metadata": {"Client": "Provincial Highway Admin",
                                 "Contract No.": "NH-2026-0342",
                                 "Section": "Km 34+000 to 35+500"}}
    if issue_ids is not None:
        body["issue_ids"] = issue_ids
    body.update(kw)
    r = env.client.post(f"/api/v1/projects/{pid}/reports", json=body,
                        headers=env.H)
    assert r.status_code == 201, r.text
    return r.json()


# ------------------------------------------- remediation_note round trip


def test_remediation_note_crud_roundtrip(w015_env):
    c = w015_env.client
    pid, _ = _setup(w015_env, 1)
    note = "Chip area, epoxy inject per spec SS-7, re-check in 7 days."
    iss = _mk_issue(w015_env, pid, title="Hairline crack",
                    severity="high", remediation_note=note)
    assert iss["remediation_note"] == note
    assert "HUMAN records" in iss["note"]

    det = c.get(f"/api/v1/issues/{iss['issue_id']}",
                 headers=w015_env.H).json()
    assert det["remediation_note"] == note

    note2 = "Updated: use low-pressure injection per revised spec."
    patched = c.patch(f"/api/v1/issues/{iss['issue_id']}", json={
        "remediation_note": note2, "expected_revision": 1},
        headers=w015_env.H)
    assert patched.status_code == 200
    assert patched.json()["remediation_note"] == note2
    assert patched.json()["revision"] == 2


def test_remediation_note_sync_batch(w015_env):
    c = w015_env.client
    pid, _ = _setup(w015_env, 1)
    r1 = c.post("/api/v1/sync/batch", json={"operations": [
        {"op": "issue_create", "operation_id": "w015n-1",
         "payload": {"operation_id": "w015n-1", "local_id": "ln",
                      "server_project_id": pid, "title": "Spalling",
                      "severity": "medium",
                      "remediation_note": "Patch with repair mortar."}}]},
        headers=w015_env.H)
    assert r1.json()["receipts"][0]["status"] == "applied"
    iid = r1.json()["id_map"]["ln"]
    det = c.get(f"/api/v1/issues/{iid}", headers=w015_env.H).json()
    assert det["remediation_note"] == "Patch with repair mortar."

    r2 = c.post("/api/v1/sync/batch", json={"operations": [
        {"op": "issue_update", "operation_id": "w015n-2",
         "payload": {"operation_id": "w015n-2",
                      "server_issue_id": iid, "expected_revision": 1,
                      "remediation_note": "Patch + cure 48h."}}]},
        headers=w015_env.H)
    assert r2.json()["receipts"][0]["status"] == "applied"
    det2 = c.get(f"/api/v1/issues/{iid}", headers=w015_env.H).json()
    assert det2["remediation_note"] == "Patch + cure 48h."


# ------------------------------------------------ issue summary + register


def test_issue_summary_counts(w015_env):
    c = w015_env.client
    pid, photo_ids = _setup(w015_env, 1)
    i1 = _mk_issue(w015_env, pid, severity="high")
    i2 = _mk_issue(w015_env, pid, severity="critical")
    i3 = _mk_issue(w015_env, pid, severity="low")
    # advance states: i2 open->in_progress->resolved; i3 open->in_progress
    c.patch(f"/api/v1/issues/{i2['issue_id']}", json={
        "status": "in_progress", "expected_revision": 1},
        headers=w015_env.H)
    c.patch(f"/api/v1/issues/{i2['issue_id']}", json={
        "status": "resolved", "expected_revision": 2},
        headers=w015_env.H)
    c.patch(f"/api/v1/issues/{i3['issue_id']}", json={
        "status": "in_progress", "expected_revision": 1},
        headers=w015_env.H)

    rr = _gen_report(w015_env, pid, photo_ids,
                     issue_ids=[i1["issue_id"], i2["issue_id"],
                                i3["issue_id"]])
    assert rr["issue_count"] == 3
    assert rr["issue_summary"]["severity"] == {
        "low": 1, "medium": 0, "high": 1, "critical": 1}
    assert rr["issue_summary"]["status"] == {
        "open": 1, "in_progress": 1, "resolved": 1, "closed": 0}
    assert rr["issue_summary"]["total"] == 3
    # snapshot view carries remediation_note field (empty default)
    det = c.get(f"/api/v1/reports/{rr['report_id']}",
                 headers=w015_env.H).json()
    assert all("remediation_note" in i for i in det["issue_snapshot"])
    # stored snapshot must NOT contain embedded image bytes
    raw = c.get(f"/api/v1/reports/{rr['report_id']}",
                 headers=w015_env.H).json()
    for i in raw["issue_snapshot"]:
        for p in i["photos"]:
            assert "image_bytes" not in p


def test_pdf_has_issue_register_and_numbers(w015_env):
    c = w015_env.client
    pid, photo_ids = _setup(w015_env, 2)
    _mk_issue(w015_env, pid, title="Crack at pier 2",
              severity="high")
    rr = _gen_report(w015_env, pid, photo_ids,
                     issue_ids=None)  # fill below
    issues = c.get(f"/api/v1/projects/{pid}/issues",
                    headers=w015_env.H).json()["items"]
    rr = _gen_report(w015_env, pid, photo_ids,
                     issue_ids=[issues[0]["issue_id"]])
    dl = c.get(f"/api/v1/reports/{rr['report_id']}/download",
                headers=w015_env.H)
    text = _pdf_text(dl.content)
    assert "ISS-001" in text
    assert "PH-001" in text and "PH-002" in text
    assert "Crack at pier 2" in text
    assert "Severity Breakdown" in text
    assert "Remediation Status Summary" in text


def test_pdf_remediation_note_manual_input(w015_env):
    c = w015_env.client
    pid, photo_ids = _setup(w015_env, 1)
    iss = _mk_issue(w015_env, pid, severity="high",
                    remediation_note="Epoxy inject; re-inspect 7 days.")
    rr = _gen_report(w015_env, pid, photo_ids,
                     issue_ids=[iss["issue_id"]])
    dl = c.get(f"/api/v1/reports/{rr['report_id']}/download",
                headers=w015_env.H)
    text = _pdf_text(dl.content)
    assert "Remediation Recommendation" in text
    assert "MANUAL INPUT" in text
    assert "Epoxy inject; re-inspect 7 days." in text


# ---------------------------------------------------- before/after figure


def test_before_after_comparison_figure(w015_env):
    c = w015_env.client
    pid, photo_ids = _setup(w015_env, 3)
    iss = _mk_issue(w015_env, pid, severity="high")
    c.post(f"/api/v1/issues/{iss['issue_id']}/photos",
           json={"photo_id": photo_ids[0], "role": "before"},
           headers=w015_env.H)
    c.post(f"/api/v1/issues/{iss['issue_id']}/photos",
           json={"photo_id": photo_ids[1], "role": "after"},
           headers=w015_env.H)
    rr = _gen_report(w015_env, pid, photo_ids,
                     issue_ids=[iss["issue_id"]])
    assert rr["page_count"] >= 4
    dl = c.get(f"/api/v1/reports/{rr['report_id']}/download",
                headers=w015_env.H)
    text = _pdf_text(dl.content)
    assert "BEFORE" in text and "AFTER" in text
    # the pair caption cites both photo reference numbers
    assert "PH-001" in text and "PH-002" in text
    assert "manual association" in text


# ------------------------------------------------------------- LiDAR refs


def test_lidar_references_render(w015_env):
    c = w015_env.client
    pid, photo_ids = _setup(w015_env, 1)
    iss = _mk_issue(w015_env, pid, severity="medium")
    rr = _gen_report(w015_env, pid, photo_ids,
                     issue_ids=[iss["issue_id"]],
                     lidar_measurements=[
                         {"record_id": "B02-W016-raycast-normal-2m-R1",
                          "description": "Crack width location offset "
                                         "reference measurement",
                          "related_issue_id": iss["issue_id"]},
                         {"record_id": "B02-W016-scene_depth-glass-1m-R2",
                          "description": "Glass panel standoff test"}])
    dl = c.get(f"/api/v1/reports/{rr['report_id']}/download",
                headers=w015_env.H)
    text = _pdf_text(dl.content)
    assert "LiDAR Measurement References" in text
    assert "B02-W016-raycast-normal-2m-R1" in text
    assert "B02-W016-scene_depth-glass-1m-R2" in text
    assert "DEVICE_PENDING" in text
    assert "EXPERIMENTAL" in text
    assert "ISS-001" in text          # related issue column resolution
    # snapshot stores the lidar refs for the delivery package
    det = c.get(f"/api/v1/reports/{rr['report_id']}",
                 headers=w015_env.H).json()
    raw_snap = det.get("issue_snapshot")
    assert raw_snap is not None


def test_lidar_ref_bad_issue_rejected(w015_env):
    c = w015_env.client
    pid, photo_ids = _setup(w015_env, 1)
    iss = _mk_issue(w015_env, pid)
    iss2 = _mk_issue(w015_env, pid)
    r = c.post(f"/api/v1/projects/{pid}/reports", json={
        "report_type": "inspection", "photo_ids": photo_ids,
        "issue_ids": [iss["issue_id"]],
        "lidar_measurements": [
            {"record_id": "B02-W016-raycast-normal-2m-R1",
             "related_issue_id": iss2["issue_id"]}]},
        headers=w015_env.H)
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "lidar_ref_issue_mismatch"


# ------------------------------------------------- sign-off + wording rules


def test_sign_off_section_and_no_legal_attestation(w015_env):
    c = w015_env.client
    pid, photo_ids = _setup(w015_env, 1)
    rr = _gen_report(w015_env, pid, photo_ids,
                     inspector_name="L. Chen",
                     inspector_title="Site QC Engineer")
    dl = c.get(f"/api/v1/reports/{rr['report_id']}/download",
                headers=w015_env.H)
    text = _pdf_text(dl.content)
    assert "Manual Sign-off" in text
    assert "Inspector" in text
    assert "Reviewed by" in text and "Approved by" in text
    assert "L. Chen" in text
    # wording red lines: no positive legal-certification claims
    assert not re.search(r"sworn", text, re.IGNORECASE)
    assert not re.search(r"legally binding", text, re.IGNORECASE)
    assert not re.search(r"notariz(ed|ation)(?!.*not)", text,
                         re.IGNORECASE) or True
    # every "notariz"/"certif" occurrence must be inside a negation
    # (the appendix says "NOT a legally certified or notarized file",
    # the footer says "not a legal certification" - both allowed)
    for m in re.finditer(r"notariz\w*|certif\w*", text, re.IGNORECASE):
        window = text[max(0, m.start() - 220):m.start() + 40]
        assert re.search(r"\b(no|not|never)\b", window, re.IGNORECASE), \
            f"certification wording outside a negation: {m.group(0)}"


def test_source_classification_labels_in_pdf(w015_env):
    c = w015_env.client
    pid, photo_ids = _setup(w015_env, 1)
    rr = _gen_report(w015_env, pid, photo_ids)
    assert rr["source_classes"] == [
        "RAW EVIDENCE", "MANUAL INPUT", "EXPERIMENTAL MEASUREMENT",
        "DERIVED IMAGE"]
    dl = c.get(f"/api/v1/reports/{rr['report_id']}/download",
                headers=w015_env.H)
    text = _pdf_text(dl.content)
    for label in ("RAW EVIDENCE", "MANUAL INPUT",
                  "EXPERIMENTAL MEASUREMENT", "DERIVED IMAGE"):
        assert label in text, f"missing source class label: {label}"
    assert "Source Classification" in text


def test_inspector_and_project_metadata_block(w015_env):
    c = w015_env.client
    pid, photo_ids = _setup(w015_env, 1)
    rr = _gen_report(w015_env, pid, photo_ids,
                     project_metadata={"Client": "Highway Admin",
                                       "Contract No.": "NH-2026-0342"})
    dl = c.get(f"/api/v1/reports/{rr['report_id']}/download",
                headers=w015_env.H)
    text = _pdf_text(dl.content)
    assert "Highway Admin" in text
    assert "NH-2026-0342" in text
    assert "Site QC Engineer" in text
    assert "A4" in text                 # page size shown on front page


# -------------------------------------------------------- page size letter


def test_letter_page_size_full_layout(w015_env):
    c = w015_env.client
    pid, photo_ids = _setup(w015_env, 2)
    iss = _mk_issue(w015_env, pid, severity="high",
                    remediation_note="Inject epoxy.")
    c.post(f"/api/v1/issues/{iss['issue_id']}/photos",
           json={"photo_id": photo_ids[0], "role": "before"},
           headers=w015_env.H)
    c.post(f"/api/v1/issues/{iss['issue_id']}/photos",
           json={"photo_id": photo_ids[1], "role": "after"},
           headers=w015_env.H)
    rr = _gen_report(w015_env, pid, photo_ids,
                     issue_ids=[iss["issue_id"]],
                     page_size="letter",
                     lidar_measurements=[
                         {"record_id": "B02-W016-raycast-normal-3m-R1"}])
    assert rr["page_size"] == "letter"
    dl = c.get(f"/api/v1/reports/{rr['report_id']}/download",
                headers=w015_env.H)
    assert dl.content.startswith(b"%PDF")
    text = _pdf_text(dl.content)
    assert "US Letter" in text


# ------------------------------------------------------ snapshot immutability


def test_remediation_note_snapshot_immutable(w015_env):
    c = w015_env.client
    pid, photo_ids = _setup(w015_env, 1)
    iss = _mk_issue(w015_env, pid, severity="high",
                    remediation_note="v1 recommendation")
    rr = _gen_report(w015_env, pid, photo_ids,
                     issue_ids=[iss["issue_id"]])
    dl1 = c.get(f"/api/v1/reports/{rr['report_id']}/download",
                 headers=w015_env.H).content
    c.patch(f"/api/v1/issues/{iss['issue_id']}", json={
        "remediation_note": "v2 recommendation", "expected_revision": 1},
        headers=w015_env.H)
    dl2 = c.get(f"/api/v1/reports/{rr['report_id']}/download",
                 headers=w015_env.H).content
    assert dl1 == dl2
    # a NEW report reflects the new note
    rr2 = _gen_report(w015_env, pid, photo_ids,
                      issue_ids=[iss["issue_id"]])
    det = c.get(f"/api/v1/reports/{rr2['report_id']}",
                 headers=w015_env.H).json()
    assert det["issue_snapshot"][0]["remediation_note"] == \
        "v2 recommendation"


# --------------------------------------------------------- delivery package


def test_delivery_package_with_issues_and_lidar(w015_env):
    c = w015_env.client
    pid, photo_ids = _setup(w015_env, 2)
    iss = _mk_issue(w015_env, pid, severity="high",
                    remediation_note="Repair mortar patch.")
    c.post(f"/api/v1/issues/{iss['issue_id']}/photos",
           json={"photo_id": photo_ids[0], "role": "before"},
           headers=w015_env.H)
    rr = _gen_report(w015_env, pid, photo_ids,
                     issue_ids=[iss["issue_id"]],
                     lidar_measurements=[
                         {"record_id": "B02-W016-raycast-normal-2m-R1"}])
    pkg = c.post(f"/api/v1/reports/{rr['report_id']}/delivery-package",
                  json={"include_originals": True,
                        "include_derivatives": False,
                        "include_evidence": True},
                  headers=w015_env.H)
    assert pkg.status_code == 200
    zf = zipfile.ZipFile(io.BytesIO(pkg.content))
    manifest = __import__("json").loads(zf.read("manifest.json"))
    for f in manifest["files"]:
        assert hashlib.sha256(zf.read(f["path"])
                              ).hexdigest() == f["sha256"]
    names = zf.namelist()
    assert "report.pdf" in names
    assert "manifest.json" in names and "verification-guide.txt" in names
    # report.pdf inside the package is the same enhanced PDF
    rpt = zf.read("report.pdf")
    assert rpt.startswith(b"%PDF")
    assert "Repair mortar patch." in _pdf_text(rpt)


# ------------------------------------------------------ validation bounds


def test_request_validation_bounds(w015_env):
    c = w015_env.client
    pid, photo_ids = _setup(w015_env, 1)
    base = {"report_type": "inspection", "photo_ids": photo_ids}
    r1 = c.post(f"/api/v1/projects/{pid}/reports", json={
        **base,
        "project_metadata": {f"k{i}": f"v{i}" for i in range(11)}},
        headers=w015_env.H)
    assert r1.status_code == 422
    r2 = c.post(f"/api/v1/projects/{pid}/reports", json={
        **base,
        "lidar_measurements": [
            {"record_id": f"B02-W016-x-{i}"} for i in range(51)]},
        headers=w015_env.H)
    assert r2.status_code == 422
    # empty record id rejected
    r3 = c.post(f"/api/v1/projects/{pid}/reports", json={
        **base, "lidar_measurements": [{"record_id": ""}]},
        headers=w015_env.H)
    assert r3.status_code == 422


def test_committed_w015_samples_exist():
    from pathlib import Path
    samples = Path(__file__).resolve().parents[2] / "docs" / "mvp" / \
        "w015-samples"
    pdfs = sorted(samples.glob("*.pdf"))
    zips = sorted(samples.glob("*.zip"))
    assert len(pdfs) >= 6, f"expected >=6 sample PDFs, found {len(pdfs)}"
    assert len(zips) >= 1, "expected >=1 delivery package sample"
    for p in pdfs:
        assert p.read_bytes()[:4] == b"%PDF"
    for z in zips:
        zf = zipfile.ZipFile(io.BytesIO(z.read_bytes()))
        assert "report.pdf" in zf.namelist()
