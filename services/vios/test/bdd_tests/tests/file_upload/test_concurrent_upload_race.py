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
BDD tests for concurrent file upload race conditions.
"""
import logging
import shutil
import tempfile
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pytest
import requests
from pytest_bdd import scenarios, given, when, then, parsers

from .upload_test_utils import (
    upload_file_sync
)

logger = logging.getLogger(__name__)

scenarios('../../features/file_upload/concurrent_upload_race.feature')


@given('the VST API is configured for file uploads')
def vst_api_configured_for_uploads(api_config):
    """Verify VST API configuration is available for file uploads."""
    assert api_config['base_url'], "Base URL must be configured"
    logger.info("VST API configured at: %s", api_config['base_url'])


def get_static_video_path() -> Path:
    """Get path to the static test video file."""
    # Look for static video file in data directory
    static_video = Path(__file__).parent.parent.parent / "data" / "test_video.mp4"
    if not static_video.exists():
        raise FileNotFoundError(f"Static test video not found: {static_video}")
    return static_video


@given('a test video file is prepared')
def prepare_single_test_file(context):
    """Prepare a single test video file for concurrent upload using static video."""
    context.temp_dir = Path(tempfile.mkdtemp(prefix='vst_race_test_'))
    
    filename = f"test_upload_race_{uuid.uuid4().hex[:8]}.mp4"
    file_path = context.temp_dir / filename
    
    # Copy static video file instead of generating
    static_video = get_static_video_path()
    shutil.copy2(static_video, file_path)
    
    context.test_files = [{
        'filename': filename,
        'path': file_path,
        'size': file_path.stat().st_size
    }]
    
    logger.info("Prepared test file: %s (%d bytes) [using static video]", filename, context.test_files[0]['size'])


@given('multiple test video files are prepared with unique names')
def prepare_multiple_test_files(context, test_params):
    """Prepare multiple test video files with unique names using static video."""
    context.temp_dir = Path(tempfile.mkdtemp(prefix='vst_race_test_'))
    
    num_files = test_params.get('concurrent_different_files_count', 10)
    context.test_files = []
    static_video = get_static_video_path()
    
    for i in range(num_files):
        filename = f"test_upload_unique_{uuid.uuid4().hex[:8]}.mp4"
        file_path = context.temp_dir / filename
        
        # Copy static video file instead of generating
        shutil.copy2(static_video, file_path)
        
        context.test_files.append({
            'filename': filename,
            'path': file_path,
            'size': file_path.stat().st_size
        })
    
    logger.info("Prepared %d unique test files [using static video]", len(context.test_files))


@when('multiple threads upload the same file concurrently using new PUT API')
def upload_same_file_concurrently(context, api_config, test_params):
    """Upload the same file from multiple threads concurrently.

    Submissions are staggered by ``upload_submit_stagger_sec`` (default
    100ms) so the server's V4L2-NVENC encoder sessions do not all open
    in the same microsecond -- a known libnvcuvid race
    (cuvidv4l2_open_nvenc_session) that segfaults the host service when
    triggered. The races between the still-overlapping uploads are
    exactly what this test exercises; only the simultaneous-open burst
    is shaped out.
    """
    num_threads = test_params['race_condition_thread_count']
    submit_stagger = test_params['upload_submit_stagger_sec']

    filename = f"test_upload_race_{uuid.uuid4().hex[:8]}.mp4"
    file_path = context.temp_dir / filename

    # Copy static video file instead of generating
    static_video = get_static_video_path()
    shutil.copy2(static_video, file_path)

    logger.info("Starting %d concurrent uploads of file: %s [using static video]", num_threads, filename)

    context.upload_results = []

    with ThreadPoolExecutor(max_workers=num_threads) as executor:
        futures = []
        for i in range(num_threads):
            futures.append(executor.submit(
                upload_file_sync,
                api_config['base_url'],
                file_path,
                filename,
                context.sensor_id,
                api_config.get('verify_ssl', False),
                i,
            ))
            if submit_stagger > 0:
                time.sleep(submit_stagger)

        for future in as_completed(futures):
            result = future.result()
            context.upload_results.append(result)
            if result.get('success') and result.get('streamId'):
                context.uploaded_stream_ids.add(result['streamId'])

    logger.info("Completed: %d upload attempts", len(context.upload_results))
    logger.info("Tracked %d unique streamId(s) for cleanup", len(context.uploaded_stream_ids))


@when('multiple threads upload different files concurrently using new PUT API')
def upload_different_files_concurrently(context, api_config, test_params):
    """Upload different files: first file sequentially (main stream), rest concurrently (sub-streams)."""
    context.upload_results = []
    base_url = api_config['base_url']
    verify_ssl = api_config.get('verify_ssl', False)

    first_file = context.test_files[0]
    logger.info("Uploading main stream first: %s", first_file['filename'])
    main_result = upload_file_sync(
        base_url, first_file['path'], first_file['filename'],
        context.sensor_id, verify_ssl, 0
    )
    context.upload_results.append(main_result)
    if main_result.get('success') and main_result.get('streamId'):
        context.uploaded_stream_ids.add(main_result['streamId'])
    assert main_result.get('success'), \
        f"Main stream upload failed: {main_result.get('error') or main_result.get('status_code')}"
    logger.info("Main stream created: streamId=%s", main_result.get('streamId'))

    remaining_files = context.test_files[1:]
    logger.info("Uploading %d sub-streams concurrently", len(remaining_files))

    # If the scenario was configured with only the main file (e.g.
    # concurrent_different_files_count=1) there are no sub-streams to
    # upload. ThreadPoolExecutor(max_workers=0) raises ValueError, so
    # short-circuit here -- the main stream was already uploaded above
    # and that is all the scenario asks for in this case.
    if not remaining_files:
        logger.info("No sub-streams to upload; completed with main stream only")
        logger.info("Tracked %d unique streamId(s) for cleanup", len(context.uploaded_stream_ids))
        return

    submit_stagger = test_params['upload_submit_stagger_sec']

    # Stagger by upload_submit_stagger_sec to avoid the libnvcuvid
    # race on simultaneous NVENC session opens (see comment on
    # upload_same_file_concurrently).
    with ThreadPoolExecutor(max_workers=len(remaining_files)) as executor:
        futures = []
        for i, test_file in enumerate(remaining_files):
            futures.append(executor.submit(
                upload_file_sync, base_url,
                test_file['path'], test_file['filename'],
                context.sensor_id, verify_ssl, i + 1,
            ))
            if submit_stagger > 0:
                time.sleep(submit_stagger)

        for future in as_completed(futures):
            result = future.result()
            context.upload_results.append(result)
            if result.get('success') and result.get('streamId'):
                context.uploaded_stream_ids.add(result['streamId'])

    logger.info("Completed: %d upload attempts", len(context.upload_results))
    logger.info("Tracked %d unique streamId(s) for cleanup", len(context.uploaded_stream_ids))


@then('only one upload should succeed with 200 OK')
def verify_single_success(context):
    """Verify that exactly one upload succeeded."""
    successful_uploads = [r for r in context.upload_results if r['success']]
    
    logger.info("Successful uploads: %d", len(successful_uploads))
    for result in successful_uploads:
        logger.info("  Thread %d: %s", result['thread_id'], result['filename'])
    
    assert len(successful_uploads) == 1, \
        f"Expected exactly 1 successful upload, but got {len(successful_uploads)}. " \
        f"This indicates a race condition bug!"


@then('all other uploads should fail with 409 Conflict')
def verify_conflicts(context):
    """Verify that all other uploads failed with 409 Conflict."""
    conflict_uploads = [r for r in context.upload_results if r['conflict']]
    error_uploads = [r for r in context.upload_results if r['error'] is not None]
    successful_uploads = [r for r in context.upload_results if r['success']]
    
    expected_conflicts = len(context.upload_results) - len(successful_uploads)
    actual_conflicts = len(conflict_uploads)
    
    logger.info("Conflict responses (409): %d", actual_conflicts)
    logger.info("Error responses: %d", len(error_uploads))
    logger.info("Expected conflicts: %d (total attempts: %d, successful: %d)", 
                expected_conflicts, len(context.upload_results), len(successful_uploads))
    
    min_expected_conflicts = int(expected_conflicts * 0.8)
    
    assert actual_conflicts >= min_expected_conflicts, \
        f"Expected at least {min_expected_conflicts} conflict responses (409), " \
        f"but got {actual_conflicts}. Errors: {len(error_uploads)}"


@then('only one file should exist on the server')
def verify_single_file_on_server(context, api_config):
    """Verify that only one stream/file was created on the server."""
    successful_uploads = [r for r in context.upload_results if r['success']]
    upload_stream_ids = set(r['streamId'] for r in successful_uploads if r.get('streamId'))
    upload_sensor_ids = set(r['sensorId'] for r in successful_uploads if r.get('sensorId'))
    
    logger.info("From upload responses:")
    logger.info("  Successful uploads: %d", len(successful_uploads))
    logger.info("  Unique streamIds: %s", upload_stream_ids)
    logger.info("  Unique sensorIds: %s", upload_sensor_ids)
    
    assert len(upload_stream_ids) == 1, \
        f"Expected 1 unique streamId from successful uploads, but got {len(upload_stream_ids)}"
    assert len(upload_sensor_ids) == 1, \
        f"Expected 1 unique sensorId from successful uploads, but got {len(upload_sensor_ids)}"
    
    streams_url = f"{api_config['base_url']}/vst/api/v1/replay/streams"
    try:
        response = requests.get(
            streams_url,
            timeout=10,
            verify=api_config.get('verify_ssl', False)
        )
        
        if response.status_code == 200:
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
            
            logger.info("From /replay/streams:")
            logger.info("  StreamIds on server: %s", server_stream_ids)
            logger.info("  Total streams: %d", len(server_stream_ids))
            
            assert upload_stream_ids == server_stream_ids, \
                f"StreamIds don't match! Upload: {upload_stream_ids}, Server: {server_stream_ids}"
            
            logger.info("Upload streamIds match server streamIds")
            
    except AssertionError:
        raise
    except Exception as e:
        logger.warning("Could not verify against /replay/streams: %s", str(e))


@then('all uploads should succeed with 200 OK')
def verify_all_success(context):
    """Verify that all uploads succeeded."""
    successful_uploads = [r for r in context.upload_results if r['success']]
    
    logger.info("Successful uploads: %d/%d", len(successful_uploads), len(context.upload_results))
    
    failed_uploads = [r for r in context.upload_results if not r['success']]
    if failed_uploads:
        logger.warning("Failed uploads:")
        for result in failed_uploads:
            logger.warning("  Thread %d: %s - Status: %s, Error: %s",
                          result['thread_id'], result['filename'],
                          result['status_code'], result.get('error'))
    
    assert len(successful_uploads) == len(context.upload_results), \
        f"Expected all {len(context.upload_results)} uploads to succeed, " \
        f"but only {len(successful_uploads)} succeeded"


@then('all files should exist on the server')
def verify_all_files_on_server(context, api_config, test_params):
    """Verify that all streams/files were created on the server."""
    successful_uploads = [r for r in context.upload_results if r['success']]
    
    upload_stream_ids = set()
    upload_sensor_ids = set()
    upload_file_ids = set()
    for r in successful_uploads:
        if r.get('streamId'):
            upload_stream_ids.add(r['streamId'])
        if r.get('sensorId'):
            upload_sensor_ids.add(r['sensorId'])
        if r.get('response_json') and r['response_json'].get('id'):
            upload_file_ids.add(r['response_json']['id'])
    
    logger.info("From upload responses:")
    logger.info("  Successful uploads: %d", len(successful_uploads))
    logger.info("  Unique streamIds (%d): %s", len(upload_stream_ids), upload_stream_ids)
    logger.info("  Unique sensorIds (%d): %s", len(upload_sensor_ids), upload_sensor_ids)
    logger.info("  Unique fileIds (%d): %s", len(upload_file_ids), upload_file_ids)
    
    expected_total_uploads = len(context.upload_results)
    
    assert len(successful_uploads) == expected_total_uploads, \
        f"Expected all {expected_total_uploads} uploads to succeed, but got {len(successful_uploads)}"
    
    assert len(upload_stream_ids) == expected_total_uploads, \
        f"Expected {expected_total_uploads} unique streamIds, but got {len(upload_stream_ids)}"
    
    assert len(upload_sensor_ids) == 1, \
        f"Expected exactly 1 unique sensorId, but got {len(upload_sensor_ids)}"
    assert context.sensor_id in upload_sensor_ids, \
        f"Test sensorId {context.sensor_id} not found in upload responses"
    
    file_list_url = f"{api_config['base_url']}/vst/api/v1/storage/file/list"
    try:
        response = requests.get(
            file_list_url,
            timeout=10,
            verify=api_config.get('verify_ssl', False)
        )
        
        if response.status_code == 200:
            file_list_data = response.json()
            
            logger.info("From /storage/file/list (all files):")
            
            if context.sensor_id in file_list_data:
                files = file_list_data[context.sensor_id]
                logger.info("  Total files for sensor %s: %d", context.sensor_id, len(files))
                
                file_list_ids = set()
                file_list_sensor_ids = set()
                
                for file_entry in files:
                    if isinstance(file_entry, dict) and 'metadata' in file_entry:
                        metadata = file_entry['metadata']
                        if isinstance(metadata, dict):
                            file_id = metadata.get('id')
                            sensor_id_in_metadata = metadata.get('sensorId')
                            
                            if file_id:
                                file_list_ids.add(file_id)
                            if sensor_id_in_metadata:
                                file_list_sensor_ids.add(sensor_id_in_metadata)
                
                logger.info("  File IDs in list: %d", len(file_list_ids))
                logger.info("  SensorIds in metadata: %s", file_list_sensor_ids)
                
                assert len(files) >= len(successful_uploads), \
                    f"Expected at least {len(successful_uploads)} files grouped under sensorId {context.sensor_id}, " \
                    f"but found {len(files)}"
                
                assert upload_file_ids.issubset(file_list_ids), \
                    f"Some uploaded file IDs not found in file list! Upload: {upload_file_ids}, File List: {file_list_ids}"
                
                assert len(file_list_sensor_ids) == 1 and context.sensor_id in file_list_sensor_ids, \
                    f"All files should have sensorId={context.sensor_id} in metadata, but got: {file_list_sensor_ids}"
                
                logger.info("File list API shows all files grouped under same sensorId")
                logger.info("All file IDs match between upload response and file list")
            else:
                pytest.fail(f"SensorId {context.sensor_id} not found in file list response. "
                           f"Files may be incorrectly grouped by streamId instead of sensorId!")
                
    except AssertionError:
        raise
    except Exception as e:
        logger.warning("Could not verify file list API: %s", str(e))
    
    streams_url = f"{api_config['base_url']}/vst/api/v1/replay/streams"
    try:
        response = requests.get(
            streams_url,
            timeout=10,
            verify=api_config.get('verify_ssl', False)
        )
        
        if response.status_code == 200:
            streams_data = response.json()
            
            server_stream_ids = set()
            main_stream_count = 0
            sub_stream_count = 0
            
            for sensor_entry in streams_data:
                if isinstance(sensor_entry, dict) and context.sensor_id in sensor_entry:
                    stream_list = sensor_entry[context.sensor_id]
                    if isinstance(stream_list, list):
                        for stream in stream_list:
                            if isinstance(stream, dict):
                                stream_id = stream.get('streamId')
                                if stream_id:
                                    server_stream_ids.add(stream_id)
                                if stream.get('isMain', False):
                                    main_stream_count += 1
                                else:
                                    sub_stream_count += 1
                        break
            
            logger.info("From /replay/streams:")
            logger.info("  Total streams for sensor %s: %d (main: %d, sub: %d)", 
                       context.sensor_id, len(server_stream_ids), main_stream_count, sub_stream_count)
            logger.info("  StreamIds on server: %s", server_stream_ids)
            
            expected_total = len(upload_stream_ids)
            expected_main = 1 if expected_total > 0 else 0
            expected_sub = max(0, expected_total - 1)
            
            logger.info("=== Verification ===")
            logger.info("Expected: %d total (%d main + %d sub)", expected_total, expected_main, expected_sub)
            
            assert len(server_stream_ids) == expected_total, \
                f"Expected {expected_total} streams on server, but found {len(server_stream_ids)}"
            assert main_stream_count == expected_main, \
                f"Expected {expected_main} main stream, but found {main_stream_count}"
            assert sub_stream_count == expected_sub, \
                f"Expected {expected_sub} sub-streams, but found {sub_stream_count}"
            
            assert upload_stream_ids == server_stream_ids, \
                f"StreamIds from uploads don't match server. Upload: {upload_stream_ids}, Server: {server_stream_ids}"
            
        else:
            pytest.fail(f"Replay streams API returned status {response.status_code}")
            
    except AssertionError:
        raise
    except Exception as e:
        pytest.fail(f"Could not verify server state: {str(e)}")
