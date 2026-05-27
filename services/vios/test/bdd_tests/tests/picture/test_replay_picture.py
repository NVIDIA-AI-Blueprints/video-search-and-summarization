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
import tempfile
from pathlib import Path
from typing import List, Dict, Any
from datetime import datetime

import aiohttp
import pytest
import requests
from pytest_bdd import scenarios, given, when, then, parsers

from ..test_utils import assert_with_detailed_failure, format_validation_failure

logger = logging.getLogger(__name__)

# Load scenarios from the feature file
scenarios('../../features/picture/replay_picture.feature')


from .picture_test_utils import (
    ENDPOINTS_REPLAY,
    delete_sensor,
    fetch_streams,
    fetch_timelines,
    filter_streams_by_codec,
    select_timestamps_from_timelines,
    fetch_picture_async,
    validate_jpeg_with_jpeginfo
)

# Test endpoints for replay pictures
@pytest.fixture
def test_endpoints():
    """Get test endpoints configuration for replay pictures."""
    return ENDPOINTS_REPLAY

# context, test_params, and setup_and_cleanup_temp_dir fixtures provided by conftest.py


@given('the VST API is configured')
def vst_api_configured(api_config, test_endpoints):
    """Verify VST API configuration is available."""
    assert api_config['base_url'], "Base URL must be configured"
    assert test_endpoints['streams'], "Streams endpoint must be configured"
    assert test_endpoints['storage_size'], "Storage size endpoint must be configured"
    assert test_endpoints['picture'], "Picture endpoint must be configured"


@when('the list of available streams is fetched')
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


@when('only H265 codec streams are selected')
def filter_h265_streams(context):
    """Filter streams to only include H265 codec streams."""
    context.streams = filter_streams_by_codec(context.streams, "H265")
    if len(context.streams) == 0:
        pytest.skip("No H265 codec streams found")
    logger.info("Filtered to %d H265 stream(s)", len(context.streams))


@when('an H265 sensor is disconnected')
def disconnect_h265_sensor(context, api_config, test_endpoints, test_params):
    """Find a connected H265 sensor with timeline data and delete it.

    Recordings are retained after sensor deletion, so the replay picture
    API should still be able to serve pictures for the disconnected sensor.
    """
    h265_stream_names = []
    for stream_obj in context.streams:
        if isinstance(stream_obj, dict):
            for stream_name in stream_obj.keys():
                h265_stream_names.append(stream_name)

    target_sensor = None
    for stream_name in h265_stream_names:
        stream_data = context.timelines.get(stream_name, {})
        if isinstance(stream_data, dict) and stream_data.get('timelines'):
            target_sensor = stream_name
            break

    if not target_sensor:
        pytest.fail("No H265 sensor with timeline data found to disconnect")

    logger.info("Disconnecting H265 sensor: %s", target_sensor)
    delete_sensor(
        api_config['base_url'],
        test_endpoints['sensor_delete'],
        target_sensor,
        test_params['timeout'],
        api_config.get('verify_ssl', False),
    )
    context.deleted_sensor_id = target_sensor

    # Filter context.streams to only the disconnected sensor
    context.streams = [
        s for s in context.streams if isinstance(s, dict) and target_sensor in s
    ]
    logger.info("Testing replay picture for disconnected sensor: %s", target_sensor)


@when('the recording timelines with timeline data are fetched')
def fetch_timelines(context, api_config, test_endpoints, test_params):
    """Fetch recording timelines from the storage API."""
    url = f"{api_config['base_url']}{test_endpoints['storage_size']}"
    params = {'timelines': 'true'}
    
    response = requests.get(
        url,
        params=params,
        timeout=test_params['timeout'],
        verify=api_config.get('verify_ssl', False)
    )
    response.raise_for_status()
    
    context.timelines = response.json()
    assert context.timelines, "No timeline data found"


@when('valid timestamps from the timelines are selected')
def select_timestamps(context):
    """Select valid timestamps from the timeline data for testing."""
    
    # Convert timelines from storage/size API format to standard format
    # API format: {"stream_name": {"timelines": [...]}} → {"stream_name": [...]}
    standard_timelines = {}
    for stream_name, stream_data in context.timelines.items():
        if isinstance(stream_data, dict) and 'timelines' in stream_data:
            standard_timelines[stream_name] = stream_data['timelines']
    
    test_data = select_timestamps_from_timelines(context.streams, standard_timelines)
    context.test_data = test_data
    
    assert len(context.test_data) > 0, "No valid test data found"
    logger.info("Selected %d timestamp(s) for testing", len(context.test_data))


