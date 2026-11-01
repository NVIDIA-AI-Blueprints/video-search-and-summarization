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

"""Unit tests for ``POST /api/v1/incidents/{incident_id}/analyze`` on the incident contract."""

from __future__ import annotations

from unittest.mock import AsyncMock
from unittest.mock import MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from vss_agents import incident_contract
from vss_agents.api.incident_analyze import register_incident_analyze_routes
from vss_agents.incident_contract import IncidentExtractionError
from vss_agents.tools import incident_experiments
from vss_agents.tools.incident_report_gen import EXPERIMENT_OUTPUT_FIELDS
from vss_agents.tools.incident_report_gen import ExperimentRefusedError
from vss_agents.tools.incident_report_gen import IncidentReportGenOutput
from vss_agents.tools.incident_report_gen import VlmNoAnswerError

VIDEO_URL = "https://r2.example/uploads/cam/clip.mp4?X-Amz-Signature=abc"
OUTPUT = IncidentReportGenOutput(
    report={"incident": {"type": "burglary", "duration": 5}},
    report_text="INCIDENT REPORT",
    report_text_error=None,
    model="nvidia/cosmos-3-nano-reasoner",
    contract_version=incident_contract.contract_version(),
    raw_output="{}",
    revision_id=5,
    evidence=[{"item_id": "E1", "image": None, "image_gap": "no store"}],
)


def _client(*, result=None, side_effect=None, tool_missing=False) -> tuple[TestClient, MagicMock]:
    app = FastAPI()
    builder = MagicMock()
    tool = MagicMock()
    tool.ainvoke = AsyncMock(return_value=result, side_effect=side_effect)
    builder.get_tool = (
        AsyncMock(side_effect=RuntimeError("not configured")) if tool_missing else AsyncMock(return_value=tool)
    )
    register_incident_analyze_routes(app, config=MagicMock(), builder=builder)
    return TestClient(app), tool


def test_passes_the_video_key_to_the_tool_when_given():
    client, tool = _client(result=OUTPUT)
    response = client.post(
        "/api/v1/incidents/v1/analyze", json={"video_url": VIDEO_URL, "video_key": "uploads/clip.mp4"}
    )
    assert response.status_code == 200
    tool.ainvoke.assert_awaited_once_with(
        {"incident_id": "v1", "video_url": VIDEO_URL, "skip_rp1": False, "video_key": "uploads/clip.mp4"}
    )


def test_passes_incident_id_video_url_and_model_run_id_to_the_tool():
    client, tool = _client(result=OUTPUT)
    response = client.post(
        "/api/v1/incidents/v1/analyze", json={"video_url": VIDEO_URL, "model_run_id": "run1", "skip_rp1": True}
    )
    assert response.status_code == 200
    tool.ainvoke.assert_awaited_once_with(
        {"incident_id": "v1", "video_url": VIDEO_URL, "model_run_id": "run1", "skip_rp1": True}
    )
    assert response.json() == {
        k: v
        for k, v in OUTPUT.model_dump().items()
        if k not in EXPERIMENT_OUTPUT_FIELDS and k not in ("tracking", "privacy", "object_tracking")
    }


def test_re_generation_passes_model_and_reviewer_prompt_to_the_tool():
    model = incident_contract.allowed_models()[0]
    client, tool = _client(result=OUTPUT)
    response = client.post(
        "/api/v1/incidents/v1/analyze",
        json={"video_url": VIDEO_URL, "model": model, "reviewer_prompt": "check the door"},
    )
    assert response.status_code == 200
    assert response.json()["revision_id"] == 5
    tool.ainvoke.assert_awaited_once_with(
        {
            "incident_id": "v1",
            "video_url": VIDEO_URL,
            "skip_rp1": False,
            "model": model,
            "reviewer_prompt": "check the door",
        }
    )


def test_model_outside_the_allowlist_is_400_and_never_reaches_the_tool():
    client, tool = _client(result=OUTPUT)
    response = client.post("/api/v1/incidents/v1/analyze", json={"video_url": VIDEO_URL, "model": "not-a-model"})
    assert response.status_code == 400
    tool.ainvoke.assert_not_called()


def test_reviewer_prompt_over_the_limit_is_422():
    client, tool = _client(result=OUTPUT)
    response = client.post("/api/v1/incidents/v1/analyze", json={"video_url": VIDEO_URL, "reviewer_prompt": "x" * 1001})
    assert response.status_code == 422
    tool.ainvoke.assert_not_called()


def test_video_url_is_required():
    client, tool = _client(result=OUTPUT)
    assert client.post("/api/v1/incidents/v1/analyze", json={}).status_code == 422
    tool.ainvoke.assert_not_called()


