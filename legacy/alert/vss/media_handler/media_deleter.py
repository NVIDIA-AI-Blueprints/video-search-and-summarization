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
import time
import requests

logger = logging.getLogger(__name__)


class MediaDeleter:
    """Handles media deletion operations for VSS API."""
    
    def __init__(self, base_url: str):
        """
        Initialize the MediaDeleter.
        
        Args:
            base_url: The base URL for the VSS API
        """
        self.base_url = base_url
        self.logger = logging.getLogger(self.__class__.__name__)
        
    def delete_media(self, session: requests.Session, media_id: str) -> bool:
        """
        Delete the uploaded media file from the server.
        
        Args:
            session: The active requests session
            media_id: The ID of the media to delete
            
        Returns:
            True if deletion was successful, False otherwise
        """
        max_retries = 2  # Fewer retries for deletion
        retry_delay = 1  # Initial delay in seconds
        
        for attempt in range(max_retries):
            try:
                self.logger.debug(
                    f"Deleting media ID {media_id}... "
                    f"(Attempt {attempt+1}/{max_retries})"
                )
                
                response = session.delete(
                    f"{self.base_url}/files/{media_id}"
                )
                response.raise_for_status()
                
                self.logger.debug(f"Media ID {media_id} deleted successfully.")
                return True
                    
            except requests.RequestException as e:
                self.logger.error(
                    f"Error deleting media {media_id} "
                    f"(Attempt {attempt+1}/{max_retries}): {str(e)}"
                )
                self._log_request_error(f"delete media {media_id}", e)
                
                # Only wait if we're going to retry
                if attempt < max_retries - 1:
                    wait_time = retry_delay * (2 ** attempt)  # Exponential backoff
                    self.logger.debug(f"Retrying deletion in {wait_time} seconds...")
                    time.sleep(wait_time)
        
        # All retries failed
        return False
        
    def _log_request_error(self, operation: str, error: Exception) -> None:
        """Log detailed information about a request error."""
        self.logger.error(f"Error during {operation}: {error}")
        
        if isinstance(error, requests.HTTPError):
            self.logger.error(f"Response status: {error.response.status_code}")
            if hasattr(error.response, 'text'):
                self.logger.error(f"Response message: {error.response.text}") 