@then('pictures for each stream and timestamp are fetched in parallel')
def fetch_pictures_parallel(context, api_config, test_endpoints, test_params):
    """Fetch pictures in parallel."""
    
    async def fetch_picture(session: aiohttp.ClientSession, stream_id: str, 
                           start_time: str, index: int) -> Dict[str, Any]:
        """Fetch a single picture asynchronously."""
        url = f"{api_config['base_url']}{test_endpoints['picture'].format(stream_id=stream_id)}"
        params = {'startTime': start_time}
        
        try:
            async with session.get(
                url, 
                params=params,
                timeout=aiohttp.ClientTimeout(total=test_params['timeout']),
                ssl=api_config.get('verify_ssl', False)
            ) as response:
                response.raise_for_status()
                content = await response.read()
                
                return {
                    'index': index,
                    'stream_id': stream_id,
                    'start_time': start_time,
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
                'content': None,
                'status': None,
                'success': False,
                'error': str(e)
            }
    
    max_concurrent = test_params.get('parallelism', 2)

    async def fetch_all_pictures(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Fetch pictures with bounded concurrency."""
        sem = asyncio.Semaphore(max_concurrent)
        connector = aiohttp.TCPConnector(ssl=api_config.get('verify_ssl', False))

        async def _throttled(i: int, item: Dict[str, Any]) -> Dict[str, Any]:
            async with sem:
                return await fetch_picture(session, item['stream_id'], item['timestamp'], i)

        async with aiohttp.ClientSession(connector=connector) as session:
            tasks = [_throttled(i, item) for i, item in enumerate(items)]
            return await asyncio.gather(*tasks)
    
    logger.info("Fetching replay pictures for %d stream(s) (parallelism=%d)", len(context.test_data), max_concurrent)
    
    # Fetch all pictures in parallel
    results = asyncio.run(fetch_all_pictures(context.test_data))
    context.pictures = results
    
    # Count successful fetches
    successful = sum(1 for p in context.pictures if p['success'])
    logger.info("Successfully fetched %d/%d pictures", successful, len(context.pictures))


@then('all fetched pictures are valid JPEG images')
def validate_pictures(context, test_params):
    """Validate that all fetched pictures are valid JPEG images using jpeginfo."""
    temp_dir = Path(test_params['temp_image_dir'])
    
    validation_results = []
    
    for picture_data in context.pictures:
        if not picture_data['success']:
            validation_results.append({
                'index': picture_data['index'],
                'stream_id': picture_data['stream_id'],
                'start_time': picture_data['start_time'],
                'valid': False,
                'error': f"Failed to fetch: {picture_data['error']}"
            })
            continue
        
        temp_file = temp_dir / f"picture_{picture_data['index']}_{picture_data['stream_id']}.jpg"
        
        try:
            with open(temp_file, 'wb') as f:
                f.write(picture_data['content'])
            
            validation = validate_jpeg_with_jpeginfo(temp_file)
            
            validation_results.append({
                'index': picture_data['index'],
                'stream_id': picture_data['stream_id'],
                'start_time': picture_data['start_time'],
                'valid': validation['valid'],
                'error': validation['error'],
                'summary': validation['summary']
            })
            
            if validation['valid']:
                logger.info("[%s] Valid JPEG: %s", picture_data['stream_id'], validation['summary'])
            else:
                logger.warning("[%s] Invalid: %s", picture_data['stream_id'], validation['error'])
                
        except Exception as e:
            logger.error("[%s] Error: %s", picture_data['stream_id'], e)
            validation_results.append({
                'index': picture_data['index'],
                'stream_id': picture_data['stream_id'],
                'start_time': picture_data['start_time'],
                'valid': False,
                'error': str(e)
            })
    
    context.validation_results = validation_results
    
    # Log validation summary
    valid_count = sum(1 for r in validation_results if r['valid'])
    total_count = len(validation_results)
    
    logger.info("Validation Results: %d/%d pictures are valid", valid_count, total_count)
    
    # Log details of invalid pictures
    invalid_pictures = [r for r in validation_results if not r['valid']]
    if invalid_pictures:
        logger.warning("Invalid pictures:")
        for result in invalid_pictures:
            logger.warning("  - Stream: %s, Time: %s, Error: %s",
                          result['stream_id'], result['start_time'], result['error'])
    
    # Assert with detailed error reporting
    if valid_count != total_count:
        failure_info = format_validation_failure(valid_count, total_count, invalid_pictures, "replay picture")
        assert_with_detailed_failure(
            False,
            "Replay Picture Validation",
            failure_info['expected'],
            failure_info['actual'],
            failure_info['failed_items'],
            f"Check jpeginfo output above. Ensure pictures are valid JPEG format.\n"
            f"  Fetched replay pictures: {total_count}\n"
            f"  Valid: {valid_count}\n"
            f"  Invalid: {total_count - valid_count}\n"
            f"  Pictures fetched from timeline timestamps"
        )

