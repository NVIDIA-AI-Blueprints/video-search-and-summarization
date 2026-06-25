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
"""EmbedSearch — embedding-based video search primitive.

Ported from services/agent/src/vss_agents/tools/embed_search.py (the body of
the `_embed_search` work function at L603-685 plus its helpers at L195-595).
Key differences from the NAT version:

  - Input is the flat-typed EmbedSearchInput (DESIGN.md §5.2). The NAT shim
    translates QueryInput → EmbedSearchInput.
  - Dependencies (ES, embed client, VST) are injected via the constructor,
    typed against protocols in clients/protocols.py.
  - Index-wildcard for RTSP comes from rt.video_embed_index_wildcard
    (previously hardcoded as "mdx-embed-filtered-*" at L615).
  - No NAT references; no env reads.

Behavior is identical: same ES query shape, same hit-processing rules, same
similarity-score conversion, same screenshot-URL construction.
"""

from __future__ import annotations

import asyncio
from datetime import UTC
from datetime import datetime
import json
import logging
import re
from typing import TYPE_CHECKING
from typing import Any

from elasticsearch import NotFoundError as ESNotFoundError

from .._internal.time_convert import datetime_to_iso8601
from .._internal.time_convert import safe_iso8601_to_datetime
from .._internal.uuid_string import is_standard_uuid_string
from ..models.embed_search import EmbedSearchInput
from ..models.embed_search import EmbedSearchOutput
from ..models.embed_search import EmbedSearchResultItem

if TYPE_CHECKING:
    from ..clients.protocols import CosmosEmbedder
    from ..clients.protocols import ElasticIndex
    from ..clients.protocols import VSTSnapshot
    from ..runtime import SearchRuntime

logger = logging.getLogger(__name__)

# Fallback timestamp when a document has no usable start/end time. Matches the
# legacy NAT path in tools/embed_search.py — kept here so a missing field still
# yields a well-formed (if synthetic) timestamp instead of failing the response.
_FALLBACK_TIMESTAMP = datetime(2025, 1, 1, tzinfo=UTC)

# UUID pattern used to EXTRACT stream IDs from sensor.info.path. The plain
# is/isn't-a-UUID predicate is `is_standard_uuid_string` from _internal —
# we still need a regex for substring extraction from a longer path.
_UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.IGNORECASE)


