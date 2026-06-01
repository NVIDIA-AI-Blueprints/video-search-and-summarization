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
Field Mapper Component

Maps fields from AlertRequestEntity to response format.
Handles field transformation and configuration-driven mapping.
"""

import logging
from typing import Dict, Any, Optional
from datetime import datetime

from ...request_entity.models.requests import AlertRequestEntity


class FieldMapper:
    """
    Maps request entity fields to response format.
    
    Provides configurable field mapping with support for aliases,
    transformations, and default value handling.
    """
    
    def __init__(self):
        """Initialize field mapper with configuration."""
        self.logger = logging.getLogger(self.__class__.__name__)
        self._load_field_mapping_config()
    
    def _load_field_mapping_config(self) -> None:
        """Load field mapping configuration from response defaults."""
        try:
            # Use fallback mapping
            self.field_mapping = self._get_default_field_mapping()
            self.default_values = self._get_default_values()
            
            self.logger.debug("Field mapping configuration loaded successfully")
            
        except Exception as e:
            self.logger.warning(f"Failed to load field mapping config: {e}")
            # Use fallback mapping
            self.field_mapping = self._get_default_field_mapping()
            self.default_values = self._get_default_values()
    
    def _get_default_field_mapping(self) -> Dict[str, str]:
        """Get default field mapping configuration."""
        return {
            'eventId': 'id',
            'alertType': 'alert.type', 
            'sensorId': 'sensor_id',
            'streamId': 'stream_id',
            'mediaFilePath': 'video_path'
        }
    
    def _get_default_values(self) -> Dict[str, Any]:
        """Get default values for response fields."""
        return {
            'version': '2.0.0',
            'streamId': None
        }
    
    def map_request_to_response(self, request_entity: AlertRequestEntity) -> Dict[str, Any]:
        """
        Map request entity fields to response format.
        
        Args:
            request_entity: Validated request entity
            
        Returns:
            Dictionary with mapped fields for response building
        """
        mapped_fields = {}
        
        # Map configured fields
        for response_field, request_path in self.field_mapping.items():
            value = self._extract_field_value(request_entity, request_path)
            if value is not None:
                mapped_fields[response_field] = value
        
        # Add timestamp (always current time for responses)
        mapped_fields['timestamp'] = datetime.utcnow().isoformat()
        
        # Apply default values for missing fields
        for field, default_value in self.default_values.items():
            if field not in mapped_fields:
                mapped_fields[field] = default_value
        
        self.logger.debug(f"Mapped {len(mapped_fields)} fields from request entity")
        return mapped_fields
    
    def _extract_field_value(self, entity: AlertRequestEntity, field_path: str) -> Any:
        """
        Extract field value from entity using dot notation path.
        
        Args:
            entity: Request entity object
            field_path: Dot-separated path to field (e.g., 'alert.type')
            
        Returns:
            Field value or None if not found
        """
        try:
            # Handle direct field access
            if '.' not in field_path:
                return getattr(entity, field_path, None)
            
            # Handle nested field access
            parts = field_path.split('.')
            current = entity
            
            for part in parts:
                if hasattr(current, part):
                    current = getattr(current, part)
                else:
                    return None
            
            return current
            
        except Exception as e:
            self.logger.warning(f"Failed to extract field '{field_path}': {e}")
            return None
    
    def add_custom_mapping(self, response_field: str, request_path: str) -> None:
        """
        Add a custom field mapping.
        
        Args:
            response_field: Target field name in response
            request_path: Source field path in request entity
        """
        self.field_mapping[response_field] = request_path
        self.logger.debug(f"Added custom mapping: {response_field} <- {request_path}")
    
    def get_mapping_info(self) -> Dict[str, Any]:
        """Get information about current field mappings."""
        return {
            'field_mappings': dict(self.field_mapping),
            'default_values': dict(self.default_values),
            'mapping_count': len(self.field_mapping)
        } 