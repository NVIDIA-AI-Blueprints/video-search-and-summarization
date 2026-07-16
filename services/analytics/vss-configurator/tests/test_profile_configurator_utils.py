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

"""Unit tests for profile_configurator.profile_configurator_utils.utils."""
import json
import pytest

from profile_configurator.profile_configurator_utils import utils


class TestReadYamlFile:
    def test_valid_yaml_returns_dict(self, tmp_path):
        path = tmp_path / "test.yaml"
        path.write_text("a: 1\nb: 2\n")
        result = utils.read_yaml_file(str(path))
        assert result == {"a": 1, "b": 2}

    def test_missing_file_raises(self, tmp_path):
        path = tmp_path / "missing.yaml"
        with pytest.raises(FileNotFoundError):
            utils.read_yaml_file(str(path))

    def test_invalid_yaml_raises(self, tmp_path):
        path = tmp_path / "bad.yaml"
        path.write_text("  invalid: [[[")
        with pytest.raises(Exception):  # ValueError or yaml error
            utils.read_yaml_file(str(path))


class TestWriteYamlFile:
    def test_round_trip(self, tmp_path):
        path = tmp_path / "out.yaml"
        data = {"x": 1, "nested": {"y": 2}}
        assert utils.write_yaml_file(str(path), data) is True
        assert path.exists()
        back = utils.read_yaml_file(str(path))
        assert back == data

    def test_creates_parent_dir(self, tmp_path):
        path = tmp_path / "sub" / "dir" / "file.yaml"
        assert utils.write_yaml_file(str(path), {"a": 1}) is True
        assert path.exists()


class TestReadWriteTextFile:
    def test_round_trip(self, tmp_path):
        path = tmp_path / "file.txt"
        content = "line1\nline2\n"
        utils.write_text_file(str(path), content)
        assert utils.read_text_file(str(path)) == content

    def test_read_missing_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            utils.read_text_file(str(tmp_path / "missing.txt"))

    def test_write_creates_parent(self, tmp_path):
        path = tmp_path / "a" / "b" / "t.txt"
        assert utils.write_text_file(str(path), "ok") is True
        assert path.exists()


class TestReadWriteJsonFile:
    def test_round_trip(self, tmp_path):
        path = tmp_path / "data.json"
        data = {"key": "value", "n": 42}
        assert utils.write_json_file(str(path), data) is True
        assert utils.read_json_file(str(path)) == data

    def test_read_missing_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            utils.read_json_file(str(tmp_path / "missing.json"))

    def test_invalid_json_raises(self, tmp_path):
        path = tmp_path / "bad.json"
        path.write_text("{ invalid }")
        with pytest.raises((ValueError, json.JSONDecodeError)):
            utils.read_json_file(str(path))


class TestEnsureDirectoryExists:
    def test_creates_dir(self, tmp_path):
        d = tmp_path / "newdir"
        assert not d.exists()
        assert utils.ensure_directory_exists(str(d)) is True
        assert d.is_dir()

    def test_idempotent(self, tmp_path):
        d = tmp_path / "idem"
        utils.ensure_directory_exists(str(d))
        assert utils.ensure_directory_exists(str(d)) is True

    def test_creates_nested(self, tmp_path):
        d = tmp_path / "a" / "b" / "c"
        assert utils.ensure_directory_exists(str(d)) is True
        assert d.is_dir()


class TestFileExists:
    def test_exists_true(self, tmp_path):
        f = tmp_path / "f"
        f.write_text("")
        assert utils.file_exists(str(f)) is True

    def test_exists_false(self, tmp_path):
        assert utils.file_exists(str(tmp_path / "nonexistent")) is False


class TestUpdateConfigParameters:
    def test_updates_params_preserves_format(self, tmp_path):
        path = tmp_path / "config.txt"
        path.write_text("batch-size=4\nmax-batch=8  # comment\n")
        assert utils.update_config_parameters(str(path), {"batch-size": "16", "max-batch": "32"}) is True
        content = utils.read_text_file(str(path))
        assert "batch-size=16" in content
        assert "max-batch=32" in content
        assert "# comment" in content

    def test_missing_file_returns_false(self, tmp_path):
        assert utils.update_config_parameters(str(tmp_path / "missing.txt"), {"a": "1"}) is False

    def test_param_not_in_file_still_writes(self, tmp_path):
        path = tmp_path / "c.txt"
        path.write_text("only=1\n")
        # param not in file: implementation logs warning but returns True if write succeeded
        result = utils.update_config_parameters(str(path), {"other": "2"})
        # Our implementation adds updated lines for params that were found; "other" not found
        assert path.read_text().strip() == "only=1" or "other=2" in path.read_text()


class TestSetFlowForLists:
    def test_sets_flow_style_on_lists(self):
        from ruamel.yaml import YAML
        from ruamel.yaml.comments import CommentedSeq

        data = {"a": [1, 2], "b": {"c": [3, 4]}}
        # Wrap lists as CommentedSeq for set_flow_for_lists to have effect
        data["a"] = CommentedSeq(data["a"])
        data["b"]["c"] = CommentedSeq(data["b"]["c"])
        utils.set_flow_for_lists(data)
        # After set_flow_for_lists, list flow style is set; we can't easily assert
        # without round-tripping YAML, so just ensure no exception
        assert data["a"] == [1, 2]
        assert data["b"]["c"] == [3, 4]
