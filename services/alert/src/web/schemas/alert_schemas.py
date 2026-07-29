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
Alert Agent HTTP Schemas

Response schemas for alert submission endpoints.
"""

from typing import Annotated, Any, Dict, List, Optional

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, StrictStr, constr


def _validate_info_value(value: Any) -> Any:
    """Allow string or structured (array/object) ``info`` values, but reject
    bare non-string scalars (int/float/bool).

    Structured values are intentional (e.g. Mode-3 direct-media ``media_urls``)
    and have a well-defined JSON round-trip into the protobuf ``map<string,
    string>`` info field. Bare scalars, on the other hand, would only be
    coerced lossily (``True`` -> ``"True"``), so they are rejected to keep the
    contract explicit. Applied per value so each offending key surfaces as its
    own ``info.<key>`` validation error.
    """
    if isinstance(value, (str, list, dict)):
        return value
    raise ValueError("must be a string, array, or object")


InfoValue = Annotated[Any, BeforeValidator(_validate_info_value)]

class IncidentSubmissionRequest(BaseModel):
    """NvSchema incident accepted by the HTTP JSON endpoint."""

    model_config = ConfigDict(extra="allow")

    id: Optional[StrictStr] = Field(
        default=None,
        max_length=256,
        description="Optional incident identifier",
    )
    sensorId: StrictStr = Field(
        ...,
        min_length=1,
        max_length=256,
        description="Camera or sensor identifier",
    )
    timestamp: StrictStr = Field(
        ...,
        min_length=1,
        max_length=64,
        description="Event start time in ISO 8601 format",
    )
    end: StrictStr = Field(
        ...,
        min_length=1,
        max_length=64,
        description="Event end time in ISO 8601 format",
    )
    category: StrictStr = Field(
        ...,
        min_length=1,
        max_length=256,
        description="Incident category used for prompt matching",
    )
    info: Dict[StrictStr, InfoValue] = Field(
        default_factory=dict,
        description=(
            "Additional metadata. String values are the common case, but "
            "structured values (arrays/objects, e.g. Mode-3 direct-media "
            "``media_urls``) are accepted and JSON-encoded into the protobuf "
            "string map downstream. Bare non-string scalars (int/float/bool) "
            "are rejected to prevent lossy silent coercion."
        ),
    )


class AlertSubmissionResponse(BaseModel):
    """
    HTTP response schema for successful alert submission.
    """
    status: str = Field(..., max_length=32, description="Submission status")
    id: str = Field(..., max_length=256, description="Original event ID (used for correlation)")
    message: str = Field(..., max_length=2000, description="Human-readable status message")
    timestamp: str = Field(..., max_length=64, description="Processing timestamp (ISO 8601)")

    class Config:
        """Pydantic configuration."""
        json_schema_extra = {
            "example": {
                "status": "accepted",
                "id": "evt-12345-67890",
                "message": "Alert queued for processing",
                "timestamp": "2025-01-15T14:30:05Z"
            }
        }


class ErrorResponse(BaseModel):
    """
    HTTP error response schema.
    """
    status: str = Field(..., max_length=32, description="Error status")
    error: str = Field(..., max_length=64, description="Error type")
    message: str = Field(..., max_length=2000, description="Error message")
    details: Optional[List[Dict[str, Any]]] = Field(
        default=None,
        description="Field-level validation errors",
    )
    timestamp: str = Field(..., max_length=64, description="Error timestamp (ISO 8601)")

    class Config:
        """Pydantic configuration."""
        json_schema_extra = {
            "example": {
                "status": "error",
                "error": "validation_failed",
                "message": "Request validation failed with 3 error(s). Please check the request format and required fields.",
                "timestamp": "2025-01-15T14:30:05Z"
            }
        }


class HealthResponse(BaseModel):
    """
    HTTP health check response schema.
    """
    status: str = Field(..., max_length=32, description="Health status")
    service: str = Field(..., max_length=64, description="Service name")
    timestamp: str = Field(..., max_length=64, description="Check timestamp (ISO 8601)")
    components: Optional[Dict[str, constr(max_length=64)]] = Field(None, description="Component health status")
    error: Optional[str] = Field(None, max_length=2000, description="Error message if unhealthy")

    class Config:
        """Pydantic configuration."""
        json_schema_extra = {
            "example": {
                "status": "healthy",
                "service": "alert-submission",
                "timestamp": "2025-01-15T14:30:00Z",
                "components": {
                    "entity_validator": "ok",
                    "event_bridge": "ok"
                }
            }
        }


 