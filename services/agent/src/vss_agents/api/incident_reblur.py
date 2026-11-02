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

"""``POST /api/v1/incidents/{incident_id}/reblur`` - the face-blur toggle of ``incident-console-v2``.

Switching a video's "Blur faces" setting changes how the evidence images look, not what the report says, so it does
not analyse the video again. The route takes an existing revision (``source_revision_id``, the one the report page
shows), keeps its report payload and evidence entries as they are, makes every evidence frame, crop and clip-heads file
again from the video at the same seconds and boxes (:mod:`vss_agents.utils.reblur_evidence`), and appends the result as
a new AI revision (``kind='ai'``, ``parent_id`` NULL, ``revision_no`` max + 1) with the images under a new
``evidence/<video key>/run-<n>/`` folder. The source revision, its run and its images are never changed.

``blur_faces: true`` pixelates every head (the only model call is the head detection); ``blur_faces: false`` stores the
images unblurred and calls nothing. The caption pass, P1, grounding, the zoomed second look and tracking never run.

Status codes: 422 for an invalid body, 404 when the source revision does not exist, 409 when it belongs to another
video, 503 when the incident DB, the evidence store (R2) or the head detector is not configured, 504 when the images take
longer than ``REBLUR_DEADLINE_SECONDS`` (no revision is written then), 500 for anything else.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from fastapi import APIRouter
from fastapi import FastAPI
from fastapi import HTTPException
from pydantic import BaseModel
from pydantic import Field

from vss_agents import incident_contract
from vss_agents.tools.incident_report_gen import revision_embedding
from vss_agents.utils import crop_evidence
from vss_agents.utils import incident_db
from vss_agents.utils import privacy_blur
from vss_agents.utils import reblur_evidence

logger = logging.getLogger(__name__)

# The images are made well inside this (seconds with the privacy call cap of 200), and the console waits longer
# (REBLUR_TIMEOUT_SECONDS in reblur-video.ts), so a call the console gave up on has never written a revision: the
# agent stops first with a 504 and the revision is inserted only after the images are stored.
REBLUR_DEADLINE_SECONDS = 480.0


class ReblurIncidentRequest(BaseModel):
    """Body for ``POST /api/v1/incidents/{incident_id}/reblur``."""

    source_revision_id: int = Field(
        ..., ge=1, description="The report_revisions.id whose report and evidence are kept."
    )
    video_url: str = Field(..., min_length=1, description="Signed R2 URL of the uploaded video.")
    video_key: str | None = Field(
        default=None,
        min_length=1,
        max_length=900,
        description="The video's R2 key; the new images go under evidence/<video_key>/run-<n>/.",
    )
    blur_faces: bool = Field(
        ..., description="True to pixelate every head in the new images; false to store them as cut."
    )
    model_run_id: str = Field(
        ...,
        min_length=1,
        max_length=20,
        description="The caller's model_runs.id for the new revision (the caller owns its notes).",
    )
    model: str = Field(..., min_length=1, description="The model name recorded on the run (the source run's).")
    video_duration_seconds: float | None = Field(
        default=None,
        gt=0,
        allow_inf_nan=False,
        strict=True,
        description="Video length in seconds, bounding the clip heads.",
    )


def _stored_embedding(row: dict[str, Any]) -> list[float] | None:
    """The source's summary vector (the payload is unchanged, so is its vector); None when it has none."""
    value = row.get("embedding")
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return None
    if isinstance(value, list) and value and all(isinstance(v, (int, float)) for v in value):
        return [float(v) for v in value]
    return None


