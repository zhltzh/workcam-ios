"""T05 - Immutable original vs. editable derivatives (tech-lab experiment).

Implements ADR-0002: originals are read-only, derivatives carry a hash
chain back to the signed original, and every mutation of an original is
rejected.

EXPERIMENTAL - not wired into the production `app` package.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from pydantic import BaseModel, Field, field_validator


class OriginalPhoto(BaseModel):
    """Immutable original. `content_hash` binds to the signed manifest."""

    photo_id: str
    content_hash: str          # SHA-256 of the on-disk bytes
    captured_at: str
    read_only: bool = True     # always True; a write attempt must fail

    def overwrite_check(self, new_bytes: bytes) -> None:
        """Any write to an original after sealing is a fatal error.

        Called by storage layers BEFORE touching the file. Raises
        OriginalTamperError unconditionally for non-identical bytes:
        originals are write-once.
        """
        new_hash = hashlib.sha256(new_bytes).hexdigest()
        if new_hash != self.content_hash:
            raise OriginalTamperError(
                f"refusing write to original {self.photo_id}: content would change "
                f"({self.content_hash[:12]}... -> {new_hash[:12]}...)"
            )
        raise OriginalTamperError(
            f"refusing write to original {self.photo_id}: originals are write-once"
        )


class OriginalTamperError(Exception):
    pass


class DerivativeRecipe(BaseModel):
    """Declarative description of HOW a derivative was produced.

    Same recipe + same original bytes should reproduce the derivative.
    Versioned so old exports remain explainable.
    """

    engine: str                  # e.g. "watermark-v1"
    template_version: str = "1"
    params: dict[str, str] = Field(default_factory=dict)

    @field_validator("params")
    @classmethod
    def _no_newline_keys(cls, v: dict[str, str]) -> dict[str, str]:
        for key, value in v.items():
            if "\n" in key or "\n" in str(value):
                raise ValueError("recipe params must not contain newlines")
        return v


class Derivative(BaseModel):
    """An editable/exported artifact that references its original.

    Chain: derivative.content_hash -> self bytes
           derivative.original_photo_hash -> signed original manifest hash
    """

    derivative_id: str
    original_photo_hash: str    # MUST match an OriginalPhoto.content_hash
    original_photo_id: str
    content_hash: str           # hash of the derivative bytes themselves
    recipe: DerivativeRecipe
    applied_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    operation_seq: int = Field(ge=0)


class DerivativeLedger:
    """In-memory registry enforcing ADR-0002 invariants."""

    def __init__(self) -> None:
        self._originals: dict[str, OriginalPhoto] = {}
        self._derivatives: list[Derivative] = []

    def seal_original(self, photo_bytes: bytes, captured_at: str) -> OriginalPhoto:
        photo = OriginalPhoto(
            photo_id=hashlib.sha256(photo_bytes).hexdigest()[:16],
            content_hash=hashlib.sha256(photo_bytes).hexdigest(),
            captured_at=captured_at,
        )
        if photo.photo_id in self._originals:
            raise OriginalTamperError(f"original {photo.photo_id} already sealed")
        self._originals[photo.photo_id] = photo
        return photo

    def get_original(self, photo_id: str) -> OriginalPhoto | None:
        return self._originals.get(photo_id)

    def add_derivative(
        self, original: OriginalPhoto, derivative_bytes: bytes, recipe: DerivativeRecipe
    ) -> Derivative:
        if original.photo_id not in self._originals:
            raise ValueError("original not sealed in ledger")
        existing_seq = [d.operation_seq for d in self._derivatives if d.original_photo_id == original.photo_id]
        derivative = Derivative(
            derivative_id=hashlib.sha256(derivative_bytes).hexdigest()[:16],
            original_photo_hash=original.content_hash,
            original_photo_id=original.photo_id,
            content_hash=hashlib.sha256(derivative_bytes).hexdigest(),
            recipe=recipe,
            operation_seq=(max(existing_seq) + 1) if existing_seq else 0,
        )
        # invariant: derivative must never claim to BE the original
        if derivative.content_hash == derivative.original_photo_hash:
            raise OriginalTamperError(
                "derivative hash equals original hash: derivative must be a distinct artifact"
            )
        self._derivatives.append(derivative)
        return derivative

    def trace_to_original(self, derivative: Derivative) -> OriginalPhoto | None:
        """Follow the chain back to the sealed original (T13 export uses this)."""
        original = self._originals.get(derivative.original_photo_id)
        if original is None or original.content_hash != derivative.original_photo_hash:
            return None
        return original

    def verify_chain(self, derivative: Derivative) -> list[str]:
        """Structural checks for the evidence package. Returns problem list."""
        problems: list[str] = []
        if not any(d is derivative or d.derivative_id == derivative.derivative_id
                   for d in self._derivatives):
            problems.append("derivative not in ledger")
        original = self.trace_to_original(derivative)
        if original is None:
            problems.append("original back-link broken or missing")
        if derivative.recipe.engine == "":
            problems.append("recipe engine missing")
        return problems
