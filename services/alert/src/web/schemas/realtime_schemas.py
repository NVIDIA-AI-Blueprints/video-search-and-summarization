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
Pydantic schemas for real-time VLM alert rule management API.

POST   /api/v1/realtime                        — create an alert rule
GET    /api/v1/realtime                        — list active alert rules
GET    /api/v1/realtime/{alert_rule_id}         — get a single alert rule
DELETE /api/v1/realtime/{alert_rule_id}         — delete an alert rule
GET    /api/v1/realtime/incidents              — query incidents from Elasticsearch
POST   /api/v1/realtime/always-on              — start/stop always-on rules for a camera event

Only the REST request/response envelopes live here. The YAML config
schemas for the always-on rules file (``AlwaysOnRuleEntry``,
``AlwaysOnRuleParams``, ``AlwaysOnRulesFile``) live in
:mod:`realtime.schemas.always_on_config` so the same models can be
shared between REST routes and non-REST callers. They are re-exported
below for backward compatibility.
"""

from typing import Any, Dict, List, Literal, Optional

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
)

from realtime import (
    AlwaysOnRuleEntry,
    AlwaysOnRuleParams,
    AlwaysOnRulesFile,
    ResponseStatus,
    RuleStatus,
)


class RealtimeAlertRequest(BaseModel):
    """Request body for POST /api/v1/realtime."""

    model_config = ConfigDict(
        populate_by_name=True,
        json_schema_extra={
            "example": {
                "live_stream_url": "rtsp://localhost:8554/media/video1",
                "sensor_id": "cc06804c-7f11-4865-bb00-6b2db072086f",
                "sensor_name": "Camera_123",
                "alert_type": "collision",
                "prompt": "Detect vehicle collisions or near-miss events in this traffic camera feed.",
                "system_prompt": "You are a traffic safety monitoring assistant.",
                "model": "nvidia/cosmos-reason2-8b",
                "chunk_duration": 30,
                "chunk_overlap_duration": 5,
                "num_frames_per_second_or_fixed_frames_chunk": 10,
                "use_fps_for_chunking": True,
                "vlm_input_width": 256,
                "vlm_input_height": 256,
                "enable_reasoning": True,
                "place_name": "Dock Entrance-East",
                "place_type": "warehouse-bay",
                "place_lat": "37.3706",
                "place_lon": "-121.9672",
            }
        },
    )

    live_stream_url: str = Field(
        ...,
        validation_alias=AliasChoices("live_stream_url", "liveStreamUrl", "stream"),
        description="RTSP URL of the live stream to monitor",
        json_schema_extra={"example": "rtsp://localhost:8554/media/video1"},
    )

    @field_validator("live_stream_url")
    @classmethod
    def validate_rtsp_url(cls, url: str) -> str:
        """Ensure live_stream_url is a valid RTSP URL."""
        if not url or not url.strip():
            raise ValueError("live_stream_url cannot be empty")
        url = url.strip()
        if not url.lower().startswith("rtsp://"):
            raise ValueError(
                f"live_stream_url must be an RTSP URL (rtsp://...), got: {url[:50]}"
            )
        return url

    sensor_id: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("sensor_id", "id"),
        description=(
            "Sensor ID from VIOS, used as the stream identifier in RTVI "
            "VLM. Optional: when omitted, the field is forwarded to RTVI "
            "as ``null`` and RTVI assigns its own stream identifier."
        ),
        json_schema_extra={"example": "cc06804c-7f11-4865-bb00-6b2db072086f"},
    )
    sensor_name: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("sensor_name", "sensorName"),
        description=(
            "Optional human-readable camera/sensor label. Forwarded "
            "verbatim to RTVI's /streams/add `sensor_name`; downstream "
            "sinks use it to correlate alerts/captions back to a camera. "
            "Always-on callers populate this from the VST event's "
            "`camera_name` automatically."
        ),
        json_schema_extra={"example": "Camera_123"},
    )
    description: Optional[str] = Field(
        default=None,
        description="Description of the live stream",
    )
    username: Optional[str] = Field(
        default=None,
        description="RTSP authentication username",
    )
    password: Optional[str] = Field(
        default=None,
        description="RTSP authentication password",
    )
    place_name: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("place_name", "placeName"),
        description="Name of the monitored location",
        json_schema_extra={"example": "Dock Entrance-East"},
    )
    place_type: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("place_type", "placeType"),
        description="Type of the monitored location",
        json_schema_extra={"example": "warehouse-bay"},
    )
    place_lat: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("place_lat", "placeLat"),
        description="Latitude of the monitored location",
        json_schema_extra={"example": "37.3706"},
    )
    place_lon: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("place_lon", "placeLon"),
        description="Longitude of the monitored location",
        json_schema_extra={"example": "-121.9672"},
    )
    place_alt: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("place_alt", "placeAlt"),
        description="Altitude of the monitored location",
        json_schema_extra={"example": "0"},
    )
    place_coordinate_x: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("place_coordinate_x", "placeCoordinateX"),
        description="X coordinate within the facility map",
        json_schema_extra={"example": "12.5"},
    )
    place_coordinate_y: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("place_coordinate_y", "placeCoordinateY"),
        description="Y coordinate within the facility map",
        json_schema_extra={"example": "4.2"},
    )
    alert_type: str = Field(
        ...,
        description="Alert type label for this rule (e.g. 'collision')",
    )
    prompt: str = Field(
        ...,
        description="User prompt describing what to detect / analyse",
    )
    system_prompt: str = Field(
        default="",
        description="Optional system prompt for the VLM",
    )
    model: str = Field(
        default="",
        description=(
            "VLM model name. If empty, the service falls back to "
            "'rtvi_vlm.default_model' from the Alert Bridge config. "
            "At least one of the two must be non-empty; otherwise the "
            "request is rejected with 422."
        ),
    )
    chunk_duration: int = Field(
        default=30,
        ge=1,
        description="Duration (seconds) of each video chunk sent to VLM",
    )
    chunk_overlap_duration: int = Field(
        default=5,
        ge=0,
        description="Overlap (seconds) between consecutive chunks",
    )
    num_frames_per_second_or_fixed_frames_chunk: int = Field(
        default=10,
        ge=1,
        validation_alias=AliasChoices(
            "num_frames_per_second_or_fixed_frames_chunk",
            "chunk_frames",
        ),
        description=(
            "Same as RTVI VLM generate_captions_alerts: FPS when use_fps_for_chunking "
            "is true, else fixed frames per chunk"
        ),
    )
    use_fps_for_chunking: bool = Field(
        default=True,
        description=(
            "RTVI VLM: if true, num_frames_per_second_or_fixed_frames_chunk is FPS; "
            "if false, fixed frame count per chunk"
        ),
    )
    vlm_input_width: int = Field(
        default=256,
        ge=1,
        description="RTVI: VLM input image width",
    )
    vlm_input_height: int = Field(
        default=256,
        ge=1,
        description="RTVI: VLM input image height",
    )
    enable_reasoning: bool = Field(
        default=True,
        description="RTVI: enable VLM reasoning",
    )
    # Extended RTVI VLM options — all optional.
    # When absent the field is omitted from the RTVI generate_captions payload
    # so RTVI applies its own server-side default for that key.
    api_type: Optional[str] = Field(
        default=None,
        description="RTVI: API type hint forwarded verbatim (e.g. 'internal')",
    )
    response_format: Optional[Dict[str, Any]] = Field(
        default=None,
        description="RTVI: response format object (e.g. {\"type\": \"text\"})",
    )
    stream_options: Optional[Dict[str, Any]] = Field(
        default=None,
        description="RTVI: streaming options (e.g. {\"include_usage\": true})",
    )
    max_tokens: Optional[int] = Field(
        default=None,
        ge=1,
        description="RTVI: maximum tokens to generate",
    )
    temperature: Optional[float] = Field(
        default=None,
        ge=0.0,
        description="RTVI: sampling temperature",
    )
    top_p: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="RTVI: nucleus sampling probability",
    )
    top_k: Optional[int] = Field(
        default=None,
        ge=0,
        description="RTVI: top-k sampling",
    )
    ignore_eos: Optional[bool] = Field(
        default=None,
        description="RTVI: ignore end-of-sequence token",
    )
    seed: Optional[int] = Field(
        default=None,
        description="RTVI: random seed for reproducibility",
    )
    media_info: Optional[Dict[str, Any]] = Field(
        default=None,
        description=(
            "RTVI: media window descriptor "
            "(e.g. {\"type\": \"offset\", \"start_offset\": 0, \"end_offset\": 4000000000})"
        ),
    )
    enable_audio: Optional[bool] = Field(
        default=None,
        description="RTVI: include audio in VLM analysis",
    )
    mm_processor_kwargs: Optional[Dict[str, Any]] = Field(
        default=None,
        description="RTVI: additional multimodal processor kwargs",
    )


class RealtimeAlertResponse(BaseModel):
    """Response returned when an alert rule is created."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "status": "success",
                "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
                "created_at": "2025-06-01T12:00:00Z",
                "message": "Realtime alert rule created",
            }
        }
    )

    status: str = Field(default=ResponseStatus.SUCCESS)
    id: str = Field(..., description="Unique alert rule ID for subsequent management")
    created_at: str = Field(..., description="ISO-8601 creation timestamp")
    message: str = Field(default="Realtime alert rule created")


