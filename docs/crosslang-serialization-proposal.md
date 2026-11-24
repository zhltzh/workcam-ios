# Cross-Language Serialization Consistency Proposal (B02-W023)

**Scope:** proposal + test vectors + reference validators only. This
document does NOT change the production signature protocol. Any change
to `docs/signature-protocol.md` or to backend production code requires a
separate review and authorization.

**Companion artifacts:**

- Fixed vectors: `test-vectors/crosslang/w023-crosslang-vectors.json`
  (deterministic, RFC 6979-signed, byte-reproducible)
- Python independent verifier: `backend/w023_reference_verifier.py`
- pytest production-agreement tests: `backend/tests/test_w023_crosslang.py`
- Swift reference tests (NOT yet run on any Mac):
  `test-vectors/crosslang/W023CrosslangVectorTests.swift`
- Mac integration steps: `test-vectors/crosslang/MacIntegrationGuide.md`

---

## 1. Review findings on the current location-field encoding

All findings below were verified against the actual code in this branch
(`backend/app/services/serialization.py`, `backend/app/services/signature.py`)
and the Swift signing code delivered on `origin/dev/mac-evidence-m003`
(`Core/Security/Evidence.swift`). Each is frozen as a vector or recorded
in the vector file.

### F1 (HIGH): the two production codebases do not agree on the location field *type*

- Python backend: `location` is a structured object
  (`LocationData`: latitude/longitude/horizontal_accuracy/altitude),
  serialized as `location={lat},{lon},{acc},{alt}`.
- Swift (M003 `EvidenceManifest`): `location` is a `String?`, and
  `payload()` **hardcodes `location=none` and guards `location == nil`**.
  A non-null location cannot be signed by the Swift side at all today.

Consequence: any record captured WITH a GPS fix can only be signed by the
backend path; the Swift capture path would reject its own location data.
This is the central "existing non-empty location field" inconsistency the
task asks to address. The vectors (`v11-location-full`,
`v11-location-altitude-none`, etc.) now pin the Python-side bytes; the
Swift production builder must be aligned to the same contract (Section 3)
before non-null location records can be cross-verified.

### F2 (HIGH): v1.0 embeds floats with Python `repr()` semantics

`_format_location_v1_0` uses f-string interpolation of raw doubles. Python
`repr()` uses shortest-round-trip with a switch to exponential form at
`x >= 1e16` or `x < 1e-4`. Swift `String(describing:)` also produces a
shortest-round-trip string but with **different exponent thresholds**, so
byte-identical output is not guaranteed across the two runtimes. Frozen
Python-side examples (real output, recomputed and frozen in
`float_cases.v1_0_repr_risk_table`):

| bits (hex)        | value                  | Python v1.0 string          |
|-------------------|------------------------|-----------------------------|
| `3fb999999999999a`| 0.1                    | `0.1`                       |
| `8000000000000000`| -0.0                   | `-0.0`                      |
| `3ff8000000000000`| 1.5                    | `1.5`                       |
| `430c6bf526340000`| 1e15                   | `1000000000000000.0`        |
| `4341c37937e08000`| 1e16                   | `1e+16`                     |
| `3ee4f8b588e368f1`| 1e-05                  | `1e-05`                     |

The Swift outputs for the same bits are **PENDING_MAC_VERIFICATION**
(`W023CrosslangVectorTests.testFloatV1_0RiskTablePendingMacOutput`
intentionally asserts only bit round-trip). v1.0 + location is the
cross-language hazard zone; v1.0 manifests without location do not embed
any float and are byte-safe.

### F3 (MEDIUM): the v1.1 float rule is portable but must be ported *whole*

The v1.1 rule (10 significant digits + integer branch + guaranteed decimal
point + `-0.0` handling) is deterministic and matches C `printf("%.10g")`
semantics available on both platforms, but a bare
`String(format: "%.10g")` is **not** equivalent:

