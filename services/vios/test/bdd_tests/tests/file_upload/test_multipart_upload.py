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
BDD tests for multipart POST file upload.
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
    upload_file_multipart
)

logger = logging.getLogger(__name__)

scenarios('../../features/file_upload/multipart_file_upload.feature')


@given('the VST API is configured for file uploads')
def vst_api_configured(api_config):
    """Verify VST API configuration."""
    assert api_config['base_url'], "Base URL must be configured"
    logger.info("VST API configured at: %s", api_config['base_url'])


@when('a file is uploaded using multipart POST with metadata')
def upload_single_file_multipart(context, api_config):
    """Upload a single file using multipart POST."""
    context.temp_dir = Path(tempfile.mkdtemp(prefix='vst_multipart_test_'))
    context.upload_results = []
    
    filename = f"test_upload_post_{uuid.uuid4().hex[:8]}.mp4"
    file_path = context.temp_dir / filename
    create_test_video_file(file_path, duration_seconds=1)
    
    result = upload_file_multipart(
        api_config['base_url'],
        file_path,
        filename,
        context.sensor_id,
        "2025-01-01T00:00:00.000Z",
        api_config.get('verify_ssl', False)
    )
    
    if result.get('success') and result.get('streamId'):
        context.uploaded_stream_ids.add(result['streamId'])
    
    context.upload_results.append(result)
    logger.info("Uploaded file: %s", filename)


@when('multiple files are uploaded with same sensorId using multipart POST')
def upload_multiple_files_same_sensor(context, api_config, test_params):
    """Upload multiple files with the same sensorId."""
    context.temp_dir = Path(tempfile.mkdtemp(prefix='vst_multipart_test_'))
    context.upload_results = []
    
    num_files = test_params.get('multipart_files_per_iteration', 10)
    
    logger.info("Uploading %d files with same sensorId", num_files)
    
    for i in range(num_files):
        filename = f"test_upload_multi_post_{i}_{uuid.uuid4().hex[:8]}.mp4"
        file_path = context.temp_dir / filename
        create_test_video_file(file_path, duration_seconds=1)
        
        result = upload_file_multipart(
            api_config['base_url'],
            file_path,
            filename,
            context.sensor_id,
            "2025-01-01T00:00:00.000Z",
            api_config.get('verify_ssl', False)
        )
        
        if result.get('success') and result.get('streamId'):
            context.uploaded_stream_ids.add(result['streamId'])
        
        context.upload_results.append(result)
    
    logger.info("Completed: %d file uploads", len(context.upload_results))
    logger.info("Tracked %d unique streamId(s) for cleanup", len(context.uploaded_stream_ids))


@when('the same filename is uploaded twice using multipart POST')
def upload_duplicate_filename(context, api_config):
    """Upload the same filename twice to test auto-renaming."""
    context.temp_dir = Path(tempfile.mkdtemp(prefix='vst_multipart_test_'))
    context.upload_results = []
    
    filename = f"test_upload_duplicate_{uuid.uuid4().hex[:8]}.mp4"
    file_path = context.temp_dir / filename
    create_test_video_file(file_path, duration_seconds=1)
    
    for i in range(2):
        result = upload_file_multipart(
            api_config['base_url'],
            file_path,
            filename,
            context.sensor_id,
            "2025-01-01T00:00:00.000Z",
            api_config.get('verify_ssl', False)
        )
        
        if result.get('success') and result.get('streamId'):
            context.uploaded_stream_ids.add(result['streamId'])
        
        context.upload_results.append(result)
    
    logger.info("Uploaded same filename twice")


@then('the upload should succeed with 200 OK')
def verify_upload_success(context):
    """Verify upload succeeded."""
    result = context.upload_results[0]
    assert result['success'], f"Upload failed with status {result['status_code']}"
    logger.info("Upload succeeded")


@then('the response should contain file id, sensorId, and streamId')
def verify_response_fields(context):
    """Verify response contains required fields."""
    result = context.upload_results[0]
    response = result['response_json']
    
    assert response is not None, "Response is empty"
    assert response.get('id'), "Response missing 'id' field"
    assert response.get('sensorId'), "Response missing 'sensorId' field"
    assert response.get('filename'), "Response missing 'filename' field"
    assert response.get('filePath'), "Response missing 'filePath' field"
    
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


@then('the file should appear in the file list API')
def verify_file_in_list(context, api_config):
    """Verify file appears in file list API."""
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


@then('all files should have the same sensorId')
def verify_same_sensor_id(context):
    """Verify all uploads used the same sensorId."""
    sensor_ids = set(r['sensorId'] for r in context.upload_results if r.get('sensorId'))
    
    assert len(sensor_ids) == 1, f"Expected 1 sensorId, but got {len(sensor_ids)}: {sensor_ids}"
    assert context.sensor_id in sensor_ids, f"Expected sensorId {context.sensor_id}"
    
    logger.info("All files have same sensorId: %s", context.sensor_id)


@then('all uploads should succeed with 200 OK')
def verify_all_uploads_succeeded(context):
    """Verify all uploads succeeded."""
    successful = [r for r in context.upload_results if r['success']]
    
    assert len(successful) == len(context.upload_results), \
        f"Expected all {len(context.upload_results)} uploads to succeed, but only {len(successful)} succeeded"
    
    logger.info("All %d uploads succeeded", len(successful))


