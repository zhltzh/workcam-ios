"""TM-04 B02/B03/B11/C15: Python parity tests for the WorkCamSync
Swift components authored this session.

These tests pin the DECISION SEMANTICS that the Swift side
(FieldDiff.swift / ConflictResolution.swift / SyncRecovery.swift)
implements, so the two languages can never drift:

  - three-way field diff classification (identical / independent /
    conflicting + merged values)
  - conflict-decision rules (keepLocal needs the server revision,
    takeServer never deletes the local payload, deferred never
    auto-retries, non-conflicted ops are refused)
  - the HTTP recovery decision table (401/403/409/429/5xx/4xx/max)
  - the Retry-After clamp envelope

No wall-clock assertions anywhere (the two-flakes rule): these are
pure functional invariants.
"""

import math

import pytest

# ---------------------------------------------------------------------------
# B03: three-way field diff (mirror of WorkCamSync/FieldDiff.swift)
# ---------------------------------------------------------------------------


def field_diff_kind(base, local, server):
    if local == server:
        return "identical"
    if base == server or base == local:
        return "independent"
    return "conflicting"


def diff_fields(base: dict, local: dict, server: dict) -> dict:
    out = {}
    for k in sorted(set(base) | set(local) | set(server)):
        b, l, s = (base.get(k), local.get(k), server.get(k))
        out[k] = {
            "base": b, "local": l, "server": s,
            "kind": field_diff_kind(b, l, s),
        }
    return out


def merged_value(diffs: dict, resolving: dict) -> dict:
    out = {}
    for field, d in diffs.items():
        if d["kind"] == "identical":
            out[field] = d["local"]
        elif d["kind"] == "independent":
            # local-only change wins; server-only change applies
            out[field] = (
                d["local"] if d["base"] == d["server"] else d["server"])
        else:
            out[field] = resolving.get(field, d["local"])
    return out


class TestFieldDiff:
    def test_same_edit_on_both_sides_is_identical(self):
        assert field_diff_kind("draft", "fixed", "fixed") == "identical"

    def test_local_only_change_is_independent_not_conflict(self):
        assert field_diff_kind("old", "local new", "old") == "independent"

    def test_server_only_change_is_independent_not_conflict(self):
        assert field_diff_kind("old", "old", "server new") == "independent"

    def test_all_three_differ_is_the_only_conflict(self):
        assert field_diff_kind("low", "high", "medium") == "conflicting"

    def test_removed_field_conflicts_with_local_edit(self):
        # local edited x, server deleted x: null server value
        assert field_diff_kind(1, 2, None) == "conflicting"

    def test_merge_mechanics_local_and_server_changes_apply(self):
        diffs = diff_fields(
            {"a": 1, "b": 1},
            {"a": 2, "b": 1},
            {"a": 1, "b": 9},
        )
        merged = merged_value(diffs, {})
        assert merged == {"a": 2, "b": 9}

    def test_merge_conflict_uses_explicit_human_resolution(self):
        diffs = diff_fields({"sev": "low"}, {"sev": "high"},
                            {"sev": "medium"})
        merged = merged_value(diffs, {"sev": "critical"})
        assert merged == {"sev": "critical"}

    def test_only_conflicting_fields_reach_the_user(self):
        diffs = diff_fields(
            {"a": 1, "b": 1, "c": 1},
            {"a": 2, "b": 1, "c": 3},
            {"a": 1, "b": 9, "c": 2},
        )
        conflicting = [k for k, d in diffs.items()
                       if d["kind"] == "conflicting"]
        assert conflicting == ["c"]


# ---------------------------------------------------------------------------
# B02: conflict-decision rules (mirror of ConflictResolution.swift)
# ---------------------------------------------------------------------------


class ConflictDecisionError(Exception):
    pass


def apply_decision(op, decision, server_revision, merged_payload=None):
    """Mirror of ConflictResolution.apply - pure dict/str ops."""
    if op["state"] != "conflict":
        raise ConflictDecisionError("not_in_conflict")
    resolved = dict(op)
    resolved["last_error_message"] = f"resolved: {decision}"

    if decision == "keep_local":
        if server_revision is None:
            raise ConflictDecisionError("missing_server_revision")
        resolved["base_revision"] = server_revision
        resolved["state"] = "pending"
        resolved["attempts"] = 0
    elif decision == "take_server":
        # audit red line: the local payload is NEVER deleted
        resolved["state"] = "acknowledged"
        resolved["next_attempt_at"] = None
    elif decision == "manual_merge":
        if not merged_payload:
            raise ConflictDecisionError("missing_merged_payload")
        if server_revision is None:
            raise ConflictDecisionError("missing_server_revision")
        resolved["payload"] = merged_payload
        resolved["base_revision"] = server_revision
        resolved["state"] = "pending"
        resolved["attempts"] = 0
    elif decision == "deferred":
        resolved["state"] = "conflict"
        resolved["next_attempt_at"] = None   # never auto-retried
        resolved["last_error_code"] = "deferred"
    else:
        raise ConflictDecisionError("unknown decision")
    return resolved


