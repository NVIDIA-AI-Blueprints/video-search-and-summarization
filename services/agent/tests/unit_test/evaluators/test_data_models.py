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
"""Tests for vss_agents/evaluators/report_evaluator/data_models.py."""

from vss_agents.evaluators.report_evaluator.data_models import EvaluationScore


class TestEvaluationScore:
    """Tests for EvaluationScore model."""

    def test_create_evaluation_score(self):
        """Test creating an EvaluationScore."""
        score = EvaluationScore(
            section_score=0.85,
            method="f1",
            actual_value="predicted text",
            reference_value="reference text",
        )
        assert score.section_score == 0.85
        assert score.method == "f1"
        assert score.actual_value == "predicted text"
        assert score.reference_value == "reference text"
        assert score.error is None
        assert score.field_scores == {}

    def test_evaluation_score_with_error(self):
        """Test EvaluationScore with error."""
        score = EvaluationScore(
            section_score=None,
            method="llm_judge",
            error="Failed to evaluate",
        )
        assert score.section_score is None
        assert score.error == "Failed to evaluate"

    def test_evaluation_score_with_field_scores(self):
        """Test EvaluationScore with nested field_scores."""
        nested_score = EvaluationScore(
            section_score=0.9,
            method="exact_match",
        )
        score = EvaluationScore(
            section_score=0.85,
            method="average",
            field_scores={"field1": nested_score},
        )
        assert "field1" in score.field_scores
        assert score.field_scores["field1"].section_score == 0.9

    def test_evaluation_score_bounds(self):
        """Test EvaluationScore bounds (0.0 to 1.0)."""
        # Valid scores
        score_zero = EvaluationScore(section_score=0.0, method="test")
        score_one = EvaluationScore(section_score=1.0, method="test")
        assert score_zero.section_score == 0.0
        assert score_one.section_score == 1.0

    def test_evaluation_score_from_error(self):
        """Test EvaluationScore.from_error class method."""
        score = EvaluationScore.from_error(
            error_message="Something went wrong",
            method="llm_judge",
            actual_value="actual",
            reference_value="reference",
        )
        assert score.section_score is None
        assert score.error == "Something went wrong"
        assert score.method == "llm_judge"
        assert score.actual_value == "actual"
        assert score.reference_value == "reference"

    def test_evaluation_score_from_error_with_field_scores(self):
        """Test EvaluationScore.from_error with field_scores."""
        nested = EvaluationScore(section_score=0.5, method="test")
        score = EvaluationScore.from_error(
            error_message="Partial failure",
            field_scores={"partial": nested},
        )
        assert score.field_scores["partial"].section_score == 0.5

    def test_evaluation_score_from_error_defaults(self):
        """Test EvaluationScore.from_error with default values."""
        score = EvaluationScore.from_error(error_message="Error")
        assert score.section_score is None
        assert score.method == "unknown"
        assert score.actual_value is None
        assert score.reference_value is None
        assert score.field_scores == {}

    def test_evaluation_score_optional_fields(self):
        """Test EvaluationScore optional fields."""
        score = EvaluationScore(
            section_score=0.75,
            method="custom",
        )
        assert score.actual_value is None
        assert score.reference_value is None
        assert score.error is None

    def test_evaluation_score_none_section_score(self):
        """Test EvaluationScore with None section_score."""
        score = EvaluationScore(
            section_score=None,
            method="skipped",
        )
        assert score.section_score is None
