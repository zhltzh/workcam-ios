"""T05 - immutable original / derivative chain tests."""

from __future__ import annotations

import pytest

from techlab.derivative_model import (
    DerivativeLedger,
    DerivativeRecipe,
    OriginalTamperError,
)


@pytest.fixture
def ledger():
    return DerivativeLedger()


def _seal(led: DerivativeLedger, data: bytes = b"jpeg-bytes-v1"):
    return led.seal_original(data, captured_at="2026-11-19T09:00:00+00:00")


def test_seal_and_trace(ledger):
    original = _seal(ledger)
    derivative = ledger.add_derivative(
        original, b"jpeg-with-watermark", DerivativeRecipe(engine="watermark-v1")
    )
    assert ledger.trace_to_original(derivative).photo_id == original.photo_id
    assert ledger.verify_chain(derivative) == []


def test_original_overwrite_always_rejected(ledger):
    """ADR-0002: ANY write to a sealed original must fail - even with
    identical bytes (write-once) and especially with changed bytes."""
    original = _seal(ledger)
    with pytest.raises(OriginalTamperError):
        original.overwrite_check(b"tampered-bytes")
    with pytest.raises(OriginalTamperError):
        original.overwrite_check(b"jpeg-bytes-v1")   # write-once, even identical


def test_derivative_cannot_pose_as_original(ledger):
    original = _seal(ledger)
    with pytest.raises(OriginalTamperError):
        ledger.add_derivative(
            original, b"jpeg-bytes-v1",   # same bytes as the original
            DerivativeRecipe(engine="watermark-v1"),
        )


def test_broken_backlink_detected(ledger):
    original = _seal(ledger)
    derivative = ledger.add_derivative(
        original, b"with-logo", DerivativeRecipe(engine="watermark-v1")
    )
    # simulate evidence package losing the original
    ledger._originals.pop(original.photo_id)
    problems = ledger.verify_chain(derivative)
    assert any("back-link broken" in p for p in problems)
    assert ledger.trace_to_original(derivative) is None


def test_watermark_change_never_touches_original(ledger):
    """Product rule: changing the watermark must not damage the evidence."""
    original = _seal(ledger)
    wm1 = ledger.add_derivative(
        original, b"derivative-with-watermark-A", DerivativeRecipe(engine="watermark-v1")
    )
    wm2 = ledger.add_derivative(
        original, b"derivative-with-watermark-B",
        DerivativeRecipe(engine="watermark-v1", template_version="2"),
    )
    # original untouched and still traceable from BOTH derivatives
    assert original.content_hash == ledger.trace_to_original(wm1).content_hash
    assert original.content_hash == ledger.trace_to_original(wm2).content_hash
    assert wm1.content_hash != wm2.content_hash
    # operation sequence increments per original
    assert wm1.operation_seq == 0
    assert wm2.operation_seq == 1


def test_double_seal_rejected(ledger):
    _seal(ledger)
    with pytest.raises(OriginalTamperError):
        _seal(ledger)


def test_recipe_newlines_rejected(ledger):
    from techlab.derivative_model import DerivativeRecipe as R
    with pytest.raises(Exception):
        R(engine="watermark-v1", params={"note": "a\nb"})
