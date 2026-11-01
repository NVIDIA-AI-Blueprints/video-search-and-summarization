# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Regressions from the Evaluation V2 review of Track A: macro-F1 support, the runner's token bound, checkpoints."""

from __future__ import annotations

import json

import pytest

from evalv2.adapters.base import CallContext
from evalv2.adapters.fake import FakeAdapter, Scripted
from evalv2.core.artifacts import ArtifactStore
from evalv2.core.budget import BudgetLedger, Cap
from evalv2.core.checkpoint import Checkpoint
from evalv2.core.config import RunConfig
from evalv2.core.retry import POLICIES, Failure
from evalv2.tasks.incident_objects import v1
from evalv2.tasks.incident_objects.aggregate import type_metrics
from evalv2.tasks.incident_objects.runner import IncidentObjectsTask

MODEL = "nvidia/cosmos-3-super-reasoner"


def _video(expected, predicted, outcome="scored"):
    return {"outcome": outcome, "incident_field_scores": {"type": {"expected": expected, "predicted": predicted}}}


# --- B12: macro-F1 over the labels with ground-truth support ------------------------------------------------------


def test_perfect_predictions_on_a_subset_without_every_label_score_one():
    videos = [_video("Assault", "assault"), _video("Burglary", "burglary"), _video("Road Accident", "road accident")]
    result = type_metrics(videos)
    assert result["macro_f1"] == 1.0, "a label no clip has must not cap macro-F1 (it was 0.6 here)"
    assert result["per_class_f1"]["explosion"] is None and result["per_class_f1"]["animal attack"] is None
    assert result["macro_f1_labels"] == ["road accident", "burglary", "assault"]
    assert result["support"] == {"road accident": 1, "burglary": 1, "explosion": 0, "assault": 1, "animal attack": 0}


def test_a_wrong_prediction_of_an_absent_label_still_costs_the_true_labels_recall():
    result = type_metrics([_video("Assault", "explosion"), _video("Assault", "assault"), _video("Burglary", "burglary")])
    assert result["per_class_f1"]["explosion"] is None and result["per_class_f1"]["assault"] == round(2 / 3, 4)
    assert result["macro_f1"] == pytest.approx(round((2 / 3 + 1) / 2, 4))
    assert result["confusion"]["assault"]["explosion"] == 1


def test_runs_without_model_output_leave_macro_f1_undefined():
    assert type_metrics([_video("Assault", None, outcome="timeout")])["macro_f1"] is None


# --- A1: every Track A attempt reserves the bound preflight assumed -----------------------------------------------


def _config():
    return RunConfig.model_validate({
        "experiment": "v2-dev-track-a", "split": {"version": "split-v2", "half": "dev"},
        "subjects": [{"id": "super", "adapter": "fake", "model": MODEL}],
        "tasks": {"incident_objects": {}}, "repeats": 1, "seed": 1,
        "caps": [{"ledger": "inference", "max_attempts": 100}]})


def _execute(tmp_path, script, *, tokens_bound=20000, budget_ledger="inference", caps=None):
    task = IncidentObjectsTask(video_url=lambda clip: f"https://signed.example/{clip}.mp4",
                               video_duration=lambda clip: 30.0)
    item = task.plan(_config(), {"clips": ["Assault001"]})[0]
    ctx = CallContext(store=ArtifactStore(tmp_path / "store"),
                      ledger=BudgetLedger(caps or [Cap("inference", max_attempts=9)]), policy=POLICIES["retry-v2"],
                      subject="super", task="incident_objects", budget_ledger=budget_ledger,
                      tokens_bound=tokens_bound, sleep=lambda s: None)
    return task.execute(item, FakeAdapter(id="fake", model=MODEL, script=script), ctx), ctx, task, item


def test_the_runner_reserves_v1s_ceiling_whatever_the_callers_context_says(tmp_path):
    timeouts = [Scripted(failure=Failure("timeout", "timed out"))] * 3
    record, ctx, task, item = _execute(tmp_path, timeouts, tokens_bound=20000, budget_ledger="judge")
    assert record.outcome == "timeout"
    assert ctx.ledger.totals("inference").tokens == 3 * v1.P1_TOKENS_CEILING, "charged as preflight assumed"
    assert ctx.ledger.totals("judge").attempts == 0, "P1 attempts are charged to the ledger preflight checked"
    per_attempt = task.budget_estimate(_config(), [item])["inference"]["tokens"]
    assert per_attempt == v1.P1_TOKENS_CEILING


def test_a_cap_one_token_below_the_preflight_worst_case_refuses_the_last_attempt(tmp_path):
    # Preflight: 1 item x 3 attempts x 35,000 = 105,000. With a 20,000 bound a 4th attempt's room was never checked.
    timeouts = [Scripted(failure=Failure("timeout", "timed out"))] * 3
    record, ctx, _, _ = _execute(tmp_path, timeouts, caps=[Cap("inference", max_tokens=104999)])
    assert ctx.ledger.totals("inference").attempts == 2 and record.failure_kind == "budget_exceeded"


# --- A2 with Track A's labels: the failure kind, not V1's folded label, decides finality ----------------------------


@pytest.mark.parametrize(("script", "kind", "final"), [
    ([Scripted(failure=Failure("transport_error", "connection reset"))] * 3, "transport_error", False),
    ([Scripted(failure=Failure("invalid_response", "JSON null"))], "invalid_response", True),
    ([Scripted(failure=Failure("http_error", "400 bad request", 400))], "http_error", True),
])
def test_track_a_failures_reach_the_checkpoint_with_their_failure_kind(tmp_path, script, kind, final):
    record, _, _, _ = _execute(tmp_path, script)
    assert record.outcome == "http_error", "V1's label folds all three into http_error"
    assert record.failure_kind == kind
    checkpoint = Checkpoint(tmp_path / "progress.jsonl")
    checkpoint.record(record.work_key, record.outcome, ref=record.prediction_ref, failure_kind=record.failure_kind)
    assert checkpoint.is_final(record.work_key) is final


def test_a_model_output_is_final_for_the_checkpoint(tmp_path):
    record, _, _, _ = _execute(tmp_path, [Scripted(text=json.dumps({"incident": {"type": "assault"}}))])
    assert record.outcome == "contract_invalid" and record.failure_kind is None
    checkpoint = Checkpoint(tmp_path / "progress.jsonl")
    checkpoint.record(record.work_key, record.outcome, ref=record.prediction_ref, failure_kind=record.failure_kind)
    assert checkpoint.is_final(record.work_key)
