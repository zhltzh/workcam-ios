#!/usr/bin/env python
"""TECH-MARATHON-03 status reconciliation: update STATUS.json from
actual code/test evidence on dev/windows-tm-geometry.

Every claim below cites a concrete file/commit/test that EXISTS.
Where work is partial, the honest partial state + remaining gap is
recorded. NOT_STARTED only where no implementation exists.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATUS = ROOT.parent / "docs" / "management" / "windows" \
    / "TECH-MARATHON-03-STATUS.json"


def sha() -> str:
    return subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        cwd=ROOT, capture_output=True, text=True).stdout.strip()


HEAD = sha()
DV = "DESKTOP_VERIFIED"
CP = "CROSS_PLATFORM_PENDING"
PX = "XCODE_PENDING"
PD = "DEVICE_PENDING"
POC = "POC_VERIFIED"
NS = "NOT_STARTED"
IP = "IN_PROGRESS"
BL = "BLOCKED"

# evidence map: task -> (state, tests, notes)
E = {}

# --- A queue (historical closeout) --------------------------------
E["A01"] = (DV, ["push: 852253b..dfb06c0 all on origin",
                 "bundle backups verified okay"],
            "working tree clean at every checkpoint; branch chain "
            "31917e3->...->dfb06c0 intact; hotfix cd1219b merged")
E["A02"] = (DV, ["backend/tests/test_techlab/test_tm91_weak_network.py (7)"],
            "deterministic fault scheduler over the LIVE backend: "
            "offline/reset/intermittent/partial-dies-after-K; ledger "
            "idempotency under flaky-proxy replay; server-restart "
            "session survival; out-of-order chunks")
E["A03"] = (DV, ["backend/tests/test_techlab/test_tm93_upgrade_matrix.py (14)"],
            "11 schema-shape levels each forward-migrated with original "
            "bytes hash-reproven + historical v1.0 signature still "
            "verifying; poisoned-level rollback sweep; v11-era backup "
            "into v14 store")
E["A04"] = (CP, ["backend/bootstrap.ps1 (executed)",
                 "backend/bootstrap.sh (authored, Mac-run pending)"],
            "Windows bootstrap verified; macOS shell authored and "
            "CROSS_PLATFORM_PENDING one Mac execution")
E["A05"] = (BL, [],
            "official Swift toolchain install failed 4x on Windows "
            "(winget MSI 0x643); Swift remains authored+vector-matched "
            "but never compiled here - honest BLOCKED, no fake runs")
E["A06"] = (DV, ["backend/tests/test_techlab/test_m03_a06_night05_contract.py (6)",
                 "backend/tests/test_win_hotfix.py (4)"],
            "payload byte-layout PROVEN identical to v1.1 canonical; "
            "issue vocabulary (open/in_progress/resolved/closed) "
            "server-accepted; annotation shape mapping; filename-id "
            "ledger stability; PR24 role CAS/unlink/cursor/rejected-"
            "receipt contracts covered in test_win_hotfix.py")
E["A07"] = (DV, ["backend/tests/test_techlab/test_m03_a07_e2e_expanded.py (6)",
                 "backend/tests/test_techlab/test_fe_a12_redteam.py (10)",
                 "backend/tests/test_techlab/test_tm89_end_to_end.py (2)"],
            "rotation-with-historical-evidence-math, suspend/resume, "
            "chunk restart, conflict rebase, backup import, checkpoint "
            "truncation + full ten-attack red team")
E["A08"] = (DV, ["backend/tools/quality_gate.py 12/12 PASSED on dfb06c0 (1599 tests)",
                 "backend/test-keys/MANIFEST.sha256 (11 digests)"],
            "fingerprint-registered test keys (swap = gate fail); ZIP "
            "safety gate; license gate; handoff-manifest gate; gate "
            "captures failing test names for flake hunts")

# --- B queue (engineering workflows) --------------------------------
E["B01"] = (DV, ["backend/tests/test_techlab/test_m03_b01_b03_business.py (19 subset)"],
            "8 English versioned templates; NOT regulatory claims")
E["B02"] = (DV, ["same suite"], "Pass/Fail/NA/Not-Inspected; unchecked "
                "never auto-passes; evidence requirements not waivable")
E["B03"] = (DV, ["same suite"], "8 field types, conditional visibility, "
                "validation; Swift Codable in WorkCamBusiness.swift")
E["B04"] = (DV, ["backend/tests/test_techlab/test_m03_b04_b06_business.py (22 subset)"],
            "Night05 vocabulary + reopened mapping; guarded transitions; "
            "history preserved; Mac state-machine diff documented in "
            "m03-a06 contract doc")
E["B05"] = (DV, ["same suite"],
            "before/progress/after roles; human-only; cross-project "
            "refused; completeness never auto-declared; PR24 CAS "
            "replace/unlink covered in test_win_hotfix.py")
E["B06"] = (DV, ["same suite"],
            "naming expressions, sanitize, reserved names, string-only "
            "output (originals untouched by construction)")
E["B07"] = (DV, ["backend/tests/test_techlab/test_m03_b07_b08_business.py (16 subset)"],
            "batch planner + failure-only retry subset; action "
            "vocabulary structurally free of original-delete ops")
E["B08"] = (DV, ["same suite"],
            "offline index: project/tag/tags-all/location/device/issue/"
            "evidence/date; keyset drift-free; OCR text searchable "
            "only after human confirmation")
E["B09"] = (DV, ["backend/tests/test_techlab/test_m03_b09_b11_business.py (18 subset)"],
            "scans are CANDIDATES until human confirm; raw preserved "
            "beside correction")
E["B10"] = (DV, ["same suite"],
            "manual/device structural split; volume needs EXPERIMENTAL "
            "note; honest labels")
E["B11"] = (DV, ["same suite"],
            "activity counts frozen once set; weather/conditions are "
            "USER-ONLY fields; date validated")
E["B12"] = (CP, ["swift/WorkCamCore/Tests/WorkCamCoreTests/BusinessRuleTests.swift (13)"],
             "Swift business package authored + rule tests; never "
             "compiled on Windows (A05) - Mac compiles")

# --- C queue (image algorithms) -------------------------------------
E["C01"] = (DV, ["backend/tests/test_techlab/test_m03_c01_c02_exif_extremes.py (56)"],
            "watermark layout: ultrawide/ultratall/tiny/CJK/logo ok; "
            "named refusals (wrap-budget/height-budget/logo-starves/"
            "margin-overflow)")
E["C02"] = (DV, ["same suite"],
            "EXIF 1..8 all mapped; two real defects found+fixed "
            "(EXIF7/8 transposed; TM15 left/right name transposition "
            "pinned by cross-reference test)")
E["C03"] = (DV, ["backend/tests/test_techlab/test_tm63_annotations.py (17)"],
            "9 kinds, EXIF/crop transforms, hit-testing, undo/redo "
            "with soft delete; rendering itself is Mac CoreGraphics")
E["C04"] = (DV, ["backend/tests/test_techlab/test_m03_c04_c05_photo_quality.py (15 subset)"],
            "blur/exposure/contrast/resolution; ADVICE ONLY, disclaimer "
            "always appended")
E["C05"] = (DV, ["same suite"],
            "SHA-256 exact vs aHash/dHash near candidates; kind=never "
            "== exact; never auto-delete")
E["C06"] = (NS, [],
            "honest NOT_STARTED: selection review (SIFT license? "
            "OpenCV-free constraint) still open; no code")
E["C07"] = (NS, [],
            "compare-view Swift reference not yet authored")
E["C08"] = (NS, [],
            "OCR sample set + schema not yet authored")
E["C09"] = (DV, ["backend/tests/test_techlab/test_m03_c09_privacy.py (9)"],
            "derivative-only; human-confirmed regions; out-of-frame "
            "defect found+fixed; detector candidates never auto-applied")
E["C10"] = (DV, ["docs/tech-lab/m03-c10-image-components.md"],
            "unified index with honest NOT_STARTEDs for C06/C08")

# --- D queue (standards/deliverables) ---------------------------------
E["D01"] = (POC, ["backend/tools/c2pa-poc/ (B02-W019 lineage, 8 JSON PoC artifacts)"],
            "isolated PoC; NOT certification; re-check vs current goals "
            "queued (FE-G06)")
E["D02"] = (DV, ["backend/tests/test_techlab/test_m03_d02_tsa.py (7)",
                 "backend/tests/test_techlab/test_m03_d02_d03_audit.py (5)"],
            "real openssl ts protocol; issuance-time cert facts; local "
            "TEST TSA clearly labeled")
E["D03"] = (DV, ["backend/tests/test_techlab/test_m03_d03_long_term.py (14)"],
            "six distinct states; expired-chain-still-proves-existence; "
            "renewal = explicit audit event")
E["D04"] = (DV, ["backend/tests/test_techlab/test_m03_d04_evpack_v2.py (11)"],
            "evpack/2.0: tamper/missing NAMED, unknown-version refused, "
            "v1 import, Night05 subset mapping")
E["D05"] = (DV, ["backend/tests/test_techlab/test_m03_d05_streaming_zip.py (8)"],
            "ZIP64 forced; ~1MiB stream unit; 70000 entries; cancel "
            "cleanup; 2.4MB python peak measured (F06)")
E["D06"] = (DV, ["backend/tests/test_techlab/test_m03_d06_report_types.py (6)",
                 "report family regression 89"],
            "six types x a4/letter; +3 new types (measurement/"
            "before_after/project_summary)")
E["D07"] = (DV, ["backend/tests/test_techlab/test_m03_d07_d08_pagination_branding.py (6)"],
            "100-photo zero content loss (every PH-ref asserted); "
            "repeatRows pinned; long notes wrap")
E["D08"] = (DV, ["same suite"],
            "Page X of Y two-pass footer every page; company name "
            "footer; sign-off/project-id visible")
E["D09"] = (DV, ["REAL browser run: good 1/1 signatures valid, tampered "
                 "NAMED, unknown refused (Playwright, loopback)",
                 "backend/tools/web_verifier.py mirror (4)"],
            "two real defects found by the real run and fixed: .evpack "
            "extension gate + DER->raw conversion")
E["D10"] = (DV, ["backend/tests/test_techlab/test_m03_d10_interop.py (8)"],
            "8 formats with native consumers; GeoJSON lon-first pinned")

# --- E queue (Apple reference) ----------------------------------------
E["E01"] = (IP, [],
            "Night05/PR24 CONTRACTS audited (A06) but UI CODE audit "
            "not yet done - queue before E02-E10 to avoid duplicates")
for i in range(2, 11):
    E[f"E{i:02d}"] = (NS, [],
                      "UI reference work not started; E01 audit first")

# --- F queue (scale/reliability) --------------------------------------
E["F01"] = (DV, ["backend/tests/test_techlab/test_m03_f01_f02_perf.py (11 subset)"],
            "seeded generator; honest counts; evidence=unverified "
            "because generator signs NOTHING")
E["F02"] = (DV, ["same suite", "backend/tools/perf_baseline.json"],
            "medium tier 100proj/10k photos/100k metadata; EXPLAIN "
            "QUERY PLAN pins tag index")
E["F03"] = (IP, [],
            "W007/W025 fsck + orphan detection exist; Night05-style "
            "full-evidence-dir scan cost profiling still queued")
E["F04"] = (DV, ["backend/tests/test_techlab/test_m03_f04_f06_perf.py (6 subset)"],
            "128KiB p50 193ms; resume functional (timing reported, "
            "never gated after FE-I09); duplicate resend 20.9ms")
E["F05"] = (DV, ["same suite"],
            "10->100 photos 180->1238ms, 52 pages; per_page contract")
E["F06"] = (DV, ["same suite"],
            "500x256KB: python-peak-delta 2.4MB (streaming proven); "
            "1MiB = stream unit wording pinned by test")
E["F07"] = (NS, [], "soak runner not yet built (longest run = full suite)")
E["F08"] = (DV, ["backend/tests/test_techlab/test_m03_f08_races.py (4, 8/8 stable)"],
            "two real flakes ROOT-CAUSED (never run-until-green): "
            "upload race assertion aligned to HOTFIX-02 semantics; "
            "resume_cost wall-clock ordering removed")
E["F09"] = (DV, ["backend/tests/test_techlab/test_tm45_fault_injection.py (5)",
                 "backend/tests/test_techlab/test_tm32_crash_recovery.py (14)",
                 "backend/tests/test_techlab/test_tm91_weak_network.py (7)"],
            "network/disk/db/crash injections live here; "
            "tmpfile-uncreatable + cache-corruption injections queued")
E["F10"] = (DV, ["backend/tools/perf_baseline.json"],
            "machine-readable, environment-tagged, DESKTOP-not-iPhone "
            "stated")

# --- G queue (enterprise, isolated) ------------------------------------
E["G01"] = (POC, ["backend/tests/test_techlab/test_b02_w028_tenant_poc.py (10)"],
            "isolated tenant PoC; never a MVP blocker")
for gid in ("G02", "G03", "G04", "G05", "G06", "G07", "G08"):
    E[gid] = (NS, [],
              "enterprise reserve; design-level notes only in "
              "b02-w028 study; isolated by directive")

# --- H queue (integration/handoff) --------------------------------------
E["H01"] = (IP, [],
            "gate covers pinned deps + licenses + sensitive scan; "
            "transitive SBOM + vuln sweep queued")
E["H02"] = (DV, ["gate: OpenAPI 88 ops live==repo",
                 "swift APIClient receiptSucceeded (rejected-replay encoded)"],
            "revision/receipt semantics documented; drift held by gate")
E["H03"] = (DV, ["backend/tools/trust_engine + trust_bridge",
                 "test_tm07 (15) + test_tm06 (8)"],
            "ten-dimension engine DESKTOP_VERIFIED; production route "
            "ISOLATED until protocol/product approval (decision "
            "required)")
E["H04"] = (DV, ["backend/tests/test_techlab/test_tm05_audit_chain.py (9)"],
            "signed checkpoints; tail-truncation local-undetectability "
            "proven + checkpoint detection; external anchor limits stated")
E["H05"] = (IP, [],
            "TM35 resumable sessions shipped; legacy whole-buffer "
            "endpoint cutover awaits review (RISK-W021-R1 recorded)")
E["H06"] = (IP, [],
            "dev/windows-tm-geometry IS the stable-candidate line "
            "(gate 12/12 at dfb06c0, 1599 tests); formal freeze/tag "
            "pending P0 sprint close")
E["H07"] = (NS, [], "RC4 experimental branch not yet split")
E["H08"] = (BL, ["backend/Dockerfile + compose delivered (W027)"],
            "authored but never built here (no Docker Desktop) - "
            "honest BLOCKED; native deploy verified")
E["H09"] = (CP, ["handoff/mac-ready/integration-manifest.json (12 components)"],
            "modular manifest with per-component states; Mac compiles "
            "Swift (A05 BLOCKED on Windows)")
E["H10"] = (DV, ["handoff/mac-ready/01-START-HERE.md",
                 "handoff/mac-ready/DEVICE-ACCEPTANCE-KIT.md"],
            "machine-readable manifest + dependency-ordered intake + "
            "user-operable device kit")
E["H11"] = (DV, ["fresh clone verified at 8d79c20 (bootstrap->v14->live HTTP)"],
            "re-run at final freeze SHA remains before RC3 tag")
E["H12"] = (IP, ["docs/progress/WORKCAM-WINDOWS-TECH-MARATHON-03-REPORT.md"],
            "report exists; final version refresh after this "
            "reconciliation")


def main() -> None:
    doc = json.loads(STATUS.read_text(encoding="utf-8"))
    tasks = doc["tasks"]
    by_id = {t["id"]: t for t in tasks}
    for tid, (state, tests, notes) in E.items():
        t = by_id.get(tid)
        if t is None:
            print(f"WARN: unknown task {tid}")
            continue
        t["state"] = state
        t["commit"] = HEAD
        t["tests"] = tests
        t["notes"] = notes
    counts: dict[str, int] = {}
    for t in tasks:
        counts[t["state"]] = counts.get(t["state"], 0) + 1
    doc["reconciled"] = {
        "at": HEAD,
        "method": "evidence-based reconciliation against actual code/tests",
        "counts": counts,
    }
    STATUS.write_text(json.dumps(doc, indent=1, ensure_ascii=False),
                       encoding="utf-8")
    print(f"reconciled {len(tasks)} tasks at {HEAD}")
    for k, v in sorted(counts.items()):
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
