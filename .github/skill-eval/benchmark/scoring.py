# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Pure scoring functions with no file or Harbor dependencies."""

from __future__ import annotations

from collections.abc import Sequence


class LogicScoringNotImplemented(NotImplementedError):
    """Raised until the VideoMME-v2 logic formula is finalized."""


def score_relevance(correctness: Sequence[bool]) -> float:
    if not correctness:
        raise ValueError("relevance groups must contain at least one question")
    return (sum(correctness) / len(correctness)) ** 2


def score_logic(correctness: Sequence[bool], group_structure: str) -> float:
    del correctness, group_structure
    raise LogicScoringNotImplemented("VideoMME-v2 logic scoring is intentionally deferred")


def aggregate_overall_mean(group_scores: Sequence[float]) -> float:
    if not group_scores:
        raise ValueError("cannot aggregate an empty benchmark")
    return sum(group_scores) / len(group_scores)

