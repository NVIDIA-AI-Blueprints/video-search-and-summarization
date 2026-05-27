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
BDD Test: Download Recent Video

This test validates downloading recent video clips (relative to current time)
from the VST storage API. It supports:
- Downloading clips ending at current time minus an offset
- Configurable duration
- Optional transcode with overlay
- Parallel downloads
- Mediainfo validation

Usage:
  pytest tests/test_download_recent_video.py -v
  pytest tests/test_download_recent_video.py -v -k "transcode"
"""

import asyncio
import gc
import json
import logging
import subprocess
import time
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import List, Dict, Any, Optional
from urllib.parse import urlencode, quote

import aiohttp
import pytest
import requests
from pytest_bdd import scenarios, given, when, then

from .download_test_utils import create_test_video_file, envoy_streamid_route_key
from ..test_utils import assert_with_detailed_failure, format_validation_failure

logger = logging.getLogger(__name__)

# Load scenarios from the feature file
scenarios('../../features/file_download/download_recent_video.feature')


# API Endpoints for this test
ENDPOINTS = {
    'streams': '/vst/api/v1/replay/streams',
    'storage_file': '/vst/api/v1/storage/file/{stream_id}'
}


class RecentDownloadContext:
    """Context to store test data between steps."""
    def __init__(self):
        self.streams: List[str] = []
        self.test_data: List[Dict[str, Any]] = []
        self.videos: List[Dict[str, Any]] = []
        self.validation_results: List[Dict[str, Any]] = []
        self.use_transcode: bool = False
        self.uploaded_stream_ids: set = set()  # Track uploaded streams for cleanup
        self.temp_upload_dir: Path = None  # Temp dir for test video files
        self.uploaded_videos: List[Dict[str, Any]] = []  # Track uploaded video details (streamId, timestamp, duration)
        self.sensor_id: Optional[str] = None  # Same as PUT sensorId; Envoy Redis routes on this, not composite stream ids


def get_current_time_ms() -> int:
    """Get current time in milliseconds since epoch."""
    return int(time.time() * 1000)


def ms_to_iso8601(timestamp_ms: int) -> str:
    """Convert milliseconds timestamp to ISO 8601 format with milliseconds."""
    timestamp_sec = timestamp_ms / 1000.0
    dt = datetime.fromtimestamp(timestamp_sec, tz=timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S") + f".{timestamp_ms % 1000:03d}Z"


@pytest.fixture
def context():
    """Create a test context."""
    return RecentDownloadContext()


@pytest.fixture
def test_endpoints():
    """Get test endpoints configuration."""
    return ENDPOINTS


# test_params, test_endpoints, and setup_and_cleanup_temp_dir fixtures provided by conftest.py


# ============================================================================
# GIVEN Steps
# ============================================================================

@given('the VST API is configured for recent download')
def vst_api_configured_recent(api_config, test_endpoints):
    """Verify VST API configuration is available."""
    assert api_config['base_url'], "Base URL must be configured"
    assert test_endpoints['streams'], "Streams endpoint must be configured"
    assert test_endpoints['storage_file'], "Storage file endpoint must be configured"


@given('test videos are uploaded to create streams for download')
def upload_test_videos_for_recent_download(context, api_config, test_params):
    """Upload test videos to ensure streams exist for recent download testing."""
    sensor_id = f"test_upload_{uuid.uuid4()}"
    context.sensor_id = sensor_id
    num_videos = 2  # Upload 2 test videos
    video_durations = [45, 90]  # 45 seconds and 1.5 minutes
    
    # Calculate timestamp that will allow the video to be in the "recent" time range
    # The test will request: (NOW - offset) to (NOW - offset - duration)
    # So we upload with timestamp: (NOW - offset - duration/2) to ensure it's in range
    offset_ms = test_params.get('offset_ms', 5000)
    duration_ms = test_params.get('duration_ms', 30000)
    
    # Upload with timestamp a bit in the past so it falls within the "recent" time range
    # Use the middle of what the test will request
    upload_time_ms = get_current_time_ms() - offset_ms - (duration_ms // 2)
    upload_timestamp = ms_to_iso8601(upload_time_ms)
    
    logger.info("Uploading %d test video(s) with timestamp %s for recent download testing", 
                num_videos, upload_timestamp)
    
    for i, duration in enumerate(video_durations):
        filename = f"test_upload_recent_download_{uuid.uuid4().hex[:8]}.mp4"
        file_path = context.temp_upload_dir / filename
        
        # Create test video file
        create_test_video_file(file_path, duration_seconds=duration)
        
        # Upload the video with specific timestamp
        url = f"{api_config['base_url']}/vst/api/v1/storage/file/{filename}"
        params = {
            'timestamp': upload_timestamp,
            'sensorId': sensor_id
        }
        
        try:
            with open(file_path, 'rb') as f:
                file_content = f.read()
            
            response = requests.put(
                url,
                params=params,
                data=file_content,
                headers={'Content-Type': 'application/octet-stream'},
                timeout=120,
                verify=api_config.get('verify_ssl', False)
            )
            
            response_data = None
            try:
                response_data = response.json()
            except Exception:
                pass
            
            if response.status_code in [200, 201] and response_data and response_data.get('streamId'):
                stream_id = response_data['streamId']
                context.uploaded_stream_ids.add(stream_id)
                
                # Track uploaded video details for download testing
                context.uploaded_videos.append({
                    'streamId': stream_id,
                    'filename': filename,
                    'timestamp': upload_timestamp,
                    'duration': duration,
                    'upload_time_ms': upload_time_ms
                })
                
                logger.info("Uploaded test video %d/%d: %s (streamId: %s, duration: %ds, timestamp: %s)", 
                           i + 1, num_videos, filename, stream_id, duration, upload_timestamp)
            else:
                logger.error("Failed to upload test video - status: %d", response.status_code)
                raise AssertionError(f"Failed to upload test video for download testing")
                
        except Exception as e:
            logger.error("Upload failed with exception: %s", str(e))
            raise AssertionError(f"Failed to upload test video: {e}")
    
    logger.info("Successfully uploaded %d test video(s), %d streamId(s) tracked for cleanup",
                num_videos, len(context.uploaded_stream_ids))


# ============================================================================
# WHEN Steps
# ============================================================================

@when('the list of available streams is fetched for recent download')
def fetch_streams_recent(context, api_config, test_endpoints, test_params):
    """Fetch the list of available streams from the API."""
    # Use only the uploaded test streams (not all streams on server)
    context.streams = [video['streamId'] for video in context.uploaded_videos]
    
    assert len(context.streams) > 0, "No uploaded test streams found"
    logger.info("Using %d uploaded test stream(s): %s", len(context.streams), context.streams)


@when('recent time ranges are calculated based on offset and duration')
def calculate_recent_time_ranges(context, test_params):
    """Calculate time ranges for recent video download based on uploaded videos."""
    duration_ms = test_params.get('duration_ms', 30000)
    
    test_data = []
    
    # Use the actual timestamp ranges from the uploaded videos
    for video in context.uploaded_videos:
        stream_id = video['streamId']
        upload_time_ms = video['upload_time_ms']
        video_duration_seconds = video['duration']
        
        # Download a clip from the middle of the uploaded video
        # Start time: upload timestamp
        # End time: upload timestamp + min(duration_ms, video_duration * 1000)
        start_ms = upload_time_ms
        end_ms = start_ms + min(duration_ms, video_duration_seconds * 1000)
        
        test_data.append({
            'stream_id': stream_id,
            'start_time': ms_to_iso8601(start_ms),
            'end_time': ms_to_iso8601(end_ms),
            'start_ms': start_ms,
            'end_ms': end_ms
        })
        
        logger.info("Stream %s: %s to %s (duration: %dms)", 
                   stream_id, 
                   ms_to_iso8601(start_ms), 
                   ms_to_iso8601(end_ms),
                   end_ms - start_ms)
    
    context.test_data = test_data
    assert len(context.test_data) > 0, "No test data generated"


# ============================================================================
# THEN Steps
# ============================================================================

@then('recent videos for each stream are downloaded in parallel')
def download_recent_videos_parallel(context, api_config, test_endpoints, test_params):
    """Download recent videos in parallel. Transcode is controlled by config 'enable_transcode'."""
    _download_videos(context, api_config, test_endpoints, test_params)


def _download_videos(context, api_config, test_endpoints, test_params):
    """Internal function to download videos. Transcode controlled by config 'enable_transcode'."""
    offset_ms = test_params.get('offset_ms', 0)
    duration_ms = test_params.get('duration_ms', 30000)
    
    # Get transcode configuration from config
    enable_transcode = test_params.get('enable_transcode', False)
    transcode_config = test_params.get('transcode_config', {})
    disable_audio = test_params.get('disable_audio', True)
    
    logger.info("Transcode mode: %s (from config)", enable_transcode)
    
    # Envoy Lua uses streamid header for Redis (ENVOYROUTEHEADER); WDM keys are sensor id, not
    # composite sub-stream ids (sensor_filename). Path must still use API streamId per stream.
    streamid_for_envoy = getattr(context, "sensor_id", None) or ""

    # Get temp directory for streaming downloads
    temp_dir = Path(test_params.get('temp_download_dir', '/tmp/vst_test_videos'))
    temp_dir.mkdir(parents=True, exist_ok=True)
    
    async def download_video(session: aiohttp.ClientSession, stream_id: str,
                            start_time: str, end_time: str, index: int,
                            transcode: bool) -> Dict[str, Any]:
        """Download a single video asynchronously, streaming directly to disk."""
        base_url = f"{api_config['base_url']}{test_endpoints['storage_file'].format(stream_id=stream_id)}"
        
        # Build URL manually (same approach as test_recent_download.sh)
        query_parts = [
            f"startTime={start_time}",
            f"endTime={end_time}",
            f"container=mp4"
        ]
        
        if transcode:
            if disable_audio:
                query_parts.append("disableAudio=true")
            
            # Only add configuration if transcode_config is provided in config.json
            if transcode_config:
                # Build JSON exactly like bash script (no spaces, compact)
                config_str = json.dumps(transcode_config, separators=(',', ':'))
                # URL-encode the entire JSON string (like jq -sRr @uri)
                # quote with safe='' encodes ALL characters including : as %3A
                config_encoded = quote(config_str, safe='')
                query_parts.append(f"configuration={config_encoded}")
                
                logger.debug("Transcode config: %s", config_str)
            else:
                logger.warning("Transcode enabled but no transcode_config provided in config.json")
        
        query_string = '&'.join(query_parts)
        url = f"{base_url}?{query_string}"
        
        logger.debug("Request URL: %s", url)
        
        # Create temp file for streaming download
        temp_file = temp_dir / f"recent_{stream_id}_{index}.mp4"
        
        try:
            download_timeout = test_params.get('download_timeout', 120)
            route_key = streamid_for_envoy if streamid_for_envoy else envoy_streamid_route_key(stream_id)
            async with session.get(
                url,
                headers={"streamid": route_key},
                timeout=aiohttp.ClientTimeout(total=download_timeout),
                ssl=api_config.get('verify_ssl', False)
            ) as response:
                response.raise_for_status()
                
                # Stream directly to disk - don't load into memory
                file_size = 0
                with open(temp_file, 'wb') as f:
                    async for chunk in response.content.iter_chunked(64 * 1024):  # 64KB chunks
                        f.write(chunk)
                        file_size += len(chunk)
                
                logger.info("Downloaded video for stream %s (%d bytes) -> %s", 
                           stream_id, file_size, temp_file)
                
                return {
                    'index': index,
                    'stream_id': stream_id,
                    'start_time': start_time,
                    'end_time': end_time,
                    'file_path': str(temp_file),  # Store file path, not content
                    'file_size': file_size,
                    'status': response.status,
                    'success': True,
                    'error': None,
                    'transcode': transcode
                }
        except Exception as e:
            logger.error("Failed to download video for stream %s: %s", stream_id, e)
            # Clean up partial file on error
            if temp_file.exists():
                temp_file.unlink()
            return {
                'index': index,
                'stream_id': stream_id,
                'start_time': start_time,
                'end_time': end_time,
                'file_path': None,
                'file_size': 0,
                'status': None,
                'success': False,
                'error': str(e),
                'transcode': transcode
            }
    
    async def download_batch(batch: List[Dict[str, Any]], transcode: bool) -> List[Dict[str, Any]]:
        """Download a batch of videos in parallel."""
        connector = aiohttp.TCPConnector(ssl=api_config.get('verify_ssl', False))
        async with aiohttp.ClientSession(connector=connector) as session:
            tasks = [
                download_video(
                    session,
                    item['stream_id'],
                    item['start_time'],
                    item['end_time'],
                    item['index'],
                    transcode
                )
                for item in batch
            ]
            return await asyncio.gather(*tasks)
    
    # Generate time ranges for recent video download
    logger.info("Downloading recent videos for %d stream(s) (transcode=%s)", 
                len(context.streams), enable_transcode)
    
    test_data = []
    for i, stream_id in enumerate(context.streams):
        now_ms = get_current_time_ms()
        end_ms = now_ms - offset_ms
        start_ms = end_ms - duration_ms
        
        test_data.append({
            'stream_id': stream_id,
            'start_time': ms_to_iso8601(start_ms),
            'end_time': ms_to_iso8601(end_ms),
            'index': i
        })
    
    if not test_data:
        raise AssertionError("No test data generated")
    
    wait_sec = float(test_params.get('post_upload_wait_sec', 0))
    if wait_sec > 0:
        logger.info(
            "Waiting %.1fs after upload before storage GET (post_upload_wait_sec)",
            wait_sec,
        )
        time.sleep(wait_sec)
    
    # Download all videos in parallel
    results = asyncio.run(download_batch(test_data, enable_transcode))
    context.videos = results
    context.use_transcode = enable_transcode
    
    # Count successful downloads
    successful = sum(1 for v in context.videos if v['success'])
    logger.info("Successfully downloaded %d/%d videos (transcode=%s)", 
                successful, len(context.videos), enable_transcode)


@then('all downloaded recent videos are valid media files')
def validate_recent_videos(context, test_params):
    """Validate that all downloaded recent videos are valid media files using mediainfo."""
    temp_dir = Path(test_params.get('temp_download_dir', '/tmp/vst_test_downloads'))
    keep_videos = test_params.get('keep_downloaded_videos', False)
    
    # Debug: Print the keep_videos setting
    logger.info("keep_downloaded_videos config value: %s (type: %s)", keep_videos, type(keep_videos).__name__)
    logger.info("temp_download_dir: %s", temp_dir)
    
    if keep_videos:
        logger.info("Videos WILL BE KEPT in: %s", temp_dir)
    else:
        logger.info("Videos WILL BE DELETED after validation")
    
    validation_results = []
    
    for video_data in context.videos:
        if not video_data['success']:
            validation_results.append({
                'index': video_data['index'],
                'stream_id': video_data['stream_id'],
                'start_time': video_data['start_time'],
                'end_time': video_data['end_time'],
                'valid': False,
                'error': f"Failed to download: {video_data['error']}"
            })
            continue
        
        # Video already downloaded to disk - get the file path
        temp_file = Path(video_data['file_path'])
        file_size = video_data.get('file_size', 0)
        
        if not temp_file.exists():
            validation_results.append({
                'index': video_data['index'],
                'stream_id': video_data['stream_id'],
                'start_time': video_data['start_time'],
                'end_time': video_data['end_time'],
                'valid': False,
                'error': f"Downloaded file not found: {temp_file}"
            })
            continue
        
        try:
            # Run mediainfo to validate
            result = subprocess.run(
                ['mediainfo', str(temp_file)],
                capture_output=True,
                text=True,
                timeout=30
            )
            
            # Check if mediainfo output contains expected video information
            output = result.stdout
            is_valid = (
                result.returncode == 0 and
                'Video' in output and
                ('Format' in output or 'Codec' in output)
            )
            
            # Print mediainfo summary
            if is_valid:
                lines = output.split('\n')
                summary = []
                for line in lines[:20]:
                    if any(keyword in line for keyword in 
                          ['Format', 'Codec', 'Duration', 'Bit rate', 'Width', 'Height', 'Frame rate']):
                        summary.append(line.strip())
                
                summary_str = ' | '.join(summary[:5]) if summary else output[:200]
                logger.info("  [%s] mediainfo: %s", video_data['stream_id'], summary_str)
            else:
                logger.warning("  [%s] mediainfo: INVALID", video_data['stream_id'])
            
            validation_results.append({
                'index': video_data['index'],
                'stream_id': video_data['stream_id'],
                'start_time': video_data['start_time'],
                'end_time': video_data['end_time'],
                'valid': is_valid,
                'mediainfo_output': output,
                'file_size': file_size,
                'error': None if is_valid else 'Invalid video format'
            })
            
        except subprocess.TimeoutExpired:
            logger.warning("  [%s] mediainfo: TIMEOUT", video_data['stream_id'])
            validation_results.append({
                'index': video_data['index'],
                'stream_id': video_data['stream_id'],
                'start_time': video_data['start_time'],
                'end_time': video_data['end_time'],
                'valid': False,
                'error': 'mediainfo timeout'
            })
        except FileNotFoundError:
            logger.warning("  [%s] mediainfo: NOT INSTALLED (skipping validation)", video_data['stream_id'])
            # If mediainfo is not installed, consider file valid if it has content
            validation_results.append({
                'index': video_data['index'],
                'stream_id': video_data['stream_id'],
                'start_time': video_data['start_time'],
                'end_time': video_data['end_time'],
                'valid': file_size > 0,
                'file_size': file_size,
                'error': None if file_size > 0 else 'Empty file'
            })
        except Exception as e:
            logger.error("  [%s] mediainfo: ERROR - %s", video_data['stream_id'], e)
            validation_results.append({
                'index': video_data['index'],
                'stream_id': video_data['stream_id'],
                'start_time': video_data['start_time'],
                'end_time': video_data['end_time'],
                'valid': False,
                'error': str(e)
            })
        finally:
            # Clean up temporary file (unless keep_downloaded_videos is enabled)
            if temp_file.exists():
                if keep_videos:
                    logger.info("  Keeping video file: %s", temp_file)
                else:
                    logger.info("  Deleting video file: %s", temp_file)
                    temp_file.unlink()
    
    context.validation_results = validation_results
    
    # Force garbage collection to reclaim memory from video content
    gc.collect()
    
    # Log validation summary
    valid_count = sum(1 for r in validation_results if r['valid'])
    total_count = len(validation_results)
    
    logger.info("Validation Results: %d/%d recent videos are valid", valid_count, total_count)
    
    # Log details of invalid videos
    invalid_videos = [r for r in validation_results if not r['valid']]
    if invalid_videos:
        logger.warning("Invalid videos:")
        for result in invalid_videos:
            logger.warning("  - Stream: %s, Time: %s to %s, Error: %s",
                          result['stream_id'], result['start_time'], result['end_time'], result['error'])
    
    # Assert with detailed error reporting
    if valid_count != total_count:
        failure_info = format_validation_failure(valid_count, total_count, invalid_videos, "recent video")
        assert_with_detailed_failure(
            False,
            "Recent Video Download Validation",
            failure_info['expected'],
            failure_info['actual'],
            failure_info['failed_items'],
            f"Check mediainfo and transcode output above.\n"
            f"  Downloaded recent videos: {total_count}\n"
            f"  Valid: {valid_count}\n"
            f"  Invalid: {total_count - valid_count}\n"
            f"  Transcode enabled: {test_params.get('enable_transcode', False)}"
        )
    
    # Clear video list to free memory
    context.videos = []

