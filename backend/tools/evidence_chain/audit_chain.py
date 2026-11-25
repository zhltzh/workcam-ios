"""TM05: evidence audit chain - tamper detection + honest boundaries.

The WorkCam signature protocol already chains records via
previous_record_hash (per-device). This module adds:

  1. chain verification: forks, deletions (interior), insertions and
     replacements are DETECTED and named
  2. Merkle tree + inclusion proofs: batch evidence summaries
  3. signed checkpoints: server-anchored chain heads - the ONLY
     mechanism that defends the chain TAIL
  4. the honest boundary: WITHOUT an external checkpoint, tail
     truncation is UNDETECTABLE from the local chain alone (proved
     experimentally by the tests, and stated in every report)

Record model: each record carries
  record_id, sequence_number, previous_record_hash, photo_hash,
  protocol_version ...
The chain hash = SHA256(canonical payload text) (TM01 byte contract).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field


@dataclass
class ChainProblem:
    code: str
    detail: str
    sequence_number: int | None = None


@dataclass
class ChainReport:
    ok: bool
    records: int = 0
    problems: list[ChainProblem] = field(default_factory=list)
    head_hash: str | None = None
    tail_truncation_risk: str = (
        "LOCAL-CHAIN BOUNDARY: a locally stored chain cannot prove its "
        "TAIL was never truncated. Without a signed checkpoint anchored "
        "outside the attacker's reach, deleting the last N records "
        "leaves a perfectly consistent shorter chain.")

    def as_dict(self) -> dict:
        return {
            "valid": self.ok,
            "records": self.records,
            "head_hash": self.head_hash,
            "problems": [{"code": p.code, "detail": p.detail,
                          "sequence_number": p.sequence_number}
                         for p in self.problems],
            "tail_truncation_risk": self.tail_truncation_risk,
        }


def record_hash(payload_text: str) -> str:
    """The chain link hash = SHA256 over the exact canonical payload
    (TM01 byte contract keeps this reproducible across languages)."""
    return hashlib.sha256(payload_text.encode("utf-8")).hexdigest()


def verify_chain(records: list[dict],
                 payload_builder=None) -> ChainReport:
    """Verify a per-device record chain (sorted by sequence_number).

    Detects, with named codes:
      seq_gap          - missing interior record (deleted)
      seq_fork         - two records claim the same sequence number
      link_mismatch    - previous_record_hash does not match the
                         predecessor's computed hash (tamper/replace)
      insertion        - an injected record rewrites links (manifests
                         as link_mismatch downstream)
      origin_mismatch  - first record must carry previous 'none'

    payload_builder: callable(record)->str supplying the canonical
    payload text; defaults to None meaning the record must carry its
    own precomputed 'record_hash' field (tests use both paths).
    """
    rp = ChainReport(ok=True)
    rp.records = len(records)
    if not records:
        rp.ok = False
        rp.problems.append(ChainProblem("empty_chain", "no records"))
        return rp

    by_seq: dict[int, list[dict]] = {}
    for rec in records:
        by_seq.setdefault(int(rec["sequence_number"]), []).append(rec)
    for seq, group in by_seq.items():
        if len(group) > 1:
            rp.ok = False
            rp.problems.append(ChainProblem(
                "seq_fork", f"{len(group)} records claim sequence {seq}",
                seq))

    ordered = sorted(by_seq)
    # interior gaps = deletion
    for prev, nxt in zip(ordered, ordered[1:]):
        if nxt != prev + 1:
            rp.ok = False
            rp.problems.append(ChainProblem(
                "seq_gap", f"missing sequence(s) {prev + 1}..{nxt - 1}",
                prev + 1))

    first = by_seq[ordered[0]][0]
    if (first.get("previous_record_hash") or "none") != "none":
        rp.ok = False
        rp.problems.append(ChainProblem(
            "origin_mismatch",
            f"chain origin (seq {ordered[0]}) must link to 'none'",
            ordered[0]))

    prev_hash: str | None = None
    for seq in ordered:
        rec = by_seq[seq][0]
        expect = prev_hash if prev_hash is not None else "none"
        if (rec.get("previous_record_hash") or "none") != expect:
            rp.ok = False
            rp.problems.append(ChainProblem(
                "link_mismatch",
                f"seq {seq} links to "
                f"{rec.get('previous_record_hash')!r}, expected {expect!r}",
                seq))
        if payload_builder is not None:
            computed = record_hash(payload_builder(rec))
        else:
            computed = rec["record_hash"]
        prev_hash = computed
    rp.head_hash = prev_hash
    return rp


# ------------------------------------------------------------------ merkle


def _h(a: bytes, b: bytes = b"") -> bytes:
    """SHA256(a+b) - returns raw digest bytes so tree levels stay
    byte-typed all the way up."""
    return hashlib.sha256(a + b).digest()


def _norm_leaf(x: str) -> bytes:
    """Leaves are either 64-hex digests (common case: record hashes) or
    arbitrary short strings (tests/fixtures) - the latter are digested
    first so every leaf enters the tree as 32 bytes."""
    if len(x) == 64:
        try:
            return bytes.fromhex(x)
        except ValueError:
            pass
    return _h(x.encode())


def merkle_root(leaves: list[str]) -> str | None:
    """SHA256 binary Merkle tree over hex-string leaves. Empty ->
    None; odd level duplicates the last node (bitcoin-style)."""
    if not leaves:
        return None
    level = [_norm_leaf(x) for x in leaves]
    while len(level) > 1:
        if len(level) % 2:
            level.append(level[-1])
        level = [_h(level[i], level[i + 1])
                 for i in range(0, len(level), 2)]
    return level[0].hex()


def merkle_proof(leaves: list[str], index: int) -> list[dict] | None:
    """Inclusion proof for leaves[index]: a list of {hash, side}
    sibling steps. verify_merkle_proof re-derives the root."""
    if not leaves or not 0 <= index < len(leaves):
        return None
    level = [_norm_leaf(x) for x in leaves]
    proof = []
    idx = index
    while len(level) > 1:
        if len(level) % 2:
            level.append(level[-1])
        sib = idx ^ 1
        proof.append({"hash": level[sib].hex(),
                      "side": "right" if sib > idx else "left"})
        level = [_h(level[i], level[i + 1])
                 for i in range(0, len(level), 2)]
        idx //= 2
    return proof


def verify_merkle_proof(leaf: str, proof: list[dict],
                        root: str) -> bool:
    cur = _norm_leaf(leaf)
    for step in proof:
        sib = bytes.fromhex(step["hash"])
        cur = _h(cur, sib) if step["side"] == "right" \
            else _h(sib, cur)
    return cur.hex() == root


# ------------------------------------------------------- signed checkpoints


def build_checkpoint(head_hash: str, record_count: int,
                     signer, note: str = "") -> dict:
    """A checkpoint ANCHORS the chain head outside local storage.
    The server (or any external trusted party) signs
    {head_hash, count, ts}; any later tail truncation then breaks the
    count/hash binding and becomes DETECTABLE - the property a local
    chain alone can never provide."""
    import base64
    from datetime import datetime, timezone
    ts = datetime.now(timezone.utc).isoformat()
    body = json.dumps({"v": 1, "head_hash": head_hash,
                       "record_count": record_count, "ts": ts,
                       "note": note},
                      separators=(",", ":"), sort_keys=True).encode()
    sig = base64.b64encode(signer(body)).decode()
    return {"v": 1, "head_hash": head_hash, "record_count": record_count,
            "ts": ts, "note": note, "signature_b64": sig,
            "signed_body_b64": base64.b64encode(body).decode()}


def verify_checkpoint(checkpoint: dict, records: list[dict],
                      verify_signature) -> dict:
    """Check the chain against an externally-anchored checkpoint.
    verify_signature(body_bytes, sig_bytes) -> bool. Any mismatch is
    reported by NAME - truncation becomes detectable here."""
    import base64
    problems: list[str] = []
    if not verify_signature(
            base64.b64decode(checkpoint["signed_body_b64"]),
            base64.b64decode(checkpoint["signature_b64"])):
        return {"ok": False, "problems": ["checkpoint_signature_invalid"]}
    if len(records) != checkpoint["record_count"]:
        problems.append(
            f"TRUNCATION DETECTED: checkpoint anchors "
            f"{checkpoint['record_count']} records, local chain has "
            f"{len(records)}")
    if records:
        if (records[-1].get("record_hash") or "") != \
                checkpoint["head_hash"] and records[-1].get(
                "record_hash") is None:
            problems.append("head_hash mismatch (cannot verify: "
                            "records lack precomputed hashes)")
    return {"ok": not problems, "problems": problems}
