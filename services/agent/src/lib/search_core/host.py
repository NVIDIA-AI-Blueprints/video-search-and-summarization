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

All four primitives (embed_search, attribute_search, search, critic) are
implemented and constructed lazily on first use. Query decomposition is
NAT-owned and must run before `.search()` receives its input; VLM analysis
remains an injected dependency for critic use.

Lifecycle: build via one of the class methods and use as an async context
manager (or call ``aclose()``) so lazily-built primitives — and any injected
``vlm_analyzer`` the facade owns — release their backend clients cleanly.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING
from typing import Any

from .errors import ConfigurationError
from .models.attribute_search import AttributeSearchInput
from .models.attribute_search import AttributeSearchOutput
from .models.critic import CriticAgentInput
from .models.critic import CriticAgentOutput
from .models.embed_search import EmbedSearchInput
from .models.embed_search import EmbedSearchOutput
from .models.search import SearchInput
from .models.search import SearchOutput
from .primitives.attribute_search import AttributeSearch
from .primitives.critic import CriticAgent
from .primitives.embed_search import EmbedSearch
from .primitives.search import Search
from .runtime import RuntimeSnapshot
from .runtime import SearchOptions
from .runtime import SearchRuntime

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from collections.abc import Mapping
    from pathlib import Path

    from .clients.protocols import VLMAnalyzer
    from .events import SearchEvent


class VSSSearch:
    """One-stop facade for direct (non-HTTP) callers — host skills, notebooks, evals.

    Build with one of the class methods; call .embed_search / .attribute_search /
    .search / .critic. Use as an async context manager so resources close cleanly:

        async with VSSSearch.from_config_file(path) as vss:
            out = await vss.embed_search(query="red car", source_type="rtsp")

    Note on `.search()`: direct callers pass already-prepared SearchInput
    fields. The facade never builds or invokes model clients for decomposition.
    """

    def __init__(
        self,
        runtime: SearchRuntime,
        *,
        search_options: SearchOptions | None = None,
        vlm_analyzer: VLMAnalyzer | None = None,
    ) -> None:
        self._rt = runtime
        self._opts = search_options or SearchOptions()
        self._vlm_analyzer = vlm_analyzer
        self._embed: EmbedSearch | None = None
        self._attribute: AttributeSearch | None = None
        self._search: Search | None = None
        self._critic: CriticAgent | None = None

    # ------------------------------------------------------------------ Builders

    @classmethod
    def from_runtime(
        cls,
        rt: SearchRuntime,
        *,
        search_options: SearchOptions | None = None,
        vlm_analyzer: VLMAnalyzer | None = None,
    ) -> VSSSearch:
        return cls(
            rt,
            search_options=search_options,
            vlm_analyzer=vlm_analyzer,
        )

    @classmethod
    def from_env(
        cls,
        env: Mapping[str, str] | None = None,
        *,
        vlm_analyzer: VLMAnalyzer | None = None,
    ) -> VSSSearch:
        """Build from os.environ only.

        Does NOT carry orchestrator-level options. For parity with a deployed
        profile, use from_config_file or from_remote instead.
        """
        return cls(SearchRuntime.from_env(env), vlm_analyzer=vlm_analyzer)

    @classmethod
    def from_config_file(
        cls,
        path: str | Path,
        *,
        env: Mapping[str, str] | None = None,
        vlm_analyzer: VLMAnalyzer | None = None,
    ) -> VSSSearch:
        """Build from a NAT-style config file.

        Loads BOTH SearchRuntime AND SearchOptions (use_attribute_search) from
        the same config — guarantees parity with the deployed profile. This is
        the recommended builder for vss-cli and any production caller.
        """
        snap = RuntimeSnapshot.from_config_file(path, env=env)
        return cls(
            snap.runtime,
            search_options=snap.search,
            vlm_analyzer=vlm_analyzer,
        )

    @classmethod
    def from_remote(
        cls,
        agent_url: str,
        *,
        vlm_analyzer: VLMAnalyzer | None = None,
    ) -> VSSSearch:
        """Fetch a snapshot from a running agent.

        WARNING (Helm): the /api/v1/runtime/config snapshot returns in-cluster
        DNS URLs that aren't reachable from a developer laptop. Use the exec
        transport (vss-cli via kubectl exec) for laptop-to-Helm flows.
        """
        snap = RuntimeSnapshot.from_remote(agent_url)
        return cls(
            snap.runtime,
            search_options=snap.search,
            vlm_analyzer=vlm_analyzer,
        )

    # ------------------------------------------------ Convenience primitive-only

    @staticmethod
    def embed_only(rt: SearchRuntime) -> EmbedSearch:
        """Build just an EmbedSearch (e.g. for embed-only workflows that don't
        need VLM env vars)."""
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
        return Search.from_runtime(
            self._rt,
            vlm_analyzer=self._vlm_analyzer,
            use_attribute_search=self._opts.use_attribute_search,
        )

    async def search(self, **kw: Any) -> SearchOutput:
        if self._search is None:
            self._search = self._build_search()
        return await self._search.run(SearchInput(**kw))

    def search_stream(self, **kw: Any) -> AsyncIterator[SearchEvent]:
        if self._search is None:
            self._search = self._build_search()
        return self._search.stream(SearchInput(**kw))

    async def critic(self, **kw: Any) -> CriticAgentOutput:
        if self._critic is None:
            if self._vlm_analyzer is None:
                raise ConfigurationError(
                    "critic() requires an injected vlm_analyzer (pass it to the VSSSearch constructor / builder)"
                )
            self._critic = CriticAgent.from_runtime(
                self._rt,
                vlm_analyzer=self._vlm_analyzer,
                time_format=self._rt.critic_time_format,
                num_videos_to_evaluate=self._rt.critic_evaluation_count,
            )
        return await self._critic.run(CriticAgentInput(**kw))

    # ---------------------------------------------------------------- Lifecycle

    async def aclose(self) -> None:
        """Close every lazily-built primitive and the injected VLM analyzer.

        The analyzer is closed here because the facade is the only owner that
        knows when critic work is done: neither ``Search`` nor ``CriticAgent``
        close the injected ``vlm_analyzer`` (they must not close a dependency
        they didn't create), so without this its httpx client would leak. The
        analyzer's ``aclose`` is optional in the ``VLMAnalyzer`` protocol, so we
        resolve it defensively. Refs are cleared afterwards so a second
        ``aclose`` (e.g. explicit call + ``__aexit__``) is a no-op.
        """
        coros: list[Any] = []
        for p in (self._embed, self._attribute, self._search, self._critic):
            if p is not None:
                coros.append(p.aclose())
        analyzer_close = getattr(self._vlm_analyzer, "aclose", None)
        if analyzer_close is not None:
            coros.append(analyzer_close())
        if coros:
            await asyncio.gather(*coros, return_exceptions=True)
        self._embed = None
        self._attribute = None
        self._search = None
        self._critic = None
        self._vlm_analyzer = None

    async def __aenter__(self) -> VSSSearch:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.aclose()
