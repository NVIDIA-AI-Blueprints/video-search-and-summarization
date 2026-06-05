# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import sys
import os
# Add parent directory to Python path for imports
sys.path.append(os.path.join(os.path.dirname(__file__), '..', '..'))

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from metrics import PROMETHEUS_ENABLED
import logging
from datetime import datetime
from models.api_status import ErrorCode, ResponseStatus
from .api.routes import router as heartbeat_router
from .api.verification_routes import router as verification_router
from .api.alert_routes import router as alert_router
from .api.incident_routes import router as incident_router
from .api.alert_config_routes import router as alert_config_router
from .api.realtime_routes import (
    router as realtime_router,
    validate_always_on_config_at_startup,
)
from .websocket.websocket_routes import router as websocket_router
from .websocket.websocket_service import websocket_service
from .core.dependencies import load_config

app = FastAPI(
    title="Alert Agent API",
    description="HTTP API for alert submission, prompt management, and WebSocket real-time alert broadcasting",
    version="1.0.0",
    redirect_slashes=False,
    servers=[
        {"url": "/", "description": "Alert Verification microservice endpoint"},
    ],
)

cors_cfg = load_config().get("cors", {})
if cors_cfg.pop("enabled", True):
    app.add_middleware(CORSMiddleware, **cors_cfg)

# Configure logging
logger = logging.getLogger(__name__)

# Custom exception handler for validation errors
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """
    Custom handler for Pydantic validation errors.
    Logs detailed validation errors and returns 422 with error details.
    """
    # Extract request details for logging
    try:
        request_body = await request.body()
        request_json = request_body.decode('utf-8') if request_body else "No body"
    except Exception:
        request_json = "Could not parse request body"
    
    # Log the validation error summary
    logger.error(f"Validation error for alert submission: {request.method} {request.url.path} "
                f"- {len(exc.errors())} error(s) in request: "
                f"{request_json[:200] + '...' if len(request_json) > 200 else request_json}")
    
    # Log each validation error for easier debugging
    for i, error in enumerate(exc.errors(), 1):
        field_path = " -> ".join(str(loc) for loc in error["loc"])
        error_type = error["type"]
        error_msg = error["msg"]
        input_value = str(error.get("input", "N/A"))[:100]
        
        logger.error(f"Validation error {i}/{len(exc.errors())}: "
                    f"field='{field_path}', type='{error_type}', "
                    f"message='{error_msg}', input='{input_value}'")
    
    return JSONResponse(
        status_code=422,
        content={
            "status": ResponseStatus.ERROR,
            "error": ErrorCode.VALIDATION_FAILED,
            "message": f"Request validation failed with {len(exc.errors())} error(s). Please check the request format and required fields.",
            "timestamp": datetime.utcnow().isoformat() + "Z"
        }
    )

# Include sampling/heartbeat router (existing functionality)
#app.include_router(heartbeat_router, tags=["sampling"])

# Include real-time VLM alert management router
app.include_router(realtime_router)

# Include on-demand verification router
app.include_router(verification_router)

# Include alert config management router
app.include_router(alert_config_router)

# Include alert submission router (existing functionality)
app.include_router(alert_router)

# Include incident submission router (new functionality)
app.include_router(incident_router)

# Include WebSocket router (new functionality)
app.include_router(websocket_router, tags=["websocket"])


# Application lifecycle events
@app.on_event("startup")
async def startup_event():
    """Start background services when FastAPI starts."""
    logger.info("Starting FastAPI application")

    # Validate always-on rules config up-front if the feature is enabled
    # in config.yaml. This is deliberately NOT wrapped in try/except —
    # a misconfigured rules file should crash app boot (visible in
    # deployment logs) rather than silently surface on the first camera
    # event. When `alert_agent.always_on` is false (default), this is a
    # no-op and the endpoint returns 503 ALWAYS_ON_DISABLED.
    validate_always_on_config_at_startup()

    # Eagerly build + hydrate the alert-config store. ``_get_service``
    # is otherwise lazy — without this call the first REST request
    # after boot pays the in-band hydration latency (Redis seed +
    # ES list) and any backend misconfiguration only surfaces on the
    # first user-visible API call rather than at startup. Wrap in
    # try/except so a transient backend hiccup at boot does not
    # block the rest of the FastAPI startup; the store will be
    # rebuilt on first request via the existing lazy path.
    try:
        from .api.alert_config_routes import _get_service
        _get_service()
        logger.info("Alert config service eagerly initialised")
    except Exception as e:
        logger.warning(
            "Eager alert config service init failed; falling back to "
            "lazy init on first REST request: %s", e,
        )

    try:
        # Start WebSocket service (Redis consumer)
        await websocket_service.start()
        logger.info("WebSocket service started successfully")
    except Exception as e:
        logger.error(f"Failed to start WebSocket service: {e}")
        # Don't prevent app startup, just log the error


@app.on_event("shutdown")
async def shutdown_event():
    """Stop background services when FastAPI shuts down."""
    logger.info("Shutting down FastAPI application")
    try:
        # Stop WebSocket service
        await websocket_service.stop()
        logger.info("WebSocket service stopped successfully")
    except Exception as e:
        logger.error(f"Error stopping WebSocket service: {e}")

# Basic health check endpoint
@app.get("/health")
async def health_check():
    """Basic health check for Alert Bridge."""
    return {"status": "ok", "message": "Alert Bridge is running"}


# Prometheus metrics endpoint info
@app.get("/metrics")
async def metrics():
    """
    Prometheus metrics are served from the main process on port 9081.
    This endpoint provides guidance for the correct metrics URL.
    """
    if not PROMETHEUS_ENABLED:
        return Response(content="Prometheus metrics disabled", status_code=404)
    
    prometheus_port = os.getenv("PROMETHEUS_PORT", "9081")
    return Response(
        content=f"Prometheus metrics available at http://localhost:{prometheus_port}/metrics\n",
        status_code=200,
        media_type="text/plain"
    )

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000) 