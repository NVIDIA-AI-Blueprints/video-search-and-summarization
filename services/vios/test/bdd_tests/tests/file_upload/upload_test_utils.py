# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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
Shared utilities for file upload BDD tests.
"""
import json
import logging
import subprocess
import uuid
from pathlib import Path
from typing import Dict, Any

import requests

logger = logging.getLogger(__name__)


class UploadContext:
    """Base context to store test data between steps."""
    def __init__(self):
        self.test_files = []
        self.upload_results = []
        self.temp_dir = None
        self.sensor_id = f"test_upload_{uuid.uuid4()}"
        self.upload_response = None
        self.second_upload_response = None
        self.uploaded_stream_ids = set()  # Track all successfully uploaded streamIds for cleanup
        # Sensors created out-of-band (e.g. via /sensor/add, not via PUT upload).
        # The autouse cleanup fixture in conftest.py will DELETE each of these
        # to keep tests self-healing regardless of pass/fail.
        self.created_sensor_ids = set()


def create_test_video_file(file_path: Path, duration_seconds: int = 2, fps: int = 30) -> None:
    """
    Create a valid MP4 test file using ffmpeg with H.264 codec.
    This ensures compatibility with the VST system's media validation.
    """
    width, height = 640, 480
    
    try:
        ffmpeg_cmd = [
            'ffmpeg',
            '-f', 'lavfi',
            '-i', f'color=c=blue:s={width}x{height}:d={duration_seconds}:r={fps}',
            '-pix_fmt', 'yuv420p',
            '-c:v', 'libx264',
            '-profile:v', 'baseline',
            '-level', '3.0',
            '-preset', 'ultrafast',
            '-movflags', '+faststart'
        ]
        
        if not file_path.suffix:
            ffmpeg_cmd.extend(['-f', 'mp4'])
        
        ffmpeg_cmd.extend(['-y', str(file_path)])
        
        result = subprocess.run(
            ffmpeg_cmd,
            capture_output=True,
            timeout=30,
            check=True
        )
        
        if not file_path.exists() or file_path.stat().st_size == 0:
            raise RuntimeError("Video file was not created or is empty")
            
        logger.info("Created valid H.264 MP4 file using ffmpeg: %s (%d bytes)", 
                   file_path.name, file_path.stat().st_size)
            
    except FileNotFoundError:
        logger.error("ffmpeg not found - please install ffmpeg")
        raise RuntimeError("ffmpeg is required to create test video files. Install with: apt-get install ffmpeg")
    except subprocess.CalledProcessError as e:
        logger.error("ffmpeg failed: %s", e.stderr.decode() if e.stderr else str(e))
        raise RuntimeError(f"ffmpeg failed to create video: {e}")
    except subprocess.TimeoutExpired:
        logger.error("ffmpeg timed out while creating video")
        raise RuntimeError("ffmpeg timed out")


def upload_file_sync(api_base_url: str, file_path: Path, filename: str, 
                     sensor_id: str, verify_ssl: bool, thread_id: int,
                     timestamp: str = None) -> Dict[str, Any]:
    """
    Upload a file using the new PUT API (synchronous version for threading).
    
    New PUT API: PUT /api/v1/storage/file/{filename}?timestamp=<>&sensorId=<>
    """
    url = f"{api_base_url}/vst/api/v1/storage/file/{filename}"
    params = {'sensorId': sensor_id}
    
    if timestamp:
        params['timestamp'] = timestamp
    else:
        params['timestamp'] = '2025-01-01T00:00:00.000Z'
    
    try:
        with open(file_path, 'rb') as f:
            file_content = f.read()
        
        logger.info("[Thread %d] Uploading %s (%d bytes)", thread_id, filename, len(file_content))
        
        response = requests.put(
            url,
            params=params,
            data=file_content,
            headers={'Content-Type': 'application/octet-stream'},
            timeout=30,
            verify=verify_ssl
        )
        
        response_data = None
        content_type = response.headers.get('content-type', '')
        
        try:
            response_data = response.json()
        except Exception as e:
            logger.warning("[Thread %d] Failed to parse response as JSON: %s. Content-Type: %s", 
                          thread_id, e, content_type)
            if response.status_code in [200, 201]:
                logger.warning("[Thread %d] Response text: %s", thread_id, response.text[:200])
        
        result = {
            'thread_id': thread_id,
            'filename': filename,
            'status_code': response.status_code,
            'success': response.status_code in [200, 201],
            'conflict': response.status_code == 409,
            'response_json': response_data,
            'streamId': response_data.get('streamId') if response_data else None,
            'sensorId': response_data.get('sensorId') if response_data else None,
            'error': None
        }
        
        if result['success']:
            logger.info("[Thread %d] Upload result: %d SUCCESS - streamId: %s, sensorId: %s", 
                       thread_id, response.status_code, 
                       result['streamId'], result['sensorId'])
        else:
            logger.info("[Thread %d] Upload result: %d %s", 
                       thread_id, response.status_code, 
                       "CONFLICT" if result['conflict'] else "ERROR")
        
        return result
        
    except Exception as e:
        logger.error("[Thread %d] Upload failed with exception: %s", thread_id, str(e))
        return {
            'thread_id': thread_id,
            'filename': filename,
            'status_code': None,
            'success': False,
            'conflict': False,
            'response_json': None,
            'error': str(e)
        }


def upload_file_simple(api_base_url: str, file_path: Path, filename: str,
                      sensor_id: str = None, timestamp: str = None,
                      verify_ssl: bool = False) -> requests.Response:
    """
    Simple file upload helper for single uploads.
    
    Returns the response object directly.
    """
    url = f"{api_base_url}/vst/api/v1/storage/file/{filename}"
    params = {}
    
    if sensor_id:
        params['sensorId'] = sensor_id
    if timestamp:
        params['timestamp'] = timestamp
    
    with open(file_path, 'rb') as f:
        response = requests.put(
            url,
            params=params,
            data=f.read(),
            headers={'Content-Type': 'application/octet-stream'},
            timeout=30,
            verify=verify_ssl
        )
    
    logger.info("Upload %s: status %d", filename, response.status_code)
    return response


def upload_bframe_multipart(api_base_url: str, bframe_video_path: Path, filename: str,
                           sensor_id: str, verify_ssl: bool = False) -> Dict[str, Any]:
    """
    Upload a B-frame video using multipart POST with chunk headers.
    
    Args:
        api_base_url: Base API URL
        bframe_video_path: Path to B-frame video file
        filename: Filename to use for upload
        sensor_id: Sensor ID
        verify_ssl: Whether to verify SSL certificates
        
    Returns:
        Dict with upload result including streamId, fileId, sensorId
    """
    url = f"{api_base_url}/vst/api/v1/storage/file"
    
    metadata = {
        "timestamp": "2025-01-15T00:00:00.000Z",
        "sensorId": sensor_id
    }
    
    chunk_identifier = str(uuid.uuid4())
    
    headers = {
        'nvstreamer-chunk-number': '1',
        'nvstreamer-total-chunks': '1',
        'nvstreamer-is-last-chunk': 'true',
        'nvstreamer-identifier': chunk_identifier,
        'nvstreamer-file-name': filename
    }
    
    with open(bframe_video_path, 'rb') as f:
        files = {
            'mediaFile': (filename, f, 'application/octet-stream')
        }
        data = {
            'filename': filename,
            'metadata': json.dumps(metadata)
        }
        
        response = requests.post(
            url,
            files=files,
            data=data,
            headers=headers,
            timeout=30,
            verify=verify_ssl
        )
    
    response_data = None
    try:
        response_data = response.json()
    except Exception:
        pass
    
    return {
        'filename': filename,
        'status_code': response.status_code,
        'success': response.status_code in [200, 201],
        'response_json': response_data,
        'sensorId': response_data.get('sensorId') if response_data else None,
        'fileId': response_data.get('id') if response_data else None,
        'streamId': response_data.get('streamId') if response_data else None
    }


def upload_file_multipart(api_base_url: str, file_path: Path, filename: str,
                          sensor_id: str, timestamp: str, verify_ssl: bool) -> Dict[str, Any]:
    """
    Upload a file using multipart POST with chunking headers.
    
    Returns a dict with upload results including streamId for cleanup.
    """
    url = f"{api_base_url}/vst/api/v1/storage/file"
    
    metadata = {
        "timestamp": timestamp,
        "sensorId": sensor_id
    }
    
    chunk_identifier = str(uuid.uuid4())
    
    headers = {
        'nvstreamer-chunk-number': '1',
        'nvstreamer-total-chunks': '1',
        'nvstreamer-is-last-chunk': 'true',
        'nvstreamer-identifier': chunk_identifier,
        'nvstreamer-file-name': filename
    }
    
    try:
        with open(file_path, 'rb') as f:
            files = {
                'mediaFile': (filename, f, 'application/octet-stream')
            }
            data = {
                'filename': filename,
                'metadata': json.dumps(metadata)
            }
            
            response = requests.post(
                url,
                files=files,
                data=data,
                headers=headers,
                timeout=30,
                verify=verify_ssl
            )
        
        response_data = None
        try:
            response_data = response.json()
        except Exception:
            pass
        
        result = {
            'filename': filename,
            'status_code': response.status_code,
            'success': response.status_code in [200, 201],
            'response_json': response_data,
            'sensorId': response_data.get('sensorId') if response_data else None,
            'fileId': response_data.get('id') if response_data else None,
            'filePath': response_data.get('filePath') if response_data else None,
            'streamId': response_data.get('streamId') if response_data else None,
            'error': None
        }
        
        logger.info("Upload %s: status %d - sensorId: %s, streamId: %s, fileId: %s",
                   filename, response.status_code, result['sensorId'], 
                   result.get('streamId'), result['fileId'])
        
        return result
        
    except Exception as e:
        logger.error("Upload %s failed: %s", filename, str(e))
        return {
            'filename': filename,
            'status_code': None,
            'success': False,
            'response_json': None,
            'error': str(e)
        }
