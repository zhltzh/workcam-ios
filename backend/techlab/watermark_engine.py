"""T10 - Declarative watermark & template engine (tech-lab experiment).

Declarative, versioned watermark templates + a layout renderer.

HONESTY RULES (product principle: no user text may pose as verified info):
- Data-bound elements (datetime / gps / project / field) resolve ONLY from
  a typed RenderingContext. Free text the user types is rendered verbatim
  and can never inherit a "verified" look.
- GPS elements render the T02 trust_label (e.g. "manual: user-entered
  (not verified)"); a manual location can NEVER appear as a verified fix.
- Rendering NEVER touches the original: output is a Derivative registered
  in the DerivativeLedger (ADR-0002).
- The Windows renderer produces a LAYOUT (machine-readable geometry +
  ASCII preview). Pixel rendering is an iOS CoreGraphics responsibility;
  the layout contract below is what both sides must implement.

EXPERIMENTAL - not wired into the production `app` package.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator

from techlab.derivative_model import DerivativeRecipe, OriginalPhoto


class ElementType(str, Enum):
    TEXT = "text"          # literal user text
    DATETIME = "datetime"  # bound: rendering context time
    GPS = "gps"            # bound: T02 LocationMetadata
    PROJECT = "project"    # bound: project name/number
    CHECK_NO = "check_no"  # bound: inspection number
    CUSTOM = "custom"      # bound: W-07 user-defined field (source names it)
    LOGO = "logo"          # image asset reference
    QRCODE = "qrcode"      # bound: encodes verified bundle URL/payload


class Anchor(str, Enum):
    TOP_LEFT = "top_left"
    TOP_RIGHT = "top_right"
    BOTTOM_LEFT = "bottom_left"
    BOTTOM_RIGHT = "bottom_right"


VALID_HEX = set("0123456789abcdefABCDEF")


class Element(BaseModel):
    type: ElementType
    anchor: Anchor
    x: int = Field(default=0, ge=0)
    y: int = Field(default=0, ge=0)
    font_size: int = Field(default=18, ge=6, le=200)
    color: str = "#FFFFFF"
    opacity: float = Field(default=1.0, ge=0.0, le=1.0)
    text: str | None = None          # only for ElementType.TEXT
    source: str | None = None        # bound field name for PROJECT/CUSTOM
    asset: str | None = None         # asset ref for LOGO
    payload: str | None = None       # static payload for QRCODE
    # W-07 conditional visibility: "<bound>==<value>" or "<bound>!=<value>".
    # Bound names: custom:<name>, project:name, check_no, gps_source,
    # gps_grade, has_location. Unknown bounds raise (typos must never
    # silently drop an element).
    visible_if: str | None = None

    @field_validator("color")
    @classmethod
    def _hex_color(cls, v: str) -> str:
        if not (v.startswith("#") and len(v) == 7 and all(c in VALID_HEX for c in v[1:])):
            raise ValueError(f"color must be #RRGGBB, got {v}")
        return v

    @field_validator("text")
    @classmethod
    def _no_newline(cls, v: str | None) -> str | None:
        if v is not None and ("\n" in v or "\r" in v):
            raise ValueError("text must not contain newlines")
        return v


class WatermarkTemplate(BaseModel):
    """Declarative template. `template_version` + engine pin the layout
    contract; exports carry both so old files stay interpretable."""

    name: str
    engine: str = "watermark-v1"
    template_version: str = "1"
    elements: list[Element] = Field(min_length=1)

    def export_json(self) -> str:
        return self.model_dump_json(indent=2)

    @classmethod
    def import_json(cls, raw: str | dict) -> "WatermarkTemplate":
        return cls.model_validate(raw if isinstance(raw, dict) else _loads(raw))


def _loads(raw: str) -> dict:
    import json
    return json.loads(raw)


class RenderingContext(BaseModel):
    """Typed inputs a template may bind to. Nothing else can appear in a
    rendered watermark (free text uses Element.text verbatim)."""

    captured_at: str | None = None
    location_meta: dict | None = None   # T02 LocationMetadata as dict
    project_name: str | None = None
    check_no: str | None = None
    qr_payload: str | None = None
    # W-07: user-defined fields (project-specific inspection forms).
    # Values are free text rendered verbatim - they are NEVER presented
    # as verified data, same honesty rule as Element.text.
    custom_fields: dict[str, str] = Field(default_factory=dict)

    def bound_value(self, element: Element) -> str | None:
        if element.type == ElementType.DATETIME:
            return self.captured_at
        if element.type == ElementType.GPS:
            return self._gps_label()
        if element.type == ElementType.PROJECT:
            if not element.source:
                raise ValueError("PROJECT element requires 'source'")
            return {"name": self.project_name}.get(element.source)
        if element.type == ElementType.CHECK_NO:
            return self.check_no
        if element.type == ElementType.CUSTOM:
            if not element.source:
                raise ValueError("CUSTOM element requires 'source' (field name)")
            return self.custom_fields.get(element.source)
        if element.type == ElementType.QRCODE:
            return self.qr_payload
        return None

    def _gps_label(self) -> str | None:
        """GPS text ALWAYS renders through T02's honesty-preserving label."""
        from techlab.location_meta import LocationMetadata

        if self.location_meta is None:
            return "no location"
        loc = LocationMetadata.model_validate(self.location_meta)
        return loc.trust_label()

    # -------------------------------------------------- W-07 condition bounds
    def condition_bound(self, name: str) -> str | None:
        """Resolve a visible_if bound name to its string value.
        Unknown names raise (typos must fail loudly, never silently hide
        an element)."""
        if name.startswith("custom:"):
            key = name[len("custom:"):]
            if key not in self.custom_fields:
                raise ValueError(f"visible_if references unknown custom field '{key}'")
            return self.custom_fields[key]
        if name.startswith("project:"):
            key = name[len("project:"):]
            if key != "name":
                raise ValueError(f"visible_if references unknown project field '{key}'")
            return self.project_name
        if name == "check_no":
            return self.check_no
        if name == "gps_source":
            if self.location_meta is None:
                return None
            return str(self.location_meta.get("source"))
        if name == "gps_grade":
            if self.location_meta is None:
                return None
            from techlab.location_meta import LocationMetadata
            return LocationMetadata.model_validate(self.location_meta).grade().value
        if name == "has_location":
            return "true" if self.location_meta is not None else "false"
        raise ValueError(f"visible_if references unknown bound '{name}'")


