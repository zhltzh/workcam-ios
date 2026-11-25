"""B02-W028 - tenant isolation PoC tests (lab module, no app/ wiring).

Pins the security property under research: with the member model
(account -> workspace membership), a denied cross-subject read stays
404-IDENTICAL to a nonexistent resource (the discipline pinned for the
single-owner baseline by the W021 audit, checklist item 07), and
membership removal is instantly effective.
"""

from __future__ import annotations

import json

from techlab import tenant_isolation_poc as poc


def _lab():
    conn = poc.connect(":memory:")
    poc.create_lab(conn)
    # tenant T1: workspaces W1 (A1, A2 members) and W2 (A3 member)
    # tenant T2: workspace W3 (A4 member)   - full cross-tenant axis
    poc.add_tenant(conn, "T1")
    poc.add_tenant(conn, "T2")
    poc.add_workspace(conn, "W1", "T1")
    poc.add_workspace(conn, "W2", "T1")
    poc.add_workspace(conn, "W3", "T2")
    poc.add_project(conn, "P1", "W1")
    poc.add_project(conn, "P2", "W2")
    poc.add_project(conn, "P3", "W3")
    for acc in ("A1", "A2", "A3", "A4"):
        poc.add_account(conn, acc)
    poc.add_member(conn, "A1", "W1")
    poc.add_member(conn, "A2", "W1")
    poc.add_member(conn, "A3", "W2")
    poc.add_member(conn, "A4", "W3")
    poc.add_photo(conn, "PH1", "P1", "aa" * 32, 100)
    poc.add_photo(conn, "PH2", "P1", "bb" * 32, 200)
    poc.add_photo(conn, "PH3", "P2", "cc" * 32, 300)
    poc.add_photo(conn, "PH4", "P3", "dd" * 32, 400)
    return conn


def test_member_reads_own_workspace_photo():
    conn = _lab()
    body = json.loads(poc.get_photo(conn, "A1", "PH1"))
    assert body["photo"]["photo_id"] == "PH1"
    assert body["photo"]["sha256"] == "aa" * 32


def test_member_reads_shared_workspace_photo_of_other_account():
    """The member model exists to SHARE: A2 (also in W1) reads the
    photo registered by another account's project scope."""
    conn = _lab()
    body = json.loads(poc.get_photo(conn, "A2", "PH2"))
    assert body["photo"]["photo_id"] == "PH2"


def test_viewer_role_can_read():
    conn = _lab()
    poc.remove_member(conn, "A1", "W1")
    poc.add_member(conn, "A1", "W1", role="viewer")
    body = json.loads(poc.get_photo(conn, "A1", "PH1"))
    assert body["photo"]["photo_id"] == "PH1"


def test_cross_workspace_same_tenant_is_404_identical():
    conn = _lab()
    denied = poc.get_photo(conn, "A1", "PH3")      # W2, same tenant
    unknown = poc.get_photo(conn, "A1", "no-such-photo")
    assert denied == unknown == poc.not_found_json()


def test_cross_tenant_is_404_identical():
    conn = _lab()
    denied = poc.get_photo(conn, "A1", "PH4")      # tenant T2
    assert denied == poc.not_found_json()


def test_denied_read_is_byte_identical_to_nonexistent():
    conn = _lab()
    unknown = poc.get_photo(conn, "A1", "no-such-photo")
    cross_ws = poc.get_photo(conn, "A1", "PH3")
    cross_tenant = poc.get_photo(conn, "A1", "PH4")
    # byte-identical (string equality on the serialized envelope)
    assert unknown == cross_ws == cross_tenant
    # and identical to the canonical envelope
    assert json.loads(unknown)["error"]["code"] == "not_found"


def test_membership_removal_is_instant():
    conn = _lab()
    assert json.loads(poc.get_photo(conn, "A2", "PH1"))["photo"]["photo_id"] == "PH1"
    poc.remove_member(conn, "A2", "W1")
    assert poc.get_photo(conn, "A2", "PH1") == poc.not_found_json()
    assert poc.get_photo(conn, "A2", "no-such") == poc.not_found_json()


def test_membership_rejoin_restores_access():
    conn = _lab()
    poc.remove_member(conn, "A2", "W1")
    poc.add_member(conn, "A2", "W1")
    body = json.loads(poc.get_photo(conn, "A2", "PH1"))
    assert body["photo"]["photo_id"] == "PH1"


def test_list_scoped_to_memberships_no_leak():
    conn = _lab()
    listing = json.loads(poc.list_photos(conn, "A1"))
    assert [p["photo_id"] for p in listing["photos"]] == ["PH1", "PH2"]
    # A3 must not see W1 photos in list form either
    listing = json.loads(poc.list_photos(conn, "A3"))
    assert [p["photo_id"] for p in listing["photos"]] == ["PH3"]
    # removed member's list is empty, not "hidden rows"
    poc.remove_member(conn, "A3", "W2")
    assert json.loads(poc.list_photos(conn, "A3"))["photos"] == []


def test_no_existence_oracle_via_counts():
    """The number of rows an account can list depends only on its own
    memberships, never on other tenants' data volumes."""
    conn = _lab()
    before = len(json.loads(poc.list_photos(conn, "A1"))["photos"])
    poc.add_photo(conn, "PH9", "P3", "ee" * 32, 500)   # T2 grows
    after = len(json.loads(poc.list_photos(conn, "A1"))["photos"])
    assert before == after == 2
