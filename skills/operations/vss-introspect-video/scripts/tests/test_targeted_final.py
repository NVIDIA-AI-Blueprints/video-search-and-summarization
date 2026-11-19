# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
import copy

import inspection_workflow as workflow
import pytest
import vlm_inspection as vlm
from targeted_final_fixtures import discriminator
from test_inspection_workflow import SCOPE, initialize, response, run_and_merge


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    monkeypatch.setattr(vlm, "allowed_sensors_from_attempt_context", lambda: ())
    monkeypatch.setattr(vlm, "_run_vlm", lambda **kw: response())
    path = initialize(tmp_path, choice=True)
    run_and_merge(path)
    return path


def final_spec():
    return {
        "scope": copy.deepcopy(SCOPE),
        "sampling": "detail",
        "candidate_answer": "SECRET_OPTION_ANSWER",
        "discriminator": discriminator(),
    }


def test_candidate_never_enters_request_and_final_is_reserved_once(
    prepared, monkeypatch
):
    calls = []

    def invoke(**kw):
        calls.append(kw)
        return response("[0:10] Blue body.\nCHECK: supported")

    monkeypatch.setattr(vlm, "_run_vlm", invoke)
    before = workflow.read(prepared)
    receipt = workflow.run_final_check(prepared, final_spec(), None)
    assert "SECRET_OPTION_ANSWER" not in receipt["prompt"]
    assert "SECRET_OPTION_ANSWER" not in calls[0]["prompt"]
    assert "distinguishing_observation" in calls[0]["prompt"]
    assert receipt["spec"]["candidate_answer"] == "SECRET_OPTION_ANSWER"
    assert workflow.run_final_check(prepared, final_spec(), None) == receipt
    assert len(calls) == 1
    assert workflow.read(prepared)["vlm_calls_used"] == before["vlm_calls_used"]
    cited = before["claims"][0]["observation_ids"]
    result = workflow.checked_final_result(
        before, str(prepared.parent), "SECRET_OPTION_ANSWER", cited
    )
    assert result["final_check"]["verdict_scope"] == "targeted_fact_not_full_answer"


@pytest.mark.parametrize(
    "change",
    [
        {"claim_id": "claim-unknown"},
        {"alternative_observations": ["same", "same"]},
        {"alternative_observations": ["only one"]},
        {"uncertain_fact": "x" * 401},
        {"extra_prompt": "override"},
    ],
)
def test_invalid_discriminator_consumes_no_calls(prepared, monkeypatch, change):
    request = final_spec()
    request["discriminator"].update(change)
    monkeypatch.setattr(
        vlm, "_run_vlm", lambda **kw: pytest.fail("No paid call expected")
    )
    with pytest.raises(ValueError):
        workflow.run_final_check(prepared, request, None)
    assert not (prepared.parent / "final-check.started.json").exists()


def test_changed_candidate_does_not_buy_an_extra_call(prepared, monkeypatch):
    monkeypatch.setattr(vlm, "_run_vlm", lambda **kw: response("CHECK: inconclusive"))
    workflow.run_final_check(prepared, final_spec(), None)
    request = final_spec()
    request["candidate_answer"] = "different"
    with pytest.raises(ValueError, match="different"):
        workflow.run_final_check(prepared, request, None)
