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
"""Shared fixtures for the config-init suite.

Mirrors the option/fixture shape of the rt-cv-bev-fusion suite so both services
are driven the same way: the image under test is passed with ``--image-ref``.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

DEFAULT_IMAGE = "vss-rt-cv-mv3dt-config-init:latest"


def pytest_addoption(parser):
    parser.addoption(
        "--image-ref",
        action="store",
        default=None,
        help=(
            "config-init image to test, e.g. "
            "nvcr.io/nv-metropolis-dev/vss-warehouse/vss-rt-cv-mv3dt-config-init:3.3.0-26.08.1. "
            f"Defaults to {DEFAULT_IMAGE}."
        ),
    )
    parser.addoption(
        "--keep-output",
        action="store_true",
        default=False,
        help="Keep integration test output directories for inspection.",
    )


@pytest.fixture(scope="session")
def tests_dir() -> Path:
    """Directory holding this suite and its fixture data."""
    return Path(__file__).resolve().parent


@pytest.fixture(scope="session")
def container_root(tests_dir: Path) -> Path:
    """The ``rt-cv-config-init/`` directory (docker build context)."""
    return tests_dir.parent


@pytest.fixture(scope="session")
def src_dir(container_root: Path) -> Path:
    """Directory holding the generator sources shipped in the image."""
    return container_root / "src"


@pytest.fixture(scope="session", autouse=True)
def _src_on_syspath(src_dir: Path):
    """Import generators the same way the container does (flat, from /app)."""
    sys.path.insert(0, str(src_dir))
    yield
    try:
        sys.path.remove(str(src_dir))
    except ValueError:
        pass


@pytest.fixture(scope="session")
def calibration_json(tests_dir: Path) -> Path:
    path = tests_dir / "calibration.json"
    if not path.exists():
        pytest.skip(f"missing fixture data: {path}")
    return path


@pytest.fixture(scope="session")
def expected_pub_sub(tests_dir: Path) -> Path:
    path = tests_dir / "expected_pub_sub_info_config.yml"
    if not path.exists():
        pytest.skip(f"missing fixture data: {path}")
    return path


@pytest.fixture(scope="session")
def stub_image() -> str:
    """Image used for the calibration stub endpoint in the API-mode test."""
    return os.environ.get("STUB_IMAGE", "python:3.13-slim")


@pytest.fixture(scope="session")
def image_ref(request) -> str:
    return (request.config.getoption("--image-ref") or DEFAULT_IMAGE).strip()


@pytest.fixture(scope="session")
def docker_available() -> bool:
    if shutil.which("docker") is None:
        return False
    return subprocess.run(
        ["docker", "info"], capture_output=True, text=True
    ).returncode == 0


@pytest.fixture(scope="session")
def image_present(image_ref: str, docker_available: bool) -> str:
    """Skip integration tests cleanly when the image is not available locally."""
    if not docker_available:
        pytest.skip("docker is not available")
    probe = subprocess.run(
        ["docker", "image", "inspect", image_ref], capture_output=True, text=True
    )
    if probe.returncode != 0:
        pull = subprocess.run(
            ["docker", "pull", image_ref], capture_output=True, text=True
        )
        if pull.returncode != 0:
            pytest.skip(f"image not available and pull failed: {image_ref}")
    return image_ref
