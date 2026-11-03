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

"""``POST /api/v1/incidents/{video_id}/box/describe`` - what is the box a reviewer drew?

``incident-console-v2`` sends a second and a box (``[ymin, xmin, ymax, xmax]``, 0-1000). The agent reads that frame
from the video's stored file (``videos.filepath`` is an R2 key), cuts the box with a little margin, enlarges it and
outlines the box, and asks the VLM what the boxed object is (``zoom/describe-box``: the fast model first, the
analysis's own model when it fails or cannot tell). The whole frame is not sent. The answer is a proposal the
reviewer edits: ``item_type``, ``name``, ``description``, ``holder`` (instruments), ``threat_level``
(instruments) and ``confidence``; or ``proposal`` null with a plain ``reason`` when the model cannot tell.
Nothing is stored.

Status codes: 422 for a bad body or a second with no frame, 404 for an unknown video, 502 when the model fails or
answers something that is not the JSON asked for, 503 when the incident database or the VLM key is not configured.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from typing import Literal

from fastapi import APIRouter
from fastapi import FastAPI
from fastapi import HTTPException
import httpx
from pydantic import BaseModel
from pydantic import Field
from pydantic import model_validator

from vss_agents.utils import crop_evidence
from vss_agents.utils import incident_db
from vss_agents.utils.r2 import resolve_r2_url

logger = logging.getLogger(__name__)


class BoxDescribeRequest(BaseModel):
    """Body for ``POST /api/v1/incidents/{video_id}/box/describe``."""

    seconds: float = Field(..., ge=0, allow_inf_nan=False, description="Video second of the frame the box is on.")
    box: list[float] = Field(
        ..., min_length=4, max_length=4, description="[ymin, xmin, ymax, xmax], normalised 0-1000."
    )

    @model_validator(mode="after")
    def _box_is_inside_the_frame(self) -> BoxDescribeRequest:
        ymin, xmin, ymax, xmax = self.box
        if not all(0 <= v <= 1000 for v in self.box) or ymin >= ymax or xmin >= xmax:
            raise ValueError("box must be [ymin, xmin, ymax, xmax] within 0-1000 with min below max")
        return self


class BoxProposalBody(BaseModel):
    """The model's proposal for the box; the reviewer edits it before it becomes an item."""

    item_type: Literal["entity", "instrument", "asset"]
    name: str
    description: str
    holder: str | None
    threat_level: int | None
    confidence: float


class BoxDescribeResponse(BaseModel):
    """``proposal`` is null, with a ``reason``, when the model cannot identify the box."""

    proposal: BoxProposalBody | None
    reason: str | None


def create_incident_box_describe_router(detector: Any | None = None) -> APIRouter:
    """Build the ``POST /api/v1/incidents/{video_id}/box/describe`` router.

    ``detector`` is the VLM client (``describe_box``); by default the crop service's own, so the model, endpoint and
    key are the analysis's.
    """
    router = APIRouter()

    @router.post(
        "/api/v1/incidents/{video_id}/box/describe",
        summary="Name and describe the box a reviewer drew on a frame (one VLM call; nothing is stored)",
        tags=["Incident Evidence"],
    )
    async def describe_box(video_id: str, body: BoxDescribeRequest) -> BoxDescribeResponse:
        active = detector if detector is not None else crop_evidence.get_crop_evidence_service().detector
        if not active.is_available():
            raise HTTPException(status_code=503, detail="No VLM key is configured for the agent")
        db = await incident_db.get_db()
        if db is None:
            raise HTTPException(status_code=503, detail="Incident database is not configured or unreachable")
        video = await db.get_video(video_id)
        if video is None or not video.get("filepath"):
            raise HTTPException(status_code=404, detail=f"Video {video_id} not found")
        video_ref = resolve_r2_url(str(video["filepath"]))

        def _look() -> crop_evidence.BoxProposal:
            frame = crop_evidence.extract_frame(video_ref, body.seconds)
            if frame is None or frame.size == 0:
                raise LookupError(f"no frame at {body.seconds} s")
            close_up = crop_evidence.describe_box_close_up(frame, body.box)
            if close_up is None:
                raise LookupError("the box is empty at that frame size")
            return active.describe_box(close_up)

        try:
            result = await asyncio.to_thread(_look)
        except LookupError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except (httpx.HTTPError, ValueError) as exc:
            logger.error("box/describe failed for video_id=%s: %s", video_id, exc, exc_info=True)
            if isinstance(exc, httpx.TimeoutException):
                detail = "The video model did not answer in time, even after a second try. Try again in a moment."
            else:
                detail = f"The video model failed: {exc}"
            raise HTTPException(status_code=502, detail=detail) from exc
        if result.item_type is None:
            return BoxDescribeResponse(proposal=None, reason=result.reason)
        return BoxDescribeResponse(
            proposal=BoxProposalBody(
                item_type=result.item_type,  # type: ignore[arg-type]
                name=result.name or "",
                description=result.description or "",
                holder=result.holder,
                threat_level=result.threat_level,
                confidence=result.confidence or 0.0,
            ),
            reason=None,
        )

    return router


def register_incident_box_describe_routes(app: FastAPI) -> None:
    """Register ``POST /api/v1/incidents/{video_id}/box/describe``."""
    app.include_router(create_incident_box_describe_router())
    logger.info("Registered POST /api/v1/incidents/{video_id}/box/describe")
