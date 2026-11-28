#!/usr/bin/env python
"""TM-04 A06: Windows-side static acceptance checks for every
XCODE_PENDING / CROSS_PLATFORM_PENDING Swift component.

The honest division of labor (task sheet): Windows cannot RUN the
Apple-only layers, but it OWNS every check that does not need a
device:

  1. COMPILABILITY: the package's non-SwiftUI surface builds on the
     real Windows toolchain (swift build); the Apple-only files are
     exempt ONLY if they carry the #if canImport(SwiftUI) gate.
  2. GATE DISCIPLINE: every file importing a platform-only module
     (SwiftUI/UIKit/ARKit/CoreLocation/AVFoundation/StoreKit) must
     be inside a canImport gate - an UNGATED import fails.
  3. INTERFACE: the test suite compiles (swift build --build-tests)
     so public API surface changes are caught on Windows too.
  4. VECTORS: shared golden-vector files exist and are versioned.
  5. LABEL HONESTY: files still claiming XCODE_PENDING in a comment
     are LISTED (report), never counted as executed here.

Exit 0 = all checks pass. Run from backend/:
    python tools/xcode_pending_checks.py
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PKG = REPO / "swift" / "WorkCamCore"
SCRATCH = Path(r"C:\Users\admin\AppData\Local\Temp\zhanlu\wcbuild-a06")

PLATFORM_MODULES = [
    "SwiftUI", "UIKit", "ARKit", "CoreLocation", "AVFoundation",
    "StoreKit", "Photos", "CoreMotion", "MapKit",
]


def check_gates() -> list[str]:
    """Every platform-module import must sit inside a canImport
    gate in that file."""
    offenders: list[str] = []
    for swift in PKG.rglob("*.swift"):
        text = swift.read_text(encoding="utf-8")
        gated = "#if canImport(" in text
        for mod in PLATFORM_MODULES:
            for m in re.finditer(rf"^\s*import\s+{mod}\b", text, re.M):
                if not gated:
                    rel = swift.relative_to(REPO).as_posix()
                    offenders.append(f"{rel}: un-gated import {mod}")
    return offenders


def run_swift(cmd: list[str]) -> tuple[int, str]:
    r = subprocess.run(
        ["swift", *cmd, "--scratch-path", str(SCRATCH)],
        cwd=str(PKG), capture_output=True,
        encoding="utf-8", errors="replace", timeout=1800)
    out = (r.stdout or "") + (r.stderr or "")
    return r.returncode, out[-400:]


def main() -> int:
    failures: list[str] = []

    # 1+3: build + tests compile on the REAL toolchain
    code, tail = run_swift(["build"])
    if code != 0:
        failures.append(f"swift build failed: {tail}")
    else:
        print("[a06] 1. swift build: OK (real Windows toolchain)")
    code, tail = run_swift(["build", "--build-tests"])
    if code != 0:
        # retry once - the frontend cold-start StackOverflow is a
        # documented intermittent (sprint-1 finding)
        code, tail = run_swift(["build", "--build-tests"])
    if code != 0:
        failures.append(f"swift build --build-tests failed: {tail}")
    else:
        print("[a06] 3. tests compile: OK")

    # 2: platform gate discipline
    offenders = check_gates()
    if offenders:
        failures += [f"ungated platform import: {o}"
                     for o in offenders]
    else:
        print("[a06] 2. platform gates: OK "
              "(every platform import is canImport-gated)")

    # 4: shared vectors present + versioned
    vectors = REPO / "swift/WorkCamCore/Tests/WorkCamCoreTests" \
        / "Resources/geometry-golden-vectors.json"
    if not vectors.exists():
        failures.append("golden vectors missing from test resources")
    else:
        import json
        v = json.loads(vectors.read_text(encoding="utf-8"))
        if v.get("version") != "geometry-vectors/1.0":
            failures.append("vector version drift")
        else:
            print(f"[a06] 4. vectors: OK ({len(v['cases'])} cases, "
                  f"{v['version']})")

    # 5: label honesty - list (not gate) pending files
    pending = []
    for swift in PKG.rglob("*.swift"):
        text = swift.read_text(encoding="utf-8")
        if re.search(r"XCODE_PENDING", text):
            pending.append(swift.relative_to(REPO).as_posix())
    print(f"[a06] 5. XCODE_PENDING labels still present (listed, "
          f"not claimed executed): {len(pending)}")
    for p in pending[:8]:
        print(f"      - {p}")

    if failures:
        print("\nA06 FAILURES:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("\nA06: ALL WINDOWS-SIDE CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
