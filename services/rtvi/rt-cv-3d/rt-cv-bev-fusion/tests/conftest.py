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
Shared pytest configuration for the MV3DT BEV fusion test suite.

Wires up import paths so tests can `import schema_pb2` (protobuf) and
`import measurement_fusion` (the service module) directly from the
src/ source tree, and registers the common command-line options
used across the unit / integration / e2e tiers.
"""

import sys
from pathlib import Path

import pytest

# Service root = parent of the tests/ directory this file lives in.
REPO_ROOT = Path(__file__).resolve().parents[1]
# The src/ source tree ships both the service and the compiled
# protobuf schema; put it on sys.path so unit + integration tests share the
# exact same Frame definition the container runs.
MEASUREMENT_FUSION_SRC = REPO_ROOT / "src"

for _p in (str(MEASUREMENT_FUSION_SRC),):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def pytest_addoption(parser):
    """CLI options shared by the integration and e2e tiers."""
    parser.addoption(
        "--image-ref",
        action="store",
        default=None,
        help="vss-rt-cv-mv3dt-bev-fusion image ref under test "
        "(default: env IMAGE_REF, else vss-rt-cv-mv3dt-bev-fusion:local).",
    )
    parser.addoption(
        "--kafka-bootstrap",
        action="store",
        default="localhost:9092",
        help="Kafka bootstrap servers reachable from the test host.",
    )
    parser.addoption(
        "--keep-stack",
        action="store_true",
        default=False,
        help="Do not tear down docker compose stacks after the test (debugging).",
    )


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return REPO_ROOT


@pytest.fixture(scope="session")
def image_ref(request) -> str:
    import os

    return (
        request.config.getoption("--image-ref")
        or os.getenv("IMAGE_REF")
        or "vss-rt-cv-mv3dt-bev-fusion:local"
    )
