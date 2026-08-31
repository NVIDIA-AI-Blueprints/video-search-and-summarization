# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import pytest
from benchmark.domain import (
    BenchmarkReport,
    CaseAnswer,
    ChoiceAnswer,
    GroupEvaluationSpec,
    GroupType,
)
from benchmark.evaluation import aggregate_benchmark, evaluate_group
from pydantic import ValidationError


def _answer(case_id: str, label: str) -> CaseAnswer:
    return CaseAnswer(case_id=case_id, answer=ChoiceAnswer(label=label))


def _spec(*, final_group: bool = False) -> GroupEvaluationSpec:
    return GroupEvaluationSpec(
        group_id="video-1",
        group_type=GroupType.RELEVANCE,
        group_structure="[1,2,3,4]",
        expected_answers=tuple(
            _answer(f"video-1-{index}", label) for index, label in enumerate("ABCD", 1)
        ),
        minimum=0.75,
        final_group=final_group,
        expected_group_ids=("video-1", "video-2"),
    )


@pytest.mark.parametrize(
    "payload",
    (
        'The answer is {"label":"A"}',
        '{"label":"Z"}',
        '{"label":"A","explanation":"extra"}',
    ),
)
def test_choice_answer_rejects_responses_outside_its_wire_schema(payload) -> None:
    with pytest.raises(ValidationError):
        ChoiceAnswer.model_validate_json(payload)


def test_choice_answer_is_shared_by_gold_and_prediction() -> None:
    gold = ChoiceAnswer(label="C")
    prediction = ChoiceAnswer.model_validate_json('{"label":"C"}')

    assert prediction == gold


def test_evaluate_group_returns_typed_grades_and_relevance_score() -> None:
    result = evaluate_group(
        _spec(),
        (
            _answer("video-1-1", "A"),
            _answer("video-1-2", "B"),
            _answer("video-1-3", "A"),
            _answer("video-1-4", "A"),
        ),
    )

    assert [grade.correct for grade in result.grades] == [True, True, False, False]
    assert result.question_accuracy == 0.5
    assert result.score == 0.25


def test_evaluate_group_rejects_mismatched_case_ids() -> None:
    with pytest.raises(ValueError, match="case IDs do not match"):
        evaluate_group(
            _spec(),
            (
                _answer("video-1-1", "A"),
                _answer("video-1-2", "B"),
                _answer("video-1-3", "C"),
                _answer("other-4", "D"),
            ),
        )


def test_aggregate_benchmark_returns_typed_report() -> None:
    first = evaluate_group(_spec(), _spec().expected_answers)
    second_spec = _spec().model_copy(
        update={
            "group_id": "video-2",
            "expected_answers": tuple(
                _answer(f"video-2-{index}", label)
                for index, label in enumerate("ABCD", 1)
            ),
        }
    )
    second = evaluate_group(second_spec, second_spec.expected_answers)

    report = aggregate_benchmark(
        (second, first),
        expected_group_ids=("video-1", "video-2"),
        minimum=0.75,
    )

    assert isinstance(report, BenchmarkReport)
    assert [group.group_id for group in report.groups] == ["video-1", "video-2"]
    assert report.overall_group_score == 1.0
    assert report.passed is True
