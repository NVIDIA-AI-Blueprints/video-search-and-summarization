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
Entity Management Response Builder

Builds validated AlertResponseEntity objects from VSS processing results.
Handles field mapping, error classification, schema validation and response formatting.
"""

import logging
from typing import Dict, Any, List
from datetime import datetime, timezone
from pydantic import ValidationError

# Import response models
from .models.responses import (
    AlertResponseEntity, AlertInfo, EventInfo, ResultInfo, MetaLabel
)
from ..request_entity.models.requests import AlertRequestEntity


class ResponseBuilder:
    """
    Response builder for the new response schema structure.
    
    Transforms VSS results and request entities into validated responses
    with nested alert, event, and verification objects.
    """
    
    def __init__(self):
        """Initialize response builder for the new schema."""
        self.logger = logging.getLogger(self.__class__.__name__)
        self.logger.info("ResponseBuilder initialized for new response schema")
    
    def _transform_vss_timestamps(self, vss_response: Dict[str, Any]) -> Dict[str, Any]:
        """
        Transform VSS timestamps from seconds (float) to milliseconds (int).
        
        VSS returns timestamps in seconds as floats, but our schema expects
        milliseconds as integers.
        """
        # Deep copy to avoid modifying original
        import copy
        response = copy.deepcopy(vss_response)
        
        # Check if we have debug.selected_frames_ts that needs conversion
        if 'result' in response and 'debug' in response['result']:
            debug = response['result']['debug']
            if debug and isinstance(debug, dict) and 'selected_frames_ts' in debug:
                frames_ts = debug['selected_frames_ts']
                if isinstance(frames_ts, list):
                    # Convert from seconds (float) to milliseconds (int)
                    debug['selected_frames_ts'] = [
                        int(float(ts) * 1000) for ts in frames_ts
                    ]
                    self.logger.debug(f"Converted {len(frames_ts)} timestamps from seconds to milliseconds")
        
        return response
    
    def build_response_from_vss(self, vss_response: Dict[str, Any]) -> AlertResponseEntity:
        """
        Build response directly from VSS verifyAlert response.
        
        Args:
            vss_response: Complete response from VSS verifyAlert API
            
        Returns:
            Validated AlertResponseEntity
        """
        try:
            # Transform VSS timestamps from seconds to milliseconds
            transformed_response = self._transform_vss_timestamps(vss_response)
            
            # VSS response already contains all required fields!
            # Just validate and convert to our response model
            response_entity = AlertResponseEntity.parse_obj(transformed_response)
            
            self.logger.info("Successfully built response from VSS response", extra={
                "event_id": response_entity.id,
                "result_status": response_entity.result.status
            })
            
            return response_entity
            
        except ValidationError as e:
            # Handle case where VSS response doesn't match our schema
            self.logger.error(f"VSS response validation failed: {e}")
            return self._build_fallback_response(vss_response, str(e))
    
    def build_redis_responses_from_vss_results(self, vss_handler_results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Convert VSS Handler results to Redis-ready responses."""
        redis_responses = []
        
        for vss_result in vss_handler_results:
            try:
                if vss_result.get('success', False):
                    # Use complete VSS response directly
                    vss_response = vss_result['raw_vss_result']
                    response_entity = self.build_response_from_vss(vss_response)
                else:
                    # Handle error cases
                    response_entity = self._build_error_response_from_vss(vss_result)
                
                # Convert to Redis dictionary
                redis_response = response_entity.model_dump(by_alias=True)
                redis_responses.append(redis_response)
                
            except Exception as e:
                self.logger.error(f"Failed to process VSS result: {e}")
                continue
        
        return redis_responses
    
    def _build_error_response_from_vss(self, vss_result: Dict[str, Any]) -> AlertResponseEntity:
        """
        Build error response from VSS error result.
        
        Args:
            vss_result: VSS error result containing original_entity and error info
            
        Returns:
            AlertResponseEntity with error information
        """
        original_entity = vss_result.get('original_entity')
        error_info = vss_result.get('raw_vss_result', {})
        
        # Build a minimal error response
        return AlertResponseEntity(
            id=original_entity.id,
            version="1.0",
            timestamp=original_entity.timestamp,
            sensor_id=original_entity.sensor_id,
            video_path=original_entity.video_path,
            cv_metadata_path=original_entity.cv_metadata_path,
            confidence=original_entity.confidence,
            alert=AlertInfo(
                severity=original_entity.alert.severity,
                status=original_entity.alert.status,  # Keep original status (not verified)
                type=original_entity.alert.type,
                description=original_entity.alert.description
            ),
            event=EventInfo(
                type=original_entity.event.type,
                description=original_entity.event.description
            ),
            result=ResultInfo(
                status="FAILURE",
                error_string=error_info.get('error', 'Unknown error'),
                verification_result=False,
                confidence=0.0,
                review_method="VSS",
                reviewed_by="VSS",
                reviewed_at=datetime.now(timezone.utc).isoformat(),
                notes=f"Error: {error_info.get('error_code', 'UNKNOWN')}"
            ),
            meta_labels=[
                {"key": ml.key, "value": ml.value}
                for ml in (original_entity.meta_labels or [])
            ]
        )
    
    def _build_fallback_response(self, vss_response: Dict[str, Any], error_msg: str) -> AlertResponseEntity:
        """
        Build fallback response when VSS response doesn't match schema.
        
        Args:
            vss_response: The VSS response that failed validation
            error_msg: The validation error message
            
        Returns:
            A minimal AlertResponseEntity
        """
        # Extract basic fields from VSS response
        return AlertResponseEntity(
            id=vss_response.get('id', 'unknown'),
            version=vss_response.get('version', '1.0'),
            timestamp=vss_response.get('@timestamp', datetime.now(timezone.utc).isoformat()),
            sensor_id=vss_response.get('sensor_id', 'unknown'),
            video_path=vss_response.get('video_path', ''),
            alert=AlertInfo(
                severity='HIGH',
                status='ACTIVE',
                type=vss_response.get('alert', {}).get('type', 'VALIDATION_ERROR'),
                description='Response validation failed'
            ),
            event=EventInfo(
                type=vss_response.get('event', {}).get('type', 'validation_error'),
                description='VSS response validation failed'
            ),
            result=ResultInfo(
                status="FAILURE",
                error_string=f"Validation error: {error_msg}",
                verification_result=False,
                review_method="VSS",
                reviewed_by="VSS",
                reviewed_at=datetime.now(timezone.utc).isoformat()
            )
        ) 