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

import asyncio
import logging
import subprocess
import time
import uuid
from pathlib import Path
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta

import aiohttp
import pytest
import requests
from pytest_bdd import scenarios, given, when, then

from .download_test_utils import (
    create_test_video_file,
    upload_test_video,
    select_time_ranges_from_timelines,
    envoy_streamid_route_key,
)
from ..test_utils import assert_with_detailed_failure, format_validation_failure

logger = logging.getLogger(__name__)

# Load scenarios from the feature file
scenarios('../../features/file_download/download_video.feature')


# API Endpoints for this test
ENDPOINTS = {
    'streams': '/vst/api/v1/replay/streams',
    'timelines': '/vst/api/v1/storage/timelines',
    'storage_file': '/vst/api/v1/storage/file/{stream_id}'
}


class ScenarioContext:
    """Context to store test data between steps."""
    def __init__(self):
        self.streams: List[Dict[str, Any]] = []
        self.timelines: Dict[str, Any] = {}
        self.test_data: List[Dict[str, Any]] = []
        self.videos: List[Dict[str, Any]] = []
        self.validation_results: List[Dict[str, Any]] = []
        self.uploaded_stream_ids: set = set()  # Track uploaded streams for cleanup
        self.temp_upload_dir: Path = None  # Temp dir for test video files
        self.uploaded_videos: List[Dict[str, Any]] = []  # Track uploaded video details
        self.sensor_id: Optional[str] = None  # PUT sensorId; Envoy Redis routes on this, not composite stream ids


@pytest.fixture
def context():
    """Create a test context."""
    return ScenarioContext()


@pytest.fixture
def test_endpoints():
    """Get test endpoints configuration."""
    return ENDPOINTS


# test_params, test_endpoints, and setup_and_cleanup_temp_dir fixtures provided by conftest.py


@given('the VST API is configured')
def vst_api_configured(api_config, test_endpoints):
    """Verify VST API configuration is available."""
    assert api_config['base_url'], "Base URL must be configured"
    assert test_endpoints['streams'], "Streams endpoint must be configured"
    assert test_endpoints['timelines'], "Storage timelines endpoint must be configured"
    assert test_endpoints['storage_file'], "Storage file endpoint must be configured"


@given('test videos are uploaded to create streams')
def upload_test_videos_for_download(context, api_config):
    """Upload test videos to ensure streams exist for download testing."""
    sensor_id = f"test_upload_{uuid.uuid4()}"
    context.sensor_id = sensor_id
    num_videos = 2  # Upload 2 test videos with different durations
    video_durations = [60, 90]  # 1 minute and 1.5 minutes
    
    logger.info("Uploading %d test video(s) to create streams for download testing", num_videos)
    
    # Note: temp_upload_dir is initialized by setup_and_cleanup fixture in conftest.py
    for i, duration in enumerate(video_durations):
        filename = f"test_upload_download_{uuid.uuid4().hex[:8]}.mp4"
        file_path = context.temp_upload_dir / filename
        
        # Create test video file
        create_test_video_file(file_path, duration_seconds=duration)
        
        # Upload the video
        result = upload_test_video(
            api_config['base_url'],
            file_path,
            filename,
            sensor_id,
            api_config.get('verify_ssl', False)
        )
        
        if result['success'] and result['streamId']:
            stream_id = result['streamId']
            context.uploaded_stream_ids.add(stream_id)
            
            # Track uploaded video details for download testing
            context.uploaded_videos.append({
                'streamId': stream_id,
                'filename': filename,
                'duration': duration
            })
            
            logger.info("Uploaded test video %d/%d: %s (streamId: %s, duration: %ds)", 
                       i + 1, num_videos, filename, stream_id, duration)
        else:
            logger.error("Failed to upload test video: %s", result.get('error'))
            raise AssertionError(f"Failed to upload test video for download testing: {result.get('error')}")
    
    logger.info("Successfully uploaded %d test video(s), %d streamId(s) tracked for cleanup",
                num_videos, len(context.uploaded_stream_ids))


@when('the list of available streams is fetched')
def fetch_streams(context, api_config, test_endpoints, test_params):
    """Fetch the list of available streams from the API."""
    # Convert uploaded videos to the expected format for download tests
    # Format: [{"streamId": [...]}] - matches API response format
    context.streams = [{video['streamId']: []} for video in context.uploaded_videos]
    
    assert len(context.streams) > 0, "No uploaded test streams found"
    logger.info("Using %d uploaded test stream(s)", len(context.streams))


@when('the recording timelines with timeline data are fetched')
def fetch_timelines(context, api_config, test_endpoints, test_params):
    """Fetch recording timelines from the storage API."""
    url = f"{api_config['base_url']}{test_endpoints['timelines']}"
    
    response = requests.get(
        url,
        timeout=test_params['timeout'],
        verify=api_config.get('verify_ssl', False)
    )
    response.raise_for_status()
    
    all_timelines = response.json()
    
    # Filter to only include timelines for our uploaded test streams
    context.timelines = {
        stream_id: all_timelines[stream_id]
        for stream_id in context.uploaded_stream_ids
        if stream_id in all_timelines
    }
    
    assert context.timelines, "No timeline data found for uploaded test streams"
    logger.info("Found timelines for %d uploaded test stream(s)", len(context.timelines))


