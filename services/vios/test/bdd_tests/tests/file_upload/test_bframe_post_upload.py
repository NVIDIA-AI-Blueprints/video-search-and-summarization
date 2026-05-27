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
BDD tests for B-frame video upload using multipart POST.
"""
import json
import logging
import uuid
from pathlib import Path

import pytest
import requests
from pytest_bdd import scenarios, given, when, then

from .upload_test_utils import upload_bframe_multipart

logger = logging.getLogger(__name__)

scenarios('../../features/file_upload/post_bframe_upload.feature')


@given('the VST API is configured for file uploads')
def vst_api_configured(api_config):
    """Verify VST API configuration."""
    assert api_config['base_url'], "Base URL must be configured"
    logger.info("VST API configured at: %s", api_config['base_url'])


@when('a B-frame video is uploaded using multipart POST')
def upload_bframe_video_once(context, api_config):
    """Upload the B-frame video once using multipart POST."""
    context.upload_results = []
    bframe_video_path = Path(__file__).parent.parent.parent / 'data' / 'test_video.mp4'
    
    assert bframe_video_path.exists(), \
        f"B-frame video not found at {bframe_video_path}"
    
    filename = f"test_upload_bframe_post_{uuid.uuid4().hex[:8]}.mp4"
    
    result = upload_bframe_multipart(
        api_config['base_url'],
        bframe_video_path,
        filename,
        context.sensor_id,
        api_config.get('verify_ssl', False)
    )
    
    if result.get('success') and result.get('streamId'):
        context.uploaded_stream_ids.add(result['streamId'])
    
    context.upload_results.append(result)
    logger.info("Uploaded B-frame video: %s (streamId: %s)", filename, result.get('streamId'))


@when('the same B-frame video is uploaded multiple times using multipart POST')
def upload_bframe_video_multiple(context, api_config, test_params):
    """Upload the B-frame video multiple times using multipart POST."""
    context.upload_results = []
    bframe_video_path = Path(__file__).parent.parent.parent / 'data' / 'test_video.mp4'
    
    assert bframe_video_path.exists(), \
        f"B-frame video not found at {bframe_video_path}"
    
    num_uploads = test_params.get('multipart_files_per_iteration', 3)
    
    logger.info("Uploading B-frame video %d times with unique filenames", num_uploads)
    
    for i in range(num_uploads):
        filename = f"test_upload_bframe_post_multi_{uuid.uuid4().hex[:8]}.mp4"
        
        result = upload_bframe_multipart(
            api_config['base_url'],
            bframe_video_path,
            filename,
            context.sensor_id,
            api_config.get('verify_ssl', False)
        )
        
        if result.get('success') and result.get('streamId'):
            context.uploaded_stream_ids.add(result['streamId'])
        
        context.upload_results.append(result)
        logger.info("Upload %d/%d: %s (streamId: %s)", i + 1, num_uploads, filename, result.get('streamId'))
    
    logger.info("Completed: %d uploads, tracked %d streamId(s) for cleanup", 
                len(context.upload_results), len(context.uploaded_stream_ids))


@then('the upload should succeed with 200 OK')
def verify_upload_success(context):
    """Verify upload succeeded."""
    result = context.upload_results[0]
    assert result['success'], f"Upload failed with status {result['status_code']}"
    logger.info("Upload succeeded")


@then('the response should contain file id and sensorId')
def verify_response_fields(context):
    """Verify response contains required fields."""
    result = context.upload_results[0]
    response = result['response_json']
    
    assert response is not None, "Response is empty"
    assert response.get('id'), "Response missing 'id' field"
    assert response.get('sensorId'), "Response missing 'sensorId' field"
    assert response.get('filename'), "Response missing 'filename' field"
    
    logger.info("Response contains id=%s, sensorId=%s, filename=%s",
               response['id'], response['sensorId'], response['filename'])


@then('the sensor should appear in the sensor list API')
def verify_sensor_in_list(context, api_config):
    """Verify sensor appears in sensor list API."""
    result = context.upload_results[0]
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
def verify_file_accessible(context, api_config):
    """Verify file is accessible via file list API."""
    result = context.upload_results[0]
    file_id = result['fileId']
    
    file_list_url = f"{api_config['base_url']}/vst/api/v1/storage/file/list"
    response = requests.get(file_list_url, timeout=10, verify=api_config.get('verify_ssl', False))
    
    assert response.status_code == 200, f"File list API failed: {response.status_code}"
    
    file_list_data = response.json()
    
    found = False
    for sensor_id, files in file_list_data.items():
        for file_entry in files:
            if file_entry.get('metadata', {}).get('id') == file_id:
                found = True
                logger.info("File found in list API under sensor %s", sensor_id)
                break
        if found:
            break
    
    assert found, f"File with id={file_id} not found in file list API"


@then('all uploads should succeed with 200 OK')
def verify_all_uploads_succeeded(context):
    """Verify all uploads succeeded."""
    successful = [r for r in context.upload_results if r['success']]
    
    assert len(successful) == len(context.upload_results), \
        f"Expected all {len(context.upload_results)} uploads to succeed, but only {len(successful)} succeeded"
    
    logger.info("All %d uploads succeeded", len(successful))


@then('all files should have unique names')
def verify_unique_filenames(context):
    """Verify all uploaded files have unique names."""
    filenames = [r['filename'] for r in context.upload_results]
    unique_filenames = set(filenames)
    
    assert len(unique_filenames) == len(filenames), \
        f"Expected {len(filenames)} unique filenames, but got {len(unique_filenames)}"
    
    logger.info("All %d files have unique names", len(filenames))


@then('all files should exist on the server')
def verify_all_files_exist(context, api_config):
    """Verify all uploaded files exist in file list."""
    file_list_url = f"{api_config['base_url']}/vst/api/v1/storage/file/list"
    response = requests.get(file_list_url, timeout=10, verify=api_config.get('verify_ssl', False))
    
    assert response.status_code == 200, f"File list API failed: {response.status_code}"
    
    file_list_data = response.json()
    
    expected_file_ids = set(r['fileId'] for r in context.upload_results if r.get('fileId'))
    found_file_ids = set()
    
    for sensor_id, files in file_list_data.items():
        for file_entry in files:
            file_id = file_entry.get('metadata', {}).get('id')
            if file_id in expected_file_ids:
                found_file_ids.add(file_id)
    
    assert found_file_ids == expected_file_ids, \
        f"Expected {len(expected_file_ids)} files on server, but found {len(found_file_ids)}"
    
    logger.info("All %d files exist on server", len(found_file_ids))
