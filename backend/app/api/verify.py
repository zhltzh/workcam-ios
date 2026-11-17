from __future__ import annotations

import base64

from fastapi import APIRouter

from app.models.capture import (
    CheckStatus,
    DeviceAttestationStatus,
    EvidenceStatus,
    KeyRegistrationStatus,
    RecordStoreEntry,
    TimeTrustLevel,
    VerifyDetail,
    VerifyRequest,
    VerifyResponse,
    SUPPORTED_VERSIONS,
)
from app.services.key_registry import (
    compute_manifest_hash,
    compute_public_key_hash,
    get_registry,
)
from app.services.signature import (
    validate_manifest_structure,
    verify_photo_hash,
    verify_signature_with_details,
)
from datetime import datetime, timezone

router = APIRouter()


@router.get("/health")
async def health():
    return {"status": "ok", "service": "workcam-verify"}


@router.post("/verify", response_model=VerifyResponse)
async def verify(req: VerifyRequest):
    details: list[VerifyDetail] = []
    warnings: list[str] = []
    manifest = req.manifest

    struct_errors = validate_manifest_structure(manifest)
    if struct_errors:
        for err in struct_errors:
            details.append(VerifyDetail(
                check="structure", status=CheckStatus.FAILED, message=err
            ))
        return VerifyResponse(
            evidence_status=EvidenceStatus.INVALID,
            cryptographic_signature_valid=False,
            photo_hash_valid=None,
            key_registration_status=KeyRegistrationStatus.UNKNOWN,
            device_identity_consistent=None,
            device_attestation_status=DeviceAttestationStatus.UNKNOWN,
            time_trust_level=TimeTrustLevel.UNKNOWN,
            summary="Structure validation failed",
            details=details,
        )

    details.append(VerifyDetail(
        check="structure",
        status=CheckStatus.PASSED,
        message="Manifest structure valid",
    ))

    if manifest.version not in SUPPORTED_VERSIONS:
        details.append(VerifyDetail(
            check="version",
            status=CheckStatus.FAILED,
            message=f"Unsupported version {manifest.version}",
        ))
        return VerifyResponse(
            evidence_status=EvidenceStatus.INVALID,
            cryptographic_signature_valid=False,
            photo_hash_valid=None,
            key_registration_status=KeyRegistrationStatus.UNKNOWN,
            device_identity_consistent=None,
            device_attestation_status=DeviceAttestationStatus.UNKNOWN,
            time_trust_level=TimeTrustLevel.UNKNOWN,
            summary="Unsupported protocol version",
            details=details,
        )

    photo_hash_valid: bool | None = None
    photo_hash_status = CheckStatus.NOT_CHECKED
    if req.photo_base64 is not None:
        try:
            photo_bytes = base64.b64decode(req.photo_base64, validate=True)
            hash_ok = verify_photo_hash(manifest, photo_bytes)
            photo_hash_valid = hash_ok
            photo_hash_status = (
                CheckStatus.PASSED if hash_ok else CheckStatus.FAILED
            )
            msg = "SHA-256 match" if hash_ok else "SHA-256 mismatch"
        except Exception as e:
            photo_hash_valid = False
            photo_hash_status = CheckStatus.FAILED
            msg = f"Failed to decode photo: {e}"
    else:
        msg = "Photo not provided, hash not verified"
        warnings.append(
            "Photo bytes not provided. photo_hash check is not_checked. "
            "Cannot verify that the signed hash matches actual photo content."
        )

    details.append(VerifyDetail(
        check="photo_hash", status=photo_hash_status, message=msg
    ))

    sig_ok, sig_msg = verify_signature_with_details(manifest)
    details.append(VerifyDetail(
        check="ecdsa_signature",
        status=CheckStatus.PASSED if sig_ok else CheckStatus.FAILED,
        message=sig_msg,
    ))

    registry = get_registry()
    key_result = registry.lookup(manifest.public_key)
    key_reg_status = key_result.registration_status

    device_identity_consistent: bool | None = None
    if key_reg_status == KeyRegistrationStatus.REGISTERED:
        is_consistent, registered_device_id = registry.check_device_identity(
            manifest.public_key, manifest.device_id
        )
        device_identity_consistent = is_consistent
        if not is_consistent:
            warnings.append(
                f"Device identity mismatch: public key registered to "
                f"device_id='{registered_device_id}', but manifest claims "
                f"device_id='{manifest.device_id}'. "
                f"device_identity_consistent=false."
            )
    else:
        warnings.append(
            "Public key is not registered. Cannot verify device identity. "
            "key_registration_status=unregistered."
        )

    device_attest_status = DeviceAttestationStatus.UNKNOWN
    warnings.append(
        "Device attestation (App Attest) is not implemented. "
        "device_attestation_status=unknown. "
        "Cannot prove device is a genuine Apple device with Secure Enclave."
    )

    time_trust_level = TimeTrustLevel.DEVICE_REPORTED
    warnings.append(
        "Timestamp is device-reported only. "
        "time_trust_level=device_reported. "
        "No server-side time anchor. Device could have falsified time."
    )

    evidence_status = _compute_evidence_status(
        sig_ok=sig_ok,
        photo_hash_valid=photo_hash_valid,
        key_reg_status=key_reg_status,
        device_identity_consistent=device_identity_consistent,
        device_attest_status=device_attest_status,
    )

    summary = _compute_summary(
        evidence_status=evidence_status,
        sig_ok=sig_ok,
        photo_hash_valid=photo_hash_valid,
        key_reg_status=key_reg_status,
        device_identity_consistent=device_identity_consistent,
    )

    return VerifyResponse(
        evidence_status=evidence_status,
        cryptographic_signature_valid=sig_ok,
        photo_hash_valid=photo_hash_valid,
        key_registration_status=key_reg_status,
        device_identity_consistent=device_identity_consistent,
        device_attestation_status=device_attest_status,
        time_trust_level=time_trust_level,
        summary=summary,
        details=details,
        warnings=warnings,
    )