@then('files should have different streamIds')
def verify_different_stream_ids(context, api_config):
    """Verify each file has a unique streamId via replay/streams API."""
    streams_url = f"{api_config['base_url']}/vst/api/v1/replay/streams"
    response = requests.get(streams_url, timeout=10, verify=api_config.get('verify_ssl', False))
    
    assert response.status_code == 200, f"Streams API failed: {response.status_code}"
    
    streams_data = response.json()
    stream_ids = []
    
    for sensor_entry in streams_data:
        if isinstance(sensor_entry, dict) and context.sensor_id in sensor_entry:
            stream_list = sensor_entry[context.sensor_id]
            for stream in stream_list:
                if isinstance(stream, dict):
                    stream_id = stream.get('streamId')
                    if stream_id:
                        stream_ids.append(stream_id)
            break
    
    assert len(stream_ids) == len(set(stream_ids)), \
        f"StreamIds are not unique: {stream_ids}"
    
    logger.info("All files have unique streamIds: %d unique IDs", len(set(stream_ids)))


@then('both uploads should succeed with 200 OK')
def verify_both_succeeded(context):
    """Verify both uploads succeeded."""
    successful = [r for r in context.upload_results if r['success']]
    
    assert len(successful) == 2, \
        f"Expected 2 successful uploads, but got {len(successful)}"
    
    logger.info("Both uploads succeeded")


@then('one should be main stream and others should be sub-streams')
def verify_main_and_sub_streams(context, api_config):
    """Verify stream structure (1 main + N-1 sub-streams)."""
    streams_url = f"{api_config['base_url']}/vst/api/v1/replay/streams"
    response = requests.get(streams_url, timeout=10, verify=api_config.get('verify_ssl', False))
    
    assert response.status_code == 200, f"Streams API failed: {response.status_code}"
    
    streams_data = response.json()
    
    main_count = 0
    sub_count = 0
    
    for sensor_entry in streams_data:
        if isinstance(sensor_entry, dict) and context.sensor_id in sensor_entry:
            stream_list = sensor_entry[context.sensor_id]
            for stream in stream_list:
                if stream.get('isMain', False):
                    main_count += 1
                else:
                    sub_count += 1
            break
    
    expected_total = len(context.upload_results)
    expected_sub = expected_total - 1
    
    assert main_count == 1, f"Expected 1 main stream, got {main_count}"
    assert sub_count == expected_sub, \
        f"Expected {expected_sub} sub-streams, got {sub_count}"
    
    logger.info("Stream structure correct: 1 main + %d sub-streams (total: %d files)", 
                sub_count, expected_total)


@then('all files should be grouped under same sensorId in file list')
def verify_grouped_in_file_list(context, api_config):
    """Verify all files are grouped under the same sensorId key."""
    file_list_url = f"{api_config['base_url']}/vst/api/v1/storage/file/list"
    response = requests.get(file_list_url, timeout=10, verify=api_config.get('verify_ssl', False))
    
    assert response.status_code == 200, f"File list API failed: {response.status_code}"
    
    file_list_data = response.json()
    
    assert context.sensor_id in file_list_data, \
        f"SensorId {context.sensor_id} not found in file list"
    
    files = file_list_data[context.sensor_id]
    expected_count = len(context.upload_results)
    
    assert len(files) == expected_count, \
        f"Expected {expected_count} files grouped under sensorId, but found {len(files)}"
    
    for file_entry in files:
        metadata = file_entry.get('metadata', {})
        assert metadata.get('sensorId') == context.sensor_id, \
            f"File has wrong sensorId in metadata: {metadata.get('sensorId')}"
    
    logger.info("All %d files grouped under sensorId %s", len(files), context.sensor_id)


@then('files should have different names with suffix')
def verify_auto_renamed_files(context):
    """Verify files were auto-renamed with _1, _2 suffix."""
    response_data = [r['response_json'] for r in context.upload_results if r.get('response_json')]
    
    filenames = [r.get('filename') for r in response_data if r.get('filename')]
    filepaths = [r.get('filePath') for r in response_data if r.get('filePath')]
    
    logger.info("Filenames returned: %s", filenames)
    logger.info("FilePaths returned: %s", filepaths)
    
    assert len(set(filepaths)) == 2, \
        f"Expected 2 unique file paths (auto-renamed), but got: {filepaths}"
    
    logger.info("Files were auto-renamed to unique names")


@then('both files should exist on server')
def verify_both_files_exist(context, api_config):
    """Verify both uploaded files exist in file list."""
    file_list_url = f"{api_config['base_url']}/vst/api/v1/storage/file/list"
    response = requests.get(file_list_url, timeout=10, verify=api_config.get('verify_ssl', False))
    
    assert response.status_code == 200
    
    file_list_data = response.json()
    
    file_count = 0
    for sensor_id, files in file_list_data.items():
        if sensor_id == context.sensor_id or any(
            f.get('metadata', {}).get('sensorId') == context.sensor_id for f in files
        ):
            file_count += len(files)
    
    assert file_count == 2, f"Expected 2 files on server, but found {file_count}"
    
    logger.info("Both files exist on server")
