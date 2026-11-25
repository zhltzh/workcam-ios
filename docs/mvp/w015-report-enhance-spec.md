# WorkCam B02-W015 — Professional Inspection Report Enhancement Spec

Version 1 · Branch `dev/windows-b02-w015-report-enhance` · Backend template `REPORTS_VERSION = "2"`

Status: DESKTOP_VERIFIED. All samples are synthetic desktop data
(DESKTOP_SIMULATION); real-device LiDAR data is DEVICE_PENDING.

## 1. What was added on top of W012/W013-R1

| # | Feature | Where |
|---|---|---|
| 1 | Inspection Issue register (problem list) with report-scoped numbers `ISS-001…` | PDF `_issue_section`, register table |
| 2 | Severity classification breakdown (low/medium/high/critical counts) | PDF `_summary_table`, API `issue_summary.severity` |
| 3 | Remediation status summary (open / in_progress / resolved / closed counts) | PDF `_summary_table`, API `issue_summary.status` |
| 4 | Before/after comparison figure (embedded pair, role-labeled, per issue) | PDF `_before_after_pair` |
| 5 | Photo reference numbering `PH-001…` + issue→photo reference system | PDF `_ref_map`, captions, register rows |
| 6 | Inspector + project metadata block (front page, MANUAL INPUT) | PDF `_front_page`, API `inspector_name` / `inspector_title` / `project_metadata` |
| 7 | Human-entered remediation recommendations, labeled MANUAL INPUT | Issue model `remediation_note` (migration v9), PDF issue detail |
| 8 | Manual sign-off area (signature lines; layout only, no legal claim) | PDF `_sign_off_section` |
| 9 | LiDAR measurement record references (B02-W016 record ids, EXPERIMENTAL class, DEVICE_PENDING) | API `lidar_measurements`, PDF `_lidar_section` |
| 10 | PDF + ZIP delivery package | W012 surface, extended with issue snapshots + report_meta |
| 11 | A4 and US Letter dual page sizes | Both layouts verified with the full section stack |

## 2. API changes

`POST /api/v1/projects/{pid}/reports` request additions:

```
inspector_name   str ≤128 (MANUAL INPUT)
inspector_title  str ≤128 (MANUAL INPUT)
project_metadata dict[str,str], ≤10 entries, key ≤64, value ≤300
lidar_measurements list[≤50] of:
    record_id         str 1..128, REQUIRED (B02-W016 record id)
    description       str ≤500
    related_issue_id optional; must be inside issue_ids (else 400
                      lidar_ref_issue_mismatch)
    data_status       str ≤32, default "DEVICE_PENDING"
```

Issue model additions (B02-W015 migration v9, additive ALTER TABLE):

```
remediation_note  TEXT NOT NULL DEFAULT ''
```

- `POST/PATCH /api/v1/projects/{pid}/issues` and `/issues/{iid}`
  accept `remediation_note`; response `note` says
  "severity/status/remediation_note are HUMAN records; no AI assessment implied".
- Sync batch ops `issue_create` / `issue_update` carry `remediation_note`
  (idempotency and optimistic revision unchanged).

Report response additions: `issue_count`, `issue_summary`
(`severity` / `status` / `total`), `source_classes`.
Snapshot JSON gains `report_meta` (inspector, project metadata, LiDAR
refs, issue summary); embedded comparison-figure bytes are stripped
from stored snapshots (`_issues_json_view`).

## 3. Report layout (template v2)

```
1 Front page        report number, generated, page size, operator,
                    company, inspector (+title), project metadata rows,
                    Source Classification legend
2 Photo pages       PH-nnn caption + RAW EVIDENCE tag, capture/GPS/evidence
3 Issue register    ISS-nnn rows: title, severity, status, linked PH refs
4 Severity breakdown + remediation status summary (count tables)
5 Issue details     per-issue: severity/status [MANUAL INPUT], description,
                    remediation recommendation (MANUAL INPUT), photo refs,
                    before/after figure pair + disclaimer, history timeline
6 LiDAR references  record id table + EXPERIMENTAL MEASUREMENT + per-row
                    data status (DEVICE_PENDING)
7 Manual sign-off   signature lines (Inspector / Reviewed by / Approved by)
8 Evidence appendix (W012, unchanged)
Footer              "reference document, not a legal certification"
```

