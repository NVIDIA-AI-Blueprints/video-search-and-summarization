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
from typing import Dict, Any, List, Optional
import time

from entity_management import AlertRequestEntity
from entity_management.request_entity.models.parameters import VSSParams
from handlers.exception_handler.error_handler import ErrorHandler
from handlers.exception_handler.vss_exceptions import (
    VSSException, VSSConnectionError, VSSMediaUploadError, 
    VSSAPIError, VSSResponseError, VSSPromptError
)

logger = logging.getLogger(__name__)


class WorkflowExecutor:
    """Executes the VSS workflow steps in the correct order."""
    
    def __init__(self, components: Dict[str, Any]):
        """
        Initialize the workflow executor.
        
        Args:
            components: Dictionary containing all VSS components
        """
        self.logger = logging.getLogger(self.__class__.__name__)
        
        # Get components
        self.prompt_manager = components['prompt_manager']
        self.alert_verification_client = components['alert_verification_client']
        self.media_uploader = components['media_uploader']
        self.media_deleter = components['media_deleter']
        self.session_manager = components['session_manager']
        self.error_handler = components['error_handler']
        self.retry_manager = components['retry_manager']
    
    def execute(self, entity: AlertRequestEntity, video_path: str, cv_metadata_path: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Execute the VSS workflow for a single entity.
        
        Args:
            entity: The AlertRequestEntity to process
            video_path: Path to the video file
            cv_metadata_path: Optional path to CV metadata file
            
        Returns:
            List of raw VSS verification responses from the verifyAlert API
            
        Raises:
            VSSException: If processing fails
        """
        # Execute with retry logic
        return self.retry_manager.with_retries(
            self._execute_workflow,
            "VSS Workflow",
            entity,
            video_path,
            cv_metadata_path
        )
    
    def _execute_workflow(self, entity: AlertRequestEntity, video_path: str, cv_metadata_path: Optional[str] = None) -> List[Dict[str, Any]]:
        """Execute the core workflow steps using alert verification API."""
        # Get prompts using PromptManager
        prompts = self._get_prompts(entity)
        
        # Resolve system prompt once (payload-first → Redis → None)
        entity_dict_full = entity.model_dump(by_alias=True, exclude_none=False)
        system_prompt_text = None
        # Prefer payload-provided system_prompt if present
        try:
            payload_system_prompt = None
            if entity.vss_params and entity.vss_params.vlm_params and getattr(entity.vss_params.vlm_params, 'system_prompt', None):
                payload_system_prompt = entity.vss_params.vlm_params.system_prompt
            if payload_system_prompt:
                system_prompt_text = payload_system_prompt
                self.logger.info("Using payload system_prompt")
            else:
                system_prompt_text = self.prompt_manager.get_system_prompt_for_entity(entity_dict_full)
                if system_prompt_text:
                    self.logger.info("Using Redis system_prompt from PromptManager")
        except Exception:
            system_prompt_text = None
        
        # Get active session
        session_info = self._get_session_info()
        
        try:
            # Process each prompt through alert verification
            all_evaluations = []
            
            for prompt_data in prompts:
                # Update entity with the prompt for this iteration
                # Create a copy of vss_params with the updated prompt nested inside vlm_params
                prompt_text = prompt_data.get('question', '')
                
                # Ensure vss_params exists (it should due to default_factory)
                if entity.vss_params is None:
                    entity.vss_params = VSSParams.create_with_defaults()
                
                # Create updated vss_params with vlm_params containing the prompt (snake_case)
                updated_vss_params = entity.vss_params.model_dump(exclude_none=False)
                
                # Ensure vlm_params exists and preserve existing values
                if 'vlm_params' not in updated_vss_params:
                    updated_vss_params['vlm_params'] = {}
                
                # Preserve existing vlm_params and only update the prompt
                existing_vlm_params = updated_vss_params.get('vlm_params', {})
                
                # Decide prompt: payload wins, otherwise use PromptManager-provided question
                payload_prompt = None
                if entity.vss_params and entity.vss_params.vlm_params and getattr(entity.vss_params.vlm_params, 'prompt', None):
                    payload_prompt = entity.vss_params.vlm_params.prompt
                if payload_prompt:
                    existing_vlm_params['prompt'] = payload_prompt
                    self.logger.info("Using payload user prompt for this request")
                else:
                    existing_vlm_params['prompt'] = prompt_text
                    self.logger.info("Using Redis user prompt from PromptManager")
                
                # System prompt: payload wins, else use resolved system_prompt_text if any
                payload_system_prompt = None
                if entity.vss_params and entity.vss_params.vlm_params and getattr(entity.vss_params.vlm_params, 'system_prompt', None):
                    payload_system_prompt = entity.vss_params.vlm_params.system_prompt
                if payload_system_prompt:
                    existing_vlm_params['system_prompt'] = payload_system_prompt
                elif system_prompt_text:
                    existing_vlm_params['system_prompt'] = system_prompt_text
                updated_vss_params['vlm_params'] = existing_vlm_params
                
                # Use model_copy to efficiently create a new entity with updated vss_params
                entity_with_prompt = entity.model_copy(
                    update={'vss_params': VSSParams.model_validate(updated_vss_params)}
                )
                
                # Call the alert verification API
                self.logger.info("Starting alert verification", extra={
                    "eventId": entity.id,
                    "video_path": video_path,
                    "prompt": prompt_data.get('question', '')[:100] + "..."
                })
                
                verification_response = self.alert_verification_client.verify_alert(
                    session_info['session'],
                    entity_with_prompt,
                    video_path,
                    cv_metadata_path
                )
                
                # Check if verification was successful
                result_obj = verification_response.get('result', {})
                if result_obj.get('status') != 'SUCCESS':
                    error_string = result_obj.get('error_string', 'Unknown error')
                    raise VSSAPIError(f"Alert verification failed: {error_string}")
                
                # Log successful verification
                self.logger.info("Alert verification completed", extra={
                    "eventId": entity.id,
                    "verification_status": result_obj.get('status'),
                    "result": result_obj.get('verification_result')
                })
                
                # Return the raw VSS response directly
                # The response already contains all necessary information
                all_evaluations.append(verification_response)
            
            # Validate we got responses
            if not all_evaluations:
                raise VSSResponseError("No response from alert verification API")
            
            return all_evaluations
            
        except Exception as e:
            # Re-raise the exception for retry manager to handle
            self.logger.error("Alert verification workflow failed", extra={
                "error": str(e),
                "eventId": entity.id
            })
            raise
    
    def _get_prompts(self, entity: AlertRequestEntity) -> List[Dict[str, str]]:
        """Get prompts for processing using the prompt manager."""
        try:
            # Pass the full entity as a dict (with all fields) to the prompt manager
            entity_dict = entity.model_dump(by_alias=True, exclude_none=False)
            prompts = self.prompt_manager.get_prompts_for_entity(entity_dict)
            
            if prompts:
                self.logger.info(f"Retrieved {len(prompts)} prompts for entity", extra={
                    "eventId": entity.id,
                    "alertType": entity.alert.type
                })
                return prompts
            else:
                # This should not happen as get_prompts_for_entity should raise error
                raise VSSPromptError("Empty prompts returned")
                
        except VSSPromptError:
            # Re-raise prompt errors without catching them
            raise
        except Exception as e:
            # Handle other unexpected errors
            self.error_handler.handle_error(e, "Unexpected error getting prompts")
            raise VSSException(f"Failed to get prompts: {str(e)}")
    
    def _get_session_info(self) -> Dict[str, Any]:
        """Get session and model information."""
        try:
            # Check if VSS is connected
            if not self.session_manager.is_connected():
                raise VSSConnectionError(
                    "VSS is not connected. The service may be unavailable. "
                    "Please check VSS service status."
                )
            
            session = self.session_manager.get_session()
            model_id = self.session_manager.get_model_id()
            
            if not model_id:
                raise VSSConnectionError("No VSS model ID available")
                
            return {'session': session, 'model_id': model_id}
            
        except Exception as e:
            self.error_handler.handle_error(e, "Error getting session info")
            raise
    
 