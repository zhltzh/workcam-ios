"""TM-04 D04: required-evidence validation matrix - photo, note and
measurement requirements, individually and combined, with the
smuggling vectors closed (NaN/Inf measurements, empty photo lists,
whitespace notes, partial credit).

The Swift twin is ChecklistEvidenceMatrixTests.swift; every case
exists on both sides.
"""

from __future__ import annotations

import math

import pytest

from techlab.business.templates import (
    ChecklistItem,
    ChecklistRun,
    ChecklistSection,
    ChecklistStateError,
    EvidenceRequired,
    ItemStatus,
    TemplateKind,
)


def run_for(reqs):
    run = ChecklistRun(
        template_kind=TemplateKind.construction, project_id="p1")
    run.init_from([
        ChecklistSection(section_id="s", title="S", items=[
            ChecklistItem(item_id="i1", text="t",
                          required_evidence=reqs),
        ])])
    return run


class TestRequiredPhoto:
    def test_pass_without_photo_refused(self):
        r = run_for([EvidenceRequired.photo])
        with pytest.raises(ChecklistStateError) as ei:
            r.set_status("i1", ItemStatus.passed)
        assert "requires a photo" in str(ei.value)
        assert "NOT waivable" in str(ei.value)

    def test_empty_photo_list_is_not_a_photo(self):
        r = run_for([EvidenceRequired.photo])
        with pytest.raises(ChecklistStateError):
            r.set_status("i1", ItemStatus.failed, photo_ids=[])

    def test_photo_provided_passes(self):
        r = run_for([EvidenceRequired.photo])
        r.set_status("i1", ItemStatus.passed, photo_ids=["ph-1"])
        assert r.summary()["complete"]


class TestRequiredNote:
    def test_whitespace_note_is_not_a_note(self):
        r = run_for([EvidenceRequired.note])
        with pytest.raises(ChecklistStateError):
            r.set_status("i1", ItemStatus.passed, note="   ")

    def test_note_provided_passes(self):
        r = run_for([EvidenceRequired.note])
        r.set_status("i1", ItemStatus.failed, note="crack 2mm wide")
        assert r.summary()["complete"]


class TestRequiredMeasurement:
    def test_missing_measurement_refused(self):
        r = run_for([EvidenceRequired.measurement])
        with pytest.raises(ChecklistStateError):
            r.set_status("i1", ItemStatus.passed)

    @pytest.mark.parametrize("bad", [
        float("nan"), float("inf"), float("-inf"),
    ])
    def test_nonfinite_measurement_never_passes_the_gate(self, bad):
        r = run_for([EvidenceRequired.measurement])
        with pytest.raises(ChecklistStateError) as ei:
            r.set_status("i1", ItemStatus.passed,
                         measurement=(bad, "mm"))
        assert "finite" in str(ei.value)

    def test_measurement_without_unit_refused(self):
        r = run_for([EvidenceRequired.measurement])
        with pytest.raises(ChecklistStateError) as ei:
            r.set_status("i1", ItemStatus.passed, measurement=(1.5, ""))
        assert "unit" in str(ei.value)

    def test_valid_measurement_passes(self):
        r = run_for([EvidenceRequired.measurement])
        r.set_status("i1", ItemStatus.passed, measurement=(2.4, "m"))
        assert r.summary()["complete"]


class TestCombined:
    def test_partial_evidence_is_not_enough(self):
        r = run_for([EvidenceRequired.photo, EvidenceRequired.note])
        with pytest.raises(ChecklistStateError):
            r.set_status("i1", ItemStatus.passed, note="only note")
        with pytest.raises(ChecklistStateError):
            r.set_status("i1", ItemStatus.passed,
                         photo_ids=["ph-1"])

    def test_all_three_required_all_three_checked(self):
        r = run_for([EvidenceRequired.photo, EvidenceRequired.note,
                     EvidenceRequired.measurement])
        with pytest.raises(ChecklistStateError):
            r.set_status("i1", ItemStatus.passed, photo_ids=["a"],
                         note="n")
        r.set_status("i1", ItemStatus.passed, photo_ids=["a"],
                     note="n", measurement=(3.0, "m"))
        assert r.summary()["complete"]

    def test_requirements_do_not_apply_to_na(self):
        r = run_for([EvidenceRequired.photo])
        r.set_status("i1", ItemStatus.not_applicable)
        assert r.summary()["complete"]

    def test_requirements_do_not_apply_to_not_inspected(self):
        # not_inspected is an EXPLICIT skip decision (TM-03 B02
        # semantics): the checklist may close with it - the label
        # keeps the record honest, the summary does not lie
        r = run_for([EvidenceRequired.note])
        r.set_status("i1", ItemStatus.not_inspected)
        assert r.summary()["complete"]
