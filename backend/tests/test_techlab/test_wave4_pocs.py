"""Wave 4 PoC tests: T15 defect detection, T16 voice chain,
T17 change detection. All data SYNTHETIC; no device claims."""

from __future__ import annotations

import pytest

from techlab.change_detection import (
    ChangeReport,
    GrayImage,
    PairError,
    TranslationRegistrar,
    detect_change_regions,
)
from techlab.defect_detection import (
    ClassicalCrackDetector,
    DefectFinding,
    PixelMetrics,
    SyntheticImage,
    evaluate,
)
from techlab.voice_chain import (
    ChainError,
    EditRecord,
    MachineTranscript,
    ReportField,
    ReportFieldSource,
    TranscriptSegment,
    VoiceChain,
    VoiceOriginal,
    audio_hash,
)


# ------------------------------------------------------------------------ T15


def test_synthetic_crack_detected_with_honest_metrics():
    img = SyntheticImage(seed=1)
    img.draw_crack(start=(64, 8), direction=(1, 2), length=90, width=2)
    detector = ClassicalCrackDetector()
    metrics = evaluate(detector, img)
    assert metrics.objects_total == 1
    assert metrics.objects_detected == 1            # the crack is found
    assert metrics.recall is not None and metrics.recall > 0.5
    assert metrics.precision is not None and metrics.precision > 0.3


def test_speckle_noise_produces_no_crack_detections():
    """Dark round stains are NOT cracks: the elongation filter must
    reject them -> zero objects detected, honest FP accounting."""
    img = SyntheticImage(seed=2)
    img.add_noise_blobs(count=10, size=6, darkness=60)   # stains only
    detector = ClassicalCrackDetector()
    metrics = evaluate(detector, img)
    assert metrics.objects_total == 0                  # no crack drawn
    assert metrics.objects_detected == 0
    # metrics stay well-defined even with nothing to find
    assert metrics.recall is None or metrics.recall == 0


def test_metrics_none_semantics_when_no_predictions():
    """Empty prediction set: precision = None (undefined), never 0.0
    dressed up as a score."""
    img = SyntheticImage(seed=3)
    detector = ClassicalCrackDetector(min_pixels=10_000)   # nothing passes
    m = evaluate(detector, img)
    assert m.precision is None


def test_metrics_out_of_range_rejected():
    with pytest.raises(Exception):
        PixelMetrics(tp_pixels=1, fp_pixels=1, fn_pixels=1,
                     precision=1.5, recall=0.5)


def test_advisory_only_is_protocol_enforced():
    """An AI finding may NEVER carry advisory_only=False - the field is
    the anti-'automatic compliance verdict' guard."""
    with pytest.raises(Exception, match="ADVISORY ONLY"):
        DefectFinding(finding_id="f1", detector="classical-baseline-v1",
                      kind="crack", box=(0, 0, 10, 10),
                      confidence=0.9, advisory_only=False)
    ok = DefectFinding(finding_id="f1", detector="classical-baseline-v1",
                       kind="crack", box=(0, 0, 10, 10),
                       confidence=0.9)
    assert "not a compliance verdict" in ok.human_label()


def test_baseline_runs_on_clean_image_without_crash():
    img = SyntheticImage(seed=4)                     # no crack, no blobs
    detector = ClassicalCrackDetector()
    m = evaluate(detector, img)
    assert m.objects_total == 0 and m.objects_detected == 0


# ------------------------------------------------------------------------ T16


def _chain(duration=30.0) -> VoiceChain:
    audio = VoiceOriginal(
        audio_sha256=audio_hash(b"SYNTHETIC-AUDIO-BYTES"),
        duration_s=duration, captured_at="2026-11-21T10:00:00+00:00")
    transcript = MachineTranscript(audio_id=audio.audio_id,
                                   recognizer="apple-speech-ondevice",
                                   recognizer_version="17.0")
    return VoiceChain(original=audio, transcript=transcript)


def test_voice_chain_layered_editing():
    chain = _chain()
    chain.add_segment(TranscriptSegment(seg_id="s1", start_s=0.0,
                                        end_s=8.0, text="三号桥墩发现裂缝宽度约零点二毫米"))
    chain.add_edit(EditRecord(seg_id="s1", new_text="三号桥墩发现裂缝，宽度约0.2毫米",
                              editor="inspector-01",
                              edited_at="2026-11-21T10:05:00+00:00"))
    # similarity recorded for audit (machine vs corrected)
    assert 0.0 < chain.edits[0].similarity < 1.0
    # final text = machine + human layer
    assert "0.2毫米" in chain.final_segment_text("s1")


def test_edit_fabrication_of_unknown_segment_rejected():
    chain = _chain()
    with pytest.raises(ChainError, match="unknown segment"):
        chain.add_edit(EditRecord(seg_id="ghost", new_text="x",
                                  editor="e", edited_at="now"))


def test_double_edit_rejected_append_only():
    """Edits are append-only: silently REPLACING a correction would
    destroy the audit trail."""
    chain = _chain()
    chain.add_segment(TranscriptSegment(seg_id="s1", start_s=0.0,
                                        end_s=5.0, text="桥面油污"))
    chain.add_edit(EditRecord(seg_id="s1", new_text="桥面油渍",
                              editor="a", edited_at="t1"))
    with pytest.raises(ChainError, match="append-only"):
        chain.add_edit(EditRecord(seg_id="s1", new_text="桥面清洁",
                                  editor="b", edited_at="t2"))