class RealtimeAlertRule(BaseModel):
    """Representation of a single active realtime alert rule."""

    model_config = ConfigDict(
        extra="ignore",
        json_schema_extra={
            "example": {
                "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
                "sensor_id": "cc06804c-7f11-4865-bb00-6b2db072086f",
                "sensor_name": "Camera_123",
                "live_stream_url": "rtsp://localhost:8554/media/video1",
                "alert_type": "collision",
                "prompt": "Detect vehicle collisions.",
                "system_prompt": "You are a traffic monitoring assistant.",
                "model": "nvidia/cosmos-reason2-8b",
                "chunk_duration": 30,
                "chunk_overlap_duration": 5,
                "num_frames_per_second_or_fixed_frames_chunk": 10,
                "use_fps_for_chunking": True,
                "vlm_input_width": 256,
                "vlm_input_height": 256,
                "enable_reasoning": True,
                "status": "active",
                "created_at": "2025-06-01T12:00:00Z",
            }
        },
    )

    id: str
    sensor_id: Optional[str] = Field(
        default=None,
        description=(
            "Sensor/stream ID in VIOS. Optional in responses to remain "
            "backward-compatible with legacy rule documents persisted "
            "before the field existed; for rules created or replayed by "
            "the current service it is always set."
        ),
    )
    sensor_name: Optional[str] = Field(default=None)
    description: Optional[str] = Field(default=None)
    live_stream_url: str
    place_name: Optional[str] = Field(default=None)
    place_type: Optional[str] = Field(default=None)
    place_lat: Optional[str] = Field(default=None)
    place_lon: Optional[str] = Field(default=None)
    place_alt: Optional[str] = Field(default=None)
    place_coordinate_x: Optional[str] = Field(default=None)
    place_coordinate_y: Optional[str] = Field(default=None)
    alert_type: str = ""
    prompt: str
    system_prompt: str
    model: str
    chunk_duration: int
    chunk_overlap_duration: int
    num_frames_per_second_or_fixed_frames_chunk: int
    use_fps_for_chunking: bool
    vlm_input_width: int
    vlm_input_height: int
    enable_reasoning: bool
    status: str = Field(default=RuleStatus.ACTIVE)
    created_at: str
    updated_at: Optional[str] = Field(
        default=None,
        description="last-update timestamp (set by Elasticsearch)",
    )
    # Extended RTVI VLM options — present only when the rule was created
    # with these values set; absent otherwise.
    api_type: Optional[str] = None
    response_format: Optional[Dict[str, Any]] = None
    stream_options: Optional[Dict[str, Any]] = None
    max_tokens: Optional[int] = None
    temperature: Optional[float] = None
    top_p: Optional[float] = None
    top_k: Optional[int] = None
    ignore_eos: Optional[bool] = None
    seed: Optional[int] = None
    media_info: Optional[Dict[str, Any]] = None
    enable_audio: Optional[bool] = None
    mm_processor_kwargs: Optional[Dict[str, Any]] = None


