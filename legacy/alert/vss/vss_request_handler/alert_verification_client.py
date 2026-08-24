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

import logging
import json
import time
from typing import Dict, Any, Optional
import requests

from handlers.exception_handler.vss_exceptions import VSSAPIError
from entity_management import AlertRequestEntity

logger = logging.getLogger(__name__)


class AlertVerificationClient:
    """
    Handles VSS API requests for alert verification endpoint.
    
    This client acts as an adapter between the Alert Agent's data format and
    the VSS API requirements. It handles several transformations:
    
    1. ID Format: Strips semantic prefixes (e.g., "event-") to provide pure UUIDs
    2. Field Structure: Moves vlmParams inside vssParams as required by VSS
    3. Status Updates: Sets appropriate status values for verification flow
    4. Path Handling: Ensures proper video and metadata path formatting
    
    These transformations allow different systems to maintain their own
    conventions while ensuring smooth integration.
    """
    
    def __init__(self, base_url: str, verify_alert_endpoint: str = "/reviewAlert",
                 request_timeout: int = 180, max_retries: int = 3, retry_delay: float = 1.0,
                 vlm_param_allowlist: Optional[Any] = None):
        """
        Initialize the AlertVerificationClient.
        
        Args:
            base_url: The base URL for the VSS API
            verify_alert_endpoint: The endpoint for alert verification (default: /verifyAlert)
            request_timeout: Request timeout in seconds (default: 180)
            max_retries: Maximum number of retry attempts (default: 3)
            retry_delay: Initial retry delay in seconds (default: 1.0)
        """
        self.base_url = base_url
        self.verify_alert_endpoint = verify_alert_endpoint
        self.request_timeout = request_timeout
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self.logger = logging.getLogger(self.__class__.__name__)
        # Internal-only fields that must never be sent to VSS
        self._internal_only_fields = {"vst_id"}
        # Allowlist for VLM params; accept list or set from config
        try:
            if isinstance(vlm_param_allowlist, (list, set, tuple)):
                self._vlm_param_allowlist = set(vlm_param_allowlist)
            else:
                self._vlm_param_allowlist = {
                    'prompt', 'system_prompt', 'response_format', 'max_tokens', 'temperature',
                    'top_p', 'top_k', 'seed'
                }
        except Exception:
            self._vlm_param_allowlist = {
                'prompt', 'system_prompt', 'response_format', 'max_tokens', 'temperature',
                'top_p', 'top_k', 'seed'
            }

    def _strip_internal_fields(self, payload: Dict[str, Any]) -> None:
        """Remove internal-only fields from the payload in-place.

        Keeping this centralized allows easy extension for more fields later.
        """
        for field in list(self._internal_only_fields):
            if field in payload:
                try:
                    self.logger.debug(f"Removing internal field '{field}' from VSS payload")
                except Exception:
                    pass
                payload.pop(field, None)
    
    def verify_alert(
        self, 
        session: requests.Session,
        entity: AlertRequestEntity,
        video_path: str,
        cv_metadata_path: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Call the VSS verifyAlert endpoint for video alert verification.
        
        Args:
            session: The active requests session
            entity: The AlertRequestEntity containing all alert information
            video_path: Path to the video file
            cv_metadata_path: Optional path to CV metadata file (overrides entity value if provided)
            
        Returns:
            The verification response dictionary
            
        Raises:
            VSSAPIError: If the API call fails
        """
        payload = self._build_verify_alert_payload(entity, video_path, cv_metadata_path)
        return self._call_verify_alert_api(session, payload)
        
    def _build_verify_alert_payload(
        self, 
        entity: AlertRequestEntity,
        video_path: str,
        cv_metadata_path: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Build payload for verifyAlert endpoint using AlertRequestEntity.
        
        Args:
            entity: The validated AlertRequestEntity with all defaults applied
            video_path: Path to the video file
            cv_metadata_path: Optional CV metadata path override
            
        Returns:
            The payload dictionary for the verifyAlert API
        """
        # Build snake_case payload (only alias needed is "@timestamp")
        payload = entity.model_dump(by_alias=True, exclude_none=True)
        # Ensure internal-only fields are not sent to VSS (modular helper)
        self._strip_internal_fields(payload)
        
        # VSS API expects vss_params.vlm_params; filter out unsupported parameters
        if "vss_params" in payload and entity.vss_params and entity.vss_params.vlm_params:
            complete_vlm_params = entity.vss_params.vlm_params.model_dump(exclude_none=False)
            
            # Filter to only include supported parameters
            filtered_vlm_params = {
                key: value for key, value in complete_vlm_params.items() 
                if key in self._vlm_param_allowlist
            }
            
            payload["vss_params"]["vlm_params"] = filtered_vlm_params
            self.logger.debug(f"Filtered VLM params to VSS-supported fields: {list(filtered_vlm_params.keys())}")
            
        self.logger.debug(f"VSS API payload format: {list(payload.get('vss_params', {}).keys())}")
        
        # 1. Override video_path with the provided video_path parameter
        payload["video_path"] = video_path
        
        # 2. Override cv_metadata_path if provided as parameter
        if cv_metadata_path is not None:
            payload["cv_metadata_path"] = cv_metadata_path
        
        # 3. Ensure cv_metadata_path is always a string (VSS API requirement)
        if "cv_metadata_path" not in payload or payload["cv_metadata_path"] is None:
            payload["cv_metadata_path"] = ""
        
        # 4. Preserve incoming alert.status from entity (validator enforces schema)
        
        # 5. Ensure prompt exists (default if missing)
        prompt = None
        if "vss_params" in payload and "vlm_params" in payload["vss_params"] and "prompt" in payload["vss_params"]["vlm_params"]:
            prompt = payload["vss_params"]["vlm_params"]["prompt"]
        else:
            prompt = "Analyze this video for any anomalies or alerts"
        # No need to move or delete params at the top level anymore
        
        self.logger.debug(f"Built verifyAlert payload:\n{json.dumps(payload, indent=2)}")
        
        return payload
        
    def _call_verify_alert_api(
        self, 
        session: requests.Session,
        payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Make a POST request to the verifyAlert API endpoint."""
        endpoint_url = f"{self.base_url}{self.verify_alert_endpoint}"
        
        for attempt in range(self.max_retries):
            try:
                self.logger.info(f"Calling verifyAlert API - Attempt: {attempt + 1}/{self.max_retries}")
                
                if self.logger.isEnabledFor(logging.DEBUG):
                    # Only log payload in debug mode
                    self.logger.debug(f"VerifyAlert API request payload:\n{json.dumps(payload, indent=2)}")
                
                response = session.post(
                    endpoint_url,
                    json=payload,
                    timeout=self.request_timeout,
                    headers={"Content-Type": "application/json"}
                )
                
                # Log response status
                self.logger.info(f"VerifyAlert API response - Status: {response.status_code} - Size: {len(response.content)} bytes")
                
                # Handle different response codes
                if response.status_code == 200:
                    response_json = response.json()
                    self.logger.debug(f"Full VerifyAlert API JSON response:\n{json.dumps(response_json, indent=2)}")
                    self.logger.info("Alert verification successful", extra={
                        "id": payload.get('id'),
                        "verification_status": response_json.get('verification', {}).get('status')
                    })
                    return response_json
                    
                elif response.status_code in [400, 401, 422, 429, 500]:
                    # Handle error responses
                    try:
                        error_json = response.json()
                        error_code = error_json.get('code', 'UNKNOWN_ERROR')
                        error_message = error_json.get('message', f'HTTP {response.status_code} error')
                        
                        self.logger.error(f"VerifyAlert API error - Code: {error_code} - Message: {error_message}")
                        
                        # Don't retry on 400, 401, 422 errors as they are client errors
                        if response.status_code in [400, 401, 422]:
                            raise VSSAPIError(f"Alert verification failed: {error_code} - {error_message}")
                            
                    except json.JSONDecodeError:
                        error_message = f"HTTP {response.status_code} error: {response.text[:200]}"
                        self.logger.error(f"VerifyAlert API error (non-JSON): {error_message}")
                        
                        if response.status_code in [400, 401, 422]:
                            raise VSSAPIError(f"Alert verification failed: {error_message}")
                    
                    # For 429 and 500 errors, retry
                    if attempt < self.max_retries - 1:
                        wait_time = self.retry_delay * (2 ** attempt)
                        self.logger.warning(f"Retrying after error - Wait time: {wait_time}s")
                        time.sleep(wait_time)
                        continue
                    else:
                        raise VSSAPIError(f"Alert verification failed after {self.max_retries} attempts: {error_message}")
                        
                else:
                    # Unexpected status code
                    raise VSSAPIError(f"Unexpected status code {response.status_code}: {response.text[:200]}")
                    
            except requests.exceptions.Timeout:
                if attempt < self.max_retries - 1:
                    wait_time = self.retry_delay * (2 ** attempt)
                    self.logger.warning(f"Request timeout, retrying - Attempt: {attempt + 1}/{self.max_retries} - Wait time: {wait_time}s")
                    time.sleep(wait_time)
                else:
                    self.logger.error(f"Request timeout after {self.max_retries} attempts")
                    raise VSSAPIError(f"Alert verification timed out after {self.request_timeout}s")
                    
            except requests.exceptions.RequestException as e:
                if attempt < self.max_retries - 1:
                    wait_time = self.retry_delay * (2 ** attempt)
                    self.logger.warning(f"Request failed, retrying - Attempt: {attempt + 1}/{self.max_retries} - Wait time: {wait_time}s - Error: {str(e)}")
                    time.sleep(wait_time)
                else:
                    self.logger.error(f"Request failed permanently - Attempts: {self.max_retries} - Error: {str(e)}")
                    raise VSSAPIError(f"Failed to call verifyAlert: {str(e)}")
                    
        raise VSSAPIError(f"Failed to call verifyAlert after {self.max_retries} attempts") 