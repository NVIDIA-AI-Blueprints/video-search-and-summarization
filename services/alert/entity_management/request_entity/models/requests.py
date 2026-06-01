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
Request Entity Models

Defines Pydantic models for incoming Alert requests.
Provides clean, validated entity models for modern alert processing.
"""

from typing import Dict, Any, List, Optional
from datetime import datetime
from pydantic import BaseModel, Field, validator
from ...shared.enums import AlertSeverity, AlertStatus
from .parameters import VSSParams, VLMParams, MetaLabel


def _get_request_defaults() -> Dict[str, Any]:
    """Get request defaults from external configuration (shared singleton)."""
    try:
        from ...config.defaults_loader import AlertsDefaultsConfigLoader
        loader = AlertsDefaultsConfigLoader()
        config = loader.load_defaults()
        return config.request_defaults
    except Exception as e:
        # If config loading fails, we should fail rather than use fallbacks
        raise RuntimeError(f"Failed to load request defaults from configuration: {e}")


def _get_optional_field_default(field_name: str):
    """
    Get default value for an optional field from config.
    Returns None if field is not defined in config (field won't be added to entity).
    """
    defaults = _get_request_defaults()
    return defaults.get(field_name)


class AlertInfo(BaseModel):
    """
    Alert information container.
    
    Contains core alert metadata including severity, status, and classification.
    """
    severity: AlertSeverity = Field(..., description="Alert severity level")
    status: AlertStatus = Field(..., description="Current alert status")
    type: str = Field(..., min_length=1, max_length=256, description="Type of alert")
    description: str = Field(..., min_length=1, max_length=2000, description="Human-readable alert description")
    
    @validator('severity', pre=True)
    def normalize_severity(cls, v):
        """Make severity case-insensitive by converting to uppercase."""
        if isinstance(v, str):
            return v.upper()
        return v
    
    @validator('status', pre=True)
    def normalize_status(cls, v):
        """Make status case-insensitive by converting to uppercase."""
        if isinstance(v, str):
            return v.upper()
        return v
    
    @validator('type', pre=True)
    def normalize_alert_type(cls, v):
        """Preserve provided case; trim whitespace only (schema uses enum-like names)."""
        if isinstance(v, str):
            return v.strip()
        return v
    
    class Config:
        """Pydantic configuration for optimal performance."""
        validate_assignment = True
        use_enum_values = True


class EventInfo(BaseModel):
    """
    Event information container.
    
    Contains metadata about the underlying event that triggered the alert.
    """
    type: str = Field(..., min_length=1, max_length=256, description="Type of event detected")
    description: str = Field(..., min_length=1, max_length=2000, description="Detailed event description")
    confidence: Optional[float] = Field(None, ge=0.0, le=1.0, description="Detection confidence score")
    
    class Config:
        """Pydantic configuration for optimal performance."""
        validate_assignment = True
        use_enum_values = True


class AlertRequestEntity(BaseModel):
    """
    Complete alert request entity for processing.
    
    Represents a validated alert request with all required fields and applied defaults.
    Used for processing through VLM/VSS pipelines with comprehensive validation.
    """
    
    # Core identification
    id: str = Field(..., min_length=1, max_length=256, description="Unique request identifier")
    version: str = Field(default="1.0", max_length=32, description="Request format version")
    timestamp: str = Field(alias="@timestamp", max_length=64, description="Request timestamp")

    # Source information
    sensor_id: str = Field(min_length=1, max_length=256, description="Source sensor identifier")
    stream_name: Optional[str] = Field(default=None, max_length=256, description="Stream/Sensor Description")
    video_path: str = Field(min_length=1, max_length=2048, description="Path to video file or stream")
    # Optional VST identifier to correlate with VST media/timeline when provided
    vst_id: Optional[str] = Field(default=None, alias="vst_id", max_length=256, description="Optional VST correlation identifier")

    # Alert and event information
    alert: AlertInfo = Field(..., description="Alert information")
    event: EventInfo = Field(..., description="Event information")

    # Processing parameters
    vss_params: Optional[VSSParams] = Field(default=None, description="VSS processing parameters (optional in HTTP; defaults will be injected)")

    # Optional metadata - only included if provided in input OR defined in config
    confidence: Optional[float] = Field(
        default=None,  # Will be set during validation if needed
        ge=0.0, 
        le=1.0, 
        description="Overall confidence score"
    )
    cv_metadata_path: Optional[str] = Field(
        default=None,
        max_length=2048,
        description="Path to computer vision metadata"
    )
    # Optional clip boundaries (seconds). Either may be present; if both present, enforce end_time > start_time
    start_time: Optional[float] = Field(
        default=None,
        ge=0.0,
        description="Starting time of the clip within the input video, in seconds"
    )
    end_time: Optional[float] = Field(
        default=None,
        ge=0.0,
        description="Ending time of the clip within the input video, in seconds"
    )
    meta_labels: Optional[List[MetaLabel]] = Field(
        default=None,
        max_items=1000,
        description="Additional metadata labels"
    )
    
    class Config:
        """Pydantic configuration for optimal performance and compatibility."""
        validate_assignment = True
        use_enum_values = True
        validate_by_name = True  # Allow both field names and aliases
    
    @validator('timestamp', pre=True)
    def validate_timestamp(cls, v):
        """Ensure timestamp is in proper ISO format."""
        if isinstance(v, datetime):
            return v.isoformat()
        return v
    
    @validator('confidence', pre=True, always=True)
    def set_confidence_default(cls, v, values):
        """Set confidence from config if not provided in input."""
        if v is None:  # Not provided in input
            config_default = _get_optional_field_default('confidence')
            return config_default  # Could be None if not in config
        return v  # Use provided value
    
    @validator('cv_metadata_path', pre=True, always=True)
    def set_cv_metadata_path_default(cls, v, values):
        """Set cv_metadata_path from config if not provided in input."""
        if v is None:  # Not provided in input
            config_default = _get_optional_field_default('cv_metadata_path')
            return config_default  # Could be None if not in config
        return v  # Use provided value
    
    @validator('meta_labels', pre=True, always=True)
    def set_meta_labels_default(cls, v, values):
        """Set meta_labels from config if not provided in input."""
        if v is None:  # Not provided in input
            config_default = _get_optional_field_default('meta_labels')
            return config_default  # Could be None if not in config
        return v  # Use provided value

    @validator('end_time')
    def validate_end_time_with_start(cls, end_time_value, values):
        """Cross-field validation: if both start_time and end_time exist, enforce end_time > start_time."""
        start_time_value = values.get('start_time')
        if end_time_value is not None and start_time_value is not None:
            if not (end_time_value > start_time_value):
                raise ValueError('end_time must be greater than start_time (zero-length not allowed)')
        return end_time_value
    
    def get_video_source(self) -> str:
        """Get the video source path."""
        return self.video_path
    
    def has_cv_metadata(self) -> bool:
        """Check if CV metadata path is provided."""
        return self.cv_metadata_path is not None and self.cv_metadata_path.strip() != ""
    
    def get_alert_summary(self) -> str:
        """Get a summary string for the alert."""
        return f"{self.alert.type} - {self.alert.severity} - {self.alert.description}" 