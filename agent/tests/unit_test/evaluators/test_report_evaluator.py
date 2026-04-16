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

import json
from pathlib import Path
import tempfile
from typing import Any
from unittest.mock import AsyncMock
from unittest.mock import Mock
from unittest.mock import patch

from nat.data_models.evaluator import EvalInputItem
import pytest
from vss_agents.evaluators.report_evaluator.data_models import EvaluationScore
from vss_agents.evaluators.report_evaluator.eval_config_models import EvalMetricsConfig
from vss_agents.evaluators.report_evaluator.eval_config_models import FieldConfig
from vss_agents.evaluators.report_evaluator.evaluate import ReportEvaluator
from vss_agents.evaluators.report_evaluator.evaluate import _fetch_and_parse_report
from vss_agents.evaluators.report_evaluator.evaluate import _load_eval_metrics_yaml
from vss_agents.evaluators.report_evaluator.field_evaluators.base import EvaluationMetric
import yaml

MOCK_METRIC_SCORE = 0.8
MOCK_LLM_JUDGE_SCORE = 0.9


class MockMetric(EvaluationMetric):
    """Mock evaluation metric for testing."""

    def __init__(self, score: float = 1.0):
        self.score = score

    async def evaluate(self, actual: Any, reference: Any, field_name: str = "") -> float | None:
        """Return mock score."""
        return self.score


class MockLLMJudge(EvaluationMetric):
    """Mock LLM judge metric with field discovery capability."""

    def __init__(self, score: float = 1.0):
        self.score = score

    async def evaluate(self, actual: Any, reference: Any, field_name: str = "") -> float | None:
        """Return mock score."""
        return self.score

    async def evaluate_with_field_discovery(
        self,
        reference_section: dict,
        actual_section: dict,
        unspecified_fields: list,
    ) -> dict:
        """Mock field discovery evaluation."""
        results = {}
        for field in unspecified_fields:
            results[field] = {"score": self.score, "reference_field": None}
        return results


