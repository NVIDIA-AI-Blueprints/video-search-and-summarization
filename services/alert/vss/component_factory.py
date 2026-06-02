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
VSS component factory that uses config dict directly and AlertRequestEntity parameters.
"""

import logging
import os
from typing import Dict, Any

from .retry_manager import RetryManager
from .session_handler.thread_safe_session_manager import SessionManager
from .media_handler.media_uploader import MediaUploader
from .media_handler.media_deleter import MediaDeleter
from .vss_request_handler.alert_verification_client import AlertVerificationClient

from handlers.prompt_handler.prompt_manager import PromptManager
from handlers.exception_handler.error_handler import ErrorHandler

logger = logging.getLogger(__name__)


class ComponentFactory:
    """Factory for creating VSS components using config dict directly."""
    
    def __init__(self, config: Dict[str, Any]):
        """
        Initialize the component factory.
        
        Args:
            config: Configuration dictionary with vss_agent section
        """
        self.config = config
        self.vss_config = config.get('vss_agent', {})
        self.logger = logging.getLogger(self.__class__.__name__)
        
    def create_all_components(self) -> Dict[str, Any]:
        """
        Create all VSS components with configuration from config dict.
        Processing parameters will come from AlertRequestEntity.
        
        Returns:
            Dictionary of initialized components
        """
        components = {}
        
        # Create infrastructure components
        components['error_handler'] = ErrorHandler()
        components['retry_manager'] = RetryManager(
            max_retries=self.vss_config.get('max_retries', 3),
            base_delay=self.vss_config.get('retry_delay', 1.0)
        )
        components['prompt_manager'] = PromptManager(self.vss_config.get('config_file', 'config.yaml'))

        # Allow ENV override for VSS agent base URL using a single variable
        # VSS_BASE_URL takes precedence over config value
        env_endpoint = os.environ.get('VSS_BASE_URL')
        base_url = self.vss_config.get('base_url')
        if env_endpoint:
            try:
                base_url = os.path.expandvars(env_endpoint)
            except Exception:
                base_url = env_endpoint

        components['session_manager'] = SessionManager(base_url)
        components['media_uploader'] = MediaUploader(
            base_url,
            self.vss_config.get('VSS_IMAGE_ENDPOINT', '/files')
        )
        components['media_deleter'] = MediaDeleter(base_url)
        components['alert_verification_client'] = AlertVerificationClient(
            base_url=base_url,
            verify_alert_endpoint=self.vss_config.get('VSS_REVIEW_ALERT_ENDPOINT', '/reviewAlert'),
            request_timeout=self.vss_config.get('request_timeout', 180),
            max_retries=self.vss_config.get('max_retries', 3),
            retry_delay=self.vss_config.get('retry_delay', 1.0),
            vlm_param_allowlist=self.vss_config.get('vlm_param_allowlist')
        )
        
        self.logger.info("Components created", extra={
            "component_count": len(components)
        })
        
        return components 