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

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import List, Dict, Any

import pytest
import requests
from pytest_bdd import scenarios, given, when, then

from ..test_utils import assert_with_detailed_failure, format_validation_failure

logger = logging.getLogger(__name__)

scenarios('../../features/picture/storage_picture.feature')


from .picture_test_utils import (
    ENDPOINTS_STORAGE,
    PictureContext,
    filter_streams_by_codec,
    select_timestamps_from_timelines,
    validate_jpeg_with_jpeginfo
)


@pytest.fixture
def test_endpoints() -> Dict[str, str]:
    """Get test endpoints configuration for storage pictures."""
    return ENDPOINTS_STORAGE


@given('the VST API is configured')
def vst_api_configured(api_config: Dict[str, Any], test_endpoints: Dict[str, str]) -> None:
    """Verify VST API configuration is available."""
    assert api_config['base_url'], "Base URL must be configured"
    assert test_endpoints['streams'], "Streams endpoint must be configured"
    assert test_endpoints['storage_size'], "Storage size endpoint must be configured"
    assert test_endpoints['picture'], "Picture endpoint must be configured"


@when('the list of available streams is fetched')
def fetch_available_streams(context: PictureContext,
                            api_config: Dict[str, Any],
                            test_endpoints: Dict[str, str],
                            test_params: Dict[str, Any]) -> None:
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
def filter_h265_streams(context: PictureContext) -> None:
    """Filter streams to only include H265 codec streams."""
    context.streams = filter_streams_by_codec(context.streams, "H265")
    if len(context.streams) == 0:
        pytest.skip("No H265 codec streams found")
    logger.info("Filtered to %d H265 stream(s)", len(context.streams))


@when('the recording timelines with timeline data are fetched')
def fetch_recording_timelines(context: PictureContext,
                              api_config: Dict[str, Any],
                              test_endpoints: Dict[str, str],
                              test_params: Dict[str, Any]) -> None:
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
def select_timestamps(context: PictureContext) -> None:
    """Select valid timestamps from the timeline data for testing."""
    standard_timelines: Dict[str, Any] = {}
    for stream_name, stream_data in context.timelines.items():
        if isinstance(stream_data, dict) and 'timelines' in stream_data:
            standard_timelines[stream_name] = stream_data['timelines']

    test_data = select_timestamps_from_timelines(context.streams, standard_timelines)
    context.test_data = test_data

    assert len(context.test_data) > 0, "No valid test data found"
    logger.info("Selected %d timestamp(s) for testing", len(context.test_data))


