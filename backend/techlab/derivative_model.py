"""T05 / W-10 - Immutable original vs. editable derivatives (tech-lab).

Implements ADR-0002: originals are read-only, derivatives carry a hash
chain back to the signed original, and every mutation of an original is
rejected.

W-10 hardening (review feedback on PR #8):
- add_derivative no longer trusts a caller-supplied OriginalPhoto object:
  the object's content_hash must match the LEDGER's sealed record
  (a forged object with a known photo_id is rejected).
- byte-level re-verification APIs for originals and derivatives, plus
  file-level verification and a directory change detector
  (replaced / missing / unknown files).
- persist()/load() so a restarted process rebuilds the same ledger and
  the derivative->original chain survives.

EXPERIMENTAL - not wired into the production `app` package.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

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
        # W-10: the ledger is the ONLY source of truth. A caller-supplied
        # OriginalPhoto object is untrusted input: same photo_id but a
        # different content_hash means the object was forged or stale.
        recorded = self._originals.get(original.photo_id)
        if recorded is None:
            raise ValueError("original not sealed in ledger")
        if recorded.content_hash != original.content_hash or recorded.read_only != original.read_only:
            raise OriginalTamperError(
                f"original object mismatch for {original.photo_id}: ledger has "
                f"{recorded.content_hash[:12]}..., object claims "
                f"{original.content_hash[:12]}... - forged or stale object rejected"
            )
        # derivative identity is computed from the BYTES, never declared
        existing_seq = [d.operation_seq for d in self._derivatives if d.original_photo_id == original.photo_id]
        derivative = Derivative(
            derivative_id=hashlib.sha256(derivative_bytes).hexdigest()[:16],
            original_photo_hash=recorded.content_hash,
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

    # ------------------------------------------------------------ W-10 APIs

    def verify_original_bytes(self, photo_id: str, data: bytes) -> bool:
        """Byte-level re-verification of a sealed original."""
        recorded = self._originals.get(photo_id)
        if recorded is None:
            return False
        return hashlib.sha256(data).hexdigest() == recorded.content_hash

    def verify_derivative_bytes(self, derivative_id: str, data: bytes) -> bool:
        """Byte-level re-verification of a registered derivative."""
        recorded = next(
            (d for d in self._derivatives if d.derivative_id == derivative_id), None
        )
        if recorded is None:
            return False
        return hashlib.sha256(data).hexdigest() == recorded.content_hash

    def verify_original_file(self, photo_id: str, path: Path) -> bool:
        """True iff the file's CURRENT bytes still match the sealed hash."""
        path = Path(path)
        if not path.exists():
            return False
        return self.verify_original_bytes(photo_id, path.read_bytes())

    def verify_derivative_file(self, derivative_id: str, path: Path) -> bool:
        path = Path(path)
        if not path.exists():
            return False
        return self.verify_derivative_bytes(derivative_id, path.read_bytes())

    def detect_file_changes(self, directory: Path) -> dict:
        """Scan an on-disk store for tampering against the ledger.

        Expected layout:
            <directory>/originals/<photo_id>.jpg
            <directory>/derivatives/<derivative_id>.jpg

        Returns {'replaced': [...], 'missing': [...], 'unknown': [...]} -
        replaced/missing are tampering indicators; unknown files are
        orphans the ledger cannot account for. Detection only - the
        caller decides what to quarantine."""
        directory = Path(directory)
        problems: dict[str, list[str]] = {"replaced": [], "missing": [], "unknown": []}

        orig_dir = directory / "originals"
        if orig_dir.exists():
            for photo_id in self._originals:
                f = orig_dir / f"{photo_id}.jpg"
                if not f.exists():
                    problems["missing"].append(f"originals/{photo_id}.jpg")
                elif not self.verify_original_file(photo_id, f):
                    problems["replaced"].append(f"originals/{photo_id}.jpg")
            known = set(self._originals.keys())
            for f in sorted(orig_dir.iterdir()):
                if f.is_file() and f.suffix == ".jpg" and f.stem not in known:
                    problems["unknown"].append(f"originals/{f.name}")
        else:
            # directory missing entirely: every sealed original is missing
            problems["missing"].extend(
                f"originals/{pid}.jpg" for pid in self._originals
            )

        derv_dir = directory / "derivatives"
        if derv_dir.exists():
            for d in self._derivatives:
                f = derv_dir / f"{d.derivative_id}.jpg"
                if not f.exists():
                    problems["missing"].append(f"derivatives/{d.derivative_id}.jpg")
                elif not self.verify_derivative_file(d.derivative_id, f):
                    problems["replaced"].append(f"derivatives/{d.derivative_id}.jpg")
            known = {d.derivative_id for d in self._derivatives}
            for f in sorted(derv_dir.iterdir()):
                if f.is_file() and f.suffix == ".jpg" and f.stem not in known:
                    problems["unknown"].append(f"derivatives/{f.name}")
        else:
            problems["missing"].extend(
                f"derivatives/{d.derivative_id}.jpg" for d in self._derivatives
            )

        return problems

    # -------------------------------------------------------- restart support

    def persist(self, path: Path) -> None:
        """Write the ledger to disk atomically (tmp + replace)."""
        payload = {
            "version": 1,
            "originals": [o.model_dump() for o in self._originals.values()],
            "derivatives": [d.model_dump() for d in self._derivatives],
        }
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp.replace(path)

    @classmethod
    def load(cls, path: Path) -> "DerivativeLedger":
        """Rebuild the ledger after a process restart. Raises ValueError
        on a corrupted file (never silently starts empty)."""
        path = Path(path)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            raise ValueError(f"ledger file unreadable: {e}") from e
        if not isinstance(payload, dict) or payload.get("version") != 1:
            raise ValueError("ledger file format not recognized (version != 1)")

        ledger = cls.__new__(cls)
        ledger._originals = {
            o["photo_id"]: OriginalPhoto.model_validate(o)
            for o in payload.get("originals", [])
        }
        ledger._derivatives = [
            Derivative.model_validate(d) for d in payload.get("derivatives", [])
        ]
        # integrity self-check: every derivative back-link must resolve
        for d in ledger._derivatives:
            if d.original_photo_id not in ledger._originals:
                raise ValueError(
                    f"corrupted ledger: derivative {d.derivative_id} references "
                    f"unknown original {d.original_photo_id}"
                )
        return ledger
