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
Evaluation Parser Component

Parses raw VSS evaluation data into standardized evaluation objects.
Handles data validation, transformation, and confidence normalization.
"""

import logging
from typing import Dict, Any, List, Optional


class EvaluationParser:
    """
    Parses raw VSS evaluation data into standardized format.
    
    Handles confidence score normalization, bounding box parsing,
    and metadata extraction from VSS responses.
    """
    
    def __init__(self):
        """Initialize evaluation parser with configuration."""
        self.logger = logging.getLogger(self.__class__.__name__)
        self._load_parsing_config()
    
    def _load_parsing_config(self) -> None:
        """Load evaluation parsing configuration from response defaults."""
        try:
            # from ....config.response_defaults_loader import ResponseDefaultsLoader
            # loader = ResponseDefaultsLoader()
            # config = loader.load_defaults()
            
            # self.parsing_config = config.get('evaluation_parsing', {})
            # self.confidence_thresholds = config.get('confidence_thresholds', {})
            
            self.logger.debug("Evaluation parsing configuration loaded successfully")
            
        except Exception as e:
            self.logger.warning(f"Failed to load evaluation parsing config: {e}")
            # Use fallback configuration
            self.parsing_config = self._get_default_parsing_config()
            self.confidence_thresholds = self._get_default_confidence_thresholds()
    
    def _get_default_parsing_config(self) -> Dict[str, Any]:
        """Get default parsing configuration."""
        return {
            'normalize_confidence': True,
            'min_confidence_threshold': 0.0,
            'max_confidence_threshold': 1.0,
            'validate_bounding_boxes': True,
            'extract_metadata': True
        }
    
    def _get_default_confidence_thresholds(self) -> Dict[str, float]:
        """Get default confidence thresholds."""
        return {
            'min_reportable': 0.1,
            'high_confidence': 0.8,
            'max_valid': 1.0
        }
    
    def parse_vss_evaluations(self, raw_evaluations: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Parse raw VSS evaluation data into standardized format.
        
        Args:
            raw_evaluations: List of raw evaluation dictionaries from VSS
            
        Returns:
            List of parsed evaluation dictionaries for response processing
        """
        if not raw_evaluations:
            self.logger.debug("No evaluations to parse")
            return []
        
        parsed_evaluations = []
        
        for idx, raw_eval in enumerate(raw_evaluations):
            try:
                parsed_eval = self._parse_single_evaluation(raw_eval, idx)
                if parsed_eval:
                    parsed_evaluations.append(parsed_eval)
                    
            except Exception as e:
                self.logger.warning(f"Failed to parse evaluation {idx}: {e}")
                # Continue with other evaluations
                continue
        
        self.logger.debug(f"Parsed {len(parsed_evaluations)} evaluations from {len(raw_evaluations)} raw")
        return parsed_evaluations
    
    def _parse_single_evaluation(self, raw_eval: Dict[str, Any], index: int) -> Optional[Dict[str, Any]]:
        """
        Parse a single evaluation from raw VSS data.
        
        Args:
            raw_eval: Raw evaluation dictionary
            index: Index for logging
            
        Returns:
            Parsed evaluation dictionary or None if invalid
        """
        # Extract and validate confidence
        confidence = self._extract_and_normalize_confidence(raw_eval, index)
        if confidence is None:
            return None
        
        # Extract label
        label = self._extract_label(raw_eval, index)
        if not label:
            return None
        
        # Extract and validate bounding box
        bounding_box = self._extract_bounding_box(raw_eval, index)
        
        # Extract metadata
        metadata = self._extract_metadata(raw_eval, index)
        
        parsed_eval = {
            'confidence': confidence,
            'label': label,
            'bounding_box': bounding_box,
            'metadata': metadata
        }
        
        self.logger.debug(f"Parsed evaluation {index}: {label} ({confidence:.3f})")
        return parsed_eval
    
    def _extract_and_normalize_confidence(self, raw_eval: Dict[str, Any], index: int) -> Optional[float]:
        """
        Extract and normalize confidence score.
        
        Args:
            raw_eval: Raw evaluation data
            index: Index for logging
            
        Returns:
            Normalized confidence score or None if invalid
        """
        # Try different confidence field names
        confidence_fields = ['confidence', 'score', 'probability', 'conf']
        confidence = None
        
        for field in confidence_fields:
            if field in raw_eval:
                confidence = raw_eval[field]
                break
        
        if confidence is None:
            self.logger.warning(f"No confidence field found in evaluation {index}")
            return None
        
        # Convert to float
        try:
            confidence = float(confidence)
        except (ValueError, TypeError):
            self.logger.warning(f"Invalid confidence value in evaluation {index}: {confidence}")
            return None
        
        # Normalize if enabled
        if self.parsing_config.get('normalize_confidence', True):
            confidence = self._normalize_confidence_score(confidence)
        
        # Validate range
        min_threshold = self.confidence_thresholds.get('min_reportable', 0.0)
        max_threshold = self.confidence_thresholds.get('max_valid', 1.0)
        
        if not (min_threshold <= confidence <= max_threshold):
            self.logger.warning(
                f"Confidence {confidence} outside valid range [{min_threshold}, {max_threshold}] for evaluation {index}"
            )
            return None
        
        return confidence
    
    def _normalize_confidence_score(self, confidence: float) -> float:
        """
        Normalize confidence score to 0-1 range.
        
        Args:
            confidence: Raw confidence score
            
        Returns:
            Normalized confidence score
        """
        # Handle common VSS confidence ranges
        if confidence > 1.0:
            # Assume percentage (0-100)
            if confidence <= 100.0:
                return confidence / 100.0
            # Assume larger scale, normalize to max found
            else:
                return min(confidence / 1000.0, 1.0)
        
        # Already in 0-1 range
        return max(0.0, min(confidence, 1.0))
    
    def _extract_label(self, raw_eval: Dict[str, Any], index: int) -> Optional[str]:
        """
        Extract detection label.
        
        Args:
            raw_eval: Raw evaluation data
            index: Index for logging
            
        Returns:
            Detection label or None if not found
        """
        # Try different label field names
        label_fields = ['label', 'class', 'category', 'type', 'name']
        
        for field in label_fields:
            if field in raw_eval:
                label = raw_eval[field]
                if isinstance(label, str) and label.strip():
                    return label.strip()
        
        self.logger.warning(f"No valid label found in evaluation {index}")
        return None
    
    def _extract_bounding_box(self, raw_eval: Dict[str, Any], index: int) -> Optional[Dict[str, float]]:
        """
        Extract and validate bounding box coordinates.
        
        Args:
            raw_eval: Raw evaluation data
            index: Index for logging
            
        Returns:
            Bounding box dictionary or None if not found/invalid
        """
        # Try different bounding box field names
        bbox_fields = ['bounding_box', 'bbox', 'box', 'coordinates', 'bounds']
        bbox_data = None
        
        for field in bbox_fields:
            if field in raw_eval:
                bbox_data = raw_eval[field]
                break
        
        if not bbox_data:
            return None
        
        # Validate bounding box if enabled
        if self.parsing_config.get('validate_bounding_boxes', True):
            return self._validate_bounding_box(bbox_data, index)
        
        return bbox_data if isinstance(bbox_data, dict) else None
    
    def _validate_bounding_box(self, bbox_data: Any, index: int) -> Optional[Dict[str, float]]:
        """
        Validate bounding box data format.
        
        Args:
            bbox_data: Raw bounding box data
            index: Index for logging
            
        Returns:
            Validated bounding box or None if invalid
        """
        if not isinstance(bbox_data, dict):
            return None
        
        # Check for required coordinates
        required_fields = ['x', 'y', 'width', 'height']
        alternative_fields = ['x1', 'y1', 'x2', 'y2']
        
        has_required = all(field in bbox_data for field in required_fields)
        has_alternative = all(field in bbox_data for field in alternative_fields)
        
        if not (has_required or has_alternative):
            self.logger.warning(f"Invalid bounding box format in evaluation {index}")
            return None
        
        # Validate numeric values
        try:
            validated_bbox = {}
            for key, value in bbox_data.items():
                if isinstance(value, (int, float)):
                    validated_bbox[key] = float(value)
                    
            return validated_bbox if validated_bbox else None
            
        except (ValueError, TypeError):
            self.logger.warning(f"Non-numeric bounding box values in evaluation {index}")
            return None
    
    def _extract_metadata(self, raw_eval: Dict[str, Any], index: int) -> Dict[str, Any]:
        """
        Extract additional metadata from evaluation.
        
        Args:
            raw_eval: Raw evaluation data
            index: Index for logging
            
        Returns:
            Metadata dictionary
        """
        if not self.parsing_config.get('extract_metadata', True):
            return {}
        
        # Exclude core fields from metadata
        core_fields = {
            'confidence', 'score', 'probability', 'conf',
            'label', 'class', 'category', 'type', 'name',
            'bounding_box', 'bbox', 'box', 'coordinates', 'bounds'
        }
        
        metadata = {}
        for key, value in raw_eval.items():
            if key not in core_fields:
                metadata[key] = value
        
        return metadata
    
    def get_parsing_stats(self) -> Dict[str, Any]:
        """Get parsing configuration statistics."""
        return {
            'parsing_config': dict(self.parsing_config),
            'confidence_thresholds': dict(self.confidence_thresholds)
        } 