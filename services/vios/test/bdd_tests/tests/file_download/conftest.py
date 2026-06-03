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
Shared fixtures for file download BDD tests.

Each test scenario is completely isolated with its own:
- Fresh context instance
- Temporary directory for downloaded videos
- Automatic cleanup after scenario completes
- Cleanup of uploaded test streams
"""
import logging
import shutil
import tempfile
import time
from pathlib import Path

import pytest
import requests

from .download_test_utils import DownloadContext, ENDPOINTS

logger = logging.getLogger(__name__)


@pytest.fixture(scope="function")
def context():
    """
    Create a fresh context for each scenario.
    
    Scope: function - ensures each test scenario gets a completely fresh context.
    """
    ctx = DownloadContext()
    logger.info("Created new download context")
    return ctx


@pytest.fixture(scope="function")
def test_endpoints():
    """
    Get test endpoints configuration.
    
    Scope: function - fresh endpoints for each scenario.
    """
    return ENDPOINTS


@pytest.fixture(scope="function")
def test_params(config, request):
    """
    Get test parameters for file download tests from config.

    Scope: function - fresh parameters for each scenario.

    ``temp_download_dir`` is rewritten to a per-scenario subdirectory of
    the configured base path. Both this conftest and the test bodies
    (which independently read ``test_params['temp_download_dir']``) then
    operate on isolated dirs, so under xdist one scenario's teardown
    can no longer delete another scenario's in-flight download
    artifacts.
    """
    params = dict(config['tests']['file_download_tests']['test_parameters'])
    base = Path(params['temp_download_dir'])
    # Sanitize the nodeid so it is safe as a directory name (xdist nodes
    # can include "::" and parametrize ids contain brackets).
    scenario_slug = request.node.nodeid.replace('/', '_').replace(':', '_').replace('[', '_').replace(']', '_')
    params['temp_download_dir'] = str(base / scenario_slug)
    return params


def cleanup_uploaded_test_streams(context, api_config, scenario_name):
    """
    Delete uploaded test streams from the server using the storage file deletion API.
    
    Download tests upload test videos for testing - these need to be cleaned up.
    
    Note: Deleting files via the storage API automatically removes the associated sensor,
    so there's no need for separate sensor cleanup.
    """
    if not hasattr(context, 'uploaded_stream_ids') or not context.uploaded_stream_ids:
        logger.debug("[%s] No uploaded test streams to clean up", scenario_name)
        return
    
    stream_ids_to_cleanup = context.uploaded_stream_ids.copy()
    logger.info("[%s] Cleaning up %d uploaded test stream(s)", 
                scenario_name, len(stream_ids_to_cleanup))
    
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
        
        for stream_id in stream_ids_to_cleanup:
            if stream_id not in storage_data:
                continue
            
            stream_info = storage_data[stream_id]
            timelines = stream_info.get('timelines', [])
            
            if not timelines:
                continue
            
            for timeline in timelines:
                start_time = timeline.get('startTime')
                end_time = timeline.get('endTime')
                
                if not start_time or not end_time:
                    continue
                
                delete_url = f"{api_config['base_url']}/vst/api/v1/storage/file/{stream_id}"
                params = {'startTime': start_time, 'endTime': end_time}
                
                try:
                    del_response = requests.delete(
                        delete_url,
                        params=params,
                        timeout=10,
                        verify=api_config.get('verify_ssl', False)
                    )
                    
                    if del_response.status_code in [200, 204]:
                        files_deleted += 1
                        logger.debug("[%s] Deleted test stream %s", scenario_name, stream_id)
                except Exception as e:
                    logger.warning("[%s] Error deleting test stream %s: %s", 
                                  scenario_name, stream_id, str(e))
        
        logger.info("[%s] Deleted %d test stream(s)", scenario_name, files_deleted)
        
    except Exception as e:
        logger.warning("[%s] Error during test stream cleanup: %s", scenario_name, str(e))


@pytest.fixture(scope="function", autouse=True)
def setup_and_cleanup(request, context, api_config, test_params, nvenc_pool):
    """
    Setup and clean up resources for each test scenario.

    This fixture:
    - Creates temp directories before each test
    - Cleans up uploaded test streams after test
    - Cleans up downloaded videos after test
    - Cleans up temp upload directory
    - Runs automatically for every test scenario

    Scope: function - runs for each individual scenario

    Each scenario also holds one slot from ``nvenc_pool`` for the
    duration of the test. Every file_download scenario either uploads a
    video (server-side transcode) or pulls a sub-range (on-demand
    transcode), so without this gate parallel xdist workers saturate
    the host NVENC sessions and uploads fail with HTTP 400.
    """
    scenario_name = request.node.name

    # Create temp download directory
    temp_download_dir = Path(test_params['temp_download_dir'])
    temp_download_dir.mkdir(parents=True, exist_ok=True)
    logger.info("[%s] Created temp download directory: %s", scenario_name, temp_download_dir)

    # Create temp upload directory for test video files
    context.temp_upload_dir = Path(tempfile.mkdtemp(prefix='vst_download_test_'))
    logger.info("[%s] Created temp upload directory: %s", scenario_name, context.temp_upload_dir)

    # Cooldown before kicking off the next download scenario. The host's
    # V4L2-NVENC + libcuosd stack occasionally races back-to-back
    # transcode session opens and crashes libcuda; spacing scenarios
    # gives the prior pipeline time to release its encoder context.
    cooldown = test_params['inter_test_cooldown_sec']

    with nvenc_pool.acquire() as slot:
        if slot is not None:
            logger.info("[%s] Holding NVENC slot %d", scenario_name, slot)
        # Sleep while holding the slot so parallel workers cannot
        # race-acquire freed slots and bypass the cooldown -- in serial
        # mode this is equivalent to sleeping before acquire; in any
        # parallel mode it forces the post-test quiet period regardless
        # of which worker released the slot.
        if cooldown > 0:
            time.sleep(cooldown)
        yield
    
    logger.info("[%s] Starting cleanup", scenario_name)
    
    auto_cleanup = test_params.get('auto_cleanup_after_test', True)
    
    # Cleanup uploaded test streams
    if auto_cleanup:
        cleanup_uploaded_test_streams(context, api_config, scenario_name)

        # Self-heal: delete sensors created (or referenced) by the scenario
        # so a redeploy is not required between runs. Includes streamIds
        # treated as sensor ids and any explicit created_sensor_ids.
        sensor_ids = set(getattr(context, 'created_sensor_ids', set()))
        sensor_ids.update(getattr(context, 'uploaded_stream_ids', set()))
        if getattr(context, 'sensor_id', None):
            sensor_ids.add(context.sensor_id)
        for sid in sensor_ids:
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
        logger.info("[%s] Skipping test stream cleanup (auto_cleanup_after_test=false)",
                   scenario_name)
    
    # Cleanup downloaded videos. ``temp_download_dir`` is the
    # per-scenario subdir allocated by the test_params fixture, so we
    # remove it whole rather than glob-deleting from a shared parent
    # (the previous glob('*.mp4') variant could nuke another scenario's
    # in-flight files under xdist).
    if auto_cleanup and temp_download_dir.exists():
        try:
            shutil.rmtree(temp_download_dir, ignore_errors=True)
            logger.info("[%s] Removed per-scenario download dir: %s",
                        scenario_name, temp_download_dir)
        except Exception as e:
            logger.warning("[%s] Failed to remove download dir %s: %s",
                          scenario_name, temp_download_dir, str(e))
    
    # Cleanup temp upload directory
    if hasattr(context, 'temp_upload_dir') and context.temp_upload_dir and context.temp_upload_dir.exists():
        try:
            shutil.rmtree(context.temp_upload_dir)
            logger.info("[%s] Cleaned up temp upload directory", scenario_name)
        except Exception as e:
            logger.warning("[%s] Failed to clean up temp upload directory: %s", 
                          scenario_name, str(e))
    
    logger.info("[%s] Cleanup complete", scenario_name)
