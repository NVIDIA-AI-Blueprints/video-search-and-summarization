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
Shared fixtures for picture BDD tests.

Each test scenario is completely isolated with its own:
- Fresh context instance
- Temporary directory for downloaded pictures
- Automatic cleanup after scenario completes
"""
import logging
import shutil
from pathlib import Path

import pytest

from .picture_test_utils import PictureContext

logger = logging.getLogger(__name__)


@pytest.fixture(scope="function")
def context():
    """
    Create a fresh context for each scenario.
    
    Scope: function - ensures each test scenario gets a completely fresh context.
    """
    ctx = PictureContext()
    logger.info("Created new picture test context")
    return ctx


@pytest.fixture(scope="function")
def test_params(config):
    """
    Get test parameters for picture tests from config.
    
    Scope: function - fresh parameters for each scenario.
    """
    return config['tests']['picture_tests']['test_parameters']


@pytest.fixture(scope="function", autouse=True)
def setup_and_cleanup_temp_dir(request, test_params):
    """
    Create and clean up temporary directory for downloaded pictures.
    
    This fixture:
    - Creates temp directory before each test
    - Cleans up temp directory after each test (if configured)
    - Runs automatically for every test scenario
    
    Scope: function - runs for each individual scenario
    """
    scenario_name = request.node.name
    temp_dir = Path(test_params['temp_image_dir'])
    temp_dir.mkdir(parents=True, exist_ok=True)
    
    logger.info("[%s] Created temp image directory: %s", scenario_name, temp_dir)
    
    yield temp_dir
    
    auto_cleanup = test_params.get('auto_cleanup_after_test', True)
    
    if auto_cleanup and temp_dir.exists():
        try:
            downloaded_images = list(temp_dir.glob('*.jpg')) + list(temp_dir.glob('*.jpeg'))
            if downloaded_images:
                for img in downloaded_images:
                    img.unlink()
                logger.info("[%s] Cleaned up %d downloaded image(s)", 
                           scenario_name, len(downloaded_images))
        except Exception as e:
            logger.warning("[%s] Failed to clean up downloaded images: %s", 
                          scenario_name, str(e))
    else:
        logger.info("[%s] Keeping downloaded images (auto_cleanup_after_test=false)", 
                   scenario_name)
    
    logger.info("[%s] Cleanup complete", scenario_name)
