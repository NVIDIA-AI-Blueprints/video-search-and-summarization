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

"""Experiment profiles (P4): a zoom-call cap, object tracking and the pre-zoom P1 report in experimental requests."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import patch

import httpx
import pytest

from vss_agents.tools.incident_report_gen import IncidentReportGenInput
from vss_agents.tools.incident_report_gen import experiment_settings
from vss_agents.tools.incident_report_gen import incident_report_gen
from vss_agents.utils import crop_evidence
from vss_agents.utils import run_provenance

from .test_incident_report_gen import MODEL_OUTPUT
from .test_incident_report_gen import VIDEO_URL
from .test_incident_report_gen import _config


class _Reply:
    def __init__(self) -> None:
        self.status_code = 200
        self.headers: dict[str, str] = {}

    def json(self) -> dict:
        content = {"name": "bottle", "description": "a bottle", "confidence": 0.9, "changed": False}
        return {"choices": [{"message": {"content": json.dumps(content)}}], "usage": {"total_tokens": 5}}

    def raise_for_status(self) -> None:
        return None


def _detector() -> crop_evidence.DefaultDetector:
    return crop_evidence.DefaultDetector(model_name="grounder", base_url="http://golden.invalid", api_key="k")


def test_a_zoom_cap_refuses_attempts_beyond_it_and_counts_them(monkeypatch):
    monkeypatch.setattr(crop_evidence.httpx, "post", lambda *_a, **_k: _Reply())
    capped = crop_evidence.with_zoom_budget(_detector(), crop_evidence.CropCallBudget(2))
    with run_provenance.collecting() as provenance:
        with pytest.raises(crop_evidence.CropCallCapReachedError):
            capped.zoom_name(b"close", item_type="asset", name="bottle", description="a bottle", context_jpeg=b"f")
    zoom = provenance.to_dict(contract_version="v", flags={})["stages"]["zoom"]
    assert zoom["calls"] == 2 and zoom["refused"] == 1, "the third call (the merge) was refused before sending"
    assert capped.zoom_budget.summary()["cap_reached"] is True
    uncapped = _detector()
    assert uncapped.zoom_budget is None, "the shared detector is never capped"
    other = object()
    assert crop_evidence.with_zoom_budget(other, crop_evidence.CropCallBudget(1)) is other


def _input(**fields) -> IncidentReportGenInput:
    return IncidentReportGenInput(
        incident_id="v1", video_url=VIDEO_URL, video_duration_seconds=30, skip_rp1=True, **fields
    )


def test_the_new_settings_make_a_request_experimental_and_need_cropping(monkeypatch):
    monkeypatch.setenv("INCIDENT_EXPERIMENTS_ENABLED", "1")
    settings = experiment_settings(_input(max_crop_calls=10, max_zoom_calls=4, experiment_object_tracking=True), None)
    assert settings is not None and settings["max_zoom_calls"] == 4 and settings["object_tracking"] is True
    with pytest.raises(ValueError, match="need cropping"):
        experiment_settings(_input(skip_crops=True, max_zoom_calls=4), None)
    monkeypatch.delenv("INCIDENT_EXPERIMENTS_ENABLED")
    with pytest.raises(Exception, match="INCIDENT_EXPERIMENTS_ENABLED"):
        experiment_settings(_input(max_crop_calls=10, max_zoom_calls=4), None)


@pytest.mark.asyncio
async def test_an_experimental_crop_run_caps_zoom_tracks_objects_and_returns_the_p1_report(monkeypatch):
    monkeypatch.setenv("INCIDENT_EXPERIMENTS_ENABLED", "1")
    for name in ("R2_BUCKET", "R2_ENDPOINT_URL", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY"):
        monkeypatch.delenv(name, raising=False)
    transport = httpx.MockTransport(
        lambda _r: httpx.Response(
            200, json={"choices": [{"message": {"content": json.dumps(MODEL_OUTPUT)}, "finish_reason": "stop"}]}
        )
    )
    real_client = httpx.AsyncClient

    def client_factory(*args, **kwargs):
        kwargs["transport"] = transport
        return real_client(*args, **kwargs)

    service = SimpleNamespace(detector=_detector(), namer=_detector())
    with (
        patch("vss_agents.tools.incident_report_gen.httpx.AsyncClient", side_effect=client_factory),
        patch(
            "vss_agents.tools.incident_report_gen.collect_evidence", new_callable=AsyncMock, return_value=[]
        ) as collect,
        patch("vss_agents.tools.incident_report_gen.is_r2_configured", return_value=False),
        patch.object(crop_evidence, "get_crop_evidence_service", return_value=service),
    ):
        async with incident_report_gen(_config(), MagicMock()) as function_info:
            result = await function_info.single_fn(
                _input(max_crop_calls=10, max_zoom_calls=3, experiment_object_tracking=True)
            )
    kwargs = collect.await_args.kwargs
    assert kwargs["namer"] is not service.namer and kwargs["namer"].zoom_budget.limit == 3
    assert kwargs["object_tracking"] is not None and kwargs["detector"].call_budget.limit == 10
    experiment = result.experiment
    assert experiment["p1_report"] == result.report, "no zoom ran (collect is faked), so the reports are equal"
    assert experiment["zoom"]["cap"] == 3
    # The setting stays a bool; the tracker's summary is the response's own field, as in production.
    assert experiment["object_tracking"] is True and result.object_tracking is not None
    assert result.provenance["flags"]["object_tracking"] is True and result.provenance["flags"]["experiment"] is True
    assert experiment["persisted"] is False and result.revision_id is None
