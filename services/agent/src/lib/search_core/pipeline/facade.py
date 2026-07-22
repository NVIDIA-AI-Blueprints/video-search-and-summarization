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
"""The deep facade: one call runs the canonical search pipeline.

``run_search`` owns the mode routing, the canonical stage composition, the
defaults and the ordering invariants — everyday callers (the CLI, skills,
evals) never assemble chains by hand. The pipeline algebra underneath
(:mod:`.ranks`, :mod:`.legs`, :mod:`.score`, :mod:`.rank`) is the documented
extension seam for power users.

Routing reproduces the retired ``execute_core_search`` semantics exactly:

1. ``object`` — behavior kNN anchored on existing objects' embeddings
2. ``attribute`` — attribute-only retrieval (no embed leg)
3. ``embed`` — semantic retrieval, with attribute-only *fallback* when embed
   confidence is low and attributes are available
4. ``fusion`` — embed retrieval, per-hit attribute scoring, score fusion

All execution is synchronous; the only IO happens inside the injected
executors carried by :class:`SearchDeps`.
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import TYPE_CHECKING

from lib._foundation.errors import LibraryError

from ..errors import ConfigurationError
from ..models.search import SearchInput
from ..models.search import SearchOutput
from ..models.search import SearchResult
from . import legs
from . import rank
from . import score
from .ranks import Ranks

if TYPE_CHECKING:
    from collections.abc import Callable

    from ..models.attribute_search import AttributeSearchInput
    from ..models.attribute_search import AttributeSearchResult
    from ..models.embed_search import EmbedSearchInput
    from ..models.embed_search import EmbedSearchOutput

logger = logging.getLogger(__name__)

# Downstream request models cap top_k at 1000; clamp the fetch size so an
# oversized caller value never trips a ValidationError mid-pipeline.
_DOWNSTREAM_MAX_TOP_K = 1000


@dataclass(frozen=True)
class SearchDeps:
    """Injected synchronous executors — the facade's entire IO surface.

    ``embed_exec`` / ``attribute_exec`` run one retrieval each;
    ``object_exec`` runs one behavior kNN for one object id;
    ``object_enrich`` optionally decorates deduplicated object results (VST
    screenshots); ``sensor_resolver`` maps a stream id to a sensor id for
    attribute scoring filters. Executors raise the library's typed errors on
    systemic failure.
    """

    embed_exec: Callable[[EmbedSearchInput], EmbedSearchOutput]
    attribute_exec: Callable[[AttributeSearchInput], list[AttributeSearchResult]] | None = None
    object_exec: Callable[[str], list[AttributeSearchResult]] | None = None
    object_enrich: Callable[[list[AttributeSearchResult]], list[AttributeSearchResult]] | None = None
    sensor_resolver: Callable[[str], str] | None = None


@dataclass(frozen=True)
class SearchParams:
    """Canonical pipeline configuration (the retired ``SearchConfig``)."""

    default_max_results: int = 10
    embed_confidence_threshold: float = 0.5
    fusion_method: str = "rrf"
    w_attribute: float = 0.55
    w_embed: float = 0.35
    rrf_k: int = 60
    rrf_w: float = 0.5
    top_percent_filter: float | None = None


def to_search_output(ranks: Ranks) -> SearchOutput:
    """Export the chain result at the boundary: current score == similarity."""
    key = ranks.score_key
    return SearchOutput(
        data=[
            SearchResult(
                video_name=h.video_name,
                description=h.description,
                start_time=h.start_time,
                end_time=h.end_time,
                sensor_id=h.sensor_id,
                screenshot_url=h.screenshot_url,
                similarity=h.score(key),
                object_ids=list(h.object_ids),
            )
            for h in ranks.hits
        ],
        search_messages=list(ranks.messages),
    )


def run_search(
    search_input: SearchInput,
    deps: SearchDeps,
    params: SearchParams | None = None,
) -> SearchOutput:
    """Execute one search end-to-end through the canonical pipeline."""
    params = params or SearchParams()
    search_input.validate_semantics()

    top_k = search_input.top_k if search_input.top_k is not None else params.default_max_results
    original_top_k = top_k
    top_k = min(top_k, _DOWNSTREAM_MAX_TOP_K)

    # ----- object mode: behavior kNN by existing objects' embeddings -----
    if search_input.search_mode == "object":
        assert search_input.object_ids is not None
        if deps.object_exec is None:
            raise ConfigurationError("object_exec is required for object_id re-search")
        ranks = (
            Ranks.empty()
            | legs.retrieve(
                legs.object_similarity(
                    deps.object_exec,
                    object_ids=search_input.object_ids,
                    top_k=top_k,
                    enrich=deps.object_enrich,
                )
            )
            | rank.rank_by(rank.ATTRIBUTE)
        )
        return to_search_output(ranks)

    attributes = [attr.strip() for attr in search_input.attributes if attr.strip()]
    attribute_available = deps.attribute_exec is not None and bool(attributes)

    # ----- attribute-only mode -----
    if search_input.search_mode == "attribute" and attribute_available:
        assert deps.attribute_exec is not None
        ranks = _attribute_only(search_input, deps.attribute_exec, attributes, min(original_top_k, top_k))
    else:
        # ----- embed retrieval (embed + fusion modes) -----
        ranks = (
            Ranks.empty()
            | legs.retrieve(
                legs.embed(
                    deps.embed_exec,
                    query=search_input.query,
                    description=search_input.description,
                    source_type=search_input.source_type,
                    video_sources=search_input.video_sources,
                    timestamp_start=search_input.timestamp_start,
                    timestamp_end=search_input.timestamp_end,
                    top_k=top_k,
                    min_cosine_similarity=search_input.min_cosine_similarity,
                )
            )
            | rank.rank_by(rank.EMBED)
        )

        if attribute_available:
            assert deps.attribute_exec is not None
            max_embed_score = max((h.score(rank.EMBED) for h in ranks.hits), default=0.0)

            if search_input.search_mode == "fusion" and not ranks.hits:
                logger.info("Explicit fusion search has no embed candidates; preserving an empty fusion result")
                ranks = ranks.with_message(
                    "Fusion search found no semantic candidates; attribute-only fallback was not used."
                )
            elif search_input.search_mode != "fusion" and (
                not ranks.hits or max_embed_score < params.embed_confidence_threshold
            ):
                logger.info(
                    f"Embed candidates absent or confidence low (max={max_embed_score:.3f}, "
                    f"threshold={params.embed_confidence_threshold:.3f}). Falling back to attribute-only."
                )
                ranks = _attribute_only(search_input, deps.attribute_exec, attributes, top_k, carry_messages_from=ranks)
            elif search_input.search_mode == "fusion" and ranks.hits:
                if max_embed_score < params.embed_confidence_threshold:
                    logger.info(
                        "Explicit fusion search is below the embed confidence threshold "
                        "(max=%.3f, threshold=%.3f); preserving the requested fusion route",
                        max_embed_score,
                        params.embed_confidence_threshold,
                    )
                ranks = (
                    ranks
                    | score.attribute_scores(
                        deps.attribute_exec,
                        attributes=attributes,
                        source_type=search_input.source_type,
                        sensor_resolver=deps.sensor_resolver,
                    )
                    | rank.fuse.by_method(
                        params.fusion_method,
                        rrf_k=params.rrf_k,
                        rrf_w=params.rrf_w,
                        w_embed=params.w_embed,
                        w_attribute=params.w_attribute,
                    )
                )

    # ----- shared post-processing: filter, merge, truncate -----
    ranks = ranks | rank.top_percent(params.top_percent_filter) | rank.merge_consecutive | rank.take(original_top_k)
    return to_search_output(ranks)


def _attribute_only(
    search_input: SearchInput,
    attribute_exec: Callable[[AttributeSearchInput], list[AttributeSearchResult]],
    attributes: list[str],
    top_k: int,
    *,
    carry_messages_from: Ranks | None = None,
) -> Ranks:
    """Attribute-only retrieval (append mode), with degrade-to-empty semantics.

    Systemic failures (missing index, backend unreachable, invalid input)
    re-raise so callers get precise errors; any other unexpected error degrades
    to an empty result with an observable diagnostic message, so an empty
    result is distinguishable from a genuine no-matches outcome.
    """
    seed = Ranks.empty() if carry_messages_from is None else carry_messages_from.with_hits(())
    leg = legs.attribute(
        attribute_exec,
        attributes=attributes,
        source_type=search_input.source_type,
        video_sources=search_input.video_sources,
        timestamp_start=search_input.timestamp_start,
        timestamp_end=search_input.timestamp_end,
        top_k=top_k,
        min_similarity=0.0,
    )
    try:
        return seed | legs.retrieve(leg) | rank.rank_by(rank.ATTRIBUTE)
    except LibraryError:
        raise
    except Exception as e:
        logger.error(f"Attribute-only search failed: {e}", exc_info=True)
        return seed.with_message("Attribute search degraded; returning partial/empty results.").with_hits(
            (), score_key=rank.ATTRIBUTE
        )
