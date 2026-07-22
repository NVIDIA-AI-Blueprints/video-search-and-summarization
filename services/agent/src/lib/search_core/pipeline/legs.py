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
"""Retrieval legs and the ``retrieve`` stage.

A :class:`Leg` is a self-contained retrieval value: query, parameters and an
**injected synchronous executor** bound at construction (the leg never creates
its own clients — dependencies are passed in). ``retrieve(leg)`` is the only
stage kind allowed to add candidates; it union-appends the leg's hits into the
incoming :class:`~.ranks.Ranks` and records the leg's own ordering for
rank-based fusion.

Executors raise the library's typed errors (:class:`LibraryError` subclasses)
on systemic failure; those propagate to the caller untouched. Data-shaped
oddities degrade per-hit with a warning, never sinking the whole leg.
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import TYPE_CHECKING

from lib._foundation.errors import LibraryError

from .._internal.coerce import _coerce_float
from .._internal.coerce import _coerce_str
from ..models.attribute_search import AttributeSearchInput
from ..models.attribute_search import AttributeSearchResult
from ..models.embed_search import EmbedSearchInput
from .rank import ATTRIBUTE
from .rank import EMBED
from .ranks import Hit
from .ranks import Ranks

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import datetime

    from ..models.common import SourceType
    from ..models.embed_search import EmbedSearchOutput
    from .ranks import Stage

logger = logging.getLogger(__name__)

# Missing-object-id sentinel used by behavior hit mapping.
_UNKNOWN_ID = "unknown"


@dataclass(frozen=True)
class Leg:
    """One retrieval leg: a name (its score key / provenance slot) + a fetch."""

    name: str
    fetch: Callable[[], list[Hit]]


def retrieve(leg: Leg) -> Stage:
    """The candidate-adding stage: union-append one leg's hits (see §ranks)."""

    def _apply(ranks: Ranks) -> Ranks:
        return ranks.union(leg.name, leg.fetch())

    return _apply


# =============================================================================
# Hit mapping (ported from _fusion.embed_output_to_search_results and
# _search_helpers.attribute_result_to_search_result)
# =============================================================================


def _embed_hit(item: object) -> Hit | None:
    video_name = _coerce_str(getattr(item, "video_name", ""))
    if not video_name:
        logger.warning("Skipping embed result with empty video_name")
        return None
    return Hit(
        video_name=video_name,
        description=_coerce_str(getattr(item, "description", "")),
        start_time=_coerce_str(getattr(item, "start_time", "")),
        end_time=_coerce_str(getattr(item, "end_time", "")),
        sensor_id=_coerce_str(getattr(item, "sensor_id", "")),
        screenshot_url=_coerce_str(getattr(item, "screenshot_url", "")),
        scores={EMBED: _coerce_float(getattr(item, "similarity_score", 0.0))},
    )


def attribute_hit(result: AttributeSearchResult, *, score_key: str = ATTRIBUTE) -> Hit:
    """Map one attribute/behavior result to a :class:`Hit`.

    Every field read from the (untrusted) payload is coerced so a null or
    odd-typed value degrades gracefully. ``object_id == "0"`` is preserved; a
    *missing* object id keeps the tuple empty (attribute hits need not be
    associated with a tracked object). Frame score is preferred over behavior
    score when positive — the same selection the retired fusion helpers used.
    """
    metadata = result.metadata
    frame_score = metadata.frame_score
    if frame_score is not None and _coerce_float(frame_score) > 0.0:
        score = _coerce_float(frame_score)
    else:
        score = _coerce_float(metadata.behavior_score)
    start_time = _coerce_str(metadata.start_time) or _coerce_str(metadata.frame_timestamp)
    end_time = _coerce_str(metadata.end_time) or _coerce_str(metadata.frame_timestamp)
    video_name = _coerce_str(metadata.video_name) or _coerce_str(metadata.sensor_id)
    object_id = _coerce_str(metadata.object_id)
    return Hit(
        video_name=video_name,
        description=f"Attribute match at {metadata.frame_timestamp or 'unknown time'}",
        start_time=start_time,
        end_time=end_time,
        sensor_id=_coerce_str(metadata.sensor_id),
        screenshot_url=_coerce_str(result.screenshot_url),
        object_ids=(object_id,) if object_id else (),
        scores={score_key: score},
    )


