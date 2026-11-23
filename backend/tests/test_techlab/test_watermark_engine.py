"""T10 - declarative watermark template engine tests."""

from __future__ import annotations

import pytest

from techlab.derivative_model import DerivativeLedger
from techlab.watermark_engine import (
    Anchor,
    Element,
    ElementType,
    RenderingContext,
    WatermarkTemplate,
    ascii_preview,
    render_layout,
    apply_template_to_original,
)


GPS_MANUAL = {
    "source": "manual", "latitude": 39.9, "longitude": 116.4,
    "horizontal_accuracy": None, "note": "B2",
}
GPS_VERIFIED = {
    "source": "gps", "latitude": 39.9, "longitude": 116.4,
    "horizontal_accuracy": 4.0,
}


def _tpl(elements):
    return WatermarkTemplate(name="site-report", elements=elements)


def test_template_import_export_roundtrip():
    tpl = _tpl([Element(type=ElementType.TEXT, anchor=Anchor.BOTTOM_RIGHT,
                        x=10, y=10, font_size=24, text="Site A")])
    clone = WatermarkTemplate.import_json(tpl.export_json())
    assert clone == tpl


def test_invalid_color_and_opacity_rejected():
    with pytest.raises(Exception):
        Element(type=ElementType.TEXT, anchor=Anchor.TOP_LEFT, x=0, y=0,
                font_size=12, color="red")
    with pytest.raises(Exception):
        Element(type=ElementType.TEXT, anchor=Anchor.TOP_LEFT, x=0, y=0,
                font_size=12, opacity=1.5)


def test_free_text_rendered_verbatim_never_verified():
    ctx = RenderingContext()   # empty context
    tpl = _tpl([Element(type=ElementType.TEXT, anchor=Anchor.TOP_LEFT,
                        x=0, y=0, font_size=12, text="QA PASSED 100%")])
    layout = render_layout(tpl, ctx)
    # user text is rendered EXACTLY as typed - engine adds nothing
    assert layout.placements[0]["text"] == "QA PASSED 100%"
    # and nothing in the layout claims engine verification
    assert "verified" not in layout.model_dump_json().lower()


def test_manual_gps_renders_not_verified_label():
    ctx = RenderingContext(location_meta=GPS_MANUAL)
    tpl = _tpl([Element(type=ElementType.GPS, anchor=Anchor.BOTTOM_LEFT,
                        x=0, y=0, font_size=14)])
    layout = render_layout(tpl, ctx)
    text = layout.placements[0]["text"]
    assert "not verified" in text
    assert "verified gps" not in text


def test_verified_gps_renders_grade():
    ctx = RenderingContext(location_meta=GPS_VERIFIED)
    tpl = _tpl([Element(type=ElementType.GPS, anchor=Anchor.BOTTOM_LEFT,
                        x=0, y=0, font_size=14)])
    layout = render_layout(tpl, ctx)
    assert "grade A" in layout.placements[0]["text"]


def test_missing_binding_fails_loudly_not_blank():
    ctx = RenderingContext()   # no check_no
    tpl = _tpl([Element(type=ElementType.CHECK_NO, anchor=Anchor.TOP_LEFT,
                        x=0, y=0, font_size=12)])
    with pytest.raises(ValueError, match="no binding"):
        render_layout(tpl, ctx)


def test_ascii_preview_smoke():
    ctx = RenderingContext(location_meta=GPS_VERIFIED, captured_at="2026-11-19T10:00:00+00:00")
    tpl = _tpl([
        Element(type=ElementType.DATETIME, anchor=Anchor.BOTTOM_LEFT, x=0, y=0, font_size=16),
        Element(type=ElementType.GPS, anchor=Anchor.BOTTOM_RIGHT, x=0, y=0, font_size=12),
    ])
    preview = ascii_preview(render_layout(tpl, ctx))
    assert "2026-11-19" in preview
    assert "gps" in preview


