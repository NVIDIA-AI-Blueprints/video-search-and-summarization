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
Shared fixtures for performance BDD tests.

Each performance test is completely isolated with:
- Fresh context instance
- Temporary directory for test files
- Automatic cleanup after test completes
- Cleanup of uploaded test streams
"""
import logging
import shutil
from pathlib import Path

import pytest

from .perf_test_utils import cleanup_streams, PerfContext

logger = logging.getLogger(__name__)


@pytest.fixture(scope="function")
def perf_params(config):
    """
    Get performance test parameters from config.
    
    Scope: function - fresh parameters for each test.
    """
    return config['tests']['performance_tests']['test_parameters']


@pytest.fixture(scope="function", autouse=True)
def setup_and_cleanup(request, context, api_config, perf_params):
    """
    Setup and clean up resources for each performance test.
    
    This fixture:
    - Creates temp directories before each test
    - Cleans up uploaded test streams after test
    - Cleans up temporary files after test
    - Runs automatically for every performance test
    
    Scope: function - runs for each individual test
    """
    test_name = request.node.name
    
    # Create temp base directory
    temp_base_dir = Path(perf_params['temp_perf_dir'])
    temp_base_dir.mkdir(parents=True, exist_ok=True)
    logger.info("[%s] Created temp base directory: %s", test_name, temp_base_dir)
    
    yield
    
    logger.info("[%s] Starting cleanup", test_name)
    
    auto_cleanup = perf_params.get('auto_cleanup_after_test', True)
    
    # Cleanup uploaded test streams
    if auto_cleanup and hasattr(context, 'uploaded_stream_ids'):
        cleanup_streams(
            api_config['base_url'],
            context.uploaded_stream_ids,
            api_config.get('verify_ssl', False)
        )
    else:
        logger.info("[%s] Skipping test stream cleanup (auto_cleanup=false or no streams)", 
                   test_name)
    
    # Cleanup temporary files
    if auto_cleanup and hasattr(context, 'temp_dir') and context.temp_dir:
        if context.temp_dir.exists():
            try:
                shutil.rmtree(context.temp_dir)
                logger.info("[%s] Cleaned up temp directory: %s", 
                           test_name, context.temp_dir)
            except Exception as e:
                logger.warning("[%s] Failed to clean up temp directory: %s", 
                              test_name, str(e))
    
    logger.info("[%s] Cleanup complete", test_name)


@pytest.fixture(scope="function")
def context():
    """
    Create a fresh performance context for each test.
    
    Scope: function - ensures each test gets a completely fresh context.
    
    This is defined here as a default, but individual tests may override it
    with their own context type.
    """
    ctx = PerfContext()
    logger.info("Created new performance test context")
    return ctx
