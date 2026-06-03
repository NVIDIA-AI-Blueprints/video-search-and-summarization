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
import hashlib
import logging
import subprocess
from pathlib import Path
from typing import List, Dict, Any

import aiohttp
import pytest
import requests
from pytest_bdd import scenarios, given, when, then

from ..test_utils import assert_with_detailed_failure, format_validation_failure

logger = logging.getLogger(__name__)


# Load scenarios from the feature file
scenarios('../../features/picture/live_picture.feature')


from .picture_test_utils import (
    ENDPOINTS_LIVE,
    fetch_streams,
    fetch_picture_async,
    validate_jpeg_with_jpeginfo
)

# Test endpoints for live pictures
@pytest.fixture
def test_endpoints():
    """Get test endpoints configuration for live pictures."""
    return ENDPOINTS_LIVE

# context, test_params, and setup_and_cleanup_temp_dir fixtures provided by conftest.py


@given('the VST API is configured')
def vst_api_configured(api_config, test_endpoints):
    """Verify VST API configuration is available."""
    assert api_config['base_url'], "Base URL must be configured"
    assert test_endpoints['streams'], "Streams endpoint must be configured"
    assert test_endpoints['picture'], "Picture endpoint must be configured"


@when('the list of available live streams is fetched')
def fetch_streams(context, api_config, test_endpoints, test_params):
    """Fetch the list of available live streams from the API."""
    url = f"{api_config['base_url']}{test_endpoints['streams']}"
    
    response = requests.get(
        url,
        timeout=test_params['timeout'],
        verify=api_config.get('verify_ssl', False)
    )
    response.raise_for_status()
    
    context.streams = response.json()
    assert len(context.streams) > 0, "No streams found"


@then('live pictures for each stream are fetched in parallel')
def fetch_pictures_parallel(context, api_config, test_endpoints, test_params):
    """Fetch live pictures in parallel."""
    
    # Extract stream names from the streams response
    # Filter out test upload sensors (exclude anything starting with "test_upload_")
    stream_names = []
    excluded_count = 0
    for stream_obj in context.streams:
        if isinstance(stream_obj, dict):
            for stream_name in stream_obj.keys():
                if stream_name.startswith("test_upload_"):
                    excluded_count += 1
                    logger.debug("Excluding test upload sensor: %s", stream_name)
                else:
                    stream_names.append(stream_name)
    
    if excluded_count > 0:
        logger.info("Excluded %d test upload sensor(s) from live picture test", excluded_count)
    
    assert len(stream_names) > 0, "No valid stream names found (all streams are test uploads)"
    
    max_concurrent = test_params.get('parallelism', 2)

    async def fetch_all_pictures(stream_ids: List[str]) -> List[Dict[str, Any]]:
        """Fetch pictures with bounded concurrency."""
        sem = asyncio.Semaphore(max_concurrent)
        connector = aiohttp.TCPConnector(ssl=api_config.get('verify_ssl', False))

        async def _throttled(idx: int, stream_id: str) -> Dict[str, Any]:
            async with sem:
                return await fetch_picture_async(
                    session,
                    f"{api_config['base_url']}{test_endpoints['picture'].format(stream_id=stream_id)}",
                    stream_id,
                    idx,
                    test_params['timeout']
                )

        async with aiohttp.ClientSession(connector=connector) as session:
            tasks = [_throttled(idx, sid) for idx, sid in enumerate(stream_ids)]
            return await asyncio.gather(*tasks)
    
    logger.info("Fetching live pictures for %d stream(s) (parallelism=%d)", len(stream_names), max_concurrent)
    
    # Fetch all pictures in parallel
    context.pictures = asyncio.run(fetch_all_pictures(stream_names))
    
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
                'valid': False,
                'error': f"Failed to fetch: {picture_data['error']}"
            })
            continue
        
        stream_hash = hashlib.md5(picture_data['stream_id'].encode()).hexdigest()[:8]
        temp_file = temp_dir / f"picture_{picture_data['index']}_{stream_hash}.jpg"
        
        try:
            with open(temp_file, 'wb') as f:
                f.write(picture_data['content'])
            
            validation = validate_jpeg_with_jpeginfo(temp_file)
            
            validation_results.append({
                'index': picture_data['index'],
                'stream_id': picture_data['stream_id'],
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
                'valid': False,
                'error': str(e)
            })
    
    context.validation_results = validation_results
    
    valid_count = sum(1 for r in validation_results if r['valid'])
    total_count = len(validation_results)
    
    logger.info("Validation Results: %d/%d pictures are valid", valid_count, total_count)
    
    invalid_pictures = [r for r in validation_results if not r['valid']]
    if invalid_pictures:
        logger.warning("Invalid pictures:")
        for result in invalid_pictures:
            logger.warning("  - Stream: %s, Error: %s", result['stream_id'], result['error'])
    
    # Assert with detailed error reporting
    if valid_count != total_count:
        failure_info = format_validation_failure(valid_count, total_count, invalid_pictures, "picture")
        assert_with_detailed_failure(
            False,
            "Live Picture Validation",
            failure_info['expected'],
            failure_info['actual'],
            failure_info['failed_items'],
            f"Check jpeginfo output above. Ensure pictures are valid JPEG format.\n"
            f"  Fetched pictures: {total_count}\n"
            f"  Valid: {valid_count}\n"
            f"  Invalid: {total_count - valid_count}\n"
            f"  Excluded test_upload_* sensors from live test"
        )

