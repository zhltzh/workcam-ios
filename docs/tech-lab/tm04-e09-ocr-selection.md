# TM-04 E09 (was TM-03 C08): OCR — Selection Review + Data Contract

Status: REVIEW COMPLETE → contract + schema + sample generator
delivered; recognition itself is Apple-platform only (DEVICE/XCODE
side), Windows never claims recognition results.
Owner: Windows session, 2026-11-28
Code: `swift/WorkCamCore/Sources/WorkCamModels/OCRReference.swift`
(real Windows compile + unit tests) ·
`backend/tools/gen_ocr_samples.py` (legitimate synthetic sample set) ·
`backend/tests/test_techlab/test_tm04_e09_ocr_schema.py` (cross-language parity)

## Candidates reviewed

| Engine | License/deps | On-device | Effort | Verdict |
|---|---|---|---|---|
| **Apple Vision `VNRecognizeTextRequest`** | system framework, zero deps, no审核 surface | yes | low (one protocol seam) | **ADOPTED — the only production path** |
| Tesseract (iOS ports) | Apache-2.0 but a heavy C++ lib, tessdata bundles, brittle Swift wrappers | yes | high | rejected for v1 (accuracy on industrial plates is weak; maintenance burden) |
| Google ML Kit | free tier + Google components / policy surface | mixed | medium | rejected (privacy posture: text leaves app process by default is unacceptable for evidence contexts) |
| PaddleOCR | Apache-2.0, best accuracy, but server-grade model + iOS integration is a research project | no (v1) | very high | rejected for v1; possible desktop-side evaluator later |

Rationale in one line: WorkCam's OCR scope is engineering labels
(serials, plates, gauge readings, location stickers) photographed
close-up; Vision's accuracy levels cover this today with ZERO new
dependencies, keeping the app's privacy promise (no text leaves the
device).

## The contract (what Windows delivered)

1. **`OCRCandidate`** — engine hypothesis: text, normalizedText
   (uppercase/whitespace-collapsed comparison key), confidence
   (reported, NEVER a decision input), normalized bbox in Vision's
   top-left convention.
2. **`OCRFieldType`** — the closed taxonomy: serial_number,
   model_plate, measurement_reading, location_label, date_code,
   barcode_text, other. No free-form auto-classification.
3. **`OCRFieldCandidate` + `OCRConfirmationState`** — the human
   confirmation machine: `unconfirmed → confirmed(text, by, at)` or
   `→ discarded(reason)`; both transitions are user-explicit, final
   for that attempt, and audited (who/when). **No code path derives
   `confirmed` from a confidence threshold** — the state payload
   cannot be constructed without the caller supplying a human id.
4. **`isSearchable`** — the single gate the offline search index
   uses: OCR text becomes searchable ONLY in the `.confirmed` state
   (this pins the TM-03 B08 contract at type level).
5. **`TextRecognitionService`** — the seam Mac adopts:
   `recognize(imageData:region:) -> [OCRCandidate]` +
   `recognitionLanguages`. Mac implements with VNRecognizeTextRequest;
   tests implement with scripted fixtures (the Windows suite runs
   those against the CONTRACT, not against recognition).

## Legitimate sample set (generator, no scraped photos)

`backend/tools/gen_ocr_samples.py` renders every taxonomy field
type with Pillow from pure programmatic text — no scraped images, no
third-party content, no privacy surface (all strings synthetic).
Per sample the generator emits the ground-truth manifest
(`manifest.json`: type, text, transform, expected-normalized-text)
so the Mac/Vision pass measures real recognition quality, and the
fixture doubles as schema demo data. Deterministic seeds; regen is
byte-stable.

## Accuracy claims policy

- Windows claims NOTHING about recognition accuracy (it cannot
  measure it — no Vision here).
- The Mac device pass reports accuracy per field type on the sample
  set; those numbers land in the acceptance kit, not in marketing.
- OCR never upgrades an issue to resolved, never writes to evidence
  records, and confirmed text stays user-owned data (editable by a
  later correction with a new audit entry).

## What is deliberately NOT here

- No language-detection magic: recognition languages are explicit
  (contract field), starting with en-US only.
- No handwriting claims.
- No auto-population of measurements from gauge photos — reading a
  gauge is a human step; OCR may propose, the user confirms.
