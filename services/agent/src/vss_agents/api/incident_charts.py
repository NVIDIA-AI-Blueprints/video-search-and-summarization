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

"""Dashboard chart routes: ``POST /api/v1/incidents/chart-requests`` and ``POST /api/v1/incidents/charts``.

``chart-requests`` is the one model call a dashboard chart needs. The console sends the two messages the
model gets (its classification prompt and the user's one request); the reply comes back as the JSON object
the model wrote, or null.
The agent never validates that object: the console checks it against its allowlist (``parseChartSpec``)
and refuses anything else, so the allowlist lives only in the console. The model never sees a figure.

``charts`` draws the figures it is given with ``chart_generator``. The console reads its figures from
the database and sends them here, so nothing in this route chooses or computes a number. The image goes
to the agent's object store and the response names its key, which ``GET /static/{key}`` serves.

The tool is called directly because NAT's generic ``front_end.endpoints`` route cannot serve it:
that route checks the result with ``isinstance(result, list[ChartGenExecOutput])``, which Python
refuses, after the tool has already drawn.

Status codes: 200 with the answer, 422 for a request that does not validate, 501 when the profile does
not configure the tool or the chart-request LLM, 502 when the model call fails, 500 when drawing fails.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING
from typing import Any
from typing import Literal

from fastapi import APIRouter
from fastapi import FastAPI
from fastapi import HTTPException
from langchain_core.messages import HumanMessage
from langchain_core.messages import SystemMessage
from nat.builder.framework_enum import LLMFrameworkEnum
from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import model_validator

from vss_agents.tools.chart_generator import ChartGeneratorInput
from vss_agents.tools.chart_generator import ChartGenExecOutput

if TYPE_CHECKING:
    from nat.builder.workflow_builder import WorkflowBuilder

logger = logging.getLogger(__name__)

# Per-call timeouts, not one budget for the whole chart. This model call gives up at 40 s, so its 502 can
# reach the console, which gives its fetch to this route 45 s. The console's chart draw calls each give up at
# 30 s (chart-renderer.ts), and a split bar chart makes up to three of them.
CHART_REQUEST_TIMEOUT_SECONDS = 40.0
# The reply is one small JSON object, so the model is capped well below the LLM entry's own max_tokens.
CHART_REPLY_MAX_TOKENS = 200


class ChartMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["system", "user"]
    content: str = Field(min_length=1, max_length=20_000)


class ChartRequestInput(BaseModel):
    """One dashboard chart request: the console's allowlist prompt (system) and the user's text (user), nothing else."""

    model_config = ConfigDict(extra="forbid")

    messages: list[ChartMessage] = Field(min_length=2, max_length=2)

    @model_validator(mode="after")
    def _system_then_one_request(self) -> ChartRequestInput:
        if [message.role for message in self.messages] != ["system", "user"]:
            raise ValueError("messages must be one system message, then one user message")
        if len(self.messages[1].content) > 300:
            raise ValueError("the user message is over 300 characters")
        return self


class ChartRequestOutput(BaseModel):
    """The JSON object the model wrote, or null when it wrote none. Unvalidated: the console decides what counts."""

    reply: dict[str, Any] | None


def json_object_in(content: object) -> dict[str, Any] | None:
    """The JSON object in a model reply, tolerating a code fence or surrounding text; null when there is none."""
    if not isinstance(content, str):
        return None
    start, end = content.find("{"), content.rfind("}")
    if start < 0 or end < start:
        return None
    try:
        value = json.loads(content[start : end + 1])
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def create_incident_charts_router(builder: WorkflowBuilder, chart_llm_name: str | None = None) -> APIRouter:
    """Build the dashboard chart routers. ``chart_llm_name`` is an ``llms:`` entry from the agent config."""
    router = APIRouter()

    @router.post("/api/v1/incidents/chart-requests", response_model=ChartRequestOutput)
    async def read_chart_request(body: ChartRequestInput) -> ChartRequestOutput:
        if not chart_llm_name:
            raise HTTPException(status_code=501, detail="chart requests are not configured on this profile")
        try:
            llm = await builder.get_llm(chart_llm_name, wrapper_type=LLMFrameworkEnum.LANGCHAIN)
        except Exception as exc:
            logger.error("chart request LLM %s is not configured: %s", chart_llm_name, exc, exc_info=True)
            raise HTTPException(status_code=501, detail="chart requests are not configured on this profile") from exc
        try:
            system, user = body.messages
            bounded = llm.bind(max_tokens=CHART_REPLY_MAX_TOKENS)
            message = await asyncio.wait_for(
                bounded.ainvoke([SystemMessage(content=system.content), HumanMessage(content=user.content)]),
                timeout=CHART_REQUEST_TIMEOUT_SECONDS,
            )
        except Exception as exc:
            # Log the exception type only: the request text must not reach the log.
            logger.error("chart request model call failed: %s", type(exc).__name__)
            raise HTTPException(status_code=502, detail="The model did not answer") from exc
        return ChartRequestOutput(reply=json_object_in(getattr(message, "content", None)))

    @router.post("/api/v1/incidents/charts", response_model=list[ChartGenExecOutput])
    async def draw_charts(body: ChartGeneratorInput) -> list[ChartGenExecOutput]:
        try:
            tool = await builder.get_tool("chart_generator", wrapper_type=LLMFrameworkEnum.LANGCHAIN)
        except Exception as exc:
            logger.error("chart_generator tool is not configured: %s", exc, exc_info=True)
            raise HTTPException(
                status_code=501, detail="chart_generator tool is not configured on this profile"
            ) from exc
        try:
            result: list[ChartGenExecOutput] = await tool.ainvoke(body.model_dump())
        except Exception as exc:
            logger.error("chart_generator failed: %s", exc, exc_info=True)
            raise HTTPException(status_code=500, detail="The chart could not be drawn") from exc
        return result

    return router


def register_incident_charts_routes(app: FastAPI, builder: WorkflowBuilder, chart_llm_name: str | None = None) -> None:
    """Register the chart routes.

    The routes are always mounted. Profiles that don't configure the ``chart_generator`` tool or its
    ``llm_name`` get a 501 at request time (both are resolved per request), so unconditional
    registration is safe.
    """
    try:
        app.include_router(create_incident_charts_router(builder, chart_llm_name))
        logger.info("Registered POST /api/v1/incidents/chart-requests and POST /api/v1/incidents/charts")
    except Exception as exc:
        logger.error("Failed to register incident charts route: %s", exc, exc_info=True)
        raise
