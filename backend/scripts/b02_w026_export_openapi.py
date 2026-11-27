"""B02-W026: export the live OpenAPI spec and enrich it for handoff.

The PATH/METHOD inventory and every JSON schema come from the running
FastAPI app (app.main:app) AS-IS - this script never invents endpoints.
Human-written summaries/description text, security annotations, response
descriptions and a few examples are layered on top; every layer is
listed under "x-b02-w026-enrichment" so the Mac side can tell machine
truth from manual prose.

Usage (from backend/):
    python scripts/b02_w026_export_openapi.py ../docs/api/openapi.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.main import app

BASELINE = "31917e3 → 2b535a0 + WIN-HOTFIX"

# ---------------------------------------------------------------- enrichment

TAG_DOCS = {
    "mvp-v1": ("Business core (W006): projects, photos, evidence links. "
               "All routes require X-Device-Token (enrolled device); "
               "cross-owner access is 404-identical to not-found."),
    "mvp-originals": ("Original-photo storage (W007): byte upload/download "
                      "with hash binding; integrity + evidence-chain view."),
    "mvp-sync": ("Offline sync (W008/W013-R1): versioned batch endpoint with "
                 "an idempotency ledger (operation_id + request hash) and a "
                 "restart-stable incremental event cursor."),
    "device-admin": ("Device administration (W009): out-of-band-code "
                     "enrollment, admin-code-gated revoke/status, "
                     "challenge-response proof of possession."),
    "mvp-derivatives": ("Watermark derivatives (W011): rendered copies with "
                         "server-resolved evidence status; the original is "
                         "never modified."),
    "mvp-reports": ("Reports (W012): immutable snapshot PDF generation, "
                    "download and ZIP delivery packages."),
    "mvp-inspection": ("Annotations & inspection issues (W013): human-added "
                       "overlay annotations, issue tracking with optimistic "
                       "revisions, before/after comparison derivatives."),
    "verify": ("Trusted-evidence verification core (W002/W003 era): manifest "
               "verify, test key registration, replay-detect record store. "
               "NOT production-grade authentication."),
    "time-anchor-experimental": ("EXPERIMENTAL time anchoring (W003): signed "
                                 "server-time receipts for offline batches. "
                                 "Research only, not production."),
    "upload-experimental": ("EXPERIMENTAL chunked upload (W003/W005): "
                            "resumable batches, device auth wrap, opt-in "
                            "gates; hard-disabled in production."),
}

DEVICE_SECURITY = [{"deviceToken": []}]

AUTH_401 = ("401 unauthorized: missing/unknown/revoked X-Device-Token, or a "
            "legacy token derived from the public key "
            "(code legacy_token_must_re_enroll).")
AUTH_404 = ("404 not_found: resource does not exist OR belongs to another "
            "owner - identical responses, no existence oracle.")
ERR_ENVELOPE = ("Business errors use the envelope "
                "{\"detail\": {\"code\", \"message\", \"details\"}} "
                "(see docs/api/b02-w026-api-reference.md).")

# method/path -> (summary, description)
OPS: dict[tuple[str, str], tuple[str, str]] = {
    ("get", "/health"): ("Service health", "Liveness probe for the verify "
                         "core. No auth, no side effects."),
    ("post", "/verify"): ("Verify a signature manifest",
                          "Runs structure, version, photo-hash, ECDSA and "
                          "key-registry checks and returns the aggregated "
                          "evidence status. photo_base64 is optional; "
                          "without it photo_hash_valid is null."),
    ("post", "/register-key"): ("Register public key (TEST only)",
                                "Development/test endpoint. Takes parameters "
                                "as QUERY arguments (public_key_b64, "
                                "device_id, allow_test_registration=true). "
                                "Refuses overwrite; disabled in production "
                                "WORKCAM_ENV=production."),
    ("post", "/store-record"): ("Store a record for replay detection",
                                "Strict validation (mandatory photo bytes, "
                                "valid signature, registered key, matching "
                                "hash) before occupying a record_id."),
    ("get", "/check-record/{record_id}"): ("Check stored record",
                                           "Returns whether a record_id is "
                                           "present in the replay store."),
    ("post", "/time-anchor/anchor"): ("Create a time anchor",
                                      "Signs the submitted batch digest with "
                                      "the server key. Proves existence "
                                      "BEFORE server time only."),
    ("post", "/time-anchor/verify"): ("Verify an anchor receipt",
                                       "Signature + (optionally) storage "
                                       "verification with trust analysis."),
    ("get", "/time-anchor/public-key"): ("Server anchor public key",
                                          "PEM public key + key_id. Test "
                                          "key - not for production."),
    ("get", "/time-anchor/receipt/{anchor_id}"): ("Fetch a stored receipt",
                                                  "Returns the stored signed "
                                                  "receipt by anchor_id."),
    ("get", "/time-anchor/health"): ("Anchor service health",
                                     "Liveness probe; states EXPERIMENTAL."),
    ("post", "/upload/experimental/auth/challenge"): ("Upload auth: get nonce",
        "Single-use server nonce (60 s) for a device to sign. Requires "
        "WORKCAM_UPLOAD_EXPERIMENTAL=1 and WORKCAM_UPLOAD_AUTH=1."),
    ("post", "/upload/experimental/auth/verify"): ("Upload auth: prove key",
        "Verifies {device_token, nonce, signature_b64} against the enrolled "
        "public key. Private keys never leave the device."),
    ("post", "/upload/experimental/batches"): ("Register an upload batch",
        "Idempotent-by-content resumable upload. Opt-in only; 503 unless "
        "WORKCAM_UPLOAD_EXPERIMENTAL=1; hard 503 in production."),
    ("put", "/upload/experimental/batches/{batch_id}/chunks/{index}"):
        ("Upload one chunk",
         "Raw bytes body; content-length must match the plan; duplicate "
         "identical delivery is an idempotent ack, different bytes 409."),
    ("get", "/upload/experimental/batches/{batch_id}"): ("Batch status",
        "Lists missing/received chunk indexes and completeness."),
    ("post", "/upload/experimental/batches/{batch_id}/complete"):
        ("Complete a batch",
         "Recomposes in chunk order, re-hashes everything; whole-batch SHA "
         "mismatch is a 409 that keeps chunks for re-upload."),
    ("get", "/upload/experimental/batches/{batch_id}/content"):
        ("Download assembled file",
         "Streams assembled.bin after COMPLETE; byte-level verification."),
    ("post", "/api/v1/projects"): ("Create a project",
        "Owner-scoped project record. 201 on success."),
    ("get", "/api/v1/projects"): ("List my projects",
        "Offset pagination: limit (default 20, clamped to 100), offset, "
        "status filter (active|archived|all). Stable order "
        "created_at DESC, project_id."),
    ("get", "/api/v1/projects/{project_id}"): ("Get one project", ""),
    ("patch", "/api/v1/projects/{project_id}"): ("Patch a project",
        "Partial update; archived projects are immutable (409); empty "
        "patch 400."),
    ("post", "/api/v1/projects/{project_id}/archive"): ("Archive a project",
        "Idempotent: re-archiving returns the same state. Archived projects "
        "accept no writes; photos/evidence are preserved (evidence "
        "retention)."),
    ("post", "/api/v1/projects/{project_id}/photos"): ("Register photo metadata",
        "Metadata-only registration (bytes go to the W007 original "
        "endpoint). Idempotent on (project, idempotency_key): same content "
        "replays the same photo with _idempotent_replay=true (201); "
        "different content 409 idempotency_conflict. captured_at/device_id "
        "are client-claimed DATA (trust_notes in response)."),
    ("get", "/api/v1/projects/{project_id}/photos"): ("List project photos",
        "Offset pagination, stable order created_at DESC, photo_id."),
    ("get", "/api/v1/photos/{photo_id}"): ("Get photo metadata",
        "Ownership chains through the photo's project."),
    ("post", "/api/v1/photos/{photo_id}/evidence-links"):
        ("Link evidence to a photo",
         "Creates a link starting at unverified. The request has no status "
         "field by design - clients can never submit a trusted status. "
         "original_sha256 is bound at link time (anti cross-photo "
         "misbinding)."),
    ("get", "/api/v1/photos/{photo_id}/evidence-links"):
        ("List evidence links", "Order created_at DESC."),
    ("post", "/api/v1/photos/{photo_id}/evidence-links/{evidence_link_id}/verify"):
        ("Server-side verification write-back",
         "Client submits MATERIAL ({manifest[, photo_base64]} or {receipt}); "
         "the server runs the existing trusted paths and writes the "
         "computed status. Binding guards: reference_mismatch / "
         "hash_binding_mismatch (400, no state change). Re-runnable."),
    ("put", "/api/v1/photos/{photo_id}/original"): ("Upload original bytes",
        "Raw photo bytes as body. Hash-while-streaming; the body must hash "
        "to the photo's registered original_sha256 (400 hash_mismatch). "
        "Idempotent for identical bytes; a bound photo rejects different "
        "content (409 binding_conflict)."),
    ("get", "/api/v1/photos/{photo_id}/original"): ("Download original bytes",
        "Content-addressed store; X-Original-Sha256 header for local "
        "re-verification."),
    ("get", "/api/v1/photos/{photo_id}/original/integrity"):
        ("Integrity + evidence chain view",
         "Separates storage integrity (bytes exist and match) from "
         "evidence state (server-verified claims). photo_authenticity_"
         "proven is always false - storage never claims authenticity."),
    ("post", "/api/v1/photos/{photo_id}/derivatives/watermark"):
        ("Render a watermark derivative",
         "Reads the REAL original bytes (must be uploaded first). "
         "evidence_status is RESOLVED SERVER-SIDE from verified evidence "
         "links and overrides any client claim. Idempotent on (photo, "
         "template, params-hash, original-sha)."),
    ("get", "/api/v1/photos/{photo_id}/derivatives"): ("List derivatives", ""),
    ("get", "/api/v1/derivatives/{derivative_id}/download"):
        ("Download derivative bytes",
         "X-Derivative-Sha256 / X-Original-Sha256 headers. A derivative is "
         "never the trusted evidence."),
    ("get", "/api/v1/projects/{project_id}/reports"): ("List reports", ""),
    ("post", "/api/v1/projects/{project_id}/reports"):
        ("Generate a report (snapshot)",
         "Render PDF from a point-in-time snapshot; existing reports are "
         "never mutated (regeneration creates a new row). Photo bytes are "
         "embedded with downscaling bound (P5); originals stay untouched."),
    ("get", "/api/v1/reports/{report_id}"): ("Report detail",
        "Includes the immutable snapshot view (per-photo entries and "
        "issue snapshot)."),
    ("get", "/api/v1/reports/{report_id}/download"): ("Download report PDF",
        "X-Report-Sha256 header; sha asserted before serving."),
    ("post", "/api/v1/reports/{report_id}/delivery-package"):
        ("Build ZIP delivery package",
         "report.pdf + originals/ + derivatives/ + evidence/ + manifest.json "
         "+ verification-guide.txt with per-file SHA-256. Never exports "
         "tokens/enrollment codes/key material."),
    ("post", "/api/v1/photos/{photo_id}/annotations"): ("Create an annotation",
        "Normalized coords (0..1) on the EXIF-transposed display frame. "
        "Per-photo cap 50 active (413). Types: arrow|rectangle|circle|"
        "freehand|text|number_marker."),
    ("get", "/api/v1/photos/{photo_id}/annotations"): ("List annotations",
        "Active layer by default; include_inactive=true returns soft-"
        "deleted history. Human-added content."),
    ("patch", "/api/v1/annotations/{annotation_id}"): ("Patch an annotation",
        "Optimistic revision (expected_revision); soft-deleted annotations "
        "are immutable (409 annotation_deleted)."),
    ("delete", "/api/v1/annotations/{annotation_id}"): ("Soft-delete an annotation",
        "is_active=0, history preserved; still queryable with "
        "include_inactive=true."),
    ("post", "/api/v1/projects/{project_id}/issues"): ("Create an issue",
        "severity in low|medium|high|critical; status starts open. "
        "Human records - no AI assessment implied."),
    ("get", "/api/v1/projects/{project_id}/issues"): ("List issues",
        "Optional status/severity filters."),
    ("get", "/api/v1/issues/{issue_id}"): ("Issue detail",
        "Full history + linked photos by role."),
    ("patch", "/api/v1/issues/{issue_id}"): ("Patch an issue",
        "Optimistic revision; severity/status changes append history; "
        "closed issues are immutable (409 issue_closed)."),
    ("post", "/api/v1/issues/{issue_id}/photos"): ("Link a photo by role",
        "role in before|progress|after; one photo per role (replace). "
        "Manual association - proves neither same-location nor acceptance."),
    ("delete", "/api/v1/issues/{issue_id}/photos/{photo_id}/{role}"):
        ("Unlink a photo", "Removes the role link; appends history."),
    ("get", "/api/v1/issues/{issue_id}/photos"): ("Comparison view", ""),
    ("post", "/api/v1/issues/{issue_id}/comparison-image"):
        ("Render before/after comparison",
         "Side-by-side JPEG derivative from two originals (read-only). "
         "X-Comparison-Sha256 header. Same-location/acceptance NOT proven."),
    ("post", "/api/v1/sync/batch"): ("Submit an offline operation batch",
        "Max 50 operations, executed IN ORDER with per-op receipts "
        "(applied|duplicate|rejected). operation_id + request-hash ledger "
        "in the business DB: same replay -> stored receipt (duplicate), "
        "different content -> rejected op_id_conflict; deterministic "
        "rejections are ledgered too. Rejected replays retain error details and "
        "result.status=rejected. project_archive uses expected_revision; role "
        "replacement requires expected_previous_photo_id. Each payload must carry "
        "its own operation_id (executors validate payload.operation_id)."),
    ("get", "/api/v1/sync/events"): ("Pull incremental events",
        "Owner-scoped, monotonic cursor. Limit counts matching rows. "
        "Store a separate cursor per filter. include_state=true returns current "
        "project/photo/issue/annotation snapshots and annotation tombstones; "
        "other kinds remain event pointers. Persist changes and cursor atomically."),
    ("post", "/api/v1/devices/enroll"): ("Enroll a device",
        "Requires the out-of-band enrollment code. Returns a RANDOM "
        "server-issued token (W012 security change - tokens are never "
        "derived from public keys). Idempotent per public key."),
    ("post", "/api/v1/devices/{device_token}/revoke"): ("Revoke a device",
        "Admin-code (X-Admin-Code) gated. Instant effect; idempotent."),
    ("get", "/api/v1/devices/{device_token}"): ("Device status",
        "Admin-code gated; returns fingerprint, never the stored public "
        "key."),
    ("post", "/api/v1/devices/challenge"): ("Issue a proof-of-possession nonce",
        "Single-use nonce (60 s) for the enrolled device to sign."),
    ("post", "/api/v1/devices/challenge/verify"): ("Verify nonce signature",
        "Checks the P-256 signature with the enrolled public key; private "
        "keys never travel."),
}

API_V1_PREFIXES = ("/api/v1",)
UPLOAD_PREFIX = "/upload/experimental"


def _is_business(path: str) -> bool:
    return any(path.startswith(p) for p in API_V1_PREFIXES)


def enrich(spec: dict) -> dict:
    spec["info"]["description"] = (
        "WorkCam trusted-photography prototype backend, baseline "
        f"{BASELINE}. Business surface /api/v1 requires an "
        "enrolled X-Device-Token; errors use the unified envelope "
        "{\"detail\": {\"code\", \"message\", \"details\"}}. "
        "This is a PROTOTYPE, not a production authentication service. "
        "Paths/methods/schemas are machine-generated from the live app; "
        "summaries, descriptions, security annotations and examples are "
        "manually enriched (see x-b02-w026-enrichment).")

    spec["x-b02-w026-enrichment"] = {
        "baseline": BASELINE,
        "generator": "backend/scripts/b02_w026_export_openapi.py",
        "note": ("The path/method inventory and all JSON schemas are the "
                 "live /openapi.json export of app.main:app, unmodified. "
                 "Fields listed below were added manually for handoff "
                 "readability; they annotate, never contradict, the "
                 "machine truth."),
        "manually_enriched_fields": [
            "info.description",
            "tags[].description",
            "paths.<p>.<m>.summary",
            "paths.<p>.<m>.description",
            "paths.<p>.<m>.security",
            "paths.<p>.<m>.responses.<status>.description",
            "components.securitySchemes",
            "components.schemas.<name>.description",
        ],
    }

    spec["tags"] = [
        {"name": name, "description": doc}
        for name, doc in sorted(TAG_DOCS.items())
        if name in {t for p in spec["paths"].values()
                    for t in (p.get("tags", []) if isinstance(p, dict) else [])
                    } or True
    ]
    # keep only tags actually referenced in the spec, in spec order
    used: list[str] = []
    for p in spec["paths"].values():
        for m, op in p.items():
            if isinstance(op, dict) and "tags" in op:
                for t in op["tags"]:
                    if t not in used:
                        used.append(t)
    spec["tags"] = [{"name": t, "description": TAG_DOCS[t]}
                    for t in used if t in TAG_DOCS]

    spec["components"]["securitySchemes"] = {
        "deviceToken": {
            "type": "apiKey",
            "in": "header",
            "name": "X-Device-Token",
            "description": ("Random opaque token issued by "
                            "/api/v1/devices/enroll (enrollment code "
                            "required). The caller's ownership key."),
        },
        "adminCode": {
            "type": "apiKey",
            "in": "header",
            "name": "X-Admin-Code",
            "description": ("The out-of-band enrollment code doubles as "
                            "the MVP admin secret (known RBAC gap)."),
        },
    }

    for path, item in spec["paths"].items():
        for method, op in item.items():
            if method not in ("get", "post", "put", "patch", "delete"):
                continue
            summary, desc = OPS.get((method, path), ("", ""))
            if summary:
                op["summary"] = summary
            if desc:
                op["description"] = desc + ("" if desc.endswith(".")
                                            else ".")
            if _is_business(path):
                op["security"] = DEVICE_SECURITY
                op["description"] = (op.get("description", "") + " "
                                     + AUTH_401 + " " + AUTH_404 + " "
                                     + ERR_ENVELOPE).strip()
            if path.startswith("/api/v1/devices") and method in (
                    "post", "get") and "{device_token}" in path:
                op["security"] = [{"adminCode": []}]
            if path.startswith("/api/v1/devices") and (
                    path.endswith("/challenge")
                    or path.endswith("/verify") or path == "/api/v1/devices"):
                op["security"] = DEVICE_SECURITY

    # a couple of illustrative examples (marked as manually enriched)
    schemas = spec.get("components", {}).get("schemas", {})
    if "VerifyResponse" in schemas:
        schemas["VerifyResponse"]["description"] = (
            "Aggregated verification verdict; see EvidenceStatus/"
            "TimeTrustLevel enum docs. Manually enriched example values.")
        schemas["VerifyResponse"]["example"] = {
            "evidence_status": "signature_valid",
            "cryptographic_signature_valid": True,
            "photo_hash_valid": None,
            "key_registration_status": "unregistered",
            "device_identity_consistent": None,
            "device_attestation_status": "unknown",
            "time_trust_level": "device_reported",
            "summary": "Signature mathematically valid but public key not "
                       "registered. Cannot verify device identity.",
            "details": [{"check": "structure", "status": "passed",
                         "message": "Manifest structure valid"}],
            "warnings": ["Public key is not registered..."],
        }
    if "ProjectCreate" in schemas:
        schemas["ProjectCreate"]["example"] = {
            "name": "Bridge N-12", "description": "Main span",
            "site_address": "Nanjing",
        }
    if "PhotoRegister" in schemas:
        schemas["PhotoRegister"]["example"] = {
            "idempotency_key": "client-generated-unique-key",
            "original_sha256": "64-lowercase-hex",
            "original_filename": "IMG_0001.jpg",
            "mime_type": "image/jpeg",
            "byte_size": 1234567,
            "captured_at": "2026-11-23T08:00:00Z",
            "device_id": "iPhone15,3",
        }
    for name in ("EvidenceStatus", "TimeTrustLevel", "KeyRegistrationStatus",
                 "CheckStatus", "DeviceAttestationStatus"):
        if name in schemas and "description" not in schemas[name]:
            schemas[name]["description"] = (
                f"Enum values as in app/models/capture.py ({name}).")
    for name in ("VerificationStatus", "TimeAnchorRequest",
                 "TimeAnchorResponse", "TimeAnchorReceipt",
                 "TimeAnchorVerification"):
        if name in schemas and "description" not in schemas[name]:
            schemas[name]["description"] = (
                f"See app/models/time_anchor.py ({name}). Signed vs "
                "unsigned fields are documented there.")
    return spec


def main() -> None:
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "openapi.json")
    spec = enrich(app.openapi())
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(spec, indent=2, ensure_ascii=False) + "\n",
                   encoding="utf-8")
    ops = sum(1 for p in spec["paths"].values()
              for m in p if m in ("get", "post", "put", "patch", "delete"))
    print(f"wrote {out} ({len(spec['paths'])} paths, {ops} operations)")


if __name__ == "__main__":
    main()
