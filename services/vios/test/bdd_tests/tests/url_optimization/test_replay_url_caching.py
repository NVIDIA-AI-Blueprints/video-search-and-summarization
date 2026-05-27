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

import json
import logging
import time
from typing import Dict, Any

import pytest
import requests
from pytest_bdd import scenarios, given, when, then

from ..test_utils import assert_with_detailed_failure
from .url_caching_test_utils import (
    envoy_streamid_route_key,
    extract_filename_from_url,
    select_replay_timestamp,
    select_video_time_range,
)

logger = logging.getLogger(__name__)

scenarios('../../features/url_optimization/replay_url_caching.feature')


# ---------------------------------------------------------------------------
# Shared steps
# ---------------------------------------------------------------------------

@given('the VST API is configured for URL caching test')
def vst_api_configured(api_config, test_endpoints):
    """Verify VST API configuration is available."""
    assert api_config['base_url'], "Base URL must be configured"
    assert test_endpoints['streams'], "Streams endpoint must be configured"


@when('the list of available replay streams is fetched')
def fetch_streams(context, api_config, test_endpoints, test_params):
    """Fetch available replay streams."""
    url = f"{api_config['base_url']}{test_endpoints['streams']}"
    response = requests.get(
        url,
        timeout=test_params['timeout'],
        verify=api_config.get('verify_ssl', False),
    )
    response.raise_for_status()
    context.streams = response.json()
    assert len(context.streams) > 0, "No replay streams found"


@when('the recording timelines are fetched for URL caching test')
def fetch_timelines(context, api_config, test_endpoints, test_params):
    """Fetch recording timelines via storage/size?timelines=true."""
    url = f"{api_config['base_url']}{test_endpoints['storage_size']}"
    response = requests.get(
        url,
        params={'timelines': 'true'},
        timeout=test_params['timeout'],
        verify=api_config.get('verify_ssl', False),
    )
    response.raise_for_status()
    context.timelines = response.json()
    assert context.timelines, "No timeline data found"


# ---------------------------------------------------------------------------
# Picture URL caching steps
# ---------------------------------------------------------------------------

@when('a valid replay timestamp is selected')
def select_timestamp(context):
    """Select a single replay timestamp from available timelines."""
    result = select_replay_timestamp(context.streams, context.timelines)
    assert result is not None, "No suitable timeline for replay picture test"
    context.selected_stream_id = result['stream_id']
    context.selected_timestamp = result['timestamp']
    logger.info("Selected stream=%s timestamp=%s", result['stream_id'], result['timestamp'])


def _request_picture_url(api_config: dict, test_endpoints: dict,
                         test_params: dict,
                         stream_id: str, timestamp: str,
                         expiry_minutes: int) -> Dict[str, Any]:
    """Issue a replay picture URL request and return response data + timing."""
    url = f"{api_config['base_url']}{test_endpoints['picture_url'].format(stream_id=stream_id)}"
    params = {
        'startTime': timestamp,
        'expiryMinutes': str(expiry_minutes),
    }
    start = time.monotonic()
    resp = requests.get(
        url,
        params=params,
        timeout=test_params.get('url_request_timeout', 60),
        verify=api_config.get('verify_ssl', False),
    )
    elapsed = time.monotonic() - start
    resp.raise_for_status()
    data = resp.json()
    return {'data': data, 'elapsed': elapsed}


@then('a replay picture URL is requested')
def request_picture_url_first(context, api_config, test_endpoints, test_params):
    """First picture URL request -- generates the file."""
    expiry = test_params.get('expiry_minutes', 5)
    result = _request_picture_url(
        api_config, test_endpoints, test_params,
        context.selected_stream_id, context.selected_timestamp, expiry,
    )
    context.first_response = result['data']
    context.first_request_duration = result['elapsed']
    assert context.first_response.get('imageUrl'), "First response missing imageUrl"
    logger.info("First picture URL response: imageUrl=%s elapsed=%.1fms",
                context.first_response['imageUrl'], result['elapsed'] * 1000)


@then('the same replay picture URL is requested again')
def request_picture_url_second(context, api_config, test_endpoints, test_params):
    """Second picture URL request -- should hit the cache."""
    expiry = test_params.get('expiry_minutes', 5)
    result = _request_picture_url(
        api_config, test_endpoints, test_params,
        context.selected_stream_id, context.selected_timestamp, expiry,
    )
    context.second_response = result['data']
    context.second_request_duration = result['elapsed']
    assert context.second_response.get('imageUrl'), "Second response missing imageUrl"
    logger.info("Second picture URL response: imageUrl=%s elapsed=%.1fms",
                context.second_response['imageUrl'], result['elapsed'] * 1000)


@then('the second picture URL response reuses the cached file')
def verify_picture_cache_hit(context):
    """Verify the cached file is reused (same filename in the URL)."""
    first_file = extract_filename_from_url(context.first_response['imageUrl'])
    second_file = extract_filename_from_url(context.second_response['imageUrl'])
    logger.info("Picture file comparison: first=%s second=%s", first_file, second_file)

    assert_with_detailed_failure(
        first_file == second_file,
        "Picture URL Cache Hit",
        f"Same temp file reused: {first_file}",
        f"First={first_file}, Second={second_file}",
        additional_info="The server should return the same cached temp file for identical stream+timestamp",
    )

    speedup = context.first_request_duration / context.second_request_duration if context.second_request_duration > 0 else 0
    logger.info("Picture URL timing: first=%.1fms second=%.1fms speedup=%.1fx",
                context.first_request_duration * 1000,
                context.second_request_duration * 1000, speedup)