def test_apply_template_never_touches_original():
    ledger = DerivativeLedger()
    original = ledger.seal_original(b"original-evidence-bytes",
                                    captured_at="2026-11-19T10:00:00+00:00")
    ctx = RenderingContext(project_name="Site A")
    tpl = _tpl([Element(type=ElementType.PROJECT, anchor=Anchor.BOTTOM_RIGHT,
                        x=0, y=0, font_size=20, source="name")])
    derivative, layout = apply_template_to_original(
        ledger, original, tpl, ctx, b"watermarked-output-bytes")

    # original untouched
    assert original.content_hash == ledger.trace_to_original(derivative).content_hash
    assert derivative.recipe.engine == "watermark-v1"
    assert derivative.recipe.template_version == "1"
    assert layout.placements[0]["text"] == "Site A"


# ---------------------------------------------------------------------------
# W-07: custom fields + conditional visibility
# ---------------------------------------------------------------------------


def test_custom_field_renders_verbatim():
    ctx = RenderingContext(custom_fields={"bridge_deck": "K12+300 上下游"})
    tpl = _tpl([Element(type=ElementType.CUSTOM, anchor=Anchor.BOTTOM_LEFT,
                        x=0, y=0, font_size=14, source="bridge_deck")])
    layout = render_layout(tpl, ctx)
    assert layout.placements[0]["text"] == "K12+300 上下游"
    # honesty: custom fields are user data, engine adds no claim
    assert "verified" not in layout.model_dump_json().lower()


def test_custom_field_missing_binding_fails_loudly():
    ctx = RenderingContext(custom_fields={})
    tpl = _tpl([Element(type=ElementType.CUSTOM, anchor=Anchor.TOP_LEFT,
                        x=0, y=0, font_size=12, source="not_there")])
    with pytest.raises(ValueError, match="no binding"):
        render_layout(tpl, ctx)


def test_visible_if_skips_element_when_condition_false():
    ctx = RenderingContext(custom_fields={"zone": "A"},
                           project_name="Site A")
    tpl = _tpl([
        Element(type=ElementType.TEXT, anchor=Anchor.TOP_LEFT, x=0, y=0,
                font_size=12, text="always on"),
        Element(type=ElementType.TEXT, anchor=Anchor.BOTTOM_LEFT, x=0, y=0,
                font_size=12, text="zone B only",
                visible_if="custom:zone==B"),
        Element(type=ElementType.TEXT, anchor=Anchor.BOTTOM_RIGHT, x=0, y=0,
                font_size=12, text="zone A only",
                visible_if="custom:zone==A"),
    ])
    layout = render_layout(tpl, ctx)
    texts = [p["text"] for p in layout.placements]
    assert "always on" in texts
    assert "zone A only" in texts
    assert "zone B only" not in texts      # skipped, not blank, not error


def test_visible_if_gps_conditions():
    manual_ctx = RenderingContext(location_meta=GPS_MANUAL)
    gps_ctx = RenderingContext(location_meta=GPS_VERIFIED)

    tpl = lambda cond: _tpl([
        Element(type=ElementType.TEXT, anchor=Anchor.TOP_LEFT, x=0, y=0,
                font_size=12, text="verified fix", visible_if=cond)
    ])
    # manual source -> 'verified fix' label must NOT render
    assert render_layout(tpl("gps_source==gps"), manual_ctx).placements == []
    # verified gps -> renders
    assert render_layout(tpl("gps_source==gps"), gps_ctx).placements[0]["text"] == "verified fix"
    # grade-based condition
    assert render_layout(tpl("gps_grade==A"), gps_ctx).placements[0]["text"] == "verified fix"
    assert render_layout(tpl("gps_grade==A"), manual_ctx).placements == []
    # has_location
    assert render_layout(tpl("has_location==true"), gps_ctx).placements != []
    assert render_layout(tpl("has_location==true"), RenderingContext()).placements == []


def test_visible_if_unknown_bound_raises():
    ctx = RenderingContext()
    tpl = _tpl([Element(type=ElementType.TEXT, anchor=Anchor.TOP_LEFT, x=0, y=0,
                        font_size=12, text="x", visible_if="typo_bound==1")])
    with pytest.raises(ValueError, match="unknown bound"):
        render_layout(tpl, ctx)


def test_visible_if_malformed_expression_raises():
    ctx = RenderingContext()
    tpl = _tpl([Element(type=ElementType.TEXT, anchor=Anchor.TOP_LEFT, x=0, y=0,
                        font_size=12, text="x", visible_if="just-a-word")])
    with pytest.raises(ValueError, match="bound==value"):
        render_layout(tpl, ctx)
