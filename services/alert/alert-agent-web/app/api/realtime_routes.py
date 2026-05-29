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
FastAPI routes for real-time VLM alert rule management.

POST   /api/v1/realtime                        — create an alert rule
GET    /api/v1/realtime                        — list active alert rules
GET    /api/v1/realtime/{alert_rule_id}         — get a single alert rule
DELETE /api/v1/realtime/{alert_rule_id}         — delete an alert rule
GET    /api/v1/realtime/incidents              — query incidents from Elasticsearch
POST   /api/v1/realtime/always-on              — start (change=camera_streaming) or stop (change=camera_remove) the YAML-configured always-on alert rules for an incoming camera event

This module is a thin REST adapter. All business logic — including
feature-flag gating, rules YAML loading, the per-camera fan-out, sidecar
state, and concurrency bookkeeping — lives in the service layer under
:mod:`realtime.services`. That way the same orchestration can be
invoked from non-REST callers (agent flows, replay tools, integration
tests) without going through HTTP.
"""

import json
import logging
from datetime import datetime
from functools import lru_cache
from http import HTTPStatus
from typing import Any, Dict, List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from .realtime_schemas import (
    AlwaysOnEventRequest,
    IncidentListResponse,
    RealtimeAlertDeleteResponse,
    RealtimeAlertErrorResponse,
    RealtimeAlertGetResponse,
    RealtimeAlertListResponse,
    RealtimeAlertRequest,
    RealtimeAlertResponse,
    RealtimeReplayResponse,
)
from realtime import (
    AlertRuleConfig,
    AlwaysOnReason,
    AlwaysOnService,
    ESRuleStore,
    IncidentService,
    RealtimeAlertService,
    RuleStore,
    load_config,
)
from persistence import create_persistence_store

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/realtime", tags=["realtime"])


def _flatten_json_schema(schema: dict) -> dict:
    """Inline all ``$defs`` references so the schema works inside
    an OpenAPI document (where ``$ref: '#/$defs/...'`` resolves from
    the document root, not from the schema object)."""
    defs = schema.pop("$defs", {})
    if not defs:
        return schema

    def _resolve(obj):
        if isinstance(obj, dict):
            if "$ref" in obj:
                ref_name = obj["$ref"].rsplit("/", 1)[-1]
                if ref_name in defs:
                    return _resolve(defs[ref_name])
                return obj
            return {k: _resolve(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [_resolve(item) for item in obj]
        return obj

    return _resolve(schema)


def _always_on_response(
    status_code: int,
    reason: str,
    details: Optional[List[Dict[str, Any]]] = None,
) -> JSONResponse:
    """Build the always-on response shape.

    The response body carries the top-level ``reason`` + ``status``
    strings SDR already consumes, plus an optional ``details`` array
    with one entry per rule so callers can see which rules succeeded
    and which failed on a partial-success or partial-failure response::

        {
            "reason":  "<REASON>",
            "status":  "HTTP/1.1 <code> <phrase>",
            "details": [                           # optional
                {"rule_id": ..., "alert_type": ..., "status": 201,
                 "result": "success", "alert_rule_id": "<uuid>"},
                {"rule_id": ..., "alert_type": ..., "status": 502,
                 "result": "error",   "error": {<upstream error dict>}},
                ...
            ]
        }

    ``details`` is included only when the service actually fanned out
    across rules (add-path or remove-path). Short-circuit responses
    (disabled feature, bad payload, config error, dedupe short-circuit)
    omit it since there's nothing per-rule to report.

    The status line reason phrase (``"OK"`` / ``"Bad Gateway"`` / …)
    comes from stdlib :class:`http.HTTPStatus` so we don't hand-roll a
    second copy of the IANA-standard phrase table. Unknown codes fall
    back to an empty phrase to keep this helper forgiving.
    """
    try:
        phrase = HTTPStatus(status_code).phrase
    except ValueError:
        phrase = ""
    status_line = f"HTTP/1.1 {status_code} {phrase}".rstrip()
    body: Dict[str, Any] = {"reason": reason, "status": status_line}
    if details is not None:
        body["details"] = details
    return JSONResponse(status_code=status_code, content=body)


# ---------------------------------------------------------------------------
# Service singletons (cached for the lifetime of the process)
# ---------------------------------------------------------------------------


@lru_cache()
def get_elastic_client():
    """Shared Elasticsearch client for the realtime API.

    Returns None if ES is disabled in config, allowing the service to
    return 503 gracefully.
    """
    config = load_config()
    es_cfg = config.get("elastic", {}) or {}

    if not es_cfg.get("enabled", False):
        return None

    hosts_config = es_cfg.get("hosts", [])
    if isinstance(hosts_config, str):
        hosts = (hosts_config,)
    elif isinstance(hosts_config, (list, tuple)):
        hosts = tuple(str(h).strip() for h in hosts_config if h)
    else:
        hosts = tuple()

    if not hosts:
        logger.warning("Elasticsearch enabled but no hosts configured")
        return None

    try:
        from elastic.elastic import ElasticClient, ElasticConfig

        return ElasticClient(
            config=ElasticConfig(
                hosts=hosts,
                username=es_cfg.get("username"),
                password=es_cfg.get("password"),
                api_key=es_cfg.get("api_key"),
                verify_certs=es_cfg.get("verify_certs", False),
                ca_certs=es_cfg.get("ca_certs"),
                request_timeout=es_cfg.get("request_timeout", 10),
            )
        )
    except ConnectionError as exc:
        logger.error(
            "Elasticsearch cluster unreachable at %s: %s", hosts, exc
        )
        return None
    except (ValueError, TypeError) as exc:
        logger.error(
            "Invalid Elasticsearch configuration: %s", exc
        )
        return None
    except Exception:
        logger.exception("Unexpected error creating Elasticsearch client")
        return None


@lru_cache()
def get_rule_store() -> Optional[RuleStore]:
    """Build the :class:`ESRuleStore` backed by the shared persistence layer.

    The ES collection name is read from ``rtvi_vlm.rules_collection`` in
    ``config.yaml`` (defaults to ``alert-realtime-rules``).

    Returns ``None`` when either:

    * the realtime-specific rollback flag
      ``rtvi_vlm.enable_realtime_persistence`` is ``false``,
      OR
    * the global ``persistence.enabled`` is ``false`` (handled by
      :func:`create_persistence_store`).

    In either case :class:`RealtimeAlertService` falls back to in-memory mode.
    """
    config = load_config()

    # Realtime-specific rollback flag. Independent of the
    # global persistence flag so operators can roll back the realtime
    # persistence + replay work without disabling persistence for alert
    # configs / prompts that other features rely on.
    rtvi_cfg = config.get("rtvi_vlm", {}) or {}
    if not rtvi_cfg.get("enable_realtime_persistence", True):
        logger.warning(
            "Realtime persistence disabled via "
            "rtvi_vlm.enable_realtime_persistence — realtime rules will "
            "not be durable and POST /api/v1/realtime/replay will return 501"
        )
        return None

    es_client = get_elastic_client()
    store = create_persistence_store(config, es_client=es_client)

    if store is None:
        logger.warning(
            "Persistence layer is disabled — realtime rules will not be durable"
        )
        return None

    collection = (
        config.get("rtvi_vlm", {}).get("rules_collection", "alert-realtime-rules")
    )
    return ESRuleStore(store, collection=collection)


@lru_cache()
def get_realtime_service() -> RealtimeAlertService:
    return RealtimeAlertService(rule_store=get_rule_store())


@lru_cache()
def get_always_on_service() -> AlwaysOnService:
    """Shared :class:`AlwaysOnService` instance.

    Cached via ``lru_cache`` so every request reuses the same sidecar /
    in-flight state; otherwise concurrent ``camera_streaming`` and
    ``camera_remove`` requests would race on private per-request copies.

    Uses a *separate* in-memory :class:`RealtimeAlertService` (no
    ``rule_store``) instead of the persistent singleton returned by
    :func:`get_realtime_service`.  After an Alert Bridge restart ES
    still carries the old active rules but the ``AlwaysOnService``
    sidecar (``_camera_rules``) is empty.  If the always-on path shared
    the persistent service, the SDR ``camera_streaming`` replay would
    call ``start_alert`` for every YAML rule and the persist-first
    write would create *new* ES documents — duplicating every
    pre-restart rule and leaving the originals outside the sidecar so
    ``camera_remove`` can never clean them up.  Keeping the always-on
    service on the in-memory path avoids this: rules live only in the
    sidecar's lifetime and are re-created cleanly on every restart.
    """
    return AlwaysOnService(realtime_service=RealtimeAlertService())


@lru_cache()
def get_incident_service() -> IncidentService:
    """Create IncidentService with shared ES client."""
    config = load_config()
    sink_cfg = config.get("vlm_enhanced_sink", {})
    incident_cfg = sink_cfg.get("incident", {})
    index_base = "mdx-vlm-incidents"
    if incident_cfg.get("type") == "elastic":
        index_base = incident_cfg.get("elastic", {}).get("index", index_base)

    return IncidentService(
        es_client=get_elastic_client(),
        index_base=index_base,
    )


def validate_always_on_config_at_startup() -> None:
    """Fail-fast check invoked from FastAPI's startup hook.

    Delegates to :meth:`AlwaysOnService.validate_config_at_startup`.
    Kept as a module-level function so ``app.main`` can import it
    without having to resolve the service instance itself.

    The ``always_on`` flag is checked *before* resolving the service
    singleton so that a disabled always-on feature never triggers the
    persistence-layer initialisation chain.  Without this guard the
    startup hook would crash the API when ``persistence.enabled`` is
    true but Elasticsearch is unreachable — even though the always-on
    validator is supposed to be a no-op when disabled.
    """
    config = load_config()
    if not config.get("alert_agent", {}).get("always_on", False):
        logger.info(
            "alert_agent.always_on is disabled — skipping always-on "
            "rules validation"
        )
        return
    get_always_on_service().validate_config_at_startup()


# ---------------------------------------------------------------------------
# POST /api/v1/realtime
# ---------------------------------------------------------------------------
@router.post(
    "",
    response_model=RealtimeAlertResponse,
    status_code=201,
    summary="Create a real-time VLM alert rule",
    description=(
        "Start monitoring a live RTSP stream for a single `alert_type`. "
        "On success returns the rule's `id`, which you pass as "
        "`alert_rule_id` to `GET` / `DELETE /api/v1/realtime/{alert_rule_id}`.\n\n"

        "### Stream sharing\n"
        "Provide `sensor_id` to share one underlying RTVI stream across "
        "multiple rules:\n"
        "- If RTVI already has a stream registered with that id, it is "
        "reused — no extra connection to the camera.\n"
        "- Otherwise a new stream is opened.\n"
        "- The stream is only torn down when the **last** rule using it "
        "is deleted.\n\n"

        "### How a create is validated\n"
        "1. The rule is persisted (if persistence is enabled).\n"
        "2. The RTVI stream is opened (or reused).\n"
        "3. The service waits briefly for caption generation to start "
        "and, for new streams, polls RTVI until the stream is visible.\n"
        "4. If the RTSP source cannot be opened, you get a `502` with "
        "`rtvi_stream_not_readable` — no silent late failure.\n\n"

        "### When you might get blocked\n"
        "While `POST /api/v1/realtime/replay` is running, this endpoint "
        "returns `503 replay_in_progress`. Retry once the replay "
        "finishes."
    ),
    responses={
        201: {
            "description": "Alert rule created successfully.",
            "model": RealtimeAlertResponse,
        },
        422: {
            "description": (
                "Request rejected before reaching RTVI. Common causes:\n"
                "- Invalid payload (missing field, bad RTSP URL, etc.)\n"
                "- No VLM model resolved — neither `model` in the "
                "request nor `rtvi_vlm.default_model` is set.\n\n"
                "Returned with `error: validation_failed`."
            ),
            "model": RealtimeAlertErrorResponse,
        },
        502: {
            "description": (
                "An upstream system failed. Check `error` to know which:\n"
                "- `rtvi_vlm_unavailable` — RTVI VLM is unreachable or "
                "rejected the request at the HTTP layer.\n"
                "- `rtvi_stream_not_readable` — RTVI accepted the call "
                "but the RTSP source could not be opened in time "
                "(camera offline, bad URL, codec mismatch, ...).\n"
                "- `rtvi_invalid_response` — RTVI accepted the stream "
                "but did not return a stream id; the rule cannot be "
                "managed.\n"
                "- `elasticsearch_write_failed` — failed to persist "
                "the rule to Elasticsearch."
            ),
            "model": RealtimeAlertErrorResponse,
        },
        503: {
            "description": (
                "A replay is currently re-onboarding rules onto RTVI. "
                "New rules cannot be created until it finishes. "
                "Returned with `error: replay_in_progress`."
            ),
            "model": RealtimeAlertErrorResponse,
        },
    },
)
async def create_realtime_alert(
    body: RealtimeAlertRequest,
    service: RealtimeAlertService = Depends(get_realtime_service),
):
    logger.info(
        "POST /api/v1/realtime — live_stream_url=%s alert_type=%s",
        body.live_stream_url,
        body.alert_type,
    )
    config = AlertRuleConfig(
        live_stream_url=body.live_stream_url,
        alert_type=body.alert_type,
        sensor_name=body.sensor_name,
        prompt=body.prompt,
        sensor_id=body.sensor_id,
        description=body.description,
        username=body.username,
        password=body.password,
        place_name=body.place_name,
        place_type=body.place_type,
        place_lat=body.place_lat,
        place_lon=body.place_lon,
        place_alt=body.place_alt,
        place_coordinate_x=body.place_coordinate_x,
        place_coordinate_y=body.place_coordinate_y,
        system_prompt=body.system_prompt,
        model=body.model,
        chunk_duration=body.chunk_duration,
        chunk_overlap_duration=body.chunk_overlap_duration,
        num_frames_per_second_or_fixed_frames_chunk=body.num_frames_per_second_or_fixed_frames_chunk,
        use_fps_for_chunking=body.use_fps_for_chunking,
        vlm_input_width=body.vlm_input_width,
        vlm_input_height=body.vlm_input_height,
        enable_reasoning=body.enable_reasoning,
        api_type=body.api_type,
        response_format=body.response_format,
        stream_options=body.stream_options,
        max_tokens=body.max_tokens,
        temperature=body.temperature,
        top_p=body.top_p,
        top_k=body.top_k,
        ignore_eos=body.ignore_eos,
        seed=body.seed,
        media_info=body.media_info,
        enable_audio=body.enable_audio,
        mm_processor_kwargs=body.mm_processor_kwargs,
    )
    response_data, status_code = await service.start_alert(config)
    return JSONResponse(status_code=status_code, content=response_data)


# ---------------------------------------------------------------------------
# GET /api/v1/realtime
# ---------------------------------------------------------------------------
@router.get(
    "",
    response_model=RealtimeAlertListResponse,
    summary="List active alert rules",
    description="Return all active real-time VLM alert rules.",
    responses={
        200: {"description": "Active alert rules", "model": RealtimeAlertListResponse},
    },
)
async def list_realtime_alerts(
    size: int = Query(
        default=100,
        ge=1,
        le=1000,
        description="Maximum number of rules to return",
    ),
    offset: int = Query(
        default=0,
        ge=0,
        description="Number of rules to skip (for pagination)",
    ),
    service: RealtimeAlertService = Depends(get_realtime_service),
):
    response_data, status_code = await service.list_alerts(
        size=size, from_=offset,
    )
    return JSONResponse(status_code=status_code, content=response_data)


# ---------------------------------------------------------------------------
# DELETE /api/v1/realtime/{alert_rule_id}
# ---------------------------------------------------------------------------
@router.delete(
    "/{alert_rule_id}",
    response_model=RealtimeAlertDeleteResponse,
    summary="Delete an alert rule",
    description=(
        "Delete a real-time VLM alert rule.\n\n"
        "The rule is removed from storage first, so it disappears from "
        "list/get even if RTVI VLM is unreachable.\n\n"

        "### What gets stopped\n"
        "- Caption generation for this rule is **always** stopped.\n"
        "- The shared RTVI stream is stopped **only** if this was the "
        "last rule using it. If other rules still share the stream "
        "(same `sensor_id`), it keeps running for them.\n\n"

        "### When you might get blocked\n"
        "While `POST /api/v1/realtime/replay` is running, this "
        "endpoint returns `503 replay_in_progress`."
    ),
    responses={
        200: {"description": "Alert rule deleted", "model": RealtimeAlertDeleteResponse},
        404: {
            "description": "Alert rule not found (`not_found`).",
            "model": RealtimeAlertErrorResponse,
        },
        422: {
            "description": "Invalid UUID format",
            "model": RealtimeAlertErrorResponse,
        },
        502: {
            "description": (
                "Upstream failure. Possible `error` codes: "
                "`elasticsearch_query_failed` (failed to read the rule "
                "before delete), `elasticsearch_write_failed` (failed "
                "to delete the rule). RTVI teardown failures are "
                "logged but do not surface as 502 — they leave the "
                "stream as a tracked orphan."
            ),
            "model": RealtimeAlertErrorResponse,
        },
        503: {
            "description": (
                "Replay in progress — rule deletion is gated until "
                "`POST /api/v1/realtime/replay` finishes (error code "
                "`replay_in_progress`)."
            ),
            "model": RealtimeAlertErrorResponse,
        },
    },
)
async def delete_realtime_alert(
    alert_rule_id: UUID,
    service: RealtimeAlertService = Depends(get_realtime_service),
):
    logger.info("DELETE /api/v1/realtime/%s", alert_rule_id)
    response_data, status_code = await service.stop_alert(str(alert_rule_id))
    return JSONResponse(status_code=status_code, content=response_data)


# ---------------------------------------------------------------------------
# GET /api/v1/realtime/incidents
# ---------------------------------------------------------------------------
@router.get(
    "/incidents",
    response_model=IncidentListResponse,
    summary="List incidents from Elasticsearch",
    description=(
        "Query incidents from Elasticsearch with optional filtering by sensor_id, "
        "category, and time range. Supports pagination via limit and offset."
    ),
    responses={
        200: {"description": "Incidents list", "model": IncidentListResponse},
        422: {"description": "Invalid timestamp format", "model": RealtimeAlertErrorResponse},
        500: {"description": "Elasticsearch query failed", "model": RealtimeAlertErrorResponse},
        503: {"description": "Elasticsearch unavailable", "model": RealtimeAlertErrorResponse},
    },
)
async def list_incidents(
    sensor_id: Optional[str] = Query(
        default=None,
        description="Filter by sensor ID",
    ),
    category: Optional[str] = Query(
        default=None,
        description="Filter by incident category",
    ),
    start_time: Optional[datetime] = Query(
        default=None,
        description="Filter incidents after this ISO-8601 timestamp (e.g. 2024-01-15T10:30:00Z)",
    ),
    end_time: Optional[datetime] = Query(
        default=None,
        description="Filter incidents before this ISO-8601 timestamp (e.g. 2024-01-15T18:00:00Z)",
    ),
    limit: int = Query(
        default=100,
        ge=1,
        le=1000,
        description="Maximum number of incidents to return",
    ),
    offset: int = Query(
        default=0,
        ge=0,
        description="Number of incidents to skip (for pagination)",
    ),
    service: IncidentService = Depends(get_incident_service),
):
    logger.info(
        "GET /api/v1/realtime/incidents — sensor_id=%s category=%s limit=%d offset=%d",
        sensor_id,
        category,
        limit,
        offset,
    )
    response_data, status_code = await service.list_incidents(
        sensor_id=sensor_id,
        category=category,
        start_time=start_time.isoformat() if start_time else None,
        end_time=end_time.isoformat() if end_time else None,
        limit=limit,
        offset=offset,
    )
    return JSONResponse(status_code=status_code, content=response_data)


# ---------------------------------------------------------------------------
# POST /api/v1/realtime/replay
# ---------------------------------------------------------------------------
@router.post(
    "/replay",
    response_model=RealtimeReplayResponse,
    summary="Replay all persisted active or failed rules onto RTVI VLM",
    description=(
        "Trigger Alert Bridge to re-onboard every persisted active or "
        "failed rule from Elasticsearch onto RTVI VLM. Intended for use "
        "after RTVI VLM restarts. Returns 409 if a replay is already "
        "running. While a replay is in progress, POST and DELETE on "
        "/realtime return 503."
    ),
    responses={
        200: {"description": "Replay completed", "model": RealtimeReplayResponse},
        409: {"description": "Replay already in progress", "model": RealtimeAlertErrorResponse},
        501: {"description": "Persistence not configured", "model": RealtimeAlertErrorResponse},
        502: {"description": "Elasticsearch read failure", "model": RealtimeAlertErrorResponse},
    },
)
async def replay_realtime_alerts(
    service: RealtimeAlertService = Depends(get_realtime_service),
):
    logger.info("POST /api/v1/realtime/replay")
    response_data, status_code = await service.replay()
    return JSONResponse(status_code=status_code, content=response_data)


# ---------------------------------------------------------------------------
# GET /api/v1/realtime/{alert_rule_id}
#
# Declared AFTER /incidents and /replay so FastAPI never mistakes them
# for a UUID path parameter.
# ---------------------------------------------------------------------------
@router.get(
    "/{alert_rule_id}",
    response_model=RealtimeAlertGetResponse,
    summary="Get a single alert rule",
    description=(
        "Retrieve a single real-time VLM alert rule by ID. "
        "Reads from Elasticsearch when persistence is enabled, "
        "otherwise from the in-memory registry."
    ),
    responses={
        200: {"description": "Alert rule", "model": RealtimeAlertGetResponse},
        404: {
            "description": "Alert rule not found (`not_found`).",
            "model": RealtimeAlertErrorResponse,
        },
        422: {
            "description": "Invalid UUID format",
            "model": RealtimeAlertErrorResponse,
        },
        502: {
            "description": (
                "Elasticsearch query failed (`elasticsearch_query_failed`)."
            ),
            "model": RealtimeAlertErrorResponse,
        },
    },
)
async def get_realtime_alert(
    alert_rule_id: UUID,
    service: RealtimeAlertService = Depends(get_realtime_service),
):
    logger.info("GET /api/v1/realtime/%s", alert_rule_id)
    response_data, status_code = await service.get_alert(str(alert_rule_id))
    return JSONResponse(status_code=status_code, content=response_data)


# ---------------------------------------------------------------------------
# POST /api/v1/realtime/always-on
# ---------------------------------------------------------------------------
@router.post(
    "/always-on",
    summary="Start/stop always-on alert rules for an incoming camera event",
    description=(
        "Starts or stops always-on alert rules in response to a "
        "camera lifecycle event.\n\n"
        "**Behavior by `event.change`:**\n\n"
        "- `camera_streaming` — starts one rule per entry in the "
        "always-on rules config. Idempotent per `camera_id`: repeats "
        "return reason `STREAM_ADD_ALREADY_ACTIVE`.\n"
        "- `camera_remove` — stops every rule previously started for "
        "that `camera_id`.\n\n"
        "Responses carry a `reason` code (`STREAM_ADD_SUCCESS`, "
        "`STREAM_ADD_PARTIAL_SUCCESS`, `STREAM_ADD_ALREADY_ACTIVE`, "
        "`STREAM_ADD_FAILED`, or `ALWAYS_ON_DISABLED`) and a "
        "`details` array with one entry per rule."
    ),
    # The handler takes ``payload: Dict[str, Any]`` and validates
    # manually so that 422s can return the custom
    # ``{reason, status, details?}`` envelope SDR consumes (see the
    # comment at the validation site) instead of the app-wide
    # ``RequestValidationError`` envelope declared in ``main.py``.
    # Because FastAPI has no per-route exception handler, this is the
    # idiomatic way to hit a non-standard 422 shape for one endpoint.
    # The requestBody schema is written inline (mirroring
    # ``AlwaysOnEventRequest`` / ``AlwaysOnEvent``) rather than via
    # ``model_json_schema()`` to avoid ``$defs`` JSON Pointer refs
    # that Swagger UI cannot resolve when embedded inside the OpenAPI
    # document root.
    responses={
        200: {
            "description": "Rules started/stopped successfully",
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "reason": {"type": "string"},
                            "status": {"type": "string"},
                            "details": {
                                "type": "array",
                                "items": {"type": "object"},
                            },
                        },
                        "required": ["reason", "status"],
                    },
                    "example": {
                        "reason": "STREAM_ADD_SUCCESS",
                        "status": "HTTP/1.1 200 OK",
                        "details": [
                            {
                                "rule_id": "intrusion-detection",
                                "alert_type": "intrusion",
                                "status": 201,
                                "result": "success",
                                "alert_rule_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
                            }
                        ],
                    },
                }
            },
        },
        422: {
            "description": "Invalid payload",
            "content": {
                "application/json": {
                    "example": {
                        "reason": "INVALID_PAYLOAD",
                        "status": "HTTP/1.1 422 Unprocessable Entity",
                    }
                }
            },
        },
        502: {
            "description": "All rules failed to start on RTVI VLM",
            "content": {
                "application/json": {
                    "example": {
                        "reason": "STREAM_ADD_FAILED",
                        "status": "HTTP/1.1 502 Bad Gateway",
                        "details": [
                            {
                                "rule_id": "intrusion-detection",
                                "alert_type": "intrusion",
                                "status": 502,
                                "result": "error",
                                "error": {"message": "RTVI VLM unreachable"},
                            }
                        ],
                    }
                }
            },
        },
        503: {
            "description": "Always-on feature disabled or rules config missing",
            "content": {
                "application/json": {
                    "example": {
                        "reason": "ALWAYS_ON_DISABLED",
                        "status": "HTTP/1.1 503 Service Unavailable",
                    }
                }
            },
        },
    },
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {
                    "schema": _flatten_json_schema(AlwaysOnEventRequest.model_json_schema()),
                    "examples": {
                        "camera_streaming": {
                            "summary": "Start always-on rules for a new camera",
                            "value": {
                                "source": "vst",
                                "alert_type": "camera_status_change",
                                "created_at": "2026-04-22T17:38:38Z",
                                "event": {
                                    "camera_id": "c0413489-6ca1-422e-a09c-08224169ff6a",
                                    "camera_name": "warehouse",
                                    "camera_url": "rtsp://localhost:8554/live/c0413489-6ca1-422e-a09c-08224169ff6a",
                                    "camera_vod_url": "rtsp://localhost:8554/vod/c0413489-6ca1-422e-a09c-08224169ff6a",
                                    "change": "camera_streaming",
                                    "metadata": {"codec": "H264"},
                                },
                            },
                        },
                        "camera_remove": {
                            "summary": "Tear down rules for a camera going offline",
                            "value": {
                                "source": "vst",
                                "event": {
                                    "camera_id": "c0413489-6ca1-422e-a09c-08224169ff6a",
                                    "change": "camera_remove",
                                },
                            },
                        },
                    },
                }
            },
        }
    },
)
async def always_on_realtime(
    payload: Dict[str, Any],
    service: AlwaysOnService = Depends(get_always_on_service),
):
    # ── Feature gate ─────────────────────────────────────────────────
    # The endpoint is opt-in via ``alert_agent.always_on`` in config.yaml
    # so operators can roll the feature back without a code change.
    # Check first, before any logging of the payload, so a disabled
    # endpoint doesn't log or parse incoming events at all.
    if not service.enabled:
        return _always_on_response(
            status_code=503,
            reason=AlwaysOnReason.DISABLED,
        )

    logger.info(
        "POST /api/v1/realtime/always-on — payload=%s",
        json.dumps(payload, default=str),
    )
    logger.debug(
        "always-on payload (pretty):\n%s",
        json.dumps(payload, indent=2, default=str),
    )

    # Validate the request body through the Pydantic model. We invoke it
    # here — rather than declaring it as the route signature type — so
    # that validation failures surface with the custom
    # ``INVALID_PAYLOAD`` response shape SDR expects, instead of
    # FastAPI's default 422 ``detail`` envelope.
    try:
        request = AlwaysOnEventRequest.model_validate(payload)
    except ValidationError as exc:
        logger.warning(
            "always-on payload validation failed: %s",
            exc.errors(include_url=False),
        )
        return _always_on_response(
            status_code=422,
            reason=AlwaysOnReason.INVALID_PAYLOAD,
        )

    event = request.event
    logger.info(
        "always-on event: camera_id=%s camera_name=%s camera_url=%s change=%s",
        event.camera_id, event.camera_name, event.camera_url, event.change,
    )

    if event.change == "camera_remove":
        result = await service.stop_camera(camera_id=event.camera_id)
        return _always_on_response(
            status_code=result.status_code,
            reason=result.reason,
            details=result.details,
        )

    # ``camera_streaming`` path. ``camera_url`` and ``camera_name`` are
    # marked optional on the model (they're ignored for
    # ``camera_remove``), so we enforce them here for the add path.
    if not (event.camera_url and event.camera_name):
        logger.warning(
            "camera_streaming missing camera_url/camera_name for camera_id=%s",
            event.camera_id,
        )
        return _always_on_response(
            status_code=422,
            reason=AlwaysOnReason.INVALID_PAYLOAD,
        )

    result = await service.start_camera(
        camera_id=event.camera_id,
        camera_url=event.camera_url,
        camera_name=event.camera_name,
    )
    return _always_on_response(
        status_code=result.status_code,
        reason=result.reason,
        details=result.details,
    )
