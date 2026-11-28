"""M03-B01/B02/B03: project templates, checklist engine, dynamic forms.

B01: 8 English engineering templates (versioned, extensible, never
     claiming regulatory status).
B02: checklist engine - sections, items, statuses, required evidence;
     unchecked is NEVER auto-passed.
B03: dynamic form definitions - typed fields, conditional visibility,
     validation; JSON Schema + Swift Codable contract.

Pure data + pure logic: no Apple frameworks, Swift-portable (B12).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

# ============================================================ B01: templates

TEMPLATE_VERSION = "project-templates/1.0"


class TemplateKind(str, Enum):
    construction = "construction"
    site_inspection = "site_inspection"
    property_inspection = "property_inspection"
    facility_maintenance = "facility_maintenance"
    roofing = "roofing"
    electrical = "electrical"
    plumbing = "plumbing"
    hvac = "hvac"


@dataclass(frozen=True)
class ProjectTemplate:
    kind: TemplateKind
    display_name: str
    description: str
    photo_categories: list[str]
    issue_categories: list[str]
    checklist_sections: list["ChecklistSection"]
    watermark_fields: list[str]
    report_structure: list[str]           # ordered report sections
    version: str = TEMPLATE_VERSION

    def __post_init__(self):
        if not self.display_name or not self.checklist_sections:
            raise ValueError("template needs a name and sections")
        for f in self.watermark_fields:
            if not re.match(r"^[a-z_]+$", f):
                raise ValueError(
                    f"watermark field {f!r} must be a snake_case key")


# ============================================================ B02: checklist

class ItemStatus(str, Enum):
    pending = "pending"           # NOT inspected yet
    passed = "passed"
    failed = "failed"
    not_applicable = "not_applicable"
    not_inspected = "not_inspected"     # explicitly skipped


class EvidenceRequired(str, Enum):
    photo = "photo"
    note = "note"
    measurement = "measurement"


@dataclass(frozen=True)
class ChecklistItem:
    item_id: str
    text: str
    required_evidence: list[EvidenceRequired] = field(default_factory=list)
    guidance: str = ""

    def __post_init__(self):
        if not self.text:
            raise ValueError("checklist item needs text")


@dataclass(frozen=True)
class ChecklistSection:
    section_id: str
    title: str
    items: list[ChecklistItem]


class ChecklistStateError(ValueError):
    """Named refusal: the engine NEVER auto-passes an unchecked item
    and NEVER lets a completion claim stand on pending items."""


@dataclass
class ChecklistItemState:
    item: ChecklistItem
    status: ItemStatus = ItemStatus.pending
    photo_ids: list[str] = field(default_factory=list)
    note: str = ""
    measurement_value: float | None = None
    measurement_unit: str = ""


@dataclass
class ChecklistRun:
    """One execution of a template's checklist against a project."""
    template_kind: TemplateKind
    project_id: str
    states: dict[str, ChecklistItemState] = field(default_factory=dict)

    def __post_init__(self):
        pass

    def init_from(self, sections: list[ChecklistSection]) -> None:
        for section in sections:
            for item in section.items:
                self.states[item.item_id] = ChecklistItemState(
                    item=item)

    def set_status(self, item_id: str, status: ItemStatus,
                   photo_ids: list[str] | None = None,
                   note: str = "",
                   measurement: tuple[float, str] | None = None
                   ) -> None:
        st = self.states.get(item_id)
        if st is None:
            raise ChecklistStateError(f"unknown item {item_id!r}")
        req = st.item.required_evidence
        if status == ItemStatus.passed or status == ItemStatus.failed:
            if EvidenceRequired.photo in req:
                if not photo_ids:
                    raise ChecklistStateError(
                        f"item {item_id!r} requires a photo to pass "
                        f"or fail - evidence requirements are NOT "
                        f"waivable")
                st.photo_ids = photo_ids
            if EvidenceRequired.note in req and not note.strip():
                raise ChecklistStateError(
                    f"item {item_id!r} requires a note")
            if EvidenceRequired.measurement in req and not measurement:
                raise ChecklistStateError(
                    f"item {item_id!r} requires a measurement")
        if measurement:
            # D04: NaN/Inf never pass the required-evidence gate
            import math
            if not math.isfinite(measurement[0]):
                raise ChecklistStateError(
                    f"item {item_id!r} measurement is not a "
                    f"finite number")
            if not measurement[1]:
                raise ChecklistStateError(
                    f"item {item_id!r} measurement needs a unit")
            st.measurement_value, st.measurement_unit = measurement
        st.note = note
        if photo_ids:
            st.photo_ids = photo_ids
        st.status = status

    def summary(self) -> dict:
        """Honest summary: counts + the BLOCKERS list. A checklist is
        'complete' ONLY when nothing is pending."""
        counts = {s.value: 0 for s in ItemStatus}
        blockers: list[str] = []
        for st in self.states.values():
            counts[st.status.value] += 1
            if st.status == ItemStatus.pending:
                blockers.append(st.item.item_id)
        return {
            "template": self.template_kind.value,
            "project_id": self.project_id,
            "counts": counts,
            "complete": not blockers,
            "blockers": blockers,       # named, never hidden
        }

    def completion_report(self) -> str:
        s = self.summary()
        if not s["complete"]:
            return (f"INCOMPLETE: {len(s['blockers'])} item(s) still "
                    f"pending: {', '.join(s['blockers'][:5])}"
                    + ("..." if len(s["blockers"]) > 5 else ""))
        return (f"COMPLETE: {s['counts']['passed']} passed, "
                f"{s['counts']['failed']} failed, "
                f"{s['counts']['not_applicable']} N/A")


