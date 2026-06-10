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
BDD tests for upload sensorId parameter handling.
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

scenarios('../../features/file_upload/sensor_id_handling.feature')


@given('the VST API is configured for file uploads')
def vst_api_configured_for_uploads(api_config):
    """Verify VST API configuration is available for file uploads."""
    assert api_config['base_url'], "Base URL must be configured"
    logger.info("VST API configured at: %s", api_config['base_url'])


@when('a file is uploaded without sensorId parameter')
def upload_without_sensor_id(context, api_config):
    """Upload a file without providing sensorId parameter."""
    context.temp_dir = Path(tempfile.mkdtemp(prefix='vst_race_test_'))
    
    filename = f"test_upload_no_sensorid_{uuid.uuid4().hex[:8]}.mp4"
    file_path = context.temp_dir / filename
    create_test_video_file(file_path, duration_seconds=1)
    
    response = upload_file_simple(
        api_config['base_url'],
        file_path,
        filename,
        sensor_id=None,
        verify_ssl=api_config.get('verify_ssl', False)
    )
    
    context.upload_response = response
    try:
        context.generated_sensor_id = response.json().get('sensorId')
    except Exception:
        context.generated_sensor_id = None
    
    logger.info("Upload without sensorId result: %d", response.status_code)


@then('the upload should succeed with 200 OK')
def verify_upload_succeeded(context):
    """Verify upload succeeded with 200 OK."""
    assert context.upload_response.status_code in [200, 201], \
        f"Expected 200/201, but got {context.upload_response.status_code}"
    logger.info("Upload succeeded with status %d", context.upload_response.status_code)


@then('the response should contain a generated sensorId')
def verify_generated_sensor_id(context):
    """Verify response contains a server-generated sensorId."""
    assert context.upload_response.status_code in [200, 201], \
        f"Upload failed: {context.upload_response.status_code}"
    
    response_data = context.upload_response.json()
    sensor_id = response_data.get('sensorId')
    stream_id = response_data.get('streamId')
    
    assert sensor_id, "Response missing sensorId field"
    context.sensor_id = sensor_id
    
    if stream_id:
        context.uploaded_stream_ids.add(stream_id)
        logger.debug("Tracked streamId for cleanup: %s", stream_id)
    
    logger.info("Server generated sensorId: %s", sensor_id)


@then('the sensorId should be a valid UUID')
def verify_sensor_id_is_uuid(context):
    """Verify the sensorId is a valid UUID format."""
    sensor_id = context.sensor_id
    
    try:
        uuid.UUID(sensor_id)
        logger.info("SensorId is valid UUID: %s", sensor_id)
    except ValueError:
        pytest.fail(f"SensorId is not a valid UUID: {sensor_id}")


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
