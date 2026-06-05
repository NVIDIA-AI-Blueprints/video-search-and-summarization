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
Schema Builder Component

Builds complete _schema_response structures for both success and error scenarios.
Provides consistent response format assembly with proper status handling.
"""

import logging
from typing import Dict, Any, List, Optional
from datetime import datetime


class SchemaBuilder:
    """
    Builds complete schema response structures.
    
    Assembles mapped fields, evaluations, and status information into
    the final _schema_response format expected by AlertResponseEntity.
    """
    
    def __init__(self):
        """Initialize schema builder with configuration."""
        self.logger = logging.getLogger(self.__class__.__name__)
        self._load_schema_config()
    
    def _load_schema_config(self) -> None:
        """Load schema building configuration from response defaults."""
        try:
            # Use fallback configuration
            self.schema_config = self._get_default_schema_config()
            self.response_format = self._get_default_response_format()
            
            self.logger.debug("Schema building configuration loaded successfully")
            
        except Exception as e:
            self.logger.warning(f"Failed to load schema building config: {e}")
            # Use fallback configuration
            self.schema_config = self._get_default_schema_config()
            self.response_format = self._get_default_response_format()
    
    def _get_default_schema_config(self) -> Dict[str, Any]:
        """Get default schema configuration."""
        return {
            'version': '2.0.0',
            'include_processing_metadata': True,
            'validate_required_fields': True
        }
    
    def _get_default_response_format(self) -> Dict[str, Any]:
        """Get default response format configuration."""
        return {
            'version': '2.0.0',
            'timestamp_format': 'iso',
            'include_empty_evaluations': True
        }
    
    def build_success_response(
        self,
        mapped_fields: Dict[str, Any],
        evaluations: List[Dict[str, Any]],
        vss_metadata: Dict[str, Any] = None
    ) -> Dict[str, Any]:
        """
        Build complete success response schema.
        
        Args:
            mapped_fields: Field mappings from request entity
            evaluations: Parsed VSS evaluation results
            vss_metadata: Additional VSS processing metadata
            
        Returns:
            Complete _schema_response dictionary for success case
        """
        schema_response = {}
        
        # Core identification and mapping
        schema_response.update(mapped_fields)
        
        # Ensure required fields have values
        schema_response = self._ensure_required_fields(schema_response)
        
        # Add evaluations
        schema_response['vlmEvaluation'] = evaluations
        
        # Add success status
        schema_response['status'] = self._build_success_status(vss_metadata)
        
        # Add processing metadata if enabled
        if self.schema_config.get('include_processing_metadata', True):
            schema_response = self._add_processing_metadata(schema_response, vss_metadata)
        
        self.logger.debug(
            f"Built success response schema with {len(evaluations)} evaluations",
            extra={
                'evaluation_count': len(evaluations),
                'has_metadata': bool(vss_metadata)
            }
        )
        
        return schema_response
    
    def build_error_response(
        self,
        mapped_fields: Dict[str, Any],
        error_status: Dict[str, Any],
        vss_metadata: Dict[str, Any] = None
    ) -> Dict[str, Any]:
        """
        Build complete error response schema.
        
        Args:
            mapped_fields: Field mappings from request entity
            error_status: Error status information from error classifier
            vss_metadata: Additional VSS processing metadata
            
        Returns:
            Complete _schema_response dictionary for error case
        """
        schema_response = {}
        
        # Core identification and mapping
        schema_response.update(mapped_fields)
        
        # Ensure required fields have values
        schema_response = self._ensure_required_fields(schema_response)
        
        # Empty evaluations for error case
        schema_response['vlmEvaluation'] = []
        
        # Add error status
        schema_response['status'] = error_status
        
        # Add processing metadata if enabled
        if self.schema_config.get('include_processing_metadata', True):
            schema_response = self._add_processing_metadata(schema_response, vss_metadata, is_error=True)
        
        self.logger.debug(
            f"Built error response schema with status: {error_status.get('code', 'UNKNOWN')}",
            extra={
                'error_code': error_status.get('code'),
                'retriable': error_status.get('retriable')
            }
        )
        
        return schema_response
    
    def _ensure_required_fields(self, schema_response: Dict[str, Any]) -> Dict[str, Any]:
        """
        Ensure all required fields are present with valid values.
        
        Args:
            schema_response: Partial response schema
            
        Returns:
            Response schema with all required fields
        """
        # Required fields with fallback values
        required_fields = {
            'eventId': 'unknown',
            'version': self.schema_config.get('version', '2.0.0'),
            'alertType': 'unknown',
            'sensorId': 'unknown',
            'timestamp': datetime.utcnow().isoformat(),
            'mediaFilePath': 'unknown'
        }
        
        # Add missing required fields
        for field, fallback in required_fields.items():
            if field not in schema_response or schema_response[field] is None:
                schema_response[field] = fallback
                self.logger.warning(f"Added fallback value for missing required field: {field}")
        
        return schema_response
    
    def _build_success_status(self, vss_metadata: Dict[str, Any] = None) -> Dict[str, Any]:
        """
        Build success status information.
        
        Args:
            vss_metadata: VSS processing metadata
            
        Returns:
            Success status dictionary
        """
        status = {
            'code': 'OK',
            'message': 'Evaluation completed successfully.',
            'retriable': False,
            'origin': 'VSS'
        }
        
        # Add processing details if available
        if vss_metadata:
            details = {}
            
            # Add timing information if available
            if 'processing_time_ms' in vss_metadata:
                details['processing_time_ms'] = vss_metadata['processing_time_ms']
            
            # Add VSS version if available
            if 'vss_version' in vss_metadata:
                details['vss_version'] = vss_metadata['vss_version']
            
            # Add evaluation count
            if 'evaluation_count' in vss_metadata:
                details['evaluation_count'] = vss_metadata['evaluation_count']
            
            if details:
                status['details'] = details
        
        return status
    
    def _add_processing_metadata(
        self,
        schema_response: Dict[str, Any],
        vss_metadata: Dict[str, Any] = None,
        is_error: bool = False
    ) -> Dict[str, Any]:
        """
        Add processing metadata to response.
        
        Args:
            schema_response: Response schema to enhance
            vss_metadata: VSS processing metadata
            is_error: Whether this is an error response
            
        Returns:
            Enhanced response schema with metadata
        """
        if not vss_metadata:
            return schema_response
        
        # Add processing timestamp
        schema_response['processedAt'] = datetime.utcnow().isoformat()
        
        # Add relevant metadata fields
        metadata_fields = [
            'processing_time_ms',
            'vss_version',
            'model_version',
            'confidence_threshold',
            'evaluation_count'
        ]
        
        for field in metadata_fields:
            if field in vss_metadata:
                # Convert to camelCase for response format
                camel_field = self._to_camel_case(field)
                schema_response[camel_field] = vss_metadata[field]
        
        return schema_response
    
    def _to_camel_case(self, snake_str: str) -> str:
        """Convert snake_case to camelCase."""
        components = snake_str.split('_')
        return components[0] + ''.join(word.capitalize() for word in components[1:])
    
    def validate_schema_response(self, schema_response: Dict[str, Any]) -> bool:
        """
        Validate that schema response has all required fields.
        
        Args:
            schema_response: Response schema to validate
            
        Returns:
            True if valid, False otherwise
        """
        if not self.schema_config.get('validate_required_fields', True):
            return True
        
        required_fields = ['eventId', 'version', 'alertType', 'sensorId', 'timestamp', 'mediaFilePath', 'vlmEvaluation', 'status']
        
        for field in required_fields:
            if field not in schema_response:
                self.logger.error(f"Schema response missing required field: {field}")
                return False
        
        # Validate status structure
        status = schema_response.get('status', {})
        required_status_fields = ['code', 'message']
        
        for field in required_status_fields:
            if field not in status:
                self.logger.error(f"Schema response status missing required field: {field}")
                return False
        
        return True
    
    def get_schema_info(self) -> Dict[str, Any]:
        """Get schema building configuration info."""
        return {
            'schema_config': dict(self.schema_config),
            'response_format': dict(self.response_format)
        } 