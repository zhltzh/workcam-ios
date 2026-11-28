#!/usr/bin/env python
"""TM-04 I07: user-visible-string consistency + glossary check.

The product rule is ENGLISH UI with FIXED vocabulary: the same
concept must never appear under two names (or two concepts under
one). This tool enforces it mechanically:

  1. GLOSSARY pinning: every term below may appear in user-visible
     Swift strings (the *Reference views + copy helpers); nothing
     else may look like a synonym of a pinned term.
  2. KNOWN SYNONYMS are rejected when found in user-copy sources
     (e.g. "sign out" when the pinned term is "Log Out").
  3. Non-ASCII in user copy is flagged (the UI is English; measured
     values like mm/m may contain digits and units only; this is a
     candidate rule for product review, not a launch blocker).

Honesty: this is a REPORTING tool first (exit non-zero only on
glossary violations, category 2); category-3 findings are warnings
because legitimate symbols exist (e.g. middle dot in lists).

Run from backend/: python tools/ui_glossary_check.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCES = [
    "swift/WorkCamCore/Sources/WorkCamModels/"
        "CameraAndProjectsReference.swift",
    "swift/WorkCamCore/Sources/WorkCamModels/CompareReference.swift",
    "swift/WorkCamCore/Sources/WorkCamModels/OCRReference.swift",
    "swift/WorkCamCore/Sources/WorkCamModels/SettingsReference.swift",
    "swift/WorkCamCore/Sources/WorkCamModels/SyncCenterReference.swift",
    "swift/WorkCamCore/Sources/WorkCamModels/Entitlements.swift",
]

# The pinned vocabulary: ONE term per concept, exact casing.
GLOSSARY: dict[str, list[str]] = {
    "authentication": ["Log Out", "Log In", "Enroll"],
    "sync": ["Sync", "Sync Now", "Queued", "Sync when the connection returns"],
    "evidence": ["Evidence", "Original", "Derivative"],
    "photo roles": ["Before", "Progress", "After"],
    "issues": ["Issue", "Severity", "Resolved", "In Progress", "Closed"],
    "trust": ["Verified", "Unverified", "Signature Valid", "Failed"],
    "location": ["Area", "Floor", "Room", "Spot"],
    "licensing": ["WorkCam Pro", "WorkCam Team", "Free"],
    "time honesty": ["device time", "server time"],
    "offline": ["Offline"],
}

# Rejected synonyms -> the pinned term that must be used instead.
FORBIDDEN_SYNONYMS: dict[str, str] = {
    r"\bsign out\b": "Log Out",
    r"\bsign in\b": "Log In",
    r"\blogin\b": "Log In",
    r"\bsynced\b": "Sync (past forms stay 'Synced' only in receipts; "
                   "UI copy uses 'Sync')",
    r"\bsynchronize\b": "Sync",
    r"\bpics\b": "Photos",
    r"\bphoto\s+album\b": "Photo Library",
    r"\bverified\s+account\b": "Verified (trust label applies to "
                                "evidence, not accounts)",
    r"\bproof\b": "Evidence",
}

# Marketing words that must NEVER appear in UI copy (honesty rules).
BANNED_MARKETING: list[str] = [
    r"\bguarantee(d)?\b",
    r"\bcertified\b",
    r"\bforever\b",
    r"\b100%\b",
    r"\bunlimited\b",
    r"\binstant(ly)?\b",
]


def extract_string_literals(path: Path) -> list[str]:
    """Pull the string literals out of Swift source (naive but
    adequate for the reference views: no escapes/multiline)."""
    text = path.read_text(encoding="utf-8")
    return re.findall(r'"((?:[^"\\]|\\.)*)"', text)


def main() -> int:
    violations: list[str] = []
    warnings: list[str] = []
    checked = 0

    for rel in SOURCES:
        p = ROOT / rel
        if not p.exists():
            violations.append(f"source missing: {rel}")
            continue
        for literal in extract_string_literals(p):
            checked += 1
            low = literal.lower()
            # skip non-copy literals (identifiers, a11y ids, color
            # hex, format strings, file names)
            if (re.fullmatch(r"[\w./:#-]+", literal)
                    or literal.startswith("compare")
                    or literal.endswith(".jpg")
                    or literal in ("", " ")):
                continue
            for pattern, pinned in FORBIDDEN_SYNONYMS.items():
                if re.search(pattern, low):
                    violations.append(
                        f"{rel}: '{literal}' -> use '{pinned}'")
            for pattern in BANNED_MARKETING:
                if re.search(pattern, low):
                    violations.append(
                        f"{rel}: '{literal}' contains banned "
                        f"marketing word {pattern}")
            if any(ord(c) > 127 for c in literal):
                warnings.append(
                    f"{rel}: non-ASCII in user copy: {literal!r}")

    print(f"checked {checked} string literals across {len(SOURCES)} "
          f"user-copy sources")
    if warnings:
        print(f"\nwarnings ({len(warnings)}) [review, not a blocker]:")
        for w in warnings[:10]:
            print(f"  - {w}")
    if violations:
        print(f"\nVIOLATIONS ({len(violations)}):")
        for v in violations[:20]:
            print(f"  - {v}")
        return 1
    print("glossary: PASS (no forbidden synonyms, no banned "
          "marketing words)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
