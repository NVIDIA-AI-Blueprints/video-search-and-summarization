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
Tests for the `enabled` parameter on file operations and prerequisites.

An operation with `enabled: false` must be skipped entirely.
An operation with `enabled: true` or without the key must execute normally.
"""

import pytest
from unittest.mock import patch

from profile_configurator.profile_config_manager import ProfileConfigManager


def make_manager(profile_configs, deployment_profile='2d') -> ProfileConfigManager:
    with patch.object(ProfileConfigManager, '__init__', return_value=None):
        mgr = ProfileConfigManager.__new__(ProfileConfigManager)
    mgr.env_vars = {}
    mgr._typed_env_vars = {}
    mgr.profile_configs = profile_configs
    mgr.hardware_profile = 'TEST'
    mgr.deployment_profile = deployment_profile
    mgr.deployment_modes_enabled = True
    mgr.config = profile_configs.get('TEST', {}).get(deployment_profile, {})
    return mgr


def _configs_with_ops(ops):
    """Minimal profile_configs with the given list as commons file_operations for 2d."""
    return {
        'commons': {'file_operations': {'2d': ops}},
        'TEST': {'2d': {'max_streams_supported': 4}},
    }


def _configs_with_prereqs(prereqs):
    """Minimal profile_configs with the given list as commons prerequisites for 2d."""
    return {
        'commons': {'prerequisites': {'2d': prereqs}},
        'TEST': {'2d': {'max_streams_supported': 4}},
    }


# ---------------------------------------------------------------------------
# execute_file_operations
# ---------------------------------------------------------------------------

def test_file_operation_enabled_false_is_skipped():
    """Operation with enabled: false must not invoke the underlying handler."""
    ops = [
        {'operation_type': 'json_update', 'enabled': False,
         'target_file': '/dummy.json', 'updates': {'x': 1}, 'backup': False},
    ]
    mgr = make_manager(_configs_with_ops(ops))
    with patch.object(mgr, '_execute_json_update', return_value=True) as mock_exec:
        mgr.execute_file_operations()
    mock_exec.assert_not_called()


def test_file_operation_enabled_true_is_executed():
    """Operation with explicit enabled: true must invoke the underlying handler."""
    ops = [
        {'operation_type': 'json_update', 'enabled': True,
         'target_file': '/dummy.json', 'updates': {'x': 1}, 'backup': False},
    ]
    mgr = make_manager(_configs_with_ops(ops))
    with patch.object(mgr, '_execute_json_update', return_value=True) as mock_exec:
        mgr.execute_file_operations()
    mock_exec.assert_called_once()


def test_file_operation_no_enabled_key_is_executed():
    """Operation without an enabled key must execute (absent == enabled by default)."""
    ops = [
        {'operation_type': 'json_update',
         'target_file': '/dummy.json', 'updates': {'x': 1}, 'backup': False},
    ]
    mgr = make_manager(_configs_with_ops(ops))
    with patch.object(mgr, '_execute_json_update', return_value=True) as mock_exec:
        mgr.execute_file_operations()
    mock_exec.assert_called_once()


def test_mixed_operations_only_enabled_ones_execute():
    """Only operations that are not explicitly disabled must execute."""
    ops = [
        {'operation_type': 'yaml_update', 'target_file': '/a.yaml', 'updates': {}},
        {'operation_type': 'json_update', 'enabled': False,
         'target_file': '/b.json', 'updates': {}},
        {'operation_type': 'text_config_update', 'enabled': True,
         'target_file': '/c.txt', 'updates': {}},
    ]
    mgr = make_manager(_configs_with_ops(ops))
    with patch.object(mgr, '_execute_yaml_update', return_value=True) as mock_yaml, \
         patch.object(mgr, '_execute_json_update', return_value=True) as mock_json, \
         patch.object(mgr, '_execute_text_config_update', return_value=True) as mock_text:
        mgr.execute_file_operations()
    mock_yaml.assert_called_once()
    mock_json.assert_not_called()
    mock_text.assert_called_once()


# ---------------------------------------------------------------------------
# _execute_prerequisites
# ---------------------------------------------------------------------------

def test_prerequisite_enabled_false_is_skipped():
    """Prerequisite with enabled: false must not invoke _execute_file_management."""
    prereqs = [
        {'operation_type': 'file_management', 'enabled': False,
         'target_directories': ['/tmp'],
         'file_management': {'action': 'file_count',
                              'parameters': {'pattern': '*.mp4'},
                              'output_variable': 'NUM_STREAMS'}},
    ]
    mgr = make_manager(_configs_with_prereqs(prereqs))
    with patch.object(mgr, '_execute_file_management', return_value=True) as mock_exec:
        mgr._execute_prerequisites()
    mock_exec.assert_not_called()


def test_prerequisite_no_enabled_key_is_executed():
    """Prerequisite without an enabled key must execute (absent == enabled by default)."""
    prereqs = [
        {'operation_type': 'file_management',
         'target_directories': ['/tmp'],
         'file_management': {'action': 'file_count',
                              'parameters': {'pattern': '*.mp4'},
                              'output_variable': 'NUM_STREAMS'}},
    ]
    mgr = make_manager(_configs_with_prereqs(prereqs))
    with patch.object(mgr, '_execute_file_management', return_value=True) as mock_exec:
        mgr._execute_prerequisites()
    mock_exec.assert_called_once()


def test_file_operation_enabled_from_computed_env_var_false():
    """enabled: ${VAR} must skip when env_vars[VAR] resolves to false."""
    ops = [
        {'operation_type': 'json_update', 'enabled': '${trim_sample_videos}',
         'target_file': '/dummy.json', 'updates': {'x': 1}, 'backup': False},
    ]
    mgr = make_manager(_configs_with_ops(ops))
    mgr.env_vars['trim_sample_videos'] = 'False'
    with patch.object(mgr, '_execute_json_update', return_value=True) as mock_exec:
        mgr.execute_file_operations()
    mock_exec.assert_not_called()


def test_file_operation_enabled_from_computed_env_var_true():
    """enabled: ${VAR} must run when env_vars[VAR] resolves to true."""
    ops = [
        {'operation_type': 'json_update', 'enabled': '${trim_sample_videos}',
         'target_file': '/dummy.json', 'updates': {'x': 1}, 'backup': False},
    ]
    mgr = make_manager(_configs_with_ops(ops))
    mgr.env_vars['trim_sample_videos'] = 'True'
    with patch.object(mgr, '_execute_json_update', return_value=True) as mock_exec:
        mgr.execute_file_operations()
    mock_exec.assert_called_once()


def test_prerequisite_enabled_from_computed_env_var_false():
    """Prerequisite enabled: ${VAR} must skip when env_vars[VAR] resolves to false."""
    prereqs = [
        {'operation_type': 'file_management', 'enabled': '${trim_sample_videos}',
         'target_directories': ['/tmp'],
         'file_management': {'action': 'file_count',
                              'parameters': {'pattern': '*.mp4'},
                              'output_variable': 'NUM_STREAMS'}},
    ]
    mgr = make_manager(_configs_with_prereqs(prereqs))
    mgr.env_vars['trim_sample_videos'] = 'false'
    with patch.object(mgr, '_execute_file_management', return_value=True) as mock_exec:
        mgr._execute_prerequisites()
    mock_exec.assert_not_called()


def test_trim_sample_videos_expression_quoted_sensor_source():
    """trim_sample_videos: skip keep_count when SENSOR_INFO_SOURCE is file."""
    mgr = make_manager(_configs_with_ops([]))
    expr = 'False if "${SENSOR_INFO_SOURCE}" == "file" else True'
    mgr.env_vars['SENSOR_INFO_SOURCE'] = 'file'
    assert mgr._evaluate_expression(mgr._substitute_env_vars(expr)) is False
    mgr.env_vars['SENSOR_INFO_SOURCE'] = 'nvstreamer'
    assert mgr._evaluate_expression(mgr._substitute_env_vars(expr)) is True
    mgr.env_vars['SENSOR_INFO_SOURCE'] = 'msb'
    assert mgr._evaluate_expression(mgr._substitute_env_vars(expr)) is True
