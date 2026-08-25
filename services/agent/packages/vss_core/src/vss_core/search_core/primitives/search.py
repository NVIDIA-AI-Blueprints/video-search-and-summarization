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
"""Search — orchestrator that fuses embed_search + attribute_search.

Self-contained: all orchestration logic lives in ``_search_helpers.py`` (thin
async wiring) and ``_fusion.py`` (pure fusion math) under this package.

Query decomposition happens before the library is called; the orchestrator
consumes prepared ``SearchInput`` fields only.
"""

from __future__ import annotations

import asyncio
from contextlib import aclosing
import json
from types import SimpleNamespace
from typing import TYPE_CHECKING
from typing import Any
from typing import Protocol

from pydantic import ValidationError

from vss_core._foundation.errors import ConfigurationError
from vss_core._foundation.errors import LibraryError

from .._internal.embed_translation import params_to_embed_input
from ..errors import InvalidInputError
from ..events import ErrorEvent
from ..events import FinalResultEvent
from ..events import SearchEvent
from ..events import StatusEvent
from ..models.attribute_search import AttributeSearchInput
from ..models.embed_search import EmbedSearchInput
from ..models.search import SearchInput
from ..models.search import SearchOutput
from ..models.tag_search import TagSearchInput
from . import _search_helpers
from .attribute_search import AttributeSearch
from .embed_search import EmbedSearch
from .tag_search import TagSearch

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from collections.abc import Callable

    from ..clients.protocols import ElasticIndex
    from ..models.common import FusionMethod
    from ..runtime import SearchRuntime


class _SupportsRun(Protocol):
    """The single-method surface :class:`_PrimitiveAdapter` drives.

    Each wrapped primitive (``EmbedSearch`` / ``AttributeSearch``) exposes an
    ``async run(inp) -> out``; their concrete input
    and output types differ, so the payload/return stay ``Any`` while the method
    contract itself is typed.
    """

    async def run(self, inp: Any) -> Any: ...


class _AttributeSearchUnavailable:
    """Stands in for the attribute leg when no RT-CV endpoint is configured.

    ``Search`` composes an embedding leg and an attribute leg, but only some of
    its modes use the latter. Building the real ``AttributeSearch`` eagerly
    made every mode require RT-CV, so a deployment running embeddings without
    the CV service could not run an embedding search at all. This satisfies the
    same one-method surface and fails only if a mode actually invokes it.
    """

    def __init__(self, detail: str = "rtvi_cv_endpoint is not configured") -> None:
        self._detail = detail

    async def run(self, inp: Any) -> Any:  # noqa: ARG002 - signature is the _SupportsRun contract
        raise ConfigurationError(
            f"attribute search is unavailable: {self._detail}. "
            f"Only search_mode='embed' works without an RT-CV endpoint."
        )

    async def aclose(self) -> None:
        return None


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
        primitive: _SupportsRun,
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
            # Forward exclusions that live alongside ``params`` in the envelope.
            return params_to_embed_input(
                payload.get("params") or {},
                payload.get("source_type", "video_file"),
                exclude_videos=payload.get("exclude_videos"),
            )
        # Map a Pydantic ValidationError (e.g. top_k out of bounds) to a typed
        # InvalidInputError so bad sizes land on exit code 2, not a masked
        # backend/unexpected fault.
        try:
            return EmbedSearchInput(**payload)
        except ValidationError as exc:
            raise InvalidInputError(f"Invalid embed-search input: {exc}") from exc
    if hasattr(payload, "model_dump"):
        try:
            return EmbedSearchInput.model_validate(payload.model_dump())
        except ValidationError as exc:
            raise InvalidInputError(f"Invalid embed-search input: {exc}") from exc
    raise TypeError(f"cannot coerce {type(payload).__name__} to EmbedSearchInput")