def test_old_body_fields_are_not_accepted_in_place_of_video_url():
    client, _ = _client(result=OUTPUT)
    response = client.post("/api/v1/incidents/v1/analyze", json={"prompt_override": "x", "reasoning": True})
    assert response.status_code == 422


def test_contract_violation_is_422_with_the_raw_output():
    error = IncidentExtractionError(
        "schema violation: incident/type", raw_output='{"incident": 1}', finish_reason="stop", model="vlm"
    )
    client, _ = _client(side_effect=error)
    response = client.post("/api/v1/incidents/v1/analyze", json={"video_url": VIDEO_URL})
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert "schema violation" in detail["message"]
    assert detail["raw_output"] == '{"incident": 1}'
    assert detail["finish_reason"] == "stop"
    assert detail["model"] == "vlm"
    assert detail["contract_version"] == incident_contract.contract_version()


def test_the_video_model_returning_no_answer_is_502_not_500():
    client, _ = _client(
        side_effect=VlmNoAnswerError("no completion in response body: None. The video model returned no answer")
    )
    response = client.post("/api/v1/incidents/v1/analyze", json={"video_url": VIDEO_URL})
    assert response.status_code == 502
    assert "returned no answer" in response.json()["detail"]


def test_timeout_is_504():
    client, _ = _client(side_effect=TimeoutError("slow"))
    assert client.post("/api/v1/incidents/v1/analyze", json={"video_url": VIDEO_URL}).status_code == 504


def test_other_failure_is_500():
    client, _ = _client(side_effect=RuntimeError("boom"))
    assert client.post("/api/v1/incidents/v1/analyze", json={"video_url": VIDEO_URL}).status_code == 500


def test_missing_tool_is_501():
    client, _ = _client(tool_missing=True)
    assert client.post("/api/v1/incidents/v1/analyze", json={"video_url": VIDEO_URL}).status_code == 501


def test_omitted_model_and_reviewer_prompt_are_not_forwarded():
    client, tool = _client(result=OUTPUT)
    response = client.post("/api/v1/incidents/v1/analyze", json={"video_url": VIDEO_URL})
    assert response.status_code == 200
    tool.ainvoke.assert_awaited_once_with({"incident_id": "v1", "video_url": VIDEO_URL, "skip_rp1": False})


def test_reported_model_is_the_model_the_tool_ran():
    model = incident_contract.allowed_models()[-1]
    client, _ = _client(result=OUTPUT.model_copy(update={"model": model}))
    response = client.post("/api/v1/incidents/v1/analyze", json={"video_url": VIDEO_URL, "model": model})
    assert response.json()["model"] == model


def test_the_callers_video_length_is_forwarded_to_the_tool():
    client, tool = _client(result=OUTPUT)
    response = client.post(
        "/api/v1/incidents/v1/analyze", json={"video_url": VIDEO_URL, "skip_rp1": True, "video_duration_seconds": 12.5}
    )
    assert response.status_code == 200
    tool.ainvoke.assert_awaited_once_with(
        {"incident_id": "v1", "video_url": VIDEO_URL, "skip_rp1": True, "video_duration_seconds": 12.5}
    )


@pytest.mark.parametrize("raw_value", ["0", "-3", "true", '"12"', "[]"])
def test_an_invalid_video_length_is_422_and_never_reaches_the_tool(raw_value):
    client, tool = _client(result=OUTPUT)
    response = client.post(
        "/api/v1/incidents/v1/analyze",
        content=f'{{"video_url": "{VIDEO_URL}", "video_duration_seconds": {raw_value}}}',
        headers={"content-type": "application/json"},
    )
    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["body", "video_duration_seconds"]
    tool.ainvoke.assert_not_awaited()


def test_the_video_bounds_outcome_reaches_the_caller():
    unchecked = OUTPUT.model_copy(update={"video_bounds_unchecked_reason": "video length unknown: HTTP 403"})
    client, _ = _client(result=unchecked)
    body = client.post("/api/v1/incidents/v1/analyze", json={"video_url": VIDEO_URL}).json()
    assert body["video_bounds_checked"] is False
    assert body["video_duration_seconds"] is None
    assert body["video_bounds_unchecked_reason"] == "video length unknown: HTTP 403"


@pytest.mark.parametrize("raw_value", ["NaN", "Infinity", "1e400"])
def test_a_non_finite_video_length_never_reaches_the_tool(raw_value):
    # Python's JSON parser accepts these non-standard literals. Pydantic rejects them, but FastAPI cannot
    # echo a non-finite input back in the 422 body, so the client sees an error status either way.
    client, tool = _client(result=OUTPUT)
    client = TestClient(client.app, raise_server_exceptions=False)
    response = client.post(
        "/api/v1/incidents/v1/analyze",
        content=f'{{"video_url": "{VIDEO_URL}", "video_duration_seconds": {raw_value}}}',
        headers={"content-type": "application/json"},
    )
    assert response.status_code >= 400
    tool.ainvoke.assert_not_awaited()


