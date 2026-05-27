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
BDD tests for B-frame video upload using PUT API.
"""
import logging
import uuid
from pathlib import Path

import pytest
import requests
from pytest_bdd import scenarios, given, when, then

logger = logging.getLogger(__name__)

scenarios('../../features/file_upload/put_bframe_upload.feature')


@given('the VST API is configured for file uploads')
def vst_api_configured(api_config):
    """Verify VST API configuration."""
    assert api_config['base_url'], "Base URL must be configured"
    logger.info("VST API configured at: %s", api_config['base_url'])


@when('a B-frame video is uploaded using PUT')
def upload_bframe_video_put_once(context, api_config):
    """Upload the B-frame video once using PUT."""
    context.upload_results = []
    bframe_video_path = Path(__file__).parent.parent.parent / 'data' / 'test_video.mp4'
    
    assert bframe_video_path.exists(), \
        f"B-frame video not found at {bframe_video_path}"
    
    context.filename = f"test_upload_bframe_put_{uuid.uuid4().hex[:8]}.mp4"
    
    url = f"{api_config['base_url']}/vst/api/v1/storage/file/{context.filename}"
    params = {
        'timestamp': "2025-01-15T00:00:00.000Z",
        'sensorId': context.sensor_id
    }
    
    with open(bframe_video_path, 'rb') as f:
        file_content = f.read()
    
    logger.info("Uploading %s (%d bytes)", context.filename, len(file_content))
    
    response = requests.put(
        url,
        params=params,
        data=file_content,
        headers={'Content-Type': 'application/octet-stream'},
        timeout=30,
        verify=api_config.get('verify_ssl', False)
    )
    
    response_data = None
    try:
        response_data = response.json()
    except Exception as e:
        logger.warning("Failed to parse response as JSON: %s", e)
    
    result = {
        'filename': context.filename,
        'status_code': response.status_code,
        'success': response.status_code in [200, 201],
        'conflict': response.status_code == 409,
        'response_json': response_data,
        'streamId': response_data.get('streamId') if response_data else None,
        'sensorId': response_data.get('sensorId') if response_data else None
    }
    
    if result.get('success') and result.get('streamId'):
        context.uploaded_stream_ids.add(result['streamId'])
    
    context.upload_results.append(result)
    context.first_upload_response = result
    
    if result['success']:
        logger.info("Upload result: SUCCESS - streamId: %s, sensorId: %s", 
                   result['streamId'], result['sensorId'])
    else:
        logger.info("Upload result: %d %s", 
                   response.status_code, 
                   "CONFLICT" if result['conflict'] else "ERROR")


@when('the same B-frame video is uploaded again using PUT')
def upload_bframe_video_put_again(context, api_config):
    """Upload the same B-frame video again using PUT."""
    bframe_video_path = Path(__file__).parent.parent.parent / 'data' / 'test_video.mp4'
    
    url = f"{api_config['base_url']}/vst/api/v1/storage/file/{context.filename}"
    params = {
        'timestamp': "2025-01-15T00:00:00.000Z",
        'sensorId': context.sensor_id
    }
    
    with open(bframe_video_path, 'rb') as f:
        file_content = f.read()
    
    response = requests.put(
        url,
        params=params,
        data=file_content,
        headers={'Content-Type': 'application/octet-stream'},
        timeout=30,
        verify=api_config.get('verify_ssl', False)
    )
    
    response_data = None
    try:
        response_data = response.json()
    except Exception:
        pass
    
    result = {
        'filename': context.filename,
        'status_code': response.status_code,
        'success': response.status_code in [200, 201],
        'conflict': response.status_code == 409,
        'response_json': response_data,
        'streamId': response_data.get('streamId') if response_data else None,
        'sensorId': response_data.get('sensorId') if response_data else None
    }
    
    context.upload_results.append(result)
    context.second_upload_response = result
    logger.info("Second upload result: %d", result['status_code'])


@then('the upload should succeed with 200 OK')
def verify_upload_success(context):
    """Verify upload succeeded."""
    result = context.first_upload_response
    assert result['success'], f"Upload failed with status {result['status_code']}"
    logger.info("Upload succeeded")


@then('the response should contain streamId and sensorId')
def verify_response_fields(context):
    """Verify response contains required fields."""
    result = context.first_upload_response
    response = result['response_json']
    
    assert response is not None, "Response is empty"
    assert response.get('streamId'), "Response missing 'streamId' field"
    assert response.get('sensorId'), "Response missing 'sensorId' field"
    assert response.get('filename'), "Response missing 'filename' field"
    
    logger.info("Response contains streamId=%s, sensorId=%s, filename=%s",
               response['streamId'], response['sensorId'], response['filename'])


@then('the sensor should appear in the sensor list API')
def verify_sensor_in_list(context, api_config):
    """Verify sensor appears in sensor list API."""
    result = context.first_upload_response
    sensor_id = result['sensorId']
    
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


@then('the file should be accessible on the server')
def verify_file_accessible_put(context, api_config):
    """Verify file is accessible via replay streams API."""
    result = context.first_upload_response
    stream_id = result.get('streamId')
    
    assert stream_id, "No streamId in upload response"
    
    streams_url = f"{api_config['base_url']}/vst/api/v1/replay/streams"
    response = requests.get(streams_url, timeout=10, verify=api_config.get('verify_ssl', False))
    
    assert response.status_code == 200, f"Streams API failed: {response.status_code}"
    
    streams_data = response.json()
    
    found = False
    for sensor_entry in streams_data:
        if isinstance(sensor_entry, dict) and context.sensor_id in sensor_entry:
            stream_list = sensor_entry[context.sensor_id]
            for stream in stream_list:
                if isinstance(stream, dict) and stream.get('streamId') == stream_id:
                    found = True
                    logger.info("Stream found in streams API")
                    break
        if found:
            break
    
    assert found, f"Stream with id={stream_id} not found in streams API"


@then('the second upload should fail with 409 Conflict')
def verify_second_upload_failed(context):
    """Verify the second upload was rejected with 409 Conflict."""
    result = context.second_upload_response
    assert result['conflict'], \
        f"Expected 409 Conflict, but got {result['status_code']}"
    logger.info("Second upload correctly rejected with 409 Conflict")


@then('only one file should exist on the server')
def verify_single_file_on_server(context, api_config):
    """Verify that only one file exists on the server."""
    successful_uploads = [r for r in context.upload_results if r['success']]
    
    assert len(successful_uploads) == 1, \
        f"Expected exactly 1 successful upload, but got {len(successful_uploads)}"
    
    upload_stream_ids = set(r['streamId'] for r in successful_uploads if r.get('streamId'))
    
    assert len(upload_stream_ids) == 1, \
        f"Expected 1 unique streamId, but got {len(upload_stream_ids)}"
    
    streams_url = f"{api_config['base_url']}/vst/api/v1/replay/streams"
    response = requests.get(streams_url, timeout=10, verify=api_config.get('verify_ssl', False))
    
    assert response.status_code == 200, f"Streams API failed: {response.status_code}"
    
    streams_data = response.json()
    server_stream_ids = set()
    
    for sensor_entry in streams_data:
        if isinstance(sensor_entry, dict) and context.sensor_id in sensor_entry:
            stream_list = sensor_entry[context.sensor_id]
            if isinstance(stream_list, list):
                for stream in stream_list:
                    if isinstance(stream, dict):
                        stream_id = stream.get('streamId')
                        if stream_id:
                            server_stream_ids.add(stream_id)
                break
    
    assert len(server_stream_ids) == 1, \
        f"Expected 1 stream on server, but found {len(server_stream_ids)}"
    
    assert upload_stream_ids == server_stream_ids, \
        f"StreamIds don't match! Upload: {upload_stream_ids}, Server: {server_stream_ids}"
    
    logger.info("Only one file exists on server")