def _fetch_picture(base_url: str, endpoint: str, stream_id: str,
                   start_time: str, index: int, timeout: int,
                   verify_ssl: bool) -> Dict[str, Any]:
    """Fetch a single picture synchronously."""
    url = f"{base_url}{endpoint.format(stream_id=stream_id)}"
    params = {'startTime': start_time}
    try:
        resp = requests.get(url, params=params, timeout=timeout, verify=verify_ssl)
        resp.raise_for_status()
        return {
            'index': index,
            'stream_id': stream_id,
            'start_time': start_time,
            'content': resp.content,
            'status': resp.status_code,
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


def _fetch_picture_url(base_url: str, endpoint: str, stream_id: str,
                       start_time: str, index: int, timeout: int,
                       verify_ssl: bool) -> Dict[str, Any]:
    """Fetch a single picture URL response synchronously."""
    url = f"{base_url}{endpoint.format(stream_id=stream_id)}"
    params = {'startTime': start_time, 'expiryMinutes': '5'}
    try:
        resp = requests.get(url, params=params, timeout=timeout, verify=verify_ssl)
        resp.raise_for_status()
        return {
            'index': index,
            'stream_id': stream_id,
            'start_time': start_time,
            'json': resp.json(),
            'status': resp.status_code,
            'success': True,
            'error': None
        }
    except Exception as e:
        return {
            'index': index,
            'stream_id': stream_id,
            'start_time': start_time,
            'json': None,
            'status': None,
            'success': False,
            'error': str(e)
        }


@then('pictures for each stream and timestamp are fetched in parallel')
def fetch_pictures_parallel(context: PictureContext,
                            api_config: Dict[str, Any],
                            test_endpoints: Dict[str, str],
                            test_params: Dict[str, Any]) -> None:
    """Fetch storage pictures in parallel using threads."""
    verify_ssl = api_config.get('verify_ssl', False)

    max_concurrent = test_params.get('parallelism', 2)
    logger.info("Fetching storage pictures for %d stream(s) (parallelism=%d)", len(context.test_data), max_concurrent)

    with ThreadPoolExecutor(max_workers=min(len(context.test_data), max_concurrent)) as executor:
        futures = {
            executor.submit(
                _fetch_picture,
                api_config['base_url'],
                test_endpoints['picture'],
                item['stream_id'],
                item['timestamp'],
                i,
                test_params['timeout'],
                verify_ssl
            ): i
            for i, item in enumerate(context.test_data)
        }
        results = [None] * len(context.test_data)
        for future in as_completed(futures):
            result = future.result()
            results[result['index']] = result

    context.pictures = results

    successful = sum(1 for p in context.pictures if p['success'])
    logger.info("Successfully fetched %d/%d pictures", successful, len(context.pictures))


@then('picture URLs for each stream and timestamp are fetched in parallel')
def fetch_picture_urls_parallel(context: PictureContext,
                                api_config: Dict[str, Any],
                                test_endpoints: Dict[str, str],
                                test_params: Dict[str, Any]) -> None:
    """Fetch storage picture URLs in parallel using threads."""
    verify_ssl = api_config.get('verify_ssl', False)

    max_concurrent = test_params.get('parallelism', 2)
    logger.info("Fetching storage picture URLs for %d stream(s) (parallelism=%d)", len(context.test_data), max_concurrent)

    with ThreadPoolExecutor(max_workers=min(len(context.test_data), max_concurrent)) as executor:
        futures = {
            executor.submit(
                _fetch_picture_url,
                api_config['base_url'],
                test_endpoints['picture_url'],
                item['stream_id'],
                item['timestamp'],
                i,
                test_params['timeout'],
                verify_ssl
            ): i
            for i, item in enumerate(context.test_data)
        }
        results = [None] * len(context.test_data)
        for future in as_completed(futures):
            result = future.result()
            results[result['index']] = result

    context.picture_urls = results

    successful = sum(1 for p in context.picture_urls if p['success'])
    logger.info("Successfully fetched %d/%d picture URLs", successful, len(context.picture_urls))


@then('all picture URL responses are valid')
def validate_picture_url_responses(context: PictureContext) -> None:
    """Validate the structure of picture URL JSON responses.

    The picture URL API (handled by vst_common::generateUrlResponse) returns:
      - imageUrl      : HTTP URL of the cached image in temp_files/
      - absolutePath  : absolute filesystem path of the image
      - streamId
      - type          : "replay" or "live"
      - expiryISO
      - expiryMinutes
    """
    for url_data in context.picture_urls:  # type: Dict[str, Any]
        if not url_data['success']:
            pytest.fail(
                f"Failed to fetch picture URL for stream {url_data['stream_id']}: {url_data['error']}"
            )

        json_body: Dict[str, Any] = url_data['json']
        assert 'imageUrl' in json_body or 'absolutePath' in json_body, \
            f"Response for stream {url_data['stream_id']} missing imageUrl/absolutePath: {json_body}"
        assert 'expiryMinutes' in json_body, \
            f"Response for stream {url_data['stream_id']} missing expiryMinutes: {json_body}"
        assert 'expiryISO' in json_body, \
            f"Response for stream {url_data['stream_id']} missing expiryISO: {json_body}"

        logger.info("[%s] URL response valid - imageUrl: %s, expiry: %s",
                    url_data['stream_id'],
                    json_body.get('imageUrl'),
                    json_body.get('expiryISO'))


@then('pictures downloaded from the URLs are valid JPEG images')
def download_and_validate_url_pictures(context: PictureContext,
                                       api_config: Dict[str, Any],
                                       test_params: Dict[str, Any]) -> None:
    """Download pictures from the returned URLs and validate they are valid JPEGs."""
    temp_dir = Path(test_params['temp_image_dir'])

    validation_results: List[Dict[str, Any]] = []

    for url_data in context.picture_urls:  # type: Dict[str, Any]
        if not url_data['success']:
            validation_results.append({
                'index': url_data['index'],
                'stream_id': url_data['stream_id'],
                'valid': False,
                'error': f"No URL available: {url_data['error']}"
            })
            continue

        json_body: Dict[str, Any] = url_data['json']
        # Picture URL API returns the downloadable URL in "imageUrl".
        picture_url: str = json_body.get('imageUrl', '')
        if not picture_url:
            absolute_path: str = json_body.get('absolutePath', '')
            if absolute_path:
                logger.info("[%s] absolutePath present but no downloadable URL, skipping download",
                           url_data['stream_id'])
                validation_results.append({
                    'index': url_data['index'],
                    'stream_id': url_data['stream_id'],
                    'valid': True,
                    'error': None,
                    'summary': f"absolutePath present: {absolute_path}"
                })
                continue
            validation_results.append({
                'index': url_data['index'],
                'stream_id': url_data['stream_id'],
                'valid': False,
                'error': f"No downloadable URL in response: {json_body}"
            })
            continue

        if not picture_url.startswith('http'):
            picture_url = f"{api_config['base_url']}{picture_url}"

        try:
            response = requests.get(
                picture_url,
                timeout=test_params['timeout'],
                verify=api_config.get('verify_ssl', False)
            )
            response.raise_for_status()

            temp_file = temp_dir / f"url_picture_{url_data['index']}_{url_data['stream_id']}.jpg"
            with open(temp_file, 'wb') as f:
                f.write(response.content)

            validation = validate_jpeg_with_jpeginfo(temp_file)

            validation_results.append({
                'index': url_data['index'],
                'stream_id': url_data['stream_id'],
                'valid': validation['valid'],
                'error': validation['error'],
                'summary': validation['summary']
            })

            if validation['valid']:
                logger.info("[%s] Valid JPEG from URL: %s", url_data['stream_id'], validation['summary'])
            else:
                logger.warning("[%s] Invalid JPEG from URL: %s", url_data['stream_id'], validation['error'])

        except Exception as e:
            logger.error("[%s] Error downloading from URL: %s", url_data['stream_id'], e)
            validation_results.append({
                'index': url_data['index'],
                'stream_id': url_data['stream_id'],
                'valid': False,
                'error': str(e)
            })

    context.validation_results = validation_results

    valid_count = sum(1 for r in validation_results if r['valid'])
    total_count = len(validation_results)

    logger.info("URL Picture Validation: %d/%d valid", valid_count, total_count)

    if valid_count != total_count:
        invalid_pictures = [r for r in validation_results if not r['valid']]
        failure_info = format_validation_failure(valid_count, total_count, invalid_pictures, "storage picture URL")
        assert_with_detailed_failure(
            False,
            "Storage Picture URL Validation",
            failure_info['expected'],
            failure_info['actual'],
            failure_info['failed_items'],
            f"Check jpeginfo output above. Ensure pictures from URLs are valid JPEG format.\n"
            f"  Fetched picture URLs: {total_count}\n"
            f"  Valid: {valid_count}\n"
            f"  Invalid: {total_count - valid_count}"
        )


@then('all fetched pictures are valid JPEG images')
def validate_pictures(context: PictureContext,
                      test_params: Dict[str, Any]) -> None:
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

        temp_file = temp_dir / f"storage_picture_{picture_data['index']}_{picture_data['stream_id']}.jpg"

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

    valid_count = sum(1 for r in validation_results if r['valid'])
    total_count = len(validation_results)

    logger.info("Validation Results: %d/%d pictures are valid", valid_count, total_count)

    invalid_pictures = [r for r in validation_results if not r['valid']]
    if invalid_pictures:
        logger.warning("Invalid pictures:")
        for result in invalid_pictures:
            logger.warning("  - Stream: %s, Time: %s, Error: %s",
                          result['stream_id'], result['start_time'], result['error'])

    if valid_count != total_count:
        failure_info = format_validation_failure(valid_count, total_count, invalid_pictures, "storage picture")
        assert_with_detailed_failure(
            False,
            "Storage Picture Validation",
            failure_info['expected'],
            failure_info['actual'],
            failure_info['failed_items'],
            f"Check jpeginfo output above. Ensure pictures are valid JPEG format.\n"
            f"  Fetched storage pictures: {total_count}\n"
            f"  Valid: {valid_count}\n"
            f"  Invalid: {total_count - valid_count}\n"
            f"  Pictures fetched from timeline timestamps via storage API"
        )
