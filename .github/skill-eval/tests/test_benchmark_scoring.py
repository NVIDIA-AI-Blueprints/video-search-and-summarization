# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import pytest
from benchmark.scoring import aggregate_overall_mean, score_logic, score_relevance


def test_relevance_score_is_squared_group_accuracy() -> None:
    assert score_relevance((True, True, True, False)) == 0.5625


@pytest.mark.parametrize(
    ("correctness", "expected"),
    (
        ((False, True, True, True), 0.0),
        ((True, False, True, True), 1 / 16),
        ((True, True, False, True), 4 / 16),
        ((True, True, True, False), 9 / 16),
        ((True, True, True, True), 1.0),
    ),
)
def test_logic_score_for_linear_dependency(correctness, expected) -> None:
    assert score_logic(correctness, [1, 2, 3, 4]) == pytest.approx(expected)


@pytest.mark.parametrize(
    ("correctness", "expected"),
    (
        ((False, True, True, True), 0.0),
        ((True, False, False, True), 1 / 12),
        ((True, True, False, True), 4 / 12),
        ((True, True, True, False), 7 / 12),
        ((True, True, True, True), 1.0),
    ),
)
def test_logic_score_for_parallel_middle_dependency(correctness, expected) -> None:
    assert score_logic(correctness, [1, [2, 3], 4]) == pytest.approx(expected)


@pytest.mark.parametrize(
    ("correctness", "expected"),
    (
        ((False, False, True, True), 0.0),
        ((True, False, True, True), 1 / 10),
        ((True, True, False, True), 2 / 10),
        ((True, True, True, False), 5 / 10),
        ((True, True, True, True), 1.0),
    ),
)
def test_logic_score_for_parallel_initial_dependency(correctness, expected) -> None:
    assert score_logic(correctness, [[1, 2], 3, 4]) == pytest.approx(expected)


def test_logic_score_requires_exactly_four_questions() -> None:
    with pytest.raises(ValueError, match="must contain four questions"):
        score_logic((True, True, True), [1, 2, 3, 4])


@pytest.mark.parametrize(
    "group_structure",
    (
        [1, 2, 4, 3],
        [1, [2, 4], 3],
    ),
)
def test_logic_score_rejects_unsupported_structure(group_structure) -> None:
    with pytest.raises(ValueError, match="unsupported VideoMME-v2 logic structure"):
        score_logic((True, True, True, True), group_structure)


def test_overall_score_is_mean_of_group_scores() -> None:
    assert aggregate_overall_mean((0.5, 1.0)) == 0.75
