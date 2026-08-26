# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Strict multiple-choice answer parsing and grading."""

from __future__ import annotations

import re

from benchmark.domain import BenchmarkCase, CaseGrade, ChoicePrediction

ANSWER_RE = re.compile(r"^[A-H]$")


def parse_choice_prediction(case_id: str, response: str) -> ChoicePrediction | None:
    answer = response.strip()
    if not ANSWER_RE.fullmatch(answer):
        return None
    return ChoicePrediction(case_id=case_id, label=answer)


def grade_choice(case: BenchmarkCase, prediction: ChoicePrediction | None) -> CaseGrade:
    return CaseGrade(
        case_id=case.task.case_id,
        correct=prediction is not None and prediction.label == case.ground_truth.label,
    )

