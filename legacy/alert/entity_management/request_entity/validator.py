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
Request Entity Validator

Provides comprehensive entity validation using Pydantic models.
Handles complex nested validations, prompt schema validation, and more.
"""

import json
import logging
from typing import Dict, Any, List, Optional
from pydantic import ValidationError as PydanticValidationError


import uuid
from .models import AlertRequestEntity, VSSParams
from .exceptions import ValidationError, InvalidPayloadError


class EntityValidator:
    """
    Comprehensive entity validator using Pydantic models.
    
    Validates incoming alert requests with automatic type conversion,
    field validation, and comprehensive error reporting.
    """
    
    def __init__(self):
        """Initialize entity validator."""
        self.logger = logging.getLogger(self.__class__.__name__)
        
        # Performance metrics
        self._validation_stats = {
            'total_requests': 0,
            'successful_validations': 0,
            'validation_errors': 0
        }
    
    def validate_and_build(self, raw_messages: List[Dict[str, Any]]) -> List[AlertRequestEntity]:
        """
        Validate and build entities from raw Alert request messages.
        
        Args:
            raw_messages: List of raw JSON alert message dictionaries
            
        Returns:
            List of validated alert request entities with all defaults applied
            
        Raises:
            ValidationError: When validation fails for critical messages
        """
        if not raw_messages:
            self.logger.warning("No messages provided for validation")
            return []
        
        validated_entities = []
        validation_errors = []
        
        for idx, message in enumerate(raw_messages):
            try:
                self._validation_stats['total_requests'] += 1
                
                # Validate and build entity
                entity = self._validate_single_message(message, message_index=idx)
                if entity:
                    validated_entities.append(entity)
                    self._validation_stats['successful_validations'] += 1
                    
            except ValidationError as e:
                self._validation_stats['validation_errors'] += 1
                validation_errors.append(e)
                self.logger.warning(
                    f"Validation failed for message {idx}",
                    extra={
                        "message_index": idx,
                        "error_count": e.error_count,
                        "payload_id": e.payload_id
                    }
                )
            except Exception as e:
                self._validation_stats['validation_errors'] += 1
                error = ValidationError(
                    f"Unexpected validation error for message {idx}: {str(e)}",
                    payload_id=self._extract_id_for_logging(message)
                )
                validation_errors.append(error)
                self.logger.error(
                    f"Unexpected error validating message {idx}",
                    extra={"error": str(e), "error_type": type(e).__name__},
                    exc_info=True
                )
        
        # Log validation summary
        self.logger.info(
            "Batch validation completed",
            extra={
                "total_messages": len(raw_messages),
                "validated_entities": len(validated_entities),
                "validation_errors": len(validation_errors),
                "success_rate": len(validated_entities) / len(raw_messages) if raw_messages else 0.0
            }
        )
        
        return validated_entities
    
    def _validate_single_message(self, message: Dict[str, Any], message_index: int = 0) -> Optional[AlertRequestEntity]:
        """
        Validate a single alert message using Pydantic.
        
        Args:
            message: Raw alert message dictionary
            message_index: Index of message in batch (for logging)
            
        Returns:
            Validated alert request entity or None if validation fails
            
        Raises:
            ValidationError: When validation fails
        """
        # Handle both JSON strings and dict objects
        if isinstance(message, str):
            try:
                # Parse JSON string to dict
                message = json.loads(message)
                self.logger.debug(f"Parsed JSON string to dict for message {message_index}")
            except json.JSONDecodeError as e:
                raise InvalidPayloadError(
                    f"Message {message_index} is not valid JSON: {str(e)}",
                    payload_type="invalid_json_string",
                    expected_structure="Valid JSON object"
                )
        elif not isinstance(message, dict):
            raise InvalidPayloadError(
                f"Message {message_index} is not a dictionary or JSON string",
                payload_type=type(message).__name__,
                expected_structure="JSON object or JSON string"
            )
        
        message_id = self._extract_id_for_logging(message)
        
        try:
            # Use Pydantic validation with automatic defaults
            # Inject config defaults for vss_params and nested vlm_params
            vss_params_input = message.get('vss_params', message.get('vssParams', {}))
            vss_params = VSSParams.create_with_defaults(**vss_params_input)
            # Build the entity, replacing vssParams with the merged version
            message_with_defaults = dict(message)
            message_with_defaults['vss_params'] = vss_params.model_dump(exclude_none=False)
            message_with_defaults['vssParams'] = vss_params.model_dump(by_alias=True, exclude_none=False)

            entity = AlertRequestEntity.model_validate(message_with_defaults)

            self.logger.debug(
                "Validation successful",
                extra={
                    "message_id": message_id,
                    "defaults_applied": self._count_defaults_applied(entity, message)
                }
            )
            
            return entity
            
        except PydanticValidationError as e:
            # Log detailed validation errors
            error_details = []
            for error in e.errors():
                field_path = '.'.join(str(loc) for loc in error['loc'])
                error_details.append(f"{field_path}: {error['msg']} (got: {error.get('input', 'N/A')})")
            
            # Format detailed error message
            error_summary = f"Validation failed for message {message_index} (ID: {message_id})"
            error_details_str = "; ".join(error_details)
            full_message = f"{error_summary} - Details: {error_details_str}"
            
            self.logger.warning(
                full_message,
                extra={
                    "message_id": message_id,
                    "message_index": message_index,
                    "validation_errors": error_details,
                    "error_count": len(error_details)
                }
            )
            
            # Convert Pydantic errors to our custom validation errors
            raise self._convert_pydantic_error(e, message_id, message_index)
    
    def _convert_pydantic_error(
        self, 
        pydantic_error: PydanticValidationError, 
        message_id: str, 
        message_index: int
    ) -> ValidationError:
        """
        Convert Pydantic validation error to custom ValidationError.
        
        Args:
            pydantic_error: Original Pydantic error
            message_id: Message ID for context
            message_index: Message index in batch
            
        Returns:
            Custom ValidationError with detailed information
        """
        validation_errors = []
        field_errors = {}
        
        for error in pydantic_error.errors():
            field_path = '.'.join(str(loc) for loc in error['loc'])
            error_msg = error['msg']
            error_type = error['type']
            
            validation_errors.append({
                'field': field_path,
                'message': error_msg,
                'type': error_type,
                'input': error.get('input', 'N/A')
            })
            
            field_errors[field_path] = error_msg
        
        return ValidationError(
            f"Validation failed for message {message_index} (ID: {message_id})",
            validation_errors=validation_errors,
            field_errors=field_errors,
            payload_id=message_id
        )
    
    def _extract_id_for_logging(self, message: Dict[str, Any]) -> str:
        """Extract ID from message for logging purposes."""
        return message.get('id', message.get('eventId', f'unknown_{hash(str(message)) % 10000}'))
    
    def _count_defaults_applied(self, entity: AlertRequestEntity, original_message: Dict[str, Any]) -> int:
        """Count how many default values were applied during validation."""
        defaults_count = 0
        
        # Check if vss_params was auto-created
        if 'vss_params' not in original_message and entity.vss_params:
            defaults_count += 1
        # No need to check for top-level vlmParams anymore
        
        # Check optional fields that got config defaults
        # Only count fields that: 1) weren't in input, 2) are present in entity, 3) got config default
        optional_field_mappings = {
            'confidence': 'confidence',
            'cv_metadata_path': 'cv_metadata_path',
            'meta_labels': 'meta_labels'
        }
        
        for input_field, entity_field in optional_field_mappings.items():
            if input_field not in original_message:
                # Field was missing from input
                entity_dict = entity.model_dump(exclude_none=True)
                if entity_field in entity_dict:
                    # Field is present in entity, so it got a config default
                    defaults_count += 1
        
        return defaults_count
    
    def get_validation_stats(self) -> Dict[str, Any]:
        """Get validation performance statistics."""
        stats = dict(self._validation_stats)
        if stats['total_requests'] > 0:
            success_rate = stats['successful_validations'] / stats['total_requests']
            error_rate = stats['validation_errors'] / stats['total_requests']
        else:
            success_rate = 0.0
            error_rate = 0.0
        
        return {
            **stats,
            'success_rate': success_rate,
            'error_rate': error_rate
        }
    
    def reset_stats(self) -> None:
        """Reset validation statistics."""
        self._validation_stats = {
            'total_requests': 0,
            'successful_validations': 0,
            'validation_errors': 0
        } 