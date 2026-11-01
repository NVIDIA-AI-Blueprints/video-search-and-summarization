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

"""``POST /api/v1/incidents/{incident_id}/analyze`` - the incident-console AI trigger.

``incident-console-v2`` (agent mode) calls this route with the console's ``videos.id`` as
``incident_id`` and a signed R2 URL for the uploaded video. The route runs the
``incident_report_gen`` tool - the shared incident contract's P1 -> RP1 calls - and returns
the validated contract report plus the ``report_revisions`` id of the AI revision the agent
inserted (``revision_id``). The video-bounds rules apply with the caller's
``video_duration_seconds`` or, without one, the length the agent reads from the MP4 header; the
response's ``video_bounds_checked`` and ``video_bounds_unchecked_reason`` say whether they did.
Re-generation is the same call with an optional ``model`` and a
human-typed ``reviewer_prompt``; it starts a new AI revision and copies nothing from earlier ones.
The agent needs no VST lookup: the console owns the ``videos`` row and signs the URL.

Status codes: 422 for an invalid body (including a ``video_duration_seconds`` that is not a
finite number above 0) or when the model output fails ``core-scored-v1`` (nothing is persisted; the
detail carries ``message``, ``raw_output``, ``finish_reason``, ``model``, ``p1_attempts`` and ``contract_version``
so the caller can record the attempt), 400 for a ``model`` outside the contract allowlist,
504 on a P1 timeout, 502 when the video model answered HTTP 200 with no completion (a video it
cannot decode), 501 when the profile does not configure ``incident_report_gen``, 500 for anything else.

Experimental settings (dev-profile-incident prompt experiments): ``experiment_prompt_id``,
``skip_crops`` and ``max_crop_calls`` are refused with 403 unless the agent runs with
``INCIDENT_EXPERIMENTS_ENABLED=1``; an unknown or edited registry prompt is 400, combining one with
``reviewer_prompt`` is 400, an experimental crops-on request without ``max_crop_calls`` (or one with
``skip_crops``) is 400, and cropping that would upload to R2 is 409. ``max_crop_calls`` caps the
request's grounding HTTP attempts, retries included; an item beyond it keeps an ``image_gap``.
An experimental request never writes a report revision, and its response adds ``experiment``
(prompt id and SHA-256 used, and for a crops-on request ``crop``: wall time, calls against the cap,
tokens, images and gaps), ``p1_elapsed_ms`` and ``usage``.
A request without them gets exactly the production response.
With ``INCIDENT_TRACKING_ENABLED`` on, a production response adds ``tracking`` (``{enabled, matched, tracks,
elapsed_ms, skipped}``); with it off the key is absent.
With ``INCIDENT_OBJECT_TRACKING_ENABLED`` on, a production response adds ``object_tracking`` (``{enabled, seeds,
tracked, tracker, elapsed_ms, time_cap_reached, skipped, detector, items}``) for the instrument and asset tracks, and
those evidence entries gain ``track_source``; with it off both are absent.
``blur_faces: true`` pixelates the heads of the people in every stored evidence frame and crop and adds
``privacy`` (``{blur_faces, regions, frames, heads, head_queries, failed}``) to the response, and each evidence
entry gains ``blur_regions``; without it the key is absent and the evidence is unchanged.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING
from typing import Any

from fastapi import APIRouter
from fastapi import FastAPI
from fastapi import HTTPException
from nat.builder.framework_enum import LLMFrameworkEnum
from pydantic import BaseModel
from pydantic import Field

from vss_agents.incident_contract import IncidentExtractionError
from vss_agents.incident_contract import allowed_models
from vss_agents.incident_contract import contract_version
from vss_agents.tools import incident_experiments
from vss_agents.tools.incident_report_gen import EXPERIMENT_OUTPUT_FIELDS
from vss_agents.tools.incident_report_gen import MAX_CROP_CALLS_LIMIT
from vss_agents.tools.incident_report_gen import MAX_REVIEWER_PROMPT_LENGTH
from vss_agents.tools.incident_report_gen import ExperimentRefusedError
from vss_agents.tools.incident_report_gen import VlmNoAnswerError

if TYPE_CHECKING:
    from nat.builder.workflow_builder import WorkflowBuilder

logger = logging.getLogger(__name__)


class AnalyzeIncidentRequest(BaseModel):
    """Body for ``POST /api/v1/incidents/{incident_id}/analyze``."""

    video_url: str = Field(..., min_length=1, description="Signed R2 URL of the uploaded video.")
    video_key: str | None = Field(
        default=None,
        min_length=1,
        max_length=900,
        description=(
            "The video's R2 key (incident-console-v2 sends it); evidence crops are stored under "
            "evidence/<video_key>/run-<n>/. Without it the crops use the incident id as folder name."
        ),
    )
    model_run_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=20,
        description=(
            "The caller's model_runs.id; when given, the caller owns the run's notes and the agent only "
            "makes sure the row exists so the AI revision can reference it."
        ),
    )
    model: str | None = Field(
        default=None, min_length=1, description="Model id from the contract allowlist; defaults to the profile's."
    )
    reviewer_prompt: str | None = Field(
        default=None,
        max_length=MAX_REVIEWER_PROMPT_LENGTH,
        description="Extra reviewer text typed by a human, added to the report prompt for this run only.",
    )
    skip_rp1: bool = Field(
        default=False, description="Run P1 only (incident-console-v2 agent mode); report_text stays null."
    )
    video_duration_seconds: float | None = Field(
        default=None,
        gt=0,
        allow_inf_nan=False,
        strict=True,
        description=(
            "Video length in seconds as the caller read it (incident-console-v2 reads the MP4 header in R2). "
            "When absent the agent reads the header from video_url itself."
        ),
    )
    experiment_prompt_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=80,
        description="Experimental (INCIDENT_EXPERIMENTS_ENABLED=1 only): a prompt id from the experiment registry.",
    )
    skip_crops: bool = Field(
        default=False, description="Experimental (INCIDENT_EXPERIMENTS_ENABLED=1 only): skip evidence cropping."
    )
    max_crop_calls: int | None = Field(
        default=None,
        ge=1,
        le=MAX_CROP_CALLS_LIMIT,
        description=(
            "Experimental (INCIDENT_EXPERIMENTS_ENABLED=1 only): the crop-call cap of a crops-on request "
            "(grounding HTTP attempts, retries included). Required when an experimental request crops."
        ),
    )

    blur_faces: bool = Field(
        default=False,
        description=(
            "Pixelate the heads of the people in every stored evidence frame and crop (person tracks, else one "
            "detector call for a person item's head). Model calls still see the unblurred images."
        ),
    )

    def is_experimental(self) -> bool:
        return self.experiment_prompt_id is not None or self.skip_crops or self.max_crop_calls is not None


def create_incident_analyze_router(config: Any, builder: WorkflowBuilder) -> APIRouter:  # noqa: ARG001
    """Build the ``POST /api/v1/incidents/{incident_id}/analyze`` router.

    ``builder`` is captured once at router-build time and used to resolve the
    ``incident_report_gen`` tool per request.
    """
    router = APIRouter()

    @router.post(
        "/api/v1/incidents/{incident_id}/analyze",
        summary="Extract a contract incident report from an uploaded video (P1) and write a prose report (RP1)",
        tags=["Incident Analyze"],
    )
    async def analyze_incident(incident_id: str, body: AnalyzeIncidentRequest) -> dict[str, Any]:
        if body.is_experimental() and not incident_experiments.experiments_enabled():
            raise HTTPException(
                status_code=403,
                detail=f"experimental settings are disabled on this agent ({incident_experiments.EXPERIMENTS_FLAG}=1)",
            )
        if body.is_experimental() and body.reviewer_prompt:
            raise HTTPException(status_code=400, detail="an experimental request cannot carry a reviewer_prompt")
        if body.skip_crops and body.max_crop_calls is not None:
            raise HTTPException(status_code=400, detail="max_crop_calls cannot be combined with skip_crops")
        if body.is_experimental() and not body.skip_crops and body.max_crop_calls is None:
            raise HTTPException(
                status_code=400, detail="an experimental request with cropping on needs max_crop_calls (its cap)"
            )
        if body.model and body.model not in allowed_models():
            raise HTTPException(
                status_code=400, detail=f"Model {body.model!r} is not in the incident contract allowlist"
            )

        try:
            incident_report_gen_tool = await builder.get_tool(
                "incident_report_gen", wrapper_type=LLMFrameworkEnum.LANGCHAIN
            )
        except Exception as exc:
            logger.error("incident_report_gen tool is not configured: %s", exc, exc_info=True)
            raise HTTPException(
                status_code=501, detail="incident_report_gen tool is not configured on this profile"
            ) from exc

        tool_input: dict[str, Any] = {
            "incident_id": incident_id,
            "video_url": body.video_url,
            "skip_rp1": body.skip_rp1,
        }
        if body.video_key:
            tool_input["video_key"] = body.video_key
        if body.model_run_id:
            tool_input["model_run_id"] = body.model_run_id
        if body.model:
            tool_input["model"] = body.model
        if body.reviewer_prompt:
            tool_input["reviewer_prompt"] = body.reviewer_prompt
        if body.video_duration_seconds is not None:
            tool_input["video_duration_seconds"] = body.video_duration_seconds
        if body.experiment_prompt_id is not None:
            tool_input["experiment_prompt_id"] = body.experiment_prompt_id
        if body.skip_crops:
            tool_input["skip_crops"] = True
        if body.max_crop_calls is not None:
            tool_input["max_crop_calls"] = body.max_crop_calls
        if body.blur_faces:
            tool_input["blur_faces"] = True

        try:
            result = await incident_report_gen_tool.ainvoke(tool_input)
        except IncidentExtractionError as exc:
            logger.error("/analyze contract violation for incident_id=%s: %s", incident_id, exc)
            detail: dict[str, Any] = {
                "message": f"Incident contract violation: {exc}",
                "raw_output": exc.raw_output,
                "finish_reason": exc.finish_reason,
                "model": exc.model,
                "p1_attempts": getattr(exc, "attempts", []),
                "contract_version": contract_version(),
            }
            if getattr(exc, "experiment", None) is not None:
                telemetry = getattr(exc, "telemetry", None) or {}
                detail.update(
                    experiment=exc.experiment,  # type: ignore[attr-defined]
                    p1_elapsed_ms=telemetry.get("p1_elapsed_ms"),
                    usage=telemetry.get("usage"),
                )
            raise HTTPException(status_code=422, detail=detail) from exc
        except incident_experiments.ExperimentsDisabledError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except incident_experiments.ExperimentPromptError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except ExperimentRefusedError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except VlmNoAnswerError as exc:
            logger.error("/analyze: the video model returned no answer for incident_id=%s: %s", incident_id, exc)
            raise HTTPException(status_code=502, detail=f"Video model returned no answer: {exc}") from exc
        except TimeoutError as exc:
            logger.error("/analyze timed out for incident_id=%s: %s", incident_id, exc)
            raise HTTPException(status_code=504, detail=f"Incident analysis timed out: {exc}") from exc
        except Exception as exc:
            logger.error("/analyze failed for incident_id=%s: %s", incident_id, exc, exc_info=True)
            raise HTTPException(status_code=500, detail=f"Analysis failed: {exc}") from exc
        response = result.model_dump() if hasattr(result, "model_dump") else dict(result)
        if not body.is_experimental():
            for key in EXPERIMENT_OUTPUT_FIELDS:  # production responses keep exactly their documented shape
                response.pop(key, None)
        # Tracking off or no blur asked (the defaults): the response is exactly as it was.
        for key in ("tracking", "privacy", "object_tracking"):
            if response.get(key) is None:
                response.pop(key, None)
        return response

    return router


def register_incident_analyze_routes(app: FastAPI, config: Any, builder: WorkflowBuilder) -> None:
    """Register ``POST /api/v1/incidents/{incident_id}/analyze``.

    The route is always mounted. Profiles that don't configure the
    ``incident_report_gen`` tool get a 501 at request time (the tool is
    resolved per request), so unconditional registration is safe.
    """
    try:
        app.include_router(create_incident_analyze_router(config, builder))
        logger.info("Registered POST /api/v1/incidents/{incident_id}/analyze")
    except Exception as exc:
        logger.error("Failed to register incident analyze route: %s", exc, exc_info=True)
        raise
