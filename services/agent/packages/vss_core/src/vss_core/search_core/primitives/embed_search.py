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

This module is deliberately thin: index selection, ES query construction, and
hit extraction all live as pure functions in ``_embed_helpers.py`` so the hard
logic is unit-testable without async or live backends. ``EmbedSearch`` only
orchestrates the injected clients (ES, embed, VST) and maps backend/input
failures onto the library error hierarchy:

  - empty/whitespace-only input raises ``InvalidInputError`` rather than
    embedding an empty query;
  - ``timestamp_start > timestamp_end`` raises ``InvalidInputError``;
  - a missing ES index raises ``IndexNotFoundError`` (a ``BackendUnreachableError``);
  - per-hit processing only swallows expected data-shape errors; unexpected
    exceptions propagate instead of silently shrinking the result set.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
import logging
from typing import TYPE_CHECKING
from typing import Any

from pydantic import ValidationError

from vss_core._foundation.sanitize import scrub_log
from vss_core.vst import VSTError
from vss_core.vst import map_timestamp_to_timeline

from .._internal.time_measure import TimeMeasure
from ..errors import BackendUnreachableError
from ..models.embed_search import EmbedSearchInput
from ..models.embed_search import EmbedSearchOutput
from ..models.embed_search import EmbedSearchResultItem
from . import _embed_helpers as helpers

if TYPE_CHECKING:
    from vss_core.vst.protocols import VSTSnapshot

    from ..clients.protocols import ElasticIndex
    from ..clients.protocols import TextEmbedder
    from ..runtime import SearchRuntime

logger = logging.getLogger(__name__)


def _safe_hit_id(hit: Any) -> str:
    """Best-effort hit id for logging, safe even if ``hit`` is not a dict."""
    return str(hit.get("_id", "unknown")) if isinstance(hit, dict) else "unknown"


