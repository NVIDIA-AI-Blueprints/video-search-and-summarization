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
Response Entity Management

Handles all response-related processing including building, formatting, and validation.
Provides clean interface for creating and managing AlertResponseEntity objects.
"""

from .response_builder import ResponseBuilder

# Import models for external use
from .models.responses import (
    AlertResponseEntity, 
    AlertInfo, 
    EventInfo, 
    ResultInfo, 
    ResultDebug, 
    MetaLabel
)

__all__ = [
    # Core classes
    'ResponseBuilder',
    
    # Models
    'AlertResponseEntity',
    'AlertInfo', 
    'EventInfo', 
    'ResultInfo', 
    'ResultDebug', 
    'MetaLabel'
] 