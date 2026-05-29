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
Error Classifier Component

Classifies VSS errors and maps them to appropriate status codes and messages.
Provides intelligent error handling with retry logic classification.
"""

import logging
from typing import Dict, Any, Optional


class ErrorClassifier:
    """
    Classifies VSS errors and generates appropriate status information.
    
    Maps error types to status codes, provides user-friendly messages,
    and determines retry behavior based on error classification.
    """
    
    def __init__(self):
        """Initialize error classifier with configuration."""
        self.logger = logging.getLogger(self.__class__.__name__)
        # Use fallback/default error classification logic only
        self.error_codes = self._get_default_error_codes()
        self.error_messages = self._get_default_error_messages()
        self.timeout_classification = self._get_default_timeout_classification()
    
    def _load_error_classification_config(self) -> None:
        """Load error classification configuration from response defaults."""
        # This method is no longer used as config loading is removed.
        # Keeping it for now to avoid breaking existing calls, but it will be removed.
        pass
    
    def _get_default_error_codes(self) -> Dict[str, str]:
        """Get default error code mappings."""
        return {
            'connection_timeout': 'CONNECTION_TIMEOUT',
            'processing_timeout': 'PROCESSING_TIMEOUT',
            'authentication_failed': 'AUTHENTICATION_ERROR',
            'service_unavailable': 'SERVICE_UNAVAILABLE',
            'invalid_request': 'INVALID_REQUEST',
            'internal_error': 'INTERNAL_ERROR',
            'unknown': 'UNKNOWN_ERROR'
        }
    
    def _get_default_error_messages(self) -> Dict[str, str]:
        """Get default user-friendly error messages."""
        return {
            'CONNECTION_TIMEOUT': 'VSS service connection timeout - please retry',
            'PROCESSING_TIMEOUT': 'VSS processing timeout - video may be too long',
            'AUTHENTICATION_ERROR': 'VSS authentication failed - check credentials',
            'SERVICE_UNAVAILABLE': 'VSS service temporarily unavailable',
            'INVALID_REQUEST': 'Invalid request format for VSS processing',
            'INTERNAL_ERROR': 'Internal VSS processing error',
            'UNKNOWN_ERROR': 'Unknown error occurred during VSS processing'
        }
    
    def _get_default_timeout_classification(self) -> Dict[str, Any]:
        """Get default timeout classification rules."""
        return {
            'connection_timeout_keywords': ['timeout', 'connection', 'connect'],
            'processing_timeout_keywords': ['processing', 'execution', 'runtime'],
            'max_retry_attempts': 3,
            'retry_delay_seconds': 30
        }
    
    def classify_vss_error(self, error_message: str, error_type: str = 'UNKNOWN') -> Dict[str, Any]:
        """
        Classify VSS error and generate status information.
        
        Args:
            error_message: Raw error message from VSS
            error_type: Error type classification from VSS
            
        Returns:
            Dictionary with status information including code, message, and retry info
        """
        # Determine error category
        error_category = self._categorize_error(error_message, error_type)
        
        # Get status code
        status_code = self.error_codes.get(error_category, self.error_codes['unknown'])
        
        # Get user-friendly message
        user_message = self.error_messages.get(status_code, error_message)
        
        # Determine retry behavior
        retry_info = self._determine_retry_behavior(error_category, error_message)
        
        status = {
            'code': status_code,
            'message': user_message,
            'retriable': retry_info['retriable'],
            'origin': 'VSS',
            'details': {
                'original_error': error_message,
                'error_type': error_type,
                'error_category': error_category,
                'retry_attempts_remaining': retry_info.get('retry_attempts', 0),
                'retry_delay_seconds': retry_info.get('retry_delay', 0)
            }
        }
        
        self.logger.debug(
            f"Classified error: {error_category} -> {status_code}",
            extra={
                'error_category': error_category,
                'status_code': status_code,
                'retriable': retry_info['retriable']
            }
        )
        
        return status
    
    def _categorize_error(self, error_message: str, error_type: str) -> str:
        """
        Categorize error based on message content and type.
        
        Args:
            error_message: Error message text
            error_type: Error type from VSS
            
        Returns:
            Error category string
        """
        error_lower = error_message.lower()
        type_lower = error_type.lower()
        
        # Check for connection timeouts
        connection_keywords = self.timeout_classification.get('connection_timeout_keywords', [])
        if any(keyword in error_lower for keyword in connection_keywords):
            if 'timeout' in error_lower:
                return 'connection_timeout'
        
        # Check for processing timeouts
        processing_keywords = self.timeout_classification.get('processing_timeout_keywords', [])
        if any(keyword in error_lower for keyword in processing_keywords):
            if 'timeout' in error_lower:
                return 'processing_timeout'
        
        # Check for authentication errors
        if any(keyword in error_lower for keyword in ['auth', 'credential', 'permission', 'unauthorized']):
            return 'authentication_failed'
        
        # Check for service availability
        if any(keyword in error_lower for keyword in ['unavailable', 'down', 'maintenance', '503', '502']):
            return 'service_unavailable'
        
        # Check for invalid request
        if any(keyword in error_lower for keyword in ['invalid', 'malformed', 'bad request', '400']):
            return 'invalid_request'
        
        # Check for internal errors
        if any(keyword in error_lower for keyword in ['internal', 'server error', '500']):
            return 'internal_error'
        
        # Default category
        return 'unknown'
    
    def _determine_retry_behavior(self, error_category: str, error_message: str) -> Dict[str, Any]:
        """
        Determine retry behavior based on error category.
        
        Args:
            error_category: Categorized error type
            error_message: Original error message
            
        Returns:
            Dictionary with retry information
        """
        # Retriable error categories
        retriable_categories = {
            'connection_timeout': True,
            'processing_timeout': False,  # Usually video is too long
            'service_unavailable': True,
            'authentication_failed': False,  # Need to fix credentials
            'invalid_request': False,  # Need to fix request
            'internal_error': True,  # Might be temporary
            'unknown': True  # Conservative approach
        }
        
        retriable = retriable_categories.get(error_category, False)
        
        retry_info = {
            'retriable': retriable
        }
        
        # Add retry details for retriable errors
        if retriable:
            retry_info.update({
                'retry_attempts': self.timeout_classification.get('max_retry_attempts', 3),
                'retry_delay': self.timeout_classification.get('retry_delay_seconds', 30)
            })
        
        return retry_info
    
    def is_timeout_error(self, error_message: str) -> bool:
        """Check if error is timeout-related."""
        return 'timeout' in error_message.lower()
    
    def get_classification_stats(self) -> Dict[str, Any]:
        """Get error classification configuration info."""
        return {
            'error_codes_count': len(self.error_codes),
            'error_messages_count': len(self.error_messages),
            'timeout_classification_rules': dict(self.timeout_classification)
        } 