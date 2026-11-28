#!/usr/bin/env python
"""TM-04 J02: versioned dependency management for the cross-platform
Swift components.

WorkCamCore is deliberately ZERO-EXTERNAL-DEPENDENCY (verified
here), so its "dependency management" contract is:

  1. VERSION pinning: swift/WorkCamCore/VERSION is the single
     version source; the package metadata must not contradict it.
  2. ZERO external deps: Package.swift may declare targets and
     intra-package dependencies only - any .package(url:...) or
     .package(path:...) dependency is a review-required change and
     FAILS this check (adding one is a DECISION_REQUIRED act).
  3. Delivery tracking: every component recorded in the Mac
     integration manifest carries the same WorkCamCore version, so
     Mac-side adoption can pin exactly what was verified here.

Run from backend/: python tools/j02_version_lock.py
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
VERSION_FILE = REPO / "swift/WorkCamCore/VERSION"
PACKAGE = REPO / "swift/WorkCamCore/Package.swift"
MANIFEST = REPO / "handoff/mac-ready/integration-manifest.json"

SEMVER = re.compile(r"^\d+\.\d+\.\d+(-[0-9A-Za-z.-]+)?$")


def main() -> int:
    failures: list[str] = []

    # 1. VERSION file exists and is a valid semver build identifier
    raw = VERSION_FILE.read_text(encoding="utf-8").strip()
    if not SEMVER.match(raw):
        failures.append(f"VERSION '{raw}' is not valid semver")
    else:
        print(f"[j02] 1. WorkCamCore version: {raw}")

    # 2. zero external dependencies (review gate, not convention)
    pkg = PACKAGE.read_text(encoding="utf-8")
    externals = re.findall(r"\.package\(([^)]*)\)", pkg)
    # allow only comments: a real .package declaration adds deps
    real = [e for e in externals if "url:" in e or "path:" in e]
    if real:
        failures.append(
            f"external package dependency declared (DECISION_REQUIRED"
            f" review needed): {real}")
    else:
        print("[j02] 2. zero external package dependencies: OK "
              "(intra-package targets only)")

    # 3. manifest carries the pinned version per WorkCamCore
    #    components
    m = json.loads(MANIFEST.read_text(encoding="utf-8-sig"))
    pinned = 0
    for comp in m.get("components", []):
        paths = " ".join(comp.get("paths", []))
        if "WorkCamCore" in paths or "swift/" in paths:
            comp["workcamcore_version"] = raw
            pinned += 1
    if pinned == 0:
        failures.append("manifest has no WorkCamCore components to pin")
    else:
        MANIFEST.write_text(
            json.dumps(m, indent=1, ensure_ascii=False) + "\n",
            encoding="utf-8")
        print(f"[j02] 3. manifest: pinned {pinned} component(s) "
              f"to version {raw}")

    if failures:
        print("\nJ02 FAILURES:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("\nJ02: version lock OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
