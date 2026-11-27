#!/usr/bin/env python
"""FE-K01/K02: unified machine-readable task matrix + risk ledger.

Merges the THREE numbering systems (TM01-100, M03 A-H 80, FE A-K 120)
into ONE deduplicated matrix keyed by CAPABILITY, not by number. Each
entry carries: code paths, fixed commit lineage, verify status per the
honest state machine, dependencies, platforms, and the unfinished
reason where applicable. A capability counts once; the numbers map to
it (many-to-one) so nothing is double-counted.

Generated (not hand-maintained): re-run after every batch to refresh.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# verify-status vocabulary (the honest state machine)
# NOT_STARTED / IMPLEMENTED / DESKTOP_VERIFIED / CROSS_PLATFORM_PENDING
# / XCODE_PENDING / DEVICE_PENDING / POC_VERIFIED / BLOCKED / ISOLATED_CANDIDATE

C = []          # capabilities


def cap(cid, title, paths, status, maps, *, deps=(), platforms=("win",),
        unfinished="", commit="lineage"):
    C.append({
        "capability_id": cid, "title": title,
        "paths": paths, "verify_status": status,
        "number_maps": maps,          # e.g. ["TM15", "M03-?", "FE-C02"]
        "dependencies": list(deps),
        "platforms": list(platforms),
        "unfinished_reason": unfinished,
        "commit_lineage": commit})


# ---------------------------------------------------- A: evidence & crypto
cap("evidence-schema", "Unified evidence protocol schema v1.x + evpack",
    ["backend/docs/evidence/evidence-schema-v1.json",
     "backend/tools/evidence_schema.py",
     "backend/techlab/business/evpack_v2.py"],
    "DESKTOP_VERIFIED",
    ["TM01", "M03-?", "FE-A01", "FE-G01"],
    deps=[], platforms=["win", "mac"],
    unfinished="v2/v3 production protocol switch awaits product review")
cap("crosslang-vectors", "Cross-language golden vectors (Python/Swift/OpenSSL)",
    ["test-vectors/crosslang/tm02-protocol-vectors.json",
     "test-vectors/crosslang/TM02ProtocolVectorTests.swift",
     "backend/tools/tm02_gen_vectors.py"],
    "DESKTOP_VERIFIED (Swift side CROSS_PLATFORM_PENDING)",
    ["TM02", "FE-A02"],
    deps=["evidence-schema"], platforms=["win", "mac"],
    unfinished="FE-A02 asks >=1000 meaningful cases: current 100+34+40; "
               "expansion queued behind FE-A02 sweep")
cap("secure-enclave-contract", "Secure Enclave integration contract",
    ["docs/mac/TM55-HANDOFF.md#A4", "backend/tools/appattest/README.md"],
    "CROSS_PLATFORM_PENDING (contract only)",
    ["FE-A03"],
    deps=["app-attest-server"], platforms=["mac"],
    unfinished="hardware acceptance is Mac-only by definition")
cap("app-attest-server", "App Attest server-side verification",
    ["backend/tools/appattest/app_attest_verify.py",
     "backend/tools/appattest/cbor_min.py",
     "backend/tools/appattest/synth_vectors.py"],
    "DESKTOP_VERIFIED (synthetic CAs; real-Apple DEVICE_PENDING)",
    ["TM04", "M03-A05-part", "FE-A04"],
    deps=["evidence-schema"], platforms=["win"],
    unfinished="dev/prod anchor isolation DONE (TM43); Apple root cross-check device-pending")
cap("device-key-lifecycle", "Device identity + key lifecycle + evidence binding",
    ["backend/app/services/device_identity.py",
     "backend/tests/test_techlab/test_tm03_key_lifecycle.py"],
    "DESKTOP_VERIFIED (production routes)",
    ["TM03", "FE-A05", "FE-D10"],
    deps=["app-attest-server"], platforms=["win"],
    unfinished="possession proof != hardware-origin proof (pinned everywhere)")
cap("audit-chain", "Evidence chain + Merkle + checkpoints",
    ["backend/tools/evidence_chain/audit_chain.py",
     "backend/tests/test_techlab/test_tm05_audit_chain.py"],
    "DESKTOP_VERIFIED; server checkpoint = ISOLATED_CANDIDATE",
    ["TM05", "FE-A06", "FE-A07"],
    deps=["evidence-schema"], platforms=["win"],
    unfinished="checkpoint anchoring into sync flow awaits review; "
               "no external anchor = tail truncation undetectable (proven)")
cap("trust-engine-10d", "Ten-dimension trust engine + bridges",
    ["backend/tools/trust_engine/trust_levels.py",
     "backend/tools/evidence-verifier/trust_bridge.py",
     "backend/tools/web_verifier.py"],
    "DESKTOP_VERIFIED (engine+CLI bridge); production route ISOLATED_CANDIDATE",
    ["TM07", "TM58", "FE-A08", "FE-A11-part"],
    deps=["evidence-schema", "device-key-lifecycle"], platforms=["win", "browser"],
    unfinished="FE-A11 cross-verifier consistency sweep queued")
cap("provenance-boundary", "Photo source & authenticity boundary",
    ["backend/tools/provenance/source_model.py",
     "docs/evidence/tm08-provenance-threat-model.md"],
    "DESKTOP_VERIFIED",
    ["TM08", "FE-A09"], deps=[], platforms=["win"],
    unfinished="")
cap("long-term-evidence", "Long-term verification policy + renewal",
    ["backend/techlab/business/long_term_evidence.py",
     "backend/techlab/business/tsa_rfc3161.py"],
    "DESKTOP_VERIFIED",
    ["M03-D03", "FE-A10", "FE-G07", "FE-G08-part"], deps=["evidence-schema"],
    platforms=["win"],
    unfinished="revocation material (CRL/OCSP) not fetched offline - "
               "recorded as a stated limit")
cap("standalone-verifiers", "W018 CLI + web verifier + Swift verifier models",
    ["backend/tools/evidence-verifier/verify_evidence.py",
     "docs/web-verifier/verifier.html",
     "backend/tools/web_verifier.py"],
    "DESKTOP_VERIFIED (CLI + web via Python mirror); BROWSER RUN PENDING FE-G09",
    ["TM06", "M03-D09", "FE-A11", "FE-G09"],
    deps=["trust-engine-10d"], platforms=["win", "browser", "mac"],
    unfinished="FE-G09 requires a REAL browser run (Playwright tool available)")
cap("red-team-regression", "Evidence red-team suite",
    ["backend/tests/test_techlab/test_tm06_trust_bridge.py",
     "backend/tests/test_techlab/test_m03_a07_e2e_expanded.py",
     "backend/tests/test_techlab/test_tm44_parser_fuzz.py"],
    "DESKTOP_VERIFIED; FE-A12 expansion queued",
    ["TM44-part", "FE-A12"], deps=["evidence-schema"],
    platforms=["win"],
    unfinished="FE-A12 asks the full 10-attack sweep incl. project-ownership swap")

# ---------------------------------------------------- B: time & location
cap("time-anchor", "Server-signed time anchor protocol",
    ["backend/app/api (time-anchor routes)",
     "backend/techlab/timegeo_trust.py"],
    "DESKTOP_VERIFIED", ["TM09-part", "FE-B01", "FE-B05-part"],
    deps=[], platforms=["win"],
    unfinished="multi-anchor conflict policy = design note queued")
cap("monotonic-clock", "Injectable monotonic clock model",
    ["backend/techlab/time_uncertainty.py",
     "swift/WorkCamCore/Sources/WorkCamTime/TimeEstimate.swift"],
    "DESKTOP_VERIFIED (Swift CROSS_PLATFORM_PENDING)",
    ["TM10", "FE-B02", "FE-B03", "FE-B04-part"],
    deps=["time-anchor"], platforms=["win", "mac"],
    unfinished="sleep/hibernate edge cases = Mac device half")
cap("clock-anomaly", "Clock rollback/forward + DST + date boundaries",
    ["backend/tests/test_techlab/test_tm09_anchor_consistency.py",
     "backend/tests/test_techlab/test_tm45_fault_injection.py (rate window)"],
    "DESKTOP_VERIFIED",
    ["TM09-part", "FE-B04"], deps=["monotonic-clock"], platforms=["win"],
    unfinished="")
cap("gnss-quality", "Location quality + risk + thresholds",
    ["backend/techlab/location_quality.py",
     "swift/WorkCamCore/Sources/WorkCamLocation/LocationQuality.swift"],
    "DESKTOP_VERIFIED (thresholds configurable)",
    ["TM12", "FE-B06", "FE-B07"],
    deps=["monotonic-clock"], platforms=["win", "mac"],
    unfinished="device calibration pending (TM22)")
cap("gnss-test-matrix", "Offline GNSS collection matrix + CSV",
    ["docs/tech-lab/tm11-gnss-research-brief.md"],
    "DESIGN COMPLETE; collection DEVICE_PENDING",
    ["TM11", "FE-B08"], deps=["gnss-quality"], platforms=["mac"],
    unfinished="Mac executes the collection; Windows ships the analyzer")
cap("geodesy", "WGS84/ECEF/ENU/GeoJSON/KML + dateline",
    ["backend/techlab/geometry/geo.py",
     "backend/tests/test_techlab/test_tm_geometry_core.py"],
    "DESKTOP_VERIFIED", ["TM13", "FE-B09"], deps=[], platforms=["win", "mac"],
    unfinished="UTM NOT implemented (projection choice awaits product need - honest)")
cap("time-geo-joint", "Joint time+location evidence semantics",
    ["docs/evidence/evidence-schema-v1.json (trust_rules)",
     "backend/tools/trust_engine/trust_levels.py"],
    "DESKTOP_VERIFIED", ["TM07-part", "FE-B10"],
    deps=["trust-engine-10d", "gnss-quality"], platforms=["win"],
    unfinished="")

# ---------------------------------------------------- C: LiDAR & geometry
cap("geometry-core", "3D math + backprojection + intrinsics",
    ["backend/techlab/geometry/camera.py",
     "backend/techlab/geometry/measurement.py",
     "backend/tests/test_techlab/test_tm_geometry_core.py"],
    "DESKTOP_VERIFIED",
    ["TM15", "TM16", "TM20", "FE-C01", "FE-C02", "FE-C03", "FE-C06"],
    deps=[], platforms=["win", "mac"], unfinished="")
cap("plane-fitting", "LSQ/PCA/RANSAC plane fits + residuals",
    ["backend/techlab/geometry/plane.py"],
    "DESKTOP_VERIFIED", ["TM17", "FE-C05"],
    deps=["geometry-core"], platforms=["win", "mac"], unfinished="")
cap("area-volume", "Polygon areas + self-intersection + basic volumes",
    ["backend/techlab/geometry/area.py"],
    "DESKTOP_VERIFIED (volume = EXPERIMENTAL label enforced)",
    ["TM18", "TM19", "FE-C04", "FE-C07"],
    deps=["plane-fitting"], platforms=["win", "mac"],
    unfinished="FE-C07 irregular point-cloud volume stays isolated PoC")
cap("pointcloud", "Voxel/statistical/radius filters + PLY + reports",
    ["backend/techlab/geometry/pointcloud.py"],
    "DESKTOP_VERIFIED", ["TM21", "FE-C08"],
    deps=["geometry-core"], platforms=["win", "mac"], unfinished="")
cap("measurement-quality", "Uncertainty + calibration + propagation",
    ["backend/techlab/geometry/quality.py"],
    "DESKTOP_VERIFIED (uncalibrated = conservative, no fake CI)",
    ["TM22", "FE-C09"], deps=["geometry-core"], platforms=["win", "mac"],
    unfinished="device calibration is the Mac half")
cap("lidar-analyzer", "Three-mode analysis + CSV + grouped stats",
    ["backend/tools/tech-lab (W-02 core)",
     "backend/tests/test_techlab/test_b02w016_lidar.py"],
    "DESKTOP_VERIFIED (synthetic; device DEVICE_PENDING)",
    ["TM14", "M03-A02-map", "FE-C10", "FE-C11-part"],
    deps=["pointcloud"], platforms=["win"],
    unfinished="real-device matrix FE-C11: template DESIGNED, collection Mac")
cap("measurement-formats", "Open measurement records + SVG/PLY/DXF",
    ["backend/techlab/geometry/measurement_format.py",
     "backend/techlab/geometry/dxf_poc.py"],
    "DESKTOP_VERIFIED (DXF = POC_VERIFIED isolated)",
    ["TM23", "FE-G10-part"],
    deps=["geometry-core"], platforms=["win"], unfinished="")
cap("swift-geometry", "WorkCamCore geometry package",
    ["swift/WorkCamCore/Sources/WorkCamGeometry/*.swift",
     "swift/WorkCamCore/Tests/WorkCamCoreTests/GeometryGoldenVectorTests.swift"],
    "CROSS_PLATFORM_PENDING (authored + vector-matched, never compiled here)",
    ["TM61", "TM65", "FE-C12"],
    deps=["geometry-core", "plane-fitting", "area-volume", "pointcloud"],
    platforms=["mac"],
    unfinished="Swift toolchain BLOCKED after 4 honest attempts")

# ---------------------------------------------------- D: sync & backend
cap("enrollment-lock-fix", "Same-key enrollment 31s-lock fix",
    ["backend/app/services/device_identity.py (same-connection audit)",
     "backend/scripts/bench_enrollment.py",
     "backend/tests/test_win_hotfix.py"],
    "DESKTOP_VERIFIED (P95 0.854ms; live HTTP 15ms replay)",
    ["WIN-HOTFIX-01", "FE-D01"],
    deps=[], platforms=["win"],
    unfinished="FE-D01 re-verification sweep queued (unified latency accounting)")
cap("bidirectional-sync", "Uplink batch + downlink cursor events",
    ["backend/app/api/sync.py",
     "swift/WorkCamCore/Sources/WorkCamSync/Outbox.swift"],
    "DESKTOP_VERIFIED",
    ["WIN-HOTFIX-02/03", "FE-D02", "FE-D03"],
    deps=["enrollment-lock-fix"], platforms=["win", "mac"],
    unfinished="checklist/measurement uplink ops = FE-D02 expansion")
cap("tombstones-archive", "Archive vs logical vs physical delete",
    ["backend/app/api/sync.py (archived status + tombstones)",
     "backend/app/api/mvp.py"],
    "DESKTOP_VERIFIED (physical delete of originals NOT implemented by design)",
    ["FE-D04"], deps=["bidirectional-sync"], platforms=["win"],
    unfinished="")
cap("multi-client-conflicts", "Conflict engine + races + prefix visibility",
    ["backend/tests/test_techlab/test_tm34_conflicts.py",
     "backend/tests/test_techlab/test_m03_f08_races.py"],
    "DESKTOP_VERIFIED", ["TM34", "M03-F08", "FE-D05", "FE-D06-part"],
    deps=["bidirectional-sync"], platforms=["win"],
    unfinished="FE-I09: F08 poll-thread timing flake needs a "
               "deterministic sync mechanism (queued P0)")
cap("chunked-upload", "Resumable sessions + integrity proof",
    ["backend/app/services/chunked_upload.py",
     "swift/WorkCamCore/Sources/WorkCamSync/ChunkedUploadClient.swift"],
    "DESKTOP_VERIFIED", ["TM35", "FE-D07", "FE-D08-part"],
    deps=["enrollment-lock-fix"], platforms=["win", "mac"],
    unfinished="FE-D08 resource-limit sweep (hostile Content-Length) queued")
cap("download-recovery", "Metadata/derivative/original download candidate",
    ["(not started)"],
    "NOT_STARTED", ["FE-D09"], deps=["bidirectional-sync"],
    platforms=["win"],
    unfinished="FE-D09 design queued: Range-based original download + "
               "integrity re-hash, reuse chunk-session hashing")
cap("api-client-parity", "API client contract (Mac Integration 02)",
    ["swift/WorkCamCore/Sources/WorkCamSync/APIClient.swift",
     "docs/api/openapi.json",
     "test-vectors/win-hotfix/vectors.json"],
    "DESKTOP_VERIFIED (receiptSucceeded rejected-replay encoded)",
    ["M03-H02", "FE-D11"],
    deps=["bidirectional-sync"], platforms=["win", "mac"],
    unfinished="Mac client drift re-check after every backend change (gate holds it)")
cap("dual-client-e2e", "Two independent clients full-chain",
    ["backend/scripts/win_hotfix_live.py (single-client live)",
     "backend/tests/test_techlab/test_tm91_weak_network.py"],
    "DESKTOP_VERIFIED (dual-thread clients in-suite); FE-D12 sweep queued",
    ["FE-D12"], deps=["bidirectional-sync", "chunked-upload"],
    platforms=["win"],
    unfinished="FE-D12: full two-client scripted journey incl. history+recovery")

# ---------------------------------------------------- E: storage & perf
cap("immutable-storage", "Content-addressed original store + fsck",
    ["backend/app/services/original_store.py",
     "backend/app/services/evidence_recovery.py"],
    "DESKTOP_VERIFIED", ["TM31", "TM32", "FE-E01", "FE-E02", "FE-E03"],
    deps=[], platforms=["win"],
    unfinished="FE-E03 crash-recovery expansion (DER write torn files) queued")
cap("low-storage-safety", "Disk-full / write-fail never fakes success",
    ["backend/tests/test_techlab/test_tm32_crash_recovery.py (disk full)",
     "backend/tests/test_techlab/test_tm45_fault_injection.py"],
    "DESKTOP_VERIFIED", ["FE-E04-part"],
    deps=["immutable-storage"], platforms=["win"],
    unfinished="tmpfile-uncreatable scenario queued")
cap("cache-thumbnails", "Byte-budget LRU (derivative-only by type)",
    ["swift/WorkCamCore/Sources/WorkCamSync/PhotoIndex.swift"],
    "CROSS_PLATFORM_PENDING", ["TM72", "FE-E06"],
    deps=[], platforms=["mac"],
    unfinished="thumbnail generator itself is Mac (CoreGraphics)")
cap("db-scale", "10k photos / 100k metadata baselines",
    ["backend/tools/synth_data.py",
     "backend/tests/test_techlab/test_m03_f01_f02_perf.py",
     "backend/tools/perf_baseline.json"],
    "DESKTOP_VERIFIED", ["M03-F01/F02", "FE-E07", "FE-E12-part"],
    deps=[], platforms=["win"],
    unfinished="photo-directory scan deep-dive FE-E05 queued")
cap("streaming-pipelines", "Streaming SHA-256/ZIP64/upload/backup",
    ["backend/techlab/business/streaming_zip.py",
     "backend/tests/test_techlab/test_m03_f04_f06_perf.py"],
    "DESKTOP_VERIFIED (1MiB = stream unit; 2.4MB python peak measured)",
    ["M03-D05", "FE-E08"], deps=[], platforms=["win"],
    unfinished="")
cap("backup-restore", "Project packages + full backup + rollback",
    ["backend/app/services/backup_v2.py",
     "backend/tests/test_techlab/test_tm36_backup_v2.py",
     "backend/tests/test_techlab/test_tm93_upgrade_matrix.py"],
    "DESKTOP_VERIFIED", ["TM36", "FE-E09"],
    deps=["immutable-storage"], platforms=["win"],
    unfinished="incremental backup = ISOLATED_CANDIDATE design note")
cap("longrun-stability", "Configurable-duration soak",
    ["(F07 deep-dive not started)"],
    "NOT_STARTED", ["FE-E10"], deps=["db-scale"],
    platforms=["win"],
    unfinished="soak runner design queued; existing longest run = 8min full suite")
cap("fault-injection", "Network/disk/db/process/migration faults",
    ["backend/tests/test_techlab/test_tm45_fault_injection.py",
     "backend/tests/test_techlab/test_tm91_weak_network.py",
     "backend/tests/test_techlab/test_tm32_crash_recovery.py"],
    "DESKTOP_VERIFIED", ["TM45", "M03-A02", "FE-E11"],
    deps=[], platforms=["win"],
    unfinished="permission-failure + cache-corruption injections queued")

# ---------------------------------------------------- F: image & OCR
cap("exif-vectors", "EXIF 1..8 + extreme watermark vectors",
    ["test-vectors/geometry/exif-watermark-extreme-vectors.json",
     "backend/tests/test_techlab/test_m03_c01_c02_exif_extremes.py"],
    "DESKTOP_VERIFIED (2 real defects fixed + cross-reference pinned)",
    ["M03-C01", "M03-C02", "FE-F01"],
    deps=["watermark-layout"], platforms=["win", "mac"],
    unfinished="FE-F01 asymmetric-pattern corner probe queued")
cap("annotation-geometry", "Normalized 9-kind annotations + transforms",
    ["backend/techlab/geometry/annotations.py",
     "swift/WorkCamCore/Sources/WorkCamGeometry/Annotations.swift"],
    "DESKTOP_VERIFIED", ["TM63", "FE-F02"],
    deps=["geometry-core"], platforms=["win", "mac"],
    unfinished="mirror variants (EXIF 2/4/5/7) on annotations queued")
cap("watermark-layout", "Rendering-free layout engine",
    ["backend/techlab/geometry/watermark_layout.py",
     "swift/WorkCamCore/Sources/WorkCamGeometry/WatermarkLayout.swift"],
    "DESKTOP_VERIFIED", ["TM64", "FE-F03"],
    deps=[], platforms=["win", "mac"],
    unfinished="W011 10-vector file parity held (mapping documented)")
cap("photo-quality", "Blur/exposure/contrast/resolution advice",
    ["backend/techlab/business/photo_quality.py"],
    "DESKTOP_VERIFIED (disclaimer always appended)",
    ["M03-C04", "FE-F04"], deps=[], platforms=["win"],
    unfinished="occlusion heuristic queued")
cap("duplicate-candidates", "SHA-256 exact + aHash/dHash near",
    ["backend/techlab/business/photo_quality.py (duplicates)",
     "swift/.../PhotoIndex.swift (duplicateCandidates)"],
    "DESKTOP_VERIFIED (near != exact, never auto-delete)",
    ["M03-C05", "TM27", "FE-F05"], deps=["photo-quality"],
    platforms=["win", "mac"],
    unfinished="rotation/crop resilience sweep queued")
cap("image-registration", "Feature/affine/homography registration",
    ["(not started - selection review pending)"],
    "NOT_STARTED", ["M03-C06", "FE-F06"],
    deps=["geometry-core"], platforms=["win"],
    unfinished="selection review document + isolated PoC queued (ECC? SIFT "
               "license? OpenCV-free constraint) - honest hold")
cap("beforeafter-compare", "Side-by-side/slide/overlay models",
    ["backend/techlab/business/issues.py (roles)"],
    "DESKTOP_VERIFIED (data+roles); UI reference NOT_STARTED",
    ["M03-B05", "FE-F07"],
    deps=["annotation-geometry"], platforms=["win", "mac"],
    unfinished="Swift compare-view model queued")
cap("ocr-engineering", "OCR sample set + schema + human-confirm",
    ["(not started)"],
    "NOT_STARTED", ["M03-C08", "FE-F08"],
    deps=[], platforms=["win"],
    unfinished="legal sample set + Vision contract + confirm-loop queued")
cap("privacy-redaction", "Derivative-only redaction + audit record",
    ["backend/techlab/business/privacy_redaction.py"],
    "DESKTOP_VERIFIED (out-of-frame defect fixed)",
    ["M03-C09", "FE-F09"], deps=[], platforms=["win"],
    unfinished="EXIF-scrub on derivatives queued")

# ---------------------------------------------------- G: reports/standards
cap("report-six", "Six report templates A4+Letter + Page X of Y",
    ["backend/app/services/report_pdf.py",
     "backend/app/api/reports.py"],
    "DESKTOP_VERIFIED (100-photo zero-loss proven)",
    ["M03-D06", "M03-D07", "M03-D08", "FE-G03", "FE-G04"],
    deps=[], platforms=["win"],
    unfinished="report snapshot immutability FE-G05: DB row already pins "
               "photo set - deep audit queued")
cap("zip64-interop", "Streaming ZIP64 cross-platform",
    ["backend/techlab/business/streaming_zip.py"],
    "DESKTOP_VERIFIED", ["M03-D05", "FE-G02"],
    deps=[], platforms=["win", "mac"],
    unfinished="macOS unzip CLI cross-check queued (needs Mac)")
cap("c2pa-poc", "C2PA study + PoC",
    ["backend/tools/c2pa-poc/ (W-019)", "docs/tech-lab/c2pa/"],
    "POC_VERIFIED (isolated; NOT certification)",
    ["TM37-part", "FE-G06"],
    deps=["evidence-schema"], platforms=["win"],
    unfinished="FE-G06 re-check vs current goals queued")
cap("rfc3161", "Local test TSA + issuance audit",
    ["backend/techlab/business/tsa_rfc3161.py",
     "backend/tests/test_techlab/test_m03_d02_tsa.py",
     "backend/tests/test_techlab/test_m03_d02_d03_audit.py"],
    "DESKTOP_VERIFIED (real protocol; test CA clearly labeled)",
    ["M03-D02", "FE-G07"],
    deps=[], platforms=["win"],
    unfinished="public TSA = deployment config, never claimed here")
cap("evpack2", "Open evidence package V2",
    ["backend/techlab/business/evpack_v2.py",
     "backend/tests/test_techlab/test_m03_d04_evpack_v2.py"],
    "DESKTOP_VERIFIED", ["M03-D04", "FE-G01"],
    deps=["immutable-storage"], platforms=["win"],
    unfinished="")
cap("web-verifier", "Offline HTML verifier",
    ["docs/web-verifier/verifier.html", "backend/tools/web_verifier.py"],
    "DESKTOP_VERIFIED via mirror; REAL BROWSER RUN PENDING",
    ["M03-D09", "FE-G09"],
    deps=["evpack2", "trust-engine-10d"], platforms=["browser"],
    unfinished="FE-G09: Playwright tool available - real browser pass queued P0")
cap("interop-formats", "8-format delivery + native consumers",
    ["backend/tests/test_techlab/test_m03_d10_interop.py"],
    "DESKTOP_VERIFIED", ["M03-D10", "FE-G10"],
    deps=["evpack2", "measurement-formats"], platforms=["win"],
    unfinished="English third-party verification guide queued")

# ---------------------------------------------------- H: business & iOS
cap("business-templates", "8 templates + checklist + forms",
    ["backend/techlab/business/templates.py",
     "swift/WorkCamCore/Sources/WorkCamModels/WorkCamBusiness.swift"],
    "DESKTOP_VERIFIED (Swift CROSS_PLATFORM_PENDING)",
    ["M03-B01/02/03", "FE-H01/02/03"],
    deps=[], platforms=["win", "mac"],
    unfinished="form version migration queued")
cap("issue-lifecycle-biz", "Lifecycle + roles + naming",
    ["backend/techlab/business/issues.py"],
    "DESKTOP_VERIFIED", ["M03-B04/05/06", "FE-H04-part"],
    deps=["business-templates"], platforms=["win", "mac"],
    unfinished="Mac state-machine diff re-check queued")
cap("photo-ops-search", "Batch planner + offline search",
    ["backend/techlab/business/photo_ops.py"],
    "DESKTOP_VERIFIED", ["M03-B07/08", "FE-H05"],
    deps=[], platforms=["win"], unfinished="")
cap("assets-dailylog", "Assets + measurements + daily log",
    ["backend/techlab/business/records.py"],
    "DESKTOP_VERIFIED", ["M03-B09/10/11", "FE-H06"],
    deps=[], platforms=["win"], unfinished="")
cap("swift-sync-package", "Outbox/upload/api/recovery Swift package",
    ["swift/WorkCamCore/Sources/WorkCamSync/*.swift"],
    "CROSS_PLATFORM_PENDING (Integration-02 parity encoded)",
    ["M03-B12-part", "TM67/69/70", "FE-H08"],
    deps=["api-client-parity", "chunked-upload"], platforms=["mac"],
    unfinished="")
cap("ios-design-system", "Design system + core screens reference",
    ["(not started)"],
    "NOT_STARTED", ["TM82-87", "M03-E02-07", "FE-H09/H10"],
    deps=["swift-sync-package"], platforms=["mac"],
    unfinished="P1 queue; Night 05 audit FE-E01 first to avoid duplication")
cap("xcuitest-suite", "XCUITest reference",
    ["(not started)"],
    "NOT_STARTED", ["TM90", "M03-E10", "FE-H12"],
    deps=["ios-design-system"], platforms=["mac"],
    unfinished="needs Mac accessibility identifiers")
cap("sync-center-settings", "Sync center + settings reference",
    ["(not started)"],
    "NOT_STARTED", ["M03-E08/09", "FE-H11"],
    deps=["swift-sync-package"], platforms=["mac"],
    unfinished="P1 queue")

# ---------------------------------------------------- I: security
cap("authz-audit", "Identity/token/ownership/cross-user",
    ["backend/tests/test_mvp_security.py",
     "backend/tests/test_techlab/test_b02_w021_security_audit.py",
     "backend/tests/test_techlab/test_m03_a06_night05_contract.py"],
    "DESKTOP_VERIFIED", ["TM43/21", "M03-A08-part", "FE-I01"],
    deps=[], platforms=["win"], unfinished="")
cap("parser-security", "JSON/CBOR/DER/B64/ZIP/CSV hostile inputs",
    ["backend/tests/test_techlab/test_tm44_parser_fuzz.py"],
    "DESKTOP_VERIFIED (3 real defects fixed)",
    ["TM44", "FE-I02"], deps=[], platforms=["win"],
    unfinished="CSV injection + image-metadata sweep queued")
cap("resource-limits", "Bombs/nesting/oversize/concurrency",
    ["backend/tests/test_techlab/test_tm44_parser_fuzz.py (zip bomb)",
     "backend/tools/appattest/cbor_min.py (depth cap)"],
    "DESKTOP_VERIFIED", ["FE-I03", "FE-D08-part"],
    deps=[], platforms=["win"],
    unfinished="hostile Content-Length upload probe queued")
cap("path-safety", "Traversal/absolute/dupes/temp isolation",
    ["backend/techlab/business/evpack_v2.py (safe_rel_path)",
     "backend/tools/evidence-verifier/verify_evidence.py (zip-slip)"],
    "DESKTOP_VERIFIED", ["FE-I04"], deps=[], platforms=["win"],
    unfinished="symlink case documented (Windows store has no symlinks)")
cap("secret-hygiene", "Fingerprint-registered test keys + scans",
    ["backend/test-keys/MANIFEST.sha256",
     "backend/tools/quality_gate.py (sensitive scan)"],
    "DESKTOP_VERIFIED (11 registered digests)",
    ["M03-A08", "FE-I05"],
    deps=[], platforms=["win"],
    unfinished="git-history scan queued (low risk: gate holds new adds)")
cap("network-security", "TLS/redirect/timeout/isolation",
    ["(client-side rules live in Swift package; server is loopback by design)"],
    "DESKTOP_VERIFIED (client refuses redirects; TLS is a deployment concern)",
    ["FE-I06"], deps=["api-client-parity"], platforms=["win", "mac"],
    unfinished="production TLS = deployment doc (never disabled in tests)")
cap("data-privacy", "Location/photo/OCR/log audit",
    ["backend/app/services/device_identity.py (audit redaction)",
     "docs/evidence/tm08-provenance-threat-model.md"],
    "DESKTOP_VERIFIED (token never logged; audit redacted)",
    ["FE-I07"], deps=[], platforms=["win"],
    unfinished="privacy manifest (App Store) queued")
cap("supply-chain", "Dependency/licenses/SBOM",
    ["backend/requirements.txt", "backend/tools/quality_gate.py (license gate)"],
    "DESKTOP_VERIFIED (9 pinned, permissive)",
    ["TM47-part", "FE-I08"],
    deps=[], platforms=["win"],
    unfinished="full SBOM (transitive) queued")
cap("flaky-fix", "F08 poll-thread deterministic sync",
    ["backend/tests/test_techlab/test_m03_f08_races.py"],
    "DESKTOP_VERIFIED currently; ROOT-CAUSE FIX QUEUED",
    ["FE-I09"],
    deps=["multi-client-conflicts"], platforms=["win"],
    unfinished="P0: the batch-prefix observer races on thread start; fix = "
               "start reader AFTER batch thread begins or use an event-sync")
cap("security-regression", "Integrated final security regression",
    ["backend/tools/quality_gate.py"],
    "DESKTOP_VERIFIED (12/12)",
    ["FE-I10"], deps=[], platforms=["win"],
    unfinished="expands as I-queue items land")

# ---------------------------------------------------- J: enterprise
cap("enterprise-tenant", "Multi-tenant isolation candidate",
    ["backend/tests/test_techlab/test_b02_w028_tenant_poc.py",
     "docs/commerce/b02-w028-commerce-foundations.md"],
    "POC_VERIFIED (isolated)", ["TM48/49/50/51", "M03-G*", "FE-J01-08"],
    deps=[], platforms=["win"],
    unfinished="P2 by directive: never blocks the personal-pro launch")

# ---------------------------------------------------- K: integration
cap("mac-ready-frozen", "Mac Ready 01-04 frozen manifests",
    ["handoff/mac-ready/integration-manifest.json",
     "handoff/mac-ready/01-START-HERE.md"],
    "DESKTOP_VERIFIED (12 components; refreshed per batch)",
    ["M03-H09/10", "FE-K03/04/05/06"],
    deps=[], platforms=["mac"],
    unfinished="K05/K06 groups = same manifest, sectioned by component id")
cap("rc3-stable", "RC3 stable candidate",
    ["branch dev/windows-tm-geometry @ current HEAD"],
    "INTEGRATED (this branch IS the RC3 candidate; main untouched)",
    ["FE-K07"], deps=["mac-ready-frozen"], platforms=["win"],
    unfinished="RC4 branch split queued once P1 queues land")
cap("clean-room-acceptance", "Fresh clone + full gate on fixed SHA",
    ["backend/bootstrap.ps1", "backend/tools/quality_gate.py"],
    "DESKTOP_VERIFIED at 8d79c20 (fresh clone earlier); re-run at freeze",
    ["FE-K10"], deps=["rc3-stable"], platforms=["win"],
    unfinished="re-run at the final freeze SHA")
cap("device-acceptance-kit", "User-operable device acceptance pack",
    ["docs/mac/TM55-HANDOFF.md"],
    "CHECKLIST EXISTS; FE-K11 user-friendly pack queued",
    ["FE-K11"], deps=["mac-ready-frozen"], platforms=["mac"],
    unfinished="P0 for the weekend: step-by-step, no-code-reading-required")


def main() -> None:
    out = ROOT.parent / "docs" / "management" / "task-matrix.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    counts: dict[str, int] = {}
    for c in C:
        counts[c["verify_status"]] = \
            counts.get(c["verify_status"], 0) + 1
    doc = {
        "matrix_version": "unified-matrix/1.0",
        "generated_from": "backend/tools/fe_k01_matrix.py",
        "baseline": {
            "branch": "dev/windows-tm-geometry",
            "report_head": "852253b",
            "full_suite": "1589 passed / 3 skipped / 0 failed",
            "gate": "12/12 PASSED (re-run on new versions per discipline)",
        },
        "status_vocabulary": [
            "NOT_STARTED", "IMPLEMENTED", "DESKTOP_VERIFIED",
            "CROSS_PLATFORM_PENDING", "XCODE_PENDING", "DEVICE_PENDING",
            "POC_VERIFIED", "BLOCKED", "ISOLATED_CANDIDATE", "INTEGRATED"],
        "counts": counts,
        "capabilities": C,
        "risk_ledger": [  # FE-K02: every open risk, by capability
            {"capability": "crosslang-vectors",
             "risk": "FE-A02 asks >=1000 independent cases; have 174",
             "class": "work-queued"},
            {"capability": "standalone-verifiers",
             "risk": "web verifier never ran in a REAL browser",
             "class": "verification-gap",
             "fix": "Playwright real-browser pass queued P0"},
            {"capability": "flaky-fix",
             "risk": "F08 prefix-observer timing flake (1 in ~3 full runs)",
             "class": "test-stability",
             "fix": "deterministic event sync queued P0"},
            {"capability": "swift-geometry/swift-sync-package",
             "risk": "never compiled on Windows (toolchain BLOCKED)",
             "class": "cross-platform",
             "mitigation": "vector-matched + rule-tested; Mac compiles"},
            {"capability": "app-attest-server",
             "risk": "synthetic CAs; real Apple root unverified",
             "class": "device-pending",
             "mitigation": "Mac cross-check in acceptance kit"},
            {"capability": "image-registration",
             "risk": "no implementation; selection review open",
             "class": "not-started",
             "boundary": "never auto-judges engineering quality"},
            {"capability": "ocr-engineering",
             "risk": "no implementation",
             "class": "not-started"},
            {"capability": "longrun-stability",
             "risk": "no soak runner",
             "class": "not-started"},
            {"capability": "download-recovery",
             "risk": "downlink originals not implemented",
             "class": "not-started"},
            {"capability": "trust-engine-10d",
             "risk": "production route switch awaits product review",
             "class": "decision-required"},
            {"capability": "audit-chain",
             "risk": "no external anchor => tail truncation undetectable",
             "class": "stated-limitation",
             "boundary": "documented + proven by test"},
            {"capability": "measurement-quality",
             "risk": "uncalibrated LiDAR = conservative defaults only",
             "class": "device-pending"},
            {"capability": "evpack2",
             "risk": "browser zip reader refuses ZIP64 (honest message)",
             "class": "stated-limitation",
             "mitigation": "CLI covers large packages"},
        ],
    }
    out.write_text(json.dumps(doc, indent=1, ensure_ascii=False),
                   encoding="utf-8")
    print(f"{len(C)} capabilities, "
          f"{sum(counts.values())} entries -> {out}")
    for k, v in sorted(counts.items()):
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
