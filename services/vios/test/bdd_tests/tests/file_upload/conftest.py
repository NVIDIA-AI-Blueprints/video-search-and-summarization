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
Shared fixtures for file upload BDD tests.

Each test scenario is completely isolated with its own:
- Fresh context instance
- Unique sensor ID
- Temporary directory
- Automatic cleanup after scenario completes

Cleanup Behavior:
- Deleting uploaded files via the storage API automatically removes the associated sensor
- No separate sensor deletion is needed
- Local temp files are always cleaned up
"""
import logging
import shutil
import time
import uuid

import pytest
import requests

from .upload_test_utils import UploadContext

logger = logging.getLogger(__name__)


@pytest.fixture(scope="function")
def context():
    """
    Create a fresh context for each scenario.
    
    Scope: function - ensures each test scenario gets a completely fresh context.
    This prevents any state leakage between scenarios.
    """
    ctx = UploadContext()
    logger.info("Created new context with sensor_id: %s", ctx.sensor_id)
    return ctx


@pytest.fixture(scope="function")
def test_params(config):
    """
    Get test parameters for file upload tests from config.
    
    Scope: function - fresh parameters for each scenario.
    """
    return config['tests']['file_upload_tests']['test_parameters']


def cleanup_uploaded_files(context, api_config, scenario_name):
    """
    Delete all uploaded files from the server using the storage file deletion API.
    
    For each uploaded streamId:
    1. Query timelines from GET /vst/api/v1/storage/size?timelines=true
    2. Delete each timeline using DELETE /vst/api/v1/storage/file/{streamId}?startTime=X&endTime=Y
    
    Note: Deleting files automatically removes the associated sensor from the server.
    """
    if not hasattr(context, 'uploaded_stream_ids') or not context.uploaded_stream_ids:
        logger.info("[%s] No uploaded files to clean up", scenario_name)
        return
    
    stream_ids_to_cleanup = context.uploaded_stream_ids.copy()
    logger.info("[%s] Cleaning up %d uploaded stream(s): %s", 
                scenario_name, len(stream_ids_to_cleanup), stream_ids_to_cleanup)
    
    try:
        storage_size_url = f"{api_config['base_url']}/vst/api/v1/storage/size?timelines=true"
        response = requests.get(
            storage_size_url,
            timeout=10,
            verify=api_config.get('verify_ssl', False)
        )
        
        if response.status_code != 200:
            logger.warning("[%s] Failed to get storage timelines: status %d", 
                          scenario_name, response.status_code)
            return
        
        storage_data = response.json()
        files_deleted = 0
        total_space_saved = 0
        
        for stream_id in stream_ids_to_cleanup:
            if stream_id not in storage_data:
                logger.debug("[%s] StreamId %s not found in storage data", 
                            scenario_name, stream_id)
                continue
            
            stream_info = storage_data[stream_id]
            timelines = stream_info.get('timelines', [])
            
            if not timelines:
                logger.debug("[%s] No timelines found for streamId %s", 
                            scenario_name, stream_id)
                continue
            
            for timeline in timelines:
                start_time = timeline.get('startTime')
                end_time = timeline.get('endTime')
                
                if not start_time or not end_time:
                    logger.warning("[%s] Invalid timeline for %s: %s", 
                                  scenario_name, stream_id, timeline)
                    continue
                
                delete_url = f"{api_config['base_url']}/vst/api/v1/storage/file/{stream_id}"
                params = {
                    'startTime': start_time,
                    'endTime': end_time
                }
                
                try:
                    del_response = requests.delete(
                        delete_url,
                        params=params,
                        timeout=10,
                        verify=api_config.get('verify_ssl', False)
                    )
                    
                    if del_response.status_code in [200, 204]:
                        files_deleted += 1
                        try:
                            result = del_response.json()
                            space_saved = result.get('spaceSaved', 0)
                            total_space_saved += space_saved
                            logger.debug("[%s] Deleted file %s [%s to %s], space saved: %d bytes", 
                                        scenario_name, stream_id, start_time, end_time, space_saved)
                        except Exception:
                            logger.debug("[%s] Deleted file %s [%s to %s]", 
                                        scenario_name, stream_id, start_time, end_time)
                    else:
                        logger.warning("[%s] Failed to delete file %s [%s to %s]: status %d", 
                                      scenario_name, stream_id, start_time, end_time, 
                                      del_response.status_code)
                except Exception as e:
                    logger.warning("[%s] Error deleting file %s [%s to %s]: %s", 
                                  scenario_name, stream_id, start_time, end_time, str(e))
        
        logger.info("[%s] Deleted %d file(s), total space saved: %d bytes", 
                   scenario_name, files_deleted, total_space_saved)
        
    except Exception as e:
        logger.warning("[%s] Error during file cleanup: %s", scenario_name, str(e))


@pytest.fixture(scope="function", autouse=True)
def cleanup_after_scenario(request, context, api_config, test_params, nvenc_pool):
    """
    Automatically clean up resources after each scenario completes.

    This fixture:
    - Runs automatically for every test scenario (autouse=True)
    - Deletes uploaded files from server storage (which also removes sensors automatically)
    - Removes temporary directories and files
    - Runs even if the test fails
    - Holds one NVENC slot for the active phase of the test so parallel
      xdist workers don't saturate the host encoder sessions and turn
      uploads into HTTP 400s

    Note: Deleting files via the storage API automatically deletes the associated sensor,
    so there's no need for separate sensor cleanup.

    Scope: function - runs after each individual scenario
    """
    scenario_name = request.node.name
    logger.info("Starting scenario: %s with sensor_id: %s", scenario_name, context.sensor_id)

    # Cooldown before kicking off the next upload scenario. Back-to-back
    # uploads under the host's V4L2-NVENC + libcuosd stack can race the
    # transcode session that the *previous* upload is still finalizing
    # and crash libcuda (cudaLaunchKernel / cuvidv4l2_open_nvenc_session
    # internal locks). The cooldown lets the prior pipeline reach its
    # NULL state before we open the next one. Tunable via
    # ``file_upload_tests.test_parameters.inter_test_cooldown_sec`` in
    # config.json.
    cooldown = test_params['inter_test_cooldown_sec']

    with nvenc_pool.acquire() as slot:
        if slot is not None:
            logger.info("[%s] Holding NVENC slot %d", scenario_name, slot)
        # Sleep while holding the slot so parallel workers cannot
        # race-acquire freed slots and bypass the cooldown -- in serial
        # mode this is equivalent to sleeping before acquire; in any
        # parallel mode it forces the documented post-test quiet period
        # regardless of which worker released the slot.
        if cooldown > 0:
            time.sleep(cooldown)
        yield

    logger.info("Cleaning up after scenario: %s", scenario_name)

    auto_cleanup = test_params.get('auto_cleanup_after_test', True)

    if auto_cleanup:
        cleanup_uploaded_files(context, api_config, scenario_name)
        logger.info("[%s] File cleanup complete (sensor automatically removed)", scenario_name)

        # Self-heal: delete any sensor explicitly created via /sensor/add
        # AND any sensor whose id matches our scenario sensor_id (in case a
        # rejected/aborted upload somehow created one). Failures here are
        # logged but never re-raised — cleanup must be best-effort.
        sensors_to_clear = set(getattr(context, 'created_sensor_ids', set()))
        if getattr(context, 'sensor_id', None):
            sensors_to_clear.add(context.sensor_id)
        for sid in sensors_to_clear:
            try:
                resp = requests.delete(
                    f"{api_config['base_url']}/vst/api/v1/sensor/{sid}",
                    timeout=10,
                    verify=api_config.get('verify_ssl', False),
                )
                logger.info("[%s] Sensor cleanup DELETE %s -> %d",
                            scenario_name, sid, resp.status_code)
            except Exception as e:
                logger.warning("[%s] Sensor cleanup DELETE %s failed: %s",
                                scenario_name, sid, e)
    else:
        logger.info("[%s] Skipping file cleanup (auto_cleanup_after_test=false) - sensor will remain on server",
                   scenario_name)

    if hasattr(context, 'temp_dir') and context.temp_dir and context.temp_dir.exists():
        try:
            shutil.rmtree(context.temp_dir)
            logger.info("[%s] Cleaned up local temporary directory: %s", 
                       scenario_name, context.temp_dir)
        except Exception as e:
            logger.warning("[%s] Failed to clean up local temp directory: %s", 
                          scenario_name, str(e))
    
    logger.info("Completed cleanup for scenario: %s", scenario_name)

