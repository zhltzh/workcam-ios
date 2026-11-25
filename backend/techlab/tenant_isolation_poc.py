"""B02-W028 tenant isolation PoC (isolated lab module).

Research question: when data ownership evolves from "single device
token = owner" (the 31917e3 baseline, see app/api/identity.py) to an
account + membership model (tenant -> workspace -> project), does the
404-identical cross-subject isolation discipline (pinned by the W021
audit checklist item 07, docs/security/b02-w021-audit-2026-11-25.md on
branch dev/windows-b02-w021-security) still hold under the member
model?

Scope boundary (deliberately narrow):
- NOT wired into app/ - no production route imports this module; it
  does not touch app/db.py MIGRATIONS or the production schema.
- Own minimal SQLite schema (PoC-only), own connection helper.
- The only production discipline it re-implements is the response
  shape: a denied cross-subject read is byte-identical to a read of a
  nonexistent resource (no existence oracle).

Minimal model:
    tenants        (org, the future multi-tenant root)
    workspaces     (team workspace, belongs to a tenant)
    projects       (belongs to a workspace; mirrors mvp_projects)
    accounts       (user identity; the future owner of a device token)
    memberships    (account <-> workspace, role + status; the
                    "project member" evolution point)
    photos         (belongs to a project; mirrors mvp_photos)

Access rule implemented here (read path):
    account may read photo  <=>  account has an ACTIVE membership in
    the workspace that owns the photo's project.
Every other outcome - other workspace (same tenant), other tenant,
unknown photo id - returns the SAME not-found envelope.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE tenants (
    tenant_id   TEXT PRIMARY KEY,
    name        TEXT NOT NULL
);
CREATE TABLE workspaces (
    workspace_id TEXT PRIMARY KEY,
    tenant_id    TEXT NOT NULL REFERENCES tenants(tenant_id),
    name         TEXT NOT NULL
);
CREATE TABLE projects (
    project_id   TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES workspaces(workspace_id),
    name         TEXT NOT NULL
);
CREATE TABLE accounts (
    account_id   TEXT PRIMARY KEY,
    display_name TEXT NOT NULL
);
CREATE TABLE memberships (
    account_id    TEXT NOT NULL REFERENCES accounts(account_id),
    workspace_id  TEXT NOT NULL REFERENCES workspaces(workspace_id),
    role          TEXT NOT NULL CHECK (role IN ('owner', 'member',
                                                'viewer')),
    status        TEXT NOT NULL DEFAULT 'active' CHECK (status IN
                     ('active', 'removed')),
    created_at    TEXT NOT NULL,
    removed_at    TEXT,
    PRIMARY KEY (account_id, workspace_id)
);
CREATE TABLE photos (
    photo_id  TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(project_id),
    sha256    TEXT NOT NULL,
    byte_size INTEGER NOT NULL CHECK (byte_size > 0)
);
CREATE INDEX idx_photos_project ON photos(project_id);
"""

# The single not-found envelope. Identity of the object itself makes
# the 404-identical discipline explicit: there is exactly ONE way a
# denied or missing read looks, so nothing about the reason leaks.
NOT_FOUND_ENVELOPE = {
    "error": {"code": "not_found",
              "message": "resource not found or not accessible",
              "details": {}}
}


def not_found_json() -> str:
    """The exact response body a denied read MUST produce."""
    return json.dumps(NOT_FOUND_ENVELOPE, sort_keys=True)


