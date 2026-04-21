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
"""Tests for vss_agents/evaluators/report_evaluator/field_evaluators/."""

import pytest

from vss_agents.evaluators.report_evaluator.field_evaluators.base import METRIC_REGISTRY
from vss_agents.evaluators.report_evaluator.field_evaluators.base import EvaluationMetric
from vss_agents.evaluators.report_evaluator.field_evaluators.base import register_metric
from vss_agents.evaluators.report_evaluator.field_evaluators.common import ExactMatchMetric
from vss_agents.evaluators.report_evaluator.field_evaluators.common import F1Metric
from vss_agents.evaluators.report_evaluator.field_evaluators.common import NonEmptyMetric
from vss_agents.evaluators.report_evaluator.field_evaluators.common import RegexMetric
from vss_agents.evaluators.report_evaluator.field_evaluators.common import calculate_f1_score
from vss_agents.evaluators.report_evaluator.field_evaluators.common import tokenize_text


class TestTokenizeText:
    """Tests for tokenize_text function."""

    def test_tokenize_simple_text(self):
        """Test tokenizing simple text."""
        result = tokenize_text("Hello World")
        assert result == ["hello", "world"]

    def test_tokenize_with_punctuation(self):
        """Test tokenizing text with punctuation."""
        result = tokenize_text("Hello, World! How are you?")
        assert "hello" in result
        assert "world" in result
        assert "how" in result

    def test_tokenize_numbers(self):
        """Test tokenizing text with numbers."""
        result = tokenize_text("Test123 456")
        assert "test123" in result
        assert "456" in result

    def test_tokenize_empty_string(self):
        """Test tokenizing empty string."""
        result = tokenize_text("")
        assert result == []

    def test_tokenize_case_insensitive(self):
        """Test that tokenization is case insensitive."""
        result = tokenize_text("HELLO hello HeLLo")
        assert result == ["hello", "hello", "hello"]


class TestCalculateF1Score:
    """Tests for calculate_f1_score function."""

    def test_f1_identical_tokens(self):
        """Test F1 score with identical tokens."""
        score = calculate_f1_score(["a", "b", "c"], ["a", "b", "c"])
        assert score == 1.0

    def test_f1_no_overlap(self):
        """Test F1 score with no overlap."""
        score = calculate_f1_score(["a", "b"], ["c", "d"])
        assert score == 0.0

    def test_f1_partial_overlap(self):
        """Test F1 score with partial overlap."""
        score = calculate_f1_score(["a", "b", "c"], ["a", "b", "d"])
        assert 0 < score < 1

    def test_f1_both_empty(self):
        """Test F1 score with both empty lists."""
        score = calculate_f1_score([], [])
        assert score == 1.0

    def test_f1_pred_empty(self):
        """Test F1 score with empty prediction."""
        score = calculate_f1_score([], ["a", "b"])
        assert score == 0.0

    def test_f1_ref_empty(self):
        """Test F1 score with empty reference."""
        score = calculate_f1_score(["a", "b"], [])
        assert score == 0.0

    def test_f1_single_token_match(self):
        """Test F1 score with single matching token."""
        score = calculate_f1_score(["hello"], ["hello"])
        assert score == 1.0

    def test_f1_zero_precision_recall_edge_case(self):
        """Test F1 score edge case where precision + recall could be 0."""
        # This tests line 50 - though it's hard to reach since
        # intersection check comes first
        score = calculate_f1_score(["x"], ["y"])
        assert score == 0.0


class TestNonEmptyMetric:
    """Tests for NonEmptyMetric."""

    @pytest.mark.asyncio
    async def test_non_empty_with_content(self):
        """Test non-empty metric with content."""
        metric = NonEmptyMetric()
        score = await metric.evaluate("some content", "reference")
        assert score == 1.0

    @pytest.mark.asyncio
    async def test_non_empty_with_empty_string(self):
        """Test non-empty metric with empty string."""
        metric = NonEmptyMetric()
        score = await metric.evaluate("", "reference")
        assert score == 0.0

    @pytest.mark.asyncio
    async def test_non_empty_with_whitespace_only(self):
        """Test non-empty metric with whitespace only."""
        metric = NonEmptyMetric()
        score = await metric.evaluate("   ", "reference")
        assert score == 0.0

    @pytest.mark.asyncio
    async def test_non_empty_with_none(self):
        """Test non-empty metric with None."""
        metric = NonEmptyMetric()
        score = await metric.evaluate(None, "reference")
        assert score == 0.0


