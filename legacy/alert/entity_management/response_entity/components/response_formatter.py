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
Response Formatter Component

Provides final response formatting utilities including JSON serialization,
field validation, and response optimization.
"""

import logging
from typing import Dict, Any, List, Optional
from datetime import datetime


class ResponseFormatter:
    """
    Provides response formatting and optimization utilities.
    
    Handles final response formatting including field ordering,
    JSON serialization optimization, and response validation.
    """
    
    def __init__(self):
        """Initialize response formatter with configuration."""
        self.logger = logging.getLogger(self.__class__.__name__)
        self._load_formatting_config()
    
    def _load_formatting_config(self) -> None:
        """Load response formatting configuration from response defaults."""
        # Use fallback configuration only
        self.formatting_config = self._get_default_formatting_config()
        self.validation_config = self._get_default_validation_config()
    
    def _get_default_formatting_config(self) -> Dict[str, Any]:
        """Get default formatting configuration."""
        return {
            'field_order': [
                'eventId', 'version', 'alertType', 'sensorId', 'streamId',
                'timestamp', 'mediaFilePath', 'vlmEvaluation', 'status'
            ],
            'sort_evaluations_by_confidence': True,
            'round_confidence_decimals': 3,
            'remove_null_fields': True,
            'optimize_for_json': True
        }
    
    def _get_default_validation_config(self) -> Dict[str, Any]:
        """Get default validation configuration."""
        return {
            'validate_field_types': True,
            'validate_confidence_range': True,
            'validate_required_fields': True,
            'strict_validation': False
        }
    
    def format_response(self, response_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Format response data for final output.
        
        Args:
            response_data: Raw response data to format
            
        Returns:
            Formatted response data
        """
        formatted_response = dict(response_data)
        
        # Apply formatting optimizations
        if self.formatting_config.get('sort_evaluations_by_confidence', True):
            formatted_response = self._sort_evaluations_by_confidence(formatted_response)
        
        if self.formatting_config.get('round_confidence_decimals') is not None:
            formatted_response = self._round_confidence_scores(
                formatted_response,
                self.formatting_config['round_confidence_decimals']
            )
        
        if self.formatting_config.get('remove_null_fields', True):
            formatted_response = self._remove_null_fields(formatted_response)
        
        if self.formatting_config.get('optimize_for_json', True):
            formatted_response = self._optimize_for_json_serialization(formatted_response)
        
        # Apply field ordering if specified
        field_order = self.formatting_config.get('field_order')
        if field_order:
            formatted_response = self._order_response_fields(formatted_response, field_order)
        
        self.logger.debug("Response formatting completed")
        return formatted_response
    
    def _sort_evaluations_by_confidence(self, response_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Sort evaluations by confidence score (highest first).
        
        Args:
            response_data: Response data with evaluations
            
        Returns:
            Response data with sorted evaluations
        """
        vlm_evaluation = response_data.get('vlmEvaluation', [])
        
        if isinstance(vlm_evaluation, list) and vlm_evaluation:
            try:
                # Sort by confidence descending
                sorted_evaluations = sorted(
                    vlm_evaluation,
                    key=lambda x: x.get('confidence', 0.0) if isinstance(x, dict) else 0.0,
                    reverse=True
                )
                response_data['vlmEvaluation'] = sorted_evaluations
                self.logger.debug(f"Sorted {len(sorted_evaluations)} evaluations by confidence")
                
            except Exception as e:
                self.logger.warning(f"Failed to sort evaluations by confidence: {e}")
        
        return response_data
    
    def _round_confidence_scores(self, response_data: Dict[str, Any], decimal_places: int) -> Dict[str, Any]:
        """
        Round confidence scores to specified decimal places.
        
        Args:
            response_data: Response data with evaluations
            decimal_places: Number of decimal places to round to
            
        Returns:
            Response data with rounded confidence scores
        """
        vlm_evaluation = response_data.get('vlmEvaluation', [])
        
        if isinstance(vlm_evaluation, list):
            for evaluation in vlm_evaluation:
                if isinstance(evaluation, dict) and 'confidence' in evaluation:
                    try:
                        evaluation['confidence'] = round(evaluation['confidence'], decimal_places)
                    except (TypeError, ValueError):
                        self.logger.warning(f"Failed to round confidence score: {evaluation.get('confidence')}")
        
        return response_data
    
    def _remove_null_fields(self, response_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Remove fields with null values from response.
        
        Args:
            response_data: Response data to clean
            
        Returns:
            Response data without null fields
        """
        def clean_dict(d):
            if isinstance(d, dict):
                return {k: clean_dict(v) for k, v in d.items() if v is not None}
            elif isinstance(d, list):
                return [clean_dict(item) for item in d if item is not None]
            return d
        
        return clean_dict(response_data)
    
    def _optimize_for_json_serialization(self, response_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Optimize response data for JSON serialization.
        
        Args:
            response_data: Response data to optimize
            
        Returns:
            Optimized response data
        """
        def optimize_value(value):
            # Convert datetime objects to ISO strings
            if isinstance(value, datetime):
                return value.isoformat()
            
            # Convert sets to lists
            elif isinstance(value, set):
                return list(value)
            
            # Recursively optimize dictionaries
            elif isinstance(value, dict):
                return {k: optimize_value(v) for k, v in value.items()}
            
            # Recursively optimize lists
            elif isinstance(value, list):
                return [optimize_value(item) for item in value]
            
            # Return value as-is for JSON-compatible types
            return value
        
        return optimize_value(response_data)
    
    def _order_response_fields(self, response_data: Dict[str, Any], field_order: List[str]) -> Dict[str, Any]:
        """
        Order response fields according to specified order.
        
        Args:
            response_data: Response data to reorder
            field_order: Desired field order
            
        Returns:
            Response data with ordered fields
        """
        ordered_response = {}
        
        # Add fields in specified order
        for field in field_order:
            if field in response_data:
                ordered_response[field] = response_data[field]
        
        # Add any remaining fields not in the order list
        for field, value in response_data.items():
            if field not in ordered_response:
                ordered_response[field] = value
        
        return ordered_response
    
    def validate_response_format(self, response_data: Dict[str, Any]) -> bool:
        """
        Validate response format according to configuration.
        
        Args:
            response_data: Response data to validate
            
        Returns:
            True if valid, False otherwise
        """
        if not self.validation_config.get('validate_required_fields', True):
            return True
        
        validation_errors = []
        
        # Check required fields
        required_fields = ['eventId', 'version', 'alertType', 'sensorId', 'timestamp', 'mediaFilePath', 'vlmEvaluation', 'status']
        
        for field in required_fields:
            if field not in response_data:
                validation_errors.append(f"Missing required field: {field}")
        
        # Validate field types if enabled
        if self.validation_config.get('validate_field_types', True):
            validation_errors.extend(self._validate_field_types(response_data))
        
        # Validate confidence ranges if enabled
        if self.validation_config.get('validate_confidence_range', True):
            validation_errors.extend(self._validate_confidence_ranges(response_data))
        
        # Log validation results
        if validation_errors:
            if self.validation_config.get('strict_validation', False):
                self.logger.error(f"Response validation failed: {validation_errors}")
                return False
            else:
                self.logger.warning(f"Response validation warnings: {validation_errors}")
        
        return True
    
    def _validate_field_types(self, response_data: Dict[str, Any]) -> List[str]:
        """Validate field types in response data."""
        errors = []
        
        # Expected field types
        field_types = {
            'eventId': str,
            'version': str,
            'alertType': str,
            'sensorId': str,
            'timestamp': str,
            'mediaFilePath': str,
            'vlmEvaluation': list,
            'status': dict
        }
        
        for field, expected_type in field_types.items():
            if field in response_data:
                if not isinstance(response_data[field], expected_type):
                    errors.append(f"Field '{field}' should be {expected_type.__name__}, got {type(response_data[field]).__name__}")
        
        return errors
    
    def _validate_confidence_ranges(self, response_data: Dict[str, Any]) -> List[str]:
        """Validate confidence score ranges in evaluations."""
        errors = []
        
        vlm_evaluation = response_data.get('vlmEvaluation', [])
        if isinstance(vlm_evaluation, list):
            for idx, evaluation in enumerate(vlm_evaluation):
                if isinstance(evaluation, dict) and 'confidence' in evaluation:
                    confidence = evaluation['confidence']
                    if not isinstance(confidence, (int, float)) or not (0.0 <= confidence <= 1.0):
                        errors.append(f"Evaluation {idx} confidence {confidence} outside valid range [0.0, 1.0]")
        
        return errors
    
    def get_formatting_stats(self) -> Dict[str, Any]:
        """Get formatting configuration statistics."""
        return {
            'formatting_config': dict(self.formatting_config),
            'validation_config': dict(self.validation_config)
        } 