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
"""Pure, dependency-free fusion helpers for the Search orchestrator.

Every function here is synchronous and side-effect-free: it transforms already
retrieved embed/attribute results into ranked :class:`SearchResult` lists. This
keeps the fusion math, video-data assembly, consecutive-chunk merging,
top-percent filtering, and embed->result coercion unit-testable with plain data
(no async, no mocks, no live backends).

Imports are limited to the library's models and ``_internal`` helpers so the
module stays free of IO and framework dependencies.
"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
import logging
from typing import TYPE_CHECKING
from typing import Any

from vss_core._foundation.time import datetime_to_iso8601
from vss_core._foundation.time import iso8601_to_datetime

from .._internal.coerce import _coerce_float
from .._internal.coerce import _coerce_str
from ..errors import InvalidInputError
from ..models.attribute_search import AttributeSearchResult
from ..models.search import SearchResult

if TYPE_CHECKING:
    from ..models.embed_search import EmbedSearchOutput
    from ..models.tag_search import TagSearchOutput

logger = logging.getLogger(__name__)

# Consecutive chunks from the same sensor are only merged when their similarity
# scores are within this ratio of each other, so a strong hit and a weak hit
# that merely overlap in time are not collapsed into one result.
SIMILARITY_RATIO_THRESHOLD = 0.9


# =============================================================================
# Fusion-candidate assembly
# =============================================================================


@dataclass
class FusionCandidate:
    """One video's fused inputs: its embed result plus normalised attribute data.

    Replaces the untyped ``dict[str, Any]`` the fusion math used to pass around.
    ``embed_score`` is the embed similarity; ``normalised_attribute_score`` is the
    per-video attribute score sum divided by the attribute count; ``screenshot_url``
    prefers the attribute screenshot and falls back to the embed result's; and
    ``object_ids`` are the de-duplicated behavior object ids matched for the video.
    """

    embed_result: SearchResult
    embed_score: float
    normalised_attribute_score: float
    screenshot_url: str
    object_ids: list[str] = field(default_factory=list)


def _validate_attribute_results(attribute_results: Any) -> list[AttributeSearchResult]:
    """Coerce a per-video attribute payload into validated result items.

    A non-list / empty payload yields an empty list; a single unprocessable item
    is skipped (with a WARNING) rather than sinking the whole video's data.
    """
    if not attribute_results or not isinstance(attribute_results, list):
        return []
    validated: list[AttributeSearchResult] = []
    for item in attribute_results:
        try:
            validated.append(
                item if isinstance(item, AttributeSearchResult) else AttributeSearchResult.model_validate(item)
            )
        except Exception:
            logger.warning("Skipping unprocessable attribute result during fusion", exc_info=True)
            continue
    return validated


def _attribute_score(result: AttributeSearchResult) -> float:
    """Pick a per-result attribute score: frame score when positive, else behavior.

    Both reads are coerced so a null / odd-typed stored score degrades to ``0.0``
    instead of raising.
    """
    frame_score = result.metadata.frame_score
    if frame_score is not None:
        frame = _coerce_float(frame_score)
        if frame > 0.0:
            return frame
    return _coerce_float(result.metadata.behavior_score)


def build_fusion_candidates(
    pairs: list[tuple[SearchResult, Any]],
    attribute_count: int,
) -> list[FusionCandidate]:
    """Assemble one :class:`FusionCandidate` per (embed_result, attribute_results).

    For each video: validate/coerce its attribute payload, sum the per-result
    attribute scores and normalise by ``attribute_count`` (guarded against a zero
    divisor), de-duplicate matched object ids, and select a screenshot url
    (attribute screenshot preferred, embed result as fallback).
    """
    candidates: list[FusionCandidate] = []
    for embed_result, attribute_results in pairs:
        validated = _validate_attribute_results(attribute_results)

        attribute_scores: list[float] = []
        object_ids: list[str] = []
        for result in validated:
            attribute_scores.append(_attribute_score(result))
            oid = _coerce_str(result.metadata.object_id)
            if oid and oid not in object_ids:
                object_ids.append(oid)

        attribute_screenshot_url = _coerce_str(validated[0].screenshot_url) if validated else ""
        normalised_attribute_score = sum(attribute_scores) / attribute_count if attribute_count > 0 else 0.0
        screenshot_url = attribute_screenshot_url or _coerce_str(embed_result.screenshot_url)

        candidates.append(
            FusionCandidate(
                embed_result=embed_result,
                embed_score=_coerce_float(embed_result.similarity),
                normalised_attribute_score=normalised_attribute_score,
                screenshot_url=screenshot_url,
                object_ids=object_ids,
            )
        )
    return candidates


def _result_from_candidate(candidate: FusionCandidate, similarity: float) -> SearchResult:
    """Build a :class:`SearchResult` for a candidate at the given fused score."""
    embed = candidate.embed_result
    return SearchResult(
        video_name=embed.video_name,
        description=embed.description,
        start_time=embed.start_time,
        end_time=embed.end_time,
        sensor_id=embed.sensor_id,
        screenshot_url=candidate.screenshot_url,
        similarity=similarity,
        object_ids=candidate.object_ids,
    )


# =============================================================================
# Fusion math
# =============================================================================


def weighted_linear_fusion(
    candidates: list[FusionCandidate],
    w_embed: float,
    w_attribute: float,
) -> list[SearchResult]:
    """Fuse with a weighted linear combination of embed and attribute scores."""
    scored: list[tuple[float, SearchResult]] = []
    for candidate in candidates:
        fusion_score = w_embed * candidate.embed_score + w_attribute * candidate.normalised_attribute_score
        scored.append((fusion_score, _result_from_candidate(candidate, fusion_score)))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [result for _, result in scored]


def rrf_fusion(
    candidates: list[FusionCandidate],
    rrf_k: int,
    rrf_w: float,
) -> list[SearchResult]:
    """Reciprocal Rank Fusion over the embed rank, boosted by attribute score."""
    sorted_candidates = sorted(candidates, key=lambda c: c.embed_score, reverse=True)
    scored: list[tuple[float, SearchResult]] = []
    for rank, candidate in enumerate(sorted_candidates, start=1):
        rrf_score = 1.0 / (rank + rrf_k) + rrf_w * candidate.normalised_attribute_score
        scored.append((rrf_score, _result_from_candidate(candidate, rrf_score)))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [result for _, result in scored]


def rrf_fusion_with_attribute_rank(
    candidates: list[FusionCandidate],
    rrf_k: int,
    rrf_w: float,
) -> list[SearchResult]:
    """RRF combining both the embed rank and the attribute rank.

    Ranks are keyed by list index (rather than object identity) so duplicate
    candidate values never collide.
    """
    indexed = list(enumerate(candidates))
    embed_order = sorted(indexed, key=lambda pair: pair[1].embed_score, reverse=True)
    embed_ranks = {idx: rank for rank, (idx, _) in enumerate(embed_order, start=1)}
    attribute_order = sorted(indexed, key=lambda pair: pair[1].normalised_attribute_score, reverse=True)
    attribute_ranks = {idx: rank for rank, (idx, _) in enumerate(attribute_order, start=1)}

    scored: list[tuple[float, SearchResult]] = []
    for idx, candidate in indexed:
        rank_embed = embed_ranks[idx]
        rank_attribute = attribute_ranks[idx]
        rrf_score = 1.0 / (rank_embed + rrf_k) + rrf_w * (1.0 / (rank_attribute + rrf_k))
        scored.append((rrf_score, _result_from_candidate(candidate, rrf_score)))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [result for _, result in scored]


def apply_fusion(
    candidates: list[FusionCandidate],
    method: str,
    *,
    rrf_k: int,
    rrf_w: float,
    w_embed: float,
    w_attribute: float,
) -> list[SearchResult]:
    """Dispatch to the requested fusion method.

    An unrecognised ``method`` raises :class:`InvalidInputError` so the caller
    surfaces a precise (exit-code-2) error rather than a bare ``ValueError``.
    """
    if method == "weighted_linear":
        return weighted_linear_fusion(candidates, w_embed, w_attribute)
    if method == "rrf":
        return rrf_fusion(candidates, rrf_k, rrf_w)
    if method == "rrf_with_attribute_rank":
        return rrf_fusion_with_attribute_rank(candidates, rrf_k, rrf_w)
    raise InvalidInputError(
        f"Unknown fusion_method: {method!r}. Must be 'weighted_linear', 'rrf', or 'rrf_with_attribute_rank'"
    )


# =============================================================================
# Result post-processing
# =============================================================================


def merge_consecutive_results(results: list[SearchResult]) -> list[SearchResult]:
    """Merge consecutive/overlapping chunks from the same sensor into one result.

    Results without both a parseable start and end timestamp are routed to a
    "no timestamp" bucket and left un-merged; the rest are grouped per sensor,
    sorted by parsed datetime (not raw string, which sorts wrongly across mixed
    ``Z``/``+00:00`` encodings), and merged when they overlap in time and their
    similarities are within :data:`SIMILARITY_RATIO_THRESHOLD`.
    """
    if not results:
        return results

    timestamped: list[SearchResult] = []
    no_timestamp: list[SearchResult] = []
    for r in results:
        if not r.start_time or not r.end_time:
            no_timestamp.append(r)
            continue
        try:
            iso8601_to_datetime(r.start_time)
            iso8601_to_datetime(r.end_time)
            timestamped.append(r)
        except (ValueError, TypeError) as e:
            logger.warning(f"Skipping merge for result with malformed timestamp (sensor={r.sensor_id}): {e}")
            no_timestamp.append(r)

    merged: list[SearchResult] = list(no_timestamp)
    if not timestamped:
        merged.sort(key=lambda r: r.similarity, reverse=True)
        return merged

    by_sensor: dict[str, list[SearchResult]] = {}
    for result in timestamped:
        by_sensor.setdefault(result.sensor_id, []).append(result)

    for sensor_id, sensor_results in by_sensor.items():
        # All results here are in ``timestamped``, so start_time/end_time parse.
        sorted_results = sorted(sensor_results, key=lambda r: iso8601_to_datetime(r.start_time))

        groups: list[list[SearchResult]] = []
        group_chunks: list[SearchResult] = [sorted_results[0]]
        group_end_dt = iso8601_to_datetime(sorted_results[0].end_time)

        for result in sorted_results[1:]:
            result_start_dt = iso8601_to_datetime(result.start_time)
            group_avg_sim = sum(c.similarity for c in group_chunks) / len(group_chunks)
            # Compatible == the two scores are within (1 - threshold) of each other,
            # measured against the larger magnitude. Basing this on the relative
            # difference (|a - b| / max(|a|, |b|)) rather than a raw min/max ratio
            # is exactly equivalent for positive similarities but stays correct for
            # zero/negative cosine scores, where a ratio is meaningless (two
            # negatives yield a ratio > 1 and would otherwise always merge, even a
            # strong -0.1 with a weak -0.9).
            sim_denominator = max(abs(group_avg_sim), abs(result.similarity))
            sim_difference = abs(group_avg_sim - result.similarity)
            sim_compatible = sim_denominator == 0 or (sim_difference / sim_denominator) <= (
                1.0 - SIMILARITY_RATIO_THRESHOLD
            )

            if result_start_dt <= group_end_dt and sim_compatible:
                result_end_dt = iso8601_to_datetime(result.end_time)
                if result_end_dt > group_end_dt:
                    group_end_dt = result_end_dt
                group_chunks.append(result)
            else:
                groups.append(group_chunks)
                group_chunks = [result]
                group_end_dt = iso8601_to_datetime(result.end_time)
        groups.append(group_chunks)

        for group in groups:
            first = group[0]
            end_dt = max(iso8601_to_datetime(g.end_time) for g in group)
            similarity = sum(g.similarity for g in group) / len(group)

            seen_ids: set[str] = set()
            merged_object_ids: list[str] = []
            for g in group:
                for oid in g.object_ids:
                    if oid not in seen_ids:
                        merged_object_ids.append(oid)
                        seen_ids.add(oid)

            merged.append(
                SearchResult(
                    video_name=first.video_name,
                    description=first.description,
                    start_time=first.start_time,
                    end_time=datetime_to_iso8601(end_dt),
                    sensor_id=sensor_id,
                    screenshot_url=first.screenshot_url,
                    similarity=similarity,
                    object_ids=merged_object_ids,
                )
            )

    merged.sort(key=lambda r: r.similarity, reverse=True)
    return merged


def apply_top_percent_filter(results: list[SearchResult], top_pct: float | None) -> list[SearchResult]:
    """Keep only results within ``top_pct`` of the top similarity score.

    A ``top_pct`` outside ``(0, 1)`` (or an empty result set) is a no-op and the
    input is returned as a fresh list.
    """
    if not results or not top_pct or not (0 < top_pct < 1.0):
        return list(results)
    max_sim = max(r.similarity for r in results)
    if max_sim <= 0:
        # "Within top_pct of the max" is only meaningful for a positive max: for a
        # non-positive max, ``max_sim * top_pct`` sits *above* max_sim and would
        # drop every result (including the top one), so leave the set untouched.
        return list(results)
    sim_threshold = max_sim * top_pct
    filtered = [r for r in results if r.similarity >= sim_threshold]
    logger.info(f"Top-percent filter: kept {len(filtered)}/{len(results)} results (>= {sim_threshold:.4f})")
    return filtered


def embed_output_to_search_results(embed_output: EmbedSearchOutput) -> list[SearchResult]:
    """Map an :class:`EmbedSearchOutput` into :class:`SearchResult` items.

    Each field is coerced to its declared type and results with an empty
    ``video_name`` are skipped (they cannot be rendered or clip-verified).
    """
    results: list[SearchResult] = []
    for item in embed_output.results:
        video_name = _coerce_str(item.video_name)
        if not video_name:
            logger.warning("Skipping embed result with empty video_name")
            continue
        results.append(
            SearchResult(
                video_name=video_name,
                description=_coerce_str(item.description),
                start_time=_coerce_str(item.start_time),
                end_time=_coerce_str(item.end_time),
                sensor_id=_coerce_str(item.sensor_id),
                screenshot_url=_coerce_str(item.screenshot_url),
                similarity=_coerce_float(item.similarity_score),
            )
        )
    return results


def tag_output_to_search_results(tag_output: TagSearchOutput) -> list[SearchResult]:
    """Map lexical tag hits into the common SearchResult shape."""
    return [
        SearchResult(
            video_name=item.video_name,
            description=item.description,
            start_time=item.start_time,
            end_time=item.end_time,
            sensor_id=item.sensor_id,
            screenshot_url=item.screenshot_url,
            similarity=item.lexical_score,
        )
        for item in tag_output.results
        if item.video_name
    ]


def _results_overlap(left: SearchResult, right: SearchResult) -> bool:
    if left.sensor_id != right.sensor_id:
        return False
    try:
        left_start = iso8601_to_datetime(left.start_time)
        left_end = iso8601_to_datetime(left.end_time)
        right_start = iso8601_to_datetime(right.start_time)
        right_end = iso8601_to_datetime(right.end_time)
    except (TypeError, ValueError):
        return (
            left.video_name == right.video_name
            and left.start_time == right.start_time
            and left.end_time == right.end_time
        )
    return left_start <= right_end and left_end >= right_start


def weighted_rrf_union(
    provider_results: dict[str, list[SearchResult]],
    *,
    weights: dict[str, float],
    rrf_k: int,
) -> list[SearchResult]:
    """Fuse the union of provider candidates with weighted reciprocal ranks.

    Candidates align by sensor and overlapping interval. A candidate absent
    from a provider contributes zero, so tag-only, embed-only, and
    attribute-only results all remain eligible.
    """
    representatives: list[SearchResult] = []
    scores: list[float] = []
    contributing_providers: list[set[str]] = []
    for provider, results in provider_results.items():
        weight = weights.get(provider, 0.0)
        if weight <= 0:
            continue
        ranked = sorted(results, key=lambda result: result.similarity, reverse=True)
        for rank, result in enumerate(ranked, start=1):
            candidate_index = next(
                (
                    index
                    for index, existing in enumerate(representatives)
                    if provider not in contributing_providers[index] and _results_overlap(existing, result)
                ),
                None,
            )
            contribution = weight / (rrf_k + rank)
            if candidate_index is None:
                representatives.append(result)
                scores.append(contribution)
                contributing_providers.append({provider})
                continue

            existing = representatives[candidate_index]
            seen_ids = set(existing.object_ids)
            object_ids = list(existing.object_ids)
            for object_id in result.object_ids:
                if object_id not in seen_ids:
                    object_ids.append(object_id)
                    seen_ids.add(object_id)
            representatives[candidate_index] = existing.model_copy(
                update={
                    "description": existing.description or result.description,
                    "screenshot_url": existing.screenshot_url or result.screenshot_url,
                    "object_ids": object_ids,
                }
            )
            scores[candidate_index] += contribution
            contributing_providers[candidate_index].add(provider)

    fused = [
        representative.model_copy(update={"similarity": score})
        for representative, score in zip(representatives, scores, strict=True)
    ]
    fused.sort(key=lambda result: result.similarity, reverse=True)
    return fused


def fuse_ranked_union(
    provider_results: dict[str, list[SearchResult]],
    *,
    method: str,
    weights: dict[str, float],
    rrf_k: int,
) -> list[SearchResult]:
    """Dispatch candidate-union fusion using the configured rank method."""
    if method == "weighted_rrf":
        effective_weights = weights
    elif method == "rrf":
        effective_weights = dict.fromkeys(provider_results, 1.0)
    else:
        raise InvalidInputError(f"Unknown union fusion_method: {method!r}. Must be 'weighted_rrf' or 'rrf'")
    return weighted_rrf_union(provider_results, weights=effective_weights, rrf_k=rrf_k)
