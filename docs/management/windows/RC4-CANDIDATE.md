# RC4 Development Candidate — Line Definition (TM-04 J01 / was TM-03 H07)

Owner: Windows session · First declared: 2026-11-28
Status: RC4 line ACTIVE (development candidate; NOT a release tag)

## The three lines, frozen facts

| Line | Ref | Meaning | May it change? |
|---|---|---|---|
| **RC3 freeze** | `1f645b6` (`dev/windows-tm-geometry`) | the frozen stable candidate: 1602 passed / 3 skipped / 0 failed, gate 12/12, final TM-03 report | **NO** — read-only forever; nothing may be merged INTO it; later work develops on descendants |
| **RC3 final doc point** | `a8b59ed` (same branch) | manifest frozen to the RC3 SHA (docs only) | NO |
| **RC4 development candidate** | `dev/windows-tm04` (created from `a8b59ed`) | this line: stabilized core + new verified components, continuously gated | YES — this is where all TM-04 work lands |
| RC5 | not yet created | future EXPERIMENTAL candidate for advanced capabilities that pass review | n/a |

Verification performed at line creation (2026-11-28, this session):
- `1f645b6` is an ancestor of `dev/windows-tm04` (baseline intact).
- Full gate re-run on the new line BEFORE any TM-04 change:
  **1602 passed, 12/12 gates PASSED** (same numbers as the frozen
  RC3 report — the line started clean).

## What RC4 may contain (acceptance rule)

A component enters RC4 only when it passes, on this machine, with
real evidence:

1. Python-side: pytest suite green (dedicated test file).
2. Swift-side: `swift build` + `swift test` green on the Windows
   Swift 6.4.3 toolchain (or, for Apple-only UI references, the
   `#if canImport(SwiftUI)` platform gate + Mac compile note — those
   remain XCODE_PENDING and are never claimed as executed here).
3. The quality gate's prod/experimental isolation check stays green
   (nothing from the techlab zone leaks into `backend/app` routes).

## What RC4 must NOT contain

- No experimental module promotion: `backend/techlab/registration/`
  (E07 PoC), tenant PoC, C2PA PoC, ten-dimension trust routing stay
  in the isolated zone or behind DECISION_REQUIRED.
- No multi-tenant swap of the single-user stable system.
- No XCODE_PENDING claim of "compiled on Windows" — Apple-only files
  carry the platform gate and the honest label.
- Nothing merges into `main` without explicit human review (PR),
  same as RC3.

## RC4 freeze point (to be pinned at J08)

When the B/C queues close and the full suite + gate pass, the
freeze SHA will be recorded here, mirrored into
`TECH-MARATHON-04-STATUS.json` (J08) and the final report (J10).
Until then this line's tip moves forward commit by commit with
per-module test evidence.

## Experimental modules currently quarantined from RC4 promotion

| Module | Where | Gate that holds it out |
|---|---|---|
| Registration PoC (E07) | `backend/techlab/registration/` | isolation gate (techlab) + selection review "phase-2 decision" |
| Tenant isolation PoC | `backend/tests/test_techlab/test_b02_w028_tenant_poc.py` + W028 branch (NOT merged) | directive: enterprise reserve, P2 |
| C2PA PoC | `backend/tools/c2pa-poc/` | "not certification" (D01 note) |
| Ten-dimension trust routing | `backend/tools/trust_engine` | DECISION_REQUIRED (product review) |
| RC5 advanced capabilities | none yet | J09 will define them from validated work |
