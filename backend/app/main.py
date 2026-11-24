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

# W009: startup security guards - dangerous configurations abort boot
# (fail_fast=True) or print warnings; prints go to stderr.
import sys as _sys
try:
    check_startup_security(fail_fast=True)
except SystemExit:
    # re-raise cleanly so uvicorn exits with a non-zero code
    raise
check_startup_security(fail_fast=False)  # warnings for the non-fatal set

# run business-DB migrations at startup (idempotent)
try:
    mvp_ready()
except Exception:  # startup must not crash when the DB path is read-only
    import logging
    logging.getLogger("workcam").exception("business DB migration failed at "
                                           "startup - /api/v1 may be unavailable")