def _compute_evidence_status(
    sig_ok: bool,
    photo_hash_valid: bool | None,
    key_reg_status: KeyRegistrationStatus,
    device_identity_consistent: bool | None,
    device_attest_status: DeviceAttestationStatus,
) -> EvidenceStatus:
    if not sig_ok:
        return EvidenceStatus.INVALID

    if photo_hash_valid is False:
        return EvidenceStatus.INVALID

    if key_reg_status == KeyRegistrationStatus.UNREGISTERED:
        return EvidenceStatus.SIGNATURE_VALID

    if device_identity_consistent is False:
        return EvidenceStatus.INVALID

    if photo_hash_valid is None:
        return EvidenceStatus.PARTIAL

    if device_attest_status != DeviceAttestationStatus.ATTESTED:
        return EvidenceStatus.CRYPTOGRAPHICALLY_VALID

    return EvidenceStatus.FULLY_ATTESTED


def _compute_summary(
    evidence_status: EvidenceStatus,
    sig_ok: bool,
    photo_hash_valid: bool | None,
    key_reg_status: KeyRegistrationStatus,
    device_identity_consistent: bool | None,
) -> str:
    if evidence_status == EvidenceStatus.INVALID:
        if not sig_ok:
            return "Cryptographic signature invalid"
        if photo_hash_valid is False:
            return "Photo hash mismatch"
        if device_identity_consistent is False:
            return "Device identity mismatch: public key not registered to claimed device"
        return "Validation failed"

    if evidence_status == EvidenceStatus.FULLY_ATTESTED:
        return (
            "Fully attested: signature valid, photo verified, key registered, "
            "device attested. Highest trust level."
        )

    if evidence_status == EvidenceStatus.CRYPTOGRAPHICALLY_VALID:
        return (
            "Cryptographically valid: signature and photo hash verified, "
            "key registered. BUT device attestation is unknown. "
            "Cannot prove device is genuine Apple device with Secure Enclave."
        )

    if evidence_status == EvidenceStatus.SIGNATURE_VALID:
        return (
            "Signature mathematically valid but public key not registered. "
            "Cannot verify device identity."
        )

    if evidence_status == EvidenceStatus.PARTIAL:
        return (
            "Partial evidence: some checks passed, some incomplete. "
            "See warnings for details."
        )

    return "Unverified"


@router.post("/register-key")
async def register_key(
    public_key_b64: str,
    device_id: str,
    allow_test_registration: bool = False,
):
    """
    Register a public key with a device_id.

    SECURITY: This endpoint is for DEVELOPMENT/TESTING ONLY.
    - Requires explicit allow_test_registration=true parameter
    - In production, this endpoint must be DISABLED
    - Production will use App Attest formal registration flow
    - Does NOT allow overwriting existing registrations
    - Returns error if device_id conflicts with existing registration
    """
    import os
    env = os.environ.get("WORKCAM_ENV", "development")

    if env == "production":
        return {
            "error": "REGISTER_DISABLED",
            "message": "Test registration is disabled in production environment. Use App Attest formal registration flow.",
            "environment": env,
        }

    if not allow_test_registration:
        return {
            "error": "TEST_REGISTRATION_NOT_ALLOWED",
            "message": "Test registration requires explicit allow_test_registration=true parameter. This is a safety measure to prevent accidental key registration.",
            "hint": "Set allow_test_registration=true to proceed in development/test environments.",
        }

    registry = get_registry()

    existing = registry.lookup(public_key_b64)
    if existing.registration_status == KeyRegistrationStatus.REGISTERED:
        if existing.device_id != device_id:
            return {
                "error": "DEVICE_ID_CONFLICT",
                "message": f"Public key already registered to different device_id. Existing: '{existing.device_id}', Requested: '{device_id}'. Cannot overwrite existing registration.",
                "existing_device_id": existing.device_id,
                "requested_device_id": device_id,
                "registered_at": existing.registered_at,
            }
        else:
            return {
                "error": "ALREADY_REGISTERED",
                "message": "Public key already registered with same device_id. Idempotent operation.",
                "public_key_hash": existing.public_key_hash,
                "registration_status": existing.registration_status.value,
                "registered_at": existing.registered_at,
                "device_id": existing.device_id,
                "idempotent": True,
            }

    result = registry.register(public_key_b64, device_id)
    return {
        "public_key_hash": result.public_key_hash,
        "registration_status": result.registration_status.value,
        "registered_at": result.registered_at,
        "device_id": result.device_id,
        "environment": env,
        "warning": "This is a TEST registration. Production will use App Attest formal registration.",
    }


