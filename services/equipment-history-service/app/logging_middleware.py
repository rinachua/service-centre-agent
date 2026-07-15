"""GENERATED FILE — do not edit directly.

Source of truth: common/logging_middleware.py at the repo root. Edit that file,
then run `python3 scripts/sync-common.py` to regenerate this copy (and the other
4 services' copies) from it.
"""

import json
import logging
import sys
import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware


def configure_logging(service_name: str) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger = logging.getLogger(service_name)
    logger.setLevel(logging.INFO)
    logger.handlers = [handler]
    logger.propagate = False


class RequestIDMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
        start = time.perf_counter()
        response = await call_next(request)
        latency_ms = round((time.perf_counter() - start) * 1000, 2)
        response.headers["X-Request-ID"] = request_id
        log = logging.getLogger(request.app.title)
        log.info(json.dumps({
            "request_id": request_id,
            "method": request.method,
            "path": request.url.path,
            "status": response.status_code,
            "latency_ms": latency_ms,
        }))
        return response
