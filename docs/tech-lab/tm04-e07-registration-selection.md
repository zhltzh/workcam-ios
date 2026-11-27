# TM-04 E07 (was TM-03 C06): Image Auto-Registration — Selection Review

Status: REVIEW COMPLETE → isolated PoC delivered (phase 1)
Owner: Windows session, 2026-11-28
PoC code: `backend/techlab/registration/` (ISOLATED — not imported by
app routes; the gate's prod/experimental isolation check enforces it)
PoC tests: `backend/tests/test_techlab/test_tm04_e07_registration_poc.py`

## The question

Before/After engineering photos are taken handheld minutes or days
apart. To overlay/slide-compare them (E08) the app needs a
translation estimate between the two frames. What algorithm do we
adopt, under which licenses, and with what honest failure model?

## Candidates reviewed

| Method | Handles | License/deps | Effort | Verdict |
|---|---|---|---|---|
| OpenCV ECC (`findTransformECC`) | translation, rotation, scale, illumination changes | OpenCV = Apache-2.0 (commercial-OK), but a +40MB binary dependency, an Obj-C++ bridge, and an App-Store build-chain risk on a first release | medium | **rejected for phase 1** — dependency cost dominates; revisit only if phase-1 accuracy fails |
| SIFT feature matching + RANSAC | rotation, scale, large displacements | SIFT patent expired (March 2020); practical implementations live in OpenCV/VLFeat — same dependency problem; a from-scratch SIFT in Swift is a multi-month project with its own correctness burden | very high | rejected (same dep problem; phase-1 scope does not need rotation invariance) |
| ORB/BRIEF + RANSAC | moderate rotation, translation | patent-free, small, exists in OpenCV only for our purposes (same dep problem); pure-Swift ports are unvetted research code | high | rejected |
| FFT phase correlation | translation only (integer + subpixel), robust to uniform brightness shifts | pure math — NumPy (PoC) / Accelerate vDSP (iOS, Mac side); ZERO third-party image deps | low | **adopted (coarse stage)** |
| Pyramid NCC refinement | subpixel translation refinement, small rotations tolerated as noise | pure math, no deps | low | **adopted (fine stage)** |
| Apple frameworks | — | no public image-registration API exists outside ARKit's AR pipeline (wrong domain: world tracking, not 2D stills) | — | n/a |

## Constraints that decided it

1. **OpenCV-free constraint (task sheet)**: the first release must
   stay free of the OpenCV binary dependency (bundle size, Obj-C++
   bridge, App Store review surface). Apache-2.0 would make it
   *legal* — the constraint is engineering, not licensing.
2. **Scenario**: Before/After pairs are handheld photos of the same
   defect/component, typically <8° apart, sub-30px displacements,
   mostly near-parallel viewing. Translation + small noise covers
   the product need; the UI already promises manual alignment (E08)
   as the fallback — auto-registration is a convenience, never a
   claim.
3. **The red line** (from TM-03 C06 wording): the engine must REFUSE
   (named error) when the estimate is not confident; it never
   silently mis-registers and never auto-declares "repair verified".

## Adopted design (phase 1)

Two-stage, translation-only, confidence-gated:

```
coarse: FFT phase correlation on a grayscale, down-scaled pair
        -> integer-ish (dx, dy) + normalized peak sharpness
gate:   peak sharpness < threshold -> RegistrationFailed(reason)
fine:   3-level image pyramid NCC around the coarse estimate
        -> subpixel (dx, dy) refinement
gate:   NCC score at the optimum < threshold -> RegistrationFailed
output: RegistrationResult(dx, dy, confidence, method: "pc+ncc/1")
```

- Brightness shifts: both stages operate on mean-subtracted,
  variance-normalized patches (uniform illumination drift is a
  non-event).
- Rotation/scale beyond ±8° / ±5% are NOT corrected; the engine's
  confidence gates are expected to fail them (validated in tests)
  and the product routes the user to manual align.
- No wall-clock assertions: tests use fixed synthetic inputs; timing
  is reported, never gated (the two-flakes rule).

## Error budget (synthetic, this PoC)

Target on synthetic textured pairs with KNOWN ground-truth shifts:
| displacement | P50 error | P95 error | requirement |
|---|---|---|---|
| ≤ 4 px (subpixel incl.) | see tests | see tests | ≤ 0.5 px P95 after refinement |
| 4–24 px | see tests | see tests | ≤ 1.0 px P95 |
| rotated 10° | REFUSED | REFUSED | named failure, never a wrong answer |

Actual measured numbers live in the test output / report — this
document does NOT pre-claim them (they are properties of the run).

## Phase 2 (only if phase 1 fails on real photos)

Evaluate OpenCV ECC (Apache-2.0) as an ISOLATED experimental module
behind the same `RegistrationResult` protocol, with a Mac-side
device-acceptance pass. That decision is DECISION_REQUIRED; nothing
in phase 1 blocks the first release.

## What this is NOT

- Not "image repair verification" — registration success never
  upgrades an issue to resolved (that flow needs human + evidence).
- Not a measurement feature — dx/dy are display-space pixels, not
  engineering units; no measurement claim may derive from them.
- Not stable-production code — the PoC lives in the techlab
  isolation zone until separately promoted.
