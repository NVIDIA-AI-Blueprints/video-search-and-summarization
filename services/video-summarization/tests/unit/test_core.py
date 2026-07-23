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
Core unit tests for VIA Engine.

These tests are lightweight, fast, and don't require GPU or external services.
They test core utility functions and logic.
"""
import pytest

from ..common import REPO_ROOT, SRC_DIR, convert_seconds_to_string


@pytest.mark.unit
def test_repo_root_path_exists():
    """Test that REPO_ROOT points to an existing directory."""
    assert REPO_ROOT.exists()
    assert REPO_ROOT.is_dir()


@pytest.mark.unit
def test_src_dir_path_exists():
    """Test that SRC_DIR points to the src directory."""
    assert SRC_DIR.exists()
    assert SRC_DIR.is_dir()
    assert SRC_DIR.name == "src"
    assert SRC_DIR.parent == REPO_ROOT


@pytest.mark.unit
@pytest.mark.parametrize(
    "seconds,expected",
    [
        (0, "00:00"),
        (59, "00:59"),
        (60, "01:00"),
        (61, "01:01"),
        (3599, "59:59"),
        (3600, "01:00:00"),
        (3661, "01:01:01"),
    ],
)
def test_convert_seconds_to_string_basic(seconds, expected):
    """Test conversion of seconds to time string format."""
    result = convert_seconds_to_string(seconds)
    assert result == expected


@pytest.mark.unit
def test_convert_seconds_to_string_with_milliseconds():
    """Test conversion with milliseconds enabled."""
    result = convert_seconds_to_string(1.5, millisec=True)
    assert result == "00:01.50"

    result = convert_seconds_to_string(61.25, millisec=True)
    assert result == "01:01.25"


@pytest.mark.unit
def test_convert_seconds_to_string_force_hour():
    """Test conversion with forced hour display."""
    result = convert_seconds_to_string(30, need_hour=True)
    assert result == "00:00:30"

    result = convert_seconds_to_string(90, need_hour=True)
    assert result == "00:01:30"


@pytest.mark.unit
def test_pyproject_toml_exists():
    """Test that pyproject.toml exists in the repository root."""
    pyproject_path = REPO_ROOT / "pyproject.toml"
    assert pyproject_path.exists(), f"pyproject.toml not found at {pyproject_path}"
    assert pyproject_path.is_file(), f"{pyproject_path} is not a file"

    # Verify basic file properties without reading content
    assert pyproject_path.stat().st_size > 0, "pyproject.toml is empty"
