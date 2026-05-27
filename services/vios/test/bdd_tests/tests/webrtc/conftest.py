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
Shared fixtures for WebRTC BDD tests.

Each test scenario is completely isolated with its own:
- Fresh context instance
- Independent WebRTC connections
- Automatic cleanup after scenario completes

Note: WebRTC tests don't create persistent resources on the server,
so cleanup is minimal (just context cleanup).
"""
import logging

import pytest

logger = logging.getLogger(__name__)

# Note: context fixture is provided by each test file
# because they use specialized ScenarioContext classes


@pytest.fixture(scope="function")
def test_params(config):
    """
    Get test parameters for WebRTC tests from config.
    
    Scope: function - fresh parameters for each scenario.
    """
    return config['tests']['webrtc_tests']['test_parameters']


@pytest.fixture(scope="function", autouse=True)
def cleanup_after_scenario(request):
    """
    Cleanup after each scenario completes.
    
    WebRTC tests don't create persistent resources on the server,
    so this is mainly for logging and potential future cleanup needs.
    
    Scope: function - runs after each individual scenario
    """
    scenario_name = request.node.name
    logger.info("Starting WebRTC scenario: %s", scenario_name)
    
    yield
    
    logger.info("Completed WebRTC scenario: %s", scenario_name)
