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
Request Entity Builder

Provides convenient factory methods for building validated alert entities from various sources.
Acts as a higher-level interface over the EntityValidator for common use cases.
"""

import logging
from typing import Dict, Any, List, Optional

from .validator import EntityValidator
from .models import AlertRequestEntity
from .exceptions import EntityBuildError


class EntityBuilder:
    """
    High-level alert entity builder with convenient factory methods.
    
    Provides simplified interfaces for building alert entities from different sources
    while maintaining the flexibility of the underlying EntityValidator.
    """
    
    def __init__(self):
        """Initialize alert entity builder."""
        self.logger = logging.getLogger(self.__class__.__name__)
        self.validator = EntityValidator()
    
    def build_from_messages(self, messages: List[Dict[str, Any]]) -> List[AlertRequestEntity]:
        """
        Build alert entities from a list of raw JSON messages.
        
        This is the primary method for alert entity creation.
        
        Args:
            messages: List of raw JSON alert message dictionaries
            
        Returns:
            List of validated alert request entities
            
        Raises:
            EntityBuildError: When entity building fails critically
        """
        try:
            self.logger.info(f"Building entities from {len(messages)} alert messages")
            entities = self.validator.validate_and_build(messages)
            
            self.logger.info(
                f"Successfully built {len(entities)} alert entities from {len(messages)} messages",
                extra={
                    "success_rate": len(entities) / len(messages) if messages else 0.0,
                    "validation_stats": self.validator.get_validation_stats()
                }
            )
            
            return entities
            
        except Exception as e:
            self.logger.error(f"Failed to build alert entities: {e}", exc_info=True)
            raise EntityBuildError(f"Entity building failed: {str(e)}") from e
    
    def build_from_single_message(self, message: Dict[str, Any]) -> Optional[AlertRequestEntity]:
        """
        Build a single alert entity from a JSON message.
        
        Args:
            message: Raw JSON alert message dictionary
            
        Returns:
            Validated alert entity or None if validation fails
            
        Raises:
            EntityBuildError: When entity building fails critically
        """
        try:
            entities = self.build_from_messages([message])
            return entities[0] if entities else None
            
        except EntityBuildError:
            # Re-raise entity build errors
            raise
        except Exception as e:
            self.logger.error(f"Failed to build single alert entity: {e}", exc_info=True)
            raise EntityBuildError(f"Single entity build failed: {str(e)}") from e
    
    def build_from_json_strings(self, json_strings: List[str]) -> List[AlertRequestEntity]:
        """
        Build alert entities from JSON string representations.
        
        Args:
            json_strings: List of JSON strings representing alert messages
            
        Returns:
            List of validated alert entities
            
        Raises:
            EntityBuildError: When JSON parsing or entity building fails
        """
        import json
        
        try:
            # Parse JSON strings to dictionaries
            messages = []
            for idx, json_str in enumerate(json_strings):
                try:
                    message = json.loads(json_str)
                    messages.append(message)
                except json.JSONDecodeError as e:
                    self.logger.warning(f"Failed to parse JSON string {idx}: {e}")
                    raise EntityBuildError(f"Invalid JSON at index {idx}: {str(e)}") from e
            
            return self.build_from_messages(messages)
            
        except EntityBuildError:
            # Re-raise entity build errors
            raise
        except Exception as e:
            self.logger.error(f"Failed to build entities from JSON strings: {e}", exc_info=True)
            raise EntityBuildError(f"JSON string processing failed: {str(e)}") from e
    
    def get_builder_stats(self) -> Dict[str, Any]:
        """
        Get builder performance statistics.
        
        Returns:
            Dictionary containing validation and building statistics
        """
        return {
            "validator_stats": self.validator.get_validation_stats(),
            "builder_version": "1.0.0"
        }
    
    def reset_stats(self) -> None:
        """Reset all builder statistics."""
        self.validator.reset_stats()
        self.logger.info("Builder statistics reset") 