class RealtimeAlertListResponse(BaseModel):
    """Response for GET /api/v1/realtime."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "status": "success",
                "rules": [
                    {
                        "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
                        "sensor_id": "cc06804c-7f11-4865-bb00-6b2db072086f",
                        "sensor_name": "Camera_123",
                        "live_stream_url": "rtsp://localhost:8554/media/video1",
                        "alert_type": "collision",
                        "prompt": "Detect vehicle collisions.",
                        "system_prompt": "",
                        "model": "nvidia/cosmos-reason2-8b",
                        "chunk_duration": 30,
                        "chunk_overlap_duration": 5,
                        "num_frames_per_second_or_fixed_frames_chunk": 10,
                        "use_fps_for_chunking": True,
                        "vlm_input_width": 256,
                        "vlm_input_height": 256,
                        "enable_reasoning": True,
                        "status": "active",
                        "created_at": "2025-06-01T12:00:00Z",
                    }
                ],
                "count": 1,
                "total": 1,
            }
        }
    )

    status: str = Field(default=ResponseStatus.SUCCESS)
    rules: List[RealtimeAlertRule]
    count: int
    total: int = Field(
        default=0,
        description="Total matching rules",
    )


class RealtimeAlertGetResponse(BaseModel):
    """Response for GET /api/v1/realtime/{alert_rule_id}."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "status": "success",
                "rule": {
                    "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
                    "sensor_id": "cc06804c-7f11-4865-bb00-6b2db072086f",
                    "sensor_name": "Camera_123",
                    "live_stream_url": "rtsp://localhost:8554/media/video1",
                    "alert_type": "collision",
                    "prompt": "Detect vehicle collisions.",
                    "system_prompt": "",
                    "model": "nvidia/cosmos-reason2-8b",
                    "chunk_duration": 30,
                    "chunk_overlap_duration": 5,
                    "num_frames_per_second_or_fixed_frames_chunk": 10,
                    "use_fps_for_chunking": True,
                    "vlm_input_width": 256,
                    "vlm_input_height": 256,
                    "enable_reasoning": True,
                    "status": "active",
                    "created_at": "2025-06-01T12:00:00Z",
                },
            }
        }
    )

    status: str = Field(default=ResponseStatus.SUCCESS)
    rule: RealtimeAlertRule