class EmbedSearch:
    """Embed-based video search.

    Today (NAT): tools/embed_search.py:603-685.
    """

    def __init__(
        self,
        *,
        es: ElasticIndex,
        embed: CosmosEmbedder,
        vst: VSTSnapshot,
        video_embed_index: str,
        video_embed_index_wildcard: str = "mdx-embed-filtered-*",
        default_max_results: int = 10,
    ) -> None:
        self._es = es
        self._embed = embed
        self._vst = vst
        self._index = video_embed_index
        self._index_wildcard = video_embed_index_wildcard
        self._default_k = default_max_results

    # -------------------------------------------------------------------- Run

    async def run(self, inp: EmbedSearchInput) -> EmbedSearchOutput:
        """Execute the embed search and return ranked results."""
        # Index selection by source_type (mirrors tools/embed_search.py:611-617)
        if inp.source_type == "video_file":
            search_index: str | list[str] = self._index
        else:
            search_index = [self._index_wildcard, "-" + self._index]
        logger.info(f"Search index(es): {search_index} (source_type={inp.source_type})")

        # Step 1: generate query embedding
        query_embedding = await self._generate_query_embedding(inp)

        # Step 2: build ES query
        search_query = self._build_es_query(inp, query_embedding)

        # Step 3: execute ES search (preserve the existing not-found error shape)
        try:
            response = await self._es.search(index=search_index, body=search_query)
        except ESNotFoundError as e:
            logger.error(f"Elasticsearch index '{search_index}' not found: {e}")
            raise ValueError(
                f"Search index '{search_index}' does not exist. "
                "Please ensure videos have been ingested before searching."
            ) from e

        # Step 4: process hits in parallel
        hits = response["hits"]["hits"]
        tasks = [self._process_search_hit(hit, inp.min_cosine_similarity, inp.exclude_videos) for hit in hits]
        processed = await asyncio.gather(*tasks)
        results = [r for r in processed if r is not None]

        # Apply explicit top_k cap
        if inp.top_k is not None:
            results = results[: inp.top_k]

        logger.info(f"Found {len(results)} videos matching the query")
        return EmbedSearchOutput(query_embedding=query_embedding, results=results)

    # ------------------------------------------------------------------- Step 1

    async def _generate_query_embedding(self, inp: EmbedSearchInput) -> list[float]:
        """Produce an embedding vector from whichever input field is set.

        Precedence (matches tools/embed_search.py:195-224):
          precomputed_embedding > image_url > query > video_url.
        """
        if inp.precomputed_embedding:
            return [float(v) for v in inp.precomputed_embedding]
        if inp.image_url:
            return await self._embed.get_image_embedding(inp.image_url)
        if inp.query:
            return await self._embed.get_text_embedding(inp.query.strip())
        if inp.video_url:
            return await self._embed.get_video_embedding(inp.video_url)
        raise ValueError("EmbedSearchInput needs at least one of: query, image_url, video_url, precomputed_embedding")

    # ------------------------------------------------------------------- Step 2

    def _build_es_query(self, inp: EmbedSearchInput, query_embedding: list[float]) -> dict[str, Any]:
        """Build the Elasticsearch nested-KNN query.

        Mirrors tools/embed_search.py:226-410 with EmbedSearchInput fields read
        directly (no params dict). Filter-construction details — UUID vs name
        handling, wildcard escaping, regexp clauses — match the original.
        """
        filters: list[dict[str, Any]] = []

        # video_sources filter
        if inp.video_sources:
            if inp.source_type == "rtsp":
                uuid_sources: list[str] = []
                non_uuid_sources = list(inp.video_sources)
            else:
                uuid_sources = [v for v in inp.video_sources if is_standard_uuid_string(v)]
                non_uuid_sources = [v for v in inp.video_sources if not is_standard_uuid_string(v)]

            if uuid_sources and not non_uuid_sources:
                filters.append({"terms": {"sensor.id.keyword": uuid_sources}})
            else:
                should_clauses: list[dict[str, Any]] = []
                if uuid_sources:
                    should_clauses.append({"terms": {"sensor.id.keyword": uuid_sources}})
                for vname in non_uuid_sources:
                    escaped = vname.replace("\\", "\\\\").replace("*", "\\*").replace("?", "\\?")
                    regex_escaped = re.escape(vname)
                    should_clauses.extend(
                        [
                            {"term": {"sensor.id.keyword": vname}},
                            {"wildcard": {"sensor.id.keyword": f"*{escaped}*"}},
                            {"wildcard": {"sensor.info.url.keyword": f"*{escaped}"}},
                            {"wildcard": {"sensor.info.url.keyword": f"*{escaped}*"}},
                            {"wildcard": {"sensor.info.path.keyword": f"*{escaped}*"}},
                            {"regexp": {"sensor.info.url": f".*{regex_escaped}"}},
                            {"regexp": {"sensor.info.path": f".*{regex_escaped}"}},
                        ]
                    )
                filters.append({"bool": {"should": should_clauses, "minimum_should_match": 1}})

        # description filter
        if inp.description:
            escaped_desc = inp.description.replace("\\", "\\\\").replace("*", "\\*").replace("?", "\\?")
            regex_escaped_desc = re.escape(inp.description)
            filters.append(
                {
                    "bool": {
                        "should": [
                            {"match": {"sensor.description": inp.description}},
                            {"wildcard": {"sensor.description.keyword": f"*{escaped_desc}*"}},
                            {"wildcard": {"sensor.description.keyword": f"*{escaped_desc}"}},
                            {"regexp": {"sensor.description": f".*{regex_escaped_desc}.*"}},
                            {"regexp": {"sensor.description.keyword": f".*{regex_escaped_desc}.*"}},
                        ],
                        "minimum_should_match": 1,
                    }
                }
            )

        # timestamp-range filter
        if inp.timestamp_start or inp.timestamp_end:
            must: list[dict[str, Any]] = []
            if inp.timestamp_start:
                must.append({"range": {"timestamp": {"gte": inp.timestamp_start.isoformat()}}})
            if inp.timestamp_end:
                must.append({"range": {"end": {"lte": inp.timestamp_end.isoformat()}}})
            filters.append({"bool": {"must": must}} if len(must) > 1 else must[0])

        # k-value selection: overfetch when filters/threshold may discard results
        if inp.top_k is None:
            k_value = self._default_k
        elif inp.min_cosine_similarity > 0.0 or filters:
            k_value = inp.top_k * 5
        else:
            k_value = inp.top_k

        knn_query: dict[str, Any] = {
            "field": "llm.visionEmbeddings.vector",
            "query_vector": query_embedding,
            "k": k_value,
            "num_candidates": k_value * 2,
        }
        nested_query: dict[str, Any] = {
            "nested": {
                "path": "llm.visionEmbeddings",
                "query": {"knn": knn_query},
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

    # ------------------------------------------------------------------- Step 3

    async def _process_search_hit(
        self,
        hit: dict[str, Any],
        min_cosine_similarity: float,
        exclude_videos: list[dict[str, str]],
    ) -> EmbedSearchResultItem | None:
        """Convert a single ES hit into an EmbedSearchResultItem.

        Mirrors tools/embed_search.py:412-595. Returns None when the hit fails
        the similarity threshold, lacks the "llm" field, or matches an entry in
        exclude_videos.
        """
        try:
            # ES score is normalized [0,1]; cosine is [-1,1]. Round to 2dp before
            # comparing — see tools/embed_search.py for the floating-point note.
            similarity_score = round(2 * hit["_score"] - 1, 2)
            if similarity_score < min_cosine_similarity:
                return None

            source = hit["_source"]
            if "llm" not in source:
                logger.warning(f"Skipping result without 'llm' field: {hit.get('_id', 'unknown')}")
                return None

            stored_llm = source.get("llm", {}) or {}
            queries_data = stored_llm.get("queries", [])
            if not isinstance(queries_data, list):
                queries_data = []

            sensor_data = source.get("sensor", {}) or {}
            sensor_info = sensor_data.get("info", {}) or {}
            video_path = sensor_info.get("path", "") or sensor_info.get("url", "")
            sensor_id_raw = sensor_data.get("id", "")

            # Extract stream_id (UUID) — priority chain matches the original.
            stream_id: str | None = None
            sensor_stream_id = sensor_data.get("stream_id", "")
            if sensor_stream_id and is_standard_uuid_string(sensor_stream_id):
                stream_id = sensor_stream_id
            if not stream_id and video_path:
                m = _UUID_RE.search(video_path)
                if m:
                    stream_id = m.group(0)
            if not stream_id:
                if is_standard_uuid_string(sensor_id_raw):
                    stream_id = sensor_id_raw
                else:
                    logger.warning(
                        f"Could not extract UUID from path '{video_path}' or sensor.stream_id "
                        f"for sensor '{sensor_id_raw}'. Using sensor.id as stream_id."
                    )
                    stream_id = sensor_id_raw

            # response_data carries human-readable fields stored at ingest time.
            response_data: dict[str, Any] = {}
            if queries_data:
                first_query = queries_data[0] if isinstance(queries_data[0], dict) else {}
                response_str = first_query.get("response", "{}")
                if response_str:
                    try:
                        parsed = json.loads(response_str)
                        if isinstance(parsed, dict):
                            response_data = parsed
                    except json.JSONDecodeError:
                        pass

            # video_name — file path's last segment for video_file; sensor name for rtsp.
            video_name = response_data.get("video_name", "")
            if not video_name:
                if is_standard_uuid_string(sensor_id_raw):
                    video_name = video_path.rsplit("/", 1)[-1] if video_path else sensor_id_raw
                else:
                    video_name = sensor_id_raw or ""

            # description
            description = response_data.get("description", "") or sensor_data.get("description", "")

            # timestamps — prefer response_data, fall back to source timestamp/end, finally _FALLBACK_TIMESTAMP.
            start_time = response_data.get("start_time", "")
            if not start_time:
                ts = source.get("timestamp", "")
                start_dt = safe_iso8601_to_datetime(str(ts)) if ts else None
                start_time = datetime_to_iso8601(start_dt or _FALLBACK_TIMESTAMP)
            end_time = response_data.get("end_time", "")
            if not end_time:
                ts = source.get("end", "")
                end_dt = safe_iso8601_to_datetime(str(ts)) if ts else None
                end_time = datetime_to_iso8601(end_dt or _FALLBACK_TIMESTAMP)

            # exclude_videos check
            for ex in exclude_videos:
                if (
                    sensor_id_raw == ex.get("sensor_id", "")
                    and start_time == ex.get("start_timestamp", "")
                    and end_time == ex.get("end_timestamp", "")
                ):
                    return None

            # screenshot URL via injected VST
            screenshot_url = ""
            if stream_id:
                screenshot_url = self._vst.build_screenshot_url(
                    sensor_id=stream_id,
                    timestamp=start_time,
                    internal=False,
                )

            return EmbedSearchResultItem(
                video_name=video_name,
                description=description,
                start_time=start_time,
                end_time=end_time,
                sensor_id=stream_id or "",
                screenshot_url=screenshot_url,
                similarity_score=similarity_score,
            )

        except Exception as e:
            logger.warning(f"Error processing search hit: {e}")
            return None

    # --------------------------------------------------------------- Factories

    @classmethod
    def from_runtime(
        cls,
        rt: SearchRuntime,
        *,
        es: ElasticIndex | None = None,
        embed: CosmosEmbedder | None = None,
        vst: VSTSnapshot | None = None,
    ) -> EmbedSearch:
        """Construct from a SearchRuntime, optionally with injected dependencies."""
        from ..clients.cosmos_embed import CosmosEmbedClient  # local — avoid cycle
        from ..clients.elastic import ElasticClient
        from ..clients.vst import VSTClient

        return cls(
            es=es or ElasticClient.from_runtime(rt),
            embed=embed or CosmosEmbedClient.from_runtime(rt),
            vst=vst or VSTClient.from_runtime(rt),
            video_embed_index=rt.video_embed_index,
            video_embed_index_wildcard=rt.video_embed_index_wildcard,
            default_max_results=rt.embed_default_max_results,
        )

    async def aclose(self) -> None:
        await asyncio.gather(
            self._es.aclose(),
            self._embed.aclose(),
            return_exceptions=True,
        )
