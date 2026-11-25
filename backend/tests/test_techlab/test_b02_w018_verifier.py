"""B02-W018 - public evidence verifier CLI tests.

End-to-end: the REAL CLI runs as a subprocess against directories,
zip packages and malicious packages (tampered / missing files /
zip-slip traversal / bomb guard / corrupt zip / bad public key /
orphan files / batch exit-code precedence). Unit-level: the zip
safety static checks and the six-layer evaluator.

All keys are generated TEST-ONLY keys (never production, never
committed). Real-device cross-validation is DEVICE_PENDING: these
tests use the public test vectors in test-vectors/fixed-vectors.json.
"""

from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric import ec

# make the CLI module importable for unit-level tests
REPO_ROOT = Path(__file__).resolve().parents[3]
CLI_DIR = REPO_ROOT / "tools" / "evidence-verifier"
CLI = CLI_DIR / "verify_evidence.py"
sys.path.insert(0, str(CLI_DIR))

import verify_evidence as ve  # noqa: E402

from app.services.time_anchor import TimeAnchorService  # noqa: E402
from app.models.time_anchor import TimeAnchorRequest  # noqa: E402
from techlab.evidence_package import build_package  # noqa: E402
from tests.conftest import make_manifest, _encode_pub  # noqa: E402


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _make_record(key=None, seed: int = 0x42, with_location: bool = False):
    key = key or ec.generate_private_key(ec.SECP256R1())
    pub_b64 = _encode_pub(key.public_key())
    photo = bytes([seed]) * 512
    from app.models.capture import LocationData

    location = (
        LocationData(latitude=31.23, longitude=121.47, horizontal_accuracy=4.5)
        if with_location else None
    )
    manifest = make_manifest(key, pub_b64, photo_bytes=photo, location=location)
    return key, manifest, photo


def _build_pkg(tmp_path: Path, n: int = 1, with_location: bool = False,
               anchor_receipts=None, key=None) -> tuple[Path, object]:
    pkg_dir = tmp_path / "pkg"
    records = []
    first_manifest = None
    for i in range(n):
        k = key if (key and i == 0) else None
        _, manifest, photo = _make_record(k, seed=0x42 + i,
                                          with_location=with_location)
        if first_manifest is None:
            first_manifest = manifest
        records.append({
            "manifest": manifest.model_dump(),
            "photo_bytes": photo,
            "location_meta": None,
        })
    build_package(
        pkg_dir, records, device_id="lab-device",
        created_at="2026-11-25T10:00:00+00:00",
        anchor_receipts=anchor_receipts,
    )
    return pkg_dir, first_manifest


def _zip_dir(pkg_dir: Path, zip_path: Path, wrap: bool = False) -> Path:
    """Zip a package. wrap=True puts everything inside one top-level
    directory (the common 'zip a folder' layout)."""
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in sorted(pkg_dir.rglob("*")):
            if not f.is_file():
                continue
            rel = f.relative_to(pkg_dir).as_posix()
            if wrap:
                arc = f"{pkg_dir.name}/{rel}"
            else:
                arc = rel
            zf.write(f, arc)
    return zip_path


def _run_cli(*paths, extra: list[str] | None = None
             ) -> subprocess.CompletedProcess:
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    return subprocess.run(
        [sys.executable, str(CLI), *[str(p) for p in paths], *(extra or [])],
        capture_output=True, encoding="utf-8", errors="replace",
        cwd=str(REPO_ROOT), env=env,
    )


