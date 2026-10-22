# SPDX-FileCopyrightText: Copyright (c) 2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

"""Deployment adapters for various platforms.

This package uses a plugin-style architecture where each adapter lives
in its own subpackage (e.g., datarobot/, nmp/). This makes it easy to
add or remove adapters without modifying the core code.

To add a new adapter:
1. Create a new folder: adapters/your_platform/
2. Add __init__.py, adapter.py, and optionally config.py
3. Use @DeploymentAdapterFactory.register() decorator on your adapter class
4. Import the adapter package below to register it
"""

import logging

from src.lib.deployment.adapters.base import (
    BaseDeploymentAdapter,
    DeploymentPlatform,
    DeploymentResult,
    DeploymentStatus,
)
from src.lib.deployment.adapters.factory import DeploymentAdapterFactory

logger = logging.getLogger(__name__)

# Import adapter packages to register them with the factory
# Each adapter package auto-registers via the @DeploymentAdapterFactory.register decorator
# These imports may fail if platform-specific dependencies are not available

try:
    from src.lib.deployment.adapters import datarobot  # noqa: F401
except ImportError as e:
    logger.warning(f"DataRobot adapter not available: {e}")

try:
    from src.lib.deployment.adapters import nmp  # noqa: F401
except ImportError as e:
    logger.warning(f"NMP adapter not available: {e}")

try:
    from src.lib.deployment.adapters import custom_nim  # noqa: F401
except ImportError as e:
    logger.warning(f"Custom NIM adapter not available: {e}")


__all__ = [
    # Base classes
    "BaseDeploymentAdapter",
    # Factory
    "DeploymentAdapterFactory",
    "DeploymentPlatform",
    "DeploymentResult",
    "DeploymentStatus",
]