def connect(db_path: Path | str | None = None) -> sqlite3.Connection:
    """Open (creating if needed) the PoC database with the same
    per-connection pragmas the production layer uses (app/db.py):
    WAL + foreign_keys=ON."""
    conn = sqlite3.connect(str(db_path) if db_path else ":memory:",
                           isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def create_lab(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)


def add_tenant(conn, tenant_id: str, name: str = "tenant") -> None:
    conn.execute("INSERT INTO tenants (tenant_id, name) VALUES (?,?)",
                 (tenant_id, name))


def add_workspace(conn, workspace_id: str, tenant_id: str,
                  name: str = "workspace") -> None:
    conn.execute("INSERT INTO workspaces (workspace_id, tenant_id, name) "
                 "VALUES (?,?,?)", (workspace_id, tenant_id, name))


def add_project(conn, project_id: str, workspace_id: str,
                name: str = "project") -> None:
    conn.execute("INSERT INTO projects (project_id, workspace_id, name) "
                 "VALUES (?,?,?)", (project_id, workspace_id, name))


def add_account(conn, account_id: str, display_name: str = "account") -> None:
    conn.execute("INSERT INTO accounts (account_id, display_name) "
                 "VALUES (?,?)", (account_id, display_name))


def add_member(conn, account_id: str, workspace_id: str,
                role: str = "member", created_at: str = "2026-11-25T00:00:00+00:00") -> None:
    """Upsert: re-joining after a removal RE-ACTIVATES the same
    membership row (keeps history in created_at/removed_at columns;
    the PoC model does not need a separate history table)."""
    conn.execute(
        "INSERT INTO memberships (account_id, workspace_id, role, "
        "status, created_at) VALUES (?,?,?, 'active', ?) "
        "ON CONFLICT (account_id, workspace_id) DO UPDATE SET "
        "role=excluded.role, status='active', "
        "created_at=excluded.created_at, removed_at=NULL",
        (account_id, workspace_id, role, created_at))


def remove_member(conn, account_id: str, workspace_id: str,
                  removed_at: str = "2026-11-25T00:00:00+00:00") -> None:
    """Membership removal is INSTANT and TOTAL (mirrors the device
    revocation discipline, W021 checklist item 03): the very next read
    through the access layer is denied, no token or cache to expire."""
    conn.execute("UPDATE memberships SET status='removed', removed_at=? "
                 "WHERE account_id=? AND workspace_id=?",
                 (removed_at, account_id, workspace_id))


def add_photo(conn, photo_id: str, project_id: str, sha256: str,
              byte_size: int = 1234) -> None:
    conn.execute("INSERT INTO photos (photo_id, project_id, sha256, "
                 "byte_size) VALUES (?,?,?,?)",
                 (photo_id, project_id, sha256, byte_size))


def resolve_access(conn: sqlite3.Connection, account_id: str,
                   photo_id: str) -> sqlite3.Row | None:
    """THE member-filter gate (PoC form of require_caller + the
    ownership check). Returns the membership row when the account may
    read the photo, None otherwise. The caller NEVER learns WHY."""
    return conn.execute(
        """
        SELECT m.role, ph.photo_id
        FROM photos ph
        JOIN projects p  ON p.project_id = ph.project_id
        JOIN workspaces w ON w.workspace_id = p.workspace_id
        JOIN memberships m ON m.workspace_id = w.workspace_id
        WHERE ph.photo_id = ?
          AND m.account_id = ?
          AND m.status = 'active'
        """,
        (photo_id, account_id)).fetchone()


def get_photo(conn: sqlite3.Connection, account_id: str,
              photo_id: str) -> str:
    """Simulated API read. Three outcomes collapse into ONE envelope:
    unknown photo / other workspace / other tenant / not a member."""
    row = resolve_access(conn, account_id, photo_id)
    if row is None:
        return not_found_json()
    photo = conn.execute(
        "SELECT photo_id, project_id, sha256, byte_size FROM photos "
        "WHERE photo_id = ?", (photo_id,)).fetchone()
    return json.dumps({"photo": dict(photo)}, sort_keys=True)


def list_photos(conn: sqlite3.Connection, account_id: str) -> str:
    """Simulated API list. Scoped STRICTLY to the account's active
    memberships - the row-level tenant filter. Non-member rows never
    appear (and their existence is not leaked via counts)."""
    rows = conn.execute(
        """
        SELECT ph.photo_id, ph.project_id, ph.sha256
        FROM photos ph
        JOIN projects p ON p.project_id = ph.project_id
        JOIN workspaces w ON w.workspace_id = p.workspace_id
        JOIN memberships m ON m.workspace_id = w.workspace_id
        WHERE m.account_id = ? AND m.status = 'active'
        ORDER BY ph.photo_id
        """,
        (account_id,)).fetchall()
    return json.dumps({"photos": [dict(r) for r in rows]},
                      sort_keys=True)
