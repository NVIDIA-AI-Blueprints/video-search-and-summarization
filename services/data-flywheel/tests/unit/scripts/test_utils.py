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

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from src.scripts.utils import validate_path


@pytest.fixture
def mock_project_root(tmp_path):
    """Fixture to create a temporary project root with test files"""
    # Create a mock project structure
    data_dir = tmp_path / "data"
    data_dir.mkdir()

    # Create a test input file
    test_file = data_dir / "test_input.txt"
    test_file.write_text("test content")

    with patch("src.scripts.utils.get_project_root", return_value=str(tmp_path)):
        yield tmp_path


def test_validate_path_input_file_exists(mock_project_root):
    """Test validation of an existing input file"""
    input_path = str(mock_project_root / "data" / "test_input.txt")
    result = validate_path(input_path, is_input=True)
    assert os.path.isfile(result)
    assert result.endswith("test_input.txt")


def test_validate_path_input_file_not_found(mock_project_root):
    """Test validation of a non-existent input file"""
    with pytest.raises(SystemExit):
        validate_path(str(mock_project_root / "data" / "nonexistent.txt"), is_input=True)


def test_validate_path_output_file_creates_dirs(mock_project_root):
    """Test that output path creates necessary directories"""
    output_path = str(mock_project_root / "data" / "new_dir" / "output.txt")
    result = validate_path(output_path, is_input=False)
    assert os.path.dirname(result) == str(mock_project_root / "data" / "new_dir")
    assert os.path.isdir(os.path.dirname(result))


def test_validate_path_absolute_path(mock_project_root):
    """Test validation with absolute path"""
    abs_path = str(mock_project_root / "data" / "test_input.txt")
    result = validate_path(abs_path, is_input=True)
    assert os.path.isfile(result)
    assert result == abs_path


def test_validate_path_outside_project(mock_project_root):
    """Test validation of path outside project directory"""
    with pytest.raises(SystemExit):
        validate_path("/tmp/outside.txt", is_input=False)


def test_validate_path_no_create_dirs(mock_project_root):
    """Test output path without directory creation"""
    output_path = str(mock_project_root / "data" / "no_create" / "output.txt")
    result = validate_path(output_path, is_input=False, create_dirs=False)
    assert not os.path.exists(os.path.dirname(result))


def test_validate_path_with_symlinks(mock_project_root):
    """Test validation with symlinks"""
    # Create a symlink to the test file
    original = mock_project_root / "data" / "test_input.txt"
    symlink = mock_project_root / "data" / "symlink.txt"
    os.symlink(original, symlink)

    result = validate_path(str(symlink), is_input=True)
    assert os.path.isfile(result)
    assert os.path.realpath(result) == os.path.realpath(original)


def test_validate_path_relative_no_data_dir(mock_project_root):
    """Test validation of relative path without data_dir"""
    input_path = str(mock_project_root / "data" / "test_input.txt")
    result = validate_path(input_path, is_input=True)
    assert os.path.isfile(result)
    assert result == input_path


def test_validate_path_empty_string(mock_project_root):
    """Test validation with empty string path"""
    with pytest.raises(SystemExit):
        validate_path("", is_input=True)


def test_validate_path_none(mock_project_root):
    """Test validation with None as path"""
    with pytest.raises(SystemExit):
        validate_path(None, is_input=True)  # type: ignore


def test_validate_path_special_chars(mock_project_root):
    """Test validation with special characters in path"""
    special_file = mock_project_root / "data" / "test file#1@.txt"
    special_file.write_text("test content")

    result = validate_path(str(special_file), is_input=True)
    assert os.path.isfile(result)
    assert result == str(special_file)


def test_validate_path_absolute_with_data_dir(mock_project_root):
    """Test that data_dir is ignored for absolute paths"""
    abs_path = str(mock_project_root / "data" / "test_input.txt")
    result = validate_path(abs_path, is_input=True, data_dir="some_other_dir")
    assert result == abs_path
    assert os.path.isfile(result)


