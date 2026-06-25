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
"""Search — orchestrator that fuses embed_search + attribute_search + critic.

Self-contained: all orchestration logic lives in `_search_helpers.py` under
this package; no upward dependency on `vss_agents.tools.search`. NAT shim
(after Gap F) imports from here, not the reverse.

Query decomposition is NAT-owned and happens before the library is called.
The library consumes prepared SearchInput fields only.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import TYPE_CHECKING
from typing import Any
from typing import Literal

from .._internal.embed_translation import params_to_embed_input
from ..errors import SearchError
from ..events import ErrorEvent
from ..events import FinalResultEvent
from ..events import SearchEvent
from ..events import StatusEvent
from ..models.attribute_search import AttributeSearchInput
from ..models.critic import CriticAgentInput
from ..models.embed_search import EmbedSearchInput
from ..models.search import SearchInput
from ..models.search import SearchOutput
from . import _search_helpers
from .attribute_search import AttributeSearch
from .critic import CriticAgent
from .embed_search import EmbedSearch

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from collections.abc import Callable

    from ..clients.protocols import ElasticIndex
    from ..clients.protocols import VLMAnalyzer
    from ..clients.protocols import VSTSnapshot
    from ..runtime import SearchRuntime


class _PrimitiveAdapter:
    """Wraps a library primitive so `execute_core_search`'s `.ainvoke(payload)`
    calls work. Caller-supplied `coerce_payload` converts whatever payload the
    orchestrator hands in (dict, JSON string, BaseModel) into the right
    input-model instance for the primitive's `.run()`. Optional `unwrap_output`
    transforms the primitive's return — used by the attribute adapter to
    return the bare list the orchestrator expects.
    """

    def __init__(
        self,
        primitive: Any,
        coerce_payload: Callable[[Any], Any],
        unwrap_output: Callable[[Any], Any] | None = None,
    ) -> None:
        self._primitive = primitive
        self._coerce = coerce_payload
        self._unwrap = unwrap_output

    async def ainvoke(self, payload: Any) -> Any:
        inp = self._coerce(payload)
        out = await self._primitive.run(inp)
        return self._unwrap(out) if self._unwrap else out


def _coerce_embed_payload(payload: Any) -> EmbedSearchInput:
    """`execute_core_search` builds `{"params": ..., "source_type": ...}` JSON
    on the embed path; detect that shape and delegate to the shared translator.
    Unknown types raise TypeError so misuse fails loudly rather than through a
    confusing Pydantic ValidationError.
    """
    if isinstance(payload, EmbedSearchInput):
        return payload
    if isinstance(payload, str):
        payload = json.loads(payload)
    if isinstance(payload, dict):
        if "params" in payload or "prompts" in payload:
            return params_to_embed_input(
                payload.get("params") or {},
                payload.get("source_type", "video_file"),
            )
        return EmbedSearchInput(**payload)
    if hasattr(payload, "model_dump"):
        return EmbedSearchInput.model_validate(payload.model_dump())
    raise TypeError(f"cannot coerce {type(payload).__name__} to EmbedSearchInput")


def _coerce_attribute_payload(payload: Any) -> AttributeSearchInput:
    if isinstance(payload, AttributeSearchInput):
        return payload
    if isinstance(payload, dict):
        return AttributeSearchInput(**payload)
    if hasattr(payload, "model_dump"):
        return AttributeSearchInput.model_validate(payload.model_dump())
    raise TypeError(f"cannot coerce {type(payload).__name__} to AttributeSearchInput")


def _coerce_critic_payload(payload: Any) -> CriticAgentInput:
    if isinstance(payload, CriticAgentInput):
        return payload
    if isinstance(payload, dict):
        return CriticAgentInput(**payload)
    if hasattr(payload, "model_dump"):
        return CriticAgentInput.model_validate(payload.model_dump())
    raise TypeError(f"cannot coerce {type(payload).__name__} to CriticAgentInput")


def _wrap_embed(primitive: EmbedSearch) -> _PrimitiveAdapter:
    return _PrimitiveAdapter(primitive, _coerce_embed_payload)


def _wrap_attribute(primitive: AttributeSearch) -> _PrimitiveAdapter:
    # Orchestrator expects a bare list of AttributeSearchResult, not the envelope.
    return _PrimitiveAdapter(primitive, _coerce_attribute_payload, unwrap_output=lambda out: out.results)


def _wrap_critic(primitive: CriticAgent) -> _PrimitiveAdapter:
    return _PrimitiveAdapter(primitive, _coerce_critic_payload)


class Search:
    """Search orchestrator.

    Library shape: takes SearchInput, returns SearchOutput; `.stream()` yields
    typed `SearchEvent` instances (StatusEvent / FinalResultEvent / ErrorEvent).

    Agent-mode query decomposition is out of scope for this library. NAT
    populates prepared SearchInput fields before calling into core search.
    """

    def __init__(
        self,
        *,
        embed: EmbedSearch,
        attribute: AttributeSearch,
        critic: CriticAgent | None,
        behavior_es: ElasticIndex,
        behavior_index: str,
        behavior_index_wildcard: str = "mdx-behavior-*",
        use_attribute_search: bool = False,
        fusion_method: Literal["weighted_linear", "rrf"] = "rrf",
        w_attribute: float = 0.55,
        w_embed: float = 0.35,
        rrf_k: int = 60,
        rrf_w: float = 0.5,
        top_percent_filter: float | None = None,
        embed_confidence_threshold: float = 0.1,
        search_max_iterations: int = 1,
        default_max_results: int = 10,
        # VST URLs are needed by execute_core_search via its config object
        vst_internal_url: str = "",
        vst_external_url: str = "",
    ) -> None:
        self._embed = embed
        self._attribute = attribute
        self._critic = critic
        self._behavior_es = behavior_es

        # Pre-build the adapters once; they're stateless wrappers around
        # immutable primitive references, so reusing them across calls is safe.
        self._embed_adapter = _wrap_embed(embed)
        self._attr_adapter = _wrap_attribute(attribute)
        self._critic_adapter = _wrap_critic(critic) if critic else None

        # Pre-build the duck-typed config that execute_core_search reads by
        # attribute. All fields are determined at construction; no per-call
        # mutation.
        self._config = SimpleNamespace(
            attribute_search_tool="attribute_search",
            critic_agent="critic_agent" if critic else None,
            enable_critic=critic is not None,
            use_attribute_search=use_attribute_search,
            embed_confidence_threshold=embed_confidence_threshold,
            search_max_iterations=search_max_iterations,
            default_max_results=default_max_results,
            fusion_method=fusion_method,
            w_attribute=w_attribute,
            w_embed=w_embed,
            rrf_k=rrf_k,
            rrf_w=rrf_w,
            top_percent_filter=top_percent_filter,
            vst_internal_url=vst_internal_url,
            vst_external_url=vst_external_url,
            behavior_es_endpoint=behavior_es.endpoint,
            behavior_index=behavior_index,
            behavior_index_wildcard=behavior_index_wildcard,
        )

    async def run(self, inp: SearchInput) -> SearchOutput:
        """Single-shot: collect chunks, return final SearchOutput."""
        return await _search_helpers.execute_core_search_wrapper(
            search_input=inp,
            embed_search=self._embed_adapter,
            config=self._config,
            attribute_search_fn=self._attr_adapter,
            critic_agent=self._critic_adapter,
            behavior_es=self._behavior_es,
        )

    async def stream(self, inp: SearchInput) -> AsyncIterator[SearchEvent]:
        """Streaming: translate AgentMessageChunk → SearchEvent. Exactly one
        terminal event (FinalResultEvent or ErrorEvent) is emitted.
        """
        try:
            async for chunk in _search_helpers.execute_core_search(
                search_input=inp,
                embed_search=self._embed_adapter,
                config=self._config,
                attribute_search_fn=self._attr_adapter,
                critic_agent=self._critic_adapter,
                behavior_es=self._behavior_es,
            ):
                if isinstance(chunk, SearchOutput):
                    yield FinalResultEvent(output=chunk)
                    return
                # The other arm of the union is AgentMessageChunk.
                yield StatusEvent(stage=chunk.type.value, message=chunk.content)
        except SearchError as e:
            yield ErrorEvent(error_code=type(e).__name__, message=str(e))
            return
        except Exception as e:
            yield ErrorEvent(error_code="UnexpectedError", message=str(e))
            return

        # for-else: the generator exited without yielding SearchOutput, which
        # violates the §8 contract. Emit a terminal ErrorEvent so callers still
        # see exactly one terminator.
        yield ErrorEvent(
            error_code="NoFinalResult",
            message="execute_core_search exited without yielding SearchOutput",
        )

    @classmethod
    def from_runtime(
        cls,
        rt: SearchRuntime,
        *,
        embed: EmbedSearch | None = None,
        attribute: AttributeSearch | None = None,
        critic: CriticAgent | None = None,
        vlm_analyzer: VLMAnalyzer | None = None,
        vst: VSTSnapshot | None = None,
        behavior_es: ElasticIndex | None = None,
        use_attribute_search: bool = False,
    ) -> Search:
        """Construct from SearchRuntime.

        Query decomposition is NAT-owned and must happen before this primitive
        receives SearchInput.

        Critic policy: explicit `critic` wins; else if rt.enable_critic and
        vlm_analyzer is provided, build via CriticAgent.from_runtime; else None.
        """
        from ..clients.elastic import ElasticClient
        from ..clients.vst import VSTClient

        # vst is needed only for CriticAgent construction (the orchestrator
        # itself uses URL strings via the config). Build once for that.
        vst_obj = vst or VSTClient.from_runtime(rt)
        if critic is not None:
            critic_obj = critic
        elif rt.enable_critic and vlm_analyzer is not None:
            critic_obj = CriticAgent.from_runtime(
                rt,
                vlm_analyzer=vlm_analyzer,
                vst=vst_obj,
                time_format=rt.critic_time_format,
                num_videos_to_evaluate=rt.critic_evaluation_count,
            )
        else:
            critic_obj = None

        behavior_es_obj = behavior_es or ElasticClient.from_runtime_behavior(rt)

        return cls(
            embed=embed or EmbedSearch.from_runtime(rt),
            attribute=attribute or AttributeSearch.from_runtime(rt),
            critic=critic_obj,
            behavior_es=behavior_es_obj,
            behavior_index=rt.behavior_index,
            behavior_index_wildcard=rt.behavior_index_wildcard,
            use_attribute_search=use_attribute_search,
            fusion_method=rt.fusion_method,
            w_attribute=rt.w_attribute,
            w_embed=rt.w_embed,
            rrf_k=rt.rrf_k,
            rrf_w=rt.rrf_w,
            top_percent_filter=rt.top_percent_filter,
            embed_confidence_threshold=rt.embed_confidence_threshold,
            search_max_iterations=rt.search_max_iterations,
            default_max_results=rt.default_max_results,
            vst_internal_url=rt.vst_internal_url,
            vst_external_url=rt.vst_external_url,
        )

    async def aclose(self) -> None:
        coros: list = [
            self._embed.aclose(),
            self._attribute.aclose(),
            self._behavior_es.aclose(),
        ]
        if self._critic is not None:
            coros.append(self._critic.aclose())
        await asyncio.gather(*coros, return_exceptions=True)
