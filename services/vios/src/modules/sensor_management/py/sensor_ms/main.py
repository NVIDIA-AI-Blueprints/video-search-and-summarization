# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""FastAPI application entry point.

Wires the three contract behaviors (DESIGN.md §6.1):
  1. default_response_class = TextPlainJSONResponse  -> JSON body, Content-Type: text/plain
  2. bearer auth on mutating routes (in the routers)
  3. snake_case {error_code, error_message} envelope via the VmsError handler

Sensor routes are mounted under /api so full paths are /api/v1/sensor/...; health probes are at root.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError

from .logging_setup import configure_logging

# Configure logging as early as possible (at import, so it applies whether started via
# `uvicorn sensor_ms.main:app` or run()).
configure_logging()

log = logging.getLogger(__name__)

from .api.errors import VmsError, VmsErrorCode
from .api.health_routes import router as health_router
from .api.responses import TextPlainJSONResponse
from .api.sensor_routes import router as sensor_router
from .config import get_config
from .core.sensor_management import SensorManagement


@asynccontextmanager
async def lifespan(app: FastAPI):
    cfg = get_config()
    app.state.cfg = cfg
    app.state.mgmt = SensorManagement(cfg)
    await app.state.mgmt.start()
    try:
        yield
    finally:
        await app.state.mgmt.stop()


app = FastAPI(
    title="Sensor Management Service",
    version="1.0.0",
    description="VIOS Sensor Management microservice (Python control-plane reimplementation).",
    default_response_class=TextPlainJSONResponse,
    lifespan=lifespan,
)


@app.exception_handler(VmsError)
async def _vms_error_handler(request: Request, exc: VmsError) -> TextPlainJSONResponse:
    # Log every error response with request context. 5xx are real failures (ERROR); 4xx are client/
    # expected conditions (WARNING); an uncredentialed-ONVIF 401 is the normal "needs credentials"
    # state the UI polls, so keep it at INFO to avoid log spam.
    if exc.http_status >= 500:
        level = logging.ERROR
    elif exc.code == VmsErrorCode.CameraUnauthorizedError:
        level = logging.INFO
    else:
        level = logging.WARNING
    log.log(level, "%s %s -> %d %s: %s", request.method, request.url.path,
            exc.http_status, exc.code.value, exc.message)
    return TextPlainJSONResponse(status_code=exc.http_status, content=exc.envelope())


@app.exception_handler(RequestValidationError)
async def _validation_handler(request: Request, exc: RequestValidationError) -> TextPlainJSONResponse:
    # Map request schema violations to the InvalidParameterError envelope (not FastAPI's 422 shape).
    # Log only field locations/types -- never the submitted values, which can contain credentials.
    locs = [{"loc": e.get("loc"), "type": e.get("type")} for e in exc.errors()]
    log.warning("%s %s -> 400 request validation failed: %s", request.method, request.url.path, locs)
    err = VmsError(VmsErrorCode.InvalidParameterError)
    return TextPlainJSONResponse(status_code=err.http_status, content=err.envelope())


@app.exception_handler(Exception)
async def _unhandled_handler(request: Request, exc: Exception) -> TextPlainJSONResponse:
    # Any unexpected internal failure -> VMSInternalError envelope (C++ parity). Log, never leak.
    log.exception("unhandled error on %s %s", request.method, request.url.path)
    err = VmsError(VmsErrorCode.VMSInternalError)
    return TextPlainJSONResponse(status_code=err.http_status, content=err.envelope())


# Sensor API under /api -> /api/v1/sensor/... (native contract).
app.include_router(sensor_router, prefix="/api")
# Also under /vst/api -> /vst/api/v1/sensor/... so the service works when reached WITHOUT the ingress
# stripping the /vst prefix (the nginx ingress maps /vst/api/... -> /api/...). Harmless either way.
app.include_router(sensor_router, prefix="/vst/api")
# Health probes at root -> /v1/live, /v1/ready, /v1/startup
app.include_router(health_router)


def run() -> None:
    import uvicorn

    cfg = get_config()
    uvicorn.run("sensor_ms.main:app", host="0.0.0.0", port=cfg.http_port, reload=False)


if __name__ == "__main__":
    run()
