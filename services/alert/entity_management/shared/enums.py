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
Shared Enums

Common enumeration types used across request and response entity processing.
These enums provide standardized values for status tracking and classification.
"""

from enum import Enum


class ProcessingStatus(Enum):
    """Status of alert processing."""
    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class AlertSeverity(Enum):
    """Severity levels for alerts."""
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class AlertStatus(Enum):
    """Status of alert lifecycle."""
    ACTIVE = "ACTIVE"
    RESOLVED = "RESOLVED"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    SUPPRESSED = "SUPPRESSED"
    REVIEW_PENDING = "REVIEW_PENDING"
    REVIEWED = "REVIEWED"
    REVIEW_FAILED = "REVIEW_FAILED"


class ReviewResultStatus(Enum):
    """Result status emitted in the response 'result' envelope."""
    SUCCESS = "SUCCESS"
    FAILURE = "FAILURE"


class ResponseFormat(Enum):
    """
    VLM response format options.
    
    Defines supported output formats for VLM responses.
    """
    TEXT = "text"
    JSON = "json_object"
    STRUCTURED = "json_schema"
    
    def __str__(self) -> str:
        """Return string value for serialization."""
        return self.value 