@router.post("/store-record")
async def store_record(req: VerifyRequest):
    """
    Store a record for replay detection.

    SECURITY: This endpoint enforces strict validation:
    - photo_base64 is MANDATORY (cannot be None or empty)
    - Signature must be mathematically valid
    - Public key must be registered
    - Device identity must be consistent
    - Photo hash MUST match the manifest's photo_hash

    Invalid evidence will NOT occupy the record_id.

    Zero-byte photos are supported if explicitly provided and hash matches,
    but must be distinguished from missing photos.

    This is a development/testing endpoint, NOT a production-grade
    trusted registration service.
    """
    registry = get_registry()
    manifest = req.manifest

    # CRITICAL: photo_base64 is MANDATORY
    if req.photo_base64 is None:
        return {
            "record_id": manifest.record_id,
            "is_new": False,
            "stored": False,
            "message": "REJECTED: photo_base64 is mandatory. Cannot store record without photo bytes.",
            "rejection_reason": "photo_missing",
        }

    # Check for empty string (different from None)
    if req.photo_base64 == "":
        return {
            "record_id": manifest.record_id,
            "is_new": False,
            "stored": False,
            "message": "REJECTED: photo_base64 is empty string. Must provide actual photo bytes or omit field entirely.",
            "rejection_reason": "photo_empty",
        }

    sig_ok, sig_msg = verify_signature_with_details(manifest)
    if not sig_ok:
        return {
            "record_id": manifest.record_id,
            "is_new": False,
            "stored": False,
            "message": f"REJECTED: Signature invalid - {sig_msg}",
            "rejection_reason": "invalid_signature",
        }

    key_result = registry.lookup(manifest.public_key)
    if key_result.registration_status != KeyRegistrationStatus.REGISTERED:
        return {
            "record_id": manifest.record_id,
            "is_new": False,
            "stored": False,
            "message": "REJECTED: Public key not registered",
            "rejection_reason": "unregistered_key",
        }

    is_consistent, registered_device_id = registry.check_device_identity(
        manifest.public_key, manifest.device_id
    )
    if not is_consistent:
        return {
            "record_id": manifest.record_id,
            "is_new": False,
            "stored": False,
            "message": (
                f"REJECTED: Device identity mismatch. "
                f"Key registered to '{registered_device_id}', "
                f"manifest claims '{manifest.device_id}'"
            ),
            "rejection_reason": "device_identity_mismatch",
        }

    # CRITICAL: Photo hash MUST be verified (not optional)
    try:
        photo_bytes = base64.b64decode(req.photo_base64, validate=True)
        if not verify_photo_hash(manifest, photo_bytes):
            return {
                "record_id": manifest.record_id,
                "is_new": False,
                "stored": False,
                "message": "REJECTED: Photo hash mismatch. Provided photo does not match manifest.photo_hash.",
                "rejection_reason": "photo_hash_mismatch",
            }
    except Exception as e:
        return {
            "record_id": manifest.record_id,
            "is_new": False,
            "stored": False,
            "message": f"REJECTED: Failed to decode photo - {e}",
            "rejection_reason": "photo_decode_failed",
        }

    manifest_dict = manifest.model_dump()
    manifest_hash = compute_manifest_hash(manifest_dict)
    public_key_hash = compute_public_key_hash(manifest.public_key)

    entry = RecordStoreEntry(
        record_id=manifest.record_id,
        photo_hash=manifest.photo_hash,
        device_id=manifest.device_id,
        public_key_hash=public_key_hash,
        stored_at=datetime.now(timezone.utc).isoformat(),
        manifest_hash=manifest_hash,
    )

    is_new, message = registry.store_record(entry)

    return {
        "record_id": entry.record_id,
        "is_new": is_new,
        "stored": is_new,
        "message": message,
        "stored_at": entry.stored_at,
    }


@router.get("/check-record/{record_id}")
async def check_record(record_id: str):
    """
    Check if a record exists in the store.
    This is a development/testing endpoint.
    """
    registry = get_registry()
    entry = registry.get_record(record_id)
    if entry is None:
        return {"exists": False, "record_id": record_id}
    return {
        "exists": True,
        "record_id": entry.record_id,
        "photo_hash": entry.photo_hash,
        "device_id": entry.device_id,
        "stored_at": entry.stored_at,
    }