# =============================================================================
# Leg constructors
# =============================================================================


def embed(
    exec_fn: Callable[[EmbedSearchInput], EmbedSearchOutput],
    *,
    query: str,
    description: str | None = None,
    source_type: SourceType = "video_file",
    video_sources: list[str] | None = None,
    timestamp_start: datetime | None = None,
    timestamp_end: datetime | None = None,
    top_k: int | None = None,
    min_cosine_similarity: float = 0.0,
) -> Leg:
    """Semantic clip retrieval over the embedding index (score key ``embed``)."""
    request = EmbedSearchInput(
        query=query,
        description=description,
        source_type=source_type,
        video_sources=video_sources,
        timestamp_start=timestamp_start,
        timestamp_end=timestamp_end,
        top_k=top_k,
        min_cosine_similarity=min_cosine_similarity,
    )

    def _fetch() -> list[Hit]:
        output = exec_fn(request)
        return [hit for item in output.results if (hit := _embed_hit(item)) is not None]

    return Leg(name=EMBED, fetch=_fetch)


def attribute(
    exec_fn: Callable[[AttributeSearchInput], list[AttributeSearchResult]],
    *,
    attributes: list[str],
    source_type: SourceType = "video_file",
    video_sources: list[str] | None = None,
    timestamp_start: datetime | None = None,
    timestamp_end: datetime | None = None,
    top_k: int | None = None,
    min_similarity: float = 0.0,
) -> Leg:
    """Object/CV retrieval over the behavior index (score key ``attribute``).

    Append-mode semantics (``fuse_multi_attribute=False``): each attribute
    contributes its own candidates; hits are sorted by score, best first.
    """
    request = AttributeSearchInput(
        query=attributes,
        source_type=source_type,
        video_sources=video_sources,
        timestamp_start=timestamp_start,
        timestamp_end=timestamp_end,
        top_k=top_k,
        min_similarity=min_similarity,
        fuse_multi_attribute=False,
    )

    def _fetch() -> list[Hit]:
        hits: list[Hit] = []
        for result in exec_fn(request):
            try:
                hits.append(attribute_hit(result))
            except Exception as e:
                logger.warning(f"Failed to convert attribute result: {e}")
                continue
        hits.sort(key=lambda h: h.score(ATTRIBUTE), reverse=True)
        return hits

    return Leg(name=ATTRIBUTE, fetch=_fetch)


def object_similarity(
    exec_fn: Callable[[str], list[AttributeSearchResult]],
    *,
    object_ids: list[int],
    top_k: int,
    enrich: Callable[[list[AttributeSearchResult]], list[AttributeSearchResult]] | None = None,
) -> Leg:
    """Object re-search: behavior kNN anchored on existing objects' embeddings.

    Per-object failures degrade to an empty contribution (a benign "object not
    found" surfaces as a plain ``ValueError`` from the executor); systemic
    :class:`LibraryError` propagates. Results de-duplicate per object id (best
    behavior score wins; hits carrying the ``unknown`` sentinel cannot identify
    the same object and are kept un-merged), are capped at ``top_k``, then
    passed to the optional ``enrich`` hook (e.g. VST screenshot enrichment)
    before hit mapping. Score key: ``attribute``.
    """

    def _fetch() -> list[Hit]:
        all_results: list[AttributeSearchResult] = []
        for oid in object_ids:
            try:
                all_results.extend(exec_fn(str(oid)))
            except LibraryError:
                raise
            except Exception as e:
                logger.warning(f"Object ID {oid} search failed: {e}", exc_info=True)

        seen: dict[str, AttributeSearchResult] = {}
        unmergeable: list[AttributeSearchResult] = []
        for r in all_results:
            key = _coerce_str(r.metadata.object_id)
            if not key or key == _UNKNOWN_ID:
                unmergeable.append(r)
                continue
            if key not in seen or _coerce_float(r.metadata.behavior_score) > _coerce_float(
                seen[key].metadata.behavior_score
            ):
                seen[key] = r
        deduped = sorted(
            [*seen.values(), *unmergeable],
            key=lambda r: _coerce_float(r.metadata.behavior_score),
            reverse=True,
        )[:top_k]
        if enrich is not None:
            deduped = enrich(deduped)
        return [attribute_hit(r) for r in deduped]

    return Leg(name=ATTRIBUTE, fetch=_fetch)