class RealtimeAlertDeleteResponse(BaseModel):
    """Response for DELETE /api/v1/realtime/{alert_rule_id}."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "status": "success",
                "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
                "message": "Alert rule deleted successfully",
            }
        }
    )

    status: str = Field(default=ResponseStatus.SUCCESS)
    id: str
    message: str


class RealtimeAlertErrorResponse(BaseModel):
    """Generic error envelope."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "status": "error",
                "error": "validation_error",
                "message": "live_stream_url must be an RTSP URL (rtsp://...)",
                "timestamp": "2025-06-01T12:00:00Z",
            }
        }
    )

    status: str = Field(default=ResponseStatus.ERROR)
    error: str
    message: str
    timestamp: str
    correlation_id: Optional[str] = Field(
        default=None,
        description=(
            "Per-invocation UUID4 hex, present on replay error responses "
            "(501 / 409 / 502) so operators can grep logs by it. Omitted "
            "for non-replay error paths."
        ),
    )


class RealtimeReplayResponse(BaseModel):
    """Response for POST /api/v1/realtime/replay."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "status": "success",
                "message": "Replay completed: 3 replayed, 0 failed out of 3 total",
                "correlation_id": "f47ac10b58cc4372a5670e02b2c3d479",
                "replayed": 3,
                "failed": 0,
                "total": 3,
                "details": [
                    {
                        "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
                        "alert_type": "collision",
                        "result": "success",
                    },
                    {
                        "id": "b2c3d4e5-f6a7-8901-bcde-f12345678901",
                        "alert_type": "intrusion",
                        "result": "success",
                    },
                ],
            }
        }
    )

    status: str = Field(default=ResponseStatus.SUCCESS)
    message: str
    correlation_id: str = Field(
        description=(
            "Per-invocation UUID4 hex woven through every replay log line "
            "(stage=replay_start|replay_rule|replay_end). Operators grep "
            "their log aggregator with it to follow one invocation "
            "end-to-end across the per-rule fan-out."
        ),
    )
    replayed: int = Field(description="Number of rules successfully re-onboarded")
    failed: int = Field(description="Number of rules that failed re-onboard")
    total: int = Field(description="Total rules attempted")
    details: List[dict] = Field(
        default_factory=list,
        description="Per-rule outcome: id, alert_type, result, error (if failed)",
    )


# ---------------------------------------------------------------------------
# Incidents schemas (for GET /api/v1/realtime/incidents)
# ---------------------------------------------------------------------------

class IncidentListResponse(BaseModel):
    """Response for GET /api/v1/realtime/incidents."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "status": "success",
                "incidents": [
                    {
                        "id": "incident-001",
                        "sensor_id": "cc06804c-7f11-4865-bb00-6b2db072086f",
                        "category": "collision",
                        "timestamp": "2025-06-01T12:00:00Z",
                        "description": "Vehicle collision detected at intersection.",
                    }
                ],
                "count": 1,
                "total": 42,
                "timestamp": "2025-06-01T12:05:00Z",
            }
        }
    )

    status: str = Field(default=ResponseStatus.SUCCESS)
    incidents: List[dict] = Field(
        default=[],
        description="List of incident documents from Elasticsearch",
    )
    count: int = Field(description="Number of incidents returned")
    total: int = Field(description="Total number of matching incidents in ES")
    timestamp: str = Field(description="ISO-8601 response timestamp")


