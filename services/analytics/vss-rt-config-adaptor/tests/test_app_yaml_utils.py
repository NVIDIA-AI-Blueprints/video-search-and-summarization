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

"""Unit tests for read_yaml_file and write_yaml_file in app module."""
import pytest

import app


class TestReadYamlFile:
    """Tests for app.read_yaml_file."""

    def test_valid_yaml_returns_dict(self, tmp_path):
        """read_yaml_file returns parsed dict for valid YAML."""
        path = tmp_path / "test.yaml"
        path.write_text("a: 1\nb: 2\n")
        result = app.read_yaml_file(str(path))
        assert result == {"a": 1, "b": 2}

    def test_missing_file_raises(self, tmp_path):
        """read_yaml_file raises FileNotFoundError when file does not exist."""
        path = tmp_path / "missing.yaml"
        with pytest.raises(FileNotFoundError) as exc_info:
            app.read_yaml_file(str(path))
        assert "YAML file not found" in str(exc_info.value) or "missing.yaml" in str(exc_info.value)

    def test_invalid_yaml_raises(self, tmp_path):
        """read_yaml_file raises on invalid YAML content."""
        path = tmp_path / "bad.yaml"
        path.write_text("  invalid: [[[")
        with pytest.raises(Exception):
            app.read_yaml_file(str(path))

    def test_nested_structure(self, tmp_path):
        """read_yaml_file parses nested structures."""
        path = tmp_path / "nested.yaml"
        path.write_text("calib_file_path: /path\nbev_group_name: grp\n")
        result = app.read_yaml_file(str(path))
        assert result["calib_file_path"] == "/path"
        assert result["bev_group_name"] == "grp"


class TestWriteYamlFile:
    """Tests for app.write_yaml_file."""

    def test_round_trip(self, tmp_path):
        """write_yaml_file then read_yaml_file returns same data."""
        path = tmp_path / "out.yaml"
        data = {"x": 1, "nested": {"y": 2}}
        assert app.write_yaml_file(str(path), data) is True
        assert path.exists()
        back = app.read_yaml_file(str(path))
        assert back == data

    def test_creates_parent_dir(self, tmp_path):
        """write_yaml_file creates parent directory if missing."""
        path = tmp_path / "sub" / "dir" / "file.yaml"
        assert app.write_yaml_file(str(path), {"a": 1}) is True
        assert path.exists()
        assert app.read_yaml_file(str(path)) == {"a": 1}

    def test_returns_true_on_success(self, tmp_path):
        """write_yaml_file returns True when write succeeds."""
        path = tmp_path / "ok.yaml"
        assert app.write_yaml_file(str(path), {"k": "v"}) is True
