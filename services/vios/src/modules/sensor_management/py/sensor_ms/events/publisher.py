# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Notification event publisher — camera_add / camera_remove / camera_streaming / camera_proxy.

Payload shape and semantics: DESIGN.md §6.3 (vst_common.cpp:1169-1232, sensor_management.cpp:499).

VERIFIED against a live deployment (2026-06-09), Redis backend:
  - Transport: Redis Stream. `XADD <message_broker_topic> * <message_broker_payload_key> <json>`.
    Observed stream key = "vst_events" (REDIS_MSG_KEY), field name = "sensor.id" (payload key).
  - Serialization: jsoncpp -> keys sorted ALPHABETICALLY, compact (no spaces). serialize_event()
    reproduces this with json.dumps(sort_keys=True, separators=(",", ":")).
  - created_at: ISO8601 UTC, second precision, e.g. "2026-06-09T07:49:54Z".
  - camera_add / camera_remove carry NO metadata and camera_url="" (add) or "" (remove).
    camera_proxy carries metadata{codec,framerate,resolution} and camera_url WITH credentials.

TODO(P2): implement redis/kafka/mqtt backends; build_payload/serialize_event are contract-locked.
"""
from __future__ import annotations

import json
from enum import Enum
from typing import Any

from ..config import Config


class ChangeEvent(str, Enum):
    camera_remove = "camera_remove"        # SensorStatusOffline (0)
    camera_add = "camera_add"              # SensorStatusOnline (1)
    camera_streaming = "camera_streaming"  # SensorStatusStreaming (2)
    camera_proxy = "camera_proxy"          # SensorStatusProxy (3)


def build_payload(
    *,
    change: ChangeEvent,
    camera_id: str,
    camera_name: str,
    camera_url: str,
    tags: str,
    created_at: str,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Construct the event body. metadata is omitted for camera_remove (per C++)."""
    event: dict[str, Any] = {
        "camera_id": camera_id,
        "camera_name": camera_name,
        "camera_url": camera_url,
        "change": change.value,
        "tags": tags,
    }
    if change is not ChangeEvent.camera_remove and metadata:
        event["metadata"] = metadata
    return {
        "created_at": created_at,
        "source": "vst",
        "alert_type": "camera_status_change",
        "event": event,
    }


def serialize_event(payload: dict[str, Any]) -> str:
    """Serialize to the exact wire bytes the C++ jsoncpp path emits: alphabetically sorted keys,
    compact separators (no spaces). Verified byte-identical to live Redis-stream entries."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def resolve_redis_host_port(cfg: Config) -> tuple[str, int]:
    """Resolve redis (host, port) from cfg.redis_server_env_var, format "ENVVARNAME:port".

    The host is read from the named environment variable (falling back to the literal token if the
    env var is unset, which is what local/dev deployments rely on). Mirrors the C++ resolution.
    """
    import os

    token = cfg.redis_server_env_var or "localhost:6379"
    name, _, port_s = token.partition(":")
    host = os.environ.get(name, name)
    try:
        port = int(port_s) if port_s else 6379
    except ValueError:
        port = 6379
    return host, port


class EventPublisher:
    """Broker-agnostic publisher. One broker active at a time (NotificationFactory singleton).

    Redis backend implemented (XADD stream). kafka/mqtt are TODO(P2).
    """

    def __init__(self, cfg: Config):
        self._cfg = cfg
        self._redis = None

    @property
    def enabled(self) -> bool:
        return self._cfg.enable_notification and bool(self._cfg.use_message_broker)

    def _redis_client(self):
        if self._redis is None:
            import redis  # lazy import; only needed when redis backend is active

            host, port = resolve_redis_host_port(self._cfg)
            self._redis = redis.Redis(host=host, port=port, socket_connect_timeout=5)
        return self._redis

    async def publish(self, payload: dict[str, Any]) -> None:
        if not self.enabled:
            return
        body = serialize_event(payload)
        backend = self._cfg.use_message_broker.lower()
        if backend == "redis":
            # XADD <topic> * <payload_key> <json>  — matches the C++ nvds_msgapi redis stream path.
            self._redis_client().xadd(
                self._cfg.message_broker_topic, {self._cfg.message_broker_payload_key: body}
            )
        else:
            raise NotImplementedError(f"event backend '{backend}' not implemented (P2)")