# ============================================================ B03: forms

class FormFieldType(str, Enum):
    text = "text"
    number = "number"
    date = "date"
    single_choice = "single_choice"
    multi_choice = "multi_choice"
    yes_no = "yes_no"
    photo_ref = "photo_ref"
    measurement_ref = "measurement_ref"


@dataclass(frozen=True)
class FormField:
    field_id: str
    label: str
    field_type: FormFieldType
    required: bool = True
    choices: list[str] = field(default_factory=list)  # for choice types
    min_value: float | None = None     # for number
    max_value: float | None = None
    min_length: int | None = None     # for text
    max_length: int | None = None
    # conditional visibility: this field renders only when ALL the
    # predicate fields hold their expected values
    visible_when: dict[str, str] = field(default_factory=dict)
    default: str | None = None

    def __post_init__(self):
        if not re.match(r"^[a-z_][a-z0-9_]*$", self.field_id):
            raise ValueError(
                f"field_id {self.field_id!r} must be a snake_case key")
        if self.field_type in (FormFieldType.single_choice,
                               FormFieldType.multi_choice) \
                and not self.choices:
            raise ValueError(
                f"choice field {self.field_id!r} needs choices")


class FormValidationError(ValueError):
    def __init__(self, field_id: str, message: str):
        super().__init__(f"{field_id}: {message}")
        self.field_id = field_id
        self.message = message


