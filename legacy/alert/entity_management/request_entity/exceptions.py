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
Request Entity Exceptions

Exceptions specific to request entity processing including validation and building errors.
All exceptions inherit from the shared EntityManagementError for consistent error handling.
"""

from typing import Dict, Any, List, Optional
from ..shared.exceptions import EntityManagementError


class ValidationError(EntityManagementError):
    """
    Exception raised when request validation fails.
    
    Provides detailed information about validation failures including
    field-specific errors and validation context.
    """
    
    def __init__(
        self, 
        message: str, 
        validation_errors: List[Dict[str, Any]] = None,
        field_errors: Dict[str, str] = None,
        payload_id: str = None,
        details: Dict[str, Any] = None
    ):
        """
        Initialize ValidationError.
        
        Args:
            message: Human-readable error message
            validation_errors: List of detailed validation error objects
            field_errors: Dictionary mapping field names to error messages
            payload_id: ID of the payload that failed validation
            details: Additional error context
        """
        super().__init__(message, details or {})
        
        self.validation_errors = validation_errors or []
        self.field_errors = field_errors or {}
        self.payload_id = payload_id
        self.error_count = len(self.validation_errors)
        
        # Add validation-specific details
        self.details.update({
            'validation_errors': self.validation_errors,
            'field_errors': self.field_errors,
            'payload_id': self.payload_id,
            'error_count': self.error_count
        })


class EntityBuildError(EntityManagementError):
    """
    Exception raised when entity building fails.
    
    This covers failures in the entity construction process beyond validation,
    such as configuration errors or processing failures.
    """
    
    def __init__(self, message: str, build_context: Dict[str, Any] = None, details: Dict[str, Any] = None):
        """
        Initialize EntityBuildError.
        
        Args:
            message: Human-readable error message
            build_context: Context information about the build process
            details: Additional error context
        """
        super().__init__(message, details or {})
        
        self.build_context = build_context or {}
        
        # Add build-specific details
        self.details.update({
            'build_context': self.build_context,
            'error_type': 'entity_build_error'
        })


class InvalidPayloadError(EntityManagementError):
    """
    Exception raised when the input payload structure is invalid.
    
    This covers cases where the payload doesn't meet basic structural requirements
    before validation can even begin (e.g., not a dictionary, invalid JSON).
    """
    
    def __init__(
        self, 
        message: str, 
        payload_type: str = None,
        expected_structure: str = None,
        details: Dict[str, Any] = None
    ):
        """
        Initialize InvalidPayloadError.
        
        Args:
            message: Human-readable error message
            payload_type: The actual type/structure of the payload
            expected_structure: Description of expected payload structure
            details: Additional error context
        """
        super().__init__(message, details or {})
        
        self.payload_type = payload_type
        self.expected_structure = expected_structure
        
        # Add payload-specific details
        self.details.update({
            'payload_type': self.payload_type,
            'expected_structure': self.expected_structure,
            'error_type': 'invalid_payload_error'
        }) 