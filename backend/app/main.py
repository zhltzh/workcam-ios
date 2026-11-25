from __future__ import annotations

import logging

from fastapi import FastAPI

from app.api.time_anchor import router as time_anchor_router
from app.api.upload import router as upload_router
from app.api.verify import router as verify_router
from app.api.mvp import router as mvp_router, ensure_ready as mvp_ready
from app.api.originals import router as originals_router
from app.api.sync import router as sync_router
from app.api.device_admin import router as device_admin_router
from app.api.derivatives import router as derivatives_router
from app.api.reports import router as reports_router
from app.api.inspection import router as inspection_router
from app.api.storage import router as storage_router
from app.services.security_config import check_startup_security

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("workcam")

SENSITIVE_KEYS = {"private_key", "secret", "token", "api_key"}


class SensitiveFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        for key in SENSITIVE_KEYS:
            if key in msg.lower():
                record.msg = f"[REDACTED contains '{key}']"
                record.args = None
                return True
        return True


logger.addFilter(SensitiveFilter())

app = FastAPI(
    title="WorkCam Verification Service",
    description=(
        "Prototype verification service for WorkCam trusted photography. "
        "This is NOT a production-grade authentication service."
    ),
    version="0.1.0",
)

app.include_router(verify_router)
app.include_router(time_anchor_router)
app.include_router(upload_router)   # experimental prefix /upload/experimental
app.include_router(mvp_router)      # /api/v1 business surface (W006)
app.include_router(originals_router)  # /api/v1 originals (W007)
app.include_router(sync_router)     # /api/v1/sync (W008)
app.include_router(device_admin_router)  # /api/v1/devices (W009)
app.include_router(derivatives_router)  # /api/v1 derivatives (W011)
app.include_router(reports_router)    # /api/v1 reports (W012)
app.include_router(inspection_router)  # /api/v1 annotations+issues (W013)
app.include_router(storage_router)    # /api/v1 storage ops (B02-W025)

# W009: startup security guards - dangerous configurations abort boot
# (fail_fast=True) or print warnings; prints go to stderr.
import sys as _sys
try:
    check_startup_security(fail_fast=True)
except SystemExit:
    # re-raise cleanly so uvicorn exits with a non-zero code
    raise
check_startup_security(fail_fast=False)  # warnings for the non-fatal set

# W013: business-DB migrations run LAZILY on first request instead of
# at module import. Module-level migrate() captured whatever
# WORKCAM_BUSINESS_DB happened to be set when tests imported app.main -
# pytest's hash-randomized collection order then made later fixtures hit
# a stale/removed tmp DB path (the 'only fails on full-suite runs'
# class of flake, root-caused by setting PYTHONHASHSEED=0 -> all green).
#
# W013-R1 P0 hardening:
#  - a threading.Lock guards the check-then-migrate sequence against
#    concurrent first requests (double-migrate was harmless because
#    migrate() is idempotent, but the lock makes the guarantee explicit)
#  - migration FAILURE now FAILS THE REQUEST with 503 instead of being
#    swallowed: the previous 'log and mark done' let the service keep
#    running on a half-initialized database (violates the R1 acceptance
#    rule: no serving from an incomplete state). _migrations_done stays
#    False on failure so a later request retries when the DB recovers.
import threading as _threading

_migration_lock = _threading.Lock()
_migrations_done = False


def _ensure_migrations() -> None:
    global _migrations_done
    if _migrations_done:
        return
    with _migration_lock:
        if _migrations_done:        # double-checked after acquiring
            return
        mvp_ready()                 # raises on failure -> request 503
        _migrations_done = True


@app.middleware("http")
async def _lazy_migration_middleware(request, call_next):
    try:
        _ensure_migrations()
    except Exception:
        import logging
        logging.getLogger("workcam").exception(
            "business DB migration failed - serving STOPPED for this "
            "request (503); no incomplete-state service is permitted")
        from fastapi.responses import JSONResponse
        return JSONResponse(
            status_code=503,
            content={"detail": {
                "code": "service_unavailable",
                "message": "service unavailable: database "
                           "initialization failed"}})
    return await call_next(request)
