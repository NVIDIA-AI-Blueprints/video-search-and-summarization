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
BDD tests for file extension conflict handling.
"""
import json
import logging
import tempfile
import uuid
from pathlib import Path

import pytest
from pytest_bdd import scenarios, given, when, then

from .upload_test_utils import (
    create_test_video_file,
    upload_file_simple
)

logger = logging.getLogger(__name__)

scenarios('../../features/file_upload/file_extension_conflicts.feature')


@given('the VST API is configured for file uploads')
def vst_api_configured_for_uploads(api_config):
    """Verify VST API configuration is available for file uploads."""
    assert api_config['base_url'], "Base URL must be configured"
    logger.info("VST API configured at: %s", api_config['base_url'])


@given('a test video file with extension exists on server')
def prepare_file_with_extension_on_server(context, api_config):
    """Upload a file with extension to the server first."""
    context.temp_dir = Path(tempfile.mkdtemp(prefix='vst_race_test_'))
    context.sensor_id = f"test_upload_{uuid.uuid4()}"
    
    base_name = f"test_upload_extension_{uuid.uuid4().hex[:8]}"
    filename_with_ext = f"{base_name}.mp4"
    file_path = context.temp_dir / filename_with_ext
    create_test_video_file(file_path, duration_seconds=1)
    
    response = upload_file_simple(
        api_config['base_url'],
        file_path,
        filename_with_ext,
        sensor_id=context.sensor_id,
        verify_ssl=api_config.get('verify_ssl', False)
    )
    
    assert response.status_code in [200, 201], f"Failed to upload initial file: {response.status_code}"
    
    try:
        response_data = response.json()
        stream_id = response_data.get('streamId')
        if stream_id:
            context.uploaded_stream_ids.add(stream_id)
            logger.debug("Tracked streamId for cleanup: %s", stream_id)
    except Exception:
        pass
    
    context.base_name = base_name
    context.filename_with_ext = filename_with_ext
    logger.info("Uploaded file with extension: %s", filename_with_ext)


@when('a file without extension is uploaded with the same base name')
def upload_file_without_extension(context, api_config):
    """Try to upload a file without extension when one with extension exists."""
    file_path = context.temp_dir / context.base_name
    create_test_video_file(file_path, duration_seconds=1)
    
    response = upload_file_simple(
        api_config['base_url'],
        file_path,
        context.base_name,
        sensor_id=context.sensor_id,
        verify_ssl=api_config.get('verify_ssl', False)
    )
    
    context.second_upload_response = response
    logger.info("Upload without extension result: %d", response.status_code)


@then('the upload should fail with 409 Conflict')
def verify_upload_failed_with_conflict(context):
    """Verify the upload was rejected with 409 Conflict."""
    assert context.second_upload_response.status_code == 409, \
        f"Expected 409 Conflict, but got {context.second_upload_response.status_code}"
    logger.info("Upload correctly rejected with 409 Conflict")


@then('the error message should indicate file already exists')
def verify_error_message(context):
    """Verify the error message indicates the file already exists."""
    try:
        response_data = context.second_upload_response.json()
        error_message = response_data.get('error_message', '')
        
        assert 'already exists' in error_message.lower() or 'conflict' in error_message.lower(), \
            f"Error message doesn't indicate file exists: {error_message}"
        
        logger.info("Error message correctly indicates: %s", error_message)
    except (json.JSONDecodeError, ValueError) as e:
        logger.exception("Failed to parse error response as JSON")
        pytest.fail(f"Error response is not valid JSON: {e}\nResponse text: {context.second_upload_response.text[:200]}")


@when('a file with extension is uploaded to server')
def upload_file_with_extension(context, api_config):
    """Upload a file with extension to the server."""
    context.temp_dir = Path(tempfile.mkdtemp(prefix='vst_race_test_'))
    context.sensor_id = f"test_upload_{uuid.uuid4()}"
    
    base_name = f"test_upload_dup_{uuid.uuid4().hex[:8]}"
    filename = f"{base_name}.mp4"
    file_path = context.temp_dir / filename
    create_test_video_file(file_path, duration_seconds=1)
    
    response = upload_file_simple(
        api_config['base_url'],
        file_path,
        filename,
        sensor_id=context.sensor_id,
        verify_ssl=api_config.get('verify_ssl', False)
    )
    
    assert response.status_code in [200, 201], f"First upload failed: {response.status_code}"
    
    try:
        response_data = response.json()
        stream_id = response_data.get('streamId')
        if stream_id:
            context.uploaded_stream_ids.add(stream_id)
            logger.debug("Tracked streamId for cleanup: %s", stream_id)
    except Exception:
        pass
    
    context.filename = filename
    context.file_path = file_path
    logger.info("Uploaded file with extension: %s", filename)


@when('the same filename with extension is uploaded again')
def upload_same_filename_again(context, api_config):
    """Try to upload the exact same filename again."""
    response = upload_file_simple(
        api_config['base_url'],
        context.file_path,
        context.filename,
        sensor_id=context.sensor_id,
        verify_ssl=api_config.get('verify_ssl', False)
    )
    
    context.second_upload_response = response
    logger.info("Second upload result: %d", response.status_code)


@then('the second upload should fail with 409 Conflict')
def verify_second_upload_failed(context):
    """Verify the second upload was rejected with 409 Conflict."""
    assert context.second_upload_response.status_code == 409, \
        f"Expected 409 Conflict, but got {context.second_upload_response.status_code}"
    logger.info("Second upload correctly rejected with 409 Conflict")


@given('a test video file without extension exists on server')
def prepare_file_without_extension_on_server(context, api_config):
    """Upload a file without extension to the server first."""
    context.temp_dir = Path(tempfile.mkdtemp(prefix='vst_race_test_'))
    context.sensor_id = f"test_upload_{uuid.uuid4()}"
    
    base_name = f"test_upload_noext_{uuid.uuid4().hex[:8]}"
    file_path = context.temp_dir / f"{base_name}_temp.mp4"
    create_test_video_file(file_path, duration_seconds=1)
    
    response = upload_file_simple(
        api_config['base_url'],
        file_path,
        base_name,
        sensor_id=context.sensor_id,
        verify_ssl=api_config.get('verify_ssl', False)
    )
    
    if response.status_code not in [200, 201]:
        try:
            error_info = response.json()
            logger.error("Upload failed with: %s", json.dumps(error_info, indent=2))
        except Exception:
            logger.error("Upload failed with status %d: %s", response.status_code, response.text)
    
    assert response.status_code in [200, 201], f"First upload failed: {response.status_code}"
    
    try:
        response_data = response.json()
        stream_id = response_data.get('streamId')
        if stream_id:
            context.uploaded_stream_ids.add(stream_id)
            logger.debug("Tracked streamId for cleanup: %s", stream_id)
    except Exception:
        pass
    
    context.base_name = base_name
    context.temp_file_path = file_path
    logger.info("Uploaded file without extension: %s (server added extension)", base_name)


@when('a file with extension is uploaded with the same base name')
def upload_file_with_matching_extension(context, api_config):
    """Try to upload a file with extension when base file exists."""
    filename_with_ext = f"{context.base_name}.mp4"
    
    response = upload_file_simple(
        api_config['base_url'],
        context.temp_file_path,
        filename_with_ext,
        sensor_id=context.sensor_id,
        verify_ssl=api_config.get('verify_ssl', False)
    )
    
    context.second_upload_response = response
    logger.info("Upload with extension result: %d", response.status_code)
