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
"""BM25 retrieval over controlled RT-VLM tag documents."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
import logging
import re
from typing import TYPE_CHECKING

from pydantic import ValidationError

from vss_core._foundation.sanitize import scrub_log
from vss_core.vios import VSTError
from vss_core.vios import map_timestamp_to_timeline

from ..errors import BackendUnreachableError
from ..models.tag_search import TagSearchInput
from ..models.tag_search import TagSearchOutput
from ..models.tag_search import TagSearchResultItem
from . import _tag_helpers as helpers

if TYPE_CHECKING:
    from vss_core.vios import VSTSnapshot

    from ..clients.protocols import ElasticIndex
    from ..runtime import SearchRuntime

logger = logging.getLogger(__name__)


class TagSearch:
    """Keyword search over validated RT-VLM tag documents."""

    def __init__(
        self,
        *,
        es: ElasticIndex,
        vst: VSTSnapshot,
        tag_index: str = "default_*",
        default_max_results: int = 10,
        owns_es: bool = False,
    ) -> None:
        self._es = es
        self._vst = vst
        self._index = tag_index
        self._default_k = default_max_results
        self._owns_es = owns_es

    async def run(self, inp: TagSearchInput) -> TagSearchOutput:
        inp.validate_semantics()
        name_to_id = await self._name_to_id()
        source_ids = self._resolve_source_ids(inp.video_sources or [], name_to_id)
        search_index = self._resolve_search_index(source_ids)
        query = helpers.build_es_query(inp, source_ids=source_ids, default_max_results=self._default_k)
        logger.info(
            "Tag search: index=%s source_type=%s query=%s",
            search_index,
            inp.source_type,
            scrub_log(inp.query),
        )
        response = await self._es.search(
            index=search_index,
            body=query,
            ignore_unavailable=True,
            allow_no_indices=True,
        )
        data = response.body if isinstance(getattr(response, "body", None), Mapping) else response
        response_hits = data.get("hits") if isinstance(data, Mapping) else None
        hits = response_hits.get("hits") if isinstance(response_hits, Mapping) else None
        if not isinstance(hits, list):
            raise BackendUnreachableError("elasticsearch", "tag search response did not contain a list at hits.hits")

        sensor_names = {stream_id: name for name, stream_id in name_to_id.items()}
        timelines = await self._timelines_best_effort()
        results: list[TagSearchResultItem] = []
        malformed = 0
        for hit in hits:
            try:
                if not isinstance(hit, dict):
                    raise TypeError("Elasticsearch hit must be an object")
                parsed = helpers.parse_hit(hit, sensor_names=sensor_names)
                screenshot_time = parsed.start_time
                timeline = timelines.get(parsed.stream_id) or timelines.get(parsed.sensor_id)
                if timeline:
                    screenshot_time = map_timestamp_to_timeline(screenshot_time, timeline[0], timeline[1])
                screenshot_url = (
                    self._vst.build_screenshot_url(
                        sensor_id=parsed.sensor_id,
                        timestamp=screenshot_time,
                        internal=False,
                    )
                    if not timelines or timeline
                    else ""
                )
                results.append(
                    TagSearchResultItem(
                        video_name=parsed.video_name,
                        description=parsed.description,
                        start_time=parsed.start_time,
                        end_time=parsed.end_time,
                        sensor_id=parsed.sensor_id,
                        screenshot_url=screenshot_url,
                        lexical_score=parsed.lexical_score,
                        tags=parsed.tags,
                    )
                )
            except (AttributeError, KeyError, TypeError, ValueError, ValidationError):
                malformed += 1
                logger.warning("Skipping malformed VLM tag document", exc_info=True)

        limit = inp.top_k or self._default_k
        return TagSearchOutput(results=results[:limit], malformed_documents=malformed)

    async def _name_to_id(self) -> dict[str, str]:
        """Fetch the authoritative VST source mapping.

        Source resolution is part of the isolation boundary, so a VST failure
        must fail the request rather than broaden or guess its scope.
        """
        return await self._vst.get_name_to_stream_id_map()

    async def _timelines_best_effort(self) -> dict[str, tuple[str, str]]:
        fetch = getattr(self._vst, "get_timelines_map", None)
        if fetch is None:
            return {}
        try:
            result: dict[str, tuple[str, str]] = await fetch()
            return result
        except VSTError as error:
            logger.warning("Could not fetch VST timelines for tag screenshots: %s", error)
            return {}

    @staticmethod
    def _resolve_source_ids(video_sources: list[str], name_to_id: dict[str, str]) -> list[str]:
        known_ids = set(name_to_id.values())
        resolved: list[str] = []
        for raw_source in video_sources:
            source = raw_source.strip()
            if source in name_to_id:
                source_id = name_to_id[source]
            elif source in known_ids:
                source_id = source
            else:
                # Parity with embed/attribute: an unresolved source is kept
                # literal rather than rejected. The derived ``default_<id>``
                # index and the ES identity filter won't match, so the result
                # is empty — narrowed, never silently broadened.
                source_id = source
            if source_id not in resolved:
                resolved.append(source_id)
        return resolved

    def _resolve_search_index(self, source_ids: list[str]) -> str:
        """Use all configured indexes, or exact source indexes when scoped."""
        if "*" not in self._index or not source_ids:
            return self._index
        indexes = []
        for source_id in source_ids:
            safe_id = re.sub(r"[-/\\ ]", "_", source_id)
            indexes.append(self._index.replace("*", safe_id))
        return ",".join(indexes)

    @classmethod
    def from_runtime(
        cls,
        rt: SearchRuntime,
        *,
        es: ElasticIndex | None = None,
        vst: VSTSnapshot | None = None,
    ) -> TagSearch:
        from vss_core.vios import VSTClient

        from ..clients.elastic import ElasticClient

        return cls(
            es=es if es is not None else ElasticClient.from_runtime(rt),
            vst=vst
            if vst is not None
            else VSTClient(
                internal_url=rt.require("vst_internal_url"),
                external_url=rt.require("vst_external_url"),
                timeout_seconds=rt.request_timeout_seconds,
            ),
            tag_index=rt.tag_index,
            default_max_results=rt.default_max_results,
            owns_es=es is None,
        )

    async def aclose(self) -> None:
        if self._owns_es:
            await asyncio.gather(self._es.aclose(), return_exceptions=True)
