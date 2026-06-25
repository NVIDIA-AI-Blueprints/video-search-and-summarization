#!/usr/bin/env python3
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

"""HTTP adapters for /api/v1/verification/config.

Routes are deliberately kept as thin request/response adapters; all
business rules (timestamps, deep merge, prompt sync) live in
``handlers/alert_config/service.py``.
"""

import logging
import os
import sys
from datetime import datetime, timezone
from typing import Union

from fastapi import APIRouter, status
from fastapi.responses import JSONResponse

from .alert_config_schemas import (
    AlertConfigRequest,
    AlertConfigResponse,
    AlertConfigUpdateRequest,
    AlertConfigListResponse,
    AlertConfigSuccessResponse,
)
from .alert_schemas import ErrorResponse
from ..core.dependencies import load_config, load_config_path

sys.path.append(os.path.join(os.path.dirname(__file__), "..", "..", ".."))

from handlers.alert_config import (  # noqa: E402
    AlertConfigAlreadyExists,
    AlertConfigNotFound,
    AlertConfigService,
    build_alert_config_store,
)
from handlers.alert_config.store import AlertConfigStoreError  # noqa: E402
from persistence.exceptions import PersistenceError  # noqa: E402
from its_redis.dynamic_prompt_handler import DynamicPromptHandler  # noqa: E402

logger = logging.getLogger(__name__)
router = APIRouter(
    prefix="/api/v1/verification/config",
    tags=["alert-verification-config"],
)

_service: AlertConfigService = None


def _get_service() -> AlertConfigService:
    global _service
    if _service is None:
        config_path = load_config_path()
        prompt_handler = DynamicPromptHandler(config_path)
        app_config = load_config()
        store = build_alert_config_store(
            prompt_handler._redis_client, app_config
        )
        _service = AlertConfigService(store=store)
    return _service


def _ts() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _error(status_code: int, error_code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"status": "error", "code": error_code, "message": message, "timestamp": _ts()},
    )


def _internal_error(exc: Exception) -> JSONResponse:
    logger.exception("Unexpected error in alert_config endpoint: %s", exc)
    return _error(
        status.HTTP_500_INTERNAL_SERVER_ERROR,
        "internal_error",
        "Internal server error occurred",
    )


def _service_unavailable(exc: Exception) -> JSONResponse:
    """Surface a real backend outage as a 503 instead of hiding it
    behind the in-memory snapshot fallback.

    The composite store keeps a memory snapshot specifically so the
    sink and prompt-handler hot paths stay up when ES + Redis both
    fail — but for the REST API path that fallback is the wrong
    answer: an operator running ``GET /verification/config`` to
    debug an outage gets a 200 with stale data instead of a
    transport-level signal that the durable backend is unreachable.
    The service layer disables the memory fallback so we surface the
    underlying ``PersistenceError`` here as 503.
    """
    logger.warning("Alert config backend unavailable: %s", exc)
    return _error(
        status.HTTP_503_SERVICE_UNAVAILABLE,
        "service_unavailable",
        "Alert config backend is temporarily unavailable; please retry.",
    )


def _to_response(data: dict) -> AlertConfigResponse:
    return AlertConfigResponse(
        alert_type=data.get("alert_type", ""),
        prompt=data.get("prompt", ""),
        system_prompt=data.get("system_prompt"),
        enrichment_prompt=data.get("enrichment_prompt"),
        vlm_params=data.get("vlm_params"),
        output_category=data.get("output_category"),
        created_at=data.get("created_at", ""),
        updated_at=data.get("updated_at", ""),
    )


# ---------------------------------------------------------------------------
# POST  — Create
# ---------------------------------------------------------------------------
@router.post(
    "",
    response_model=AlertConfigResponse,
    status_code=201,
    summary="Create Alert Type Config",
    description="Create a new alert type configuration",
    responses={
        201: {"description": "Config created", "model": AlertConfigResponse},
        409: {"description": "Config already exists", "model": ErrorResponse},
        503: {"description": "Backend storage temporarily unavailable", "model": ErrorResponse},
        500: {"description": "Internal server error", "model": ErrorResponse},
    },
)
async def create_config(request: AlertConfigRequest) -> Union[AlertConfigResponse, JSONResponse]:
    try:
        service = _get_service()
        data = service.create(
            alert_type=request.alert_type,
            prompt=request.prompt,
            system_prompt=request.system_prompt,
            enrichment_prompt=request.enrichment_prompt,
            vlm_params=request.vlm_params.model_dump(exclude_none=True) if request.vlm_params else None,
            output_category=request.output_category,
        )
        return _to_response(data)
    except AlertConfigAlreadyExists as exc:
        return _error(status.HTTP_409_CONFLICT, "config_exists", str(exc))
    except (PersistenceError, AlertConfigStoreError) as exc:
        return _service_unavailable(exc)
    except Exception as exc:
        return _internal_error(exc)


