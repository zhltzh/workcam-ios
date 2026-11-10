from __future__ import annotations

import base64

from fastapi import APIRouter

from app.models.capture import (
    VerifyDetail,
    VerifyRequest,
    VerifyResponse,
    SUPPORTED_VERSIONS,
)
from app.services.signature import (
    validate_manifest_structure,
    verify_photo_hash,
    verify_signature_with_details,
)
from app.services.chain import verify_chain_link

router = APIRouter()


@router.get("/health")
async def health():
    return {"status": "ok", "service": "workcam-verify"}


@router.post("/verify", response_model=VerifyResponse)
async def verify(req: VerifyRequest):
    details: list[VerifyDetail] = []
    manifest = req.manifest

    struct_errors = validate_manifest_structure(manifest)
    if struct_errors:
        for err in struct_errors:
            details.append(VerifyDetail(
                check="structure", passed=False, message=err
            ))
        return VerifyResponse(
            valid=False,
            summary="Structure validation failed",
            details=details,
        )

    details.append(VerifyDetail(
        check="structure", passed=True, message="Manifest structure valid"
    ))

    if manifest.version not in SUPPORTED_VERSIONS:
        details.append(VerifyDetail(
            check="version",
            passed=False,
            message=f"Unsupported version {manifest.version}",
        ))
        return VerifyResponse(
            valid=False,
            summary="Unsupported protocol version",
            details=details,
        )

    if req.photo_base64 is not None:
        try:
            photo_bytes = base64.b64decode(req.photo_base64, validate=True)
            hash_ok = verify_photo_hash(manifest, photo_bytes)
            details.append(VerifyDetail(
                check="photo_hash",
                passed=hash_ok,
                message="SHA-256 match" if hash_ok else "SHA-256 mismatch",
            ))
        except Exception as e:
            details.append(VerifyDetail(
                check="photo_hash",
                passed=False,
                message=f"Failed to decode photo: {e}",
            ))
    else:
        details.append(VerifyDetail(
            check="photo_hash",
            passed=True,
            message="Skipped (no photo provided)",
        ))

    sig_ok, sig_msg = verify_signature_with_details(manifest)
    details.append(VerifyDetail(
        check="ecdsa_signature", passed=sig_ok, message=sig_msg
    ))

    all_passed = all(d.passed for d in details)

    return VerifyResponse(
        valid=all_passed,
        summary="All checks passed" if all_passed else "One or more checks failed",
        details=details,
    )
