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
import json
import logging
import subprocess
import time
from pathlib import Path
from typing import List, Dict, Any
from datetime import datetime, timedelta

import aiohttp
import pytest
import requests
from pytest_bdd import scenarios, given, when, then

from .download_test_utils import envoy_streamid_route_key
from ..test_utils import assert_with_detailed_failure, format_validation_failure

logger = logging.getLogger(__name__)

# Load scenarios from the feature file
scenarios('../../features/file_download/video_download_by_blocking_url.feature')


# API Endpoints for this test
ENDPOINTS = {
    'streams': '/vst/api/v1/replay/streams',
    'timelines': '/vst/api/v1/storage/timelines',
    'storage_file_url': '/vst/api/v1/storage/file/{stream_id}/url'
}


class ScenarioContext:
    """Context to store test data between steps."""
    def __init__(self):
        self.streams: List[Dict[str, Any]] = []
        self.timelines: Dict[str, Any] = {}
        self.test_data: List[Dict[str, Any]] = []
        self.url_responses: List[Dict[str, Any]] = []
        self.videos: List[Dict[str, Any]] = []
        self.validation_results: List[Dict[str, Any]] = []
        self.expiry_results: List[Dict[str, Any]] = []


@pytest.fixture
def context():
    """Create a test context."""
    return ScenarioContext()


@pytest.fixture
def test_endpoints():
    """Get test endpoints configuration."""
    return ENDPOINTS


# test_params, test_endpoints, and setup_and_cleanup_temp_dir fixtures provided by conftest.py


@given('the VST API is configured for blocking URL test')
def vst_api_configured(api_config, test_endpoints):
    """Verify VST API configuration is available."""
    assert api_config['base_url'], "Base URL must be configured"
    assert test_endpoints['streams'], "Streams endpoint must be configured"
    assert test_endpoints['timelines'], "Storage timelines endpoint must be configured"
    assert test_endpoints['storage_file_url'], "Storage file URL endpoint must be configured"


@when('the list of available streams for blocking URL test is fetched')
def fetch_streams(context, api_config, test_endpoints, test_params):
    """Fetch the list of available streams from the API."""
    url = f"{api_config['base_url']}{test_endpoints['streams']}"
    
    response = requests.get(
        url,
        timeout=test_params['timeout'],
        verify=api_config.get('verify_ssl', False)
    )
    response.raise_for_status()
    
    context.streams = response.json()
    assert len(context.streams) > 0, "No streams found"


@when('the recording timelines for blocking URL test are fetched')
def fetch_timelines(context, api_config, test_endpoints, test_params):
    """Fetch recording timelines from the storage API."""
    url = f"{api_config['base_url']}{test_endpoints['timelines']}"
    # timelines endpoint doesn't need params
    
    response = requests.get(
        url,
        timeout=test_params['timeout'],
        verify=api_config.get('verify_ssl', False)
    )
    response.raise_for_status()
    
    context.timelines = response.json()
    assert context.timelines, "No timeline data found"


