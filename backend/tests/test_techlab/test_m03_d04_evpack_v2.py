"""M03-D04 tests - open evidence package V2."""

from __future__ import annotations

import json
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from techlab.business.evpack_v2 import (  # noqa: E402
    EVPACK_VERSION, EvpackError, Section, build_evpack, import_v1_package,
    safe_rel_path, verify_evpack, verify_night05_zip)


def _pack_sections() -> list[Section]:
    ev = Section("evidence")
    ev.add("rec-001.json", b'{"version": "1.1", "record_id": "rec-001"}')
    an = Section("annotations")
    an.add("ph-1.json", b'{"items": [{"text": "crack"}]}')
    iss = Section("issues")
    iss.add("iss-1.json", b'{"title": "Seepage", "status": "open"}')
    ts = Section("timestamps")
    ts.add("rec-001.tsr", b"\x30\x82\x01\x02fake-token")
    return [ev, an, iss, ts]


class TestBuildVerify:
    def test_roundtrip_ok(self, tmp_path):
        out = tmp_path / "p.evpack"
        stats = build_evpack(_pack_sections(),
                             {"project": "Site A"}, out)
        assert stats["entries"] == 4
        v = verify_evpack(out)
        assert v["ok"], v["problems"]
        assert v["entries"] == 4

    def test_manifest_carries_version_and_night05_note(self, tmp_path):
        out = tmp_path / "p.evpack"
        build_evpack(_pack_sections(), {}, out)
        with zipfile.ZipFile(out) as zf:
            m = json.loads(zf.read("manifest.json"))
        assert m["format_version"] == EVPACK_VERSION
        assert "strict subset" in m["night05_map"]

    def test_tamper_detected(self, tmp_path):
        out = tmp_path / "p.evpack"
        build_evpack(_pack_sections(), {}, out)
        with zipfile.ZipFile(out) as zf:
            items = {n: zf.read(n) for n in zf.namelist()}
        items["evidence/rec-001.json"] = \
            items["evidence/rec-001.json"] + b"tampered"
        out2 = tmp_path / "t.evpack"
        with zipfile.ZipFile(out2, "w") as zf:
            for n, d in items.items():
                zf.writestr(n, d)
        v = verify_evpack(out2)
        assert not v["ok"]
        assert any("TAMPERED" in p and "rec-001" in p
                   for p in v["problems"])

    def test_missing_file_named(self, tmp_path):
        out = tmp_path / "p.evpack"
        build_evpack(_pack_sections(), {}, out)
        with zipfile.ZipFile(out) as zf:
            items = {n: zf.read(n) for n in zf.namelist()}
        del items["issues/iss-1.json"]
        out2 = tmp_path / "m.evpack"
        with zipfile.ZipFile(out2, "w") as zf:
            for n, d in items.items():
                zf.writestr(n, d)
        v = verify_evpack(out2)
        assert not v["ok"]
        assert any("MISSING" in p and "iss-1" in p
                   for p in v["problems"])

    def test_unknown_version_refused(self, tmp_path):
        out = tmp_path / "p.evpack"
        build_evpack(_pack_sections(), {}, out)
        with zipfile.ZipFile(out) as zf:
            items = {n: zf.read(n) for n in zf.namelist()}
        m = json.loads(items["manifest.json"])
        m["format_version"] = "evpack/9.9"
        items["manifest.json"] = json.dumps(m).encode()
        out2 = tmp_path / "u.evpack"
        with zipfile.ZipFile(out2, "w") as zf:
            for n, d in items.items():
                zf.writestr(n, d)
        v = verify_evpack(out2)
        assert not v["ok"]
        assert any("unknown format_version" in p for p in v["problems"])

    def test_extra_files_strict_vs_lenient(self, tmp_path):
        out = tmp_path / "p.evpack"
        build_evpack(_pack_sections(), {}, out)
        with zipfile.ZipFile(out) as zf:
            items = {n: zf.read(n) for n in zf.namelist()}
        items["mystery.bin"] = b"x"
        out2 = tmp_path / "e.evpack"
        with zipfile.ZipFile(out2, "w") as zf:
            for n, d in items.items():
                zf.writestr(n, d)
        strict = verify_evpack(out2, strict=True)
        lenient = verify_evpack(out2, strict=False)
        assert not strict["ok"]
        assert any("EXTRA" in p for p in strict["problems"])
        assert lenient["ok"]       # lenient reports nothing extra:
        # unlisted files cannot lie about listed content

    def test_path_safety(self):
        with pytest.raises(EvpackError):
            safe_rel_path("../escape.jpg")
        with pytest.raises(EvpackError):
            safe_rel_path("/abs.jpg")
        with pytest.raises(EvpackError):
            safe_rel_path("back\\slash.jpg")
        assert safe_rel_path("a/b.jpg") == "a/b.jpg"

    def test_duplicate_path_refused(self, tmp_path):
        s1 = Section("evidence")
        s1.add("dup.json", b"a")
        s2 = Section("evidence")
        s2.add("dup.json", b"b")
        with pytest.raises(EvpackError, match="duplicate"):
            build_evpack([s1, s2], {}, tmp_path / "d.evpack")


class TestV1Import:
    def test_v1_package_becomes_sections(self, tmp_path):
        v1 = tmp_path / "v1pkg"
        (v1 / "originals" / "ab").mkdir(parents=True)
        (v1 / "originals" / "ab" / ("ab" * 31 + "c" + ".jpg")) \
            .write_bytes(b"photo-bytes")
        sha = "ab" * 31 + "c"
        (v1 / "package.json").write_text(json.dumps({
            "records": [
                {"photo": {"photo_id": "ph-1",
                            "original_sha256": sha},
                 "issues": [{"issue_id": "iss-9",
                              "title": "Crack"}]}]}),
            encoding="utf-8")
        sections = import_v1_package(v1)
        names = [n for s in sections for n, _ in s.files]
        assert any("c.jpg" in n for n in names)
        assert "iss-9.json" in names


class TestNight05Subset:
    def test_night05_zip_verified_against_own_manifest(self, tmp_path):
        z = tmp_path / "n05.zip"
        with zipfile.ZipFile(z, "w") as zf:
            content = b"small photo"
            digest = __import__("hashlib").sha256(content).hexdigest()
            zf.writestr("IMG_001.jpg", content)
            zf.writestr("manifest.json", json.dumps({
                "entries": [{"name": "IMG_001.jpg",
                              "sha256": digest}]}))
        v = verify_night05_zip(z)
        assert v["ok"], v["problems"]
        assert v["night05_subset"] is True

    def test_night05_tamper_detected(self, tmp_path):
        z = tmp_path / "n05.zip"
        with zipfile.ZipFile(z, "w") as zf:
            content = b"small photo"
            digest = __import__("hashlib").sha256(content).hexdigest()
            zf.writestr("IMG_001.jpg", content + b"x")
            zf.writestr("manifest.json", json.dumps({
                "entries": [{"name": "IMG_001.jpg",
                              "sha256": digest}]}))
        v = verify_night05_zip(z)
        assert not v["ok"]
        assert any("TAMPERED" in p for p in v["problems"])