def _read_json(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8"))


def _first_record(out_json: Path) -> dict:
    return _read_json(out_json)["packages"][0]["records"][0]


# ---------------------------------------------------------------------------
# unit level: zip safety static checks
# ---------------------------------------------------------------------------
def _zip_entries(tmp_path: Path, entries: dict[str, bytes]) -> Path:
    zp = tmp_path / "evil.zip"
    with zipfile.ZipFile(zp, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return zp


def _raw_zip_one_entry(name: str, data: bytes) -> bytes:
    """Hand-built zip bytes with ONE entry and an arbitrary raw name.

    Python's zipfile.writestr normalizes backslashes to '/', so a
    real-world hostile zip entry name with backslashes must be built
    at the byte level (STORED method, minimal headers)."""
    import struct
    import zlib

    name_b = name.encode("utf-8")
    crc = zlib.crc32(data) & 0xFFFFFFFF
    local = struct.pack(
        "<IHHHHHIIIHH", 0x04034B50, 20, 0, 0, 0, 0,
        crc, len(data), len(data), len(name_b), 0,
    ) + name_b + data
    cd = struct.pack(
        "<IHHHHHHIIIHHHHHII",
        0x02014B50, 20, 20, 0, 0, 0, 0,
        crc, len(data), len(data), len(name_b), 0, 0, 0, 0, 0, 0,
    ) + name_b
    eocd = struct.pack(
        "<IHHHHIIH", 0x06054B50, 0, 0, 1, 1, len(cd), len(local), 0,
    )
    return local + cd + eocd


class TestZipSafety:
    def test_clean_zip_passes_check(self, tmp_path):
        zp = _zip_entries(tmp_path, {
            "package.json": b"{}", "photos/a.jpg": b"x" * 16,
        })
        with zipfile.ZipFile(zp) as zf:
            assert ve.check_zip_safety(zf, 1024 * 1024) == []

    def test_rejects_dotdot_traversal(self, tmp_path):
        zp = _zip_entries(tmp_path, {"../../evil.txt": b"pwn"})
        with zipfile.ZipFile(zp) as zf:
            problems = ve.check_zip_safety(zf, 1024 * 1024)
        assert any("path traversal" in p for p in problems)

    def test_rejects_absolute_path(self, tmp_path):
        zp = _zip_entries(tmp_path, {"/etc/pwn.txt": b"pwn"})
        with zipfile.ZipFile(zp) as zf:
            problems = ve.check_zip_safety(zf, 1024 * 1024)
        assert any("absolute path" in p for p in problems)

    def test_rejects_windows_drive_letter(self, tmp_path):
        zp = _zip_entries(tmp_path, {"C:/evil.txt": b"pwn"})
        with zipfile.ZipFile(zp) as zf:
            problems = ve.check_zip_safety(zf, 1024 * 1024)
        assert any("drive letter" in p for p in problems)

    def test_rejects_backslash_separator(self, tmp_path):
        # entry name with a backslash: built at the byte level because
        # zipfile.writestr normalizes backslashes away on write.
        # CPython 3.12's zipfile READ side also normalizes '\' to '/',
        # so this entry resurfaces as 'evil/../pwn.txt' and is caught
        # by the '..' traversal guard - exit code 2 either way.
        zp = tmp_path / "backslash.zip"
        zp.write_bytes(_raw_zip_one_entry("evil\\..\\pwn.txt", b"pwn"))
        out = _run_cli(zp)
        assert out.returncode == 2
        assert ("backslash" in out.stdout
                or "path traversal" in out.stdout)

    def test_backslash_guard_logic_reachable(self):
        # unit-level: feed the raw name straight into the checker
        # (bypassing zipfile's normalization) to prove the backslash
        # branch itself rejects the entry
        import types

        info = types.SimpleNamespace(
            filename="evil\\..\\pwn.txt", file_size=16,
            compress_size=16, is_dir=lambda: False,
        )

        class _FakeZip:
            def infolist(self):
                return [info]

        problems = ve.check_zip_safety(_FakeZip(), 1024 * 1024)
        assert any("backslash" in p for p in problems)

    def test_rejects_case_insensitive_collision(self, tmp_path):
        zp = _zip_entries(tmp_path, {
            "photos/a.jpg": b"x", "photos/A.JPG": b"y",
        })
        with zipfile.ZipFile(zp) as zf:
            problems = ve.check_zip_safety(zf, 1024 * 1024)
        assert any("collide case-insensitively" in p for p in problems)

    def test_rejects_total_size_bomb(self, tmp_path):
        # 2 MiB of compressible data against a 1 MiB total guard
        zp = _zip_entries(tmp_path, {"big.bin": b"\x00" * (2 * 1024 * 1024)})
        with zipfile.ZipFile(zp) as zf:
            problems = ve.check_zip_safety(zf, 1024 * 1024)
        assert any("total uncompressed size exceeds guard" in p
                   for p in problems)

    def test_rejects_extreme_compression_ratio(self, tmp_path):
        # 4 MiB -> few KiB is > 200:1 and above the 1 MiB floor
        zp = _zip_entries(tmp_path, {"bomb.bin": b"\x00" * (4 * 1024 * 1024)})
        with zipfile.ZipFile(zp) as zf:
            problems = ve.check_zip_safety(zf, 64 * 1024 * 1024)
        assert any("extreme compression ratio" in p for p in problems)


# ---------------------------------------------------------------------------
# end-to-end: happy paths
# ---------------------------------------------------------------------------
class TestValidPackages:
    def test_valid_directory_package_exit_zero(self, tmp_path):
        pkg, _ = _build_pkg(tmp_path)
        out = _run_cli(pkg, "--json", str(tmp_path / "r.json"))
        assert out.returncode == 0, out.stdout + out.stderr
        rec = _first_record(tmp_path / "r.json")
        assert rec["verdict"] == "pass"
        layers = rec["layers"]
        assert layers["L1_file_integrity"] == "pass"
        assert layers["L2_signature_math"] == "pass"
        assert layers["L3_device_identity"] == "not_checked"
        assert layers["L4_hardware_provenance"] == "not_verifiable_offline"
        assert layers["L5_trusted_time"] == "device_reported"
        assert layers["L6_location_provenance"] == "none"

    def test_valid_zip_package_exit_zero(self, tmp_path):
        pkg, _ = _build_pkg(tmp_path)
        zp = _zip_dir(pkg, tmp_path / "pkg.zip")
        out = _run_cli(zp, "--json", str(tmp_path / "r.json"))
        assert out.returncode == 0, out.stdout + out.stderr
        rep = _read_json(tmp_path / "r.json")
        assert rep["summary"] == {
            "packages": 1, "passed": 1, "failed": 0, "corrupt": 0,
        }

    def test_zip_with_single_top_level_dir_wrapper(self, tmp_path):
        pkg, _ = _build_pkg(tmp_path)
        zp = _zip_dir(pkg, tmp_path / "wrapped.zip", wrap=True)
        out = _run_cli(zp)
        assert out.returncode == 0, out.stdout + out.stderr

    def test_signed_location_reports_L6_signed_in_payload(self, tmp_path):
        pkg, _ = _build_pkg(tmp_path, with_location=True)
        out = _run_cli(pkg, "--json", str(tmp_path / "r.json"))
        assert out.returncode == 0, out.stdout + out.stderr
        rec = _first_record(tmp_path / "r.json")
        assert rec["layers"]["L6_location_provenance"] == "signed_in_payload"
        assert any("coordinate source" in n for n in rec["notes"])

    def test_public_fixed_vectors_package_passes(self, tmp_path):
        """The PUBLIC test vectors (test-vectors/fixed-vectors.json)
        assembled into a package must verify end-to-end."""
        vectors = json.loads(
            (REPO_ROOT / "test-vectors" / "fixed-vectors.json")
            .read_text(encoding="utf-8")
        )
        manifest_dict = dict(vectors["manifest"])
        manifest_dict["signature"] = vectors["signature"]["base64"]
        manifest_dict["public_key"] = vectors["public_key"]["base64"]
        photo = bytes.fromhex(vectors["photo"]["bytes_hex"])
        pkg = tmp_path / "fixed_vectors_pkg"
        build_package(
            pkg,
            [{"manifest": manifest_dict, "photo_bytes": photo,
              "location_meta": None}],
            device_id="vector-device-001",
            created_at="2026-11-25T10:00:00+00:00",
        )
        out = _run_cli(pkg, "--json", str(tmp_path / "r.json"))
        assert out.returncode == 0, out.stdout + out.stderr
        rec = _first_record(tmp_path / "r.json")
        assert rec["verdict"] == "pass"
        assert rec["layers"]["L1_file_integrity"] == "pass"
        assert rec["layers"]["L2_signature_math"] == "pass"

    def test_registry_reports_registered_identity(self, tmp_path):
        pkg, manifest = _build_pkg(tmp_path)
        reg = tmp_path / "registry.json"
        reg.write_text(json.dumps({
            "keys": [{
                "public_key_b64": manifest.public_key,
                "device_id": "lab-device",
            }]
        }), encoding="utf-8")
        out = _run_cli(pkg, "--json", str(tmp_path / "r.json"),
                       "--registry", str(reg))
        assert out.returncode == 0, out.stdout + out.stderr
        rec = _first_record(tmp_path / "r.json")
        assert rec["layers"]["L3_device_identity"] == "registered"

    def test_registry_with_unknown_key_reports_unregistered(self, tmp_path):
        pkg, _ = _build_pkg(tmp_path)
        other_key = ec.generate_private_key(ec.SECP256R1())
        reg = tmp_path / "registry.json"
        reg.write_text(json.dumps({
            "keys": [{
                "public_key_b64": base64.b64encode(
                    _encode_pub(other_key.public_key())
                ).decode(),
                "device_id": "someone-else",
            }]
        }), encoding="utf-8")
        out = _run_cli(pkg, "--json", str(tmp_path / "r.json"),
                       "--registry", str(reg))
        assert out.returncode == 0, out.stdout + out.stderr
        rec = _first_record(tmp_path / "r.json")
        assert rec["verdict"] == "pass_unregistered_key"
        assert rec["layers"]["L3_device_identity"] == "unregistered"

    def test_anchor_receipt_reports_server_anchored_time(self, tmp_path):
        service = TimeAnchorService(key_id="b02-key")
        request = TimeAnchorRequest(
            batch_id="batch-b02w018", chain_head_hash="a" * 64,
            chain_tail_hash="b" * 64, record_count=1,
            device_reported_start_time="2026-11-25T10:00:00+00:00",
            device_reported_end_time="2026-11-25T10:01:00+00:00",
        )
        service.create_anchor(request)
        receipt = service._store.get_by_batch_id(request.batch_id).model_dump()
        pem = service.get_public_key_pem()
        pem_path = tmp_path / "anchor_key.pem"
        pem_path.write_text(pem, encoding="utf-8")

        pkg, _ = _build_pkg(tmp_path, anchor_receipts=None)
        # rebuild WITH the receipt bound to the record
        import shutil as _sh
        _sh.rmtree(pkg)
        _, manifest, photo = _make_record(seed=0x42)
        build_package(
            pkg,
            [{"manifest": manifest.model_dump(), "photo_bytes": photo,
              "location_meta": None}],
            device_id="lab-device",
            created_at="2026-11-25T10:00:00+00:00",
            anchor_receipts={manifest.record_id: receipt},
        )
        out = _run_cli(pkg, "--json", str(tmp_path / "r.json"),
                       "--anchor-pubkey", str(pem_path),
                       "--anchor-key-id", "b02-key")
        assert out.returncode == 0, out.stdout + out.stderr
        rec = _first_record(tmp_path / "r.json")
        assert rec["layers"]["L5_trusted_time"] == "server_anchored"

    def test_output_carries_disclaimer_and_device_pending(self, tmp_path):
        pkg, _ = _build_pkg(tmp_path)
        out = _run_cli(pkg)
        assert "NOT proof that the photo content is genuine" in out.stdout
        assert "NOT a legal certification" in out.stdout
        assert "DEVICE_PENDING" in out.stdout

    def test_json_report_structure(self, tmp_path):
        pkg, _ = _build_pkg(tmp_path)
        jp = tmp_path / "r.json"
        tp = tmp_path / "report.txt"
        out = _run_cli(pkg, "--json", str(jp), "--report", str(tp))
        assert out.returncode == 0, out.stdout + out.stderr
        rep = _read_json(jp)
        assert rep["tool"] == "workcam-evidence-verifier"
        assert rep["task"] == "B02-W018"
        assert rep["offline"] is True
        assert rep["device_pending"] is True
        assert len(rep["disclaimer"]) == 5
        assert "exit_code" in rep
        assert tp.exists()
        assert "Summary:" in tp.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# end-to-end: tamper / missing-file negatives (exit 1)
# ---------------------------------------------------------------------------
class TestTampering:
    def test_tampered_photo_in_zip_exit_one(self, tmp_path):
        pkg, manifest = _build_pkg(tmp_path)
        target = pkg / "photos" / f"{manifest.record_id}.jpg"
        target.write_bytes(b"\x99" * 512)
        zp = _zip_dir(pkg, tmp_path / "tampered.zip")
        out = _run_cli(zp, "--json", str(tmp_path / "r.json"))
        assert out.returncode == 1
        rec = _first_record(tmp_path / "r.json")
        assert rec["verdict"] == "fail"
        assert rec["layers"]["L1_file_integrity"] == "fail"
        assert rec["layers"]["L2_signature_math"] == "pass"

    def test_missing_manifest_in_zip_is_cannot_verify(self, tmp_path):
        pkg, manifest = _build_pkg(tmp_path)
        (pkg / "manifests" / f"{manifest.record_id}.json").unlink()
        zp = _zip_dir(pkg, tmp_path / "missing.zip")
        out = _run_cli(zp, "--json", str(tmp_path / "r.json"))
        assert out.returncode == 1
        rec = _first_record(tmp_path / "r.json")
        assert rec["verdict"] == "cannot_verify"
        assert rec["layers"]["L2_signature_math"] == "cannot_verify"
        # cannot_verify is never reported as a pass (uppercased verdict)
        assert "CANNOT_VERIFY" in out.stdout

    def test_missing_photo_in_zip_exit_one(self, tmp_path):
        pkg, manifest = _build_pkg(tmp_path)
        (pkg / "photos" / f"{manifest.record_id}.jpg").unlink()
        zp = _zip_dir(pkg, tmp_path / "nophoto.zip")
        out = _run_cli(zp)
        assert out.returncode == 1

    def test_bad_public_key_is_L2_fail_not_cannot_verify(self, tmp_path):
        """A manifest with a malformed public key (64 bytes, no 0x04
        prefix - the invalid_public_key public vector) must be reported
        as a signature-math FAIL, never as pass or as cannot-verify."""
        key, manifest, photo = _make_record(seed=0x42)
        bad = manifest.model_dump()
        bad["public_key"] = "A" * 86  # 64 raw bytes after b64, no 0x04
        pkg = tmp_path / "pkg_badkey"
        build_package(
            pkg,
            [{"manifest": bad, "photo_bytes": photo, "location_meta": None}],
            device_id="lab-device", created_at="2026-11-25T10:00:00+00:00",
        )
        out = _run_cli(pkg, "--json", str(tmp_path / "r.json"))
        assert out.returncode == 1
        rec = _first_record(tmp_path / "r.json")
        assert rec["verdict"] == "fail"
        assert rec["layers"]["L2_signature_math"] == "fail"

    def test_manifest_field_tamper_breaks_signature_math(self, tmp_path):
        pkg, manifest = _build_pkg(tmp_path)
        mpath = pkg / "manifests" / f"{manifest.record_id}.json"
        data = _read_json(mpath)
        data["device_id"] = "honest-company"
        mpath.write_text(json.dumps(data), encoding="utf-8")
        out = _run_cli(pkg, "--json", str(tmp_path / "r.json"))
        assert out.returncode == 1
        rec = _first_record(tmp_path / "r.json")
        assert rec["verdict"] == "fail"
        assert rec["layers"]["L2_signature_math"] == "fail"

    def test_anchor_receipt_without_trusted_key_is_cannot_verify(self, tmp_path):
        """Package CLAIMS an anchor receipt but no --anchor-pubkey was
        given: L5 cannot_verify and the record verdict must NOT be a
        bare pass."""
        service = TimeAnchorService(key_id="b02-key")
        request = TimeAnchorRequest(
            batch_id="batch-b02w018-nokey", chain_head_hash="a" * 64,
            chain_tail_hash="b" * 64, record_count=1,
            device_reported_start_time="2026-11-25T10:00:00+00:00",
            device_reported_end_time="2026-11-25T10:01:00+00:00",
        )
        service.create_anchor(request)
        receipt = service._store.get_by_batch_id(request.batch_id).model_dump()
        pkg = tmp_path / "pkg_anchor_nokey"
        _, manifest, photo = _make_record(seed=0x42)
        build_package(
            pkg,
            [{"manifest": manifest.model_dump(), "photo_bytes": photo,
              "location_meta": None}],
            device_id="lab-device", created_at="2026-11-25T10:00:00+00:00",
            anchor_receipts={manifest.record_id: receipt},
        )
        out = _run_cli(pkg, "--json", str(tmp_path / "r.json"))
        assert out.returncode == 1
        rec = _first_record(tmp_path / "r.json")
        assert rec["verdict"] == "cannot_verify"
        assert rec["layers"]["L5_trusted_time"] == "cannot_verify"

    def test_invalid_location_meta_is_a_failure(self, tmp_path):
        """A location_meta block that does not satisfy the T02 v2
        schema (unknown source) is a manifest-consistency failure."""
        pkg, manifest, photo = _make_record(seed=0x42)
        pkg_dir = tmp_path / "pkg_loc"
        build_package(
            pkg_dir,
            [{"manifest": manifest.model_dump(), "photo_bytes": photo,
              "location_meta": {"source": "smoke-signal", "latitude": 1.0}}],
            device_id="lab-device", created_at="2026-11-25T10:00:00+00:00",
        )
        out = _run_cli(pkg_dir, "--json", str(tmp_path / "r.json"))
        assert out.returncode == 1
        rec = _first_record(tmp_path / "r.json")
        assert rec["verdict"] == "fail"
        assert any("location_meta invalid" in p for p in rec["problems"])

    def test_orphan_file_not_in_manifest_exit_one(self, tmp_path):
        """Files physically present but not listed in package.json are
        a manifest-consistency failure."""
        pkg, _ = _build_pkg(tmp_path)
        (pkg / "photos" / "extra-unlisted.jpg").write_bytes(b"smuggled")
        out = _run_cli(pkg, "--json", str(tmp_path / "r.json"))
        assert out.returncode == 1
        rep = _read_json(tmp_path / "r.json")
        assert any("orphan file" in p
                   for p in rep["packages"][0]["package_problems"])

    def test_invalid_anchor_receipt_signature_is_L5_fail(self, tmp_path):
        service = TimeAnchorService(key_id="b02-key")
        request = TimeAnchorRequest(
            batch_id="batch-b02w018-badsig", chain_head_hash="a" * 64,
            chain_tail_hash="b" * 64, record_count=1,
            device_reported_start_time="2026-11-25T10:00:00+00:00",
            device_reported_end_time="2026-11-25T10:01:00+00:00",
        )
        service.create_anchor(request)
        receipt = service._store.get_by_batch_id(request.batch_id).model_dump()
        receipt["record_count"] = 999  # tamper a SIGNED field
        pem = service.get_public_key_pem()
        pem_path = tmp_path / "anchor_key.pem"
        pem_path.write_text(pem, encoding="utf-8")

        pkg = tmp_path / "pkg_anchor_bad"
        _, manifest, photo = _make_record(seed=0x42)
        build_package(
            pkg,
            [{"manifest": manifest.model_dump(), "photo_bytes": photo,
              "location_meta": None}],
            device_id="lab-device", created_at="2026-11-25T10:00:00+00:00",
            anchor_receipts={manifest.record_id: receipt},
        )
        out = _run_cli(pkg, "--json", str(tmp_path / "r.json"),
                       "--anchor-pubkey", str(pem_path),
                       "--anchor-key-id", "b02-key")
        assert out.returncode == 1
        rec = _first_record(tmp_path / "r.json")
        assert rec["verdict"] == "fail"
        assert rec["layers"]["L5_trusted_time"] == "fail"


# ---------------------------------------------------------------------------
# end-to-end: corrupt packages (exit 2)
# ---------------------------------------------------------------------------
class TestCorruptPackages:
    def test_zip_slip_traversal_rejected_exit_two(self, tmp_path):
        """Malicious zip with a '../../evil.txt' entry: the CLI must
        refuse the WHOLE package and never write outside the extraction
        directory."""
        zp = _zip_entries(tmp_path, {
            "package.json": b"{}",
            "photos/a.jpg": b"x" * 16,
            "../../w018_zip_slip_canary.txt": b"pwn",
        })
        out = _run_cli(zp, "--json", str(tmp_path / "r.json"))
        assert out.returncode == 2
        assert "path traversal" in out.stdout
        rep = _read_json(tmp_path / "r.json")
        assert rep["packages"][0]["outcome"] == "corrupt"
        # the canary must NOT have escaped
        assert not (tmp_path / "w018_zip_slip_canary.txt").exists()
        assert not (tmp_path.parent / "w018_zip_slip_canary.txt").exists()

    def test_zip_slip_absolute_path_rejected_exit_two(self, tmp_path):
        zp = _zip_entries(tmp_path, {"/abs/pwn.txt": b"pwn"})
        out = _run_cli(zp)
        assert out.returncode == 2
        assert "absolute path" in out.stdout

    def test_zip_slip_drive_letter_rejected_exit_two(self, tmp_path):
        zp = _zip_entries(tmp_path, {"C:/Windows/evil.txt": b"pwn"})
        out = _run_cli(zp)
        assert out.returncode == 2
        assert "drive letter" in out.stdout

    def test_broken_zip_exit_two(self, tmp_path):
        zp = tmp_path / "garbage.zip"
        zp.write_bytes(b"this is not a zip file at all" * 10)
        out = _run_cli(zp)
        assert out.returncode == 2
        assert "not a valid zip file" in out.stdout

    def test_zip_size_guard_triggers_exit_two(self, tmp_path):
        zp = _zip_entries(tmp_path, {"big.bin": b"\x00" * (2 * 1024 * 1024)})
        out = _run_cli(zp, "--max-uncompressed-mb", "1")
        assert out.returncode == 2
        assert ("size guard" in out.stdout
                or "compression ratio" in out.stdout)

    def test_zip_case_collision_rejected_exit_two(self, tmp_path):
        zp = _zip_entries(tmp_path, {
            "photos/a.jpg": b"x", "photos/A.JPG": b"y",
        })
        out = _run_cli(zp)
        assert out.returncode == 2
        assert "collide case-insensitively" in out.stdout

    def test_empty_zip_without_package_json_exit_two(self, tmp_path):
        zp = _zip_entries(tmp_path, {"readme.txt": b"no package here"})
        out = _run_cli(zp)
        assert out.returncode == 2
        assert "package.json not found" in out.stdout

    def test_directory_without_package_json_exit_two(self, tmp_path):
        empty = tmp_path / "not_a_package"
        empty.mkdir()
        out = _run_cli(empty)
        assert out.returncode == 2
        assert "package.json" in out.stdout

    def test_corrupt_package_json_exit_two(self, tmp_path):
        bad = tmp_path / "bad_pkg"
        bad.mkdir()
        (bad / "package.json").write_text("{ nope", encoding="utf-8")
        out = _run_cli(bad)
        assert out.returncode == 2
        assert "unreadable" in out.stdout

    def test_missing_input_path_exit_two(self, tmp_path):
        out = _run_cli(tmp_path / "does_not_exist")
        assert out.returncode == 2
        assert "does not exist" in out.stdout


# ---------------------------------------------------------------------------
# end-to-end: batch mode + exit-code precedence
# ---------------------------------------------------------------------------
class TestBatchMode:
    def test_batch_all_valid_exit_zero(self, tmp_path):
        p1, _ = _build_pkg(tmp_path / "one")
        p2, _ = _build_pkg(tmp_path / "two")
        out = _run_cli(p1, p2, "--json", str(tmp_path / "r.json"))
        assert out.returncode == 0, out.stdout + out.stderr
        rep = _read_json(tmp_path / "r.json")
        assert rep["summary"]["packages"] == 2
        assert rep["summary"]["passed"] == 2

    def test_batch_mixed_valid_and_tampered_exit_one(self, tmp_path):
        p1, _ = _build_pkg(tmp_path / "good")
        p2, m2 = _build_pkg(tmp_path / "bad")
        (p2 / "photos" / f"{m2.record_id}.jpg").write_bytes(b"\x00" * 512)
        out = _run_cli(p1, p2, "--json", str(tmp_path / "r.json"))
        assert out.returncode == 1
        rep = _read_json(tmp_path / "r.json")
        assert rep["summary"]["passed"] == 1
        assert rep["summary"]["failed"] == 1

    def test_batch_corrupt_takes_precedence_exit_two(self, tmp_path):
        p1, _ = _build_pkg(tmp_path / "good")
        zp = _zip_entries(tmp_path, {"nope.txt": b"not a package"})
        out = _run_cli(p1, zp, "--json", str(tmp_path / "r.json"))
        assert out.returncode == 2
        rep = _read_json(tmp_path / "r.json")
        assert rep["summary"]["corrupt"] == 1
        assert rep["summary"]["passed"] == 1

    def test_batch_dir_and_zip_mixed(self, tmp_path):
        p1, _ = _build_pkg(tmp_path / "dirpkg")
        p2, _ = _build_pkg(tmp_path / "zippkg")
        zp = _zip_dir(p2, tmp_path / "zippkg.zip")
        out = _run_cli(p1, zp, "--json", str(tmp_path / "r.json"))
        assert out.returncode == 0, out.stdout + out.stderr
        rep = _read_json(tmp_path / "r.json")
        assert rep["summary"]["passed"] == 2
        kinds = {p["kind"] for p in rep["packages"]}
        assert kinds == {"dir", "zip"}
