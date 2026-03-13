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
"""Tests for vss_agents/evaluators/report_evaluator/eval_config_models.py."""

from pydantic import ValidationError
import pytest
from vss_agents.evaluators.report_evaluator.eval_config_models import EvalMetricsConfig
from vss_agents.evaluators.report_evaluator.eval_config_models import FieldConfig


class TestFieldConfig:
    """Tests for FieldConfig model."""

    def test_create_field_config_defaults(self):
        """Test creating FieldConfig with defaults."""
        config = FieldConfig()
        assert config.method is None
        assert config.fields is None
        assert config.allow_dynamic_field_discovery is False

    def test_create_field_config_with_method(self):
        """Test creating FieldConfig with method."""
        config = FieldConfig(method="f1")
        assert config.method == "f1"

    def test_field_config_with_nested_fields(self):
        """Test FieldConfig with nested fields."""
        config = FieldConfig(
            method="average",
            fields={
                "field1": FieldConfig(method="exact_match"),
                "field2": FieldConfig(method="f1"),
            },
        )
        assert len(config.fields) == 2
        assert config.fields["field1"].method == "exact_match"

    def test_field_config_dynamic_discovery(self):
        """Test FieldConfig with dynamic field discovery."""
        config = FieldConfig(
            method="average",
            allow_dynamic_field_discovery=True,
        )
        assert config.allow_dynamic_field_discovery is True

    def test_field_config_method_collection(self):
        """Test that methods are collected in _methods."""
        config = FieldConfig(method="f1")
        assert "f1" in config._methods

    def test_field_config_nested_method_collection(self):
        """Test that nested methods are collected."""
        config = FieldConfig(
            method="average",
            fields={
                "a": FieldConfig(method="exact_match"),
                "b": FieldConfig(method="regex"),
            },
        )
        # Parent should collect child methods
        assert "exact_match" in config._methods or "regex" in config._methods

    def test_field_config_average_without_fields_error(self):
        """Test that average method without fields raises error."""
        with pytest.raises(ValueError, match="average"):
            FieldConfig(method="average", allow_dynamic_field_discovery=False)

    def test_field_config_empty_fields_error(self):
        """Test that explicitly empty fields raises error."""
        with pytest.raises(ValueError):
            FieldConfig(method="exact_match", fields={})

    def test_field_config_forbid_extra(self):
        """Test that extra fields are forbidden."""
        with pytest.raises(ValidationError):
            FieldConfig(method="f1", unknown_field="value")

    def test_default_method_is_llm_judge(self):
        """Test that llm_judge is added as default when no method specified."""
        config = FieldConfig()
        assert config.method is None
        assert "llm_judge" in config._methods

    def test_methods_collected_from_nested_structure(self):
        """Test that methods are correctly collected from nested structure and are deduplicated."""
        config = FieldConfig(
            method="average",
            fields={
                "field1": FieldConfig(method="exact_match"),
                "field2": FieldConfig(
                    method="average",
                    fields={
                        "nested1": FieldConfig(method="f1"),
                        "nested2": FieldConfig(method="llm_judge"),
                        "nested3": FieldConfig(method="exact_match"),
                    },
                ),
                "field3": FieldConfig(method="exact_match"),
            },
        )
        assert config._methods == {"exact_match", "f1", "llm_judge"}

    @pytest.mark.parametrize(
        "invalid_config,expected_error",
        [
            ({"method": "average"}, "Method 'average' can only be used for sections"),
            ({"method": "average", "fields": {}}, "must contain at least one field"),
            ({"method": "average", "fields": None}, "must contain at least one field"),
            ({"method": "average", "fields": {"field1": {"fields": {}}}}, "must contain at least one field"),
        ],
    )
    def test_validation_errors_parametrized(self, invalid_config, expected_error):
        """Test custom validation logic in FieldConfig with parametrized inputs."""
        with pytest.raises(ValidationError, match=expected_error):
            FieldConfig(**invalid_config)


class TestEvalMetricsConfig:
    """Tests for EvalMetricsConfig model."""

    def test_create_from_dict(self):
        """Test creating EvalMetricsConfig from dict."""
        config_dict = {
            "report": {
                "method": "average",
                "fields": {
                    "summary": {"method": "f1"},
                    "details": {"method": "exact_match"},
                },
            }
        }
        config = EvalMetricsConfig.from_dict(config_dict)
        assert config.root_key == "report"
        assert config.root.method == "average"
        assert len(config.root.fields) == 2

    def test_from_dict_single_root_key(self):
        """Test from_dict requires exactly one root key."""
        with pytest.raises(ValueError, match="exactly one root key"):
            EvalMetricsConfig.from_dict({"key1": {}, "key2": {}})

    def test_from_dict_empty_dict(self):
        """Test from_dict with empty dict."""
        with pytest.raises(ValueError, match="exactly one root key"):
            EvalMetricsConfig.from_dict({})

    def test_from_dict_invalid_type(self):
        """Test from_dict with invalid type."""
        with pytest.raises(ValueError, match="must be a dict"):
            EvalMetricsConfig.from_dict("not a dict")

    def test_methods_collected(self):
        """Test that methods are collected in config."""
        config_dict = {
            "root": {
                "method": "average",
                "fields": {
                    "field1": {"method": "f1"},
                    "field2": {"method": "exact_match"},
                },
            }
        }
        config = EvalMetricsConfig.from_dict(config_dict)
        assert len(config.methods) > 0

    def test_config_with_dynamic_discovery(self):
        """Test config with dynamic field discovery."""
        config_dict = {
            "root": {
                "method": "average",
                "allow_dynamic_field_discovery": True,
            }
        }
        config = EvalMetricsConfig.from_dict(config_dict)
        assert config.root.allow_dynamic_field_discovery is True

    def test_deep_nesting(self):
        """Test deeply nested configuration."""
        config_dict = {
            "root": {
                "method": "average",
                "fields": {
                    "level1": {
                        "method": "average",
                        "fields": {
                            "level2": {
                                "method": "f1",
                            }
                        },
                    }
                },
            }
        }
        config = EvalMetricsConfig.from_dict(config_dict)
        assert config.root.fields["level1"].fields["level2"].method == "f1"
