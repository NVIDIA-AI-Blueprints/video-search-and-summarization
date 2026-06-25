#!/usr/bin/env python3
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

"""
Schemas for on-demand verification API.
Accepts full Incident payload -- same structure DirectMedia receives from Kafka.
"""

from typing import Any, Dict

from pydantic import BaseModel, Field, validator


class OnDemandVerificationRequest(BaseModel):
    """Full Incident payload for on-demand verification.

    Accepts the same message structure that DirectMedia receives from Kafka.
    ``category`` and ``info`` (with ``media_urls`` + ``media_type``) are required;
    all other Incident fields are optional and passed through to the response.
    """

    category: str = Field(..., min_length=1, max_length=100)
    info: Dict[str, Any] = Field(..., description="Must contain media_urls (list) and media_type ('video'|'image')")

    @validator("category")
    def validate_category(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not normalized:
            raise ValueError("category cannot be empty")
        return normalized

    @validator("info")
    def validate_info(cls, value: Dict[str, Any]) -> Dict[str, Any]:
        media_urls = value.get("media_urls")
        if not media_urls:
            raise ValueError("info.media_urls is required")
        if isinstance(media_urls, list):
            cleaned = [str(u).strip() for u in media_urls if u and str(u).strip()]
            if not cleaned:
                raise ValueError("info.media_urls must contain at least one non-empty entry")
            value["media_urls"] = cleaned
        media_type = str(value.get("media_type", "")).strip().lower()
        if media_type not in ("video", "image"):
            raise ValueError("info.media_type is required and must be 'video' or 'image'")
        value["media_type"] = media_type
        return value

    class Config:
        extra = "allow"
        json_schema_extra = {
            "example": {
                "id": "incident-123",
                "sensorId": "sensor-01",
                "timestamp": "2026-04-22T10:00:00Z",
                "end": "2026-04-22T10:00:30Z",
                "category": "collision",
                "info": {
                    "media_urls": ["http://localhost:30888/vst/sim/media/incident.mp4"],
                    "media_type": "video",
                },
            }
        }


