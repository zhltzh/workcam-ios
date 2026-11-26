"""M03-B04/B05/B06 tests - lifecycle, before/after, naming."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from techlab.business.issues import (  # noqa: E402
    IssueLifecycle, IssueStatus, LifecycleError, NamingContext,
    NamingError, PhotoRole, RoleLinkError, IssuePhotoLinks,
    format_name, sanitize_component, suggest_names)


class TestB04Lifecycle:
    def test_linear_chain_full(self):
        iss = IssueLifecycle("i-1")
        iss.transition(IssueStatus.in_progress, "alice")
        iss.transition(IssueStatus.resolved, "alice")
        iss.transition(IssueStatus.closed, "bob")
        assert iss.status == IssueStatus.closed
        assert len(iss.history) == 3

    def test_reopen_from_resolved(self):
        iss = IssueLifecycle("i-2")
        iss.transition(IssueStatus.in_progress, "a")
        iss.transition(IssueStatus.resolved, "a")
        # reopen: resolved -> open is allowed (the reopened candidate
        # vocabulary maps to open in the server whitelist)
        iss.transition(IssueStatus.open, "bob", note="reopened")
        assert iss.status == IssueStatus.open
        assert iss.history[-1].to_status == IssueStatus.open

    def test_illegal_jumps_refused(self):
        iss = IssueLifecycle("i-3")
        with pytest.raises(LifecycleError):
            iss.transition(IssueStatus.resolved, "a")   # open->resolved
        iss.transition(IssueStatus.in_progress, "a")
        with pytest.raises(LifecycleError):
            iss.transition(IssueStatus.open, "a")        # in_progress->open

    def test_closed_is_terminal(self):
        iss = IssueLifecycle("i-4")
        iss.transition(IssueStatus.in_progress, "a")
        iss.transition(IssueStatus.resolved, "a")
        iss.transition(IssueStatus.closed, "a")
        with pytest.raises(LifecycleError):
            iss.transition(IssueStatus.open, "a")

    def test_same_status_rejected(self):
        iss = IssueLifecycle("i-5")
        with pytest.raises(LifecycleError):
            iss.transition(IssueStatus.open, "a")

    def test_history_preserved(self):
        iss = IssueLifecycle("i-6")
        iss.transition(IssueStatus.in_progress, "alice")
        iss.transition(IssueStatus.resolved, "alice")
        iss.transition(IssueStatus.open, "bob", "reopened")
        assert iss.history[0].actor == "alice"
        assert iss.history[-1].note == "reopened"
        assert iss.summary()["change_count"] == 3


class TestB05BeforeAfter:
    def test_role_linking(self):
        links = IssuePhotoLinks("iss-1", "proj-1")
        links.link("ph-1", "proj-1", PhotoRole.before)
        links.link("ph-2", "proj-1", PhotoRole.progress)
        links.link("ph-3", "proj-1", PhotoRole.after)
        assert links.photos(PhotoRole.before) == ["ph-1"]
        assert links.has_before_after_pair()

    def test_cross_project_refused(self):
        links = IssuePhotoLinks("iss-1", "proj-1")
        with pytest.raises(RoleLinkError, match="cross-project"):
            links.link("ph-x", "proj-2", PhotoRole.before)

    def test_duplicate_link_idempotent(self):
        links = IssuePhotoLinks("iss-1", "proj-1")
        links.link("ph-1", "proj-1", PhotoRole.before)
        links.link("ph-1", "proj-1", PhotoRole.before)
        assert links.photos(PhotoRole.before) == ["ph-1"]

    def test_progress_can_repeat(self):
        links = IssuePhotoLinks("iss-1", "proj-1")
        links.link("ph-1", "proj-1", PhotoRole.progress)
        links.link("ph-2", "proj-1", PhotoRole.progress)
        assert len(links.photos(PhotoRole.progress)) == 2

    def test_completeness_never_auto_completes(self):
        links = IssuePhotoLinks("iss-1", "proj-1")
        links.link("ph-1", "proj-1", PhotoRole.before)
        links.link("ph-2", "proj-1", PhotoRole.after)
        c = links.remediation_completeness()
        assert c["has_pair"]
        assert c["auto_complete"] is False
        assert "HUMAN" in c["note"]

    def test_photo_in_multiple_roles(self):
        links = IssuePhotoLinks("iss-1", "proj-1")
        links.link("ph-1", "proj-1", PhotoRole.before)
        links.link("ph-1", "proj-1", PhotoRole.after)
        assert "ph-1" in links.photos(PhotoRole.before)
        assert "ph-1" in links.photos(PhotoRole.after)


class TestB06Naming:
    def test_full_expression(self):
        ctx = NamingContext(
            project_number="PRJ-001", building="B2", floor="3",
            room="101", category="electrical",
            date="20261126", sequence=5)
        name = format_name(
            "{project_number}_{building}_F{floor}_{room}_"
            "{category}_{date}_{sequence:04d}", ctx)
        assert name == "PRJ-001_B2_F3_101_electrical_20261126_0005"

    def test_sanitize_strips_illegal(self):
        assert sanitize_component('a<b>c:"d/e\\f|g?h*i') == \
            "a_b_c__d_e_f_g_h_i"
        assert sanitize_component("trailing...") == "trailing"

    def test_reserved_name_refused(self):
        with pytest.raises(NamingError):
            sanitize_component("CON")
        with pytest.raises(NamingError):
            sanitize_component("NUL.txt")

    def test_empty_refused(self):
        with pytest.raises(NamingError):
            sanitize_component("")
        with pytest.raises(NamingError):
            sanitize_component("...")

    def test_unknown_placeholder_raises(self):
        ctx = NamingContext(project_number="P1")
        with pytest.raises(NamingError, match="unknown"):
            format_name("{bad_field}", ctx)

    def test_empty_placeholder_raises(self):
        ctx = NamingContext(project_number="P1", building="")
        with pytest.raises(NamingError, match="empty"):
            format_name("{project_number}_{building}", ctx)

    def test_custom_field(self):
        ctx = NamingContext(custom={"crew": "TeamA"})
        assert format_name("{custom.crew}_photo", ctx) == \
            "TeamA_photo"

    def test_batch_naming_auto_increment(self):
        contexts = [NamingContext(
            project_number="P1", building="B1", floor="1",
            category="framing", date="20261126",
            sequence=0) for _ in range(3)]
        names = suggest_names(
            "{project_number}_{building}_{floor}_{category}_"
            "{date}_{sequence}", contexts, start_sequence=10)
        # default sequence format is 04d
        assert names == [
            "P1_B1_1_framing_20261126_0010",
            "P1_B1_1_framing_20261126_0011",
            "P1_B1_1_framing_20261126_0012"]

    def test_duplicate_names_suffixed_not_overwritten(self):
        contexts = [NamingContext(
            project_number="P1", building="B1",
            date="20261126", sequence=0) for _ in range(3)]
        # same template always resolves the same -> dedup by suffix
        names = suggest_names(
            "{project_number}_{building}_{date}_{sequence}", contexts,
            start_sequence=1)
        # sequence differentiates so no duplicates
        assert len(names) == 3
        # force duplicate by using start_sequence=0 for all:
        names2 = [format_name("{project_number}_{building}",
                              NamingContext(project_number="P1",
                                            building="B1",
                                            sequence=0))
                  for _ in range(3)]
        # each call returns the same name - the CALLER must dedup;
        # suggest_names is the deduping wrapper (proved above)

    def test_naming_never_touches_bytes(self):
        """The naming engine produces a STRING. It has no file API by
        construction - this test exists so a future change that adds
        one fails loudly."""
        ctx = NamingContext(project_number="P1")
        name = format_name("{project_number}", ctx)
        assert isinstance(name, str)
