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
BDD tests for upload timestamp parameter handling.
"""
import logging
import tempfile
import uuid
from pathlib import Path

import pytest
import requests
from pytest_bdd import scenarios, given, when, then

from .upload_test_utils import (
    create_test_video_file,
    upload_file_simple
)

logger = logging.getLogger(__name__)

scenarios('../../features/file_upload/timestamp_handling.feature')


@given('the VST API is configured for file uploads')
def vst_api_configured_for_uploads(api_config):
    """Verify VST API configuration is available for file uploads."""
    assert api_config['base_url'], "Base URL must be configured"
    logger.info("VST API configured at: %s", api_config['base_url'])


@when('a file is uploaded without timestamp parameter')
def upload_without_timestamp(context, api_config):
    """Upload a file without providing timestamp parameter."""
    context.temp_dir = Path(tempfile.mkdtemp(prefix='vst_race_test_'))
    context.sensor_id = f"test_upload_{uuid.uuid4()}"
    
    filename = f"test_upload_no_timestamp_{uuid.uuid4().hex[:8]}.mp4"
    file_path = context.temp_dir / filename
    create_test_video_file(file_path, duration_seconds=1)
    
    response = upload_file_simple(
        api_config['base_url'],
        file_path,
        filename,
        sensor_id=context.sensor_id,
        timestamp=None,
        verify_ssl=api_config.get('verify_ssl', False)
    )
    
    context.upload_response = response
    logger.info("Upload without timestamp result: %d", response.status_code)


@when('a file is uploaded with specific timestamp')
def upload_with_timestamp(context, api_config):
    """Upload a file with a specific timestamp."""
    context.temp_dir = Path(tempfile.mkdtemp(prefix='vst_race_test_'))
    context.sensor_id = f"test_upload_{uuid.uuid4()}"
    context.upload_timestamp = "2025-06-15T12:30:45.000Z"
    
    filename = f"test_upload_with_timestamp_{uuid.uuid4().hex[:8]}.mp4"
    file_path = context.temp_dir / filename
    create_test_video_file(file_path, duration_seconds=1)
    
    response = upload_file_simple(
        api_config['base_url'],
        file_path,
        filename,
        sensor_id=context.sensor_id,
        timestamp=context.upload_timestamp,
        verify_ssl=api_config.get('verify_ssl', False)
    )
    
    context.upload_response = response
    logger.info("Upload with timestamp %s result: %d", context.upload_timestamp, response.status_code)


@then('the upload should succeed with 200 OK')
def verify_upload_succeeded(context):
    """Verify upload succeeded with 200 OK."""
    assert context.upload_response.status_code in [200, 201], \
        f"Expected 200/201, but got {context.upload_response.status_code}"
    
    try:
        response_data = context.upload_response.json()
        stream_id = response_data.get('streamId')
        if stream_id:
            context.uploaded_stream_ids.add(stream_id)
            logger.debug("Tracked streamId for cleanup: %s", stream_id)
    except Exception:
        pass
    
    logger.info("Upload succeeded with status %d", context.upload_response.status_code)


@then('the sensor should appear in the sensor list API')
def verify_sensor_in_list(context, api_config):
    """Verify sensor appears in sensor list API."""
    sensor_id = context.sensor_id
    
    sensor_list_url = f"{api_config['base_url']}/vst/api/v1/sensor/list"
    response = requests.get(sensor_list_url, timeout=10, verify=api_config.get('verify_ssl', False))
    
    assert response.status_code == 200, f"Sensor list API failed: {response.status_code}"
    
    sensor_list_data = response.json()
    
    assert isinstance(sensor_list_data, list), "Sensor list response should be an array"
    
    found = False
    sensor_info = None
    for sensor_entry in sensor_list_data:
        if sensor_entry.get('sensorId') == sensor_id:
            found = True
            sensor_info = sensor_entry
            logger.info("Sensor found in sensor list API: name=%s, type=%s, state=%s",
                       sensor_info.get('name'), sensor_info.get('type'), sensor_info.get('state'))
            break
    
    assert found, f"Sensor with sensorId={sensor_id} not found in sensor list API"
    
    if sensor_info.get('isTimelinePresent') is not None:
        logger.info("  Timeline present: %s", sensor_info.get('isTimelinePresent'))


@then('the timeline should show epoch time as start time')
def verify_epoch_time_in_timeline(context, api_config):
    """Verify timeline shows epoch time (1970-01-01T00:00:00.000Z)."""
    storage_size_url = f"{api_config['base_url']}/vst/api/v1/storage/size?timelines=true"
    response = requests.get(storage_size_url, timeout=10, verify=api_config.get('verify_ssl', False))
    
    assert response.status_code == 200, f"Storage size API failed: {response.status_code}"
    
    size_data = response.json()
    epoch_time = "1970-01-01T00:00:00.000Z"
    
    found = False
    for stream_id, stream_info in size_data.items():
        if isinstance(stream_info, dict) and 'timelines' in stream_info:
            timelines = stream_info['timelines']
            if isinstance(timelines, list):
                for timeline in timelines:
                    if timeline.get('startTime') == epoch_time:
                        found = True
                        logger.info("Found timeline with epoch time: %s", stream_id[:30])
                        break
        if found:
            break
    
    assert found, f"Expected to find timeline with startTime={epoch_time}"
    logger.info("Timeline shows epoch time as expected")


@then('the timeline should show the provided timestamp as start time')
def verify_custom_timestamp_in_timeline(context, api_config):
    """Verify timeline shows the exact timestamp we provided."""
    response_data = context.upload_response.json()
    uploaded_sensor_id = response_data.get('sensorId')
    
    assert uploaded_sensor_id, "Upload response missing sensorId"
    
    storage_size_url = f"{api_config['base_url']}/vst/api/v1/storage/{uploaded_sensor_id}/timelines"
    response = requests.get(storage_size_url, timeout=10, verify=api_config.get('verify_ssl', False))
    
    assert response.status_code == 200, f"Timelines API failed: {response.status_code}"
    
    timelines_data = response.json()
    expected_timestamp = context.upload_timestamp
    
    logger.info("Looking for timeline with startTime: %s", expected_timestamp)
    logger.info("Timelines for sensor %s: %s", uploaded_sensor_id[:30], timelines_data)
    
    found = False
    if isinstance(timelines_data, list):
        for timeline in timelines_data:
            if isinstance(timeline, dict):
                start_time = timeline.get('startTime', '')
                logger.info("  Found timeline: startTime=%s", start_time)
                
                if start_time == expected_timestamp:
                    found = True
                    logger.info("Timeline matches expected timestamp")
                    break
    
    if not found:
        logger.error("Expected timestamp: %s", expected_timestamp)
        logger.error("Actual timelines returned: %s", timelines_data)
    
    assert found, f"Expected to find timeline with startTime={expected_timestamp}. " \
                  f"Check server logs for timestamp conversion issues."
    
    logger.info("Timeline shows provided timestamp: %s", expected_timestamp)