@then('the picture URL expiry time is refreshed on cache hit')
def verify_picture_expiry_refresh(context):
    """Verify the expiry timestamp is refreshed on the second request."""
    first_expiry = context.first_response.get('expiryISO', '')
    second_expiry = context.second_response.get('expiryISO', '')
    assert first_expiry, "First response missing expiryISO"
    assert second_expiry, "Second response missing expiryISO"

    logger.info("Picture expiry: first=%s second=%s", first_expiry, second_expiry)

    assert second_expiry >= first_expiry, (
        f"Expiry was not refreshed: second ({second_expiry}) < first ({first_expiry})"
    )


# ---------------------------------------------------------------------------
# Video URL caching steps
# ---------------------------------------------------------------------------

@when('a valid video time range is selected')
def select_video_range(context, test_params):
    """Select a time range for video URL testing."""
    duration = test_params.get('video_duration_seconds', 5)
    result = select_video_time_range(context.streams, context.timelines, duration)
    assert result is not None, "No suitable timeline for video URL test"
    context.selected_stream_id = result['stream_id']
    context.selected_start_time = result['start_time']
    context.selected_end_time = result['end_time']
    logger.info("Selected stream=%s start=%s end=%s",
                result['stream_id'], result['start_time'], result['end_time'])


def _request_video_url(api_config: dict, test_endpoints: dict,
                       test_params: dict,
                       stream_id: str, start_time: str, end_time: str,
                       expiry_minutes: int) -> Dict[str, Any]:
    """Issue a blocking video URL request and return response data + timing."""
    url = f"{api_config['base_url']}{test_endpoints['video_url'].format(stream_id=stream_id)}"
    params = {
        'startTime': start_time,
        'endTime': end_time,
        'expiryMinutes': str(expiry_minutes),
        'blocking': 'true',
        'container': 'mp4',
    }
    headers = {"streamid": envoy_streamid_route_key(stream_id)}
    start = time.monotonic()
    resp = requests.get(
        url,
        params=params,
        headers=headers,
        timeout=test_params.get('url_request_timeout', 300),
        verify=api_config.get('verify_ssl', False),
    )
    elapsed = time.monotonic() - start
    resp.raise_for_status()

    text = resp.text
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        pytest.fail(f"Video URL response is not valid JSON: {e}. Body: {text[:200]}")

    return {'data': data, 'elapsed': elapsed}


@then('a blocking video URL is requested')
def request_video_url_first(context, api_config, test_endpoints, test_params):
    """First blocking video URL request -- generates the file."""
    expiry = test_params.get('expiry_minutes', 5)
    result = _request_video_url(
        api_config, test_endpoints, test_params,
        context.selected_stream_id, context.selected_start_time,
        context.selected_end_time, expiry,
    )
    context.first_response = result['data']
    context.first_request_duration = result['elapsed']
    assert context.first_response.get('videoUrl'), "First response missing videoUrl"
    logger.info("First video URL response: videoUrl=%s elapsed=%.1fms",
                context.first_response['videoUrl'], result['elapsed'] * 1000)


@then('the same blocking video URL is requested again')
def request_video_url_second(context, api_config, test_endpoints, test_params):
    """Second blocking video URL request -- should hit the cache."""
    expiry = test_params.get('expiry_minutes', 5)
    result = _request_video_url(
        api_config, test_endpoints, test_params,
        context.selected_stream_id, context.selected_start_time,
        context.selected_end_time, expiry,
    )
    context.second_response = result['data']
    context.second_request_duration = result['elapsed']
    assert context.second_response.get('videoUrl'), "Second response missing videoUrl"
    logger.info("Second video URL response: videoUrl=%s elapsed=%.1fms",
                context.second_response['videoUrl'], result['elapsed'] * 1000)


@then('the second video URL response reuses the cached file')
def verify_video_cache_hit(context):
    """Verify the cached video file is reused (same filename in the URL)."""
    first_file = extract_filename_from_url(context.first_response['videoUrl'])
    second_file = extract_filename_from_url(context.second_response['videoUrl'])
    logger.info("Video file comparison: first=%s second=%s", first_file, second_file)

    assert_with_detailed_failure(
        first_file == second_file,
        "Video URL Cache Hit",
        f"Same temp file reused: {first_file}",
        f"First={first_file}, Second={second_file}",
        additional_info="The server should return the same cached temp file for identical stream+time range",
    )

    speedup = context.first_request_duration / context.second_request_duration if context.second_request_duration > 0 else 0
    logger.info("Video URL timing: first=%.1fms second=%.1fms speedup=%.1fx",
                context.first_request_duration * 1000,
                context.second_request_duration * 1000, speedup)


@then('the video URL expiry time is refreshed on cache hit')
def verify_video_expiry_refresh(context):
    """Verify the expiry timestamp is refreshed on the second request."""
    first_expiry = context.first_response.get('expiryISO', '')
    second_expiry = context.second_response.get('expiryISO', '')
    assert first_expiry, "First response missing expiryISO"
    assert second_expiry, "Second response missing expiryISO"

    logger.info("Video expiry: first=%s second=%s", first_expiry, second_expiry)

    assert second_expiry >= first_expiry, (
        f"Expiry was not refreshed: second ({second_expiry}) < first ({first_expiry})"
    )
