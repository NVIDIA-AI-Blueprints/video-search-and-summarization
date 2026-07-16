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
Integration tests for _execute_json_update using write_json_preserving.

Tests verify that _execute_json_update preserves the original file's
indentation style and trailing-newline convention after the t02 change.
"""

import json
import pytest
from unittest.mock import patch

from profile_configurator.profile_config_manager import ProfileConfigManager


def make_manager() -> ProfileConfigManager:
    """
    Create a ProfileConfigManager instance with __init__ bypassed.
    Sets up only the attributes required by _execute_json_update.
    """
    with patch.object(ProfileConfigManager, '__init__', return_value=None):
        mgr = ProfileConfigManager.__new__(ProfileConfigManager)

    mgr.env_vars = {}
    mgr._typed_env_vars = {}
    mgr.profile_configs = {}
    mgr.hardware_profile = 'default'
    mgr.deployment_profile = 'default'
    mgr.deployment_modes_enabled = False
    mgr.config = {}
    return mgr


# ---------------------------------------------------------------------------
# Acceptance criterion 1: 4-space indented JSON preserved
# ---------------------------------------------------------------------------
def test_4space_indent_preserved(tmp_path):
    """After update, file must still use 4-space indentation."""
    target = tmp_path / "config.json"
    original_raw = '{\n    "key": "old",\n    "other": "value"\n}\n'
    target.write_text(original_raw, encoding='utf-8')

    mgr = make_manager()
    operation = {'target_file': str(target), 'updates': {'key': 'new'}, 'backup': False}
    result = mgr._execute_json_update(operation)

    assert result is True
    output = target.read_text(encoding='utf-8')
    parsed = json.loads(output)
    assert parsed['key'] == 'new'
    assert parsed['other'] == 'value'
    assert output.splitlines()[1].startswith('    '), f"Expected 4-space indent, got: {output.splitlines()[1]!r}"


# ---------------------------------------------------------------------------
# Acceptance criterion 2: Compact JSON preserved
# ---------------------------------------------------------------------------
def test_compact_json_preserved(tmp_path):
    """After update, compact JSON must stay compact (no newlines)."""
    target = tmp_path / "config.json"
    original_raw = '{"key":"old","other":"value"}'
    target.write_text(original_raw, encoding='utf-8')

    mgr = make_manager()
    operation = {'target_file': str(target), 'updates': {'key': 'new'}, 'backup': False}
    result = mgr._execute_json_update(operation)

    assert result is True
    output = target.read_text(encoding='utf-8')
    assert json.loads(output)['key'] == 'new'
    assert '\n' not in output, f"Expected compact JSON (no newlines), got: {output!r}"


# ---------------------------------------------------------------------------
# Acceptance criterion 3: Trailing newline preserved (present)
# ---------------------------------------------------------------------------
def test_trailing_newline_preserved_when_present(tmp_path):
    """File ending in \\n must still end in \\n after update."""
    target = tmp_path / "config.json"
    target.write_text('{\n    "x": 1\n}\n', encoding='utf-8')

    mgr = make_manager()
    result = mgr._execute_json_update({'target_file': str(target), 'updates': {'x': 2}, 'backup': False})

    assert result is True
    assert target.read_text(encoding='utf-8').endswith('\n')


# ---------------------------------------------------------------------------
# Acceptance criterion 4: No trailing newline preserved (absent)
# ---------------------------------------------------------------------------
def test_no_trailing_newline_preserved_when_absent(tmp_path):
    """File NOT ending in \\n must still NOT end in \\n after update."""
    target = tmp_path / "config.json"
    target.write_text('{\n    "x": 1\n}', encoding='utf-8')

    mgr = make_manager()
    result = mgr._execute_json_update({'target_file': str(target), 'updates': {'x': 2}, 'backup': False})

    assert result is True
    assert not target.read_text(encoding='utf-8').endswith('\n')


# ---------------------------------------------------------------------------
# Acceptance criterion 5: Dot-notation key updates nested dict correctly
# ---------------------------------------------------------------------------
def test_dot_notation_updates_nested_dict(tmp_path):
    """key 'a.b' updates nested dict; sibling keys of 'a' and 'b' unchanged."""
    target = tmp_path / "config.json"
    data = {"a": {"b": "old", "c": "sibling_b"}, "d": "sibling_a"}
    target.write_text(json.dumps(data, indent=4) + '\n', encoding='utf-8')

    mgr = make_manager()
    result = mgr._execute_json_update({'target_file': str(target), 'updates': {'a.b': 'new'}, 'backup': False})

    assert result is True
    parsed = json.loads(target.read_text(encoding='utf-8'))
    assert parsed['a']['b'] == 'new'
    assert parsed['a']['c'] == 'sibling_b'
    assert parsed['d'] == 'sibling_a'


# ---------------------------------------------------------------------------
# Acceptance criterion 6a: Backup is created when backup=True (default)
# ---------------------------------------------------------------------------
def test_backup_created_when_backup_true(tmp_path):
    """When backup=True (default), a backup file should be created."""
    target = tmp_path / "config.json"
    target.write_text('{\n    "x": 1\n}\n', encoding='utf-8')

    mgr = make_manager()
    result = mgr._execute_json_update({'target_file': str(target), 'updates': {'x': 2}, 'backup': True})

    assert result is True
    assert len(list(tmp_path.glob("config.backup_*.json"))) >= 1


# ---------------------------------------------------------------------------
# Acceptance criterion 6b: No backup when backup=False
# ---------------------------------------------------------------------------
def test_no_backup_when_backup_false(tmp_path):
    """When backup=False, no backup file should be created."""
    target = tmp_path / "config.json"
    target.write_text('{\n    "x": 1\n}\n', encoding='utf-8')

    mgr = make_manager()
    result = mgr._execute_json_update({'target_file': str(target), 'updates': {'x': 2}, 'backup': False})

    assert result is True
    assert len(list(tmp_path.glob("config.backup_*.json"))) == 0


# ---------------------------------------------------------------------------
# Acceptance criterion 7: Missing file returns False
# ---------------------------------------------------------------------------
def test_missing_file_returns_false(tmp_path):
    """When target_file does not exist, method returns False."""
    mgr = make_manager()
    result = mgr._execute_json_update(
        {'target_file': str(tmp_path / "nonexistent.json"), 'updates': {'x': 2}, 'backup': False}
    )
    assert result is False


def test_array_kv_updates_sets_app_entries_by_name(tmp_path, monkeypatch):
    """array_kv_updates matches app[] items by name and sets value (e.g. sourceType/sinkType)."""
    target = tmp_path / "vss-behavior-analytics-config.json"
    data = {
        "app": [
            {"name": "coordinateSystem", "value": "euclidean"},
            {"name": "sourceType", "value": "kafka"},
            {"name": "sinkType", "value": "kafka"},
        ],
    }
    target.write_text(json.dumps(data, indent=4) + "\n", encoding="utf-8")

    mgr = make_manager()
    monkeypatch.setitem(mgr.env_vars, "spatial_analytics_source_sink_type", "redisStream")

    result = mgr._execute_json_update(
        {
            "target_file": str(target),
            "updates": {},
            "backup": False,
            "array_kv_updates": [
                {
                    "list_key": "app",
                    "match_field": "name",
                    "updates": [
                        {
                            "match": "sourceType",
                            "set_key": "value",
                            "set_value": "${spatial_analytics_source_sink_type}",
                        },
                        {
                            "match": "sinkType",
                            "set_key": "value",
                            "set_value": "${spatial_analytics_source_sink_type}",
                        },
                    ],
                }
            ],
        }
    )

    assert result is True
    parsed = json.loads(target.read_text(encoding="utf-8"))
    assert parsed["app"][1]["value"] == "redisStream"
    assert parsed["app"][2]["value"] == "redisStream"
    assert parsed["app"][0]["value"] == "euclidean"


def test_json_update_typed_none_for_kafka_brokers(tmp_path):
    """Typed ${var} can set JSON null (e.g. vss-video-analytics-api kafka.brokers when STREAM_TYPE=redis)."""
    target = tmp_path / "vss-video-analytics-api-config.json"
    target.write_text(
        json.dumps(
            {"kafka": {"brokers": ["localhost:9092"]}},
            indent=4,
        )
        + "\n",
        encoding="utf-8",
    )

    mgr = make_manager()
    mgr._typed_env_vars["vss_video_analytics_kafka_brokers"] = None

    result = mgr._execute_json_update(
        {
            "target_file": str(target),
            "updates": {"kafka.brokers": "${vss_video_analytics_kafka_brokers}"},
            "backup": False,
        }
    )

    assert result is True
    assert json.loads(target.read_text(encoding="utf-8"))["kafka"]["brokers"] is None


def test_json_update_typed_list_for_kafka_brokers(tmp_path):
    """Typed ${var} can set kafka.brokers to a list."""
    target = tmp_path / "vss-video-analytics-api-config.json"
    target.write_text(
        json.dumps({"kafka": {"brokers": None}}, indent=4) + "\n",
        encoding="utf-8",
    )

    mgr = make_manager()
    mgr._typed_env_vars["vss_video_analytics_kafka_brokers"] = ["localhost:9092"]

    result = mgr._execute_json_update(
        {
            "target_file": str(target),
            "updates": {"kafka.brokers": "${vss_video_analytics_kafka_brokers}"},
            "backup": False,
        }
    )

    assert result is True
    assert json.loads(target.read_text(encoding="utf-8"))["kafka"]["brokers"] == [
        "localhost:9092"
    ]
