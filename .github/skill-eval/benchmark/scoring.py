# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Pure scoring functions with no file or Harbor dependencies."""

from __future__ import annotations

from collections.abc import Sequence

from benchmark.domain import GroupStructure


def score_relevance(correctness: Sequence[bool]) -> float:
    if not correctness:
        raise ValueError("relevance groups must contain at least one question")
    return (sum(correctness) / len(correctness)) ** 2


def score_logic(
    correctness: Sequence[bool],
    group_structure: GroupStructure,
) -> float:
    """Score one four-question VideoMME-v2 logic group on a 0-to-1 scale."""
    if len(correctness) != 4:
        raise ValueError("a VideoMME-v2 logic group must contain four questions")

    q1, q2, q3, q4 = correctness

    if group_structure == [1, 2, 3, 4]:
        # Q1 -> Q2 -> Q3 -> Q4
        progress = next(
            (index for index, correct in enumerate(correctness) if not correct),
            len(correctness),
        )

        score_by_progress = {
            0: 0.0,
            1: 1 / 16,
            2: 4 / 16,
            3: 9 / 16,
            4: 1.0,
        }

    elif group_structure == [1, [2, 3], 4]:
        # Q1 -> (Q2 and Q3) -> Q4
        if not q1:
            progress = 0
        elif not q2 and not q3:
            progress = 1
        elif not (q2 and q3):
            progress = 2
        elif not q4:
            progress = 3
        else:
            progress = 4

        score_by_progress = {
            0: 0.0,
            1: 1 / 12,
            2: 4 / 12,
            3: 7 / 12,
            4: 1.0,
        }

    elif group_structure == [[1, 2], 3, 4]:
        # (Q1 and Q2) -> Q3 -> Q4
        initial_correct = int(q1) + int(q2)

        if initial_correct == 0:
            progress = 0
        elif initial_correct == 1:
            progress = 1
        elif not q3:
            progress = 2
        elif not q4:
            progress = 3
        else:
            progress = 4

        score_by_progress = {
            0: 0.0,
            1: 1 / 10,
            2: 2 / 10,
            3: 5 / 10,
            4: 1.0,
        }

    else:
        raise ValueError(
            f"unsupported VideoMME-v2 logic structure: {group_structure}"
        )

    return score_by_progress[progress]


def aggregate_overall_mean(group_scores: Sequence[float]) -> float:
    if not group_scores:
        raise ValueError("cannot aggregate an empty benchmark")
    return sum(group_scores) / len(group_scores)
