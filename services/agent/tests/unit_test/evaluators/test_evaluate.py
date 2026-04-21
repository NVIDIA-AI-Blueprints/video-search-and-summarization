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
"""Unit tests for report_evaluator/evaluate module."""


# Since evaluate module has complex dependencies, we test what we can import
class TestEvaluateModuleImports:
    """Test that evaluate module can be imported."""

    def test_module_import(self):
        # Test that the module can be imported without errors
        from vss_agents.evaluators.report_evaluator import evaluate

        assert evaluate is not None


class TestEvaluationHelpers:
    """Test helper functionality from evaluate module."""

    def test_evaluation_metrics_exist(self):
        """Test that evaluation metrics are defined."""
        from vss_agents.evaluators.report_evaluator.field_evaluators.base import METRIC_REGISTRY
        from vss_agents.evaluators.report_evaluator.field_evaluators.base import register_metric

        assert callable(register_metric)
        assert isinstance(METRIC_REGISTRY, dict)