def test_validate_path_relative_with_data_dir(mock_project_root):
    """Test relative path with data_dir parameter"""
    # Create a subdirectory in data
    subdir = mock_project_root / "data" / "subdir"
    subdir.mkdir()
    test_file = subdir / "test.txt"
    test_file.write_text("test content")

    # Test with relative path and data_dir
    result = validate_path("test.txt", is_input=True, data_dir=str(subdir))
    assert os.path.isfile(result)
    assert result == str(test_file)


def test_get_project_root():
    """Test that get_project_root returns the correct absolute path."""
    from src.scripts.utils import get_project_root

    # Mock the __file__ path to be in src/scripts/utils.py
    mock_file = Path(__file__).resolve().parent.parent.parent / "src" / "scripts" / "utils.py"

    with patch("src.scripts.utils.__file__", str(mock_file)):
        # Get the expected project root by going up three levels from the mocked file
        expected_root = str(mock_file.parent.parent.parent)

        # Get the actual project root
        actual_root = get_project_root()

        # Compare the paths
        assert actual_root == expected_root
        assert os.path.isdir(actual_root)  # Verify it's a valid directory


def test_read_data_file_csv(tmp_path):
    """Test reading CSV file"""
    from src.scripts.utils import read_data_file

    # Create a CSV file
    csv_file = tmp_path / "test.csv"
    csv_file.write_text("name,age\nAlice,30\nBob,25\n")

    data = read_data_file(str(csv_file))
    assert len(data) == 2
    assert data[0]["name"] == "Alice"
    assert data[0]["age"] == "30"


def test_read_data_file_csv_with_limit(tmp_path):
    """Test reading CSV file with limit"""
    from src.scripts.utils import read_data_file

    csv_file = tmp_path / "test.csv"
    csv_file.write_text("name,age\nAlice,30\nBob,25\nCharlie,35\n")

    data = read_data_file(str(csv_file), limit=2)
    assert len(data) == 2
    assert data[0]["name"] == "Alice"
    assert data[1]["name"] == "Bob"


def test_read_data_file_json(tmp_path):
    """Test reading JSON file"""
    from src.scripts.utils import read_data_file

    json_file = tmp_path / "test.json"
    json_file.write_text('[{"name": "Alice"}, {"name": "Bob"}]')

    data = read_data_file(str(json_file))
    assert len(data) == 2
    assert data[0]["name"] == "Alice"


def test_read_data_file_jsonl(tmp_path):
    """Test reading JSONL file"""
    from src.scripts.utils import read_data_file

    jsonl_file = tmp_path / "test.jsonl"
    jsonl_file.write_text('{"name": "Alice"}\n{"name": "Bob"}\n')

    data = read_data_file(str(jsonl_file))
    assert len(data) == 2
    assert data[0]["name"] == "Alice"
    assert data[1]["name"] == "Bob"


def test_read_data_file_json_with_limit(tmp_path):
    """Test reading JSON file with limit"""
    from src.scripts.utils import read_data_file

    json_file = tmp_path / "test.json"
    json_file.write_text('[{"name": "Alice"}, {"name": "Bob"}, {"name": "Charlie"}]')

    data = read_data_file(str(json_file), limit=2)
    assert len(data) == 2


def test_resolve_file_path_absolute_exists(tmp_path):
    """Test resolve_file_path with absolute path that exists"""
    from src.scripts.utils import resolve_file_path

    test_file = tmp_path / "test.txt"
    test_file.write_text("content")

    result = resolve_file_path(str(test_file))
    assert result == str(test_file)


def test_resolve_file_path_absolute_not_exists(tmp_path):
    """Test resolve_file_path with absolute path that doesn't exist"""
    from src.scripts.utils import resolve_file_path

    with pytest.raises(FileNotFoundError):
        resolve_file_path(str(tmp_path / "nonexistent.txt"))


def test_resolve_file_path_pathlib_exists(tmp_path):
    """Test resolve_file_path with Path object that exists"""
    from src.scripts.utils import resolve_file_path

    test_file = tmp_path / "test.txt"
    test_file.write_text("content")

    result = resolve_file_path(test_file)
    assert result == str(test_file)


def test_resolve_file_path_pathlib_not_exists(tmp_path):
    """Test resolve_file_path with Path object that doesn't exist"""
    from src.scripts.utils import resolve_file_path

    with pytest.raises(FileNotFoundError):
        resolve_file_path(tmp_path / "nonexistent.txt")