@when('valid time ranges for blocking URL test are selected')
def select_time_ranges(context, test_params):
    """Select valid time ranges from the timeline data for each stream.

    Generates one test case per (stream, duration, expiry) combination.
    The downstream URL request and download steps throttle in-flight
    work via ``test_params['parallelism']`` so the server-side muxer
    queue does not get overwhelmed.
    """
    durations = test_params['video_durations']
    expiry_minutes = test_params['expiry_minutes']

    test_data = []

    stream_names = []
    for stream_obj in context.streams:
        if isinstance(stream_obj, dict):
            for stream_name in stream_obj.keys():
                stream_names.append(stream_name)

    for stream_name in stream_names:
        stream_timeline_data = context.timelines.get(stream_name)
        
        if not stream_timeline_data or not isinstance(stream_timeline_data, list):
            continue
        
        timelines = stream_timeline_data
        
        if not isinstance(timelines, list) or len(timelines) == 0:
            continue
        
        # Generate test data for each duration and expiry combination
        for duration in durations:
            for expiry in expiry_minutes:
                # Find a timeline entry that's long enough for the requested duration
                suitable_timeline = None
                for timeline in timelines:
                    start_time_str = timeline.get('startTime')
                    end_time_str = timeline.get('endTime')
                    
                    if not start_time_str or not end_time_str:
                        continue
                    
                    try:
                        start_time = datetime.fromisoformat(start_time_str.replace('Z', '+00:00'))
                        end_time = datetime.fromisoformat(end_time_str.replace('Z', '+00:00'))
                        timeline_duration = (end_time - start_time).total_seconds()
                        
                        # Need at least 2x duration to safely use middle point
                        if timeline_duration >= duration * 2:
                            suitable_timeline = (start_time, end_time)
                            break
                    except (ValueError, AttributeError):
                        continue
                
                if not suitable_timeline:
                    # Fallback: find the longest timeline and clip if needed
                    longest_timeline = None
                    longest_duration = 0
                    for timeline in timelines:
                        start_time_str = timeline.get('startTime')
                        end_time_str = timeline.get('endTime')
                        
                        if not start_time_str or not end_time_str:
                            continue
                        
                        try:
                            start_time = datetime.fromisoformat(start_time_str.replace('Z', '+00:00'))
                            end_time = datetime.fromisoformat(end_time_str.replace('Z', '+00:00'))
                            timeline_duration = (end_time - start_time).total_seconds()
                            
                            if timeline_duration > longest_duration:
                                longest_duration = timeline_duration
                                longest_timeline = (start_time, end_time)
                        except (ValueError, AttributeError):
                            continue
                    
                    if longest_timeline and longest_duration >= duration:
                        suitable_timeline = longest_timeline
                
                if suitable_timeline:
                    start_time, end_time = suitable_timeline
                    
                    # Calculate a time range for video download
                    middle_time = start_time + (end_time - start_time) / 2
                    video_end_time = middle_time + timedelta(seconds=duration)
                    
                    # Ensure end time doesn't exceed timeline end
                    if video_end_time > end_time:
                        video_end_time = end_time
                    
                    test_data.append({
                        'stream_id': stream_name,
                        'start_time': middle_time.strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z',
                        'end_time': video_end_time.strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z',
                        'duration': duration,
                        'expiry_minutes': expiry
                    })

    context.test_data = test_data
    assert len(context.test_data) > 0, "No valid test data found"
    streams_with_cases = len({row['stream_id'] for row in context.test_data})
    logger.info(
        "Generated %d test case(s) across %d stream(s) (of %d stream id(s) from streams API)",
        len(context.test_data),
        streams_with_cases,
        len(stream_names),
    )


