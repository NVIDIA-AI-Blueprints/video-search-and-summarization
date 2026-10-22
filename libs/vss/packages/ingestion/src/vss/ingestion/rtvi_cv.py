# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

"""Private REST and Kafka transport for the optional RTVI-CV backend."""

from __future__ import annotations

import asyncio
import importlib
import json
import os
from datetime import datetime
from typing import Any, Protocol

import httpx

from .contracts import BoundingBox, CvDetection


class _RTVICvTransportError(Exception):
    pass


class _CvMessageConsumer(Protocol):
    async def start(self) -> None: ...
    async def receive(self, timeout_s: float) -> dict[str, Any] | None: ...
    async def aclose(self) -> None: ...


class _ConfluentKafkaConsumer:
    def __init__(
        self,
        *,
        source_id: str,
        bootstrap_servers: str,
        topic: str,
    ) -> None:
        try:
            kafka = importlib.import_module("confluent_kafka")
        except ImportError as error:
            raise _RTVICvTransportError(
                "rtvi_cv requires the optional confluent-kafka dependency"
            ) from error
        self._topic = topic
        self._consumer = kafka.Consumer(
            {
                "bootstrap.servers": bootstrap_servers,
                "group.id": f"vss-rtvi-cv-{source_id}",
                "auto.offset.reset": "latest",
                "enable.auto.commit": False,
            }
        )

    async def start(self) -> None:
        self._consumer.subscribe([self._topic])
        deadline = asyncio.get_running_loop().time() + 10
        while not self._consumer.assignment():
            await asyncio.to_thread(self._consumer.poll, 0.1)
            if asyncio.get_running_loop().time() >= deadline:
                raise _RTVICvTransportError("Kafka consumer assignment timed out")

    async def receive(self, timeout_s: float) -> dict[str, Any] | None:
        message = await asyncio.to_thread(self._consumer.poll, timeout_s)
        if message is None:
            return None
        if message.error():
            raise _RTVICvTransportError(f"Kafka consumer error: {message.error()}")
        try:
            payload = json.loads(message.value())
        except (TypeError, ValueError) as error:
            raise _RTVICvTransportError(
                "RTVI-CV returned malformed Kafka JSON"
            ) from error
        if not isinstance(payload, dict):
            raise _RTVICvTransportError("RTVI-CV Kafka payload must be an object")
        return payload

    async def aclose(self) -> None:
        await asyncio.to_thread(self._consumer.close)


class _RTVICvTransport:
    def __init__(
        self,
        base_url: str | None = None,
        *,
        client: httpx.AsyncClient | None = None,
        timeout_s: float = 30,
        bootstrap_servers: str | None = None,
        topic: str | None = None,
    ) -> None:
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            base_url=(
                base_url or os.getenv("RTVI_CV_BASE_URL") or "http://127.0.0.1:9000"
            ).rstrip("/"),
            timeout=httpx.Timeout(timeout_s),
        )
        self._bootstrap_servers = (
            bootstrap_servers
            or os.getenv("RTVI_CV_KAFKA_BOOTSTRAP_SERVERS")
            or "127.0.0.1:9092"
        )
        self._topic = topic or os.getenv("RTVI_CV_KAFKA_TOPIC") or "ds-perception"

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def check_health(self) -> bool:
        try:
            response = await self._client.get("/api/v1/ready")
            return response.status_code == httpx.codes.OK
        except httpx.HTTPError:
            return False

    def create_consumer(self, source_id: str) -> _CvMessageConsumer:
        return _ConfluentKafkaConsumer(
            source_id=source_id,
            bootstrap_servers=self._bootstrap_servers,
            topic=self._topic,
        )

    async def add_shared_stream(
        self,
        *,
        source_id: str,
        camera_id: str,
        frame_source: dict[str, Any],
    ) -> None:
        await self._request(
            "POST",
            "/api/v1/stream/add",
            json={
                "key": "sensor",
                "value": {
                    "camera_id": camera_id or source_id,
                    "camera_name": camera_id or source_id,
                    "frame_source": frame_source,
                    "change": "camera_add",
                },
            },
        )

    async def stream_status(self, camera_id: str) -> dict[str, Any]:
        response = await self._request("GET", f"/api/v1/stream/status/{camera_id}")
        try:
            body = response.json()
        except ValueError as error:
            raise _RTVICvTransportError(
                "RTVI-CV returned malformed stream status JSON"
            ) from error
        if not isinstance(body, dict):
            raise _RTVICvTransportError("RTVI-CV returned invalid stream status")
        return body

    async def remove_shared_stream(self, camera_id: str) -> None:
        await self._request(
            "POST",
            "/api/v1/stream/remove",
            json={
                "key": "sensor",
                "value": {
                    "camera_id": camera_id,
                    "change": "camera_remove",
                },
            },
        )

    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        try:
            response = await self._client.request(method, path, **kwargs)
            response.raise_for_status()
            return response
        except httpx.HTTPError as error:
            raise _RTVICvTransportError("RTVI-CV request failed") from error


