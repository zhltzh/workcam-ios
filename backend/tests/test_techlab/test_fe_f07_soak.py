"""FE-F07 tests: the soak runner works, honors caps, and reports
honestly. Runs use cwd-relative invocation (the repo path contains
non-ASCII characters - passing it as a subprocess ARGUMENT breaks
Windows console encodings)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2]


def _run(argv: list[str]) -> tuple[dict, str]:
    proc = subprocess.run(
        [sys.executable, "tools/soak_runner.py"] + argv,
        cwd=str(BACKEND), capture_output=True, timeout=600)
    assert proc.returncode == 0, proc.stderr.decode(errors="replace")
    out = proc.stdout.decode(errors="replace")
    start = out.index("{")
    return json.loads(
        out[start:out.rindex("}", 0, out.index("probes saved")) + 1]), out


class TestSoakRunner:
    def test_short_functional_run(self):
        r, _ = _run(["--max-ops", "20", "--duration-s", "0"])
        assert r["iterations"] == 20
        assert r["errors"] == 0, "a clean 20-op soak must be errorless"
        assert r["ops_per_s"] > 0
        assert r["iteration_latency_ms"]["p50"] > 0
        assert "not iPhone" in r["host"]

    def test_thirty_second_growth_monotone(self):
        r, out = _run(["--duration-s", "30", "--max-ops", "0"])
        assert r["errors"] == 0, r
        assert r["iterations"] > 20     # meaningful soak, not a blip
        import re
        m = re.search(r"soak-\d{8}-\d{6}\.json", out)
        assert m, "soak artifact path missing from stdout"
        doc = json.loads((Path(r["_artifact"]) if "_artifact" in r
                          else (BACKEND.parent / "docs" / "perf"
                                / m.group())).read_text(
                                    encoding="utf-8"))
        probes = doc["probes"]
        assert len(probes) >= 2
        photos = [p["photos"] for p in probes]
        events = [p["sync_events"] for p in probes]
        assert photos == sorted(photos), \
            f"photo count must be monotone: {photos}"
        assert events == sorted(events), \
            f"event count must be monotone: {events}"
        assert doc["errors"] == 0

    def test_db_size_growth_reported(self):
        r, _ = _run(["--max-ops", "40", "--duration-s", "0"])
        g = r["growth"]
        assert g["first"]["db_bytes"] > 0
        assert g["last"]["db_bytes"] >= g["first"]["db_bytes"]
