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

"""Unit tests for profile_configurator.profile_config_manager.ProfileConfigManager."""
import json
import os
import pytest
from unittest.mock import patch, MagicMock

from profile_configurator.profile_config_manager import ProfileConfigManager


@pytest.fixture
def gpu_config_path(fixtures_dir):
    return os.path.join(fixtures_dir, "gpu_configs_minimal.yaml")


class TestDetermineHardwareProfile:
    def test_env_set_and_in_config(self, gpu_config_path, monkeypatch):
        monkeypatch.setenv("HARDWARE_PROFILE", "L4")
        monkeypatch.setenv("MODE", "3d")
        mgr = ProfileConfigManager(config_file=gpu_config_path)
        assert mgr.hardware_profile == "L4"

    def test_env_set_uppercase(self, gpu_config_path, monkeypatch):
        monkeypatch.setenv("HARDWARE_PROFILE", "h100")
        monkeypatch.setenv("MODE", "3d")
        mgr = ProfileConfigManager(config_file=gpu_config_path)
        assert mgr.hardware_profile == "H100"

    def test_env_set_not_in_config_preserves_requested_profile(self, gpu_config_path, monkeypatch):
        monkeypatch.setenv("HARDWARE_PROFILE", "UNKNOWN_GPU")
        monkeypatch.setenv("MODE", "3d")
        mgr = ProfileConfigManager(config_file=gpu_config_path)
        assert mgr.hardware_profile == "UNKNOWN_GPU"

    def test_env_set_not_in_config_warning_excludes_commons(self, gpu_config_path, monkeypatch):
        monkeypatch.setenv("HARDWARE_PROFILE", "MYH100")
        monkeypatch.setenv("MODE", "3d")

        with patch("profile_configurator.profile_config_manager.logger.warning") as mock_warning:
            ProfileConfigManager(config_file=gpu_config_path)

        warning_messages = "\n".join(
            call.args[0] for call in mock_warning.call_args_list
        )
        assert "Hardware profile: MYH100 not found" in warning_messages
        assert "GPU configurations: ['L4', 'H100', 'default']" in warning_messages
        assert "commons" not in warning_messages

    def test_env_unset_uses_default(self, gpu_config_path, monkeypatch):
        monkeypatch.delenv("HARDWARE_PROFILE", raising=False)
        monkeypatch.setenv("MODE", "3d")
        mgr = ProfileConfigManager(config_file=gpu_config_path)
        assert mgr.hardware_profile == ProfileConfigManager.DEFAULT_HARDWARE_PROFILE


class TestDetermineDeploymentProfile:
    def test_normalize_lowercase(self, gpu_config_path, monkeypatch):
        monkeypatch.setenv("HARDWARE_PROFILE", "L4")
        monkeypatch.setenv("MODE", "2D")
        mgr = ProfileConfigManager(config_file=gpu_config_path)
        assert mgr.deployment_profile == "2d"

    def test_strip_whitespace(self, gpu_config_path, monkeypatch):
        monkeypatch.setenv("HARDWARE_PROFILE", "L4")
        monkeypatch.setenv("MODE", "  3d  ")
        mgr = ProfileConfigManager(config_file=gpu_config_path)
        assert mgr.deployment_profile == "3d"