def _coerce_attribute_payload(payload: Any) -> AttributeSearchInput:
    if isinstance(payload, AttributeSearchInput):
        return payload
    # Map a Pydantic ValidationError (e.g. top_k out of bounds) to a typed
    # InvalidInputError so bad sizes land on exit code 2.
    if isinstance(payload, dict):
        try:
            return AttributeSearchInput(**payload)
        except ValidationError as exc:
            raise InvalidInputError(f"Invalid attribute-search input: {exc}") from exc
    if hasattr(payload, "model_dump"):
        try:
            return AttributeSearchInput.model_validate(payload.model_dump())
        except ValidationError as exc:
            raise InvalidInputError(f"Invalid attribute-search input: {exc}") from exc
    raise TypeError(f"cannot coerce {type(payload).__name__} to AttributeSearchInput")


def _wrap_embed(primitive: EmbedSearch) -> _PrimitiveAdapter:
    return _PrimitiveAdapter(primitive, _coerce_embed_payload)


def _wrap_attribute(primitive: AttributeSearch | _AttributeSearchUnavailable) -> _PrimitiveAdapter:
    # Orchestrator expects a bare list of AttributeSearchResult, not the envelope.
    return _PrimitiveAdapter(primitive, _coerce_attribute_payload, unwrap_output=lambda out: out.results)


def _coerce_tag_payload(payload: Any) -> TagSearchInput:
    if isinstance(payload, TagSearchInput):
        return payload
    if isinstance(payload, dict):
        try:
            return TagSearchInput(**payload)
        except ValidationError as exc:
            raise InvalidInputError(f"Invalid tag-search input: {exc}") from exc
    if hasattr(payload, "model_dump"):
        try:
            return TagSearchInput.model_validate(payload.model_dump())
        except ValidationError as exc:
            raise InvalidInputError(f"Invalid tag-search input: {exc}") from exc
    raise TypeError(f"cannot coerce {type(payload).__name__} to TagSearchInput")


def _wrap_tag(primitive: TagSearch) -> _PrimitiveAdapter:
    return _PrimitiveAdapter(primitive, _coerce_tag_payload)


def _attribute_leg(rt: SearchRuntime) -> AttributeSearch | _AttributeSearchUnavailable:
    """The attribute leg, or a stand-in when the runtime has no RT-CV endpoint."""
    if not (rt.rtvi_cv_endpoint or "").strip():
        return _AttributeSearchUnavailable()
    return AttributeSearch.from_runtime(rt)