# ---------------------------------------------------------------------------
# GET  — List all
# ---------------------------------------------------------------------------
@router.get(
    "",
    response_model=AlertConfigListResponse,
    summary="List All Configs",
    description="Retrieve all alert type configurations",
    responses={
        200: {"description": "Configs retrieved", "model": AlertConfigListResponse},
        503: {"description": "Backend storage temporarily unavailable", "model": ErrorResponse},
        500: {"description": "Internal server error", "model": ErrorResponse},
    },
)
async def list_configs() -> Union[AlertConfigListResponse, JSONResponse]:
    try:
        service = _get_service()
        configs = [_to_response(item) for item in service.list_all()]
        return AlertConfigListResponse(status="success", configs=configs, count=len(configs))
    except (PersistenceError, AlertConfigStoreError) as exc:
        return _service_unavailable(exc)
    except Exception as exc:
        return _internal_error(exc)


# ---------------------------------------------------------------------------
# GET /{alert_type}  — Get one
# ---------------------------------------------------------------------------
@router.get(
    "/{alert_type}",
    response_model=AlertConfigResponse,
    summary="Get Config",
    description="Get configuration for a specific alert type",
    responses={
        200: {"description": "Config retrieved", "model": AlertConfigResponse},
        404: {"description": "Config not found", "model": ErrorResponse},
        503: {"description": "Backend storage temporarily unavailable", "model": ErrorResponse},
        500: {"description": "Internal server error", "model": ErrorResponse},
    },
)
async def get_config(alert_type: str) -> Union[AlertConfigResponse, JSONResponse]:
    try:
        service = _get_service()
        return _to_response(service.get(alert_type))
    except AlertConfigNotFound as exc:
        return _error(status.HTTP_404_NOT_FOUND, "config_not_found", str(exc))
    except (PersistenceError, AlertConfigStoreError) as exc:
        return _service_unavailable(exc)
    except Exception as exc:
        return _internal_error(exc)


# ---------------------------------------------------------------------------
# PUT /{alert_type}  — Update
# ---------------------------------------------------------------------------
@router.put(
    "/{alert_type}",
    response_model=AlertConfigResponse,
    summary="Update Config",
    description="Update an existing alert type configuration (partial update)",
    responses={
        200: {"description": "Config updated", "model": AlertConfigResponse},
        404: {"description": "Config not found", "model": ErrorResponse},
        503: {"description": "Backend storage temporarily unavailable", "model": ErrorResponse},
        500: {"description": "Internal server error", "model": ErrorResponse},
    },
)
async def update_config(
    alert_type: str,
    request: AlertConfigUpdateRequest,
) -> Union[AlertConfigResponse, JSONResponse]:
    try:
        service = _get_service()
        # Pydantic collapses "field omitted" and "field set to null" to the
        # same value. Use ``model_fields_set`` (or ``__fields_set__`` for
        # Pydantic v1 Config-style models) so we can pass an explicit ``None``
        # to the service and clear stored values.
        try:
            provided = request.model_fields_set
        except AttributeError:
            provided = getattr(request, "__fields_set__", set())

        kwargs = {}
        for field in ("prompt", "system_prompt", "enrichment_prompt", "output_category"):
            if field in provided:
                kwargs[field] = getattr(request, field)
        if "vlm_params" in provided:
            vlm_params = request.vlm_params
            kwargs["vlm_params"] = (
                vlm_params.model_dump(exclude_none=True) if vlm_params else None
            )

        data = service.update(alert_type=alert_type, **kwargs)
        return _to_response(data)
    except AlertConfigNotFound as exc:
        return _error(status.HTTP_404_NOT_FOUND, "config_not_found", str(exc))
    except (PersistenceError, AlertConfigStoreError) as exc:
        return _service_unavailable(exc)
    except Exception as exc:
        return _internal_error(exc)


# ---------------------------------------------------------------------------
# DELETE /{alert_type}
# ---------------------------------------------------------------------------
@router.delete(
    "/{alert_type}",
    response_model=AlertConfigSuccessResponse,
    summary="Delete Config",
    description="Delete an alert type configuration",
    responses={
        200: {"description": "Config deleted", "model": AlertConfigSuccessResponse},
        404: {"description": "Config not found", "model": ErrorResponse},
        503: {"description": "Backend storage temporarily unavailable", "model": ErrorResponse},
        500: {"description": "Internal server error", "model": ErrorResponse},
    },
)
async def delete_config(alert_type: str) -> Union[AlertConfigSuccessResponse, JSONResponse]:
    try:
        service = _get_service()
        service.delete(alert_type)
        return AlertConfigSuccessResponse(
            status="success",
            message=f"Config '{alert_type}' deleted",
        )
    except AlertConfigNotFound as exc:
        return _error(status.HTTP_404_NOT_FOUND, "config_not_found", str(exc))
    except (PersistenceError, AlertConfigStoreError) as exc:
        return _service_unavailable(exc)
    except Exception as exc:
        return _internal_error(exc)
