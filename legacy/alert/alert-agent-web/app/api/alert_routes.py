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
Alert Agent HTTP Routes

FastAPI routes for alert submission endpoints.
"""

import logging
import json
from datetime import datetime
from typing import Union
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

# Import existing entity management models
from entity_management.request_entity.models.requests import AlertRequestEntity

# Import response schemas  
from .alert_schemas import (
    AlertSubmissionResponse,
    ErrorResponse,
    HealthResponse
)

# Import service
from ..core.alert_service import AlertSubmissionService


# Create router with versioned prefix
router = APIRouter(prefix="/api/v1/alerts", tags=["alert-submission"])

# Dependency to capture raw request body
async def get_raw_body(request: Request) -> str:
    """Capture raw request body for debugging."""
    body = await request.body()
    raw_json = body.decode('utf-8')
    logger = logging.getLogger(__name__)
    logger.info(f"[DEBUG-RAW] Raw JSON received:\n{raw_json}")
    
    return raw_json

# Global service instance (will be created on first use)
_alert_service: AlertSubmissionService = None


def get_alert_service() -> AlertSubmissionService:
    """
    Dependency to get alert submission service instance.
    
    Returns:
        AlertSubmissionService instance
    """
    global _alert_service
    if _alert_service is None:
        _alert_service = AlertSubmissionService()
    return _alert_service


@router.post(
    "",
    response_model=AlertSubmissionResponse,
    status_code=202,
    summary="Submit Alert for Processing",
    description=(
        "Submit a new alert for processing through the Alert Agent pipeline. "
        "Accepts NvSchema Behavior JSON (default) or a serialized Protobuf "
        "Behavior (set Content-Type: application/x-protobuf)."
    ),
    responses={
        202: {
            "description": "Alert accepted and queued for processing",
            "model": AlertSubmissionResponse,
        },
        422: {
            "description": "Validation error or invalid request format",
            "model": ErrorResponse,
        },
        500: {
            "description": "Internal server error",
            "model": ErrorResponse,
        },
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
                            "sensor": {
                                "type": "object",
                                "properties": {"id": {"type": "string"}},
                            },
                            "analyticsModule": {
                                "type": "object",
                                "properties": {
                                    "id": {"type": "string"},
                                    "info": {"type": "object"},
                                },
                            },
                            "videoPath": {"type": "string"},
                            "event": {
                                "type": "object",
                                "properties": {
                                    "id": {"type": "string"},
                                    "type": {"type": "string"},
                                },
                            },
                        },
                        "required": ["id", "timestamp", "sensor", "event"],
                    },
                    "example": {
                        "id": "alert-12345",
                        "timestamp": "2025-01-15T14:30:00Z",
                        "end": "2025-01-15T14:30:00Z",
                        "sensor": {"id": "cam_highway_01"},
                        "analyticsModule": {
                            "id": "Alert Module",
                            "info": {"source": "http_ingest"},
                        },
                        "videoPath": "/media/videos/traffic_incident.mp4",
                        "event": {
                            "id": "alert-12345",
                            "type": "alert_event",
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
async def submit_alert(
    request: Request,
    service: AlertSubmissionService = Depends(get_alert_service)
) -> Union[AlertSubmissionResponse, JSONResponse]:
    """
    Submit an NvSchema alert for processing.
    
    This endpoint accepts NvSchema JSON and publishes a Protobuf Behavior to the
    Kafka alert topic configured in config.yaml. The alert is processed
    asynchronously by downstream components.
    
    Args:
        request: Alert submission request
        service: Alert submission service (injected dependency)
        
    Returns:
        Success response with processing details or error response
        
    Example Request (NvSchema Behavior):
        ```json
        {
            "id": "alert-12345",
            "timestamp": "2025-01-15T14:30:00Z",
            "end": "2025-01-15T14:30:00Z",
            "sensor": { "id": "cam_highway_01" },
            "analyticsModule": { "id": "Alert Module", "info": { "source": "http_ingest" } },
            "videoPath": "/media/videos/traffic_incident.mp4",
            "event": { "id": "alert-12345", "type": "alert_event" }
        }
        ```
    """
    logger = logging.getLogger(__name__)
    
    # Protobuf path: Content-Type must be application/x-protobuf
    content_type = request.headers.get("content-type", "")
    if content_type.lower().strip() == "application/x-protobuf":
        body_bytes = await request.body()
        response_data, status_code = await service.submit_nvschema_alert_protobuf(body_bytes)
        return JSONResponse(status_code=status_code, content=response_data)
    else:
        # JSON NvSchema path (default)
        try:
            incoming_json = await request.json()
        except Exception:
            logger.error("Failed to parse request body as JSON")
            return JSONResponse(
                status_code=422,
                content={
                    "status": "error",
                    "error": "validation_failed",
                    "message": "Request body must be valid JSON",
                    "timestamp": datetime.utcnow().isoformat() + "Z"
                }
            )
        response_data, status_code = await service.submit_nvschema_alert(incoming_json)
        return JSONResponse(status_code=status_code, content=response_data)


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Alert Submission Health Check",
    description="Check the health status of the alert submission service",
    responses={
        200: {
            "description": "Service is healthy",
            "model": HealthResponse
        },
        503: {
            "description": "Service is unhealthy",
            "model": HealthResponse
        }
    }
)
async def alert_submission_health(
    service: AlertSubmissionService = Depends(get_alert_service)
) -> JSONResponse:
    """
    Check the health of the alert submission service.
    
    This endpoint verifies that all components required for alert submission
    are functioning properly, including entity validation and event bridge
    connectivity.
    
    Returns:
        Health status with component details
    """
    try:
        # Basic health check - verify service is initialized
        if service is None:
            return JSONResponse(
                status_code=503,
                content={
                    "status": "unhealthy",
                    "service": "alert-submission",
                    "timestamp": datetime.utcnow().isoformat() + "Z",
                    "error": "Alert submission service not initialized"
                }
            )
        
        # Check if we can access the entity validator
        validator_status = "ok" if service.entity_validator else "error"
        
        # Check Redis event bridge connectivity
        try:
            event_bridge_status = "ok" if hasattr(service, "redis_client") and service.redis_client and service.redis_client.ping() else "error"
        except Exception:
            event_bridge_status = "error"
        
        # Determine overall status
        overall_status = "healthy" if (validator_status == "ok" and event_bridge_status == "ok") else "unhealthy"
        status_code = 200 if overall_status == "healthy" else 503
        
        health_response = {
            "status": overall_status,
            "service": "alert-submission",
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "components": {
                "entity_validator": validator_status,
                "event_bridge": event_bridge_status
            }
        }
        
        # Add error field if unhealthy
        if overall_status == "unhealthy":
            health_response["error"] = "One or more components are not functioning properly"
        
        return JSONResponse(
            status_code=status_code,
            content=health_response
        )
        
    except Exception as e:
        return JSONResponse(
            status_code=503,
            content={
                "status": "unhealthy",
                "service": "alert-submission",
                "timestamp": datetime.utcnow().isoformat() + "Z",
                "error": f"Health check failed: {str(e)}"
            }
        ) 