@when('valid time ranges from the timelines are selected')
def select_time_ranges(context, test_params):
    """Select valid time ranges from the timeline data for testing."""
    video_duration = test_params.get('video_duration_seconds', 10)
    
    test_data = select_time_ranges_from_timelines(
        context.streams,
        context.timelines,
        video_duration
    )
    
    context.test_data = test_data
    
    assert len(context.test_data) > 0, "No valid test data found"
    logger.info("Selected %d time range(s) for testing", len(context.test_data))


@then('videos for each stream and time range are downloaded in parallel')
def download_videos_parallel(context, api_config, test_endpoints, test_params):
    """Download videos in parallel."""
    streamid_for_envoy = getattr(context, "sensor_id", None) or ""

    wait_sec = float(test_params.get("post_upload_wait_sec", 0))
    if wait_sec > 0:
        logger.info(
            "Waiting %.1fs before storage GET (post_upload_wait_sec)",
            wait_sec,
        )
        time.sleep(wait_sec)

    async def download_video(session: aiohttp.ClientSession, stream_id: str, 
                            start_time: str, end_time: str, index: int) -> Dict[str, Any]:
        """Download a single video asynchronously."""
        url = f"{api_config['base_url']}{test_endpoints['storage_file'].format(stream_id=stream_id)}"
        params = {
            'startTime': start_time,
            'endTime': end_time,
            'container': 'mp4'
        }
        route_key = streamid_for_envoy if streamid_for_envoy else envoy_streamid_route_key(stream_id)

        try:
            async with session.get(
                url,
                params=params,
                headers={"streamid": route_key},
                timeout=aiohttp.ClientTimeout(total=test_params.get('download_timeout', 120)),
                ssl=api_config.get('verify_ssl', False)
            ) as response:
                response.raise_for_status()
                content = await response.read()
                
                return {
                    'index': index,
                    'stream_id': stream_id,
                    'start_time': start_time,
                    'end_time': end_time,
                    'content': content,
                    'status': response.status,
                    'success': True,
                    'error': None
                }
        except Exception as e:
            return {
                'index': index,
                'stream_id': stream_id,
                'start_time': start_time,
                'end_time': end_time,
                'content': None,
                'status': None,
                'success': False,
                'error': str(e)
            }
    
    async def download_batch(batch: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Download a batch of videos in parallel."""
        connector = aiohttp.TCPConnector(ssl=api_config.get('verify_ssl', False))
        async with aiohttp.ClientSession(connector=connector) as session:
            tasks = [
                download_video(
                    session,
                    item['stream_id'],
                    item['start_time'],
                    item['end_time'],
                    i
                )
                for i, item in enumerate(batch)
            ]
            return await asyncio.gather(*tasks)
    
    logger.info("Downloading %d video(s) in parallel", len(context.test_data))
    
    # Download all videos in parallel
    results = asyncio.run(download_batch(context.test_data))
    context.videos = results
    
    # Count successful downloads
    successful = sum(1 for v in context.videos if v['success'])
    logger.info("Successfully downloaded %d/%d videos", successful, len(context.videos))


@then('all downloaded videos are valid media files')
def validate_videos(context, test_params):
    """Validate that all downloaded videos are valid media files using mediainfo."""
    temp_dir = Path(test_params['temp_download_dir'])
    
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
        
        # Save video to temporary file
        temp_file = temp_dir / f"video_{video_data['index']}_{video_data['stream_id']}.mp4"
        
        try:
            with open(temp_file, 'wb') as f:
                f.write(video_data['content'])
            
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
            
            # Print mediainfo summary in verbose mode
            if is_valid:
                # Extract key info from mediainfo output
                lines = output.split('\n')
                summary = []
                for line in lines[:20]:  # First 20 lines usually have the summary
                    if any(keyword in line for keyword in ['Format', 'Codec', 'Duration', 'Bit rate', 'Width', 'Height', 'Frame rate']):
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
                'file_size': len(video_data['content']),
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
            # Clean up temporary file
            if temp_file.exists():
                temp_file.unlink()
    
    context.validation_results = validation_results
    
    # Log validation summary
    valid_count = sum(1 for r in validation_results if r['valid'])
    total_count = len(validation_results)
    
    logger.info("Validation Results: %d/%d videos are valid", valid_count, total_count)
    
    # Log details of invalid videos
    invalid_videos = [r for r in validation_results if not r['valid']]
    if invalid_videos:
        logger.warning("Invalid videos:")
        for result in invalid_videos:
            logger.warning("  - Stream: %s, Time: %s to %s, Error: %s",
                          result['stream_id'], result['start_time'], result['end_time'], result['error'])
    
    # Assert that all videos are valid with detailed error reporting
    if valid_count != total_count:
        failure_info = format_validation_failure(valid_count, total_count, invalid_videos, "video")
        assert_with_detailed_failure(
            False,
            "Video Download Validation",
            failure_info['expected'],
            failure_info['actual'],
            failure_info['failed_items'],
            f"Check mediainfo output above. Ensure videos are valid H.264/MP4 format.\n"
            f"  Downloaded videos: {total_count}\n"
            f"  Valid: {valid_count}\n"
            f"  Invalid: {total_count - valid_count}"
        )

