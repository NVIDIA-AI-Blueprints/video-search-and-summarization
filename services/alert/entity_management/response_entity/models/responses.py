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

#!/usr/bin/env python3
"""
Response Entity Models

Defines Pydantic models for Alert Bridge response schema.
Supports nested objects for alert, event, and verification information.
"""

from typing import Dict, Any, List, Optional
from datetime import datetime
from pydantic import BaseModel, Field
from ...shared.enums import AlertSeverity, AlertStatus, ReviewResultStatus


class MetaLabel(BaseModel):
    """
    Metadata label key-value pair.
    """
    key: str = Field(..., description="Label key")
    value: str = Field(..., description="Label value")
    
    class Config:
        validate_assignment = True


class AlertInfo(BaseModel):
    """
    Alert information for response.
    """
    severity: AlertSeverity = Field(..., description="Alert severity level")
    status: AlertStatus = Field(..., description="Alert verification status")
    type: str = Field(..., max_length=256, description="Alert type")
    description: str = Field(..., max_length=2000, description="Alert description")
    
    class Config:
        validate_assignment = True
        use_enum_values = True


class EventInfo(BaseModel):
    """
    Event information for response.
    """
    type: str = Field(..., max_length=256, description="Event type")
    description: str = Field(..., max_length=2000, description="Event description")
    
    class Config:
        validate_assignment = True


class ResultDebug(BaseModel):
    """
    Debug information for verification process.
    """
    input_prompt: Optional[str] = Field(None, description="Prompt sent to VSS")
    selected_frames_ts: Optional[List[int]] = Field(None, description="Selected frame timestamps in ms")
    
    class Config:
        validate_assignment = True


class ResultInfo(BaseModel):
    """
    Review result information from VSS processing.
    """
    status: ReviewResultStatus = Field(..., description="Result status: SUCCESS or FAILURE")
    error_string: Optional[str] = Field(None, description="Error message if result is FAILURE")
    verification_result: Optional[bool] = Field(None, description="True, False, or null when unset")
    confidence: Optional[float] = Field(None, ge=0.0, le=1.0, description="Confidence score")
    review_method: str = Field(default="VSS", description="Review method")
    reviewed_by: str = Field(..., description="Model/system that performed the review")
    reviewed_at: str = Field(..., description="Timestamp when review completed (ISO 8601)")
    notes: Optional[str] = Field(None, description="Additional notes")
    debug: Optional[ResultDebug] = Field(None, description="Debug information")
    input_prompt: Optional[str] = Field(None, description="Prompt used for the review request")
    description: Optional[str] = Field(None, description="VLM response summary")
    reasoning: Optional[str] = Field(None, description="Reasoning string output")
    
    class Config:
        validate_assignment = True
        use_enum_values = True


class AlertResponseEntity(BaseModel):
    """
    Complete alert response entity.
    
    Schema structure with nested alert, event, and verification objects.
    Uses the new unified response format.
    """
    
    # Core identification fields
    id: str = Field(..., description="Unique alert ID")
    version: str = Field(default="1.0", description="Response format version")
    timestamp: str = Field(alias="@timestamp", description="Event timestamp (ISO 8601)")
    
    # Source information
    sensor_id: str = Field(description="Source sensor identifier")
    stream_name: Optional[str] = Field(default=None, description="Stream/sensor description")
    video_path: str = Field(description="Path to video file")
    cv_metadata_path: Optional[str] = Field(default=None, description="Path to CV metadata file")
    # Optional clip boundaries (seconds). Included only if VSS echoes these back.
    start_time: Optional[float] = Field(default=None, ge=0.0, description="Clip start time in seconds")
    end_time: Optional[float] = Field(default=None, ge=0.0, description="Clip end time in seconds")
    
    # Top-level confidence (optional)
    confidence: Optional[float] = Field(None, ge=0.0, le=1.0, description="Overall confidence score")
    
    # Nested information objects
    alert: AlertInfo = Field(..., description="Alert information")
    event: EventInfo = Field(..., description="Event information")
    result: ResultInfo = Field(..., description="Review result from VSS")
    
    # Metadata labels (optional)
    meta_labels: List[MetaLabel] = Field(default_factory=list, description="Additional metadata labels")
    
    class Config:
        """Pydantic configuration for optimal performance and compatibility."""
        validate_assignment = True
        use_enum_values = True
        validate_by_name = True  # Allow both field names and aliases
        
        # Schema info
        json_schema_extra = {
            "example": {
                "id": "37e4ca9aad5d4c02846603eca2a2cfbf",
                "version": "1.0",
                "@timestamp": "2025-07-01T15:30:00Z",
                "sensor_id": "788372f8fcbef6be0dd519e47548e3e9f269d262",
                "stream_name": "Stream Description",
                "video_path": "./media/events/abc12345_20240115_103000.mp4",
                "cv_metadata_path": "./media/events/cv_metadata_abc12345.json",
                "confidence": 0.92,
                "alert": {
                    "severity": "HIGH",
                    "status": "VERIFIED",
                    "type": "RESTRICTED_ACCESS",
                    "description": "Person entered in restricted area"
                },
                "event": {
                    "type": "Person Detected",
                    "description": "Person detected in ZONE A"
                },
                "result": {
                    "status": "SUCCESS",
                    "error_string": "",
                    "verification_result": True,
                    "confidence": 0.92,
                    "review_method": "VSS",
                    "reviewed_by": "MVILA-15B v1.0",
                    "reviewed_at": "2025-07-01T15:33:00Z",
                    "notes": "Alert auto-reviewed by VSS",
                    "debug": {
                        "input_prompt": "Is there person detected in restricted area?",
                        "selected_frames_ts": [0, 1000, 2000, 3000, 4000]
                    },
                    "description": "Yes, there is a person with white shirt detected in restricted area",
                    "reasoning": "Alert Reasoning String Output"
                },
                "meta_labels": [
                    {"key": "SHIFT", "value": "NIGHT"},
                    {"key": "ZONE", "value": "A"},
                    {"key": "KEY", "value": "Value"}
                ]
            }
        } 