class EmbedSearch:
    """Embedding-based video search over an Elasticsearch vision index."""

    def __init__(
        self,
        *,
        es: ElasticIndex,
        embed: TextEmbedder,
        vst: VSTSnapshot,
        video_embed_index_wildcard: str = "mdx-embed-filtered-*",
        default_max_results: int = 10,
        owns_es: bool = False,
        owns_embed: bool = False,
    ) -> None:
        self._es = es
        self._embed = embed
        self._vst = vst
        self._index_wildcard = video_embed_index_wildcard
        self._default_k = default_max_results
        self._owns_es = owns_es
        self._owns_embed = owns_embed

    # -------------------------------------------------------------------- Run

    async def run(self, inp: EmbedSearchInput) -> EmbedSearchOutput:
        """Execute the embed search and return ranked results."""
        inp.validate_semantics()

        search_index = helpers.select_search_index(self._index_wildcard)
        logger.info(f"Embed search: index={search_index} source_type={inp.source_type} query={scrub_log(inp.query)}")

        with TimeMeasure("embed_search: generate query embedding"):
            query_embedding = await self._generate_query_embedding(inp)

        with TimeMeasure("embed_search: build ES query"):
            search_query = helpers.build_es_query(inp, query_embedding, default_max_results=self._default_k)
        logger.debug(f"Embed search: ES query size={search_query.get('size')} (vector omitted)")

        with TimeMeasure("embed_search: ES search execution"):
            response = await self._search(search_index, search_query)

        with TimeMeasure("embed_search: process search hits"):
            response_data = response.body if isinstance(getattr(response, "body", None), Mapping) else response
            response_hits = response_data.get("hits") if isinstance(response_data, Mapping) else None
            hits = response_hits.get("hits") if isinstance(response_hits, Mapping) else None
            if not isinstance(hits, list):
                raise BackendUnreachableError(
                    "elasticsearch",
                    "search response did not contain a list at hits.hits",
                )
            timelines = await self._fetch_timelines_best_effort(hits)
            results = self._process_hits(hits, inp, timelines)

        if inp.top_k is not None:
            results = results[: inp.top_k]

        logger.info(f"Embed search: found {len(results)} videos matching the query")
        return EmbedSearchOutput(query_embedding=query_embedding, results=results)

    # ------------------------------------------------------------------- Steps

    async def _generate_query_embedding(self, inp: EmbedSearchInput) -> list[float]:
        """Produce a text embedding for the validated query."""
        return await self._embed.get_text_embedding(inp.query.strip())

    async def _search(self, search_index: str | list[str], search_query: dict[str, Any]) -> Any:
        """Run the ES search through the library's ElasticIndex boundary.

        Returns the raw ES response (an ``ObjectApiResponse``-like mapping, typed
        ``Any`` to match the ``ElasticIndex`` protocol). Other transport/API
        failures are already mapped to the library error hierarchy by the
        ``ElasticClient`` wrapper.
        """
        return await self._es.search(index=search_index, body=search_query)

    async def _fetch_timelines_best_effort(self, hits: list[dict]) -> dict[str, tuple[str, str]]:
        """Fetch VST replay timelines for screenshot-timestamp mapping.

        File-ingested sources are indexed on a synthetic epoch while VST
        records at ingest wall-clock; screenshot URLs built from raw ES
        timestamps point outside the recording and VST returns 500. One
        timelines call covers every stream. Best-effort: on any failure the
        search proceeds with unmapped timestamps rather than losing hits.
        ``getattr`` keeps older VSTSnapshot implementations (without
        ``get_timelines_map``) working unmapped.
        """
        if not hits:
            return {}
        fetch = getattr(self._vst, "get_timelines_map", None)
        if fetch is None:
            return {}
        try:
            timelines: dict[str, tuple[str, str]] = await fetch()
        except VSTError as e:
            logger.warning(f"Could not fetch VST timelines; screenshot timestamps left unmapped: {e}")
            return {}
        return timelines

    def _process_hits(
        self,
        hits: list[dict],
        inp: EmbedSearchInput,
        timelines: dict[str, tuple[str, str]] | None = None,
    ) -> list[EmbedSearchResultItem]:
        """Map raw ES hits to result items, skipping filtered or unprocessable hits.

        Mapping is best-effort per document: a single corrupt/unexpected stored
        document must never fail the whole search, so any error from one hit is
        logged (at WARNING, with traceback for visibility) and that hit is
        skipped while the rest are returned.
        """
        results: list[EmbedSearchResultItem] = []
        for hit in hits:
            try:
                item = self._hit_to_item(hit, inp, timelines)
            except (AttributeError, KeyError, TypeError, ValueError, ValidationError):
                logger.warning(f"Skipping unprocessable search hit {scrub_log(_safe_hit_id(hit))}", exc_info=True)
                continue
            if item is not None:
                results.append(item)
        return results

    def _hit_to_item(
        self,
        hit: dict[str, Any],
        inp: EmbedSearchInput,
        timelines: dict[str, tuple[str, str]] | None = None,
    ) -> EmbedSearchResultItem | None:
        """Convert a single ES hit into a result item, or None if it is filtered."""
        parsed = helpers.parse_hit(
            hit,
            min_cosine_similarity=inp.min_cosine_similarity,
            exclude_videos=inp.exclude_videos,
        )
        if parsed is None:
            return None

        screenshot_url = ""
        if parsed.sensor_id:
            screenshot_ts = parsed.start_time
            timeline = (timelines or {}).get(parsed.sensor_id)
            if timeline:
                screenshot_ts = map_timestamp_to_timeline(screenshot_ts, timeline[0], timeline[1])
            if timelines and not timeline:
                # VST reported its replayable streams and this hit's stream is
                # not among them (stale ES document from a prior registration).
                # A picture URL for an unknown stream is a guaranteed VST
                # error; return the hit without a screenshot instead.
                logger.warning(
                    f"Stream {scrub_log(parsed.sensor_id)} not in VST timelines; returning hit without screenshot"
                )
            else:
                screenshot_url = self._vst.build_screenshot_url(
                    sensor_id=parsed.sensor_id,
                    timestamp=screenshot_ts,
                    internal=False,
                )

        return EmbedSearchResultItem(
            video_name=parsed.video_name,
            description=parsed.description,
            start_time=parsed.start_time,
            end_time=parsed.end_time,
            sensor_id=parsed.sensor_id,
            screenshot_url=screenshot_url,
            similarity_score=parsed.similarity_score,
        )

    # --------------------------------------------------------------- Factories

    @classmethod
    def from_runtime(
        cls,
        rt: SearchRuntime,
        *,
        es: ElasticIndex | None = None,
        embed: TextEmbedder | None = None,
        vst: VSTSnapshot | None = None,
    ) -> EmbedSearch:
        """Construct from a SearchRuntime, optionally with injected dependencies."""
        from vss_core.vst import VSTClient

        from ..clients.cosmos_embed import CosmosEmbedClient  # local — avoid cycle
        from ..clients.elastic import ElasticClient

        owns_es = es is None
        owns_embed = embed is None
        return cls(
            es=es if es is not None else ElasticClient.from_runtime(rt),
            embed=embed if embed is not None else CosmosEmbedClient.from_runtime(rt),
            vst=vst
            if vst is not None
            else VSTClient(
                # Embed search only builds externally consumable screenshot
                # URLs; the internal VST route is not contacted.
                internal_url=rt.vst_internal_url or "",
                external_url=rt.require("vst_external_url"),
                timeout_seconds=rt.request_timeout_seconds,
            ),
            video_embed_index_wildcard=rt.video_embed_index_wildcard,
            default_max_results=rt.default_max_results,
            owns_es=owns_es,
            owns_embed=owns_embed,
        )

    async def aclose(self) -> None:
        coros = []
        if self._owns_es:
            coros.append(self._es.aclose())
        if self._owns_embed:
            coros.append(self._embed.aclose())
        if coros:
            await asyncio.gather(*coros, return_exceptions=True)
