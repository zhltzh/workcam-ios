"""TM08: provenance model tests - detectability ledger honesty."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"
                        / "provenance"))

import pytest  # noqa: E402

from source_model import (  # noqa: E402
    Detectability, SourceClass, check_exact_duplicate,
    check_exif_consistency, check_perceptual_similarity,
    check_recapture_heuristics, classify_submission)


def test_exact_duplicate_is_detectable():
    r = check_exact_duplicate("ab" * 32, {"ab" * 32})
    assert r.source_class == SourceClass.DUPLICATE_SUBMISSION
    assert any(s.grade == Detectability.DETECTABLE
               and s.name == "exact_duplicate" for s in r.signals)


def test_novel_bytes_reported_without_praise():
    r = check_exact_duplicate("cd" * 32, {"ab" * 32})
    assert r.source_class is None
    assert any(s.name == "novel_bytes" for s in r.signals)


def test_screenshot_tag_is_detectable():
    r = check_exif_consistency({"Software": "MacOS Screenshot Tool"})
    assert r.source_class == SourceClass.SYNTHETIC_INPUT
    assert any(s.grade == Detectability.DETECTABLE
               and s.name == "screenshot_software_tag" for s in r.signals)


def test_editor_tag_flags_import():
    r = check_exif_consistency({"Software": "Adobe Photoshop 2026",
                                "Make": "Apple"})
    assert r.source_class == SourceClass.APP_IMPORT
    assert any(s.name == "editor_software_tag" for s in r.signals)


def test_clean_exif_proves_nothing():
    r = check_exif_consistency({"Make": "Apple", "Model": "iPhone 16 Pro",
                                "Software": "WorkCam 1.0"})
    assert any(s.name == "exif_consistent" and "PROVES NOTHING"
               in s.reason for s in r.signals)


def test_no_exif_flagged():
    r = check_exif_consistency({})
    assert any(s.name == "no_exif" for s in r.signals)


def test_perceptual_candidate_is_heuristic_not_verdict():
    phash = "ff00" * 16
    known = {"photo-1": "ff00" * 15 + "fe00"}       # distance 2
    r = check_perceptual_similarity(phash, known)
    assert r.source_class == SourceClass.RECAPTURE_SUSPECT
    sig = next(s for s in r.signals
               if s.name == "perceptual_near_duplicate")
    assert sig.grade == Detectability.HEURISTIC
    assert "human review" in sig.reason


def test_perceptual_clean_scan():
    r = check_perceptual_similarity("00" * 32, {"x": "ff" * 32})
    assert r.source_class is None
    assert any(s.name == "no_near_duplicate" for s in r.signals)


def test_recapture_heuristic_flags_and_undetectable_boundary():
    r = check_recapture_heuristics({"moire_score": 0.8,
                                    "skew_estimated_deg": 12.0,
                                    "signals_available": True})
    assert r.source_class == SourceClass.RECAPTURE_SUSPECT
    # the UNDETECTABLE boundary is stated even when flags fire
    r2 = check_recapture_heuristics({"signals_available": False})
    assert any(s.grade == Detectability.UNDETECTABLE
               and s.name == "recapture_undetectable"
               for s in r2.signals)


def test_channel_classification_is_data_not_proof():
    r = classify_submission({"channel": "camera"})
    assert r.source_class == SourceClass.CAMERA_CAPTURE
    sig = next(s for s in r.signals if s.name == "declared_channel")
    assert "not proof" in sig.reason
    r2 = classify_submission({"channel": "weird"})
    assert r2.source_class == SourceClass.EXTERNAL_FILE


def test_every_report_carries_the_honest_boundary():
    for r in (check_exact_duplicate("a" * 64, set()),
              check_exif_consistency({}),
              check_recapture_heuristics({})):
        assert "can prove a photo shows a real scene" in \
            r.honest_boundary          # inside the "No ... can" frame
        assert "UNDETECTABLE_OFFLINE" in r.honest_boundary
