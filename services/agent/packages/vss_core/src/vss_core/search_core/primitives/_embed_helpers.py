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
"""Pure, dependency-free helpers for EmbedSearch.

Every function here is synchronous and side-effect-free: it transforms plain
dicts/strings into plain dicts/strings. This keeps the orchestration in
``embed_search.py`` thin and makes the hard logic (ES query construction, hit
extraction) unit-testable without async, mocks, or live backends.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC
from datetime import datetime
import json
import re
from typing import TYPE_CHECKING
from typing import Any

from vss_core._foundation.time import datetime_to_iso8601
from vss_core._foundation.time import iso8601_instants_match
from vss_core._foundation.time import safe_iso8601_to_datetime

from .._internal.es_filters import build_video_sources_filter
from .._internal.es_filters import escape_wildcard
from .._internal.uuid_string import is_standard_uuid_string

if TYPE_CHECKING:
    from ..models.embed_search import EmbedSearchInput

# Fallback timestamp for documents that have no usable start/end time, so a
# missing field still yields a well-formed (if synthetic) result rather than
# failing the whole response.
FALLBACK_TIMESTAMP = datetime(2025, 1, 1, tzinfo=UTC)

# Regex for extracting a UUID substring from a longer path. The plain
# is/isn't-a-UUID predicate is ``is_standard_uuid_string``; this is only for
# pulling a stream id out of ``sensor.info.path``.
_UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.IGNORECASE)


# =============================================================================
# Index selection
# =============================================================================


def select_search_index(video_embed_index_wildcard: str) -> str:
    """Return the embed index pattern to query, for any source type.

    Source-type partitioning is a positive document filter on ``sensor.type``
    (see :func:`build_source_type_filter`), not index-name arithmetic, so both
    ``video_file`` and ``rtsp`` query the same ``mdx-embed-filtered-*`` wildcard
    and the ``sensor.type`` term does the separating.

    This replaces the former ``video_file -> base`` / ``rtsp -> wildcard - base``
    scheme, which inferred the partition from a discovered "uploads base" index
    and silently inverted in stream-first / single-index deployments: a live-only
    stack has exactly one embed index, so ``wildcard - base`` excluded the only
    index holding data and ``rtsp`` returned nothing.
    """
    return video_embed_index_wildcard


# =============================================================================
# ES query construction
# =============================================================================


# The RT-Embed publisher records each document's media kind at ingest as
# ``sensor.type`` ("Camera" for live RTSP, "Video" for an uploaded file). These
# are the only two values the ``source_type`` request field maps onto.
_SOURCE_TYPE_TO_SENSOR_TYPE = {"rtsp": "Camera", "video_file": "Video"}


def build_source_type_filter(source_type: str) -> dict[str, Any] | None:
    """Build the ES term filter selecting documents by media source type.

    Filters positively on the document's own ``sensor.type`` field, which is
    what partitions embed results by source. Returns None for an unrecognized
    ``source_type`` so the query stays unpartitioned rather than matching
    nothing.
    """
    sensor_type = _SOURCE_TYPE_TO_SENSOR_TYPE.get(source_type)
    if sensor_type is None:
        return None
    return {"term": {"sensor.type.keyword": sensor_type}}


def build_description_filter(description: str | None) -> dict[str, Any] | None:
    """Build the ES filter clause matching ``sensor.description``."""
    if not description:
        return None
    escaped_desc = escape_wildcard(description)
    regex_escaped_desc = re.escape(description)
    return {
        "bool": {
            "should": [
                {"match": {"sensor.description": description}},
                {"wildcard": {"sensor.description.keyword": f"*{escaped_desc}*"}},
                {"wildcard": {"sensor.description.keyword": f"*{escaped_desc}"}},
                {"regexp": {"sensor.description": f".*{regex_escaped_desc}.*"}},
                {"regexp": {"sensor.description.keyword": f".*{regex_escaped_desc}.*"}},
            ],
            "minimum_should_match": 1,
        }
    }


def build_timestamp_filter(
    timestamp_start: datetime | None,
    timestamp_end: datetime | None,
) -> dict[str, Any] | None:
    """Build the ES range filter for the result time window using OVERLAP semantics.

    A segment ``[timestamp, end]`` overlaps the requested window when its
    ``end >= start`` and its ``timestamp <= end`` — so a segment that straddles a
    window boundary still matches. This mirrors the attribute path
    (``_attribute_helpers.build_behavior_overlap_filter``); previously this used
    CONTAINMENT (``timestamp >= start AND end <= end``), which silently dropped
    straddling segments.
    """
    if not timestamp_start and not timestamp_end:
        return None
    must: list[dict[str, Any]] = []
    if timestamp_start:
        must.append({"range": {"end": {"gte": timestamp_start.isoformat()}}})
    if timestamp_end:
        must.append({"range": {"timestamp": {"lte": timestamp_end.isoformat()}}})
    return {"bool": {"must": must}} if len(must) > 1 else must[0]


def compute_k_value(
    top_k: int | None,
    *,
    default_max_results: int,
    min_cosine_similarity: float,
    has_filters: bool,
) -> int:
    """Choose the KNN ``k``.

    Overfetch (``top_k * 5``) when filters or a positive similarity threshold
    may discard retrieved hits; otherwise use ``top_k`` directly, falling back
    to ``default_max_results`` when ``top_k`` is unset.
    """
    if top_k is None:
        return default_max_results
    if min_cosine_similarity > 0.0 or has_filters:
        return top_k * 5
    return top_k


def build_es_query(
    inp: EmbedSearchInput,
    query_embedding: list[float],
    *,
    default_max_results: int,
) -> dict[str, Any]:
    """Build the nested-KNN ES query body for an embed search.

    ``default_max_results`` is the KNN ``k`` used when ``inp.top_k`` is unset;
    the primitive passes its runtime-configured embed default here.
    """
    filters: list[dict[str, Any]] = []
    for clause in (
        build_video_sources_filter(inp.video_sources, inp.source_type),
        build_source_type_filter(inp.source_type),
        build_description_filter(inp.description),
        build_timestamp_filter(inp.timestamp_start, inp.timestamp_end),
    ):
        if clause is not None:
            filters.append(clause)

    k_value = compute_k_value(
        inp.top_k,
        default_max_results=default_max_results,
        min_cosine_similarity=inp.min_cosine_similarity,
        has_filters=bool(filters),
    )

    nested_query: dict[str, Any] = {
        "nested": {
            "path": "llm.visionEmbeddings",
            "query": {
                "knn": {
                    "field": "llm.visionEmbeddings.vector",
                    "query_vector": query_embedding,
                    "k": k_value,
                    "num_candidates": k_value * 2,
                }
            },
            "inner_hits": {"size": 1},
        }
    }

    if filters:
        filter_clause = {"bool": {"must": filters}} if len(filters) > 1 else filters[0]
        return {
            "query": {"bool": {"must": [nested_query], "filter": [filter_clause]}},
            "size": k_value,
        }
    return {"query": nested_query, "size": k_value}


# =============================================================================
# Hit processing
# =============================================================================


def score_to_cosine(score: float) -> float:
    """Convert an ES ``_score`` in [0, 1] to cosine similarity in [-1, 1].

    Rounds to 2dp before any threshold comparison to avoid floating-point edge
    cases (e.g. ``2 * 0.60 - 1 = 0.19999...`` failing a ``0.20`` threshold).
    """
    return round(2 * score - 1, 2)


def extract_stream_id(sensor_data: dict[str, Any], video_path: str) -> str | None:
    """Resolve the stream UUID for a hit.

    Priority: ``sensor.stream_id`` (if a UUID) -> a UUID found in the path ->
    ``sensor.id`` (if a UUID) -> ``sensor.id`` verbatim as a last-resort
    fallback. Returns None only when ``sensor.id`` itself is empty.
    """
    sensor_stream_id = str(sensor_data.get("stream_id", "") or "")
    if sensor_stream_id and is_standard_uuid_string(sensor_stream_id):
        return sensor_stream_id

    if video_path:
        match = _UUID_RE.search(video_path)
        if match:
            return match.group(0)

    sensor_id_raw = str(sensor_data.get("id", "") or "")
    if is_standard_uuid_string(sensor_id_raw):
        return sensor_id_raw
    return sensor_id_raw or None


def extract_response_data(queries_data: Any) -> dict[str, Any]:
    """Parse the human-readable JSON stored in ``llm.queries[0].response``."""
    if not isinstance(queries_data, list) or not queries_data:
        return {}
    first_query = queries_data[0] if isinstance(queries_data[0], dict) else {}
    response_str = first_query.get("response", "{}")
    if not response_str:
        return {}
    try:
        parsed = json.loads(response_str)
    except (json.JSONDecodeError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def extract_video_name(response_data: dict[str, Any], sensor_id_raw: str, video_path: str) -> str:
    """Derive a display name: file basename for uploads, sensor name for RTSP."""
    video_name = str(response_data.get("video_name", "") or "")
    if video_name:
        return video_name
    if is_standard_uuid_string(sensor_id_raw):
        return video_path.rsplit("/", 1)[-1] if video_path else sensor_id_raw
    return sensor_id_raw or ""


def extract_timestamps(source: dict[str, Any], response_data: dict[str, Any]) -> tuple[str, str]:
    """Resolve (start, end) as ISO-8601 strings.

    Prefers ``response_data``; falls back to the document's ``timestamp``/``end``
    fields; finally uses ``FALLBACK_TIMESTAMP`` so the result is always a
    well-formed string (never None or a stray non-string value).
    """
    start_time = response_data.get("start_time") or ""
    if not start_time:
        ts = source.get("timestamp", "")
        start_dt = safe_iso8601_to_datetime(str(ts)) if ts else None
        start_time = datetime_to_iso8601(start_dt or FALLBACK_TIMESTAMP)

    end_time = response_data.get("end_time") or ""
    if not end_time:
        ts = source.get("end", "")
        end_dt = safe_iso8601_to_datetime(str(ts)) if ts else None
        end_time = datetime_to_iso8601(end_dt or FALLBACK_TIMESTAMP)

    return str(start_time), str(end_time)


def is_excluded(
    *,
    sensor_id_raw: str,
    stream_id: str | None,
    start_time: str,
    end_time: str,
    exclude_videos: list[dict[str, str]],
) -> bool:
    """Return True when this (sensor, window) is in ``exclude_videos``.

    An entry matches when its ``sensor_id`` equals either the raw ``sensor.id``
    or the resolved stream UUID. Matching both is important for RTSP, where the
    raw id is a camera name but callers build exclude lists from the returned
    ``sensor_id`` (the UUID).

    Timestamps are compared by instant (via :func:`iso8601_instants_match`), not
    by exact string, so this stays consistent with the attribute path
    (``_attribute_helpers._is_attribute_excluded``). This remains robust when
    an ``end_time`` has been reformatted by ``merge_consecutive_results`` (a
    round-trip that turns e.g. ``.752Z`` into ``.752000Z``).
    """
    for ex in exclude_videos:
        ex_sensor = ex.get("sensor_id", "")
        sensor_matches = ex_sensor == sensor_id_raw or (stream_id is not None and ex_sensor == stream_id)
        if (
            sensor_matches
            and iso8601_instants_match(start_time, ex.get("start_timestamp", ""))
            and iso8601_instants_match(end_time, ex.get("end_timestamp", ""))
        ):
            return True
    return False


@dataclass(frozen=True)
class ParsedHit:
    """Fields extracted from a single ES hit, before screenshot-URL assembly.

    Screenshot-URL construction needs the VST client, so it stays in the
    primitive; everything else is pure and lives here for testability.
    """

    similarity_score: float
    sensor_id: str
    video_name: str
    description: str
    start_time: str
    end_time: str


def parse_hit(
    hit: dict[str, Any],
    *,
    min_cosine_similarity: float,
    exclude_videos: list[dict[str, str]],
) -> ParsedHit | None:
    """Convert one ES hit into a :class:`ParsedHit`, or None if it is filtered.

    A hit is dropped (None) when it is below the similarity threshold, lacks the
    ``llm`` field, or matches ``exclude_videos``. Recoverable field-level quirks
    (null/odd-typed values) are coerced to clean strings so a usable document is
    never dropped; genuinely malformed structures raise (e.g. ``KeyError`` for a
    missing ``_score``) and are skipped by the caller per-hit.
    """
    similarity_score = score_to_cosine(hit["_score"])
    if similarity_score < min_cosine_similarity:
        return None

    source = hit["_source"]
    if "llm" not in source:
        return None

    stored_llm = source.get("llm") or {}
    sensor_data = source.get("sensor") or {}
    sensor_info = sensor_data.get("info") or {}
    # Coerce to clean strings: stored documents are untrusted and a field may be
    # absent, null, or (rarely) a non-string, none of which should crash mapping.
    video_path = str(sensor_info.get("path") or sensor_info.get("url") or "")
    sensor_id_raw = str(sensor_data.get("id") or "")

    stream_id = extract_stream_id(sensor_data, video_path)
    response_data = extract_response_data(stored_llm.get("queries", []))
    video_name = extract_video_name(response_data, sensor_id_raw, video_path)
    description = str(response_data.get("description") or sensor_data.get("description") or "")
    start_time, end_time = extract_timestamps(source, response_data)

    if is_excluded(
        sensor_id_raw=sensor_id_raw,
        stream_id=stream_id,
        start_time=start_time,
        end_time=end_time,
        exclude_videos=exclude_videos,
    ):
        return None

    return ParsedHit(
        similarity_score=similarity_score,
        sensor_id=stream_id or "",
        video_name=video_name,
        description=description,
        start_time=start_time,
        end_time=end_time,
    )
