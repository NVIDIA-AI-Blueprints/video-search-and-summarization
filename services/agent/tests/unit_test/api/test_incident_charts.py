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

"""Unit tests for ``POST /api/v1/incidents/charts``."""

from __future__ import annotations

from unittest.mock import AsyncMock
from unittest.mock import MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage
from langchain_core.messages import HumanMessage
from langchain_core.messages import SystemMessage

from vss_agents.api.incident_charts import register_incident_charts_routes
from vss_agents.tools.chart_generator import ChartGenExecOutput

BODY = {
    "charts_data": [
        {
            "chart_file_format": "png",
            "title": "Reports by incident type",
            "x_categories": ["Assault", "Burglary"],
            "series": {"Official": [1, 0], "Not official": [4, 2]},
            "x_label": "Incident type",
            "y_label": "Reports",
        }
    ],
    "output_dir": "dashboard-charts",
    "file_prefix": "abc_",
}
OUTPUT = [ChartGenExecOutput(success=True, error_message=None, object_store_key="dashboard-charts/abc_0.png")]


def _client(tool=None, *, configured=True, llm=None, chart_llm_name="openai_llm") -> TestClient:
    builder = MagicMock()
    builder.get_tool = AsyncMock(return_value=tool) if configured else AsyncMock(side_effect=ValueError("no such tool"))
    builder.get_llm = (
        AsyncMock(return_value=llm) if llm is not None else AsyncMock(side_effect=ValueError("no such llm"))
    )
    app = FastAPI()
    register_incident_charts_routes(app, builder, chart_llm_name)
    return TestClient(app)


def _llm(content=None, *, error: Exception | None = None) -> MagicMock:
    llm = MagicMock()
    llm.ainvoke = AsyncMock(side_effect=error) if error else AsyncMock(return_value=AIMessage(content=content))
    llm.bind.return_value = llm  # the route invokes the bound model; here it is the same mock
    return llm


CHAT_BODY = {
    "messages": [
        {"role": "system", "content": "You classify one request."},
        {"role": "user", "content": "severity breakdown"},
    ]
}


def test_passes_the_figures_to_the_tool_and_returns_the_image_key():
    tool = MagicMock()
    tool.ainvoke = AsyncMock(return_value=OUTPUT)
    response = _client(tool).post("/api/v1/incidents/charts", json=BODY)
    assert response.status_code == 200
    assert response.json() == [
        {"success": True, "error_message": None, "object_store_key": "dashboard-charts/abc_0.png"}
    ]
    sent = tool.ainvoke.await_args.args[0]
    assert sent["charts_data"][0]["series"] == {"Official": [1.0, 0.0], "Not official": [4.0, 2.0]}
    assert sent["output_dir"] == "dashboard-charts"
    assert sent["file_prefix"] == "abc_"


def test_draws_a_pie():
    tool = MagicMock()
    tool.ainvoke = AsyncMock(return_value=OUTPUT)
    pie = {
        "chart_file_format": "png",
        "title": "Reports by severity",
        "sizes": [3, 1],
        "labels": ["Low (3)", "High (1)"],
    }
    response = _client(tool).post("/api/v1/incidents/charts", json={**BODY, "charts_data": [pie]})
    assert response.status_code == 200
    assert tool.ainvoke.await_args.args[0]["charts_data"][0]["labels"] == ["Low (3)", "High (1)"]


def test_a_request_without_figures_is_refused_before_the_tool():
    tool = MagicMock()
    tool.ainvoke = AsyncMock(return_value=OUTPUT)
    response = _client(tool).post("/api/v1/incidents/charts", json={"charts_data": [{"title": "no figures"}]})
    assert response.status_code == 422
    tool.ainvoke.assert_not_awaited()


def test_a_profile_without_the_tool_gets_501():
    response = _client(configured=False).post("/api/v1/incidents/charts", json=BODY)
    assert response.status_code == 501


def test_a_tool_failure_is_a_plain_500():
    tool = MagicMock()
    tool.ainvoke = AsyncMock(side_effect=RuntimeError("object store path /secret failed"))
    response = _client(tool).post("/api/v1/incidents/charts", json=BODY)
    assert response.status_code == 500
    assert "secret" not in response.text


def test_the_chart_request_model_reads_the_system_and_the_one_user_message():
    llm = _llm('{"metric": "incidents", "dimension": "severity"}')
    response = _client(llm=llm).post("/api/v1/incidents/chart-requests", json=CHAT_BODY)
    assert response.status_code == 200
    assert response.json() == {"reply": {"metric": "incidents", "dimension": "severity"}}
    sent = llm.ainvoke.await_args.args[0]
    assert isinstance(sent[0], SystemMessage) and sent[0].content == "You classify one request."
    assert isinstance(sent[1], HumanMessage) and sent[1].content == "severity breakdown"
    assert len(sent) == 2


def test_the_chart_request_caps_the_reply_at_200_tokens():
    llm = _llm('{"unsupported": true}')
    assert _client(llm=llm).post("/api/v1/incidents/chart-requests", json=CHAT_BODY).status_code == 200
    llm.bind.assert_called_once_with(max_tokens=200)


def test_the_chart_request_reads_the_object_from_a_fence_and_gives_null_for_anything_else():
    client = _client(llm=_llm('```json\n{"unsupported": true}\n```'))
    assert client.post("/api/v1/incidents/chart-requests", json=CHAT_BODY).json() == {"reply": {"unsupported": True}}
    for content in ("Sure! Assault had 999 incidents.", "{not json}", '["a", "list"]', ""):
        client = _client(llm=_llm(content))
        assert client.post("/api/v1/incidents/chart-requests", json=CHAT_BODY).json() == {"reply": None}


def test_the_chart_request_is_refused_when_no_llm_is_named_or_found():
    assert (
        _client(llm=_llm("{}"), chart_llm_name=None)
        .post("/api/v1/incidents/chart-requests", json=CHAT_BODY)
        .status_code
        == 501
    )
    assert _client(llm=None).post("/api/v1/incidents/chart-requests", json=CHAT_BODY).status_code == 501


def test_a_model_failure_is_a_plain_502_that_echoes_nothing():
    llm = _llm(error=RuntimeError("upstream secret detail, request severity breakdown"))
    response = _client(llm=llm).post("/api/v1/incidents/chart-requests", json=CHAT_BODY)
    assert response.status_code == 502
    assert "secret" not in response.text
    assert "severity breakdown" not in response.text


def _with_user(text: str) -> dict:
    return {"messages": [CHAT_BODY["messages"][0], {"role": "user", "content": text}]}


def test_the_chart_request_validates_its_body_before_the_model():
    llm = _llm("{}")
    client = _client(llm=llm)
    post = lambda body: client.post("/api/v1/incidents/chart-requests", json=body).status_code  # noqa: E731
    assert post(_with_user("")) == 422
    assert post(_with_user("x" * 301)) == 422
    assert post({"messages": [CHAT_BODY["messages"][1], CHAT_BODY["messages"][0]]}) == 422  # wrong order
    assert post({"messages": [CHAT_BODY["messages"][0]]}) == 422  # one message only
    assert post({"messages": [*CHAT_BODY["messages"], {"role": "user", "content": "again"}]}) == 422
    assert post({"messages": [CHAT_BODY["messages"][0], {"role": "assistant", "content": "x"}]}) == 422
    assert post({**CHAT_BODY, "history": []}) == 422
    assert post({**CHAT_BODY, "system_prompt": "x"}) == 422  # the old shape is refused
    llm.ainvoke.assert_not_awaited()
