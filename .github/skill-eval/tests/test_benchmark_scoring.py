# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from benchmark.scoring import aggregate_overall_mean, score_relevance


def test_relevance_score_is_squared_group_accuracy() -> None:
    assert score_relevance((True, True, True, False)) == 0.5625


def test_overall_score_is_mean_of_group_scores() -> None:
    assert aggregate_overall_mean((0.5, 1.0)) == 0.75

