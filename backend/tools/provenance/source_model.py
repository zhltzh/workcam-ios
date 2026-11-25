"""TM08: photo provenance & anti-substitution technical boundary.

A SOURCE-CLASSIFICATION model + detectability ledger. The central
honesty rule (from the task brief):

  Content-level re-shooting (photographing a screen, re-compositing)
  CANNOT be reliably detected from bytes alone. This module never
  claims it can. What it CAN do:

  1. classify the SUBMISSION channel (camera capture / app import /
     external file / duplicate submission) from metadata the client
     itself provides + server-side replay checks
  2. detect EXACT reuse (SHA-256 identity) and near-duplicates
     (perceptual hash) - candidates only, never proof
  3. cross-check EXIF/metadata consistency and flag IMPOSSIBLE or
     suspicious combinations
  4. report every signal with an explicit detectability grade:
     DETECTABLE / HEURISTIC / UNDETECTABLE_OFFLINE

Nothing here ever claims 'this photo shows a real scene'.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Detectability(str, Enum):
    DETECTABLE = "detectable"            # cryptographic certainty
    HEURISTIC = "heuristic"              # signal, may be defeated
    UNDETECTABLE = "undetectable_offline"


class SourceClass(str, Enum):
    CAMERA_CAPTURE = "camera_capture"       # app camera, live session
    APP_IMPORT = "app_import"               # user picked from library
    EXTERNAL_FILE = "external_file"         # Files/airdrop/extension
    DUPLICATE_SUBMISSION = "duplicate_submission"   # same bytes seen
    REUSED_ON_DIFFERENT_PHOTO = "reused_on_different_photo"
    RECAPTURE_SUSPECT = "recapture_suspect"         # heuristic signal
    SYNTHETIC_INPUT = "synthetic_input"     # injected/test input


@dataclass
class ProvenanceSignal:
    name: str
    grade: Detectability
    reason: str
    evidence: str


@dataclass
class ProvenanceReport:
    source_class: SourceClass | None
    signals: list[ProvenanceSignal]
    honest_boundary: str = (
        "No byte-level analysis can prove a photo shows a real scene. "
        "Screen re-shoots, composites and AI generation remain "
        "UNDETECTABLE_OFFLINE in the general case; every 'suspect' "
        "flag here is a HEURISTIC prompt for human review, never a "
        "verdict.")

    def as_dict(self) -> dict:
        return {
            "source_class": self.source_class.value if self.source_class
            else None,
            "signals": [{"name": s.name, "grade": s.grade.value,
                         "reason": s.reason, "evidence": s.evidence}
                        for s in self.signals],
            "honest_boundary": self.honest_boundary,
        }


# --------------------------------------------------------------- signals


def check_exact_duplicate(photo_sha256: str,
                          known_hashes: set[str]) -> ProvenanceReport:
    """DETECTABLE: SHA-256 identity against the caller's known set.
    Exact reuse is cryptographic - if the bytes were seen before,
    this is the same photo, whatever the filename says."""
    signals = []
    src = None
    if photo_sha256 in known_hashes:
        src = SourceClass.DUPLICATE_SUBMISSION
        signals.append(ProvenanceSignal(
            "exact_duplicate", Detectability.DETECTABLE,
            "byte-identical photo already registered under a different "
            "record",
            f"sha256 {photo_sha256} matches an existing record"))
    else:
        signals.append(ProvenanceSignal(
            "novel_bytes", Detectability.DETECTABLE,
            "no exact prior submission of these bytes",
            "sha256 lookup miss against the registry"))
    return ProvenanceReport(src, signals)


def check_exif_consistency(exif: dict) -> ProvenanceReport:
    """HEURISTIC + DETECTABLE mixed: EXIF is client-supplied data -
    it can be trivially forged, so consistency problems are strong
    NEGATIVE signals, while consistent EXIF proves nothing."""
    signals = []
    problems = []

    if not exif:
        signals.append(ProvenanceSignal(
            "no_exif", Detectability.HEURISTIC,
            "image carries no EXIF at all - stripped or imported",
            "EXIF block absent"))
        problems.append("no_exif")

    software = (exif.get("Software") or "").lower()
    if any(k in software for k in ("photoshop", "gimp", "snapseed",
                                   "lightroom", "pixelmator")):
        signals.append(ProvenanceSignal(
            "editor_software_tag", Detectability.HEURISTIC,
            "EXIF Software field names an image EDITOR",
            f"Software={exif.get('Software')}"))
        problems.append("editor_software_tag")

    # orientation vs claimed capture: a camera-captured photo usually
    # carries an orientation tag and a lens/camera make
    if exif and not (exif.get("Make") or exif.get("Model")):
        signals.append(ProvenanceSignal(
            "no_camera_make_model", Detectability.HEURISTIC,
            "EXIF lacks Make/Model - stripped, synthetic or editor "
            "re-export",
            "Make/Model absent"))
        problems.append("no_camera_make_model")

    # screenshot fingerprints (DETECTABLE as a tag, HEURISTIC as class)
    if software and any(k in software for k in ("screenshot", "screen",
                                                "simulator", "xcode")):
        signals.append(ProvenanceSignal(
            "screenshot_software_tag", Detectability.DETECTABLE,
            "EXIF Software field literally names a screen-capture "
            "tool",
            f"Software={exif.get('Software')}"))
        problems.append("screenshot_software_tag")

    src = None
    if "screenshot_software_tag" in problems:
        src = SourceClass.SYNTHETIC_INPUT
    elif problems:
        src = SourceClass.APP_IMPORT       # stripped/mutated metadata
    else:
        signals.append(ProvenanceSignal(
            "exif_consistent", Detectability.HEURISTIC,
            "EXIF looks camera-native - this PROVES NOTHING about "
            "content, forged EXIF is trivial",
            "Make/Model present, no editor tags"))
    return ProvenanceReport(src, signals)


def check_perceptual_similarity(phash: str, known_phashes: dict[str, str],
                                hamming_threshold: int = 12) -> ProvenanceReport:
    """HEURISTIC: near-duplicate candidates via perceptual hash. A
    candidate is a PROMPT FOR HUMAN REVIEW, never a verdict - crops,
    re-encodes and recaptures may or may not trip it, and different
    photos of the same wall can legitimately collide."""
    def hamming(a: str, b: str) -> int:
        return sum(x != y for x, y in zip(a, b))

    signals = []
    candidates = []
    for other_id, other_hash in known_phashes.items():
        d = hamming(phash, other_hash)
        if d <= hamming_threshold:
            candidates.append((other_id, d))
    if candidates:
        candidates.sort(key=lambda x: x[1])
        nearest, dist = candidates[0]
        signals.append(ProvenanceSignal(
            "perceptual_near_duplicate", Detectability.HEURISTIC,
            f"visually near-identical to existing photo {nearest} "
            f"(hamming {dist}) - candidate for human review only",
            f"phash distance {dist} <= {hamming_threshold}"))
    else:
        signals.append(ProvenanceSignal(
            "no_near_duplicate", Detectability.HEURISTIC,
            "no visually similar prior photo within threshold",
            "phash scan clean"))
    return ProvenanceReport(SourceClass.RECAPTURE_SUSPECT if candidates
                            else None, signals)


def check_recapture_heuristics(image_features: dict) -> ProvenanceReport:
    """HEURISTIC: signals correlated with SCREEN RECAPTURE (moire,
    skewed EXIF resolution vs stated camera, missing depth). Each is
    defeatable; together they raise a review flag. Never a verdict."""
    signals = []
    flags = []
    if image_features.get("moire_score", 0.0) > 0.6:
        signals.append(ProvenanceSignal(
            "moire_pattern", Detectability.HEURISTIC,
            "regular screen-pixel interference detected - consistent "
            "with re-photographing a display (also occurs on real "
            "textures)",
            f"moire_score={image_features['moire_score']}"))
        flags.append("moire")
    if image_features.get("skew_estimated_deg", 0.0) > 8.0:
        signals.append(ProvenanceSignal(
            "perspective_skew", Detectability.HEURISTIC,
            "strong perspective skew - phones shooting a mounted "
            "screen are rarely square to it (handheld documents too)",
            f"skew={image_features['skew_estimated_deg']}deg"))
        flags.append("skew")
    if not image_features.get("signals_available"):
        signals.append(ProvenanceSignal(
            "recapture_undetectable", Detectability.UNDETECTABLE,
            "in the general case re-shooting a photo on another "
            "screen produces a NOVEL image: new pixels, new EXIF, new "
            "hash - no offline analysis can universally detect it",
            "fundamental boundary of this task"))
    src = SourceClass.RECAPTURE_SUSPECT if flags else None
    return ProvenanceReport(src, signals)


def classify_submission(submission_meta: dict) -> ProvenanceReport:
    """Submission-channel classification from client-declared intake
    metadata + server replay checks (caller supplies precomputed
    hashes/features). The intake channel is client-DECLARED and is
    data, not proof - exactly like device_id."""
    signals = []
    channel = submission_meta.get("channel")
    channel_map = {
        "camera": SourceClass.CAMERA_CAPTURE,
        "library": SourceClass.APP_IMPORT,
        "files": SourceClass.EXTERNAL_FILE,
    }
    src = channel_map.get(channel)
    if src is None:
        signals.append(ProvenanceSignal(
            "unknown_channel", Detectability.HEURISTIC,
            "intake channel not declared or not recognized",
            f"channel={channel!r}"))
        src = SourceClass.EXTERNAL_FILE
    else:
        signals.append(ProvenanceSignal(
            "declared_channel", Detectability.HEURISTIC,
            "client-declared intake channel - data, not proof",
            f"channel={channel}"))
    return ProvenanceReport(src, signals)
