from __future__ import annotations

import hashlib

from app.models.capture import SignatureManifest


def compute_record_hash(manifest: SignatureManifest) -> str:
    from app.services.serialization import canonical_payload

    payload = canonical_payload(manifest)
    sig_bytes = manifest.signature.encode("ascii")
    combined = payload + sig_bytes
    return hashlib.sha256(combined).hexdigest()


def verify_chain_link(
    current: SignatureManifest,
    previous: SignatureManifest | None,
) -> tuple[bool, str]:
    if current.sequence_number == 0:
        if current.previous_record_hash is not None:
            return False, (
                "First record (sequence_number=0) must have "
                "previous_record_hash=null"
            )
        return True, "Genesis record OK"

    if current.previous_record_hash is None:
        return False, (
            f"Record seq={current.sequence_number} must have "
            "previous_record_hash"
        )

    if previous is None:
        return False, (
            f"Missing previous record for seq={current.sequence_number}"
        )

    if previous.sequence_number != current.sequence_number - 1:
        return False, (
            f"Sequence gap: expected prev seq={current.sequence_number - 1}, "
            f"got {previous.sequence_number}"
        )

    prev_hash = compute_record_hash(previous)
    if prev_hash != current.previous_record_hash:
        return False, (
            f"Hash chain broken at seq={current.sequence_number}: "
            f"previous_record_hash does not match computed hash of prev record"
        )

    return True, "Chain link valid"


class ChainVerificationResult:
    def __init__(self) -> None:
        self.results: list[dict] = []
        self.warnings: list[str] = []
        self.original_order_preserved: bool = True

    def add_result(self, result: dict) -> None:
        self.results.append(result)

    def add_warning(self, warning: str) -> None:
        self.warnings.append(warning)

    def to_dict(self) -> dict:
        return {
            "results": self.results,
            "warnings": self.warnings,
            "original_order_preserved": self.original_order_preserved,
        }


def verify_chain(
    records: list[SignatureManifest],
    preserve_original_order: bool = True,
) -> dict:
    result = ChainVerificationResult()

    if not records:
        return {"error": "Empty record list", "results": [], "warnings": []}

    original_indices = {id(r): i for i, r in enumerate(records)}

    if preserve_original_order:
        sorted_records = list(records)
        sorted_records.sort(key=lambda r: r.sequence_number)
        for i, r in enumerate(sorted_records):
            orig_idx = original_indices[id(r)]
            if orig_idx != i:
                result.original_order_preserved = False
                result.add_warning(
                    f"Record seq={r.sequence_number} was at original index "
                    f"{orig_idx}, not {i}. Original submission order differs "
                    f"from sequence order."
                )
    else:
        sorted_records = sorted(records, key=lambda r: r.sequence_number)

    seen_seqs: dict[int, int] = {}
    for i, r in enumerate(sorted_records):
        if r.sequence_number in seen_seqs:
            result.add_result({
                "sequence_number": r.sequence_number,
                "valid": False,
                "reason": (
                    f"Duplicate sequence number {r.sequence_number} "
                    f"(first at index {seen_seqs[r.sequence_number]}, "
                    f"second at index {i})"
                ),
            })
        else:
            seen_seqs[r.sequence_number] = i

    expected_seqs = set(range(len(sorted_records)))
    actual_seqs = set(r.sequence_number for r in sorted_records)
    missing_seqs = expected_seqs - actual_seqs
    if missing_seqs:
        result.add_warning(
            f"Missing sequence numbers: {sorted(missing_seqs)}. "
            f"Chain may have been rebuilt with gaps."
        )

    for i, record in enumerate(sorted_records):
        prev_record = sorted_records[i - 1] if i > 0 else None

        if i > 0 and prev_record is not None:
            expected_prev_seq = record.sequence_number - 1
            if prev_record.sequence_number != expected_prev_seq:
                result.add_result({
                    "sequence_number": record.sequence_number,
                    "valid": False,
                    "reason": (
                        f"Sequence gap: prev is seq={prev_record.sequence_number}, "
                        f"expected seq={expected_prev_seq}"
                    ),
                })
                continue

        if record.sequence_number == 0:
            if record.previous_record_hash is not None:
                result.add_result({
                    "sequence_number": 0,
                    "valid": False,
                    "reason": "Genesis record must have previous_record_hash=null",
                })
            else:
                result.add_result({
                    "sequence_number": 0,
                    "valid": True,
                    "reason": "Genesis record",
                })
            continue

        if record.previous_record_hash is None:
            result.add_result({
                "sequence_number": record.sequence_number,
                "valid": False,
                "reason": "Non-genesis record missing previous_record_hash",
            })
            continue

        if prev_record is None:
            result.add_result({
                "sequence_number": record.sequence_number,
                "valid": False,
                "reason": "Previous record not provided",
            })
            continue

        prev_hash = compute_record_hash(prev_record)
        if prev_hash != record.previous_record_hash:
            result.add_result({
                "sequence_number": record.sequence_number,
                "valid": False,
                "reason": "Hash chain mismatch",
            })
        else:
            result.add_result({
                "sequence_number": record.sequence_number,
                "valid": True,
                "reason": "Chain link valid",
            })

    return result.to_dict()
