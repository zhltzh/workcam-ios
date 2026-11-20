"""T05 - immutable original / derivative chain tests."""

from __future__ import annotations

import json

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


# ---------------------------------------------------------------------------
# W-10 hardening: forged objects, byte/file re-verification, replacement
# and deletion detection, restart recovery.
# ---------------------------------------------------------------------------


def test_forged_original_object_rejected(ledger):
    """Same photo_id but a fake content_hash: the ledger record wins."""
    original = _seal(ledger)
    forged = original.model_copy(
        update={"content_hash": "ff" * 32}   # claims different content
    )
    with pytest.raises(OriginalTamperError, match="forged or stale object rejected"):
        ledger.add_derivative(
            forged, b"watermarked-bytes", DerivativeRecipe(engine="watermark-v1")
        )
    # and the real original still works
    ledger.add_derivative(
        original, b"watermarked-bytes", DerivativeRecipe(engine="watermark-v1")
    )


def test_verify_original_bytes_detects_tamper(ledger):
    original = _seal(ledger)
    assert ledger.verify_original_bytes(original.photo_id, b"jpeg-bytes-v1") is True
    assert ledger.verify_original_bytes(original.photo_id, b"tampered!") is False
    assert ledger.verify_original_bytes("never-sealed", b"x") is False


def test_verify_derivative_bytes(ledger):
    original = _seal(ledger)
    d = ledger.add_derivative(
        original, b"wm-bytes", DerivativeRecipe(engine="watermark-v1")
    )
    assert ledger.verify_derivative_bytes(d.derivative_id, b"wm-bytes") is True
    assert ledger.verify_derivative_bytes(d.derivative_id, b"swapped") is False
    assert ledger.verify_derivative_bytes("unknown", b"x") is False


def test_detect_file_changes_replacement_and_deletion(tmp_path, ledger):
    """On-disk tampering: replaced original + deleted derivative are both
    detected; unknown files are reported as orphans."""
    original = _seal(ledger)
    d = ledger.add_derivative(
        original, b"wm-bytes", DerivativeRecipe(engine="watermark-v1")
    )

    store = tmp_path / "store"
    (store / "originals").mkdir(parents=True)
    (store / "derivatives").mkdir(parents=True)
    orig_file = store / "originals" / f"{original.photo_id}.jpg"
    orig_file.write_bytes(b"jpeg-bytes-v1")
    derv_file = store / "derivatives" / f"{d.derivative_id}.jpg"
    derv_file.write_bytes(b"wm-bytes")

    # clean store: no problems
    assert ledger.detect_file_changes(store) == {"replaced": [], "missing": [], "unknown": []}

    # 1. replace original bytes (silent bit-rot / tampering)
    orig_file.write_bytes(b"jpeg-bytes-vX")
    result = ledger.detect_file_changes(store)
    assert str(orig_file.relative_to(store)) in result["replaced"] or \
           result["replaced"] == [f"originals/{original.photo_id}.jpg"]
    orig_file.write_bytes(b"jpeg-bytes-v1")           # restore

    # 2. delete the derivative file
    derv_file.unlink()
    result = ledger.detect_file_changes(store)
    assert "derivatives/" + derv_file.name in result["missing"]
    derv_file.write_bytes(b"wm-bytes")                # restore

    # 3. unknown file appears in the store (orphan)
    (store / "originals" / "ghost.jpg").write_bytes(b"?")
    result = ledger.detect_file_changes(store)
    assert "originals/ghost.jpg" in result["unknown"]

    # 4. missing directory = everything reported missing, no crash
    empty = tmp_path / "nowhere"
    result2 = ledger.detect_file_changes(empty)
    assert any(p.startswith("originals/") for p in result2["missing"])
    assert any(p.startswith("derivatives/") for p in result2["missing"])


def test_persist_load_survives_restart(tmp_path, ledger):
    """Ledger survives a process restart (persist -> load) with the full
    derivative->original chain intact, and derived records stay equal."""
    original = _seal(ledger)
    d1 = ledger.add_derivative(
        original, b"wm-A", DerivativeRecipe(engine="watermark-v1")
    )
    d2 = ledger.add_derivative(
        original, b"wm-B", DerivativeRecipe(engine="watermark-v1", template_version="2")
    )

    path = tmp_path / "ledger.json"
    ledger.persist(path)

    restored = DerivativeLedger.load(path)
    assert restored.get_original(original.photo_id) is not None
    # chain integrity after restart
    r1 = next(d for d in restored._derivatives if d.derivative_id == d1.derivative_id)
    r2 = next(d for d in restored._derivatives if d.derivative_id == d2.derivative_id)
    assert restored.trace_to_original(r1).content_hash == original.content_hash
    assert restored.verify_chain(r1) == []
    assert r1.operation_seq == 0 and r2.operation_seq == 1
    # byte verification still works from the restored ledger
    assert restored.verify_original_bytes(original.photo_id, b"jpeg-bytes-v1")
    assert restored.verify_derivative_bytes(d2.derivative_id, b"wm-B")
    # tampering still rejected across restart
    forged = original.model_copy(update={"content_hash": "ff" * 32})
    with pytest.raises(OriginalTamperError):
        restored.add_derivative(forged, b"wm-C", DerivativeRecipe(engine="x"))


def test_load_rejects_corrupted_ledger(tmp_path, ledger):
    path = tmp_path / "ledger.json"
    ledger.persist(path)
    path.write_text("{ broken", encoding="utf-8")
    with pytest.raises(ValueError, match="unreadable"):
        DerivativeLedger.load(path)

    # dangling back-link is caught at load time (never silently dropped)
    path.write_text(json.dumps({
        "version": 1,
        "originals": [],
        "derivatives": [{
            "derivative_id": "orphan01", "original_photo_hash": "ab" * 32,
            "original_photo_id": "does-not-exist", "content_hash": "cd" * 32,
            "recipe": {"engine": "x", "template_version": "1", "params": {}},
            "applied_at": "2026-11-20T10:00:00+00:00", "operation_seq": 0,
        }],
    }), encoding="utf-8")
    with pytest.raises(ValueError, match="corrupted ledger"):
        DerivativeLedger.load(path)
