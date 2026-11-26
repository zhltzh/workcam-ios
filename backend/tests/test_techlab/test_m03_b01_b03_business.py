"""M03-B01/B02/B03 tests - templates, checklist honesty rules, forms."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from techlab.business.templates import (  # noqa: E402
    ChecklistRun, ChecklistStateError, ChecklistSection,
    EvidenceRequired, FormDefinition, FormField, FormFieldType,
    FormValidationError, ItemStatus, ProjectTemplate, TemplateKind,
    _mk, get_template, register_template)


class TestB01Templates:
    def test_all_eight_registered(self):
        for kind in TemplateKind:
            t = get_template(kind)
            assert t.display_name
            assert t.checklist_sections

    def test_every_template_is_versioned(self):
        for kind in TemplateKind:
            assert get_template(kind).version == \
                "project-templates/1.0"

    def test_template_extensible(self):
        from techlab.business import templates as mod
        saved = mod._TEMPLATES[TemplateKind.construction]
        custom = ProjectTemplate(
            kind=TemplateKind.construction,
            display_name="My Custom Site",
            description="user-owned variant",
            photo_categories=["a"],
            issue_categories=["b"],
            checklist_sections=[ChecklistSection("s", "S", [
                _mk("x", "custom item")])],
            watermark_fields=["project_name"],
            report_structure=["cover"])
        register_template(custom)
        assert get_template(TemplateKind.construction).display_name \
            == "My Custom Site"
        # restore: later tests rely on the built-in construction
        mod._TEMPLATES[TemplateKind.construction] = saved

    def test_watermark_fields_snake_case(self):
        with pytest.raises(ValueError):
            ProjectTemplate(
                kind=TemplateKind.hvac,
                display_name="Bad", description="d",
                photo_categories=[], issue_categories=[],
                checklist_sections=[ChecklistSection("s", "S", [
                    _mk("i", "x")])],
                watermark_fields=["Bad-Field"],
                report_structure=[])


class TestB02Checklist:
    def _run(self) -> tuple[ChecklistRun, list]:
        t = get_template(TemplateKind.construction)
        run = ChecklistRun(
            template_kind=t.kind, project_id="p-1")
        run.init_from(t.checklist_sections)
        return run, t.checklist_sections

    def test_unchecked_never_auto_passes(self):
        run, _ = self._run()
        s = run.summary()
        assert not s["complete"]
        assert s["counts"]["pending"] > 0
        assert "pending" not in (s["counts"]["passed"],)

    def test_evidence_required_blocks_pass(self):
        run, _ = self._run()
        # site_access requires a photo
        with pytest.raises(ChecklistStateError, match="photo"):
            run.set_status("site_access", ItemStatus.passed)
        # providing the photo works
        run.set_status("site_access", ItemStatus.passed,
                       photo_ids=["ph-1"])
        assert run.states["site_access"].status == ItemStatus.passed

    def test_measurement_required_blocks(self):
        run, _ = self._run()
        with pytest.raises(ChecklistStateError, match="measurement"):
            run.set_status("rebar_layout", ItemStatus.passed,
                           photo_ids=["ph-1"])
        run.set_status("rebar_layout", ItemStatus.passed,
                       photo_ids=["ph-1"],
                       measurement=(12.5, "mm"))
        assert run.states["rebar_layout"].measurement_value == 12.5

    def test_completion_report_honest(self):
        run, sections = self._run()
        # pass all items
        for section in sections:
            for item in section.items:
                evidence = {}
                if EvidenceRequired.photo in item.required_evidence:
                    evidence["photo_ids"] = [f"ph-{item.item_id}"]
                if EvidenceRequired.note in item.required_evidence:
                    evidence["note"] = "checked"
                if EvidenceRequired.measurement \
                        in item.required_evidence:
                    evidence["measurement"] = (1.0, "m")
                run.set_status(item.item_id, ItemStatus.passed,
                               **evidence)
        s = run.summary()
        assert s["complete"]
        assert "COMPLETE" in run.completion_report()
        assert "passed" in run.completion_report()

    def test_not_applicable_allowed(self):
        run, _ = self._run()
        run.set_status("site_access", ItemStatus.not_applicable)
        s = run.summary()
        assert s["counts"]["not_applicable"] == 1

    def test_unknown_item_refused(self):
        run, _ = self._run()
        with pytest.raises(ChecklistStateError, match="unknown"):
            run.set_status("no-such-item", ItemStatus.passed)


class TestB03Forms:
    FORM = FormDefinition(
        form_id="site_visit",
        title="Site Visit Form",
        fields=[
            FormField("weather", "Weather", FormFieldType.single_choice,
                      choices=["clear", "rain", "snow"]),
            FormField("rain_note", "Rain precautions taken",
                      FormFieldType.text, required=True,
                      visible_when={"weather": "rain"}),
            FormField("temperature", "Temperature",
                      FormFieldType.number, required=True,
                      min_value=-50, max_value=60),
            FormField("defects_found", "Defects found?",
                      FormFieldType.yes_no, required=True),
            FormField("defect_areas", "Affected areas",
                      FormFieldType.multi_choice, required=False,
                      choices=["roof", "walls", "foundation"],
                      visible_when={"defects_found": "yes"}),
            FormField("inspector", "Inspector name",
                      FormFieldType.text, required=True,
                      min_length=2, max_length=100),
        ])

    def test_valid_submission(self):
        errors = self.FORM.validate({
            "weather": "clear",
            "temperature": "22",
            "defects_found": "no",
            "inspector": "Jane Doe"})
        assert errors == []

    def test_missing_required(self):
        errors = self.FORM.validate({})
        assert any(e.field_id == "inspector"
                   for e in errors)
        assert any(e.field_id == "temperature" for e in errors)

    def test_conditional_field_ignored_when_hidden(self):
        # weather=clear hides rain_note: a MISSING rain_note is NOT
        # an error
        errors = self.FORM.validate({
            "weather": "clear",
            "temperature": "20",
            "defects_found": "no",
            "inspector": "Jane"})
        assert not any(e.field_id == "rain_note" for e in errors)

    def test_conditional_field_required_when_visible(self):
        errors = self.FORM.validate({
            "weather": "rain",
            "temperature": "15",
            "defects_found": "no",
            "inspector": "Jane"})
        assert any(e.field_id == "rain_note"
                   for e in errors), \
            "rain_note is required when weather=rain"

    def test_number_range(self):
        errors = self.FORM.validate({
            "weather": "clear",
            "temperature": "95",
            "defects_found": "no",
            "inspector": "Jane"})
        assert any(e.field_id == "temperature" for e in errors)

    def test_choice_out_of_vocabulary(self):
        errors = self.FORM.validate({
            "weather": "tornado",
            "temperature": "20",
            "defects_found": "no",
            "inspector": "Jane"})
        assert any(e.field_id == "weather" for e in errors)

    def test_multi_choice_valid_and_invalid(self):
        base = {"weather": "clear", "temperature": "20",
                "defects_found": "yes", "inspector": "Jane"}
        assert self.FORM.validate({**base,
                                  "defect_areas": ["roof"]}) == []
        errs = self.FORM.validate({**base,
                                   "defect_areas": ["lunar_module"]})
        assert any(e.field_id == "defect_areas" for e in errs)

    def test_text_length_bounds(self):
        base = {"weather": "clear", "temperature": "20",
                "defects_found": "no"}
        errs = self.FORM.validate({**base, "inspector": "J"})
        assert any(e.field_id == "inspector" for e in errs)
        assert self.FORM.validate({**base, "inspector": "Jo"}) == []

    def test_field_id_snake_case(self):
        with pytest.raises(ValueError):
            FormField("Bad-ID", "label", FormFieldType.text)
        with pytest.raises(ValueError):
            FormField("choice-no-options", "label",
                      FormFieldType.single_choice)
