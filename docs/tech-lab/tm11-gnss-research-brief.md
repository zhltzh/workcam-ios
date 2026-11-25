# TM11 · Offline GNSS Capability Research (Mac executes collection)

## Division of labor

Windows owns: experiment design, data schema, statistics tooling
(reuse the B02-W016 analyzer pipeline: CSV -> validation -> grouped
metrics).
Mac owns: ACTUAL iPhone collection. Nothing below is a measurement
until Mac uploads the CSVs - every number in the eventual report will
come from real device rows, not from this document.

## Positioning source taxonomy (offline-first)

| Source | Available offline? | Notes |
|---|---|---|
| GNSS (GPS/GLONASS/Galileo/BeiDou/QZSS) | YES (the core case) | assisted data missing extends cold-start; chips still fix standalone |
| Wi-Fi positioning | NO | Apple's crowd-sourced database needs network |
| Cell-tower positioning | NO | radio is off in airplane mode |
| Cached last-fix | partial | stale-fix rules apply (TM12) |
| Manual pin | YES | human-entered, never graded (TM12) |
| Unknown | YES | recorded as-is, never upgraded |

## Experiment matrix (Mac collection protocol)

Per-cell minimum: 5 sessions x 5 fixes, recorded in the B02-W016 CSV
schema (mode/distance/material/light columns map to
scenario/accuracy/fix-count below; a dedicated GNSS CSV profile is
defined in `docs/tech-lab/experiments/tm11-gnss-protocol.md` once Mac
starts - do not fabricate rows before then).

| # | Scenario | Airplane mode | Duration target | Metrics |
|---|---|---|---|---|
| G1 | Cold start, open sky | ON | time-to-first-fix, fix-rate curve | TTFF, accuracy over time |
| G2 | Warm start (fix < 30 min ago) | ON | re-acquisition time | TTFF warm |
| G3 | Dense urban canyon | ON | 10 min walk | accuracy bands, fix dropouts |
| G4 | Indoors, window seat | ON | 10 min | accuracy, stale-fix frequency |
| G5 | Deep indoors / basement | ON | 10 min | fix absence rate (record ABSENCES - they are data) |
| G6 | Vehicle, 60 km/h | ON | 10 min drive | accuracy under motion, jump rate |
| G7 | Same spot re-visit (3 days) | ON | 5 min per day | day-to-day accuracy variance |
| G8 | Airplane-mode OFF (network assist) | OFF | repeat G1 | the a-GPS delta - the CONTROL row |

Permission states to cover: authorized-when-in-use (production),
authorized-always, denied (fix absent - record as source=denied, not
as a failure of the hardware). Precise vs approximate location toggle
(App Settings) must be a recorded column - approximate mode changes
horizontalAccuracy semantics.

## Data schema (rows Mac uploads)

fix_timestamp, latitude, longitude, horizontal_accuracy_m,
vertical_accuracy_m, altitude_m, source (gnss/manual/none/denied),
airplane_mode (0/1), permission_state, precise_toggle, session_id,
scenario (G1..G8), device_model, os_version, notes.

Mandatory honesty rules for the eventual analysis:
1. ABSENT fixes are rows too (source=none) - the absence rate is a
   headline metric, not dropped data.
2. Accuracy figures are per-scenario distributions (P50/P95), never
   a single best-case number.
3. Cold-start TTFF distribution carries the session temperature
   context; a-GPS (G8) results are reported SEPARATELY from offline
   rows, never mixed into an "offline capability" figure.
4. Simulator or mocked locations are FORBIDDEN rows in this dataset.

## Status

DESIGN COMPLETE on Windows; COLLECTION DEVICE_PENDING on Mac. The
statistics tooling reuses `tools/tech-lab` analyzer conventions
(failure rows preserved, grouped percentiles, DESKTOP vs DEVICE rows
never mixed).
