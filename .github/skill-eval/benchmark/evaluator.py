# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""VideoMME-v2 benchmark evaluator."""

from __future__ import annotations

from benchmark.domain import (
    BenchmarkDataset,
    ChoicePrediction,
    EvaluationResult,
    GroupScore,
    GroupType,
    MetricResult,
)
from benchmark.multiple_choice import grade_choice
from benchmark.scoring import aggregate_overall_mean, score_logic, score_relevance


class VideoMMEv2Evaluator:
    def evaluate(
        self,
        dataset: BenchmarkDataset,
        predictions: tuple[ChoicePrediction, ...],
    ) -> EvaluationResult:
        known_ids = {case.task.case_id for group in dataset.groups for case in group.cases}
        prediction_ids = [prediction.case_id for prediction in predictions]
        if len(prediction_ids) != len(set(prediction_ids)):
            raise ValueError("duplicate prediction case_id")
        unknown = set(prediction_ids) - known_ids
        if unknown:
            raise ValueError(f"unknown prediction case_id(s): {sorted(unknown)}")
        by_case = {prediction.case_id: prediction for prediction in predictions}

        groups: list[GroupScore] = []
        for group in dataset.groups:
            grades = tuple(grade_choice(case, by_case.get(case.task.case_id)) for case in group.cases)
            correctness = tuple(grade.correct for grade in grades)
            if group.group_type is GroupType.RELEVANCE:
                score = score_relevance(correctness)
            else:
                score = score_logic(correctness, group.group_structure)
            groups.append(GroupScore(group.group_id, group.group_type, score, grades))

        overall = aggregate_overall_mean([group.score for group in groups])
        return EvaluationResult(
            metrics=(MetricResult("overall_group_score", overall),),
            group_scores=tuple(groups),
        )

