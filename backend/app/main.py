from __future__ import annotations

import logging

from fastapi import FastAPI

from app.api.verify import router as verify_router

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