# ---------------------------------------------------------------------------
# Always-on request schemas (for POST /api/v1/realtime/always-on)
# ---------------------------------------------------------------------------


AlwaysOnChange = Literal["camera_streaming", "camera_remove"]


class AlwaysOnEvent(BaseModel):
    """Inner ``event`` object of a VST-style camera lifecycle event.

    The shape mirrors what the real producer emits; extra fields are
    tolerated so producers can add metadata without breaking us.
    """

    model_config = ConfigDict(extra="allow")

    camera_id: str = Field(
        ...,
        min_length=1,
        description="Unique camera identifier",
    )
    camera_name: Optional[str] = Field(
        default=None,
        description=(
            "Human-readable camera label. Required on `camera_streaming` "
            "(enforced by the handler); ignored on `camera_remove`."
        ),
    )
    camera_url: Optional[str] = Field(
        default=None,
        description=(
            "RTSP URL for the live stream. Required on `camera_streaming` "
            "(enforced by the handler); ignored on `camera_remove`."
        ),
    )
    camera_vod_url: Optional[str] = Field(
        default=None,
        description="Optional VOD RTSP URL (not used by always-on)",
    )
    change: AlwaysOnChange = Field(
        ...,
        description=(
            "Lifecycle signal: `camera_streaming` starts the configured "
            "always-on rules for this camera; `camera_remove` tears them "
            "down."
        ),
    )
    metadata: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Producer-specific metadata (informational; not used by always-on)",
    )


class AlwaysOnEventRequest(BaseModel):
    """Request body for ``POST /api/v1/realtime/always-on``.

    Only the canonical VST shape is accepted:

    ```json
    {
      "source": "vst",
      "alert_type": "camera_status_change",
      "created_at": "2026-04-22T17:38:38Z",
      "event": {
        "camera_id": "c0413489-6ca1-422e-a09c-08224169ff6a",
        "camera_name": "warehouse",
        "camera_url": "rtsp://localhost:8554/live/<id>",
        "camera_vod_url": "rtsp://localhost:8554/vod/<id>",
        "change": "camera_streaming",
        "metadata": {"codec": "H264"}
      }
    }
    ```

    Producer-only fields on the outer envelope (``source``,
    ``alert_type``, ``created_at``) and inside ``event`` (``metadata``,
    ``camera_vod_url``) pass through unused; extra unknown fields are
    tolerated (``extra="allow"``) so upstream changes don't break the
    endpoint.
    """

    model_config = ConfigDict(extra="allow")

    source: Optional[str] = Field(
        default=None,
        description="Upstream producer tag (informational; e.g. 'vst')",
    )
    alert_type: Optional[str] = Field(
        default=None,
        description=(
            "Producer-assigned event type, e.g. 'camera_status_change'. "
            "Not to be confused with the always-on rule's `alert_type` in "
            "the YAML config — this one is informational and passes through "
            "unused."
        ),
    )
    created_at: Optional[str] = Field(
        default=None,
        description="Producer-assigned creation timestamp (informational)",
    )
    event: AlwaysOnEvent = Field(
        ...,
        description="Camera lifecycle event payload",
    )


# ---------------------------------------------------------------------------
# Re-exports of the always-on YAML config schemas
# ---------------------------------------------------------------------------
# The YAML config schemas live in ``realtime.schemas.always_on_config``
# so they can be loaded/validated from non-REST contexts (agent flows,
# CLI, workers). Re-exported here for callers that historically
# imported them from this module.

__all__ = [
    "AlwaysOnChange",
    "AlwaysOnEvent",
    "AlwaysOnEventRequest",
    "AlwaysOnRuleEntry",
    "AlwaysOnRuleParams",
    "AlwaysOnRulesFile",
    "IncidentListResponse",
    "RealtimeAlertDeleteResponse",
    "RealtimeAlertErrorResponse",
    "RealtimeAlertGetResponse",
    "RealtimeAlertListResponse",
    "RealtimeAlertRequest",
    "RealtimeAlertResponse",
    "RealtimeAlertRule",
    "RealtimeReplayResponse",
]