@dataclass(frozen=True)
class FormDefinition:
    form_id: str
    title: str
    fields: list[FormField]
    version: str = "form-def/1.0"

    def __post_init__(self):
        ids = [f.field_id for f in self.fields]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate field ids in form")

    def validate(self, answers: dict) -> list[FormValidationError]:
        """Validate a filled form. Returns [] when valid. Missing
        REQUIRED fields are errors; missing OPTIONAL fields are fine.
        Values for fields whose visibility condition is NOT met are
        IGNORED (not errors)."""
        errors: list[FormValidationError] = []

        def is_visible(f: FormField) -> bool:
            for other_id, expected in f.visible_when.items():
                if str(answers.get(other_id, "")) != expected:
                    return False
            return True

        for f in self.fields:
            if not is_visible(f):
                continue
            v = answers.get(f.field_id)
            if v is None or v == "":
                if f.required:
                    errors.append(FormValidationError(
                        f.field_id, "required field is empty"))
                continue
            if f.field_type == FormFieldType.number:
                try:
                    num = float(v)
                except (TypeError, ValueError):
                    errors.append(FormValidationError(
                        f.field_id, f"not a number: {v!r}"))
                    continue
                if f.min_value is not None and num < f.min_value:
                    errors.append(FormValidationError(
                        f.field_id, f"{num} < min {f.min_value}"))
                if f.max_value is not None and num > f.max_value:
                    errors.append(FormValidationError(
                        f.field_id, f"{num} > max {f.max_value}"))
            elif f.field_type == FormFieldType.yes_no:
                if v not in ("yes", "no", True, False):
                    errors.append(FormValidationError(
                        f.field_id, f"must be yes/no, got {v!r}"))
            elif f.field_type == FormFieldType.single_choice:
                if v not in f.choices:
                    errors.append(FormValidationError(
                        f.field_id, f"{v!r} not in {f.choices}"))
            elif f.field_type == FormFieldType.multi_choice:
                if not isinstance(v, list) \
                        or any(x not in f.choices for x in v):
                    errors.append(FormValidationError(
                        f.field_id,
                        f"multi-choice values must be from "
                        f"{f.choices}"))
            elif f.field_type == FormFieldType.text:
                s = str(v)
                if f.min_length and len(s) < f.min_length:
                    errors.append(FormValidationError(
                        f.field_id, f"shorter than {f.min_length}"))
                if f.max_length and len(s) > f.max_length:
                    errors.append(FormValidationError(
                        f.field_id, f"longer than {f.max_length}"))
        return errors


# ============================================================ B01 registry

def _mk(item_id: str, text: str,
        evidence: list[EvidenceRequired] | None = None,
        guidance: str = "") -> ChecklistItem:
    return ChecklistItem(item_id=item_id, text=text,
                         required_evidence=evidence or [],
                         guidance=guidance)


_TEMPLATES: dict[TemplateKind, ProjectTemplate] = {}


def register_template(t: ProjectTemplate) -> None:
    _TEMPLATES[t.kind] = t


def get_template(kind: TemplateKind) -> ProjectTemplate:
    if kind not in _TEMPLATES:
        raise KeyError(f"template {kind!r} not registered")
    return _TEMPLATES[kind]