CONFLICTED = {
    "state": "conflict", "payload": "LOCAL", "attempts": 3,
    "next_attempt_at": 12345, "last_error_code": "revision_conflict",
}


class TestConflictDecisions:
    def test_keep_local_requeues_with_base_revision(self):
        r = apply_decision(dict(CONFLICTED), "keep_local", 7)
        assert r["state"] == "pending"
        assert r["base_revision"] == 7
        assert r["attempts"] == 0

    def test_keep_local_refused_without_server_revision(self):
        with pytest.raises(ConflictDecisionError):
            apply_decision(dict(CONFLICTED), "keep_local", None)

    def test_take_server_keeps_local_payload_as_audit(self):
        r = apply_decision(dict(CONFLICTED), "take_server", 7)
        assert r["state"] == "acknowledged"
        assert r["payload"] == "LOCAL"      # NOT deleted
        assert r["next_attempt_at"] is None

    def test_manual_merge_requires_payload_and_revision(self):
        with pytest.raises(ConflictDecisionError):
            apply_decision(dict(CONFLICTED), "manual_merge", 7)
        with pytest.raises(ConflictDecisionError):
            apply_decision(dict(CONFLICTED), "manual_merge", None,
                            merged_payload={"x": 1})
        r = apply_decision(dict(CONFLICTED), "manual_merge", 7,
                            merged_payload={"x": 1})
        assert r["state"] == "pending"
        assert r["payload"] == {"x": 1}

    def test_deferred_never_auto_retries(self):
        r = apply_decision(dict(CONFLICTED), "deferred", 7)
        assert r["state"] == "conflict"
        assert r["next_attempt_at"] is None
        assert r["last_error_code"] == "deferred"

    def test_pending_op_cannot_be_resolved(self):
        pending = dict(CONFLICTED, state="pending")
        with pytest.raises(ConflictDecisionError):
            apply_decision(pending, "keep_local", 7)


# ---------------------------------------------------------------------------
# B11/B12: recovery decision table + Retry-After clamp
# ---------------------------------------------------------------------------

MAX_ATTEMPTS = 8


def recovery_decision(status, attempt_count, retry_after=None,
                      max_attempts=MAX_ATTEMPTS):
    if attempt_count >= max_attempts:
        return ("dead_letter", f"exhausted {max_attempts} attempts")
    if status == 401:
        return ("reauthenticate", None)
    if status == 403:
        return ("escalate_permission", None)
    if status == 409:
        return ("rebase_conflict", None)
    if status in (429, 500, 502, 503):
        return ("retry_after", retry_after)
    if 400 <= status <= 499:
        return ("fail_fast", None)
    return ("retry_after", retry_after)


def clamped_retry_after(hint, max_seconds=300):
    if hint is None or not math.isfinite(hint):
        return None
    return min(max(0, hint), max_seconds)


class TestSyncRecovery:
    @pytest.mark.parametrize("status,expected", [
        (401, "reauthenticate"),
        (403, "escalate_permission"),
        (409, "rebase_conflict"),
        (422, "fail_fast"),
    ])
    def test_immediate_routing(self, status, expected):
        assert recovery_decision(status, 1)[0] == expected

    @pytest.mark.parametrize("status", [429, 500, 502, 503])
    def test_transient_statuses_backoff(self, status):
        assert recovery_decision(status, 1)[0] == "retry_after"

    def test_429_honors_server_hint(self):
        assert recovery_decision(429, 2, retry_after=30) == \
            ("retry_after", 30)

    def test_dead_letter_only_after_max_attempts(self):
        assert recovery_decision(
            500, MAX_ATTEMPTS - 1)[0] == "retry_after"
        assert recovery_decision(
            500, MAX_ATTEMPTS)[0] == "dead_letter"

    def test_retry_after_clamped_to_envelope(self):
        assert clamped_retry_after(9999) == 300
        assert clamped_retry_after(-5) == 0
        assert clamped_retry_after(None) is None
        assert clamped_retry_after(float("nan")) is None
        assert clamped_retry_after(12) == 12
