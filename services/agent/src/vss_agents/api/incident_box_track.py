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

"""``POST /api/v1/incidents/{incident_id}/box/track`` - follow a reviewer's box through the clip.

``incident-console-v2`` (Edit mode) sends the box the reviewer drew, the second it was drawn at, and the item's name
and description. The agent finds the video through ``videos.filepath`` (an R2 key), runs
:func:`vss_agents.utils.box_track.track_from_box` and returns ``{"track": {...}}``. Nothing is persisted.

Status codes: 422 for an invalid body, 404 for an unknown video or one without a stored file, 503 when the incident
database or an object tracker is not available, 502 when the video cannot be read.
"""

import asyncio
import logging

from fastapi import APIRouter
from fastapi import FastAPI
from fastapi import HTTPException
from pydantic import BaseModel
from pydantic import Field
from pydantic import field_validator

from vss_agents.utils import incident_db
from vss_agents.utils.box_track import DEFAULT_WINDOW_SECONDS
from vss_agents.utils.box_track import MAX_WINDOW_SECONDS
from vss_agents.utils.box_track import BoxTrackUnreadableError
from vss_agents.utils.box_track import request_settings
from vss_agents.utils.box_track import track_from_box
from vss_agents.utils.crop_evidence import CropCallBudget
from vss_agents.utils.crop_evidence import DefaultDetector
from vss_agents.utils.object_tracking import ObjectTrackingSettings
from vss_agents.utils.object_tracking import TrackerUnavailableError
from vss_agents.utils.object_tracking import select_tracker
from vss_agents.utils.r2 import resolve_r2_url

logger = logging.getLogger(__name__)


class BoxTrackRequest(BaseModel):
    """Body for ``POST /api/v1/incidents/{incident_id}/box/track``."""

    seconds: float = Field(..., ge=0, allow_inf_nan=False, description="Video second the box was drawn at.")
    box: list[float] = Field(..., description="[ymin, xmin, ymax, xmax] on the 0-1000 grid.")
    item_type: str = Field(..., min_length=1, max_length=40)
    name: str = Field(..., min_length=1, max_length=200)
    description: str = Field(default="", max_length=2000)
    back_seconds: float = Field(default=DEFAULT_WINDOW_SECONDS, ge=0, le=MAX_WINDOW_SECONDS, allow_inf_nan=False)
    forward_seconds: float = Field(default=DEFAULT_WINDOW_SECONDS, ge=0, le=MAX_WINDOW_SECONDS, allow_inf_nan=False)

    @field_validator("box")
    @classmethod
    def _valid_box(cls, box: list[float]) -> list[float]:
        if len(box) != 4 or not all(0 <= value <= 1000 for value in box) or box[0] >= box[2] or box[1] >= box[3]:
            raise ValueError("box must be [ymin, xmin, ymax, xmax] on 0-1000 with ymin < ymax and xmin < xmax")
        return box


async def _video_ref(video_id: str) -> str:
    db = await incident_db.get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="incident database is not configured or unreachable")
    video = await db.get_video(video_id)
    filepath = str((video or {}).get("filepath") or "").strip()
    if not filepath:
        raise HTTPException(status_code=404, detail=f"no stored video for {video_id}")
    return resolve_r2_url(filepath)


def create_incident_box_track_router() -> APIRouter:
    router = APIRouter()

    @router.post(
        "/api/v1/incidents/{incident_id}/box/track",
        summary="Follow a reviewer's box (with the item's name and description) back and forward through the video",
        tags=["Incident Box"],
    )
    async def box_track(incident_id: str, body: BoxTrackRequest) -> dict[str, object]:
        settings = ObjectTrackingSettings.from_env()
        try:
            _, factory = select_tracker(settings.model_dir)
        except TrackerUnavailableError as exc:
            raise HTTPException(status_code=503, detail=f"no object tracker available: {exc}") from exc
        video_ref = await _video_ref(incident_id)
        detector = DefaultDetector(call_budget=CropCallBudget(settings.max_detector_calls)).with_request_limits(
            settings.detector_timeout_seconds, 1
        )
        try:
            track = await asyncio.to_thread(
                track_from_box,
                video_ref,
                seconds=body.seconds,
                box=body.box,
                name=body.name,
                description=body.description,
                back_seconds=body.back_seconds,
                forward_seconds=body.forward_seconds,
                detect=detector.detect,
                settings=request_settings(settings),
                tracker_factory=factory,
            )
        except BoxTrackUnreadableError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        except Exception as exc:
            logger.error("/box/track failed for incident_id=%s: %s", incident_id, exc, exc_info=True)
            raise HTTPException(status_code=500, detail=f"Tracking failed: {exc}") from exc
        return {"track": track}

    return router


def register_incident_box_track_routes(app: FastAPI) -> None:
    """Register ``POST /api/v1/incidents/{incident_id}/box/track`` (always mounted)."""
    app.include_router(create_incident_box_track_router())
    logger.info("Registered POST /api/v1/incidents/{incident_id}/box/track")
