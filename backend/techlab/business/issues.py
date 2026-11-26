"""M03-B04/B05/B06: issue lifecycle, before/after management, naming.

B04: issue lifecycle (open/in_progress/resolved/closed + reopened
     candidate; Night 05 status vocabulary; history preservation).
B05: before/progress/after photo management (roles, human-only
     linking, cross-project rejection, NEVER auto-resolving).
B06: engineering photo naming rules (template expressions, safe
     filenames, duplicate handling; NEVER touching original bytes).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum


# ================================================== B04: issue lifecycle

class IssueStatus(str, Enum):
    """Night 05-compatible vocabulary (m03-a06 contract)."""
    open = "open"
    in_progress = "in_progress"
    resolved = "resolved"
    closed = "closed"
    reopened = "reopened"          # candidate state (protocol change
    #                              # is DECISION_REQUIRED; engine
    #                              # accepts it but server transition
    #                              # whitelist still lacks it)


# The LINEAR chain Night 05 enforces; the engine maps it, plus the
# reopened candidate that maps to a reopen path (resolved -> open).
_ALLOWED_TRANSITIONS: dict[IssueStatus, set[IssueStatus]] = {
    IssueStatus.open: {IssueStatus.in_progress, IssueStatus.closed},
    IssueStatus.in_progress: {IssueStatus.resolved},
    IssueStatus.resolved: {IssueStatus.closed, IssueStatus.open},
    IssueStatus.closed: set(),                 # terminal
    IssueStatus.reopened: {IssueStatus.in_progress},
}


class LifecycleError(ValueError):
    """Named refusal - never a silent status jump."""


@dataclass(frozen=True)
class StatusChange:
    timestamp: str
    from_status: IssueStatus
    to_status: IssueStatus
    actor: str
    note: str = ""


@dataclass
class IssueLifecycle:
    """Client-side lifecycle engine (the server enforces the same via
    issue_transition CAS; this adds the history log and vocabulary
    validation the client needs BEFORE hitting the API)."""
    issue_id: str
    status: IssueStatus = IssueStatus.open
    history: list[StatusChange] = field(default_factory=list)
    responsible_party: str = ""
    due_date: str | None = None

    def transition(self, new_status: IssueStatus, actor: str,
                   note: str = "") -> None:
        if new_status == self.status:
            raise LifecycleError(
                f"issue {self.issue_id}: already {self.status.value}")
        allowed = _ALLOWED_TRANSITIONS.get(self.status, set())
        if new_status not in allowed:
            raise LifecycleError(
                f"issue {self.issue_id}: {self.status.value} -> "
                f"{new_status.value} is not an allowed transition "
                f"(allowed: {sorted(s.value for s in allowed)})")
        self.history.append(StatusChange(
            timestamp=datetime.now(timezone.utc).isoformat(),
            from_status=self.status, to_status=new_status,
            actor=actor, note=note))
        self.status = new_status

    def can_close(self) -> bool:
        """Closed is only reachable from resolved (or open for
        trivial issues) - the caller decides, never the engine."""
        return IssueStatus.closed in \
            _ALLOWED_TRANSITIONS.get(self.status, set())

    def summary(self) -> dict:
        return {
            "issue_id": self.issue_id,
            "status": self.status.value,
            "responsible_party": self.responsible_party,
            "due_date": self.due_date,
            "change_count": len(self.history),
        }


# ==================================== B05: before/progress/after photos

class PhotoRole(str, Enum):
    before = "before"
    progress = "progress"
    after = "after"


class RoleLinkError(ValueError):
    pass


@dataclass
class IssuePhotoLinks:
    """Photo role associations for one issue. HUMAN-ONLY linking (the
    caller is a UI action); cross-project photo ids are REJECTED;
    the same photo may appear in multiple roles across SEPARATE
    remediations (progress can repeat) but one role holds one photo
    once per remediation cycle."""
    issue_id: str
    project_id: str
    _by_role: dict[PhotoRole, list[str]] = field(default_factory=dict)
    _photo_projects: dict[str, str] = field(default_factory=dict)

    def link(self, photo_id: str, project_id: str,
             role: PhotoRole) -> None:
        if project_id != self.project_id:
            raise RoleLinkError(
                f"photo {photo_id} belongs to project "
                f"{project_id!r}, but issue {self.issue_id} is in "
                f"{self.project_id!r} - cross-project links refused")
        if photo_id in self._photo_projects \
                and self._photo_projects[photo_id] != project_id:
            raise RoleLinkError("photo already cross-linked")
        self._by_role.setdefault(role, [])
        if photo_id in self._by_role[role]:
            return          # idempotent relink
        self._by_role[role].append(photo_id)
        self._photo_projects[photo_id] = project_id

    def photos(self, role: PhotoRole) -> list[str]:
        return list(self._by_role.get(role, []))

    def has_before_after_pair(self) -> bool:
        return bool(self._by_role.get(PhotoRole.before)) and \
            bool(self._by_role.get(PhotoRole.after))

    def remediation_completeness(self) -> dict:
        """HONEST completeness: reports which roles have photos -
        it NEVER auto-declares the remediation complete (that is a
        human decision)."""
        return {
            "issue_id": self.issue_id,
            "before_count": len(self._by_role.get(PhotoRole.before,
                                                  [])),
            "progress_count": len(self._by_role.get(
                PhotoRole.progress, [])),
            "after_count": len(self._by_role.get(PhotoRole.after,
                                                 [])),
            "has_pair": self.has_before_after_pair(),
            "auto_complete": False,
            "note": "photo presence does NOT prove remediation - a "
                    "HUMAN confirms completion",
        }


# ============================================ B06: photo naming rules

class NamingError(ValueError):
    pass


_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED = {"CON", "PRN", "AUX", "NUL", "COM1", "COM2", "COM3",
             "COM4", "LPT1", "LPT2", "LPT3"}


def sanitize_component(value: str, max_len: int = 80) -> str:
    """Make one path component safe: strip illegal chars, trim dots
    and spaces, refuse reserved names, enforce length."""
    if not value:
        raise NamingError("empty component")
    cleaned = _ILLEGAL.sub("_", value)
    cleaned = cleaned.strip(". ")
    if not cleaned:
        raise NamingError(f"component {value!r} reduces to nothing")
    upper = cleaned.upper().split(".")[0]
    if upper in _RESERVED:
        raise NamingError(f"component {value!r} hits reserved name "
                          f"{upper}")
    if len(cleaned) > max_len:
        cleaned = cleaned[:max_len].rstrip(". ")
    return cleaned


@dataclass(frozen=True)
class NamingContext:
    project_number: str = ""
    building: str = ""
    floor: str = ""
    area: str = ""
    room: str = ""
    device: str = ""
    date: str = ""                # YYYYMMDD or ISO date
    category: str = ""
    sequence: int = 0
    custom: dict[str, str] = field(default_factory=dict)


def format_name(template: str, ctx: NamingContext) -> str:
    """Resolve a naming expression like:
        {project_number}_{building}_{floor}_{room}_{category}_{
        date}_{sequence:04d}
    Unknown placeholders raise (typos are better caught loudly than
    silently left as literal braces). The result is SAFE (passes
    sanitize) and NEVER touches original photo bytes - this produces
    a LOGICAL name for the record, not a file rename."""
    def repl(m: re.Match) -> str:
        key = m.group(1)
        fmt = m.group(2) or ""
        if key in ("custom",):
            raise NamingError(
                "'custom' is a bag - use {{custom.field}} syntax")
        if key.startswith("custom."):
            field_name = key[len("custom."):]
            v = ctx.custom.get(field_name, "")
            if not v:
                raise NamingError(
                    f"custom field {field_name!r} not provided")
            return sanitize_component(v)
        if key == "sequence":
            return f"{ctx.sequence:{fmt}}" if fmt else \
                f"{ctx.sequence:04d}"
        v = getattr(ctx, key, None)
        if v is None:
            raise NamingError(f"unknown placeholder {{{key}}}")
        v = str(v)
        if not v:
            raise NamingError(f"placeholder {{{key}}} is empty")
        return sanitize_component(v)

    result = re.sub(r"\{([a-z_.]+)(?::([^}]+))?\}", repl, template)
    # final safety net
    return sanitize_component(result, max_len=180)


def suggest_names(template: str, contexts: list[NamingContext],
                  start_sequence: int = 1) -> list[str]:
    """Batch naming with per-photo auto-increment. Duplicate names
    get a numeric suffix - never a silent overwrite."""
    out: list[str] = []
    seen: dict[str, int] = {}
    seq = start_sequence
    for ctx in contexts:
        ctx2 = NamingContext(
            project_number=ctx.project_number,
            building=ctx.building, floor=ctx.floor,
            area=ctx.area, room=ctx.room, device=ctx.device,
            date=ctx.date, category=ctx.category,
            sequence=seq, custom=ctx.custom)
        name = format_name(template, ctx2)
        if name in seen:
            seen[name] += 1
            name = f"{name}_{seen[name]}"
        else:
            seen[name] = 1
        out.append(name)
        seq += 1
    return out
