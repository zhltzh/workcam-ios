"""T02 - location metadata & quality grading tests."""

from __future__ import annotations

import pytest

from techlab.location_meta import (
    LocationMetadata,
    LocationQuality,
    LocationSource,
    grade_offline_batch,
)


def test_gps_within_5m_grades_a():
    loc = LocationMetadata(
        source=LocationSource.GPS, latitude=39.9, longitude=116.4,
        horizontal_accuracy=4.2,
    )
    assert loc.grade() == LocationQuality.A
    assert loc.is_evidence_grade() is True


def test_network_offline_capped_at_c():
    """Offline Wi-Fi/cell fixes are routinely 30-100 m off: never above C."""
    loc = LocationMetadata(
        source=LocationSource.NETWORK, latitude=39.9, longitude=116.4,
        horizontal_accuracy=3.0,   # even if device *claims* 3 m
    )
    assert loc.grade() == LocationQuality.C


def test_manual_never_evidence_and_label_is_honest():
    loc = LocationMetadata(
        source=LocationSource.MANUAL, latitude=39.9, longitude=116.4,
        note="B2 basement pillar",
    )
    assert loc.is_evidence_grade() is False
    assert loc.grade() == LocationQuality.D
    label = loc.trust_label()
    assert "not verified" in label
    assert "verified" not in label.replace("not verified", "")


def test_simulated_flag_blocks_evidence():
    loc = LocationMetadata(
        source=LocationSource.GPS, latitude=39.9, longitude=116.4,
        horizontal_accuracy=1.0, is_simulated=True,
    )
    assert loc.is_evidence_grade() is False
    assert "SIMULATED" in loc.trust_label()


def test_missing_fix_counts_as_missing_not_guessed():
    records = [
        LocationMetadata(source=LocationSource.NONE),
        LocationMetadata(source=LocationSource.GPS, latitude=1.0, longitude=2.0,
                         horizontal_accuracy=10.0),
        LocationMetadata(source=LocationSource.MANUAL, latitude=1.0, longitude=2.0),
    ]
    s = grade_offline_batch(records)
    assert s["total"] == 3
    assert s["missing_fix"] == 1
    assert s["manual"] == 1
    assert s["evidence_gradeable"] == 1
    assert s["verdict"] == "usable"
    # GPS @10 m accuracy grades B (<=15 m threshold); the no-fix record
    # is counted as missing, never graded
    assert s["grades"]["B"] == 1
    assert s["grades"]["D"] == 2   # manual + no-fix


def test_no_data_session():
    assert grade_offline_batch([])["verdict"] == "no_data"


def test_nan_rejected():
    with pytest.raises(Exception):
        LocationMetadata(
            source=LocationSource.GPS,
            latitude=float("nan"), longitude=0.0, horizontal_accuracy=1.0,
        )


def test_note_newline_rejected():
    with pytest.raises(Exception):
        LocationMetadata(source=LocationSource.MANUAL, note="line1\nline2")