class TestF1Metric:
    """Tests for F1Metric."""

    @pytest.mark.asyncio
    async def test_f1_metric_identical(self):
        """Test F1 metric with identical strings."""
        metric = F1Metric()
        score = await metric.evaluate("hello world", "hello world")
        assert score == 1.0

    @pytest.mark.asyncio
    async def test_f1_metric_different(self):
        """Test F1 metric with different strings."""
        metric = F1Metric()
        score = await metric.evaluate("hello world", "goodbye moon")
        assert score == 0.0

    @pytest.mark.asyncio
    async def test_f1_metric_partial(self):
        """Test F1 metric with partial match."""
        metric = F1Metric()
        score = await metric.evaluate("hello world test", "hello world other")
        assert 0 < score < 1


class TestExactMatchMetric:
    """Tests for ExactMatchMetric."""

    @pytest.mark.asyncio
    async def test_exact_match_identical(self):
        """Test exact match with identical strings."""
        metric = ExactMatchMetric()
        score = await metric.evaluate("hello world", "hello world")
        assert score == 1.0

    @pytest.mark.asyncio
    async def test_exact_match_different(self):
        """Test exact match with different strings."""
        metric = ExactMatchMetric()
        score = await metric.evaluate("hello", "world")
        assert score == 0.0

    @pytest.mark.asyncio
    async def test_exact_match_whitespace_normalized(self):
        """Test exact match with normalized whitespace."""
        metric = ExactMatchMetric()
        score = await metric.evaluate("hello   world", "hello world")
        assert score == 1.0

    @pytest.mark.asyncio
    async def test_exact_match_case_sensitive(self):
        """Test exact match is case sensitive."""
        metric = ExactMatchMetric()
        score = await metric.evaluate("Hello", "hello")
        assert score == 0.0


class TestRegexMetric:
    """Tests for RegexMetric."""

    @pytest.mark.asyncio
    async def test_regex_match(self):
        """Test regex match."""
        metric = RegexMetric()
        score = await metric.evaluate("hello123", r"hello\d+")
        assert score == 1.0

    @pytest.mark.asyncio
    async def test_regex_no_match(self):
        """Test regex no match."""
        metric = RegexMetric()
        score = await metric.evaluate("hello", r"world")
        assert score == 0.0

    @pytest.mark.asyncio
    async def test_regex_invalid_pattern(self):
        """Test regex with invalid pattern."""
        metric = RegexMetric()
        score = await metric.evaluate("test", r"[invalid(pattern")
        assert score == 0.0

    @pytest.mark.asyncio
    async def test_regex_email_pattern(self):
        """Test regex with email pattern."""
        metric = RegexMetric()
        score = await metric.evaluate("test@example.com", r"[\w.]+@[\w.]+\.\w+")
        assert score == 1.0


class TestRegisterMetric:
    """Tests for register_metric decorator."""

    def test_register_new_metric(self):
        """Test registering a new metric."""
        # Note: We can't easily test this without modifying the registry
        # Just verify the registry contains expected metrics
        assert "f1" in METRIC_REGISTRY
        assert "exact_match" in METRIC_REGISTRY
        assert "non_empty" in METRIC_REGISTRY
        assert "regex" in METRIC_REGISTRY

    def test_duplicate_registration_raises(self):
        """Test that duplicate registration raises error."""
        with pytest.raises(ValueError, match="already registered"):

            @register_metric("f1")  # f1 is already registered
            class DuplicateMetric(EvaluationMetric):
                async def evaluate(self, actual, reference, field_name=""):
                    return 1.0
