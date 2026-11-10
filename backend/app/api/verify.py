from __future__ import annotations

import base64

from fastapi import APIRouter

from app.models.capture import (
    CheckStatus,
    DeviceAttestationStatus,
    EvidenceStatus,
    KeyRegistrationStatus,
    VerifyDetail,
    VerifyRequest,
    VerifyResponse,
    SUPPORTED_VERSIONS,
)
from app.services.key_registry import get_registry
from app.services.signature import (
    validate_manifest_structure,
    verify_photo_hash,
    verify_signature_with_details,
)

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
            key_registration_status=KeyRegistrationStatus.UNKNOWN,
            device_attestation_status=DeviceAttestationStatus.UNKNOWN,
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
            key_registration_status=KeyRegistrationStatus.UNKNOWN,
            device_attestation_status=DeviceAttestationStatus.UNKNOWN,
            summary="Unsupported protocol version",
            details=details,
        )

    photo_hash_status = CheckStatus.NOT_CHECKED
    if req.photo_base64 is not None:
        try:
            photo_bytes = base64.b64decode(req.photo_base64, validate=True)
            hash_ok = verify_photo_hash(manifest, photo_bytes)
            photo_hash_status = (
                CheckStatus.PASSED if hash_ok else CheckStatus.FAILED
            )
            msg = "SHA-256 match" if hash_ok else "SHA-256 mismatch"
        except Exception as e:
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

    if key_reg_status == KeyRegistrationStatus.UNREGISTERED:
        warnings.append(
            "Public key is not registered. Signature is mathematically valid "
            "but the signing device is not recognized. "
            "key_registration_status=unregistered."
        )

    device_attest_status = DeviceAttestationStatus.UNKNOWN
    warnings.append(
        "Device attestation (App Attest) is not implemented in this version. "
        "device_attestation_status=unknown."
    )

    if not sig_ok:
        evidence_status = EvidenceStatus.INVALID
        summary = "Cryptographic signature invalid"
    elif photo_hash_status == CheckStatus.FAILED:
        evidence_status = EvidenceStatus.INVALID
        summary = "Photo hash mismatch"
    elif photo_hash_status == CheckStatus.NOT_CHECKED:
        evidence_status = EvidenceStatus.PARTIAL
        summary = (
            "Signature valid but photo not verified. "
            "Evidence is partial without photo bytes."
        )
    elif key_reg_status == KeyRegistrationStatus.UNREGISTERED:
        evidence_status = EvidenceStatus.PARTIAL
        summary = (
            "Signature valid but public key not registered. "
            "Evidence is partial without key registration."
        )
    else:
        evidence_status = EvidenceStatus.VERIFIED
        summary = "All checks passed"

    return VerifyResponse(
        evidence_status=evidence_status,
        cryptographic_signature_valid=sig_ok,
        key_registration_status=key_reg_status,
        device_attestation_status=device_attest_status,
        summary=summary,
        details=details,
        warnings=warnings,
    )