## 4. Four-tier source classification (wording red line #1)

Every content element carries exactly one class, declared in the
front-page legend and labeled in place:

| Class | Meaning | Where used |
|---|---|---|
| RAW EVIDENCE | original capture bytes, immutable, SHA-256 recorded | photo pages, before/after figures |
| MANUAL INPUT | human-entered record; not an automated assessment | severity, status, remediation notes, inspector block, sign-off, issue associations |
| EXPERIMENTAL MEASUREMENT | sensor estimate under test conditions; not survey-grade; device data status stated per row | LiDAR reference rows |
| DERIVED IMAGE | image generated from an original; never a substitute for the original | legend; W011/W013 derivatives cited in the appendix |

Tests: `test_source_classification_labels_in_pdf`,
`test_pdf_remediation_note_manual_input`,
`test_lidar_references_render`.

## 5. No-certification wording rule (wording red line #2)

The report must never present human sign-off or measurements as legal
certification / attestation. Implemented statements:

- Footer (every page): "Generated by WorkCam - reference document,
  not a legal certification".
- Sign-off section: "…no legal attestation, certification or
  notarization is made or implied." Signature = record-keeping only.
- LiDAR section: "No measurement in this report is presented as a
  certified or legally attested value."
- Source legend: "No element in this report constitutes a legal
  certification, attestation, or notarized statement."
- Evidence appendix (W012, unchanged): "NOT a legally certified or
  notarized file".

Enforced by `test_sign_off_section_and_no_legal_attestation`: every
occurrence of `certif*` / `notariz*` in the extracted PDF text must sit
inside a negation window; "sworn" / "legally binding" must not appear.

## 6. LiDAR reference format (B02-W016 interop)

Record ids follow the B02-W016 experiment protocol
(`docs/tech-lab/experiments/b02-w016-lidar-experiment.md`):

- row id: `B02-W016-<mode>-<condition>-<dist>m-R<n>` (e.g.
  `B02-W016-raycast-normal-2m-R1`)
- batch id (description field): `B02W016-<date>-<batch>`

Data statuses rendered per row:

- `DEVICE_PENDING` (default) - real-device data not captured
- `DEVICE_VERIFIED` - verified in the B02-W016 experiment dataset
- `DESKTOP_SIMULATION` - synthetic analyzer data, not device results

The report cites record ids only; it never renders a measured value.
References must point at rows that exist in the B02-W016 dataset.

## 7. Samples (real runs, not handcrafted)

`docs/mvp/w015-samples/` - generated by
`python tools/tech-lab/validation/gen_w015_samples.py`:

| File | Route | Pages |
|---|---|---|
| inspection_full_a4.pdf | full API chain (issues, before/after, remediation, LiDAR, sign-off) | 8 |
| inspection_full_letter.pdf | same scenario, US Letter | 8 |
| inspection_open_items_a4.pdf | full API chain, open-items register | 5 |
| evidence_report_with_issues_a4.pdf | full API chain + evidence appendix | 9 |
| site_photo_letter.pdf | full API chain, site photo template, US Letter | 4 |
| inspection_grid4_a4.pdf | direct PDF engine, 4-up grid | 4 |
| inspection_register8_a4.pdf | direct PDF engine, 8-issue register | 7 |
| delivery_package_full.zip | full API chain (report.pdf + originals + evidence + manifest) | 7 files |

Five of the PDFs and the ZIP come from the complete API chain
(enrollment -> project -> photos -> issues -> transitions ->
photo links -> report -> download / delivery-package) on a throwaway
SQLite instance.

## 8. Tests

`backend/tests/test_w015_report.py` (16 tests), full suite
`648 passed, 3 skipped` at delivery (baseline `31917e3` was
632 passed / 3 skipped).
