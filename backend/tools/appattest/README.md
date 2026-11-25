# TM04 · Apple App Attest Server-Side Verification (Windows)

## What this is

Server-side verification of App Attest attestation/assertion objects:
CBOR parsing (dependency-free), certificate-chain validation to a
configurable trust anchor, nonce binding (SHA256(challenge) in the
credCert extension OID 1.2.840.113635.100.8.9), app-identity binding
(OID 1.2.840.113635.100.10), assertion signature checks
(authData || clientDataHash, ECDSA P-256) and strictly-increasing
counter replay protection.

## Honesty boundary (do not cross in any report)

1. **Synthetic vectors only.** `synth_vectors.py` builds a CA tree
   that MIMICS Apple's structure (root -> intermediate -> credCert).
   It is NOT Apple material. Real-Apple cross-verification is
   **DEVICE_PENDING** until Mac supplies genuine attestations from a
   real device.
2. **App Attest != photo-key provenance.** App Attest proves an
   app-scoped hardware-backed key used for the app's own
   attestation/authentication traffic. It does NOT by itself prove
   that the key signing WorkCam PHOTOS is the same Secure Enclave key.
   That binding requires the app to sign a WorkCam-issued challenge
   with the SAME credential - a separate verification step this module
   reports as an explicit, never-auto-assumed field
   (`key_binding_note` on every result).
3. **Revocation checking is not implemented** - Apple's revocation
   approach for App Attest relies on assertion counters + trust-list
   updates; recorded as a known gap.

## Mac integration protocol

1. Mac requests a one-time challenge from the backend
   (`nonce` semantics identical to the device-identity challenge
   flow; single-use, TTL-bound).
2. iOS calls `DCAppAttestService.generateKey` -> keyId;
   `attestKey(keyId, challenge)` -> attestation object.
3. Mac posts the CBOR attestation object to the backend; backend runs
   `verify_attestation(obj, challenge, app_id, root_der)` with the
   real **Apple App Attest Root CA** as `trust_anchor_der`
   (fetched from Apple's published trust material by Mac or pinned in
   deployment config - never from the client payload).
4. Later authentications: `generateAssertion(keyId, clientData)`
   where `clientData` embeds a fresh server challenge; backend runs
   `verify_assertion(obj, sha256(clientData), credential_point,
   rp_id_hash, last_counter)` with the counter stored server-side
   per credential.
5. Development environment: Apple issues dev certificates from a
   dev-specific CA chain - the backend must pin the correct anchor per
   environment and never accept dev attestations in production.

## Device acceptance criteria (Mac / real iPhone)

- [ ] Real `attestKey` attestation verifies against the Apple root
- [ ] Real assertion verifies; counter increments across two runs
- [ ] Dev-environment attestation REJECTED when production anchor is
      configured
- [ ] Replay of a captured assertion REJECTED (counter)
- [ ] Wrong-challenge attestation REJECTED (nonce)
