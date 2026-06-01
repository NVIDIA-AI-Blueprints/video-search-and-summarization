# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

"""Pytest configuration for Kafka incident E2E tests."""

from typing import Any

import pytest

from . import fixtures

# Register CLI options
def pytest_addoption(parser: Any) -> None:
    """Register CLI switches used by the Kafka incident tests."""

    parser.addoption(
        "--use-real-endpoints",
        action="store_true",
        default=False,
        help="Target actual Kafka/Redis/VST/NIM/Elastic endpoints instead of local simulators.",
    )

# Import all fixtures to make them available to pytest
from .fixtures import (
    kafka_config,
    kafka_service,
    simulators,
    topic_initializer,
    use_real_endpoints,
)

# Make fixtures available to pytest
__all__ = [
    "kafka_config",
    "kafka_service",
    "simulators", 
    "topic_initializer",
    "use_real_endpoints",
]
