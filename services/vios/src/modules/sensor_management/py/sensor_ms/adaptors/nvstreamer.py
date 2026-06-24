# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""nvstreamer stream discovery (vst_rtsp adaptor parity).

The C++ vst_rtsp adaptor (rtsp_streams.cpp) GETs the nvstreamer stream list and registers each
stream. nvstreamer exposes GET <endpoint>/api/v1/sensor/streams returning a StreamInfoWrapper array:
    [ { "<streamId>": [ { "isMain", "name", "url", "type", "metadata": {...} }, ... ] }, ... ]
This module fetches and flattens that into per-sensor dicts the orchestrator registers as
sensor_rtsp sensors. Synchronous (stdlib urllib); call via asyncio.to_thread to avoid blocking.
"""
from __future__ import annotations

import json
import logging
import urllib.request
from typing import Any

log = logging.getLogger(__name__)


def _base(endpoint: str) -> str:
    ep = endpoint.strip().rstrip("/")
    if not ep.startswith("http://") and not ep.startswith("https://"):
        ep = "http://" + ep
    return ep


def fetch_streams(endpoint: str, timeout: float = 5.0,
                  api: str = "/api/v1/sensor/streams", max_count: int | None = None) -> list[dict[str, Any]]:
    """Query one nvstreamer endpoint and return [{sensorId, name, url, metadata}] for its main streams.

    `api` is the path to GET (rtsp_streams.json Nvstreamer.api) and `max_count` caps how many streams
    are returned (rtsp_streams.json Nvstreamer.max_stream_count). Raises on connection/HTTP error
    (caller logs and skips). Each entry uses the nvstreamer's stable streamId as the sensor id so
    re-polling is idempotent and recordings survive delete+readd.
    """
    url = _base(endpoint) + (api or "/api/v1/sensor/streams")
    with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310 (trusted internal endpoint)
        data = json.loads(resp.read().decode("utf-8"))
    out: list[dict[str, Any]] = []
    if not isinstance(data, list):
        return out
    for entry in data:
        if not isinstance(entry, dict):
            continue
        for sensor_id, streams in entry.items():
            if not streams:
                continue
            main = next((s for s in streams if s.get("isMain")), streams[0])
            out.append({
                "sensorId": sensor_id,
                "name": main.get("name") or sensor_id,
                "url": main.get("url", "") or "",
                "metadata": main.get("metadata", {}) or {},
            })
            if max_count and len(out) >= int(max_count):
                return out
    return out
