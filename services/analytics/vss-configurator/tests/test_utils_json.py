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

"""
Unit tests for detect_json_indent and write_json_preserving in utils.py.
"""
import json
import pytest

from profile_configurator_utils.utils import detect_json_indent, write_json_preserving


# ---------------------------------------------------------------------------
# detect_json_indent tests
# ---------------------------------------------------------------------------

def test_detect_indent_2_space():
    raw = '{\n  "a": 1\n}'
    assert detect_json_indent(raw) == 2


def test_detect_indent_4_space():
    raw = '{\n    "a": 1\n}'
    assert detect_json_indent(raw) == 4


def test_detect_indent_tab():
    raw = '{\n\t"a": 1\n}'
    assert detect_json_indent(raw) == '\t'


def test_detect_indent_compact():
    raw = '{"a":1}'
    assert detect_json_indent(raw) is None


# ---------------------------------------------------------------------------
# write_json_preserving tests
# ---------------------------------------------------------------------------

def test_write_preserving_4space_trailing_newline(tmp_path):
    out = tmp_path / "test.json"
    original_raw = '{\n    "a": 1,\n    "b": 2\n}\n'
    data = {"a": 99, "b": 2}
    result = write_json_preserving(str(out), data, original_raw)
    assert result is True
    content = out.read_text()
    parsed = json.loads(content)
    assert parsed["a"] == 99
    assert parsed["b"] == 2
    assert content.endswith('\n')
    lines = content.splitlines()
    assert lines[1].startswith('    ')   # 4-space indent


def test_write_preserving_compact_no_trailing_newline(tmp_path):
    out = tmp_path / "compact.json"
    original_raw = '{"a":1,"b":2}'
    data = {"a": 99, "b": 2}
    result = write_json_preserving(str(out), data, original_raw)
    assert result is True
    content = out.read_text()
    parsed = json.loads(content)
    assert parsed["a"] == 99
    assert '\n' not in content
    assert ' ' not in content
    assert not content.endswith('\n')


def test_write_preserving_trailing_newline_preserved(tmp_path):
    out = tmp_path / "newline.json"
    original_raw = '{\n  "x": 1\n}\n'
    data = {"x": 1}
    result = write_json_preserving(str(out), data, original_raw)
    assert result is True
    content = out.read_text()
    assert content.endswith('\n')


def test_write_preserving_no_trailing_newline_preserved(tmp_path):
    out = tmp_path / "no_newline.json"
    original_raw = '{\n  "x": 1\n}'
    data = {"x": 1}
    result = write_json_preserving(str(out), data, original_raw)
    assert result is True
    content = out.read_text()
    assert not content.endswith('\n')


def test_write_preserving_tab_indent(tmp_path):
    out = tmp_path / "tab.json"
    original_raw = '{\n\t"a": 1\n}'
    data = {"a": 42}
    result = write_json_preserving(str(out), data, original_raw)
    assert result is True
    content = out.read_text()
    parsed = json.loads(content)
    assert parsed["a"] == 42
    lines = content.splitlines()
    assert lines[1].startswith('\t'), f"Expected tab indent, got: {repr(lines[1])}"
    assert not lines[1].startswith('    '), "Should not use space indent"
