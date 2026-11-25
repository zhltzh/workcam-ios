# TM08 · Photo Provenance & Anti-Substitution Threat Model

## The boundary this document refuses to cross

**No byte-level analysis can prove a photograph shows a real scene.**
A screen re-shoot produces a genuinely NEW image: new pixels, new
EXIF, new hash. Composites and AI generation are likewise invisible in
the general case. Every claim below is scoped to what is actually
checkable, and every "detect" that is not cryptographic is labeled a
HEURISTIC for human review.

## Attack surface (who can do what)

| Attack | Attacker capability | Detectability | Mechanism |
|---|---|---|---|
| Resubmit someone else's package photo | has captured package | **DETECTABLE** | SHA-256 identity vs registry |
| Reuse own photo on a new record | normal user | **DETECTABLE** | same SHA-256 (W017 duplicates) |
| Swap the photo under a signed record | write access to storage | **DETECTABLE** | photo_hash binding (L1) breaks |
| Swap manifest public key without re-signing | has package | **DETECTABLE** | L2 signature fails (TM06 test) |
| Edit EXIF after signing | file write | **DETECTABLE** | any byte change breaks photo_hash; edited copy is a DERIVATIVE at best |
| Import library photo instead of shooting | normal user | HEURISTIC | intake channel (client-declared), EXIF software/make signals |
| Re-photograph a screen | phone + screen | HEURISTIC at best | moire/skew/no-EXIF signals; defeats are trivial |
| Photograph a printed photo | phone + print | UNDETECTABLE | genuinely new capture |
| Composite in an editor before import | editor | UNDETECTABLE | import path accepts any JPEG |
| AI-generated scene | generator | UNDETECTABLE | novel plausible image |

## Source classes

`camera_capture` / `app_import` / `external_file` /
`duplicate_submission` / `reused_on_different_photo` /
`recapture_suspect` / `synthetic_input` — classification is driven by
cryptographic identity where possible (DETECTABLE) and by declared
channel otherwise (HEURISTIC, data-not-proof).

## What the server enforces (product stance)

1. The INTAKE CHANNEL is recorded with every photo (client-declared,
   treated as untrusted data exactly like device_id).
2. EXACT duplicates are detected and reported (W017 duplicates:
   candidates, never auto-deleted).
3. Signed photos are immutable after upload; any byte change breaks
   the hash chain at L1 and cannot be re-signed by the client (the
   private key never re-signs store-side content).
4. Perceptual near-duplicates are surfaced as review candidates with
   a stated hamming distance - never as proof.
5. Screen-recapture and synthetic signals, when present, only ever
   label a photo "suspect" - the human reviewer decides.

## Relationship to trust dimensions (TM07)

Provenance signals feed `signature_validity` (crypto identity) and
`file_integrity`; the CONTENT question is deliberately absent from
the ten dimensions - the honest answer to "is the scene real?" is
outside what this system can verify, and the engine refuses to invent
a dimension for it.

## Test samples & scenarios (tests/test_techlab/test_tm08_provenance.py)

- exact duplicate -> DETECTABLE duplicate_submission
- screenshot software tag -> DETECTABLE synthetic_input
- editor tag -> APP_IMPORT heuristic
- clean EXIF -> "consistent" signal whose reason text itself says
  PROVES NOTHING
- perceptual candidate -> RECAPTURE_SUSPECT, human-review wording
- recapture flags -> HEURISTIC; the UNDETECTABLE boundary is stated
  even when flags fire
- intake channel -> classified but explicitly data-not-proof