def _payload_sensor_id(payload: dict[str, Any]) -> str | None:
    value = payload.get("sensorId", payload.get("sensor_id"))
    if value is None and isinstance(payload.get("place"), dict):
        value = payload["place"].get("id")
    return str(value) if value is not None else None


def _payload_timestamp_ns(payload: dict[str, Any]) -> int | None:
    """Return the latest timestamp represented by one source result payload."""

    timestamps = [_timestamp_ns(payload, payload, -1)]
    objects = payload.get("objects")
    if isinstance(objects, list):
        timestamps.extend(
            _timestamp_ns(item, payload, -1)
            for item in objects
            if isinstance(item, dict)
        )
    latest = max(timestamps, default=-1)
    return latest if latest >= 0 else None


def _parse_cv_detections(
    payload: dict[str, Any], *, fallback_timestamp_ns: int = 0
) -> list[CvDetection]:
    raw_objects = payload.get("objects")
    if not isinstance(raw_objects, list):
        raw_objects = [payload]
    detections: list[CvDetection] = []
    for raw in raw_objects:
        if not isinstance(raw, dict):
            continue
        timestamp_ns = _timestamp_ns(raw, payload, fallback_timestamp_ns)
        bbox = _bbox(raw)
        confidence = raw.get("confidence")
        if not isinstance(confidence, int | float) or isinstance(confidence, bool):
            confidence = None
        embedding = raw.get("embedding")
        if not isinstance(embedding, list):
            embedding = None
        frame_id = raw.get("frameId", payload.get("frameId"))
        if not isinstance(frame_id, int) or isinstance(frame_id, bool) or frame_id < 0:
            frame_id = None
        detections.append(
            CvDetection(
                timestamp_ns=timestamp_ns,
                frame_id=frame_id,
                tracking_id=_optional_text(
                    raw.get("trackingId", raw.get("tracking_id"))
                ),
                object_id=_optional_text(raw.get("objectId", raw.get("id"))),
                class_label=_optional_text(
                    raw.get("class", raw.get("label", raw.get("type")))
                ),
                confidence=float(confidence) if confidence is not None else None,
                bbox=bbox,
                embedding=embedding,
            )
        )
    return detections


def _timestamp_ns(item: dict[str, Any], payload: dict[str, Any], fallback: int) -> int:
    for key in ("source_pts_ns", "pts_ns", "timestamp_ns"):
        value = item.get(key, payload.get(key))
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            return value
    text = item.get(
        "@timestamp",
        item.get("ts", payload.get("@timestamp", payload.get("ts"))),
    )
    if isinstance(text, str):
        try:
            instant = datetime.fromisoformat(text.replace("Z", "+00:00"))
            return max(0, round(instant.timestamp() * 1_000_000_000))
        except ValueError:
            pass
    return fallback


def _bbox(item: dict[str, Any]) -> BoundingBox | None:
    raw = item.get("bbox")
    if isinstance(raw, dict):
        values = (
            raw.get("left", raw.get("x")),
            raw.get("top", raw.get("y")),
            raw.get("width", raw.get("w")),
            raw.get("height", raw.get("h")),
        )
    elif isinstance(raw, list | tuple) and len(raw) == 4:
        values = tuple(raw)
    else:
        return None
    if any(
        isinstance(value, bool) or not isinstance(value, int | float)
        for value in values
    ):
        return None
    numbers = [float(value) for value in values if isinstance(value, int | float)]
    return BoundingBox(
        left=max(0.0, numbers[0]),
        top=max(0.0, numbers[1]),
        width=max(0.0, numbers[2]),
        height=max(0.0, numbers[3]),
    )


def _optional_text(value: Any) -> str | None:
    return str(value) if value is not None else None
