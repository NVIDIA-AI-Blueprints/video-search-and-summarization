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
Entity Management Parameter Models

Defines Pydantic models for VSS and VLM parameters with external configuration support.
Provides validated parameter objects for alert processing.
"""

import logging
import copy
from typing import Dict, Any, Optional
from pydantic import BaseModel, Field
from pydantic.config import ConfigDict

# ResponseFormat enum removed - using flexible Dict[str, Any] for response_format

# Global configuration cache
_loaded_defaults = None

logger = logging.getLogger(__name__)


def _get_defaults() -> Dict[str, Any]:
    """
    Get defaults from external configuration.
    
    Raises:
        RuntimeError: If external configuration is not available
        ValueError: If required configuration sections are missing
    """
    global _loaded_defaults
    
    if _loaded_defaults is None:
        try:
            # Load external configuration using defaults_loader
            from ...config.defaults_loader import AlertsDefaultsConfigLoader
            
            loader = AlertsDefaultsConfigLoader()
            config = loader.load_defaults()
            
            # Validate that all required sections are present
            required_sections = ['vlm_params', 'request_defaults']
            missing_sections = []
            
            for section in required_sections:
                section_data = getattr(config, section, None)
                if not section_data:
                    missing_sections.append(section)
            
            if missing_sections:
                raise ValueError(f"Configuration missing required sections: {missing_sections}")
            
            _loaded_defaults = {
                'vlm_params': config.vlm_params,
                'request_defaults': config.request_defaults
            }
            
            logger.info("External configuration loaded successfully for parameters")
            
        except ImportError as e:
            raise RuntimeError(f"Failed to import configuration module: {e}")
        except Exception as e:
            raise RuntimeError(f"Failed to load external configuration for parameters: {e}")
    
    return _loaded_defaults


def deep_merge(a, b):
    for k, v in b.items():
        if k in a and isinstance(a[k], dict) and isinstance(v, dict):
            deep_merge(a[k], v)
        else:
            a[k] = v
    return a

class MetaLabel(BaseModel):
    """
    Metadata label for additional request context.
    
    Provides flexible key-value labeling with validation for additional request metadata.
    """
    key: str = Field(..., min_length=1, max_length=100, description="Label key")
    value: str = Field(..., min_length=1, max_length=500, description="Label value")
    
    class Config:
        """Pydantic configuration for optimal performance."""
        validate_assignment = True


class VLMParams(BaseModel):
    """
    Vision Language Model processing parameters (nested inside vssParams).
    """
    prompt: Optional[str] = Field(None, description="VLM processing prompt (optional for Alert Bridge)", max_length=12000)
    system_prompt: Optional[str] = Field(None, description="System Prompt with context of the stream", max_length=14000)
    response_format: Optional[Dict[str, Any]] = Field(None, description="Expected response format")
    max_tokens: Optional[int] = Field(None, gt=0, le=100000, description="Maximum tokens for VLM response")
    temperature: Optional[float] = Field(None, ge=0.0, le=2.0, description="VLM temperature parameter")
    top_p: Optional[float] = Field(None, ge=0.0, le=1.0, description="VLM top_p parameter")
    top_k: Optional[int] = Field(None, gt=0, le=2048, description="VLM top_k parameter")
    seed: Optional[int] = Field(None, ge=0, le=2_147_483_647, description="VLM seed for reproducibility")

    # Pydantic v2: single config source
    model_config = ConfigDict(
        validate_assignment=True,
        use_enum_values=True,
        extra='ignore'  # Ignore unknown/unsupported fields
    )

    @classmethod
    def create_with_defaults(cls, **overrides) -> 'VLMParams':
        """
        Create VLMParams with defaults from external configuration.
        
        Args:
            **overrides: Field overrides for specific use cases
            
        Returns:
            VLMParams instance with applied defaults and overrides
            
        Raises:
            RuntimeError: If external configuration is not available
        """
        defaults = _get_defaults()
        vlm_defaults = defaults.get('vlm_params', {})
        
        # Use deep copy to prevent global state mutation
        vlm_defaults_copy = copy.deepcopy(vlm_defaults)
        
        # Deep merge overrides into the deep copy (not the original!)
        params = deep_merge(vlm_defaults_copy, overrides)
        
        logger.debug(f"Creating VLMParams with defaults and {len(overrides)} overrides")
        return cls.model_validate(params)

class EventParameters(BaseModel):
    """
    Event-specific processing parameters.
    
    Contains parameters specific to event detection and classification processing.
    Provides event-level configuration for specialized processing workflows.
    """
    
    detection_sensitivity: float = Field(..., ge=0.0, le=1.0, description="Event detection sensitivity")
    temporal_window: int = Field(..., gt=0, description="Temporal analysis window in seconds")
    enable_motion_analysis: bool = Field(..., description="Enable motion pattern analysis")
    enable_audio_analysis: bool = Field(..., description="Enable audio event analysis")
    classification_threshold: float = Field(..., ge=0.0, le=1.0, description="Event classification threshold")
    
    class Config:
        """Pydantic configuration for optimal performance."""
        validate_assignment = True
    
    @classmethod
    def create_with_defaults(cls, **overrides) -> 'EventParameters':
        """
        Create EventParameters with defaults from external configuration.
        
        Args:
            **overrides: Field overrides for specific use cases
            
        Returns:
            EventParameters instance with applied defaults and overrides
            
        Raises:
            RuntimeError: If external configuration is not available
        """
        defaults = _get_defaults()
        request_defaults = defaults['request_defaults']
        
        # Merge defaults with overrides
        params = {
            **request_defaults.get('event_parameters', {}),
            **overrides
        }
        
        logger.debug(f"Creating EventParameters with defaults and {len(overrides)} overrides")
        return cls(**params)
    
    def get_analysis_window(self) -> int:
        """Get temporal analysis window in seconds."""
        return self.temporal_window
    
    def requires_multimodal_analysis(self) -> bool:
        """Check if multimodal analysis is required."""
        return self.enable_motion_analysis and self.enable_audio_analysis


# Support for backward compatibility with existing code
VLMParams = VLMParams 