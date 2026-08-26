# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from benchmark.multiple_choice import parse_choice_prediction


def test_parser_accepts_only_one_exact_option_letter() -> None:
    case_id = "vss-sample-warehouse-4min-1"
    assert parse_choice_prediction(case_id, " A \n").label == "A"
    assert parse_choice_prediction(case_id, "The answer is A") is None
    assert parse_choice_prediction(case_id, "AA") is None
