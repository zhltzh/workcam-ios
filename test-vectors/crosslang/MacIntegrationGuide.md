# B02-W023 Mac Integration Guide — Cross-Language Signature & Serialization Vectors

**Status: Swift side NOT VERIFIED yet.** The Windows side has produced the
vectors, the Python reference verifier and this Swift reference test as
text. Nothing in this task has been compiled or run on a Mac — running the
steps below is the missing Mac cross-validation. Until then every
Swift-side claim must be treated as `Not Implemented / pending`.

## What to integrate

| File | Where | Purpose |
|------|-------|---------|
| `test-vectors/crosslang/W023CrosslangVectorTests.swift` | any macOS/iOS unit test target | 17 test methods covering every vector section |
| `test-vectors/crosslang/w023-crosslang-vectors.json` | same test target, as a **bundle resource** | the fixed vectors (inputs + expected outputs) |

The Swift test is self-contained: it only needs `Foundation`, `CryptoKit`
and `XCTest`. It does **not** import the WorkCam app target, so it can run
in a scratch test target or an existing one.

## Step-by-step

1. Open the WorkCam Xcode project (or any scratch project on a Mac with
   Xcode 15+ / Swift 5.9+).
2. Create (or pick) a unit test target.
3. Add `W023CrosslangVectorTests.swift` to the test target
   (Target Membership checkbox).
4. Add `w023-crosslang-vectors.json` to the same test target as a
   resource. The test locates it via
   `Bundle(for: W023CrosslangVectorTests.self)`, so it must be a member
   of that target, not just a folder reference.
5. Run `W023CrosslangVectorTests`. All 17 test methods should pass if the
   two languages agree byte-for-byte.

## What each test validates

| Test method | Vector section | Cross-language question answered |
|---|---|---|
| `testSha256Vectors` | `sha256_cases` | CryptoKit SHA-256 == hashlib SHA-256 (FIPS KATs) |
| `testBase64Vectors` | `base64_cases` | `Data(base64Encoded:)` strictness == Python `b64decode(validate=True)` |
| `testPublicKeyEncodingVectors` | `public_key_cases` | 65-byte/0x04 format gate == Python; compressed/SPKI/PEM rejected on both |
| `testDerStructureVectors` | `der_cases` | DER structural check parity (incl. the BER non-minimal finding) |
| `testPayloadContract` | `payload_contract` | Exact signed payload bytes, line map, single trailing LF, no CR |
| `testSignedManifestCases` | `signed_manifest_cases` (15 cases) | **THE core check**: Swift rebuilds identical payload bytes and CryptoKit verifies the RFC 6979 signatures |
| `testSignedCaseLocationBitPatterns` | per-case `location_bits_hex` | IEEE-754 bits survive JSON round-trip identically |
| `testUnicodeCases` | `unicode_cases` | `String.utf8` bytes == Python `str.encode()` (NFC≠NFD, bidi, emoji) |
| `testNewlineRejects` | `newline_cases` | CRLF/CR rebuilds must fail; field values with LF/CR rejected |
| `testFloatV1_1Table` | `float_cases` | **Mac-side headline check**: the ported `formatFloatV11` (integer branch + `%.10g`) must match every Python-produced string |
| `testFloatV1_0RiskTablePendingMacOutput` | `float_cases` | documents the v1.0 repr drift risk (bits round-trip only) |
| `testNullVsAbsentEquivalence` | `null_absent_cases` | JSON `null` vs absent key == same payload on both languages |
| `testGpsValidCases` / `testGpsRejectCases` | `gps_cases` | Same bounds (lat ±90, lon ±180, acc > 0, alt [-1000, 100000]) and same `-0.0` handling |
| `testIso8601Cases` | `iso8601_cases` | Same shape regex; timestamps stay opaque strings |
| `testTimezoneCases` | `timezone_cases` | `Z` vs `+00:00` vs `+08:00` are different bytes/signatures |
| `testFieldOrderCase` | `field_order_cases` | JSON key order cannot affect the payload |
| `testTamperCases` | `tamper_cases` (13 cases) | every single-byte change must fail verification |
| `testVersionCases` | `version_cases` | version↔domain binding; unknown versions rejected before serialization (no Python fallback mimicry) |

## What to do if a test fails

A failure is a **finding**, not a bug in the test: record the exact
assertion message (the tests print the Python expected value and the
Swift actual value). Known candidates for legitimate divergence are
exactly the ones flagged in
`docs/crosslang-serialization-proposal.md`:

1. `testFloatV1_1Table` — if `String(format: "%.10g")` differs from
   Python's `%.10g` for any row, that row is a real cross-language
   drift bug in the v1.1 float rule and the proposal must be updated
   with the actual measured values.
2. `testDerStructureVectors` — CryptoKit
   `ECDSASignature(derRepresentation:)` may be stricter or looser than
   the OpenSSL-backed Python path for the BER non-minimal case
   (`der-r6-ber-nonminimal-r`). Record which side rejects/accepts.
3. `testBase64Vectors` — if `Data(base64Encoded:)` accepts a reject
   vector (e.g. non-canonical padding bits), tighten the Swift helper.

## Out of scope for this integration

- No changes to the production Swift signing code (`Core/Security/`).
  This test validates the *contract*, not the app implementation.
- If you additionally want to diff the app's own payload builder against
  these vectors, that is a separate follow-up task (the M003
  `EvidenceManifest.payload()` currently hardcodes `location=none` and
  v1.1 only — see the proposal doc).

## Reporting back

Append to `docs/progress/b02-w023-crosslang-2026-11-25.md`:

- Xcode + macOS version, test target type
- Full test output (all 17 methods)
- Any divergence findings with exact byte/string diffs
- Only after a clean full pass may the Swift status be upgraded from
  `pending Mac cross-validation` to `verified on macOS <version>`.
