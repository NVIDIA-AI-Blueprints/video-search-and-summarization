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

"""
On-demand verification HTTP routes.

Returns HTTP 202 immediately with a correlationId.  VLM processing and
result publishing (Kafka / Elasticsearch) run in a background task via
DirectMediaHandler — identical to the Kafka-driven pipeline.
"""

from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Depends, status
from fastapi.responses import JSONResponse
from openai import BadRequestError

from ..schema.verification_schemas import OnDemandVerificationRequest
from ..service.ondemand_verification_service import (
    AlertTypeNotFoundError,
    OnDemandVerificationService,
)

router = APIRouter(prefix="/api/v1/verification", tags=["verification"])

_ondemand_service: OnDemandVerificationService = None


def get_ondemand_service() -> OnDemandVerificationService:
    global _ondemand_service
    if _ondemand_service is None:
        _ondemand_service = OnDemandVerificationService()
    return _ondemand_service


def _error_response(status_code: int, error: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "status": "error",
            "error": error,
            "message": message,
            "timestamp": datetime.utcnow().isoformat() + "Z",
        },
    )


@router.post(
    "/ondemand",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Verify alert on demand",
    description=(
        "Async on-demand verification using category-based prompt lookup. "
        "Returns HTTP 202 with a correlationId immediately. "
        "VLM processing runs in the background and results are published "
        "to Kafka / Elasticsearch via the same sink as the Kafka pipeline."
    ),
    responses={
        202: {
            "description": "Verification request accepted for background processing",
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "status": {"type": "string"},
                            "correlationId": {"type": "string"},
                            "message": {"type": "string"},
                            "timestamp": {"type": "string", "format": "date-time"},
                        },
                        "required": ["status", "correlationId", "message", "timestamp"],
                    },
                    "example": {
                        "status": "accepted",
                        "correlationId": "incident-123",
                        "message": "Verification request accepted for processing",
                        "timestamp": "2025-06-01T12:00:00Z",
                    },
                }
            },
        },
        400: {
            "description": "Unknown category or invalid request",
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "status": {"type": "string"},
                            "error": {"type": "string"},
                            "message": {"type": "string"},
                            "timestamp": {"type": "string", "format": "date-time"},
                        },
                        "required": ["status", "error", "message", "timestamp"],
                    },
                    "example": {
                        "status": "error",
                        "error": "unknown_category",
                        "message": "No alert config found for category 'unknown_type'",
                        "timestamp": "2025-06-01T12:00:00Z",
                    },
                }
            },
        },
    },
)
async def verify_ondemand(
    payload: OnDemandVerificationRequest,
    background_tasks: BackgroundTasks,
    service: OnDemandVerificationService = Depends(get_ondemand_service),
) -> JSONResponse:
    try:
        message, user_prompt, system_prompt = service.prepare(
            payload.model_dump()
        )
    except AlertTypeNotFoundError as e:
        return _error_response(
            status.HTTP_400_BAD_REQUEST, "unknown_category", str(e)
        )
    except (ValueError, BadRequestError) as e:
        return _error_response(
            status.HTTP_400_BAD_REQUEST, "invalid_request", str(e)
        )

    correlation_id = message["id"]
    background_tasks.add_task(
        service.process_and_publish, message, user_prompt, system_prompt
    )

    return JSONResponse(
        status_code=status.HTTP_202_ACCEPTED,
        content={
            "status": "accepted",
            "correlationId": correlation_id,
            "message": "Verification request accepted for processing",
            "timestamp": datetime.utcnow().isoformat() + "Z",
        },
    )