- `1e15` → Python integer branch emits `1000000000000000.0` while bare
  `%.10g` would emit `1e+15`;
- `1e16` → integer branch emits `10000000000000000.0` (bare `%.10g`:
  `1e+16`);
- `-0.0` → Python emits `-0.0` (bare `%.10g`: `-0`);
- `1234567.0` → integer branch emits `1234567.0`.

The full 19-row v1.1 table with IEEE-754 bit patterns is frozen in
`float_cases.v1_1_format_table`; the reference Swift port is
`formatFloatV11()` in `W023CrosslangVectorTests.swift` (including the
2^53 / `%.0f` path for integer-valued doubles beyond Int64). Mac-side
confirmation is the headline check of the whole vector suite.

### F4 (MEDIUM): unknown protocol versions silently fall back to the v1.0 domain separator in the serializer

`serialization.py:canonical_payload()` maps `version == "1.1"` to the
v1.1 domain and **everything else** to `WorkCam-Sig-v1`. Structure
validation rejects unsupported versions first
(`validate_manifest_structure`), so the fallback is unreachable through
the public API — but a fresh Swift implementation that copies the
serializer logic would sign/verify under the wrong domain for unknown
versions. Frozen evidence: `version_cases.unknown_version_behavior`
(version `"2.0"` → domain line `WorkCam-Sig-v1` + structural rejection,
both recorded from real execution). Cross-language rule pinned by the
vectors and the reference verifier: **check
`version ∈ {"1.0", "1.1"}` BEFORE serialization and reject otherwise.**

### F5 (LOW): ISO 8601 validation is shape-only

`2026-13-01T00:00:00Z` (month 13) passes the regex; calendar semantics
are unchecked. Frozen in `iso8601_cases.shape_only_accepted_example`.
Both ends agree (both use the same regex shape), so it is not a
cross-language divergence, but it is a shared validation gap. Tightening
would require the same calendar check on both sides; proposed for v1.2,
not implemented here.

### F6 (MEDIUM): DER structural check is laxer than RFC 3279

The production checker validates SEQUENCE tag, declared length, two
INTEGER tags, and no trailing data — but not INTEGER minimality/padding
and not low-s. Empirically measured and frozen in
`der_cases.reject_cases["der-r6-ber-nonminimal-r"]`: a signature with
`r` encoded **without** the required leading `0x00` (non-minimal BER)
passes the production structural check, and the measured
crypto-verification outcome for the BER form is recorded as-is in the
vector. CryptoKit may behave differently — the Swift test records it.
Any tightening must land on both sides simultaneously.

### F7 (already-correct rule to preserve): timestamps are opaque strings

`Z` vs `+00:00` vs `+08:00` denote the same instant but are different
bytes with different signatures (`v11-timestamp-z-suffix` vs
`v11-timestamp-offset-plus-8` in `signed_manifest_cases`). Verifiers must
never parse-then-reformat. Pinned by vectors + `timezone_cases`.

### F8 (already-correct rule to preserve): null vs absent vs empty-string

JSON `null`, absent key and (for `location.altitude`) absent inner field
all serialize to the same payload and the same signature; the empty
string `""` is **not** equivalent (`previous_record_hash=` vs
`previous_record_hash=none`) and is rejected by structure validation
(length != 64). Pinned by `null_absent_cases` (including a real
signature that verifies under both JSON forms).

### F9 (already-correct rule to preserve): no Unicode normalization

NFC and NFD of the "same" text are different bytes and both sign
validly (`unicode_cases` u2 vs u3, plus signed cases with a
combining-character record_id, bidi RLM device_id and an astral-plane
emoji device_id). Neither side may normalize on the verification path
(no `unicodedata.normalize`, no `precomposedStringWith*`).

---

## 2. The byte contract (as-is, normative for v1.x)

The exact signed payload is fully specified by `payload_contract` in the
vector file (rules P1–P8) and enforced twice per case (Python verifier +
pytest against production). Summary:

