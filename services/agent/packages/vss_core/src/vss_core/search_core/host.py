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
"""VSSSearch — facade for direct (non-HTTP) callers.

All three primitives (embed_search, attribute_search, search) are
implemented and constructed lazily on first use. Query decomposition is
NAT-owned and must run before `.search()` receives its input.

Lifecycle: build via one of the class methods and use as an async context
manager (or call ``aclose()``) so lazily-built primitives release their backend clients cleanly.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING
from typing import Any

from .models.attribute_search import AttributeSearchInput
from .models.attribute_search import AttributeSearchOutput
from .models.embed_search import EmbedSearchInput
from .models.embed_search import EmbedSearchOutput
from .models.search import SearchInput
from .models.search import SearchOutput
from .primitives.attribute_search import AttributeSearch
from .primitives.embed_search import EmbedSearch
from .primitives.search import Search

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from vss_core.critic import CriticAgent

    from .events import SearchEvent
    from .runtime import SearchRuntime

logger = logging.getLogger(__name__)


class VSSSearch:
    """One-stop facade for direct (non-HTTP) callers — host skills, notebooks, evals.

    Build from a SearchRuntime; call .embed_search / .attribute_search /
    .search. Use as an async context manager so resources close cleanly:

        async with VSSSearch.from_runtime(runtime) as vss:
            out = await vss.embed_search(query="red car", source_type="rtsp")

    Note on `.search()`: direct callers pass already-prepared SearchInput
    fields. The facade never builds or invokes model clients for decomposition.
    """

    def __init__(self, runtime: SearchRuntime, *, critic: CriticAgent | None = None) -> None:
        self._rt = runtime
        self._critic = critic
        self._embed: EmbedSearch | None = None
        self._attribute: AttributeSearch | None = None
        self._search: Search | None = None

    @property
    def runtime(self) -> SearchRuntime:
        """Resolved runtime used by this facade (read-only).

        Host entry points use this to perform deployment-aware preflights before
        dispatching a primitive.  Returning the frozen dataclass preserves the
        facade's no-mutable-runtime invariant.
        """
        return self._rt

    # ------------------------------------------------------------------ Builders

    @classmethod
    def from_runtime(cls, rt: SearchRuntime, *, critic: CriticAgent | None = None) -> VSSSearch:
        return cls(rt, critic=critic)

    # ------------------------------------------------ Convenience primitive-only

    @staticmethod
    def embed_only(rt: SearchRuntime) -> EmbedSearch:
        """Build just an EmbedSearch (e.g. for embed-only workflows that don't
        do not need the RTVI-CV endpoint)."""
        return EmbedSearch.from_runtime(rt)

    @staticmethod
    def attribute_only(rt: SearchRuntime) -> AttributeSearch:
        """Build just an AttributeSearch."""
        return AttributeSearch.from_runtime(rt)

    # -------------------------------------------------------------- Primitives

    async def embed_search(self, **kw: Any) -> EmbedSearchOutput:
        if self._embed is None:
            self._embed = EmbedSearch.from_runtime(self._rt)
        return await self._embed.run(EmbedSearchInput(**kw))

    async def attribute_search(self, **kw: Any) -> AttributeSearchOutput:
        if self._attribute is None:
            self._attribute = AttributeSearch.from_runtime(self._rt)
        return await self._attribute.run(AttributeSearchInput(**kw))

    def _build_search(self) -> Search:
        """Lazy-build the Search primitive."""
        return Search.from_runtime(self._rt)

    async def search(self, **kw: Any) -> SearchOutput:
        if self._search is None:
            self._search = self._build_search()
        inp = SearchInput(**kw)
        output = await self._search.run(inp)
        return await self._verify_results(output, inp)

    async def _verify_results(self, output: SearchOutput, inp: SearchInput) -> SearchOutput:
        """Best-effort critic pass over retrieved intervals.

        Search never depends on verification succeeding. Missing dependencies,
        invalid media bounds, or a critic/VLM failure leave the affected hits at
        their model default of ``None``; a critic that ran but produced no
        verdict leaves ``unverified``.
        """
        if self._critic is None or not output.data:
            return output

        if inp.original_query and inp.original_query.strip():
            query = inp.original_query.strip()
        else:
            query = inp.query.strip()
            attributes = [attribute.strip() for attribute in inp.attributes if attribute.strip()]
            missing_attributes = [attribute for attribute in attributes if attribute.casefold() not in query.casefold()]
            if missing_attributes:
                suffix = ", ".join(missing_attributes)
                query = f"{query}; required visual attributes: {suffix}" if query else suffix
        if not query:
            return output

        from pydantic import ValidationError

        from vss_core.critic import CriticAgentInput
        from vss_core.critic import CriticAgentResult
        from vss_core.critic import VideoInfo

        from .models.search import CriticResult

        candidate_indices: list[int] = []
        videos: list[VideoInfo] = []
        for index, result in enumerate(output.data):
            if not result.sensor_id:
                continue
            try:
                video = VideoInfo.model_validate(
                    {
                        "sensor_id": result.sensor_id,
                        "start_timestamp": result.start_time,
                        "end_timestamp": result.end_time,
                        # Only file sources are indexed on the synthetic epoch the
                        # critic rebases; live bounds must be taken literally.
                        "source_type": inp.source_type,
                    }
                )
            except ValidationError:
                logger.warning(
                    "Search result %d has invalid verification bounds; leaving it without a critic verdict", index
                )
                continue
            candidate_indices.append(index)
            videos.append(video)

        if not videos:
            return output

        extra_messages: list[str] = []

        try:
            critic_output = await self._critic.run(CriticAgentInput(query=query, videos=videos))
        except Exception:
            logger.warning(
                "Search-result verification failed; returning retrieval hits without a critic verdict", exc_info=True
            )
            return output.model_copy(
                update={
                    "search_messages": [
                        *output.search_messages,
                        "Visual verification failed; retrieval results carry no critic verdict (critic_result is null).",
                    ]
                }
            )

        verified_results = list(output.data)
        for index, verdict in zip(candidate_indices, critic_output.video_results, strict=False):
            verified_results[index] = verified_results[index].model_copy(
                update={
                    "critic_result": CriticResult(
                        result=verdict.result.value,
                        criteria_met=verdict.criteria_met or {},
                    )
                }
            )

        # The critic degrades a failed candidate to `unverified` instead of
        # raising, so a deployment whose VLM answers /v1/models but fails
        # every completion still emits a per-hit `unverified` verdict --
        # distinguishable from the silent `None` left when no critic ran
        # at all. Say which it was: the message below marks the former.
        if critic_output.video_results and all(
            verdict.result == CriticAgentResult.UNVERIFIED for verdict in critic_output.video_results
        ):
            extra_messages.append(
                "Visual verification ran but produced no verdict for any hit; check the configured RT-VLM service."
            )

        update: dict[str, Any] = {"data": verified_results}
        if extra_messages:
            update["search_messages"] = [*output.search_messages, *extra_messages]
        return output.model_copy(update=update)

    def search_stream(self, **kw: Any) -> AsyncIterator[SearchEvent]:
        if self._search is None:
            self._search = self._build_search()
        return self._verified_search_stream(SearchInput(**kw))

    async def _verified_search_stream(self, inp: SearchInput) -> AsyncIterator[SearchEvent]:
        """Preserve streaming progress and verify only the terminal output."""
        from .events import FinalResultEvent

        assert self._search is not None
        async for event in self._search.stream(inp):
            if isinstance(event, FinalResultEvent):
                yield event.model_copy(update={"output": await self._verify_results(event.output, inp)})
            else:
                yield event

    # ---------------------------------------------------------------- Lifecycle

    async def aclose(self) -> None:
        """Close every lazily-built search primitive."""
        coros: list[Any] = []
        for p in (self._embed, self._attribute, self._search):
            if p is not None:
                coros.append(p.aclose())
        if coros:
            await asyncio.gather(*coros, return_exceptions=True)
        self._embed = None
        self._attribute = None
        self._search = None

    async def __aenter__(self) -> VSSSearch:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.aclose()
