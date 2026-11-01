# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Dashboard spec benchmark: the allowlist, every valid spec, blind references and the spec scorers."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from evalv2.datasets import generation_prompts as gp
from evalv2.datasets.records import validate_record
from evalv2.references import dashboard as dash
from evalv2.tasks.dashboard import scoring as sc

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "chart-allowlist.json"
ALLOWLIST = json.loads(FIXTURE.read_text())
SPEC = {"metric": "incidents", "dimension": "severity", "period": "last_30_days", "incidentType": "assault",
        "severity": None}
GEN = {"prompt": "gen-dashboard-request-v1", "model": "claude"}
VAL = {"id": "gen-dashboard-respec-v1"}


@pytest.mark.skipif(not dash.ALLOWLIST_PATH.is_file(),
                    reason="the console's allowlist export is not on this branch yet")
def test_the_fixture_is_the_consoles_exported_allowlist():
    assert dash.load_allowlist() == ALLOWLIST


def test_every_valid_spec_is_enumerated_once():
    specs = dash.all_specs(ALLOWLIST)
    assert len(specs) == 1690 and len({json.dumps(s, sort_keys=True) for s in specs}) == 1690
    assert all(dash.parse_spec(s, ALLOWLIST) == s for s in specs)


@pytest.mark.parametrize(("reply", "valid"), [
    (SPEC, True),
    ({**SPEC, "title": "x"}, False),
    ({**SPEC, "metric": "instruments"}, False),
    ({**SPEC, "period": "last_2_years"}, False),
    ({**SPEC, "incidentType": "robbery"}, False),
    ({"metric": "analyses", "dimension": "model", "period": "all_time", "incidentType": None, "severity": None}, True),
    ({"metric": "analyses", "dimension": "model", "period": "all_time", "incidentType": "assault", "severity": None},
     False),
    ({"unsupported": True}, False),
    ("not an object", False),
])
def test_the_spec_parser_follows_the_consoles_rules(reply, valid):
    assert (dash.parse_spec(reply, ALLOWLIST) is not None) is valid


def test_a_missing_export_is_explained(tmp_path):
    with pytest.raises(dash.DashboardReferenceError, match="export:prompts"):
        dash.load_allowlist(tmp_path / "allowlist.json")


def test_references_validate_only_on_exact_blind_agreement():
    record = dash.build_record("Pie of assault severity over the last month", SPEC, ALLOWLIST,
                               paraphrases=["Severity split for assaults, past 30 days", "Assault severity last month"],
                               dataset="dashboard-v0", generator=GEN, rederived=dict(SPEC), validator=VAL)
    assert record["tier"] == "ai_validated" and validate_record(record) == []
    wrong = dash.build_record("Pie of assault severity over the last month", SPEC, ALLOWLIST, paraphrases=["a", "b"],
                              dataset="dashboard-v0", generator=GEN, rederived={**SPEC, "period": "all_time"},
                              validator=VAL)
    assert wrong["tier"] == "ai_proposed" and "validators_disagree" in wrong["flags"]
    refusal = dash.build_record("Incidents at the north gate in March", dash.UNSUPPORTED, ALLOWLIST,
                                paraphrases=["North gate incidents in March", "March north gate chart"],
                                dataset="dashboard-v0", generator=GEN, rederived={"unsupported": True},
                                validator=VAL)
    assert refusal["tier"] == "ai_validated" and refusal["strata"]["class"] == "unsupported"
    with pytest.raises(dash.DashboardReferenceError, match="allowlist"):
        dash.build_record("x", {**SPEC, "metric": "analyses"}, ALLOWLIST, paraphrases=[], dataset="d", generator=GEN)


def test_the_validator_packet_is_blind():
    record = dash.build_record("Severity of assaults last month", SPEC, ALLOWLIST, paraphrases=["p1", "p2"],
                               dataset="d", generator=GEN)
    packet = dash.validator_packet(record, ALLOWLIST, prompt_id="gen-dashboard-respec-v1")
    dash.assert_blind(packet, record)
    with pytest.raises(dash.DashboardReferenceError):
        dash.assert_blind({**packet, "expected": SPEC}, record)
    with pytest.raises(dash.DashboardReferenceError):
        dash.assert_blind({**packet, "request": "another request"}, record)


@pytest.mark.parametrize(("expected", "reply", "outcome"), [
    (SPEC, SPEC, "exact"),
    (SPEC, {**SPEC, "period": "all_time"}, "valid_but_wrong"),
    (SPEC, {"unsupported": True}, "false_refusal"),
    (SPEC, {**SPEC, "title": "x"}, "parse_failure"),
    (dash.UNSUPPORTED, {"unsupported": True}, "refusal_correct"),
    (dash.UNSUPPORTED, SPEC, "false_accept"),
    (dash.UNSUPPORTED, None, "parse_failure"),
])
def test_each_reply_has_one_outcome(expected, reply, outcome):
    assert sc.outcome(reply, expected, ALLOWLIST)[0] == outcome


def test_aggregates_field_accuracy_stability_and_tiers():
    spec_record = dash.build_record("Severity of assaults last month", SPEC, ALLOWLIST, paraphrases=["p1", "p2"],
                                    dataset="d", generator=GEN, rederived=dict(SPEC), validator=VAL)
    refusal_record = dash.build_record("Incidents by camera", dash.UNSUPPORTED, ALLOWLIST, paraphrases=["q1", "q2"],
                                       dataset="d", generator=GEN, rederived={"unsupported": True}, validator=VAL)
    scores = [sc.score_reply(spec_record, SPEC, ALLOWLIST, repeat=1),
              sc.score_reply(spec_record, {**SPEC, "period": "all_time"}, ALLOWLIST, repeat=2),
              sc.score_reply(refusal_record, {"unsupported": True}, ALLOWLIST, repeat=1),
              sc.score_reply(refusal_record, {"unsupported": True}, ALLOWLIST, repeat=2)]
    summary = sc.aggregate(scores)["ai_validated"]
    assert summary["exact_spec_accuracy"] == 0.5 and summary["valid_but_wrong_rate"] == 0.5
    assert summary["field_accuracy"]["period"] == 0.5 and summary["field_accuracy"]["dimension"] == 1.0
    assert summary["refusal_precision"] == 1.0 and summary["refusal_recall"] == 1.0
    assert summary["stability"] == 0.5, "the refusal is stable across repeats; the spec is not"


def test_the_dashboard_prompts_are_registered():
    for prompt_id, role in (("gen-dashboard-request-v1", "generator"), ("gen-dashboard-respec-v1", "validator")):
        entry = next(p for p in gp.load()["prompts"] if p["id"] == prompt_id)
        assert entry["role"] == role and entry["purpose"] == "dashboard_reference"