def _build_default_templates() -> None:
    """The 8 built-in templates. IMPORTANT: these are ENGINEERING
    CONVENIENCE DEFAULTS, not any country's mandatory regulation -
    the docstrings say so and users can replace every one."""

    construction = ProjectTemplate(
        kind=TemplateKind.construction,
        display_name="Construction",
        description="General construction progress and QC photos.",
        photo_categories=["site_overview", "excavation", "foundation",
                          "framing", "concrete", "steel", "finishing"],
        issue_categories=["material", "workmanship", "safety",
                          "schedule", "design_change"],
        checklist_sections=[
            ChecklistSection("site", "Site Conditions", [
                _mk("site_access", "Site access clear and safe",
                    [EvidenceRequired.photo]),
                _mk("site_signage", "Safety signage posted",
                    [EvidenceRequired.photo]),
                _mk("site_erosion", "Erosion controls in place",
                    [EvidenceRequired.photo,
                     EvidenceRequired.note]),
            ]),
            ChecklistSection("work", "Work in Progress", [
                _mk("rebar_layout", "Rebar layout matches drawings",
                    [EvidenceRequired.photo,
                     EvidenceRequired.measurement]),
                _mk("formwork", "Formwork plumb and aligned",
                    [EvidenceRequired.photo,
                     EvidenceRequired.measurement]),
                _mk("concrete_pour", "Concrete pour conditions OK",
                    [EvidenceRequired.photo,
                     EvidenceRequired.note]),
            ]),
        ],
        watermark_fields=["project_name", "site_address",
                          "captured_at", "photo_category"],
        report_structure=["cover", "site_summary",
                          "progress_photos", "issues",
                          "checklist_results", "sign_off"])

    site_insp = ProjectTemplate(
        kind=TemplateKind.site_inspection,
        display_name="Site Inspection",
        description="General site inspection visit.",
        photo_categories=["overview", "defects", "access",
                          "utilities", "drainage"],
        issue_categories=["safety", "environmental", "access",
                          "structural", "compliance"],
        checklist_sections=[
            ChecklistSection("exterior", "Exterior", [
                _mk("ext_grading", "Grading directs water away",
                    [EvidenceRequired.photo]),
                _mk("ext_fencing", "Fencing intact"),
                _mk("ext_drainage", "Drainage unobstructed",
                    [EvidenceRequired.photo]),
            ]),
            ChecklistSection("utilities", "Utilities", [
                _mk("util_water", "Water service condition",
                    [EvidenceRequired.note]),
                _mk("util_electric", "Electrical panels condition",
                    [EvidenceRequired.photo]),
                _mk("util_gas", "Gas meter / lines condition",
                    [EvidenceRequired.photo]),
            ]),
        ],
        watermark_fields=["project_name", "inspector", "captured_at"],
        report_structure=["cover", "inspection_summary",
                          "findings", "issues", "sign_off"])

    prop_insp = ProjectTemplate(
        kind=TemplateKind.property_inspection,
        display_name="Property Inspection",
        description="Pre-purchase / pre-lease property condition.",
        photo_categories=["front", "rear", "kitchen", "bathrooms",
                          "roof", "basement", "systems"],
        issue_categories=["structural", "moisture", "hvac",
                          "plumbing", "electrical", "cosmetic"],
        checklist_sections=[
            ChecklistSection("structure", "Structure", [
                _mk("foundation", "Foundation condition",
                    [EvidenceRequired.photo,
                     EvidenceRequired.note]),
                _mk("walls", "Wall cracks or movement",
                    [EvidenceRequired.photo]),
                _mk("roof_condition", "Roof condition",
                    [EvidenceRequired.photo]),
            ]),
            ChecklistSection("interior", "Interior", [
                _mk("flooring", "Flooring condition",
                    [EvidenceRequired.photo]),
                _mk("moisture", "Signs of moisture / mold",
                    [EvidenceRequired.photo,
                     EvidenceRequired.note]),
            ]),
        ],
        watermark_fields=["property_address", "unit", "captured_at"],
        report_structure=["cover", "property_summary",
                          "condition_photos", "defects",
                          "checklist_results", "sign_off"])

    facility = ProjectTemplate(
        kind=TemplateKind.facility_maintenance,
        display_name="Facility Maintenance",
        description="Recurring facility maintenance rounds.",
        photo_categories=["hvac_units", "plumbing", "electrical",
                          "safety_equipment", "exterior"],
        issue_categories=["hvac", "plumbing", "electrical",
                          "safety", "custodial"],
        checklist_sections=[
            ChecklistSection("hvac", "HVAC", [
                _mk("hvac_filters", "Filters checked / replaced",
                    [EvidenceRequired.note]),
                _mk("hvac_coils", "Coil condition",
                    [EvidenceRequired.photo]),
            ]),
            ChecklistSection("safety", "Safety Equipment", [
                _mk("extinguishers", "Extinguishers in place + "
                    "inspected", [EvidenceRequired.photo]),
                _mk("exits", "Emergency exits clear",
                    [EvidenceRequired.photo]),
            ]),
        ],
        watermark_fields=["facility_name", "round_date",
                          "technician", "captured_at"],
        report_structure=["cover", "maintenance_log",
                          "findings", "issues", "sign_off"])

    roofing = ProjectTemplate(
        kind=TemplateKind.roofing,
        display_name="Roofing",
        description="Roof inspection and repair documentation.",
        photo_categories=["overview", "flashing", "drainage",
                          "membrane", "penetrations", "gutters"],
        issue_categories=["leak", "flashing", "drainage",
                          "membrane", "structural"],
        checklist_sections=[
            ChecklistSection("surface", "Roof Surface", [
                _mk("membrane_condition", "Membrane condition",
                    [EvidenceRequired.photo,
                     EvidenceRequired.note]),
                _mk("ponding", "Ponding water areas",
                    [EvidenceRequired.photo,
                     EvidenceRequired.measurement]),
            ]),
            ChecklistSection("penetrations", "Penetrations", [
                _mk("flashing_vents", "Vent flashing sealed",
                    [EvidenceRequired.photo]),
                _mk("flashing_edges", "Edge flashing secure",
                    [EvidenceRequired.photo]),
            ]),
        ],
        watermark_fields=["project_name", "roof_area",
                          "captured_at"],
        report_structure=["cover", "roof_overview",
                          "defect_photos", "issues",
                          "repair_plan", "sign_off"])

    electrical = ProjectTemplate(
        kind=TemplateKind.electrical,
        display_name="Electrical",
        description="Electrical system inspection and work photos.",
        photo_categories=["panels", "wiring", "fixtures",
                          "grounding", "conduit"],
        issue_categories=["wiring", "panel", "grounding",
                          "code", "fixture"],
        checklist_sections=[
            ChecklistSection("panels", "Panels", [
                _mk("panel_label", "Panel labeling complete",
                    [EvidenceRequired.photo]),
                _mk("panel_clearance", "Working clearance maintained",
                    [EvidenceRequired.photo,
                     EvidenceRequired.measurement]),
            ]),
            ChecklistSection("wiring", "Wiring", [
                _mk("wiring_condition", "Visible wiring condition",
                    [EvidenceRequired.photo]),
            ]),
        ],
        watermark_fields=["project_name", "voltage_level",
                          "captured_at"],
        report_structure=["cover", "scope", "findings",
                          "issues", "sign_off"])

    plumbing = ProjectTemplate(
        kind=TemplateKind.plumbing,
        display_name="Plumbing",
        description="Plumbing inspection and repair photos.",
        photo_categories=["supply_lines", "drain_lines",
                          "fixtures", "water_heater", "valves"],
        issue_categories=["leak", "drainage", "supply",
                          "fixture", "water_heater"],
        checklist_sections=[
            ChecklistSection("supply", "Supply System", [
                _mk("supply_leaks", "No active supply leaks",
                    [EvidenceRequired.photo,
                     EvidenceRequired.note]),
                _mk("supply_pressure", "Pressure within range",
                    [EvidenceRequired.measurement]),
            ]),
            ChecklistSection("drainage", "Drainage", [
                _mk("drain_flow", "Drains flow freely",
                    [EvidenceRequired.note]),
                _mk("drain_vents", "Vents clear",
                    [EvidenceRequired.photo]),
            ]),
        ],
        watermark_fields=["project_name", "system", "captured_at"],
        report_structure=["cover", "scope", "findings",
                          "issues", "sign_off"])

    hvac = ProjectTemplate(
        kind=TemplateKind.hvac,
        display_name="HVAC",
        description="Heating, ventilation and AC documentation.",
        photo_categories=["units", "ductwork", "thermostats",
                          "filters", "condensate"],
        issue_categories=["cooling", "heating", "airflow",
                          "filtration", "refrigerant"],
        checklist_sections=[
            ChecklistSection("units", "Units", [
                _mk("unit_operation", "Operating normally",
                    [EvidenceRequired.note]),
                _mk("refrigerant_lines", "Refrigerant lines clean",
                    [EvidenceRequired.photo]),
                _mk("condensate_drain", "Condensate draining",
                    [EvidenceRequired.photo]),
            ]),
        ],
        watermark_fields=["project_name", "system_type",
                          "captured_at"],
        report_structure=["cover", "scope", "readings",
                          "findings", "issues", "sign_off"])

    for t in (construction, site_insp, prop_insp, facility,
              roofing, electrical, plumbing, hvac):
        register_template(t)


_build_default_templates()
