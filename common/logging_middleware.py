"""Canonical source for the shared structured-logging middleware used by all 5
services (ticket-service, equipment-history-service, knowledge-service,
recommendation-service, agent-orchestrator).

Do not hand-edit the copies inside services/*/app/logging_middleware.py — those are
generated. Edit this file, then run scripts/sync-common.sh to regenerate them.

Why a sync script instead of a shared pip package or Docker-level copy: each service
builds its own Docker image from its own isolated build context (services/<name>) and
is meant to stay independently deployable, with no shared runtime dependency on an
internal library's version. A generated copy gives one real source of truth to edit
without introducing that coupling, and without breaking each service's independent
local venv + pytest workflow (the file still physically exists where `from
app.logging_middleware import ...` expects it).
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