class TestLoadEvalMetricsYAML:
    """Test cases for _load_eval_metrics_yaml function."""

    def test_load_eval_metrics_yaml_success(self):
        """Test successful loading of eval metrics YAML."""
        yaml_content = {
            "report": {
                "method": "average",
                "fields": {
                    "summary": {"method": "llm_judge"},
                    "details": {"method": "exact_match"},
                },
            }
        }

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            yaml.dump(yaml_content, f)
            temp_path = f.name

        try:
            config = _load_eval_metrics_yaml(temp_path)
            assert isinstance(config, EvalMetricsConfig)
            assert config.root_key == "report"
        finally:
            Path(temp_path).unlink()

    @pytest.mark.parametrize(
        "yaml_content,expected_error",
        [
            (None, "is empty"),  # Empty file
            (
                {"root1": {"method": "average"}, "root2": {"method": "average"}},
                "Invalid evaluation metrics config",
            ),  # Invalid config
        ],
    )
    def test_load_eval_metrics_yaml_errors(self, yaml_content, expected_error):
        """Test error cases."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            if yaml_content:
                yaml.dump(yaml_content, f)
            temp_path = f.name

        try:
            with pytest.raises(ValueError) as exc_info:
                _load_eval_metrics_yaml(temp_path)
            assert expected_error in str(exc_info.value)
        finally:
            Path(temp_path).unlink()


class TestFetchAndParseReport:
    """Test cases for _fetch_and_parse_report function."""

    @pytest.mark.asyncio
    async def test_fetch_and_parse_report_success(self):
        """Test successful report fetching and parsing."""
        markdown_content = "# Report\n\n## Summary\nTest summary"

        mock_obj = Mock()
        mock_obj.data = markdown_content.encode("utf-8")

        mock_client = AsyncMock()
        mock_client.get_object = AsyncMock(return_value=mock_obj)

        response = "Here is the report: report_123.md"
        url_pattern = r"report_(\w+\.md)"

        parsed, url = await _fetch_and_parse_report(mock_client, response, url_pattern)

        assert url == "report_123.md"
        assert isinstance(parsed, dict)
        mock_client.get_object.assert_called_once_with("123.md")

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "response,url_pattern,mock_return,expected_error",
        [
            ("No report here", r"report_(\w+)\.md", None, "No report URL found"),
            ("report_123.md", r"report_(\w+)\.md", None, "not found in object store"),
        ],
    )
    async def test_fetch_and_parse_report_errors(self, response, url_pattern, mock_return, expected_error):
        """Test error cases."""
        mock_client = AsyncMock()
        mock_client.get_object = AsyncMock(return_value=mock_return)

        with pytest.raises(ValueError) as exc_info:
            await _fetch_and_parse_report(mock_client, response, url_pattern)
        assert expected_error in str(exc_info.value)


class TestReportEvaluator:
    def setup_method(self):
        """Set up test fixtures."""
        self.config = EvalMetricsConfig.from_dict(
            {
                "report": {
                    "method": "average",
                    "fields": {
                        "summary": {"method": "mock_metric"},
                        "details": {"method": "mock_metric"},
                    },
                }
            }
        )
        self.mock_metric = MockMetric(score=MOCK_METRIC_SCORE)
        self.mock_llm_judge = MockLLMJudge(score=MOCK_LLM_JUDGE_SCORE)
        self.metric_instances = {"mock_metric": self.mock_metric, "llm_judge": self.mock_llm_judge}
        self.mock_object_store = AsyncMock()
        self.report_url_pattern = r"report_(\w+\.md)"

        self.evaluator = ReportEvaluator(
            config=self.config,
            metric_instances=self.metric_instances,
            object_store_client=self.mock_object_store,
            report_url_pattern=self.report_url_pattern,
            include_vlm_output=True,
            vlm_related_fields=["vlm_field_1", "vlm_field_2", "vlm_field_3"],
        )

    @pytest.mark.asyncio
    async def test_evaluate_tree_section_with_fields_verifies_averaging(self):
        """Test evaluate_tree correctly averages field scores."""
        mock_metric_field1 = MockMetric(score=0.6)
        mock_metric_field2 = MockMetric(score=1.0)

        evaluator = ReportEvaluator(
            config=self.config,
            metric_instances={
                "metric_field1": mock_metric_field1,
                "metric_field2": mock_metric_field2,
                "llm_judge": self.mock_llm_judge,
            },
            object_store_client=self.mock_object_store,
            report_url_pattern=self.report_url_pattern,
            include_vlm_output=False,
        )

        section_config = FieldConfig(
            method="average",
            fields={
                "field1": FieldConfig(method="metric_field1"),
                "field2": FieldConfig(method="metric_field2"),
            },
        )
        reference = {"field1": "ref1", "field2": "ref2"}
        actual = {"field1": "act1", "field2": "act2"}
        result = await evaluator.evaluate_tree(
            reference=reference, actual=actual, config=section_config, path=["section"]
        )

        assert isinstance(result, EvaluationScore)
        assert result.method == "average"
        assert len(result.field_scores) == 2
        assert result.field_scores["field1"].section_score == 0.6
        assert result.field_scores["field2"].section_score == 1.0
        assert result.section_score == 0.8

    @pytest.mark.asyncio
    async def test_evaluate_tree_default_to_llm_judge_when_method_none(self):
        """Test evaluate_tree defaults to llm_judge when method is None."""
        field_config = FieldConfig()  # method is None
        result = await self.evaluator.evaluate_tree(reference="ref", actual="act", config=field_config, path=["field"])

        assert isinstance(result, EvaluationScore)
        assert result.method == "llm_judge"
        assert result.section_score == MOCK_LLM_JUDGE_SCORE

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "setup_func,expected_error_msg",
        [
            ("non_dict_reference", "Reference at 'section' is str, expected dict for section"),
            ("metric_returns_none", "Evaluation failed: metric returned None"),
            ("metric_raises_exception", "Metric evaluation failed"),
        ],
    )
    async def test_evaluate_tree_error_scenarios(self, setup_func, expected_error_msg):
        """Test evaluate_tree handles various error scenarios."""
        if setup_func == "non_dict_reference":
            section_config = FieldConfig(method="average", fields={"field1": FieldConfig(method="mock_metric")})
            result = await self.evaluator.evaluate_tree(
                reference="not a dict",
                actual={"field1": "act1"},
                config=section_config,
                path=["section"],
            )
        elif setup_func == "metric_returns_none":
            none_metric = MockMetric(score=None)
            self.evaluator.metric_instances = {"none_metric": none_metric}
            field_config = FieldConfig(method="none_metric")
            result = await self.evaluator.evaluate_tree(
                reference="ref", actual="act", config=field_config, path=["field"]
            )
        else:  # metric_raises_exception
            failing_metric = MockMetric(score=MOCK_METRIC_SCORE)

            async def failing_evaluate(actual, reference, field_name):
                raise RuntimeError("Metric evaluation failed")

            failing_metric.evaluate = failing_evaluate
            self.evaluator.metric_instances = {"mock_metric": failing_metric, "llm_judge": self.mock_llm_judge}
            field_config = FieldConfig(method="mock_metric")
            result = await self.evaluator.evaluate_tree(
                reference="ref", actual="act", config=field_config, path=["field"]
            )

        assert isinstance(result, EvaluationScore)
        assert result.section_score is None
        assert result.error is not None
        assert result.error == expected_error_msg

    @pytest.mark.asyncio
    async def test_evaluate_tree_dynamic_discovery_reference_field_scenarios(self):
        """Test evaluate_tree handles all reference_field scenarios in dynamic discovery."""
        mock_llm = MockLLMJudge(score=MOCK_LLM_JUDGE_SCORE)

        # Mock discovery returns different reference_field scenarios
        async def mock_discovery(reference_section, actual_section, unspecified_fields):
            return {
                "field_with_match": {"score": 0.9, "reference_field": "ref_field_exists"},
                "field_with_missing_ref": {"score": 0.7, "reference_field": "ref_field_missing"},
                "field_no_ref": {"score": 0.8, "reference_field": None},
                "field_with_none_result": None,  # LLM failed to score
            }

        mock_llm.evaluate_with_field_discovery = mock_discovery

        evaluator = ReportEvaluator(
            config=self.config,
            metric_instances={"mock_metric": self.mock_metric, "llm_judge": mock_llm},
            object_store_client=self.mock_object_store,
            report_url_pattern=self.report_url_pattern,
            include_vlm_output=False,
        )

        section_config = FieldConfig(method="average", allow_dynamic_field_discovery=True)

        reference = {"ref_field_exists": "ref_value"}
        actual = {
            "field_with_match": "act1",
            "field_with_missing_ref": "act2",
            "field_no_ref": "act3",
            "field_with_none_result": "act4",
        }

        result = await evaluator.evaluate_tree(
            reference=reference, actual=actual, config=section_config, path=["section"]
        )

        assert isinstance(result, EvaluationScore)

        # Scenario 1: Field with matching reference in reference section
        assert result.field_scores["field_with_match"].section_score == 0.9
        assert result.field_scores["field_with_match"].reference_value == "ref_value"

        # Scenario 2: Field with reference_field specified but not found in reference section
        assert result.field_scores["field_with_missing_ref"].section_score == 0.7
        assert (
            result.field_scores["field_with_missing_ref"].reference_value
            == "[no matching reference field: ref_field_missing]"
        )

        # Scenario 3: Field with no reference_field
        assert result.field_scores["field_no_ref"].section_score == 0.8
        assert (
            result.field_scores["field_no_ref"].reference_value == "[no matching reference field found in LLM response]"
        )

        # Scenario 4: LLM failed to score field (returns None)
        assert result.field_scores["field_with_none_result"].section_score is None
        assert result.field_scores["field_with_none_result"].error == "LLM failed to score this field during discovery"

    @pytest.mark.asyncio
    async def test_evaluate_tree_nested_sections(self):
        """Test evaluate_tree with nested sections verifies multi-level averaging."""
        mock_metric_0_4 = MockMetric(score=0.4)
        mock_metric_0_6 = MockMetric(score=0.6)
        mock_metric_1_0 = MockMetric(score=1.0)

        evaluator = ReportEvaluator(
            config=self.config,
            metric_instances={
                "metric_0_4": mock_metric_0_4,
                "metric_0_6": mock_metric_0_6,
                "metric_1_0": mock_metric_1_0,
                "llm_judge": self.mock_llm_judge,
            },
            object_store_client=self.mock_object_store,
            report_url_pattern=self.report_url_pattern,
            include_vlm_output=False,
        )

        nested_config = FieldConfig(
            method="average",
            fields={
                "section1": FieldConfig(
                    method="average",
                    fields={
                        "field1": FieldConfig(method="metric_0_4"),
                        "field2": FieldConfig(method="metric_0_6"),
                    },
                ),
                "section2": FieldConfig(method="metric_1_0"),
            },
        )

        reference = {
            "section1": {"field1": "ref1", "field2": "ref2"},
            "section2": "ref3",
        }
        actual = {
            "section1": {"field1": "act1", "field2": "act2"},
            "section2": "act3",
        }

        result = await evaluator.evaluate_tree(reference=reference, actual=actual, config=nested_config, path=["root"])

        assert isinstance(result, EvaluationScore)
        assert len(result.field_scores) == 2

        # Check section1 nested scores
        assert result.field_scores["section1"].field_scores["field1"].section_score == 0.4
        assert result.field_scores["section1"].field_scores["field2"].section_score == 0.6
        # section1 average
        assert result.field_scores["section1"].section_score == 0.5

        # Check section2 score
        assert result.field_scores["section2"].section_score == 1.0

        # Root level average
        assert result.section_score == 0.75

    @pytest.mark.asyncio
    async def test_evaluate_tree_explicit_plus_dynamic_discovery(self):
        """Test section with both explicit fields and dynamic discovery enabled."""
        mock_llm = MockLLMJudge(score=MOCK_LLM_JUDGE_SCORE)

        # Mock discovery for dynamic field
        async def mock_discovery(reference_section, actual_section, unspecified_fields):
            return {
                "surprise_field": {"score": 0.85, "reference_field": None},
            }

        mock_llm.evaluate_with_field_discovery = mock_discovery

        evaluator = ReportEvaluator(
            config=self.config,
            metric_instances={"mock_metric": self.mock_metric, "llm_judge": mock_llm},
            object_store_client=self.mock_object_store,
            report_url_pattern=self.report_url_pattern,
            include_vlm_output=False,
        )

        section_config = FieldConfig(
            method="average",
            fields={"known_field": FieldConfig(method="mock_metric")},
            allow_dynamic_field_discovery=True,
        )

        reference = {"known_field": "ref1", "ref_only": "ref2"}
        actual = {"known_field": "act1", "surprise_field": "act2"}

        result = await evaluator.evaluate_tree(
            reference=reference, actual=actual, config=section_config, path=["section"]
        )

        assert isinstance(result, EvaluationScore)

        # Should have explicit field scored by mock_metric
        assert "known_field" in result.field_scores
        assert result.field_scores["known_field"].method == "mock_metric"
        assert result.field_scores["known_field"].section_score == MOCK_METRIC_SCORE

        # Should have dynamic field scored by llm_judge with field discovery
        assert "surprise_field" in result.field_scores
        assert result.field_scores["surprise_field"].method == "llm_judge_with_field_discovery"
        assert result.field_scores["surprise_field"].section_score == 0.85

        assert result.section_score == 0.825

    @pytest.mark.asyncio
    async def test_score_value_with_env_vars(self):
        """Test _score_value expands environment variables embedded in strings."""
        import os

        os.environ["TEST_VAR"] = "test_value"
        os.environ["ANOTHER_VAR"] = "another"

        mock_metric = AsyncMock(return_value=MOCK_METRIC_SCORE)
        self.evaluator.metric_instances["mock_metric"].evaluate = mock_metric

        await self.evaluator._score_value(
            reference="Expected value is $TEST_VAR and $ANOTHER_VAR here",
            actual="actual_value",
            method="mock_metric",
            path=["field"],
        )

        call_args = mock_metric.call_args
        assert call_args[0][1] == "Expected value is test_value and another here"

        del os.environ["TEST_VAR"]
        del os.environ["ANOTHER_VAR"]

    @pytest.mark.asyncio
    async def test_evaluate_item_success(self):
        """Test evaluate_item with successful evaluation."""
        # Create temp reference file
        reference_data = {"report": {"summary": "ref summary", "details": "ref details"}}
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(reference_data, f)
            reference_path = f.name

        try:
            # Mock the fetch_and_parse_report
            generated_data = {"summary": "gen summary", "details": "gen details"}
            with patch(
                "vss_agents.evaluators.report_evaluator.evaluate._fetch_and_parse_report",
                AsyncMock(return_value=(generated_data, "report_123.md")),
            ):
                item = EvalInputItem(
                    id="test_item",
                    input_obj="test query",
                    expected_output_obj=reference_path,
                    output_obj="Here is the report: report_123.md",
                    trajectory=[],
                    expected_trajectory=[],
                    full_dataset_entry={"id": "test_item", "evaluation_method": ["report"]},
                )

                result = await self.evaluator.evaluate_item(item)

                assert result.id == "test_item"
                assert result.score == MOCK_METRIC_SCORE
                assert isinstance(result.reasoning, dict)
                assert set(result.reasoning.keys()) == {"sections", "metadata"}
                assert set(result.reasoning["metadata"].keys()) == {"reference_file", "actual_file"}
                assert result.reasoning["metadata"]["actual_file"] == "report_123.md"
                assert reference_path in result.reasoning["metadata"]["reference_file"]
        finally:
            Path(reference_path).unlink()

    @pytest.mark.asyncio
    async def test_evaluate_item_error_handling(self):
        """Test evaluate_item handles errors gracefully."""
        item = EvalInputItem(
            id="test_item",
            input_obj="test query",
            expected_output_obj="/nonexistent/path.json",
            output_obj="report content",
            trajectory=[],
            expected_trajectory=[],
            full_dataset_entry={"id": "test_item", "evaluation_method": ["report"]},
        )

        result = await self.evaluator.evaluate_item(item)

        assert result.id == "test_item"
        assert result.score is None
        assert isinstance(result.reasoning, dict)
        assert set(result.reasoning.keys()) == {"error"}
        assert isinstance(result.reasoning["error"], str)
        assert len(result.reasoning["error"]) > 0

    @pytest.mark.asyncio
    async def test_evaluate_item_with_vlm_scoring(self):
        """Test evaluate_item includes vlm_field_score when enabled."""
        mock_metric_0_6 = MockMetric(score=0.6)  # vlm_field_1
        mock_metric_0_8 = MockMetric(score=0.8)  # vlm_field_2
        mock_metric_1_0 = MockMetric(score=1.0)  # other_field

        config = EvalMetricsConfig.from_dict(
            {
                "report": {
                    "method": "average",
                    "fields": {
                        "vlm_field_1": {"method": "metric_0_6"},
                        "vlm_field_2": {"method": "metric_0_8"},
                        "other_field": {"method": "metric_1_0"},
                    },
                }
            }
        )

        evaluator = ReportEvaluator(
            config=config,
            metric_instances={
                "metric_0_6": mock_metric_0_6,
                "metric_0_8": mock_metric_0_8,
                "metric_1_0": mock_metric_1_0,
                "llm_judge": self.mock_llm_judge,
            },
            object_store_client=self.mock_object_store,
            report_url_pattern=self.report_url_pattern,
            include_vlm_output=True,
            vlm_related_fields=["vlm_field_1", "vlm_field_2"],
        )

        reference_data = {
            "report": {
                "vlm_field_1": "ref1",
                "vlm_field_2": "ref2",
                "other_field": "ref3",
            }
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(reference_data, f)
            reference_path = f.name

        try:
            generated_data = {
                "vlm_field_1": "gen1",
                "vlm_field_2": "gen2",
                "other_field": "gen3",
            }
            with patch(
                "vss_agents.evaluators.report_evaluator.evaluate._fetch_and_parse_report",
                AsyncMock(return_value=(generated_data, "report_123.md")),
            ):
                item = EvalInputItem(
                    id="test_item",
                    input_obj="test query",
                    expected_output_obj=reference_path,
                    output_obj="Here is the report: report_123.md",
                    trajectory=[],
                    expected_trajectory=[],
                    full_dataset_entry={"id": "test_item", "evaluation_method": ["report"]},
                )

                result = await evaluator.evaluate_item(item)

                # Overall score should be average of all 3 fields: (0.6 + 0.8 + 1.0) / 3 = 0.8
                assert result.score == pytest.approx(0.8)

                # VLM score should be average of only vlm_field_1 and vlm_field_2: (0.6 + 0.8) / 2 = 0.7
                assert result.vlm_field_score == pytest.approx(0.7)
        finally:
            Path(reference_path).unlink()

    @pytest.mark.asyncio
    async def test_evaluate_item_with_vlm_scoring_disabled(self):
        """Test evaluate_item doesn't include vlm_field_score when disabled."""
        evaluator = ReportEvaluator(
            config=self.config,
            metric_instances=self.metric_instances,
            object_store_client=self.mock_object_store,
            report_url_pattern=self.report_url_pattern,
            include_vlm_output=False,
            vlm_related_fields=None,
        )

        # Create temp reference file
        reference_data = {"report": {"summary": "ref", "details": "ref"}}
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(reference_data, f)
            reference_path = f.name

        try:
            generated_data = {"summary": "gen", "details": "gen"}
            with patch(
                "vss_agents.evaluators.report_evaluator.evaluate._fetch_and_parse_report",
                AsyncMock(return_value=(generated_data, "report_123.md")),
            ):
                item = EvalInputItem(
                    id="test_item",
                    input_obj="test query",
                    expected_output_obj=reference_path,
                    output_obj="Here is the report: report_123.md",
                    trajectory=[],
                    expected_trajectory=[],
                    full_dataset_entry={"id": "test_item", "evaluation_method": ["report"]},
                )

                result = await evaluator.evaluate_item(item)

                assert result.score == MOCK_METRIC_SCORE
                # VLM score should be None when disabled
                assert result.vlm_field_score is None
        finally:
            Path(reference_path).unlink()

    @pytest.mark.asyncio
    async def test_evaluate_item_vlm_scoring_treats_none_as_zero(self):
        """Test VLM scoring treats None scores as 0.0 in average."""
        mock_metric_0_6 = MockMetric(score=0.6)
        mock_metric_none = MockMetric(score=None)
        mock_metric_1_0 = MockMetric(score=1.0)

        config = EvalMetricsConfig.from_dict(
            {
                "report": {
                    "method": "average",
                    "fields": {
                        "vlm_field_1": {"method": "metric_0_6"},
                        "vlm_field_2": {"method": "metric_none"},
                        "vlm_field_3": {"method": "metric_1_0"},
                    },
                }
            }
        )

        evaluator = ReportEvaluator(
            config=config,
            metric_instances={
                "metric_0_6": mock_metric_0_6,
                "metric_none": mock_metric_none,
                "metric_1_0": mock_metric_1_0,
                "llm_judge": self.mock_llm_judge,
            },
            object_store_client=self.mock_object_store,
            report_url_pattern=self.report_url_pattern,
            include_vlm_output=True,
            vlm_related_fields=["vlm_field_1", "vlm_field_2", "vlm_field_3"],
        )

        reference_data = {
            "report": {
                "vlm_field_1": "ref1",
                "vlm_field_2": "ref2",
                "vlm_field_3": "ref3",
            }
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(reference_data, f)
            reference_path = f.name

        try:
            generated_data = {
                "vlm_field_1": "gen1",
                "vlm_field_2": "gen2",
                "vlm_field_3": "gen3",
            }
            with patch(
                "vss_agents.evaluators.report_evaluator.evaluate._fetch_and_parse_report",
                AsyncMock(return_value=(generated_data, "report_123.md")),
            ):
                item = EvalInputItem(
                    id="test_item",
                    input_obj="test query",
                    expected_output_obj=reference_path,
                    output_obj="Here is the report: report_123.md",
                    trajectory=[],
                    expected_trajectory=[],
                    full_dataset_entry={"id": "test_item", "evaluation_method": ["report"]},
                )

                result = await evaluator.evaluate_item(item)

                # VLM score should treat None as 0.0: (0.6 + 0.0 + 1.0) / 3 = 0.533...
                # vlm_field_2 with None is treated as 0.0
                assert result.vlm_field_score == pytest.approx(0.533, rel=0.01)
        finally:
            Path(reference_path).unlink()
