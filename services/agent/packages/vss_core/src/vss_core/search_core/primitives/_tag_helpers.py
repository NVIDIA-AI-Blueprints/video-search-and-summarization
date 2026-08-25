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
"""Pure Elasticsearch query and hit-normalization helpers for VLM tag search."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC
from datetime import datetime
import json
import math
from typing import TYPE_CHECKING
from typing import Any

from .._internal.coerce import _coerce_float
from .._internal.coerce import _coerce_str

if TYPE_CHECKING:
    from ..models.tag_search import TagSearchInput

META_PREFIX = "metadata.content_metadata"
_MAX_TAGS = 32


@dataclass(frozen=True, slots=True)
class ParsedTagHit:
    """Validated fields extracted from one Elasticsearch hit."""

    video_name: str
    description: str
    start_time: str
    end_time: str
    sensor_id: str
    stream_id: str
    lexical_score: float
    tags: list[str]


def build_es_query(
    inp: TagSearchInput,
    *,
    source_ids: list[str] | None,
    default_max_results: int,
) -> dict[str, Any]:
    """Build a BM25 query with source, identity, and interval-overlap filters."""
    filters: list[dict[str, Any]] = [
        {
            "bool": {
                "should": [
                    {"term": {f"{META_PREFIX}.doc_type.keyword": "raw_events"}},
                    {"term": {f"{META_PREFIX}.doc_type": "raw_events"}},
                ],
                "minimum_should_match": 1,
            }
        },
    ]

    source_values = ["Camera", "camera", "rtsp"] if inp.source_type == "rtsp" else ["Video", "video", "video_file"]
    filters.append(
        {
            "bool": {
                "should": [
                    {"terms": {"sensor.type.keyword": source_values}},
                    {"terms": {"sensor.type": source_values}},
                ],
                "minimum_should_match": 1,
            }
        }
    )

    if source_ids:
        identity_fields = (
            f"{META_PREFIX}.sensorId.keyword",
            f"{META_PREFIX}.sensorId",
            f"{META_PREFIX}.cameraId.keyword",
            f"{META_PREFIX}.cameraId",
            f"{META_PREFIX}.streamId.keyword",
            f"{META_PREFIX}.streamId",
            "sensor.id.keyword",
            "sensor.id",
        )
        filters.append(
            {
                "bool": {
                    "should": [{"terms": {field: source_ids}} for field in identity_fields],
                    "minimum_should_match": 1,
                }
            }
        )

    if inp.timestamp_end is not None:
        filters.append({"range": {f"{META_PREFIX}.start_ntp_float": {"lte": inp.timestamp_end.timestamp()}}})
    if inp.timestamp_start is not None:
        filters.append({"range": {f"{META_PREFIX}.end_ntp_float": {"gte": inp.timestamp_start.timestamp()}}})

    requested = inp.top_k or default_max_results
    # Corrupt legacy documents are skipped after retrieval. Bounded overfetch
    # keeps one malformed row from unnecessarily reducing a useful result set.
    size = min(requested * 5, 1000)
    return {
        "size": size,
        "query": {
            "bool": {
                "must": [{"match": {"text": {"query": inp.query.strip(), "operator": "or"}}}],
                "filter": filters,
            }
        },
    }


def _epoch_float(value: Any) -> float:
    if value is None or isinstance(value, bool):
        raise ValueError("tag timestamp must be numeric")
    try:
        timestamp = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("tag timestamp must be numeric") from exc
    if not math.isfinite(timestamp):
        raise ValueError("tag timestamp must be finite")
    if timestamp < 0:
        raise ValueError("tag timestamp must be non-negative")
    return timestamp


def _iso_from_epoch(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, tz=UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _parse_tag_payload(raw_text: Any) -> tuple[list[str], str]:
    if not isinstance(raw_text, str):
        raise ValueError("tag document text must be a JSON string")
    normalized = raw_text.strip()
    if normalized.startswith("```") and normalized.endswith("```"):
        first_newline = normalized.find("\n")
        if first_newline < 0:
            raise ValueError("fenced tag document must contain JSON")
        normalized = normalized[first_newline + 1 : -3].strip()
    payload = json.loads(normalized)
    if not isinstance(payload, dict) or "tags" not in payload or set(payload) - {"tags", "description"}:
        raise ValueError("tag document must contain tags and may contain description")
    raw_tags = payload["tags"]
    if not isinstance(raw_tags, list) or len(raw_tags) > _MAX_TAGS:
        raise ValueError("tag document tags must be a list with at most 32 items")
    tags: list[str] = []
    for value in raw_tags:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("each tag must be a non-empty string")
        tag = value.strip().lower()
        if tag not in tags:
            tags.append(tag)
    if not tags:
        raise ValueError("tag document must contain at least one tag")
    raw_description = payload.get("description", "")
    if not isinstance(raw_description, str):
        raise ValueError("tag document description must be a string")
    description = raw_description.strip()
    if "description" in payload and not description:
        raise ValueError("tag document description must be non-empty when provided")
    return tags, description


def parse_hit(hit: dict[str, Any], *, sensor_names: dict[str, str]) -> ParsedTagHit:
    """Validate one indexed raw-event tag hit.

    Any shape or tag-contract problem raises ``ValueError``/``TypeError`` and is
    handled per-document by the primitive.
    """
    source = hit["_source"]
    if not isinstance(source, dict):
        raise TypeError("_source must be an object")
    metadata = source.get("metadata")
    if not isinstance(metadata, dict):
        raise TypeError("metadata must be an object")
    content = metadata.get("content_metadata")
    if not isinstance(content, dict):
        raise TypeError("content_metadata must be an object")

    tags, description = _parse_tag_payload(source.get("text"))
    sensor = source.get("sensor")
    sensor_data = sensor if isinstance(sensor, dict) else {}
    stream_id = _coerce_str(content.get("streamId"))
    sensor_id = (
        _coerce_str(content.get("sensorId"))
        or _coerce_str(content.get("cameraId"))
        or _coerce_str(sensor_data.get("id"))
        or stream_id
    )
    if not sensor_id:
        raise ValueError("tag document has no sensor identity")

    start_epoch = _epoch_float(content.get("start_ntp_float"))
    end_epoch = _epoch_float(content.get("end_ntp_float"))
    if end_epoch < start_epoch:
        raise ValueError("tag document end precedes start")
    start_time = _iso_from_epoch(start_epoch)
    end_time = _iso_from_epoch(end_epoch)

    source_name = _coerce_str(metadata.get("source"))
    if source_name in {"", "N/A"}:
        source_name = sensor_names.get(stream_id, "") or sensor_names.get(sensor_id, "")
    video_name = (
        source_name or _coerce_str(sensor_data.get("name")) or _coerce_str(sensor_data.get("description")) or sensor_id
    )

    return ParsedTagHit(
        video_name=video_name,
        description=description or ", ".join(tags),
        start_time=start_time,
        end_time=end_time,
        sensor_id=sensor_id,
        stream_id=stream_id or sensor_id,
        lexical_score=_coerce_float(hit.get("_score")),
        tags=tags,
    )
