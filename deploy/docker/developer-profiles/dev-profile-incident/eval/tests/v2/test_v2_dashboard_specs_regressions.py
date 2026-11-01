# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Regressions from the Evaluation V2 review of the dashboard specs: a non-string value is a parse failure, not a crash."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from evalv2.references import dashboard as dash
from evalv2.tasks.dashboard import scoring as sc

ALLOWLIST = json.loads((Path(__file__).resolve().parent / "fixtures" / "chart-allowlist.json").read_text())
SPEC = {"metric": "incidents", "dimension": "severity", "period": "last_30_days", "incidentType": "assault",
        "severity": None}
GEN = {"prompt": "gen-dashboard-request-v1", "model": "claude"}


@pytest.mark.parametrize("key", dash.SPEC_KEYS)
@pytest.mark.parametrize("value", [["severity"], {"name": "severity"}, 7, 1.5, True])
def test_a_non_string_value_is_refused_not_raised(key, value):
    assert dash.parse_spec({**SPEC, key: value}, ALLOWLIST) is None


def test_the_reviews_reply_is_a_parse_failure_in_the_scorer():
    reply = {"metric": "incidents", "dimension": ["severity"], "period": "last_30_days", "incidentType": None,
             "severity": None}
    record = dash.build_record("Severity of incidents last month", {**SPEC, "incidentType": None}, ALLOWLIST,
                               paraphrases=["a", "b"], dataset="dashboard-v0", generator=GEN)
    scored = sc.score_reply(record, reply, ALLOWLIST)
    assert scored["outcome"] == "parse_failure" and scored["spec"] is None
    assert sc.outcome({**reply, "dimension": "severity", "period": ["last_30_days"]}, SPEC, ALLOWLIST)[0] == (
        "parse_failure")


def test_null_filters_stay_valid_and_a_refusal_must_be_exactly_true():
    assert dash.parse_spec({**SPEC, "incidentType": None}, ALLOWLIST) == {**SPEC, "incidentType": None}
    assert dash.is_refusal({"unsupported": True})
    assert not dash.is_refusal({"unsupported": 1}), "1 == True in Python, but the console wants true"
    record = dash.build_record("Incidents at the north gate", dash.UNSUPPORTED, ALLOWLIST, paraphrases=["a", "b"],
                               dataset="dashboard-v0", generator=GEN)
    assert sc.score_reply(record, {"unsupported": 1}, ALLOWLIST)["outcome"] == "parse_failure"
