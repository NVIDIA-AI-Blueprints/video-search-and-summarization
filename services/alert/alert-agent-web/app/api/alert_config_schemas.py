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
Alert Config Management HTTP Schemas

Request and response schemas for the /api/v1/verification/config endpoints.
Manages full alert type configuration: prompts, VLM params, output category.
"""

import os
import sys
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field, validator

sys.path.append(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
from handlers.alert_config import normalize_alert_type
from handlers.prompt_handler.alert_type_config_loader import VlmParams


def _validate_alert_type(v: str) -> str:
    if not v.replace('_', '').replace('-', '').replace(' ', '').isalnum():
        raise ValueError(
            'Alert type must contain only alphanumeric characters, spaces, underscores, and hyphens'
        )
    return normalize_alert_type(v)


class AlertConfigRequest(BaseModel):
    """Create a new alert type configuration."""
    alert_type: str = Field(..., description="Alert type identifier", min_length=1, max_length=100)
    prompt: str = Field(..., description="User prompt text", min_length=1, max_length=5000)
    system_prompt: Optional[str] = Field(None, description="System prompt text", max_length=5000)
    enrichment_prompt: Optional[str] = Field(None, description="Optional enrichment prompt for post-verification VLM call", max_length=5000)
    vlm_params: Optional[VlmParams] = Field(None, description="VLM parameter overrides")
    output_category: Optional[str] = Field(None, description="Display name for output", max_length=200)

    @validator('alert_type')
    def validate_alert_type(cls, v):
        return _validate_alert_type(v)

    @validator('prompt')
    def validate_prompt(cls, v):
        v = v.strip()
        if not v:
            raise ValueError('Prompt cannot be empty')
        return v

    class Config:
        extra = "forbid"
        json_schema_extra = {
            "example": {
                "alert_type": "collision",
                "prompt": "Analyze the scene for vehicle collisions or near-miss events.",
                "system_prompt": "Answer the user's question correctly in yes or no",
                "vlm_params": {
                    "model": "nvidia/cosmos-reason2-8b",
                    "num_frames": 10,
                    "temperature": 0.6,
                    "max_tokens": 512
                },
                "output_category": "Vehicle Collision"
            }
        }


class AlertConfigUpdateRequest(BaseModel):
    """Update an existing alert type configuration. All fields optional (partial update)."""
    prompt: Optional[str] = Field(None, description="User prompt text", max_length=5000)
    system_prompt: Optional[str] = Field(None, description="System prompt text", max_length=5000)
    enrichment_prompt: Optional[str] = Field(None, description="Optional enrichment prompt for post-verification VLM call", max_length=5000)
    vlm_params: Optional[VlmParams] = Field(None, description="VLM parameter overrides")
    output_category: Optional[str] = Field(None, description="Display name for output", max_length=200)

    @validator('prompt')
    def validate_prompt(cls, v):
        if v is not None:
            v = v.strip()
            if not v:
                raise ValueError('Prompt cannot be empty')
        return v

    class Config:
        extra = "forbid"
        json_schema_extra = {
            "example": {
                "prompt": "Detect vehicle collisions, rear-end impacts, and side-swipe events.",
                "vlm_params": {
                    "num_frames": 8,
                    "max_tokens": 1024
                }
            }
        }


class AlertConfigResponse(BaseModel):
    """Response for a single alert type configuration."""
    alert_type: str = Field(..., description="Alert type identifier")
    prompt: str = Field(..., description="User prompt text")
    system_prompt: Optional[str] = Field(None, description="System prompt text")
    enrichment_prompt: Optional[str] = Field(None, description="Optional enrichment prompt")
    vlm_params: Optional[Dict[str, Any]] = Field(None, description="VLM parameter overrides")
    output_category: Optional[str] = Field(None, description="Display name for output")
    created_at: str = Field("", description="Creation timestamp (ISO 8601)")
    updated_at: str = Field("", description="Last update timestamp (ISO 8601)")

    class Config:
        extra = "forbid"
        json_schema_extra = {
            "example": {
                "alert_type": "collision",
                "prompt": "Analyze the scene for vehicle collisions or near-miss events.",
                "system_prompt": "Answer the user's question correctly in yes or no",
                "enrichment_prompt": None,
                "vlm_params": {
                    "model": "nvidia/cosmos-reason2-8b",
                    "num_frames": 10,
                    "temperature": 0.6,
                    "max_tokens": 512,
                },
                "output_category": "Vehicle Collision",
                "created_at": "2025-06-01T10:00:00Z",
                "updated_at": "2025-06-01T10:00:00Z",
            }
        }


class AlertConfigListResponse(BaseModel):
    """Response for listing all alert type configurations."""
    status: str = Field("success", description="Operation status")
    configs: List[AlertConfigResponse] = Field(..., description="List of configurations")
    count: int = Field(..., description="Total number of configurations")

    class Config:
        json_schema_extra = {
            "example": {
                "status": "success",
                "configs": [
                    {
                        "alert_type": "collision",
                        "prompt": "Analyze the scene for vehicle collisions.",
                        "system_prompt": "Answer yes or no",
                        "enrichment_prompt": None,
                        "vlm_params": {"model": "nvidia/cosmos-reason2-8b", "num_frames": 10},
                        "output_category": "Vehicle Collision",
                        "created_at": "2025-06-01T10:00:00Z",
                        "updated_at": "2025-06-01T10:00:00Z",
                    }
                ],
                "count": 1,
            }
        }


class AlertConfigSuccessResponse(BaseModel):
    """Generic success response for config operations."""
    status: str = Field("success", description="Operation status")
    message: str = Field(..., description="Success message")

    class Config:
        json_schema_extra = {
            "example": {
                "status": "success",
                "message": "Config 'collision' deleted",
            }
        }