# --- experimental settings (dev-profile-incident PR 6B) -------------------------------------------

PRODUCTION_KEYS = {
    "report",
    "report_text",
    "report_text_error",
    "model",
    "contract_version",
    "raw_output",
    "finish_reason",
    "p1_attempts",
    "revision_id",
    "evidence",
    "video_duration_seconds",
    "video_duration_source",
    "video_bounds_checked",
    "video_bounds_unchecked_reason",
}
EXPERIMENT = {"prompt_id": "p1-v4-e01-x", "prompt_sha256": "a" * 64, "skip_crops": True, "persisted": False}


def test_a_tracking_summary_is_kept_in_the_response_only_when_the_tool_returned_one():
    summary = {"enabled": True, "matched": 1, "tracks": 3, "elapsed_ms": 9.0, "skipped": None}
    client, _ = _client(result=OUTPUT.model_copy(update={"tracking": summary}))
    response = client.post("/api/v1/incidents/v1/analyze", json={"video_url": VIDEO_URL})
    assert response.json()["tracking"] == summary
    assert set(response.json()) == PRODUCTION_KEYS | {"tracking"}


def test_an_object_tracking_summary_is_kept_in_the_response_only_when_the_tool_returned_one():
    summary = {"enabled": True, "seeds": 2, "tracked": 1, "tracker": "MIL", "items": {}}
    client, _ = _client(result=OUTPUT.model_copy(update={"object_tracking": summary}))
    body = client.post("/api/v1/incidents/v1/analyze", json={"video_url": VIDEO_URL}).json()
    assert body["object_tracking"] == summary
    assert set(body) == PRODUCTION_KEYS | {"object_tracking"}
    client, _ = _client(result=OUTPUT)
    assert "object_tracking" not in client.post("/api/v1/incidents/v1/analyze", json={"video_url": VIDEO_URL}).json()


def test_a_production_response_keeps_exactly_its_documented_keys(monkeypatch):
    monkeypatch.setenv(incident_experiments.EXPERIMENTS_FLAG, "1")  # even with experiments enabled
    client, tool = _client(result=OUTPUT)
    response = client.post("/api/v1/incidents/v1/analyze", json={"video_url": VIDEO_URL})
    assert set(response.json()) == PRODUCTION_KEYS
    assert "experiment_prompt_id" not in tool.ainvoke.await_args.args[0]
    assert "skip_crops" not in tool.ainvoke.await_args.args[0]


@pytest.mark.parametrize(
    "extra", [{"experiment_prompt_id": "p1-v4-e01-x"}, {"skip_crops": True}, {"max_crop_calls": 10}]
)
def test_experimental_fields_are_refused_without_the_flag(monkeypatch, extra):
    monkeypatch.delenv(incident_experiments.EXPERIMENTS_FLAG, raising=False)
    client, tool = _client(result=OUTPUT)
    response = client.post("/api/v1/incidents/v1/analyze", json={"video_url": VIDEO_URL, **extra})
    assert response.status_code == 403
    tool.ainvoke.assert_not_awaited()


