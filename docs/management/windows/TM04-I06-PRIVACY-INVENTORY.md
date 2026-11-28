# TM-04 I06: Privacy Data Inventory (candidate)

Status: CANDIDATE inventory for product review · 2026-11-28
Scope: what the WorkCam system (single-user stable product +
backend + the TM-04 experimental reserve) actually stores, where,
for how long, and under which control.

## Principle

WorkCam is an evidence tool. Its privacy posture is defined by one
rule: **evidence integrity beats feature convenience**. Nothing in
this inventory may be relaxed to add a feature.

## 1. Data categories actually collected

| # | Category | Fields | Where | Lifetime | Control |
|---|---|---|---|---|---|
| 1 | Account/device identity | public_key_b64 (P-256), device_id, enrollment code, device_token | `mvp_devices` (server DB) | account lifetime | owner-enrollable; token revocable via device admin API |
| 2 | Project metadata | name, description, site_address, status, revision | `mvp_projects` | until archived + deleted by owner | full CRUD by owner |
| 3 | Photo originals | image bytes + SHA-256 | immutable object store | until owner deletes | NEVER auto-deleted (fsck discipline) |
| 4 | Photo provenance | original_filename, mime, byte_size, captured_at (device-claimed, UNTRUSTED), device_id | `mvp_photos` | with the photo | captured_at is DATA not fact (time-anchor gate) |
| 5 | Annotations | geometry points, kind, text content, z-order | `mvp_annotations` | with the photo | editable pre-freeze |
| 6 | Issues | title, severity, status history, remediation note (human-entered) | `mvp_issues` + event log | until owner deletes | lifecycle-guarded |
| 7 | Sync bookkeeping | operation ids, revisions, receipts, cursors | outbox + event log | append-only | never rewritten (audit discipline) |
| 8 | Location (structured, TM-04 D07) | area/floor/room/spot path (user-entered) | photo/issue records | with the record | free text was already stored; now normalized |
| 9 | OCR candidates (TM-04 E09) | recognized text hypothesis + bbox + confidence | local device only (Vision) | until confirmed/discarded | UNCONFIRMED candidates never enter the search index (B08 gate) |

**Explicitly NOT collected** (as of RC3/TM-04):
- Contacts, calendar, photo-library-wide access, health, ad IDs,
  analytics, crash-reporting SDKs: none in the dependency SBOM (TM-04
  H01: 9 direct deps, all server-side libraries).
- No third-party analytics/networking SDK exists in the Swift
  package (WorkCamCore: zero external dependencies).
- Server logs: access log carries the request line only (no
  payloads); not persisted beyond the process by default.

## 2. App permissions requested (iOS surface)

| Permission | Purpose | Denied behavior |
|---|---|---|
| Camera | the product's core capture | shutter blocked with honest copy |
| Location WhenIn-Use | stamps photos with capture location | photos proceed WITHOUT location; UI labels it missing (no fake zeros) |
| Photo Library Add-only | saving watermarked copies out | export via Files instead |

No other permission prompts exist. The TM-04 C13 FeatureGate is
the single authority for the denied/undetermined copy.

## 3. Data lifecycle summary

- **Capture → local evidence**: originals immutable on store;
  derivatives (watermarks/previews) derivable and disposable; the
  ORIGINAL is never mutated (red-line, C09/E10 proven).
- **Sync**: outbox queue local-first; server receives operations +
  media; nothing is shared cross-owner (B13: 404 + rejected writes).
- **Archive**: read-only lock (D14), restore explicit.
- **Deletion (candidate I05)**: two-phase, 7-day window, explicit
  plan listing originals as gone-forever; engine never executes.
- **Export (candidate I05)**: full portability with originals +
  hash-verified manifest (schema workcam-export/1.0).
- **Account removal (server)**: DECISION_REQUIRED product step;
  the technical flow is modeled, the launch decision is not made
  here.

## 4. Third-party exposure

- Zero. No third-party API receives WorkCam data. The backend is
  self-hosted (local Docker candidate I09 authored, BLOCKED until
  Docker Desktop exists; native deploy verified).
- OSV-scanned dependency set (TM-04 H01): 39 installed components,
  0 known vulnerabilities at scan time after the pip CVE remediation
  (25.0.1 → 26.3). Scan honesty: OSV performed via local proxy; a
  failed scan is reported as NOT PERFORMED, never as clean.

## 5. Open items for product review (honest list)

1. captured_at is device-claimed and untrusted by design — the
   product copy must keep saying "device time" wherever it is shown.
2. The enterprise reserve (H02-H10) would add member ids and audit
   journals; that inventory section is deliberately unwritten until
   the enterprise line is a real product decision.
3. The deletion waiting-window length (7d) and grace length (14d)
   are candidate numbers, not launch commitments.
