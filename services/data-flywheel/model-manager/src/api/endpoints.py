# SPDX-FileCopyrightText: Copyright (c) 2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

"""Model Promotion API endpoints (matches model_promotion/openapi.json)."""

import logging

from fastapi import APIRouter, status
from fastapi.responses import JSONResponse

from src.api.errors import envelope_response
from src.api.errors import error_response as _error_response
from src.api.schemas import (
    ApplyRequest,
    ApplyResponse,
    CleanupRequest,
    CleanupResponse,
    ConvergenceReport,
    ConvergenceRequest,
    HealthResponse,
    PullRequest,
    PullResponse,
    ServingStateResponse,
    UnloadRequest,
    UnloadResponse,
)
from src.core.errors import ModelManagerError
from src.core.manager import model_manager

logger = logging.getLogger(__name__)

router = APIRouter()


# The model handlers are sync ``def``: they block (downloads, hashing, poll
# loops), and FastAPI runs sync handlers in a threadpool. ``health`` stays async
# so it can answer while every threadpool slot is busy.
@router.get("/v1/health", response_model=HealthResponse, tags=["health"])
async def health() -> HealthResponse:
    """Liveness."""
    return HealthResponse(status="ok")


@router.post(
    "/v1/models/pull",
    response_model=PullResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["models"],
)
def pull_model(request: PullRequest) -> PullResponse | JSONResponse:
    """Pull (stage) a model version into the serving repo."""
    try:
        return model_manager.pull(request)
    except ModelManagerError as exc:
        return _error_response(exc)


@router.post(
    "/v1/models/apply",
    response_model=ApplyResponse,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["models"],
)
def apply_model(request: ApplyRequest) -> ApplyResponse | JSONResponse:
    """Apply/activate a staged version with a rollout control."""
    try:
        return model_manager.apply(request)
    except ModelManagerError as exc:
        return _error_response(exc)


@router.post("/v1/models/convergence", response_model=ConvergenceReport, tags=["models"])
def convergence(request: ConvergenceRequest) -> ConvergenceReport | JSONResponse:
    """Report whether an applied intent has landed. Stateless.

    The caller passes back ``apply``'s ``applied`` object; the answer is a pure
    function of that plus live serving state. A POST because the expectation is a
    nested body and the poll must not be cached.

    Do not add mutation here: no reconcile-on-read, no phase settling.
    """
    try:
        return model_manager.convergence(request.expectation)
    except ModelManagerError as exc:
        return _error_response(exc)


@router.get("/v1/models/status/{swap_id}", tags=["models"], deprecated=True)
def promotion_status(swap_id: str) -> JSONResponse:
    """Gone. Poll ``POST /v1/models/convergence`` with ``apply``'s ``applied``.

    410 not 404: the surface is retired, the swap id is not unknown.

    Declared before ``/v1/models/{model_name}/serving-state`` so the literal path
    wins. ``response_model`` is omitted so the error envelope survives.
    """
    return envelope_response(
        410,
        # ErrorCode mirrors an external openapi.json and has no "gone" member.
        code="bad_request",
        message=(
            f"GET /v1/models/status/{{swap_id}} is gone (swap_id={swap_id}). "
            "Poll POST /v1/models/convergence with the 'applied' object that "
            "apply returned."
        ),
        retryable=False,
    )


# After /v1/models/status/{swap_id} so the literal route keeps precedence; the
# two only overlap for a model literally named "status".
@router.get(
    "/v1/models/{model_name}/serving-state",
    response_model=ServingStateResponse,
    tags=["models"],
)
def serving_state(model_name: str) -> ServingStateResponse | JSONResponse:
    """Read what is currently live for a model (routing, versions, readiness)."""
    try:
        return model_manager.serving_state(model_name)
    except ModelManagerError as exc:
        return _error_response(exc)


@router.post("/v1/models/unload", response_model=UnloadResponse, tags=["models"])
def unload_model(request: UnloadRequest) -> UnloadResponse | JSONResponse:
    """Evict a version from serving memory, leaving its directory on disk.

    What retiring a baseline wants: stop paying GPU for it, keep it on disk so a
    rollback still has something to load. Reclaiming is ``cleanup``.
    """
    try:
        return model_manager.unload(request)
    except ModelManagerError as exc:
        return _error_response(exc)


@router.post("/v1/models/cleanup", response_model=CleanupResponse, tags=["models"])
def cleanup_model(request: CleanupRequest) -> CleanupResponse | JSONResponse:
    """Reclaim the directory of a version the backend reports reclaimable. Never evicts.

    "Reclaimable" is affirmed three ways: the backend publishes the version in its
    own ``deletable`` array, its row literally reads ``unloaded``, or -- only on a
    backend that DROPS the row on eviction -- the caller asserts
    ``evicted_by_caller``. A resident version is refused, not evicted.

    A directory ALREADY gone answers ``removed``/``disk_reclaimed: true`` and
    deletes nothing; refusing had the sweep re-offering it every tick forever.
    Guards run first, so the same absence under live routing is still a 409.

    A caller predating this release gets no eviction it asked for, and on a
    publishing backend a delete it did not -- hence deploy MM first, then DFW,
    with ``reclaim_dry_run`` (not the age floor) covering the window.
    """
    try:
        return model_manager.cleanup(request)
    except ModelManagerError as exc:
        return _error_response(exc)
