# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Pure, typed evaluation for grouped VideoMME-v2 answers."""

from __future__ import annotations

from collections.abc import Sequence

from benchmark.domain import (
    BenchmarkReport,
    CaseAnswer,
    CaseGrade,
    ChoiceAnswer,
    GroupEvaluationSpec,
    GroupScore,
    GroupType,
)
from benchmark.scoring import aggregate_overall_mean, score_logic, score_relevance


def _index_case_answers(
    answers: Sequence[CaseAnswer],
    *,
    description: str,
) -> dict[str, ChoiceAnswer]:
    indexed: dict[str, ChoiceAnswer] = {}
    for item in answers:
        if item.case_id in indexed:
            raise ValueError(f"duplicate case_id in {description}: {item.case_id}")
        indexed[item.case_id] = item.answer
    return indexed


def evaluate_group(
    spec: GroupEvaluationSpec,
    predictions: Sequence[CaseAnswer],
) -> GroupScore:
    """Grade one four-question group and return its validated result."""

    expected_by_case = _index_case_answers(
        spec.expected_answers,
        description="expected answers",
    )
    predicted_by_case = _index_case_answers(
        predictions,
        description="predictions",
    )
    if predicted_by_case.keys() != expected_by_case.keys():
        missing = sorted(expected_by_case.keys() - predicted_by_case.keys())
        unexpected = sorted(predicted_by_case.keys() - expected_by_case.keys())
        raise ValueError(
            f"prediction case IDs do not match expected case IDs; "
            f"missing={missing}, unexpected={unexpected}"
        )

    grades = tuple(
        CaseGrade(
            case_id=case_id,
            expected=expected,
            predicted=predicted_by_case[case_id],
            correct=predicted_by_case[case_id] == expected,
        )
        for case_id, expected in expected_by_case.items()
    )
    correctness = tuple(grade.correct for grade in grades)
    score = (
        score_relevance(correctness)
        if spec.group_type is GroupType.RELEVANCE
        else score_logic(correctness, spec.group_structure)
    )
    return GroupScore(
        group_id=spec.group_id,
        group_type=spec.group_type,
        score=score,
        question_accuracy=sum(correctness) / len(correctness),
        grades=grades,
    )


def aggregate_benchmark(
    groups: Sequence[GroupScore],
    *,
    expected_group_ids: Sequence[str],
    minimum: float,
) -> BenchmarkReport:
    """Validate a complete group set and apply the benchmark threshold."""

    expected_ids = tuple(expected_group_ids)
    if len(expected_ids) != len(set(expected_ids)):
        raise ValueError("expected benchmark group IDs must be unique")
    observed_ids = [group.group_id for group in groups]
    if len(observed_ids) != len(set(observed_ids)):
        raise ValueError("observed benchmark group IDs must be unique")
    if set(observed_ids) != set(expected_ids):
        raise ValueError("benchmark group-result set is incomplete")

    by_group_id = {group.group_id: group for group in groups}
    ordered = tuple(by_group_id[group_id] for group_id in expected_ids)
    overall = aggregate_overall_mean([group.score for group in ordered])
    return BenchmarkReport(
        overall_group_score=overall,
        minimum=minimum,
        passed=overall >= minimum,
        groups=ordered,
    )
