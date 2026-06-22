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
Entity Management

Central package for all entity management functionality including both request and response processing.
Provides unified interface for request validation, building, and response creation.
"""

# Export request entity management
from .request_entity import (
    EntityBuilder,
    EntityValidator,
    AlertRequestEntity,
    ValidationError,
    EntityBuildError,
    InvalidPayloadError
)

# Export response entity management
from .response_entity import (
    ResponseBuilder,
    AlertResponseEntity
)

# Export shared components
from .shared import (
    ProcessingStatus,
    AlertSeverity,
    AlertStatus,
    EntityManagementError
)

__all__ = [
    # Request entity components
    'EntityBuilder',
    'EntityValidator',
    'AlertRequestEntity',
    'ValidationError',
    'EntityBuildError', 
    'InvalidPayloadError',
    
    # Response entity components
    'ResponseBuilder',
    'AlertResponseEntity',
    
    # Shared components
    'ProcessingStatus',
    'AlertSeverity',
    'AlertStatus',
    'EntityManagementError'
]


def get_version_info():
    """Get version and configuration information."""
    return {
        "version": __version__,
        "external_config_enabled": True,
        "config_location": "alert_request_defaults.yaml"
    } 