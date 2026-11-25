"""TM05: audit chain tamper detection + honest truncation boundary."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"
                        / "evidence_chain"))

import audit_chain as ac  # noqa: E402

import pytest  # noqa: E402


def _rec(seq, prev, rec_hash):
    return {"sequence_number": seq, "previous_record_hash": prev,
            "record_hash": rec_hash}


def _chain_ok(n=5):
    """A consistent chain: each record links to the previous hash."""
    import hashlib
    chain = []
    prev = "none"
    for i in range(n):
        h = hashlib.sha256(f"rec-{i}-{prev}".encode()).hexdigest()
        chain.append(_rec(i, prev, h))
        prev = h
    return chain


def test_valid_chain_passes_and_head_reported():
    chain = _chain_ok(5)
    r = ac.verify_chain(chain)
    assert r.ok, r.problems
    assert r.records == 5
    assert r.head_hash == chain[-1]["record_hash"]


def test_interior_deletion_detected():
    chain = _chain_ok(6)
    del chain[3]                      # seq 3 removed
    r = ac.verify_chain(chain)
    assert not r.ok
    assert any(p.code == "seq_gap" and p.sequence_number == 3
               for p in r.problems)


def test_replacement_detected_via_link_mismatch():
    chain = _chain_ok(5)
    chain[2]["record_hash"] = "f" * 64     # tamper ONE hash, no relink
    r = ac.verify_chain(chain)
    assert not r.ok
    assert any(p.code == "link_mismatch" for p in r.problems)
    # NOTE (honest boundary #2): an attacker who re-links EVERYTHING
    # downstream can make the local chain consistent again - that is
    # exactly why signatures over each record (and the external
    # checkpoint) exist; local links alone are not the final defense.


def test_insertion_detected():
    chain = _chain_ok(5)
    # attacker injects a record at seq 2 re-linking the rest
    injected = _rec(2, chain[1]["record_hash"], "e" * 64)
    chain[2]["previous_record_hash"] = "e" * 64
    chain.insert(2, injected)
    r = ac.verify_chain(chain)
    assert not r.ok
    assert any(p.code in ("seq_fork", "link_mismatch", "seq_gap")
               for p in r.problems)


def test_fork_detected():
    chain = _chain_ok(5)
    dup = dict(chain[2], record_id="clone")
    chain.append(dup)
    r = ac.verify_chain(chain)
    assert not r.ok
    assert any(p.code == "seq_fork" for p in r.problems)


def test_origin_must_link_none():
    chain = _chain_ok(5)
    chain[0]["previous_record_hash"] = "a" * 64
    r = ac.verify_chain(chain)
    assert not r.ok
    assert any(p.code == "origin_mismatch" for p in r.problems)


def test_merkle_root_and_proofs():
    leaves = [f"{i:064x}" for i in range(7)]
    root = ac.merkle_root(leaves)
    assert root and len(root) == 64
    for i in range(7):
        proof = ac.merkle_proof(leaves, i)
        assert ac.verify_merkle_proof(leaves[i], proof, root)
    # a foreign leaf cannot use someone else's proof
    assert not ac.verify_merkle_proof("f" * 64,
                                      ac.merkle_proof(leaves, 3), root)
    # single-leaf tree: root is the leaf itself
    assert ac.merkle_root(["ab"]) == ac._h(b"ab").hex()


# ---------------------------------------------- the honest tail boundary


def test_tail_truncation_is_locally_undetectable_then_checkpointed():
    """THE core TM05 finding, demonstrated:

    1. Truncate the last 2 records -> the shortened chain is FULLY
       consistent: no local verifier can detect the loss.
    2. A signed checkpoint (external anchor) makes the SAME truncation
       detectable by name.
    """
    full = _chain_ok(8)
    r_full = ac.verify_chain(full)
    assert r_full.ok

    attacker_view = full[:6]              # last 2 records deleted
    r_attacker = ac.verify_chain(attacker_view)
    assert r_attacker.ok, \
        "a truncated LOCAL chain must look perfectly consistent - " \
        "this is why local chains cannot prove non-deletion"
    assert ac.ChainReport(ok=True).tail_truncation_risk.startswith(
        "LOCAL-CHAIN BOUNDARY")

    # now anchor with an external checkpoint BEFORE the attack
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec
    key = ec.generate_private_key(ec.SECP256R1())
    cp = ac.build_checkpoint(r_full.head_hash, len(full), lambda b: key.sign(b, ec.ECDSA(hashes.SHA256())))

    def verify_sig(body, sig):
        try:
            key.public_key().verify(sig, body, ec.ECDSA(hashes.SHA256()))
            return True
        except Exception:
            return False

    # full chain against the checkpoint: clean
    ok_report = ac.verify_checkpoint(cp, full, verify_sig)
    assert ok_report["ok"], ok_report
    # truncated chain against the SAME checkpoint: named detection
    bad_report = ac.verify_checkpoint(cp, attacker_view, verify_sig)
    assert not bad_report["ok"]
    assert any("TRUNCATION DETECTED" in p
               for p in bad_report["problems"])
    # tampered checkpoint signature: refused
    forged = dict(cp, signature_b64=cp["signature_b64"][:-4] + "AAAA")
    forged_report = ac.verify_checkpoint(forged, full, verify_sig)
    assert not forged_report["ok"]
    assert "checkpoint_signature_invalid" in forged_report["problems"]


def test_checkpoint_count_covers_missing_head_case():
    """Even when records lack precomputed hashes, the COUNT binding
    alone catches truncation (defense in depth)."""
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec
    key = ec.generate_private_key(ec.SECP256R1())
    full = _chain_ok(4)
    r = ac.verify_chain(full)
    cp = ac.build_checkpoint(r.head_hash, 4, lambda b: key.sign(b, ec.ECDSA(hashes.SHA256())))

    def verify_sig(body, sig):
        try:
            key.public_key().verify(sig, body, ec.ECDSA(hashes.SHA256()))
            return True
        except Exception:
            return False

    recs_no_hash = [{k: v for k, v in rec.items() if k != "record_hash"}
                    for rec in full[:2]]
    rep = ac.verify_checkpoint(cp, recs_no_hash, verify_sig)
    assert not rep["ok"]
    assert any("TRUNCATION DETECTED" in p for p in rep["problems"])