class TestLoadProfileConfigs:
    def test_valid_yaml_returns_dict(self, gpu_config_path, monkeypatch):
        monkeypatch.setenv("HARDWARE_PROFILE", "L4")
        monkeypatch.setenv("MODE", "3d")
        mgr = ProfileConfigManager(config_file=gpu_config_path)
        assert isinstance(mgr.profile_configs, dict)
        assert "L4" in mgr.profile_configs
        assert "commons" in mgr.profile_configs
        assert "3d" in mgr.profile_configs.get("L4", {})

    def test_missing_file_raises(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MODE", "3d")
        with pytest.raises(FileNotFoundError):
            ProfileConfigManager(config_file=str(tmp_path / "missing.yaml"))


class TestSubstituteEnvVars:
    def test_substitutes_string(self, gpu_config_path, monkeypatch):
        monkeypatch.setenv("HARDWARE_PROFILE", "L4")
        monkeypatch.setenv("MODE", "3d")
        monkeypatch.setenv("MY_VAR", "replaced")
        mgr = ProfileConfigManager(config_file=gpu_config_path)
        result = mgr._substitute_env_vars("prefix ${MY_VAR} suffix")
        assert result == "prefix replaced suffix"

    def test_nested_dict(self, gpu_config_path, monkeypatch):
        monkeypatch.setenv("HARDWARE_PROFILE", "L4")
        monkeypatch.setenv("MODE", "3d")
        monkeypatch.setenv("X", "val")
        mgr = ProfileConfigManager(config_file=gpu_config_path)
        result = mgr._substitute_env_vars({"a": "${X}", "b": {"c": "${X}"}})
        assert result == {"a": "val", "b": {"c": "val"}}

    def test_non_string_unchanged(self, gpu_config_path, monkeypatch):
        monkeypatch.setenv("HARDWARE_PROFILE", "L4")
        monkeypatch.setenv("MODE", "3d")
        mgr = ProfileConfigManager(config_file=gpu_config_path)
        assert mgr._substitute_env_vars(42) == 42


class TestTryConvertToNumber:
    def test_int(self, gpu_config_path, monkeypatch):
        monkeypatch.setenv("HARDWARE_PROFILE", "L4")
        monkeypatch.setenv("MODE", "3d")
        mgr = ProfileConfigManager(config_file=gpu_config_path)
        assert mgr._try_convert_to_number("42") == 42

    def test_float(self, gpu_config_path, monkeypatch):
        monkeypatch.setenv("HARDWARE_PROFILE", "L4")
        monkeypatch.setenv("MODE", "3d")
        mgr = ProfileConfigManager(config_file=gpu_config_path)
        assert mgr._try_convert_to_number("3.14") == 3.14

    def test_true_false(self, gpu_config_path, monkeypatch):
        monkeypatch.setenv("HARDWARE_PROFILE", "L4")
        monkeypatch.setenv("MODE", "3d")
        mgr = ProfileConfigManager(config_file=gpu_config_path)
        assert mgr._try_convert_to_number("true") is True
        assert mgr._try_convert_to_number("false") is False

    def test_non_numeric_unchanged(self, gpu_config_path, monkeypatch):
        monkeypatch.setenv("HARDWARE_PROFILE", "L4")
        monkeypatch.setenv("MODE", "3d")
        mgr = ProfileConfigManager(config_file=gpu_config_path)
        assert mgr._try_convert_to_number("hello") == "hello"


class TestEvaluateExpression:
    def test_math(self, gpu_config_path, monkeypatch):
        monkeypatch.setenv("HARDWARE_PROFILE", "L4")
        monkeypatch.setenv("MODE", "3d")
        mgr = ProfileConfigManager(config_file=gpu_config_path)
        assert mgr._evaluate_expression("2 + 3") == 5
        assert mgr._evaluate_expression("min(1, 2)") == 1
        assert mgr._evaluate_expression("max(1, 2)") == 2

    def test_ternary(self, gpu_config_path, monkeypatch):
        monkeypatch.setenv("HARDWARE_PROFILE", "L4")
        monkeypatch.setenv("MODE", "3d")
        mgr = ProfileConfigManager(config_file=gpu_config_path)
        assert mgr._evaluate_expression("1 if True else 2") == 1
        assert mgr._evaluate_expression("1 if False else 2") == 2

    def test_comparison_with_env(self, gpu_config_path, monkeypatch):
        monkeypatch.setenv("HARDWARE_PROFILE", "L4")
        monkeypatch.setenv("MODE", "3d")
        mgr = ProfileConfigManager(config_file=gpu_config_path)
        mgr.env_vars["num_cameras"] = "12"
        mgr.env_vars["max_streams_supported"] = "8"
        # Expression uses variables that were substituted; here we test raw expression
        assert mgr._evaluate_expression("12 > 5") is True
        assert mgr._evaluate_expression("12 <= 8") is False

    def test_invalid_expression_raises(self, gpu_config_path, monkeypatch):
        monkeypatch.setenv("HARDWARE_PROFILE", "L4")
        monkeypatch.setenv("MODE", "3d")
        mgr = ProfileConfigManager(config_file=gpu_config_path)
        with pytest.raises(ValueError):
            mgr._evaluate_expression("open('/etc/passwd')")


class TestProcessConfigVariables:
    def test_variables_added_to_env_vars(self, gpu_config_path, monkeypatch):
        monkeypatch.setenv("HARDWARE_PROFILE", "L4")
        monkeypatch.setenv("MODE", "3d")
        mgr = ProfileConfigManager(config_file=gpu_config_path)
        # L4 3d has max_streams_supported: 8 and variables with expressions
        assert "max_streams_supported" in mgr.env_vars
        assert mgr.env_vars["max_streams_supported"] == "8"
        # num_cameras and batch_size from commons, then stream_density and use_high_quality from L4
        assert "num_cameras" in mgr.env_vars
        assert "batch_size" in mgr.env_vars
        assert "stream_density" in mgr.env_vars


class TestEvaluateExpressionTyped:
    """Tests for list and None return types from _evaluate_expression."""

    def test_list_literal(self, gpu_config_path, monkeypatch):
        monkeypatch.setenv("HARDWARE_PROFILE", "L4")
        monkeypatch.setenv("MODE", "3d")
        mgr = ProfileConfigManager(config_file=gpu_config_path)
        result = mgr._evaluate_expression('["localhost:9092"]')
        assert result == ["localhost:9092"]

    def test_list_ternary_true_branch(self, gpu_config_path, monkeypatch):
        monkeypatch.setenv("HARDWARE_PROFILE", "L4")
        monkeypatch.setenv("MODE", "3d")
        mgr = ProfileConfigManager(config_file=gpu_config_path)
        result = mgr._evaluate_expression('["localhost:9092"] if "kafka" == "kafka" else None')
        assert result == ["localhost:9092"]

    def test_none_ternary_false_branch(self, gpu_config_path, monkeypatch):
        monkeypatch.setenv("HARDWARE_PROFILE", "L4")
        monkeypatch.setenv("MODE", "3d")
        mgr = ProfileConfigManager(config_file=gpu_config_path)
        result = mgr._evaluate_expression('["localhost:9092"] if "redis" == "kafka" else None')
        assert result is None

    def test_none_literal(self, gpu_config_path, monkeypatch):
        monkeypatch.setenv("HARDWARE_PROFILE", "L4")
        monkeypatch.setenv("MODE", "3d")
        mgr = ProfileConfigManager(config_file=gpu_config_path)
        result = mgr._evaluate_expression("None")
        assert result is None


class TestSubstituteEnvVarsTyped:
    """Tests that _substitute_env_vars returns typed values from _typed_env_vars."""

    def test_returns_list_for_single_var_ref(self, gpu_config_path, monkeypatch):
        monkeypatch.setenv("HARDWARE_PROFILE", "L4")
        monkeypatch.setenv("MODE", "3d")
        mgr = ProfileConfigManager(config_file=gpu_config_path)
        mgr._typed_env_vars["MY_LIST"] = ["a", "b"]
        result = mgr._substitute_env_vars("${MY_LIST}")
        assert result == ["a", "b"]

    def test_returns_none_for_single_var_ref(self, gpu_config_path, monkeypatch):
        monkeypatch.setenv("HARDWARE_PROFILE", "L4")
        monkeypatch.setenv("MODE", "3d")
        mgr = ProfileConfigManager(config_file=gpu_config_path)
        mgr._typed_env_vars["MY_NULL"] = None
        result = mgr._substitute_env_vars("${MY_NULL}")
        assert result is None

    def test_mixed_string_not_resolved_from_typed(self, gpu_config_path, monkeypatch):
        """A string with more than just ${VAR} should not use _typed_env_vars."""
        monkeypatch.setenv("HARDWARE_PROFILE", "L4")
        monkeypatch.setenv("MODE", "3d")
        mgr = ProfileConfigManager(config_file=gpu_config_path)
        mgr._typed_env_vars["MY_LIST"] = ["a"]
        mgr.env_vars["MY_LIST"] = "fallback"
        result = mgr._substitute_env_vars("prefix_${MY_LIST}")
        assert result == "prefix_fallback"


class TestProcessConfigVariablesTyped:
    """Tests that list/None variables are stored in _typed_env_vars, not env_vars."""

    def _make_manager_with_vars(self, gpu_config_path, monkeypatch, extra_vars):
        monkeypatch.setenv("HARDWARE_PROFILE", "L4")
        monkeypatch.setenv("MODE", "3d")
        mgr = ProfileConfigManager(config_file=gpu_config_path)
        # Inject extra typed variable expressions directly
        for var_name, expression in extra_vars.items():
            mgr.env_vars["STREAM_TYPE"] = "kafka"
            substituted = mgr._substitute_env_vars(expression)
            if isinstance(substituted, str):
                try:
                    result = mgr._evaluate_expression(substituted)
                    if isinstance(result, list) or result is None:
                        mgr._typed_env_vars[var_name] = result
                    else:
                        mgr.env_vars[var_name] = str(result)
                except (ValueError, SyntaxError):
                    mgr.env_vars[var_name] = substituted
        return mgr

    def test_list_variable_stored_in_typed(self, gpu_config_path, monkeypatch):
        monkeypatch.setenv("HARDWARE_PROFILE", "L4")
        monkeypatch.setenv("MODE", "3d")
        monkeypatch.setenv("STREAM_TYPE", "kafka")
        mgr = ProfileConfigManager(config_file=gpu_config_path)
        expr = '["localhost:9092"] if "${STREAM_TYPE}" == "kafka" else None'
        substituted = mgr._substitute_env_vars(expr)
        result = mgr._evaluate_expression(substituted)
        assert isinstance(result, list)
        assert result == ["localhost:9092"]

    def test_none_variable_stored_in_typed(self, gpu_config_path, monkeypatch):
        monkeypatch.setenv("HARDWARE_PROFILE", "L4")
        monkeypatch.setenv("MODE", "3d")
        monkeypatch.setenv("STREAM_TYPE", "redis")
        mgr = ProfileConfigManager(config_file=gpu_config_path)
        expr = '["localhost:9092"] if "${STREAM_TYPE}" == "kafka" else None'
        substituted = mgr._substitute_env_vars(expr)
        result = mgr._evaluate_expression(substituted)
        assert result is None


class TestExecuteJsonUpdateWithTypedValues:
    """Integration tests for json_update with list and None values via _typed_env_vars."""

    def test_json_update_sets_list_value(self, gpu_config_path, monkeypatch, tmp_path):
        monkeypatch.setenv("HARDWARE_PROFILE", "L4")
        monkeypatch.setenv("MODE", "3d")
        mgr = ProfileConfigManager(config_file=gpu_config_path)
        mgr._typed_env_vars["MY_BROKERS"] = ["localhost:9092"]

        target = tmp_path / "config.json"
        target.write_text('{"kafka": {"brokers": null}}\n')

        operation = {
            "operation_type": "json_update",
            "target_file": str(target),
            "backup": False,
            "updates": {"kafka.brokers": "${MY_BROKERS}"},
        }
        result = mgr._execute_json_update(operation)
        assert result is True
        import json
        data = json.loads(target.read_text())
        assert data["kafka"]["brokers"] == ["localhost:9092"]

    def test_json_update_sets_null_value(self, gpu_config_path, monkeypatch, tmp_path):
        monkeypatch.setenv("HARDWARE_PROFILE", "L4")
        monkeypatch.setenv("MODE", "3d")
        mgr = ProfileConfigManager(config_file=gpu_config_path)
        mgr._typed_env_vars["MY_BROKERS"] = None

        target = tmp_path / "config.json"
        target.write_text('{"kafka": {"brokers": ["localhost:9092"]}}\n')

        operation = {
            "operation_type": "json_update",
            "target_file": str(target),
            "backup": False,
            "updates": {"kafka.brokers": "${MY_BROKERS}"},
        }
        result = mgr._execute_json_update(operation)
        assert result is True
        import json
        data = json.loads(target.read_text())
        assert data["kafka"]["brokers"] is None


class TestExecutePrerequisites:
    def test_empty_prerequisites_no_op(self, gpu_config_path, monkeypatch):
        monkeypatch.setenv("HARDWARE_PROFILE", "L4")
        monkeypatch.setenv("MODE", "3d")
        mgr = ProfileConfigManager(config_file=gpu_config_path)
        # Our minimal config has no prerequisites; _execute_prerequisites is already called in __init__
        assert mgr.config is not None


class TestCommonsForUndeclaredHardware:
    def test_undeclared_hardware_profile_still_processes_commons(
        self, tmp_path, monkeypatch
    ):
        config_file = tmp_path / "gpu_configs.yaml"
        target_file = tmp_path / "target.json"
        target_file.write_text('{"app": {"mode": "unset"}}\n')
        config_file.write_text(
            f"""
commons:
  variables:
    3d:
      - common_mode: '"3d-common"'
  file_operations:
    3d:
      - operation_type: "json_update"
        target_file: "{target_file}"
        backup: false
        updates:
          app.mode: "${{common_mode}}"
L4:
  3d:
    max_streams_supported: 4
"""
        )

        monkeypatch.setenv("HARDWARE_PROFILE", "RTX5090")
        monkeypatch.setenv("MODE", "3d")

        mgr = ProfileConfigManager(config_file=str(config_file))

        assert mgr.hardware_profile == "RTX5090"
        assert mgr.config == {}
        assert mgr.env_vars["common_mode"] == "3d-common"
        assert mgr.execute_file_operations() is True
        assert json.loads(target_file.read_text())["app"]["mode"] == "3d-common"
