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

import os
import logging
import time
from typing import Optional
import requests
from handlers.exception_handler.vss_exceptions import VSSMediaUploadError

logger = logging.getLogger(__name__)


class MediaUploader:
    """Handles media upload operations for VSS API."""
    
    def __init__(self, base_url: str, upload_endpoint: str):
        """
        Initialize the MediaUploader.
        
        Args:
            base_url: The base URL for the VSS API
            upload_endpoint: The endpoint for media uploads
        """
        self.base_url = base_url
        self.upload_endpoint = upload_endpoint
        self.logger = logging.getLogger(self.__class__.__name__)
        
    def upload_video(self, session: requests.Session, video_path: str) -> Optional[str]:
        """
        Upload a video to the VSS API and return the media ID.
        
        Args:
            session: The active requests session
            video_path: Path to the video file
            
        Returns:
            The media ID if successful
            
        Raises:
            VSSMediaUploadError: If the file is not found or upload fails
        """
        if not video_path:
            self.logger.error("No video path provided")
            raise VSSMediaUploadError("No video file path provided")
            
        # Check if file exists
        if not os.path.exists(video_path):
            self.logger.error("Video file not found", extra={
                "file_path": video_path
            })
            raise VSSMediaUploadError(f"Video file not found: {video_path}")
            
        # Check if file is readable
        if not os.access(video_path, os.R_OK):
            self.logger.error("Video file is not readable", extra={
                "file_path": video_path
            })
            raise VSSMediaUploadError(f"Video file is not readable: {video_path}")
            
        # Check file size
        try:
            file_size = os.path.getsize(video_path)
            if file_size == 0:
                self.logger.error("Video file is empty", extra={
                    "file_path": video_path
                })
                raise VSSMediaUploadError(f"Video file is empty: {video_path}")
        except OSError as e:
            self.logger.error("Error accessing video file", extra={
                "file_path": video_path,
                "error": str(e)
            })
            raise VSSMediaUploadError(f"Error accessing video file: {video_path} - {str(e)}")
            
        media_id = self._upload_media(
            session=session,
            file_path=video_path,
            media_type='video',
            content_type='video/mp4'
        )
        
        if media_id is None:
            raise VSSMediaUploadError(f"Failed to upload video after multiple attempts: {video_path}")
            
        return media_id
        
    def _upload_media(
        self, 
        session: requests.Session, 
        file_path: str, 
        media_type: str,
        content_type: str
    ) -> Optional[str]:
        """
        Generic media upload method with retry logic.
        
        Args:
            session: The active requests session
            file_path: Path to the media file
            media_type: Type of media ('video')
            content_type: MIME type of the media
            
        Returns:
            The media ID if successful, None otherwise
        """
        max_retries = 3
        retry_delay = 2  # Initial delay in seconds
        last_error = None
        
        for attempt in range(max_retries):
            try:
                base_filename = os.path.basename(file_path)
                with open(file_path, 'rb') as file_object:
                    file_data = file_object.read()
                
                self.logger.debug("Uploading media", extra={
                    "media_type": media_type,
                    "media_filename": base_filename,
                    "file_size": len(file_data),
                    "attempt": attempt + 1,
                    "max_attempts": max_retries
                })
                
                files = {
                    'file': (base_filename, file_data, content_type),
                    'purpose': (None, 'vision'),
                    'media_type': (None, media_type)
                }
                
                response = session.post(
                    f"{self.base_url}{self.upload_endpoint}", 
                    files=files,
                    timeout=120  # 2 minute timeout for uploads
                )
                response.raise_for_status()
                result = response.json()
                
                media_id = result.get("id")
                self.logger.info("Media uploaded successfully", extra={
                    "media_type": media_type,
                    "media_id": media_id,
                    "media_filename": base_filename
                })
                return media_id
                
            except (requests.RequestException, IOError) as e:
                last_error = e
                self.logger.error("Media upload failed", extra={
                    "media_type": media_type,
                    "media_filename": os.path.basename(file_path),
                    "attempt": attempt + 1,
                    "max_attempts": max_retries,
                    "error": str(e),
                    "error_type": type(e).__name__
                })
                
                if isinstance(e, requests.HTTPError) and hasattr(e, 'response'):
                    self.logger.error("Upload response details", extra={
                        "status_code": e.response.status_code,
                        "response_text": e.response.text[:500] if hasattr(e.response, 'text') else None
                    })
                
                # Only wait if we're going to retry
                if attempt < max_retries - 1:
                    wait_time = retry_delay * (2 ** attempt)  # Exponential backoff
                    self.logger.debug("Retrying upload", extra={
                        "wait_time": wait_time
                    })
                    time.sleep(wait_time)
                    
        # All retries failed - log the final error
        if last_error:
            self.logger.error("All upload attempts failed", extra={
                "file_path": file_path,
                "final_error": str(last_error)
            })
        return None 