1. UTF-8, no BOM, LF-only lines, exactly one trailing LF.
2. Line 1: domain separator bound to the version field.
3. Then 9 fixed-order `key=value` lines (FIELD_ORDER in
   `serialization.py`).
4. `null` → literal `none`; `location` → `lat,lon,acc,alt` (v1.1 float
   rule); `signature`/`public_key` are never part of the payload.
5. `record_hash = SHA-256(payload || signature_base64_ascii)`.

Field-order independence from JSON key order is pinned by
`field_order_cases` (one signature verifies across a shuffled-JSON
rebuild).

---

## 3. Proposal for the location field going forward (v1.2 — for review, NOT implemented)

Goal: byte-identical serialization on Python and Swift for non-null
location without relying on each runtime's float-to-text behavior.

### Option A — integer fixed-point (micro-degrees)

`location=399042000,1164074000,5000,43500` where
`lat/lon` are degrees × 10^7, `accuracy` in millimeters, `altitude` in
millimeters. Integer DECIMAL strings — no float formatting, no
exponent, trivially portable (`Int64` → decimal digits on both
platforms, identical). Precision ≈ 1.1 cm horizontal, 1 mm vertical —
matches or exceeds phone GPS reality.

Drawback: requires a domain-separator bump (v1.2) and changes
`record_hash` continuity; readers must convert for display.

### Option B — fixed 7-decimal digit strings (recommended)

`location=39.9042000,116.4074000,5.0000000,43.5000000`.

Rendering rule: `String(format: "%.7f")` / Python `f"{v:.7f}"` — a single
C `printf` call on both sides, no integer branch, no `%g` exponent, no
shortest-round-trip algorithm. Both runtimes use the same underlying
C-style conversion; the Mac-side vector run must still confirm this,
but unlike `%g`/repr there is only ONE possible textual shape
(`-?\d+\.\d{7}`), so divergence risks are minimal and any divergence is
immediately visible in the vector suite rather than latent.

Keeps human-readable payloads; GPS doubles carry ~15-17 significant
digits of which 7 decimals (≈1.1 cm) is far beyond sensor accuracy, so
no meaningful information is lost.

### Option C — sign canonical JSON (RFC 8785 JCS) instead of the KV format

Most far-reaching: replace the `key=value` line format with JCS over
the manifest JSON (ES6 number serialization = shortest round-trip,
defined language-independently). Resolves float text once and for all,
but invalidates all existing signatures and both production builders;
only appropriate for a major protocol revision.

### Recommendation

Adopt **Option B** in a proposed v1.2 (new domain separator
`WorkCam-Sig-v1.2`, `version` allowlist extended, old versions keep
verifying). Additionally:

- Keep F4's cross-language rule (version allowlist before serialization)
  as a MUST in the protocol doc.
- Optionally tighten F5 (calendar check) and F6 (DER minimality) in the
  same v1.2 review, since both ends must change together anyway.

No production code was modified in this task; these options exist only
as this document plus the frozen vectors that make each candidate rule
testable.

---

## 4. Swift production alignment checklist (follow-up, not done here)

To make the Swift capture path able to sign non-null locations
consistently with the backend (F1), the M003-era
`EvidenceManifest.payload()` would need — pending its own review:

1. `location` typed as the same 4-field structure (or the v1.2 wire
   string), not `String?` with a `location == nil` guard.
2. The full v1.1 float rule port (or the v1.2 `%.7f` rule if adopted),
   including `-0.0` and the integer branch.
3. The version allowlist check before payload construction.
4. No timestamp reformatting (`ISO8601DateFormatter().string(from:)` on
   the capture side is fine for *producing* strings; the verify side must
   not parse-and-reformat).
5. Run `W023CrosslangVectorTests` (17 methods) on a Mac and record the
   results in the progress doc; only then update the Swift status from
   `pending Mac cross-validation` to verified.