async def reblur_revision(incident_id: str, body: ReblurIncidentRequest) -> dict[str, Any]:
    """Make the new revision of ``body.source_revision_id`` and return the response body (see the module docstring)."""
    started = time.perf_counter()
    if not incident_db.is_configured():
        raise HTTPException(status_code=503, detail="the incident DB is not configured on this agent")
    db = await incident_db.get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="the incident DB is not reachable")
    service = crop_evidence.get_crop_evidence_service()
    if service.store is None:
        raise HTTPException(status_code=503, detail="no evidence image store is configured (R2 settings missing)")
    if body.blur_faces and not service.is_detector_available():
        raise HTTPException(status_code=503, detail="no head detector is configured (VLM grounding key missing)")

    source = await db.get_revision(body.source_revision_id)
    if source is None:
        raise HTTPException(status_code=404, detail=f"revision {body.source_revision_id} does not exist")
    if source.get("video_id") != incident_id:
        raise HTTPException(status_code=409, detail=f"revision {body.source_revision_id} belongs to another video")
    payload = source.get("payload")
    evidence = source.get("evidence")
    if not isinstance(payload, dict) or not isinstance(evidence, list):
        raise HTTPException(status_code=409, detail=f"revision {body.source_revision_id} has no report or evidence")

    privacy = privacy_blur.PrivacySummary() if body.blur_faces else None
    try:
        new_evidence = await asyncio.wait_for(
            reblur_evidence.reblur_evidence(
                video_ref=body.video_url,
                video_id=incident_id,
                source_evidence=[entry for entry in evidence if isinstance(entry, dict)],
                blur=body.blur_faces,
                store=service.store,
                detector=service.detector if body.blur_faces else None,
                privacy=privacy,
                video_key=body.video_key,
                video_seconds=body.video_duration_seconds,
            ),
            timeout=REBLUR_DEADLINE_SECONDS,
        )
    except TimeoutError as exc:
        raise HTTPException(
            status_code=504, detail=f"the re-blur took longer than {REBLUR_DEADLINE_SECONDS:.0f}s; nothing was stored"
        ) from exc

    await db.insert_model_run_if_absent(
        body.model_run_id, model_name=body.model, prompt_version=incident_contract.contract_version()
    )
    row = await db.insert_ai_revision(
        incident_id,
        model_run_id=body.model_run_id,
        payload=payload,
        evidence=new_evidence,
        note=f"Re-blur of revision {source.get('revision_no')} (faces {'blurred' if body.blur_faces else 'not blurred'}); report unchanged",
        embedding=_stored_embedding(source) or await revision_embedding(payload),
    )
    images = sum(1 for entry in new_evidence if entry.get("image") is not None)
    response: dict[str, Any] = {
        "incident_id": incident_id,
        "source_revision_id": body.source_revision_id,
        "revision_id": int(row["id"]),
        "blur_faces": body.blur_faces,
        "items": len(new_evidence),
        "images": images,
        "elapsed_ms": round((time.perf_counter() - started) * 1000.0, 1),
    }
    if privacy is not None:
        response["privacy"] = privacy.to_dict()
        logger.info("reblur: privacy blur for %s: %s", incident_id, privacy.to_dict())
    return response


def create_incident_reblur_router() -> APIRouter:
    """Build the ``POST /api/v1/incidents/{incident_id}/reblur`` router."""
    router = APIRouter()

    @router.post(
        "/api/v1/incidents/{incident_id}/reblur",
        summary="Make a revision's evidence images again with the faces blurred or not, keeping its report",
        tags=["Incident Analyze"],
    )
    async def reblur_incident(incident_id: str, body: ReblurIncidentRequest) -> dict[str, Any]:
        try:
            return await reblur_revision(incident_id, body)
        except HTTPException:
            raise
        except Exception as exc:
            logger.error("/reblur failed for incident_id=%s: %s", incident_id, exc, exc_info=True)
            raise HTTPException(status_code=500, detail=f"Re-blur failed: {exc}") from exc

    return router


def register_incident_reblur_routes(app: FastAPI) -> None:
    """Register ``POST /api/v1/incidents/{incident_id}/reblur``."""
    app.include_router(create_incident_reblur_router())
    logger.info("Registered POST /api/v1/incidents/{incident_id}/reblur")
