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
import pytest
import os
import tempfile
import shutil
import argparse
from mdx.analytics.core.utils.io_utils import (
    ValidateFile,
    validate_file_path,
    load_json_from_file,
)


# Test fixtures
@pytest.fixture
def temp_dir():
    """Create a temporary directory for testing"""
    temp_dir = tempfile.mkdtemp()
    yield temp_dir
    shutil.rmtree(temp_dir)


@pytest.fixture
def sample_json_file(temp_dir):
    """Create a sample JSON file"""
    file_path = os.path.join(temp_dir, "test.json")
    with open(file_path, "w") as f:
        f.write('{"key": "value"}')
    return file_path


def test_validate_file_path():
    assert validate_file_path("valid/path/file.txt") == "valid/path/file.txt"
    with pytest.raises(argparse.ArgumentTypeError):
        validate_file_path("invalid*path")


def test_load_json_from_file(sample_json_file):
    data = load_json_from_file(sample_json_file)
    assert data == {"key": "value"}


def test_validate_file_action():
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", action=ValidateFile)

    # Test with non-existent file
    with pytest.raises(SystemExit):
        parser.parse_args(["--file", "nonexistent.txt"])