def _evaluate_visible_if(expr: str, ctx: RenderingContext) -> bool:
    """Evaluate 'bound==value' / 'bound!=value'. Both sides compared as
    strings; a None bound only matches != (absence)."""
    for op in ("!=", "=="):
        if op in expr:
            left, right = expr.split(op, 1)
            left, right = left.strip(), right.strip()
            actual = ctx.condition_bound(left)
            if op == "==":
                return (actual or "") == right
            return (actual or "") != right
    raise ValueError(f"visible_if must be 'bound==value' or 'bound!=value', got: {expr!r}")


class RenderedLayout(BaseModel):
    """Machine-readable geometry contract. iOS CoreGraphics consumes this;
    the ASCII preview is for humans/debugging."""

    template_name: str
    engine: str
    template_version: str
    placements: list[dict[str, Any]]   # element + resolved text + box


def render_layout(template: WatermarkTemplate, ctx: RenderingContext) -> RenderedLayout:
    """Resolve every element to concrete text + geometry. Free TEXT is
    rendered verbatim; bound elements resolve from the context or fail
    loudly (missing binding = render error, not blank output).
    W-07: elements whose visible_if evaluates false are SKIPPED (they
    are not placed); unknown condition bounds raise (never silently
    hide anything)."""
    placements: list[dict[str, Any]] = []
    for el in template.elements:
        if el.visible_if is not None and not _evaluate_visible_if(el.visible_if, ctx):
            continue
        if el.type == ElementType.TEXT:
            if el.text is None:
                raise ValueError(f"TEXT element missing text")
            resolved = el.text
        elif el.type == ElementType.LOGO:
            if el.asset is None:
                raise ValueError(f"LOGO element missing asset")
            resolved = f"[logo:{el.asset}]"
        else:
            resolved = ctx.bound_value(el)
            if resolved is None:
                raise ValueError(
                    f"element {el.type.value} has no binding in rendering context"
                )
        placements.append({
            "type": el.type.value, "anchor": el.anchor.value,
            "x": el.x, "y": el.y, "font_size": el.font_size,
            "color": el.color, "opacity": el.opacity,
            "text": resolved,
        })
    return RenderedLayout(
        template_name=template.name,
        engine=template.engine,
        template_version=template.template_version,
        placements=placements,
    )


def ascii_preview(layout: RenderedLayout, width: int = 64, height: int = 16) -> str:
    grid = [[" "] * width for _ in range(height)]
    for p in layout.placements:
        base = {"top_left": (0, 0), "top_right": (1, 0),
                "bottom_left": (0, 1), "bottom_right": (1, 1)}[p["anchor"]]
        row = min(height - 1, (base[0] * (height - 3) // 1) + (p["y"] // 40))
        col = min(width - 1, p["x"] // 40 + base[1] * (width - len(p["text"]) - 2))
        for i, ch in enumerate(p["text"][: width - 1]):
            if col + i < width:
                grid[row][col + i] = ch
    return "\n".join("".join(r) for r in grid)


def apply_template_to_original(
    ledger, original: OriginalPhoto, template: WatermarkTemplate,
    ctx: RenderingContext, derivative_bytes: bytes,
) -> Any:
    """Render + register as a Derivative. The ORIGINAL is never modified
    (enforced by the DerivativeLedger / ADR-0002 tests)."""
    layout = render_layout(template, ctx)
    recipe = DerivativeRecipe(
        engine=template.engine,
        template_version=template.template_version,
        params={"template_name": template.name},
    )
    return ledger.add_derivative(original, derivative_bytes, recipe), layout
