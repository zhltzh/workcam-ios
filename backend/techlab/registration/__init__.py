"""WorkCam registration PoC package (TM-04 E07, was TM-03 C06).

ISOLATED experimental module - the techlab zone. Nothing here is
imported by backend/app (the quality gate's prod/experimental
isolation check enforces that). Promotion to production is a
separate DECISION_REQUIRED step.

Phase-1 design (see docs/tech-lab/tm04-e07-registration-selection.md):
FFT phase correlation (coarse) + pyramid NCC refinement (fine),
translation-only, confidence-gated with NAMED refusals.

Dependencies: numpy only (already a backend dependency for other
techlab modules). No OpenCV - that constraint is reviewed in the
selection document and is deliberate.
"""

from .phase_registration import (
    RegistrationError,
    RegistrationResult,
    estimate_translation,
)

__all__ = [
    "RegistrationError",
    "RegistrationResult",
    "estimate_translation",
]
