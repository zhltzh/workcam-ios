"""T16 - voice record -> transcript -> human edit chain (Wave 4).

RESEARCH CONCLUSION FIRST:
  iOS on-device speech recognition exists (Speech framework,
  SFSpeechRecognizer requiresOnDeviceOperation / SpeechAnalyzer in
  newer SDKs) but CANNOT be evaluated on this Windows box - no iPhone,
  and downloading speech models is outside task boundaries. What CAN be
  honestly built: the CHAIN-OF-CUSTODY protocol for voice records:

  1. the ORIGINAL audio is hashed and immutable (like T05 originals);
  2. the machine TRANSCRIPT carries the recognizer version and is
     never edited in place;
  3. human corrections are a LAYER (diff records) - who changed what,
     with edit distances, so 'the report' is always traceable to both
     the machine output and every human touch;
  4. the protocol FORBIDS fabricating inspection values: a report field
     may be machine-transcribed or human-corrected, but never
     free-invented from nothing - every field cites its source span.

  This module is that protocol + its tests. Real recognizer evaluation
  is DEVICE_PENDING (Mac), like T01/T04.
"""

from __future__ import annotations

import hashlib
import uuid
from difflib import SequenceMatcher

from pydantic import BaseModel, Field, field_validator


class ChainError(ValueError):
    pass


class VoiceOriginal(BaseModel):
    """Immutable audio + capture metadata (T05-style write-once)."""

    audio_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    audio_sha256: str
    duration_s: float = Field(gt=0)
    captured_at: str
    language: str = "zh-CN"

    @field_validator("audio_sha256")
    @classmethod
    def _hex64(cls, v: str) -> str:
        if len(v) != 64 or any(c not in "0123456789abcdef" for c in v):
            raise ValueError("audio_sha256 must be 64 lowercase hex chars")
        return v


class TranscriptSegment(BaseModel):
    """One machine-transcribed segment with timing. Never edited in
    place; corrections go to EditRecords."""

    seg_id: str
    start_s: float = Field(ge=0)
    end_s: float = Field(gt=0)
    text: str

    @field_validator("text")
    @classmethod
    def _no_newline(cls, v: str) -> str:
        if "\n" in v or "\r" in v:
            raise ValueError("segment text must be single-line")
        return v


class MachineTranscript(BaseModel):
    """The frozen machine output."""

    transcript_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    audio_id: str
    recognizer: str                      # e.g. 'apple-speech-ondevice'
    recognizer_version: str = "unknown"
    language: str = "zh-CN"
    segments: list[TranscriptSegment] = []


class EditRecord(BaseModel):
    """A human correction: replaces the text of ONE segment. The final
    report is machine text + ordered edit layer."""

    edit_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:10])
    seg_id: str
    new_text: str
    editor: str                          # human identity (device owner)
    edited_at: str
    similarity: float | None = None      # edit distance ratio kept for audit

    @field_validator("new_text")
    @classmethod
    def _no_newline(cls, v: str) -> str:
        if "\n" in v or "\r" in v:
            raise ValueError("corrected text must be single-line")
        return v


class ReportFieldSource(str, __import__("enum").Enum):
    """Every report field must cite ONE of these - there is no 'made
    up' source in the protocol."""

    MACHINE_TRANSCRIPT = "machine_transcript"
    HUMAN_CORRECTED = "human_corrected"
    MANUAL_ENTRY = "manual_entry"


class ReportField(BaseModel):
    """One field of the auto-generated report draft. Provenance is
    mandatory; uncited values are rejected (anti-fabrication core)."""

    field_name: str
    value: str
    source: ReportFieldSource
    source_seg_id: str | None = None     # required for transcript sources

    @field_validator("field_name")
    @classmethod
    def _no_newline(cls, v: str) -> str:
        if "\n" in v:
            raise ValueError("field name must be single-line")
        return v

    def validate_citation(self, chain: "VoiceChain") -> None:
        if self.source in (ReportFieldSource.MACHINE_TRANSCRIPT,
                          ReportFieldSource.HUMAN_CORRECTED):
            if not self.source_seg_id:
                raise ChainError(
                    f"report field '{self.field_name}' cites "
                    f"{self.source.value} without a segment id - "
                    f"uncited values are forbidden (anti-fabrication)")
            chain.require_segment(self.source_seg_id)
            if self.source == ReportFieldSource.HUMAN_CORRECTED:
                if not chain.has_edit(self.source_seg_id):
                    raise ChainError(
                        f"report field '{self.field_name}' claims "
                        f"human_corrected but segment {self.source_seg_id} "
                        f"has no edit record")


class VoiceChain(BaseModel):
    """The full chain: audio -> machine transcript -> edits -> report."""

    original: VoiceOriginal
    transcript: MachineTranscript
    edits: list[EditRecord] = []
    report: list[ReportField] = []

    # ---------------------------------------------------------- invariants

    def add_segment(self, seg: TranscriptSegment) -> None:
        if seg.end_s <= seg.start_s:
            raise ChainError("segment end must be after start")
        if seg.start_s > self.original.duration_s or seg.end_s > self.original.duration_s:
            raise ChainError("segment outside audio duration - fabricated timing")
        self.transcript.segments.append(seg)

    def require_segment(self, seg_id: str) -> TranscriptSegment:
        seg = next((s for s in self.transcript.segments if s.seg_id == seg_id), None)
        if seg is None:
            raise ChainError(f"unknown segment {seg_id}")
        return seg

    def has_edit(self, seg_id: str) -> bool:
        return any(e.seg_id == seg_id for e in self.edits)

    def add_edit(self, edit: EditRecord) -> None:
        seg = self.require_segment(edit.seg_id)
        if self.has_edit(edit.seg_id):
            raise ChainError(f"segment {edit.seg_id} already has an edit - "
                             f"edits are append-only, add a new edit record "
                             f"instead of silently replacing one")
        edit.similarity = round(SequenceMatcher(
            None, seg.text, edit.new_text).ratio(), 4)
        self.edits.append(edit)

    def add_report_field(self, f: ReportField) -> None:
        f.validate_citation(self)
        self.report.append(f)

    def final_segment_text(self, seg_id: str) -> str:
        """Machine text with the human layer applied."""
        seg = self.require_segment(seg_id)
        edit = next((e for e in self.edits if e.seg_id == seg_id), None)
        return edit.new_text if edit else seg.text

    def audit(self) -> dict:
        """Export the full provenance trail."""
        return {
            "audio_sha256": self.original.audio_sha256,
            "recognizer": f"{self.transcript.recognizer} "
                          f"v{self.transcript.recognizer_version}",
            "segments": len(self.transcript.segments),
            "human_edits": len(self.edits),
            "edit_similarities": [e.similarity for e in self.edits],
            "report_fields": [
                {"name": f.field_name, "source": f.source.value,
                 "seg": f.source_seg_id} for f in self.report],
        }


def audio_hash(audio_bytes: bytes) -> str:
    return hashlib.sha256(audio_bytes).hexdigest()