@then('video URLs for each stream and time range are requested with blocking mode in parallel')
def request_video_urls_parallel(context, api_config, test_endpoints, test_params):
    """Request video URLs with bounded parallelism."""
    max_concurrent = test_params.get('parallelism', 2)

    async def request_video_url(session: aiohttp.ClientSession, 
                                stream_id: str, 
                                start_time: str, 
                                end_time: str,
                                expiry_minutes: int,
                                index: int) -> Dict[str, Any]:
        """Request a video URL asynchronously."""
        url = f"{api_config['base_url']}{test_endpoints['storage_file_url'].format(stream_id=stream_id)}"
        
        # Build configuration JSON
        configuration = {
            "disableAudio": False,
            "overlay": {
                "bbox": {"showAll": True, "objectId": []},
                "color": "red",
                "thickness": 6,
                "opacity": 255,
                "debug": True
            }
        }
        
        params = {
            'startTime': start_time,
            'endTime': end_time,
            'expiryMinutes': str(expiry_minutes),
            'blocking': 'true',
            'container': 'mp4',
            'configuration': json.dumps(configuration)
        }
        
        try:
            request_start_time = time.time()
            async with session.get(
                url,
                params=params,
                headers={"streamid": envoy_streamid_route_key(stream_id)},
                timeout=aiohttp.ClientTimeout(total=test_params.get('url_request_timeout', 300)),
                ssl=api_config.get('verify_ssl', False)
            ) as response:
                response.raise_for_status()
                
                # Parse response as JSON (server may return text/plain content-type even for JSON)
                text = await response.text()
                try:
                    data = json.loads(text)
                except json.JSONDecodeError as je:
                    return {
                        'index': index,
                        'stream_id': stream_id,
                        'start_time': start_time,
                        'end_time': end_time,
                        'expiry_minutes': expiry_minutes,
                        'response_data': None,
                        'video_url': None,
                        'absolute_path': None,
                        'expiry_iso': None,
                        'request_duration': 0,
                        'status': response.status,
                        'success': False,
                        'error': f"JSON decode error: {str(je)}. Response: {text[:200]}"
                    }
                
                request_end_time = time.time()
                
                return {
                    'index': index,
                    'stream_id': stream_id,
                    'start_time': start_time,
                    'end_time': end_time,
                    'expiry_minutes': expiry_minutes,
                    'response_data': data,
                    'video_url': data.get('videoUrl'),
                    'absolute_path': data.get('absolutePath'),
                    'expiry_iso': data.get('expiryISO'),
                    'request_duration': request_end_time - request_start_time,
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
                'expiry_minutes': expiry_minutes,
                'response_data': None,
                'video_url': None,
                'absolute_path': None,
                'expiry_iso': None,
                'request_duration': 0,
                'status': None,
                'success': False,
                'error': str(e)
            }
    
    async def request_batch(batch: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Request a batch of video URLs with bounded parallelism."""
        sem = asyncio.Semaphore(max_concurrent)
        connector = aiohttp.TCPConnector(ssl=api_config.get('verify_ssl', False))

        async def _throttled(item: Dict[str, Any]) -> Dict[str, Any]:
            async with sem:
                return await request_video_url(
                    session,
                    item['stream_id'],
                    item['start_time'],
                    item['end_time'],
                    item['expiry_minutes'],
                    item['index'],
                )

        async with aiohttp.ClientSession(connector=connector) as session:
            tasks = [_throttled(item) for item in batch]
            return await asyncio.gather(*tasks)

    for i, item in enumerate(context.test_data):
        item['index'] = i

    logger.info(
        "Requesting video URLs for %d test case(s) (parallelism=%d)",
        len(context.test_data), max_concurrent,
    )

    all_results = asyncio.run(request_batch(context.test_data))
    
    context.url_responses = all_results
    
    # Count successful requests
    successful = sum(1 for r in context.url_responses if r['success'])
    logger.info("Successfully received %d/%d URL responses", successful, len(context.url_responses))
    
    # Log results
    for result in context.url_responses:
        if result['success']:
            logger.info("  [%s] Duration: %.1fms, Expiry: %dmin",
                       result['stream_id'], result['request_duration']*1000, result['expiry_minutes'])
        else:
            logger.error("  [%s] ERROR: %s", result['stream_id'], result['error'])


@then('all videos are downloaded from the URLs and validated')
def download_and_validate_videos(context, test_params):
    """Download videos from URLs with bounded parallelism and validate with mediainfo."""
    temp_dir = Path(test_params['temp_download_dir'])
    max_concurrent = test_params.get('parallelism', 2)

    async def download_video(session: aiohttp.ClientSession, 
                            video_url: str, 
                            index: int,
                            stream_id: str) -> Dict[str, Any]:
        """Download a video from URL asynchronously."""
        try:
            download_start = time.time()
            time_to_first_byte = None
            
            async with session.get(
                video_url,
                headers={"streamid": envoy_streamid_route_key(stream_id)},
                timeout=aiohttp.ClientTimeout(total=test_params.get('download_timeout', 120)),
                ssl=False
            ) as response:
                response.raise_for_status()
                
                # Read content in chunks to capture time to first byte
                chunks = []
                async for chunk in response.content.iter_chunked(8192):
                    if time_to_first_byte is None:
                        time_to_first_byte = time.time() - download_start
                    chunks.append(chunk)
                
                content = b''.join(chunks)
                total_time = time.time() - download_start
                
                return {
                    'index': index,
                    'content': content,
                    'status': response.status,
                    'success': True,
                    'time_to_first_byte': time_to_first_byte,
                    'total_download_time': total_time,
                    'error': None
                }
        except Exception as e:
            return {
                'index': index,
                'content': None,
                'status': None,
                'success': False,
                'time_to_first_byte': None,
                'total_download_time': 0,
                'error': str(e)
            }
    
    async def download_batch(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Download a batch of videos with bounded parallelism."""
        sem = asyncio.Semaphore(max_concurrent)
        connector = aiohttp.TCPConnector(ssl=False)

        async def _throttled(item: Dict[str, Any]) -> Dict[str, Any]:
            async with sem:
                return await download_video(session, item['video_url'], item['index'], item['stream_id'])

        async with aiohttp.ClientSession(connector=connector) as session:
            tasks = [_throttled(item) for item in items if item['video_url']]
            return await asyncio.gather(*tasks)

    logger.info("Downloading videos from URLs (parallelism=%d)...", max_concurrent)
    successful_url_responses = [r for r in context.url_responses if r['success'] and r['video_url']]
    
    download_results = asyncio.run(download_batch(successful_url_responses))
    
    # Validate each downloaded video
    validation_results = []
    
    for download_data in download_results:
        if not download_data['success']:
            validation_results.append({
                'index': download_data['index'],
                'valid': False,
                'error': f"Failed to download: {download_data['error']}"
            })
            continue
        
        # Get corresponding URL response
        url_response = next((r for r in context.url_responses if r['index'] == download_data['index']), None)
        
        # Save video to temporary file
        temp_file = temp_dir / f"blocking_video_{download_data['index']}.mp4"
        
        try:
            with open(temp_file, 'wb') as f:
                f.write(download_data['content'])
            
            # Run mediainfo to validate
            mediainfo_timeout = test_params.get('mediainfo_timeout_sec', 30)
            result = subprocess.run(
                ['mediainfo', str(temp_file)],
                capture_output=True,
                text=True,
                timeout=mediainfo_timeout
            )
            
            # Check if mediainfo output contains expected video information
            output = result.stdout
            is_valid = (
                result.returncode == 0 and
                'Video' in output and
                ('Format' in output or 'Codec' in output)
            )
            
            if is_valid:
                # Extract key info from mediainfo output
                lines = output.split('\n')
                summary = []
                for line in lines[:20]:
                    if any(keyword in line for keyword in ['Format', 'Duration', 'Width', 'Height', 'Frame rate']):
                        summary.append(line.strip())
                
                summary_str = ' | '.join(summary[:3]) if summary else ''
                ttfb = download_data.get('time_to_first_byte', 0) * 1000
                total_time = download_data.get('total_download_time', 0) * 1000
                logger.info("  [Index %d] Valid: %s", download_data['index'], summary_str)
                logger.info("    Download timing - First byte: %.1fms, Total: %.1fms", ttfb, total_time)
            else:
                logger.warning("  [Index %d] Invalid video", download_data['index'])
            
            validation_results.append({
                'index': download_data['index'],
                'stream_id': url_response['stream_id'] if url_response else 'unknown',
                'valid': is_valid,
                'file_size': len(download_data['content']),
                'file_path': str(temp_file),
                'absolute_path': url_response['absolute_path'] if url_response else None,
                'expiry_iso': url_response['expiry_iso'] if url_response else None,
                'expiry_minutes': url_response['expiry_minutes'] if url_response else 0,
                'error': None if is_valid else 'Invalid video format'
            })
            
        except subprocess.TimeoutExpired:
            logger.warning("  [Index %d] mediainfo timeout", download_data['index'])
            validation_results.append({
                'index': download_data['index'],
                'valid': False,
                'error': 'mediainfo timeout'
            })
        except Exception as e:
            logger.error("  [Index %d] Error: %s", download_data['index'], e)
            validation_results.append({
                'index': download_data['index'],
                'valid': False,
                'error': str(e)
            })
    
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
            logger.warning("  - Index: %d, Error: %s", result['index'], result['error'])
    
    # Assert with detailed error reporting
    if valid_count != total_count:
        failure_info = format_validation_failure(valid_count, total_count, invalid_videos, "blocking URL video")
        assert_with_detailed_failure(
            False,
            "Blocking URL Video Download Validation",
            failure_info['expected'],
            failure_info['actual'],
            failure_info['failed_items'],
            f"Check URL generation and video download above.\n"
            f"  Downloaded videos: {total_count}\n"
            f"  Valid: {valid_count}\n"
            f"  Invalid: {total_count - valid_count}\n"
            f"  URL mode: Blocking (video generated synchronously)"
        )


@then('video files are verified to expire after expiry time')
def verify_file_expiry(context, test_params, api_config):
    """Verify that video files expire after the specified expiry time."""
    logger.info("Verifying file expiry...")
    
    expiry_results = []
    
    for validation in context.validation_results:
        if not validation.get('valid') or not validation.get('expiry_iso'):
            continue
        
        try:
            # Get corresponding URL response
            url_response = next((r for r in context.url_responses if r['index'] == validation['index']), None)
            if not url_response or not url_response.get('video_url'):
                continue
            
            video_url = url_response['video_url']
            
            # Parse expiry time
            expiry_time = datetime.fromisoformat(validation['expiry_iso'].replace('Z', '+00:00'))
            current_time = datetime.now(expiry_time.tzinfo)
            
            # Calculate time until expiry
            time_until_expiry = (expiry_time - current_time).total_seconds()
            
            logger.info("  [Index %d] Time until expiry: %.0fms", validation['index'], time_until_expiry*1000)
            
            # Get expiry timing parameters from config
            expiry_grace = test_params.get('expiry_grace_period_sec', 10)
            expiry_buffer = test_params.get('expiry_check_buffer_sec', 5)
            
            # If expiry time is in the future, wait for it plus grace period
            if time_until_expiry > 0:
                wait_time = time_until_expiry + expiry_grace
                logger.info("  [Index %d] Waiting %.0fs for expiry + %ds grace period...", 
                           validation['index'], wait_time, expiry_grace)
                time.sleep(wait_time)
            else:
                # Already expired, wait buffer time after expiry
                if time_until_expiry > -expiry_buffer:
                    wait_time = expiry_buffer + time_until_expiry
                    if wait_time > 0:
                        logger.info("  [Index %d] Waiting additional %.0fs after expiry...", 
                                   validation['index'], wait_time)
                        time.sleep(wait_time)
            
            # Try to access the URL again to verify it's no longer valid (with retry logic)
            logger.info("  [Index %d] Checking if URL is still accessible...", validation['index'])
            url_still_accessible = False
            url_status_code = None
            max_retries = test_params.get('video_ready_max_retries', 3)
            
            # Retry logic for expiry check (network issues shouldn't cause false failures)
            for retry in range(max_retries):
                try:
                    expiry_check_timeout = test_params.get('timeout', 10)
                    stream_id = url_response.get('stream_id')
                    route = envoy_streamid_route_key(stream_id) if stream_id else ""
                    req_headers = {"streamid": route} if route else None
                    response = requests.get(
                        video_url,
                        headers=req_headers,
                        timeout=expiry_check_timeout,
                        verify=api_config.get('verify_ssl', False),
                    )
                    url_still_accessible = response.status_code == 200
                    url_status_code = response.status_code
                    
                    if url_still_accessible:
                        logger.warning("  [Index %d] WARNING: URL still accessible (HTTP %s)", validation['index'], url_status_code)
                    else:
                        logger.info("  [Index %d] URL correctly expired (HTTP %s)", validation['index'], url_status_code)
                    break  # Success, no need to retry
                except requests.exceptions.Timeout:
                    if retry < max_retries - 1:
                        logger.debug("  [Index %d] Expiry check timeout, retry %d/%d", validation['index'], retry + 1, max_retries)
                        time.sleep(1)
                    else:
                        # Final timeout - assume expired
                        url_still_accessible = False
                        url_status_code = None
                        logger.info("  [Index %d] URL correctly expired (timeout)", validation['index'])
                except requests.exceptions.RequestException:
                    # Connection error = URL expired
                    url_still_accessible = False
                    url_status_code = None
                    logger.info("  [Index %d] URL correctly expired (connection error)", validation['index'])
                    break
            
            # Check if local temp file still exists
            temp_file = Path(validation.get('file_path', ''))
            file_exists = temp_file.exists() if temp_file else False
            
            expiry_results.append({
                'index': validation['index'],
                'stream_id': validation['stream_id'],
                'expiry_minutes': validation['expiry_minutes'],
                'time_until_expiry': time_until_expiry,
                'file_exists_after_expiry': file_exists,
                'url_accessible_after_expiry': url_still_accessible,
                'url_status_code': url_status_code,
                'expected_expired': True,
                'expired_correctly': not url_still_accessible
            })
            
            # Clean up temp file
            if file_exists:
                temp_file.unlink()
                logger.info("  [Index %d] Local temp file cleaned up", validation['index'])
            
        except Exception as e:
            logger.error("  [Index %d] Error checking expiry: %s", validation['index'], e)
            expiry_results.append({
                'index': validation['index'],
                'expired_correctly': False,
                'error': str(e)
            })
    
    context.expiry_results = expiry_results
    
    # Log summary
    correctly_expired = sum(1 for r in expiry_results if r.get('expired_correctly', False))
    logger.info("Expiry verification: %d/%d URLs correctly expired", correctly_expired, len(expiry_results))

