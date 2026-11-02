# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Regressions from the Evaluation V2 review of the dashboard figures: a reference whose own chart needs bars."""

from __future__ import annotations

import sys
from pathlib import Path

from evalv2.tasks.dashboard import scoring as sc

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_v2_dashboard_figures import ALLOWLIST, _chart, _record, _spec  # noqa: E402


def _pie(spec):
    return _chart(spec, "pie")


def test_an_exact_reply_to_a_pie_of_a_dimension_that_needs_bars_is_right():
    expected = _spec(dimension="report_day", period="last_90_days")
    assert _pie(expected) == {"kind": "needs_bar"}, "the reference's own chart is not a chart"
    scored = sc.score_reply(_record(expected), expected, ALLOWLIST, figures_of=_pie)
    assert (scored["outcome"], scored["figures"]) == ("exact", "no_chart_match")
    assert sc.aggregate([scored])["ai_proposed"]["figure_accuracy"] == 1.0, "it scored 0 as no_chart"


def test_a_chart_where_the_reference_needs_bars_and_the_reverse_are_wrong():
    needs_bar = _spec(dimension="report_day", period="last_90_days")
    pie_able = _spec(dimension="severity", period="last_90_days")
    shown = sc.score_reply(_record(needs_bar), pie_able, ALLOWLIST, figures_of=_pie)
    assert shown["figures"] == "figures_differ", "a pie of other data where the user should be told to use bars"
    missing = sc.score_reply(_record(pie_able), needs_bar, ALLOWLIST, figures_of=_pie)
    assert missing["figures"] == "no_chart"
    assert sc.aggregate([shown, missing])["ai_proposed"]["figure_accuracy"] == 0.0
