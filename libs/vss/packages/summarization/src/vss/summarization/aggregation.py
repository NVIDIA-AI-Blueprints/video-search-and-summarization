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

"""Deterministic native aggregation for VLM chunk responses."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from typing import Any

from vss.ingestion import ChunkInfo, VlmChunkResponse

_JSON_FENCE = re.compile(
    r"^\s*```(?:json)?\s*(.*?)\s*```\s*$",
    flags=re.DOTALL | re.IGNORECASE,
)


def aggregate_events(
    chunks: list[VlmChunkResponse],
    *,
    overlap_threshold: float = 0.1,
    adjacent_threshold: float = 4.0,
) -> list[dict[str, Any]]:
    """Normalize RTVI events and merge related temporal observations."""

    if overlap_threshold < 0 or adjacent_threshold < 0:
        raise ValueError("aggregation thresholds must not be negative")

    usable = [chunk for chunk in chunks if chunk.error is None and chunk.vlm_response]
    usable.sort(key=_chunk_sort_key)

    normalized: list[dict[str, Any]] = []
    for response in usable:
        chunk = response.chunk or ChunkInfo()
        for event in _events_from_caption(response.vlm_response or ""):
            normalized_event = _normalize_event(event, chunk)
            if normalized_event is not None:
                normalized.append(normalized_event)

    normalized.sort(key=lambda event: event["start_time"])
    merged = _merge_events(
        normalized,
        overlap_threshold=overlap_threshold,
        adjacent_threshold=adjacent_threshold,
    )

    for index, event in enumerate(merged, start=1):
        event["id"] = index
    return merged


def _events_from_caption(caption: str) -> list[dict[str, Any]]:
    candidate = caption.strip()
    fenced = _JSON_FENCE.match(candidate)
    if fenced:
        candidate = fenced.group(1)
    elif boundaries := _json_boundaries(candidate):
        candidate = candidate[boundaries[0] : boundaries[1] + 1]

    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError:
        return [{"type": "caption", "description": caption.strip()}]

    if isinstance(parsed, list):
        return [_event_dict(item) for item in parsed]
    if isinstance(parsed, dict):
        events = parsed.get("events")
        if isinstance(events, list):
            return [_event_dict(item) for item in events]
        return [_event_dict(parsed)]
    return [{"description": str(parsed)}]


def _event_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    return {"description": str(value)}


def _normalize_event(event: dict[str, Any], chunk: ChunkInfo) -> dict[str, Any] | None:
    chunk_start = _chunk_start_value(chunk)
    chunk_end = _chunk_end_value(chunk)
    start = _timestamp(event.get("start_time"), default=chunk_start)
    end = _timestamp(event.get("end_time"), default=chunk_end)
    if start >= end:
        start, end = chunk_start, chunk_end
    if start >= end:
        return None

    return {
        "start_time": start,
        "end_time": end,
        "type": _event_type(event),
        "description": _event_description(event),
        "evidence": [
            {
                "chunk_idx": chunk.chunkIdx,
                "source_id": chunk.sourceId,
            }
        ],
    }


def _merge_events(
    events: list[dict[str, Any]],
    *,
    overlap_threshold: float,
    adjacent_threshold: float,
) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    for event in events:
        candidate = next(
            (
                existing
                for existing in reversed(merged)
                if _types_merge(existing, event)
                and _temporally_related(
                    existing,
                    event,
                    overlap_threshold=overlap_threshold,
                    adjacent_threshold=adjacent_threshold,
                )
            ),
            None,
        )
        if candidate is None:
            merged.append(event)
            continue

        candidate["start_time"] = min(candidate["start_time"], event["start_time"])
        candidate["end_time"] = max(candidate["end_time"], event["end_time"])
        if _dedupe_key(candidate) != _dedupe_key(event):
            candidate["description"] += f" | {event['description']}"
        candidate["evidence"].extend(event["evidence"])

    merged.sort(key=lambda event: event["start_time"])
    return merged


def _types_merge(first: dict[str, Any], second: dict[str, Any]) -> bool:
    if first["type"] != second["type"]:
        return False
    if first["type"] in {"caption", "event"}:
        return _dedupe_key(first) == _dedupe_key(second)
    return True


def _temporally_related(
    first: dict[str, Any],
    second: dict[str, Any],
    *,
    overlap_threshold: float,
    adjacent_threshold: float,
) -> bool:
    overlap = min(first["end_time"], second["end_time"]) - max(
        first["start_time"], second["start_time"]
    )
    if overlap > 0:
        return overlap >= overlap_threshold
    gap = max(
        second["start_time"] - first["end_time"],
        first["start_time"] - second["end_time"],
    )
    return gap <= adjacent_threshold


def _json_boundaries(content: str) -> tuple[int, int] | None:
    candidates = [
        (content.find("["), content.rfind("]")),
        (content.find("{"), content.rfind("}")),
    ]
    valid = [(start, end) for start, end in candidates if end > start >= 0]
    return min(valid, key=lambda pair: pair[0]) if valid else None


def _timestamp(value: Any, *, default: float) -> float:
    if value is None or value == "":
        return default
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            normalized = value.replace("Z", "+00:00") if value.endswith("Z") else value
            try:
                parsed = datetime.fromisoformat(normalized)
            except ValueError:
                return default
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=UTC)
            return parsed.timestamp()
    return default


def _event_type(event: dict[str, Any]) -> str:
    value = event.get("type")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return "event"


def _event_description(event: dict[str, Any]) -> str:
    for key in ("description", "caption", "content", "event", "summary"):
        value = event.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return json.dumps(event, sort_keys=True, separators=(",", ":"))


def _dedupe_key(event: dict[str, Any]) -> str:
    return " ".join(event["description"].casefold().split())


def _chunk_sort_key(response: VlmChunkResponse) -> tuple[int, float | str]:
    chunk = response.chunk
    if chunk is None:
        return (2, "")
    if chunk.start_ntp:
        return (1, chunk.start_ntp)
    return (0, chunk.start_pts / 1_000_000_000)


def _chunk_start_value(chunk: ChunkInfo) -> float:
    if chunk.start_ntp:
        return _timestamp(chunk.start_ntp, default=0.0)
    return chunk.start_pts / 1_000_000_000


def _chunk_end_value(chunk: ChunkInfo) -> float:
    if chunk.end_ntp:
        return _timestamp(chunk.end_ntp, default=0.0)
    return chunk.end_pts / 1_000_000_000
