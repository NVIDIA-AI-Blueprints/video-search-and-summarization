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
from typing import Dict, Any, List

# Simplified factory (using config dict directly)
from .component_factory import ComponentFactory

# Workflow and entity management  
from .workflow.workflow_executor import WorkflowExecutor

# Entity management
from entity_management.request_entity.models import AlertRequestEntity

# External handlers
from handlers.exception_handler.vss_exceptions import (
    VSSException, VSSConnectionError, VSSRetryExhaustedError, VSSMediaUploadError,
    VSSPromptError, VSSAPIError, VSSResponseError
)

logger = logging.getLogger(__name__)


class VSSHandler:
    """
    Unified VSS handler for video processing.
    This class provides a clean interface for external scripts while managing the complete workflow internally.
    """
    
    def __init__(self, config: Dict[str, Any]):
        """
        Initialize the VSS Handler.
        
        Args:
            config: Configuration dictionary with VSS settings
        """
        self.logger = logging.getLogger(self.__class__.__name__)
        
        try:
            # Create component factory using config dict directly
            self.component_factory = ComponentFactory(config)
            
            # Create all components
            components = self.component_factory.create_all_components()
            
            # Initialize workflow executor
            self.workflow_executor = WorkflowExecutor(components)
            
            # Store session manager reference for initialization
            self.session_manager = components['session_manager']
            
            self._initialized = False
            
        except Exception as e:
            self.logger.error(f"VSS Handler initialization failed: {e}")
            raise VSSException(f"Failed to initialize VSS Handler: {str(e)}")
    
    def initialize(self) -> None:
        """
        Initialize the VSS handler. Must be called before processing.
        
        This method will attempt to connect to VSS up to 3 times.
        If connection fails, the handler will still be marked as initialized
        and API calls will handle connection errors when they occur.
        """
        if self._initialized:
            self.logger.debug("VSS Handler already initialized")
            return
            
        self.logger.info("Initializing VSS Handler...")
        self.session_manager.initialize()
        self._initialized = True
        
        if self.session_manager.is_connected():
            self.logger.info("VSS Handler initialized successfully with connection")
        else:
            self.logger.warning("VSS Handler initialized but VSS is not connected. "
                              "API calls will handle connection errors.")
    
    def process_video_batch(self, alert_entities: List[AlertRequestEntity]) -> List[Dict[str, Any]]:
        """
        Process a batch of video entities through the VSS pipeline.
        This is the main entry point for external scripts.
        
        Args:
            alert_entities: List of AlertRequestEntity objects with video paths
            
        Returns:
            List of processed results containing original entity and raw VSS results
        """
        if not self._initialized:
            error_msg = "VSS Handler not initialized. Call initialize() first."
            self.logger.error(error_msg)
            # Return all entities with initialization error
            return [{"error": error_msg, "error_code": "INITIALIZATION_ERROR"} for _ in alert_entities]
        
        results = []
        total = len(alert_entities)
        
        self.logger.info("Processing video batch", extra={
            "batch_size": total,
            "component": "VSSHandler"
        })
        
        for idx, entity in enumerate(alert_entities, 1):
            try:
                self.logger.debug("Processing entity", extra={
                    "entity_index": idx,
                    "total_entities": total,
                    "eventId": entity.id
                })
                result = self._process_single_entity(entity)
                results.append(result)
            except Exception as e:
                # All errors are already handled in _process_single_entity
                # This is just a safety net for any unexpected errors
                self.logger.error(f"Unexpected error processing entity {idx}/{total}: {e}")
                results.append(self._build_error_response(entity, str(e), "UNEXPECTED_ERROR"))
                
        self.logger.info("Batch processing completed", extra={
            "batch_size": total,
            "successful": len([r for r in results if r.get('success', False)]),
            "failed": len([r for r in results if not r.get('success', False)])
        })
        return results
    
    def _process_single_entity(self, entity: AlertRequestEntity) -> Dict[str, Any]:
        """Process a single video entity through the complete VSS workflow."""
        # Log entity details with structured logging
        self.logger.info("Processing entity", extra={
            "eventId": entity.id,
            "sensorId": entity.sensor_id,
            "timestamp": entity.timestamp,
            "alertType": entity.alert.type
        })
        
        try:
            # Extract video path
            video_path = entity.video_path
            if not video_path:
                return self._build_error_response(
                    entity, "Missing video file path", "VALIDATION_ERROR"
                )
            
            # Extract cv_metadata_path (optional)
            cv_metadata_path = entity.cv_metadata_path
            
            # Execute workflow with AlertRequestEntity directly
            vss_evaluations = self.workflow_executor.execute(entity, video_path, cv_metadata_path)
            
            # Build success response
            return self._build_success_response(entity, vss_evaluations)
            
        except VSSMediaUploadError as e:
            # Handle media upload errors (including file not found)
            return self._build_error_response(
                entity, str(e), "MEDIA_UPLOAD_ERROR"
            )
        except VSSPromptError as e:
            # Handle prompt errors
            return self._build_error_response(
                entity, str(e), "PROMPT_ERROR"
            )
        except VSSRetryExhaustedError as e:
            return self._build_error_response(
                entity, str(e), "RETRY_EXHAUSTED", retriable=True
            )
        except VSSConnectionError as e:
            return self._build_error_response(
                entity, str(e), "CONNECTION_ERROR", retriable=True
            )
        except VSSAPIError as e:
            return self._build_error_response(
                entity, str(e), "PROCESSING_ERROR"
            )
        except VSSResponseError as e:
            return self._build_error_response(
                entity, str(e), "PROCESSING_ERROR"
            )
        except VSSException as e:
            # Handle other VSS exceptions (this catches any we missed)
            return self._build_error_response(
                entity, str(e), "PROCESSING_ERROR"
            )
        except Exception as e:
            self.logger.error(f"Error processing entity {entity.id}: {e}")
            return self._build_error_response(entity, str(e), "PROCESSING_ERROR")
        
    def _build_success_response(self, entity: AlertRequestEntity, vss_evaluations: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Build success response - now much simpler."""
        
        # VSS response contains everything we need
        vss_response = vss_evaluations[0] if vss_evaluations else {}  # First response contains full result
        
        self.logger.info("VSS processing successful", extra={
            "eventId": entity.id,
            "sensorId": entity.sensor_id,
            "verification_status": vss_response.get('verification', {}).get('status', 'N/A')
        })
        
        return {
            'original_entity': entity,  # Keep reference for error handling
            'raw_vss_result': vss_response,  # Complete VSS response
            'success': True
        }
    


    def _build_error_response(
        self, 
        entity: AlertRequestEntity, 
        error_msg: str, 
        error_code: str,
        retriable: bool = False
    ) -> Dict[str, Any]:
        """Build error response with minimal VSS-specific details."""
        
        # Log the error with context
        self.logger.error(f"VSS Error for entity", extra={
            'eventId': entity.id,
            'sensorId': entity.sensor_id,
            'timestamp': entity.timestamp,
            'alertType': entity.alert.type,
            'error_code': error_code,
            'error_message': error_msg,
            'retriable': retriable
        })
        
        # Return minimal error structure
        return {
            'original_entity': entity,
            'raw_vss_result': {
                'error': error_msg,
                'error_code': error_code,
                'retriable': retriable
            },
            'success': False
        }
    

    
    def close(self) -> None:
        """Close the VSS handler and clean up resources."""
        try:
            self.session_manager.close()
            self._initialized = False
            self.logger.info("VSS Handler closed successfully")
        except Exception as e:
            self.logger.error(f"Error closing VSS Handler: {e}")


# Backward compatibility alias
ITS_VSS_HANDLER = VSSHandler 