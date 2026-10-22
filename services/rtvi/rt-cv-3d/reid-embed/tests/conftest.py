# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

"""Shared configuration for the top-level ReID service tests."""

import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def pytest_addoption(parser):
    parser.addoption(
        "--image-ref",
        default=None,
        help="reid-embed image to start for integration tests (or set IMAGE_REF).",
    )
    parser.addoption(
        "--kafka-bootstrap",
        default="localhost:9092",
        help="Kafka bootstrap reachable from the test host.",
    )
    parser.addoption(
        "--keep-stack",
        action="store_true",
        default=False,
        help="Leave the integration compose stack up after the test.",
    )


@pytest.fixture(scope="session")
def image_ref(request) -> str:
    ref = request.config.getoption("--image-ref") or os.getenv("IMAGE_REF")
    if not ref:
        pytest.skip("set --image-ref or IMAGE_REF to test a built image")
    return ref