class Search:
    """Search orchestrator.

    Library shape: takes SearchInput, returns SearchOutput; `.stream()` yields
    typed `SearchEvent` instances (StatusEvent / FinalResultEvent / ErrorEvent).

    Agent-mode query decomposition is out of scope for this library; callers
    populate prepared SearchInput fields before invoking core search.
    """

    def __init__(
        self,
        *,
        embed: EmbedSearch,
        attribute: AttributeSearch | _AttributeSearchUnavailable,
        behavior_es: ElasticIndex,
        behavior_index: str,
        tag: TagSearch | None = None,
        behavior_index_wildcard: str = "mdx-behavior-*",
        fusion_method: FusionMethod = "weighted_rrf",
        w_attribute: float = 0.55,
        w_embed: float = 0.35,
        w_tag: float = 0.45,
        rrf_k: int = 60,
        rrf_w: float = 0.5,
        top_percent_filter: float | None = None,
        embed_confidence_threshold: float = 0.1,
        merge_adjacent: bool = True,
        default_max_results: int = 10,
        # VST URLs are needed by execute_core_search via its config object
        vst_internal_url: str = "",
        vst_external_url: str = "",
        owns_embed: bool = False,
        owns_attribute: bool = False,
        owns_tag: bool = False,
        owns_behavior_es: bool = False,
    ) -> None:
        self._embed = embed
        self._attribute = attribute
        self._tag = tag
        self._behavior_es = behavior_es
        self._owns_embed = owns_embed
        self._owns_attribute = owns_attribute
        self._owns_tag = owns_tag
        self._owns_behavior_es = owns_behavior_es

        # Pre-build the adapters once; they're stateless wrappers around
        # immutable primitive references, so reusing them across calls is safe.
        self._embed_adapter = _wrap_embed(embed)
        self._attr_adapter = _wrap_attribute(attribute)
        self._tag_adapter = _wrap_tag(tag) if tag is not None else None

        # Pre-build the duck-typed config that execute_core_search reads by
        # attribute. All fields are determined at construction; no per-call
        # mutation.
        self._config = SimpleNamespace(
            attribute_search_tool="attribute_search",
            embed_confidence_threshold=embed_confidence_threshold,
            merge_adjacent=merge_adjacent,
            default_max_results=default_max_results,
            fusion_method=fusion_method,
            w_attribute=w_attribute,
            w_embed=w_embed,
            w_tag=w_tag,
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
        inp.validate_semantics()
        return await _search_helpers.execute_core_search_wrapper(
            search_input=inp,
            embed_search=self._embed_adapter,
            config=self._config,
            attribute_search_fn=self._attr_adapter,
            tag_search=self._tag_adapter,
            behavior_es=self._behavior_es,
        )

    async def stream(self, inp: SearchInput) -> AsyncIterator[SearchEvent]:
        """Streaming: translate AgentMessageChunk → SearchEvent. Exactly one
        terminal event (FinalResultEvent or ErrorEvent) is emitted.
        """
        try:
            inp.validate_semantics()
            core_updates = _search_helpers.execute_core_search(
                search_input=inp,
                embed_search=self._embed_adapter,
                config=self._config,
                attribute_search_fn=self._attr_adapter,
                tag_search=self._tag_adapter,
                behavior_es=self._behavior_es,
            )
            async with aclosing(core_updates):
                async for chunk in core_updates:
                    if isinstance(chunk, SearchOutput):
                        yield FinalResultEvent(output=chunk)
                        return
                    # The other arm of the union is AgentMessageChunk.
                    yield StatusEvent(stage=chunk.type.value, message=chunk.content)
        except LibraryError as e:
            yield ErrorEvent(error_code=type(e).__name__, message=str(e))
            return
        except Exception as e:
            yield ErrorEvent(error_code="UnexpectedError", message=str(e))
            return

        # The generator exited without yielding SearchOutput, which violates the
        # streaming contract. Emit a terminal ErrorEvent so callers still see
        # exactly one terminator.
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
        tag: TagSearch | None = None,
        behavior_es: ElasticIndex | None = None,
    ) -> Search:
        """Construct from SearchRuntime.

        Query decomposition must happen before this primitive receives
        SearchInput.

        """
        from ..clients.elastic import ElasticClient

        owns_embed = embed is None
        owns_attribute = attribute is None
        owns_tag = tag is None
        owns_behavior_es = behavior_es is None
        behavior_es_obj = behavior_es if behavior_es is not None else ElasticClient.from_runtime_behavior(rt)

        return cls(
            embed=embed if embed is not None else EmbedSearch.from_runtime(rt),
            attribute=attribute if attribute is not None else _attribute_leg(rt),
            tag=tag if tag is not None else TagSearch.from_runtime(rt),
            behavior_es=behavior_es_obj,
            behavior_index=rt.behavior_index,
            behavior_index_wildcard=rt.behavior_index_wildcard,
            fusion_method=rt.fusion_method,
            w_attribute=rt.w_attribute,
            w_embed=rt.w_embed,
            w_tag=rt.w_tag,
            rrf_k=rt.rrf_k,
            rrf_w=rt.rrf_w,
            top_percent_filter=rt.top_percent_filter,
            embed_confidence_threshold=rt.embed_confidence_threshold,
            merge_adjacent=rt.merge_adjacent,
            default_max_results=rt.default_max_results,
            vst_internal_url=rt.require("vst_internal_url"),
            vst_external_url=rt.require("vst_external_url"),
            owns_embed=owns_embed,
            owns_attribute=owns_attribute,
            owns_tag=owns_tag,
            owns_behavior_es=owns_behavior_es,
        )

    async def aclose(self) -> None:
        coros: list = []
        if self._owns_embed:
            coros.append(self._embed.aclose())
        if self._owns_attribute:
            coros.append(self._attribute.aclose())
        if self._owns_tag and self._tag is not None:
            coros.append(self._tag.aclose())
        if self._owns_behavior_es:
            coros.append(self._behavior_es.aclose())
        if coros:
            await asyncio.gather(*coros, return_exceptions=True)
