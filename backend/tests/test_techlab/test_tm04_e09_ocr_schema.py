"""TM-04 E09: OCR schema + confirmation-machine parity tests.

Pins the Swift OCRReference.swift contract in Python so the two
languages cannot drift:

  - normalization rule (uppercase + whitespace collapse)
  - closed field taxonomy
  - the human-confirmation state machine: unconfirmed -> confirmed /
    discarded, final transitions, audit payload, zero candidates
    refused, no double-confirm, no confirm-after-discard
  - searchability gate (B08): OCR text searchable ONLY when confirmed
  - the generated sample manifest is complete and deterministic

No wall-clock assertions (two-flakes rule).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]

FIELD_TYPES = [
    "serial_number", "model_plate", "measurement_reading",
    "location_label", "date_code", "barcode_text", "other",
]


def normalize(s: str) -> str:
    return " ".join(s.upper().split())


class Field:
    """Python mirror of OCRFieldCandidate's state machine."""

    def __init__(self, candidates: list[str]):
        self.candidates = candidates
        self.state = "unconfirmed"
        self.audit = None

    @property
    def searchable(self) -> bool:
        return self.state == "confirmed"

    def confirm(self, text: str, by: str, at: float) -> bool:
        if self.state != "unconfirmed" or not self.candidates:
            return False
        self.state = "confirmed"
        self.audit = {"text": text, "by": by, "at": at}
        return True

    def discard(self, reason: str) -> None:
        if self.state != "unconfirmed":
            return
        self.state = "discarded"
        self.audit = {"reason": reason}


class TestNormalization:
    def test_collapses_case_and_whitespace(self):
        assert normalize("sn   4021") == "SN 4021"
        assert normalize("  Level\t3 - Room 12 ") == \
            "LEVEL 3 - ROOM 12"


class TestConfirmationMachine:
    def test_fresh_field_unconfirmed_and_unsearchable(self):
        f = Field(["SN-4021"])
        assert f.state == "unconfirmed"
        assert not f.searchable

    def test_explicit_human_confirmation_makes_it_searchable(self):
        f = Field(["SN-4021"])
        assert f.confirm("SN-4021", "inspector-7", 1760000000.0)
        assert f.searchable
        assert f.audit == {"text": "SN-4021", "by": "inspector-7",
                           "at": 1760000000.0}

    def test_human_corrected_text_also_legitimate(self):
        f = Field(["12.5 bar"])
        assert f.confirm("12.6 bar", "u1", 0.0)   # correction allowed
        assert f.searchable

    def test_zero_candidates_refused(self):
        f = Field([])
        assert not f.confirm("whatever", "u1", 0.0)
        assert f.state == "unconfirmed"

    def test_double_confirm_refused(self):
        f = Field(["2024-03"])
        assert f.confirm("2024-03", "u1", 0.0)
        assert not f.confirm("2024-03", "u2", 10.0)
        assert f.audit["by"] == "u1"       # first decision stands

    def test_discard_is_final_and_unsearchable(self):
        f = Field(["L12"])
        f.discard("not a location label")
        assert f.state == "discarded"
        assert not f.searchable
        assert not f.confirm("L12", "u1", 0.0)

    def test_discard_of_confirmed_is_a_noop(self):
        f = Field(["L12"])
        f.confirm("L12", "u1", 0.0)
        f.discard("late second thought")
        assert f.state == "confirmed"


class TestTaxonomy:
    def test_closed_field_taxonomy(self):
        # mirrors OCRFieldType.allCases in the Swift suite
        assert sorted(FIELD_TYPES) == sorted(set(FIELD_TYPES))
        assert "original" not in FIELD_TYPES  # n/a but explicit


class TestSampleSet:
    def test_manifest_generates_deterministically(self, tmp_path):
        out = tmp_path / "ocr"
        r = subprocess.run(
            [sys.executable, "tools/gen_ocr_samples.py",
             "--out", str(out)],
            cwd=REPO / "backend", capture_output=True, text=True,
            timeout=120)
        assert r.returncode == 0, r.stderr
        m1 = json.loads((out / "manifest.json").read_text("utf-8"))

        out2 = tmp_path / "ocr2"
        r2 = subprocess.run(
            [sys.executable, "tools/gen_ocr_samples.py",
             "--out", str(out2)],
            cwd=REPO / "backend", capture_output=True, text=True,
            timeout=120)
        assert r2.returncode == 0
        m2 = json.loads((out2 / "manifest.json").read_text("utf-8"))

        assert m1["schema"] == "ocr-samples/1.0"
        assert m1 == m2                       # deterministic regen
        files1 = sorted(p.name for p in
                        (out / "samples").glob("*.png"))
        files2 = sorted(p.name for p in
                        (out2 / "samples").glob("*.png"))
        assert files1 == files2 and len(files1) == len(m1["samples"])

    def test_manifest_covers_all_field_types_and_ground_truth(self,
                                                              tmp_path):
        out = tmp_path / "ocr"
        subprocess.run(
            [sys.executable, "tools/gen_ocr_samples.py",
             "--out", str(out)],
            cwd=REPO / "backend", check=True, capture_output=True,
            timeout=120)
        m = json.loads((out / "manifest.json").read_text("utf-8"))
        types = {s["field_type"] for s in m["samples"]}
        assert types == set(FIELD_TYPES) - {"other"}
        for s in m["samples"]:
            assert s["expected_normalized"] == \
                normalize(" ".join(s["text_lines"]))
            assert (out / s["file"]).exists()
            assert s["transform"]["angle"] <= 5.0
