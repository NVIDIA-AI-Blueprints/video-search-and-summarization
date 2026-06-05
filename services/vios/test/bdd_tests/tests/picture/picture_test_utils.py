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
Shared utilities for picture BDD tests.
"""
import asyncio
import hashlib
import logging
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict, Any

import aiohttp
import requests

logger = logging.getLogger(__name__)

# API Endpoints
ENDPOINTS_LIVE = {
    'streams': '/vst/api/v1/live/streams',
    'picture': '/vst/api/v1/live/stream/{stream_id}/picture'
}

ENDPOINTS_REPLAY = {
    'streams': '/vst/api/v1/replay/streams',
    'timelines': '/vst/api/v1/storage/timelines',
    'storage_size': '/vst/api/v1/storage/size',
    'picture': '/vst/api/v1/replay/stream/{stream_id}/picture',
    'sensor_delete': '/vst/api/v1/sensor/{sensor_id}'
}

ENDPOINTS_STORAGE = {
    'streams': '/vst/api/v1/storage/streams',
    'timelines': '/vst/api/v1/storage/timelines',
    'storage_size': '/vst/api/v1/storage/size',
    'picture': '/vst/api/v1/storage/stream/{stream_id}/picture',
    'picture_url': '/vst/api/v1/storage/stream/{stream_id}/picture/url',
    'sensor_delete': '/vst/api/v1/sensor/{sensor_id}'
}


class PictureContext:
    """Base context to store test data between picture test steps."""
    def __init__(self):
        self.streams: List[Dict[str, Any]] = []
        self.timelines: Dict[str, Any] = {}
        self.test_data: List[Dict[str, Any]] = []
        self.pictures: List[Dict[str, Any]] = []
        self.validation_results: List[Dict[str, Any]] = []
        self.deleted_sensor_id: str = ""


def filter_streams_by_codec(streams: List[Dict[str, Any]], codec: str) -> List[Dict[str, Any]]:
    """
    Filter streams list to only include streams matching the given codec.

    Each stream object is a dict like {"stream_name": [{"metadata": {"codec": "H265"}, ...}]}.
    """
    filtered = []
    for stream_obj in streams:
        if not isinstance(stream_obj, dict):
            continue
        for stream_name, stream_details in stream_obj.items():
            if not isinstance(stream_details, list):
                continue
            if any(
                detail.get("metadata", {}).get("codec", "").upper() == codec.upper()
                for detail in stream_details
            ):
                filtered.append(stream_obj)
                break
    return filtered


def delete_sensor(api_base_url: str, endpoint: str, sensor_id: str,
                  timeout: int, verify_ssl: bool) -> int:
    """Delete a sensor via the sensor management API. Returns status code."""
    url = f"{api_base_url}{endpoint.format(sensor_id=sensor_id)}"
    response = requests.delete(url, timeout=timeout, verify=verify_ssl)
    response.raise_for_status()
    logger.info("Deleted sensor %s (status: %d)", sensor_id, response.status_code)
    return response.status_code


def fetch_streams(api_base_url: str, endpoint: str, timeout: int, verify_ssl: bool) -> List[Dict[str, Any]]:
    """Fetch available streams from the API."""
    url = f"{api_base_url}{endpoint}"
    
    response = requests.get(
        url,
        timeout=timeout,
        verify=verify_ssl
    )
    response.raise_for_status()
    
    streams = response.json()
    logger.info("Fetched %d stream(s)", len(streams))
    return streams


def fetch_timelines(api_base_url: str, endpoint: str, timeout: int, verify_ssl: bool) -> Dict[str, Any]:
    """Fetch recording timelines from the storage API."""
    url = f"{api_base_url}{endpoint}"
    
    response = requests.get(
        url,
        timeout=timeout,
        verify=verify_ssl
    )
    response.raise_for_status()
    
    timelines = response.json()
    logger.info("Fetched timelines for %d stream(s)", len(timelines))
    return timelines


def select_timestamps_from_timelines(streams: List[Dict[str, Any]], 
                                    timelines: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Select valid timestamps from timeline data for picture testing.
    
    Returns a list of test data with stream_id and timestamp.
    """
    test_data = []
    
    stream_names = []
    for stream_obj in streams:
        if isinstance(stream_obj, dict):
            for stream_name in stream_obj.keys():
                stream_names.append(stream_name)
    
    for stream_name in stream_names:
        stream_timeline_data = timelines.get(stream_name)
        
        if not stream_timeline_data or not isinstance(stream_timeline_data, list):
            continue
        
        timeline_list = stream_timeline_data
        
        if not isinstance(timeline_list, list) or len(timeline_list) == 0:
            continue
        
        timeline = timeline_list[0]
        start_time_str = timeline.get('startTime')
        end_time_str = timeline.get('endTime')
        
        if not start_time_str or not end_time_str:
            continue
        
        try:
            start_time = datetime.fromisoformat(start_time_str.replace('Z', '+00:00'))
            end_time = datetime.fromisoformat(end_time_str.replace('Z', '+00:00'))
            
            middle_time = start_time + (end_time - start_time) / 2
            
            test_data.append({
                'stream_id': stream_name,
                'timestamp': middle_time.strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'
            })
            
            break
        except (ValueError, AttributeError):
            continue
    
    logger.info("Generated %d test case(s)", len(test_data))
    return test_data


def validate_jpeg_with_jpeginfo(image_path: Path) -> Dict[str, Any]:
    """
    Validate a JPEG image file using jpeginfo.
    
    Returns dict with 'valid', 'error', 'summary' keys.
    """
    try:
        result = subprocess.run(
            ['jpeginfo', '-c', str(image_path)],
            capture_output=True,
            text=True,
            timeout=10
        )
        
        output = result.stdout + result.stderr
        is_valid = result.returncode == 0 and 'ERROR' not in output
        
        if is_valid:
            summary = f"Valid JPEG ({image_path.stat().st_size} bytes)"
        else:
            summary = output.strip()[:100]
        
        return {
            'valid': is_valid,
            'error': None if is_valid else 'Invalid JPEG format',
            'summary': summary
        }
        
    except subprocess.TimeoutExpired:
        return {
            'valid': False,
            'error': 'jpeginfo timeout',
            'summary': ''
        }
    except FileNotFoundError:
        return {
            'valid': False,
            'error': 'jpeginfo not found - install with: apt-get install jpeginfo',
            'summary': ''
        }
    except Exception as e:
        return {
            'valid': False,
            'error': str(e),
            'summary': ''
        }


async def fetch_picture_async(session: aiohttp.ClientSession,
                              url: str,
                              stream_id: str,
                              index: int,
                              timeout: int) -> Dict[str, Any]:
    """Fetch a single picture asynchronously."""
    try:
        async with session.get(
            url,
            timeout=aiohttp.ClientTimeout(total=timeout),
            ssl=False
        ) as response:
            response.raise_for_status()
            content = await response.read()
            
            return {
                'index': index,
                'stream_id': stream_id,
                'content': content,
                'status': response.status,
                'success': True,
                'error': None
            }
    except Exception as e:
        return {
            'index': index,
            'stream_id': stream_id,
            'content': None,
            'status': None,
            'success': False,
            'error': str(e)
        }
