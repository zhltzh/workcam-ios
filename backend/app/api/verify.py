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
async def register_key(public_key_b64: str, device_id: str):
    """
    Register a public key with a device_id.
    This is a development/testing endpoint.
    """
    registry = get_registry()
    result = registry.register(public_key_b64, device_id)
    return {
        "public_key_hash": result.public_key_hash,
        "registration_status": result.registration_status,
        "registered_at": result.registered_at,
        "device_id": result.device_id,
    }


@router.post("/store-record")
async def store_record(req: VerifyRequest):
    """
    Store a record for replay detection.
    This is a development/testing endpoint.
    """
    registry = get_registry()

    manifest_dict = req.manifest.model_dump()
    manifest_hash = compute_manifest_hash(manifest_dict)
    public_key_hash = compute_public_key_hash(req.manifest.public_key)

    entry = RecordStoreEntry(
        record_id=req.manifest.record_id,
        photo_hash=req.manifest.photo_hash,
        device_id=req.manifest.device_id,
        public_key_hash=public_key_hash,
        stored_at=datetime.now(timezone.utc).isoformat(),
        manifest_hash=manifest_hash,
    )

    is_new, message = registry.store_record(entry)

    return {
        "record_id": entry.record_id,
        "is_new": is_new,
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
