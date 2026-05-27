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
Shared fixtures for URL caching optimization BDD tests.
"""
import logging
from pathlib import Path

import pytest

from .url_caching_test_utils import CachingTestContext

logger = logging.getLogger(__name__)


@pytest.fixture(scope="function")
def context():
    """Create a fresh context for each scenario."""
    return CachingTestContext()


@pytest.fixture(scope="function")
def test_endpoints():
    """Get test endpoints configuration."""
    from .url_caching_test_utils import ENDPOINTS
    return ENDPOINTS


@pytest.fixture(scope="function")
def test_params(config):
    """Get test parameters for URL caching tests from config."""
    return config['tests']['url_optimization_tests']['test_parameters']
