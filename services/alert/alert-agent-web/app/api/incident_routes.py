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
Incident HTTP Routes

FastAPI routes for incident submission endpoints (NvSchema).
"""

import logging
from datetime import datetime
from typing import Union
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from .alert_schemas import (
    AlertSubmissionResponse as IncidentSubmissionResponse,  # reuse envelope shape
    ErrorResponse,
)
from ..core.alert_service import AlertSubmissionService

router = APIRouter(prefix="/api/v1/incidents", tags=["incident-submission"])

_incident_service: AlertSubmissionService = None


def get_incident_service() -> AlertSubmissionService:
    global _incident_service
    if _incident_service is None:
        _incident_service = AlertSubmissionService()
    return _incident_service


@router.post(
    "",
    response_model=IncidentSubmissionResponse,
    status_code=202,
    summary="Submit Incident for Processing",
    description=(
        "Submit a new incident (NvSchema) for processing and publish to Kafka. "
        "Accepts NvSchema Incident JSON (default) or a serialized Protobuf "
        "Incident (set Content-Type: application/x-protobuf)."
    ),
    responses={
        202: {"description": "Incident accepted and queued", "model": IncidentSubmissionResponse},
        400: {"description": "Invalid Protobuf payload", "model": ErrorResponse},
        422: {"description": "Invalid JSON body", "model": ErrorResponse},
        500: {"description": "Internal server error", "model": ErrorResponse},
    },
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"},
                            "timestamp": {"type": "string", "format": "date-time"},
                            "end": {"type": "string", "format": "date-time"},
                            "sensorId": {"type": "string"},
                            "category": {"type": "string"},
                            "info": {"type": "object"},
                            "event": {"type": "object"},
                        },
                        "required": ["id", "timestamp", "sensorId"],
                    },
                    "example": {
                        "id": "incident-67890",
                        "timestamp": "2025-01-15T14:30:00Z",
                        "end": "2025-01-15T14:30:30Z",
                        "sensorId": "cam_warehouse_02",
                        "category": "collision",
                        "info": {
                            "media_urls": [
                                "http://localhost:30888/vst/sim/media/incident.mp4"
                            ],
                            "media_type": "video",
                        },
                        "event": {
                            "id": "incident-67890",
                            "type": "incident_event",
                        },
                    },
                },
                "application/x-protobuf": {
                    "schema": {"type": "string", "format": "binary"},
                },
            },
        }
    },
)
async def submit_incident(
    request: Request,
    service: AlertSubmissionService = Depends(get_incident_service),
) -> Union[IncidentSubmissionResponse, JSONResponse]:
    """
    Submit an NvSchema Incident for processing.

    JSON default; set Content-Type: application/x-protobuf to send a serialized Incident proto.
    """
    logger = logging.getLogger(__name__)

    content_type = request.headers.get("content-type", "").lower().strip()
    if content_type == "application/x-protobuf":
        body_bytes = await request.body()
        response_data, status_code = await service.submit_nvschema_incident_protobuf(body_bytes)
        return JSONResponse(status_code=status_code, content=response_data)

    # JSON path
    try:
        incoming_json = await request.json()
    except Exception:
        logger.error("Failed to parse incident request body as JSON")
        return JSONResponse(
            status_code=422,
            content={
                "status": "error",
                "error": "validation_failed",
                "message": "Request body must be valid JSON",
                "timestamp": datetime.utcnow().isoformat() + "Z",
            },
        )

    response_data, status_code = await service.submit_nvschema_incident(incoming_json)
    return JSONResponse(status_code=status_code, content=response_data)


