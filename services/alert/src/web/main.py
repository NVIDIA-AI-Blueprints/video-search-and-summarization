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

import os
import time
from typing import Optional

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from metrics import PROMETHEUS_ENABLED
from utils import fleet_state
import logging
from datetime import datetime
from schemas.api_status import ErrorCode, ResponseStatus
from .api.verification_routes import router as verification_router
from .api.alert_routes import router as alert_router
from .api.incident_routes import router as incident_router
from .api.alert_config_routes import router as alert_config_router
from .api.realtime_routes import (
    router as realtime_router,
    validate_always_on_config_at_startup,
)
from .core.dependencies import load_config

app = FastAPI(
    title="Alert Agent API",
    description="HTTP API for alert submission, prompt management, and verification config",
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

# Readiness state. The alert-config store MUST be built successfully at
# startup for the service to be ready: if persistence is enabled but ES is
# unreachable, or a non-dev profile has persistence disabled without the
# explicit dev opt-in, the store build raises and the service must
# report NOT ready rather than admitting traffic to a broken subsystem.
_startup_ready: bool = False
_startup_error: str = "startup has not completed"

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

    # Eagerly build + hydrate the alert-config store and gate readiness on
    # it. A failure here is NOT swallowed: the store build enforces the
    # persistence gate and confirms ES is reachable, so if it raises
    # the service marks itself NOT ready and ``/health`` returns 503. This
    # prevents a pod from admitting traffic while a mandatory subsystem
    # (durable, ES-backed config storage) is unusable.
    global _startup_ready, _startup_error
    try:
        from .api.alert_config_routes import _get_service
        _get_service()
        _startup_ready = True
        _startup_error = ""
        logger.info("Alert config service eagerly initialised; service is ready")
    except Exception as e:
        _startup_ready = False
        _startup_error = f"alert-config store initialisation failed: {e}"
        logger.error(
            "Alert config store initialisation failed at startup; service will "
            "report NOT ready until this is resolved: %s", e,
        )


@app.on_event("shutdown")
async def shutdown_event():
    """Stop background services when FastAPI shuts down."""
    logger.info("Shutting down FastAPI application")

_NOT_READY = "not_ready"


def _startup_failure() -> Optional[JSONResponse]:
    """503 while the alert-config store could not be initialised.

    Persistence enabled but Elasticsearch unreachable, or a non-dev profile
    with persistence disabled. This process cannot serve its own API in that
    state, so it is a failure for both endpoints below.
    """
    if _startup_ready:
        return None
    return JSONResponse(
        status_code=503,
        content={
            "status": _NOT_READY,
            "message": _startup_error or "service is not ready",
        },
    )


# Both endpoints report startup and the pipeline fleet. ``/ready`` exists only
# as the conventional name; the aggregate worker assignment state must reach
# ``/health``, which is what the deployment contract probes.
async def _health_payload(ready_message: str):
    failure = _startup_failure()
    if failure is not None:
        return failure
    degraded = _degraded_workers()
    if degraded:
        # A rebalance can take every partition from a worker that is still
        # running. Reporting ok while part of the instance serves nothing
        # hides exactly the degradation this is for.
        return JSONResponse(
            status_code=503,
            content={"status": _NOT_READY, "message": degraded},
        )
    return {"status": "ok", "message": ready_message}


@app.get("/health")
async def health_check():
    """Health + readiness for Alert Bridge, including the pipeline fleet."""
    return await _health_payload("Alert Bridge is running")


@app.get("/ready")
async def readiness_check():
    """The same answer as ``/health``, under the conventional probe name.

    Additive only. Fleet state stays on ``/health`` because that is the
    endpoint operators and the deployment contract already point at.
    """
    return await _health_payload("Alert Bridge is ready")


_DEGRADED_CACHE: dict = {"at": 0.0, "value": None}
_DEGRADED_TTL_SECONDS = 1.0


def _describe_fleet(configured, alive, ready) -> Optional[str]:
    """The degradation to report, or None when the fleet is whole."""
    if configured <= 0:
        return None
    if alive < configured:
        # A dead worker keeps its ready signal until the supervisor tears the
        # instance down, so readiness alone can still look whole for a poll.
        return f"{int(alive)} of {int(configured)} pipeline processes are alive"
    if ready < configured:
        return (f"{int(ready)} of {int(configured)} pipeline processes hold a "
                f"partition assignment")
    return None


def _degraded_workers() -> Optional[str]:
    """Describe the fleet when fewer workers are alive or assigned than exist.

    Read from the shared array the parent publishes, which crosses to this
    process whether or not metrics are exported. The metric shards are the
    fallback, for a process that was started without the array; an
    observability switch used to decide whether this endpoint could tell a
    dead fleet from a whole one, and it should not.
    """
    # The shared array first: it is published whether or not metrics are
    # exported, so an observability switch cannot decide whether this endpoint
    # can tell a dead fleet from a whole one.
    published = fleet_state.read()
    if published is not None:
        configured, alive, ready = published
        return _describe_fleet(configured, alive, ready)

    if not os.getenv("PROMETHEUS_MULTIPROC_DIR"):
        return None

    # Cached: reading these two numbers means mmapping and parsing every
    # metric shard in the directory, histograms included, and /health is the
    # most frequently polled endpoint there is. The counts behind it are
    # refreshed on the supervisor's one-second poll, so a fresher read carries
    # no more information than this does.
    now = time.monotonic()
    if now - _DEGRADED_CACHE["at"] < _DEGRADED_TTL_SECONDS:
        return _DEGRADED_CACHE["value"]

    try:
        from prometheus_client import CollectorRegistry, multiprocess

        registry = CollectorRegistry()
        multiprocess.MultiProcessCollector(registry)
        values = {
            sample.name: sample.value
            for metric in registry.collect()
            for sample in metric.samples
        }
        configured = values.get("alert_bridge_pipeline_processes_configured")
        ready = values.get("alert_bridge_pipeline_processes_ready")
        alive = values.get("alert_bridge_pipeline_processes_alive")
        if configured is None or ready is None or configured <= 0:
            _DEGRADED_CACHE.update(at=now, value=None)
            return None
        degraded = _describe_fleet(configured, configured if alive is None else alive, ready)
        _DEGRADED_CACHE.update(at=now, value=degraded)
        return degraded
    except Exception:
        # Degraded, not silent. The shards are the only channel left once the
        # array is absent, so one that cannot be read leaves a dead fleet
        # indistinguishable from a whole one -- and answering ok is the single
        # thing this endpoint must never do on a guess.
        unreadable = "pipeline readiness could not be read from the metric shards"
        _DEGRADED_CACHE.update(at=now, value=unreadable)
        logger.debug("Could not read pipeline readiness from metrics", exc_info=True)
        return unreadable


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