def test_an_experimental_response_carries_its_prompt_and_telemetry(monkeypatch):
    monkeypatch.setenv(incident_experiments.EXPERIMENTS_FLAG, "1")
    output = OUTPUT.model_copy(
        update={"experiment": EXPERIMENT, "p1_elapsed_ms": 1234.5, "usage": {"total": 9}, "revision_id": None}
    )
    client, tool = _client(result=output)
    response = client.post(
        "/api/v1/incidents/v1/analyze",
        json={"video_url": VIDEO_URL, "experiment_prompt_id": "p1-v4-e01-x", "skip_crops": True, "skip_rp1": True},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["experiment"] == EXPERIMENT and body["p1_elapsed_ms"] == 1234.5 and body["usage"] == {"total": 9}
    assert tool.ainvoke.await_args.args[0]["experiment_prompt_id"] == "p1-v4-e01-x"
    assert tool.ainvoke.await_args.args[0]["skip_crops"] is True


@pytest.mark.parametrize(
    "error, status",
    [
        (incident_experiments.ExperimentPromptError("unknown"), 400),
        (incident_experiments.ExperimentsDisabledError("off"), 403),
        (ExperimentRefusedError("would upload to R2"), 409),
    ],
)
def test_experiment_errors_map_to_their_status(monkeypatch, error, status):
    monkeypatch.setenv(incident_experiments.EXPERIMENTS_FLAG, "1")
    client, _ = _client(side_effect=error)
    response = client.post("/api/v1/incidents/v1/analyze", json={"video_url": VIDEO_URL, "skip_crops": True})
    assert response.status_code == status


@pytest.mark.parametrize(
    "extra",
    [
        {"experiment_prompt_id": "p1-v4-e01-x"},  # crops on, no cap
        {"skip_crops": True, "max_crop_calls": 10},  # a cap with nothing to cap
    ],
)
def test_experimental_cropping_needs_exactly_a_cap(monkeypatch, extra):
    monkeypatch.setenv(incident_experiments.EXPERIMENTS_FLAG, "1")
    client, tool = _client(result=OUTPUT)
    response = client.post("/api/v1/incidents/v1/analyze", json={"video_url": VIDEO_URL, **extra})
    assert response.status_code == 400
    tool.ainvoke.assert_not_awaited()


@pytest.mark.parametrize("cap", [0, 201, 2.5])
def test_an_out_of_range_crop_call_cap_is_422(monkeypatch, cap):
    monkeypatch.setenv(incident_experiments.EXPERIMENTS_FLAG, "1")
    client, tool = _client(result=OUTPUT)
    response = client.post("/api/v1/incidents/v1/analyze", json={"video_url": VIDEO_URL, "max_crop_calls": cap})
    assert response.status_code == 422
    tool.ainvoke.assert_not_awaited()


def test_a_crop_call_cap_is_forwarded_to_the_tool(monkeypatch):
    monkeypatch.setenv(incident_experiments.EXPERIMENTS_FLAG, "1")
    client, tool = _client(result=OUTPUT.model_copy(update={"experiment": EXPERIMENT, "revision_id": None}))
    response = client.post(
        "/api/v1/incidents/v1/analyze", json={"video_url": VIDEO_URL, "max_crop_calls": 40, "skip_rp1": True}
    )
    assert response.status_code == 200
    assert tool.ainvoke.await_args.args[0]["max_crop_calls"] == 40


def test_an_experiment_cannot_carry_a_reviewer_prompt(monkeypatch):
    monkeypatch.setenv(incident_experiments.EXPERIMENTS_FLAG, "1")
    client, tool = _client(result=OUTPUT)
    response = client.post(
        "/api/v1/incidents/v1/analyze", json={"video_url": VIDEO_URL, "skip_crops": True, "reviewer_prompt": "x"}
    )
    assert response.status_code == 400
    tool.ainvoke.assert_not_awaited()


def test_a_rejected_experimental_output_names_its_prompt_in_the_422(monkeypatch):
    monkeypatch.setenv(incident_experiments.EXPERIMENTS_FLAG, "1")
    error = IncidentExtractionError("bad", raw_output="{bad", finish_reason="stop", model="m")
    error.experiment = EXPERIMENT  # type: ignore[attr-defined]
    error.telemetry = {"p1_elapsed_ms": 10.0, "usage": {}}  # type: ignore[attr-defined]
    client, _ = _client(side_effect=error)
    response = client.post("/api/v1/incidents/v1/analyze", json={"video_url": VIDEO_URL, "skip_crops": True})
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["raw_output"] == "{bad" and detail["experiment"] == EXPERIMENT and detail["p1_elapsed_ms"] == 10.0


def test_a_production_422_keeps_its_documented_detail():
    client, _ = _client(side_effect=IncidentExtractionError("bad", raw_output="{bad", model="m"))
    detail = client.post("/api/v1/incidents/v1/analyze", json={"video_url": VIDEO_URL}).json()["detail"]
    assert set(detail) == {"message", "raw_output", "finish_reason", "model", "p1_attempts", "contract_version"}


def test_blur_faces_is_passed_to_the_tool_only_when_true():
    client, tool = _client(result=OUTPUT)
    client.post("/api/v1/incidents/v1/analyze", json={"video_url": VIDEO_URL, "blur_faces": False})
    tool.ainvoke.assert_awaited_once_with({"incident_id": "v1", "video_url": VIDEO_URL, "skip_rp1": False})
    client, tool = _client(result=OUTPUT)
    client.post("/api/v1/incidents/v1/analyze", json={"video_url": VIDEO_URL, "blur_faces": True})
    tool.ainvoke.assert_awaited_once_with(
        {"incident_id": "v1", "video_url": VIDEO_URL, "skip_rp1": False, "blur_faces": True}
    )


def test_a_privacy_summary_is_kept_in_the_response_only_when_the_tool_returned_one():
    summary = {"blur_faces": True, "regions": 2, "frames": 2, "heads": {}, "head_queries": 0, "failed": 0}
    client, _ = _client(result=OUTPUT.model_copy(update={"privacy": summary}))
    body = client.post("/api/v1/incidents/v1/analyze", json={"video_url": VIDEO_URL, "blur_faces": True}).json()
    assert body["privacy"] == summary
    assert set(body) == PRODUCTION_KEYS | {"privacy"}
    client, _ = _client(result=OUTPUT)
    assert "privacy" not in client.post("/api/v1/incidents/v1/analyze", json={"video_url": VIDEO_URL}).json()