def test_segment_outside_duration_rejected():
    """A transcript segment longer than the audio is fabricated timing."""
    chain = _chain(duration=10.0)
    with pytest.raises(ChainError, match="outside audio duration"):
        chain.add_segment(TranscriptSegment(seg_id="s1", start_s=0.0,
                                            end_s=15.0, text="x"))
    with pytest.raises(ChainError):
        chain.add_segment(TranscriptSegment(seg_id="s2", start_s=8.0,
                                            end_s=8.0, text="x"))


def test_report_field_must_cite_source():
    """Anti-fabrication core: an uncited report field is refused."""
    chain = _chain()
    chain.add_segment(TranscriptSegment(seg_id="s1", start_s=0.0,
                                        end_s=5.0, text="裂缝宽度零点二毫米"))
    with pytest.raises(ChainError, match="uncited values are forbidden"):
        chain.add_report_field(ReportField(field_name="crack_width",
                                           value="0.2mm",
                                           source=ReportFieldSource.MACHINE_TRANSCRIPT))
    # correctly cited machine field passes
    chain.add_report_field(ReportField(field_name="crack_width",
                                       value="0.2mm",
                                       source=ReportFieldSource.MACHINE_TRANSCRIPT,
                                       source_seg_id="s1"))
    # human_corrected without an edit record is ALSO fabrication
    with pytest.raises(ChainError, match="no edit record"):
        chain.add_report_field(ReportField(
            field_name="crack_width", value="0.2mm",
            source=ReportFieldSource.HUMAN_CORRECTED, source_seg_id="s1"))
    # manual_entry needs no segment (honest: typed by the human)
    chain.add_report_field(ReportField(field_name="inspector_name",
                                       value="张工",
                                       source=ReportFieldSource.MANUAL_ENTRY))


def test_audit_trail_complete():
    chain = _chain()
    chain.add_segment(TranscriptSegment(seg_id="s1", start_s=0.0,
                                        end_s=6.0, text="原始转写文本"))
    chain.add_edit(EditRecord(seg_id="s1", new_text="修正后的文本",
                              editor="张工", edited_at="2026-11-21T10:02:00+00:00"))
    chain.add_report_field(ReportField(field_name="summary",
                                       value="修正后的文本",
                                       source=ReportFieldSource.HUMAN_CORRECTED,
                                       source_seg_id="s1"))
    audit = chain.audit()
    assert audit["segments"] == 1
    assert audit["human_edits"] == 1
    assert audit["report_fields"][0]["source"] == "human_corrected"
    assert len(audit["audio_sha256"]) == 64


# ------------------------------------------------------------------------ T17


SIZE = 64


def test_registration_finds_translation():
    ref = GrayImage.synthetic_scene(SIZE, seed=10)
    moved = ref.shifted(5, -3)
    reg = TranslationRegistrar().register(ref, moved)
    # register() answers 'what shift realigns moved onto ref' = the
    # INVERSE of the applied motion (a direction the first draft got
    # wrong; kept as a comment lesson)
    assert (reg.dx, reg.dy) == (-5, 3)


def test_change_detected_after_registration():
    ref = GrayImage.synthetic_scene(SIZE, seed=11)
    moved = GrayImage.synthetic_scene(SIZE, seed=11,
                                      change_rect=(20, 20, 32, 30),
                                      change_delta=-90)
    reg, regions = detect_change_regions(ref, moved)
    assert len(regions) == 1
    x0, y0, x1, y1 = regions[0].box
    assert 18 <= x0 <= 22 and 18 <= y0 <= 22          # roughly the rect


def test_brightness_shift_is_not_a_change():
    """Classic false positive: same scene, exposure +30. After
    illumination normalization the pipeline must find ZERO regions."""
    ref = GrayImage.synthetic_scene(SIZE, seed=12)
    brighter = GrayImage.synthetic_scene(SIZE, seed=12, brightness=30)
    reg, regions = detect_change_regions(ref, brighter)
    assert regions == []


def test_unregistered_shift_produces_spurious_regions():
    """Honest limitation demo: the SAME scene, shifted but NOT
    registered, fires spurious diff - which is exactly why registration
    runs FIRST (and why angle/scale blindness is documented). Shift
    (3,3): the synthetic structure has period 40 and 7*4+3*4 == 40, so
    (4,4) would be a NO-OP structure shift - the shift must actually
    move content."""
    ref = GrayImage.synthetic_scene(SIZE, seed=13)
    moved = ref.shifted(3, 3)
    raw = ref.diff_map(moved)                            # no registration
    assert sum(raw) > 100                                # would be all 'change'
    reg, regions = detect_change_regions(ref, moved)     # registered
    assert regions == []                                 # correctly: none


def test_size_mismatch_rejected():
    a = GrayImage(SIZE, [128] * (SIZE * SIZE))
    b = GrayImage(32, [128] * (32 * 32))
    with pytest.raises(PairError):
        detect_change_regions(a, b)


def test_report_is_advisory_with_stated_limitations():
    report = ChangeReport(registration=(0, 0), change_regions=[(1, 1, 5, 5)])
    assert report.advisory_only is True
    assert any("translation-only" in l for l in report.limitations)
    assert "not a damage verdict" in report.human_label()
