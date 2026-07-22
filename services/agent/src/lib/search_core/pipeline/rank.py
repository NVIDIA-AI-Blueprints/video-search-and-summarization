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
"""Rankers: pure, total ``Ranks -> Ranks`` stages that reorder or filter.

Every function here is synchronous, side-effect-free and never raises on data
(an unknown fusion method is an :class:`InvalidInputError` — a caller bug, not
a data condition). Empty ``Ranks`` flow through unchanged. The fusion math is
ported verbatim from the retired ``primitives/_fusion.py``, re-expressed over
:class:`~.ranks.Hit` score maps.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from lib._foundation.time import datetime_to_iso8601
from lib._foundation.time import iso8601_to_datetime

from ..errors import InvalidInputError
from .ranks import Hit
from .ranks import Ranks

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)

# Consecutive chunks from the same sensor are only merged when their similarity
# scores are within this ratio of each other, so a strong hit and a weak hit
# that merely overlap in time are not collapsed into one result.
SIMILARITY_RATIO_THRESHOLD = 0.9

# Score keys written by the standard legs/scorers.
EMBED = "embed"
ATTRIBUTE = "attribute"
FUSION = "fusion"


# =============================================================================
# Generic rankers
# =============================================================================


def rank_by(key: str) -> Callable[[Ranks], Ranks]:
    """Reorder hits by the named score, descending; make it the current key."""

    def _apply(ranks: Ranks) -> Ranks:
        ordered = sorted(ranks.hits, key=lambda h: h.score(key), reverse=True)
        return ranks.with_hits(ordered, score_key=key)

    return _apply


def filter_by(key: str, *, minimum: float) -> Callable[[Ranks], Ranks]:
    """Drop hits whose named score is below ``minimum`` (order preserved).

    Usable with any score key in any chain — this is what makes a similarity
    threshold compose with fusion instead of applying to one mode only.
    """

    def _apply(ranks: Ranks) -> Ranks:
        return ranks.with_hits(h for h in ranks.hits if h.score(key) >= minimum)

    return _apply


def take(n: int) -> Callable[[Ranks], Ranks]:
    """Keep the first ``n`` hits of the current ordering."""

    def _apply(ranks: Ranks) -> Ranks:
        return ranks.with_hits(ranks.hits[:n])

    return _apply


def top_percent(top_pct: float | None) -> Callable[[Ranks], Ranks]:
    """Keep hits within ``top_pct`` of the top current score.

    A ``top_pct`` outside ``(0, 1)``, an empty set, or a non-positive maximum
    (where the threshold would sit *above* the maximum and drop everything) is
    a no-op.
    """

    def _apply(ranks: Ranks) -> Ranks:
        if not ranks.hits or not top_pct or not (0 < top_pct < 1.0):
            return ranks
        key = ranks.score_key
        max_score = max(h.score(key) for h in ranks.hits)
        if max_score <= 0:
            return ranks
        threshold = max_score * top_pct
        kept = [h for h in ranks.hits if h.score(key) >= threshold]
        logger.info(f"Top-percent filter: kept {len(kept)}/{len(ranks.hits)} results (>= {threshold:.4f})")
        return ranks.with_hits(kept)

    return _apply


# =============================================================================
# Fusion (embed + attribute score fusion — ported from _fusion.py)
# =============================================================================


def _fused(hit: Hit, fusion_score: float) -> Hit:
    return hit.with_scores(**{FUSION: fusion_score})


def _weighted_linear(ranks: Ranks, w_embed: float, w_attribute: float) -> Ranks:
    """Weighted linear combination of the embed and attribute scores."""
    fused = [_fused(h, w_embed * h.score(EMBED) + w_attribute * h.score(ATTRIBUTE)) for h in ranks.hits]
    fused.sort(key=lambda h: h.score(FUSION), reverse=True)
    return ranks.with_hits(fused, score_key=FUSION)


def _rrf(ranks: Ranks, rrf_k: int, rrf_w: float) -> Ranks:
    """Reciprocal Rank Fusion over the embed rank, boosted by attribute score."""
    by_embed = sorted(ranks.hits, key=lambda h: h.score(EMBED), reverse=True)
    fused = [
        _fused(hit, 1.0 / (rank + rrf_k) + rrf_w * hit.score(ATTRIBUTE)) for rank, hit in enumerate(by_embed, start=1)
    ]
    fused.sort(key=lambda h: h.score(FUSION), reverse=True)
    return ranks.with_hits(fused, score_key=FUSION)


def _rrf_with_attribute_rank(ranks: Ranks, rrf_k: int, rrf_w: float) -> Ranks:
    """RRF combining both the embed rank and the attribute rank.

    Ranks are keyed by list index (rather than object identity) so duplicate
    hit values never collide.
    """
    indexed = list(enumerate(ranks.hits))
    embed_order = sorted(indexed, key=lambda pair: pair[1].score(EMBED), reverse=True)
    embed_ranks = {idx: rank for rank, (idx, _) in enumerate(embed_order, start=1)}
    attribute_order = sorted(indexed, key=lambda pair: pair[1].score(ATTRIBUTE), reverse=True)
    attribute_ranks = {idx: rank for rank, (idx, _) in enumerate(attribute_order, start=1)}

    fused = [
        _fused(hit, 1.0 / (embed_ranks[idx] + rrf_k) + rrf_w * (1.0 / (attribute_ranks[idx] + rrf_k)))
        for idx, hit in indexed
    ]
    fused.sort(key=lambda h: h.score(FUSION), reverse=True)
    return ranks.with_hits(fused, score_key=FUSION)


class fuse:  # noqa: N801  namespace for fusion stage constructors
    """Fusion stage constructors: ``fuse.rrf(...)``, ``fuse.weighted_linear(...)``."""

    @staticmethod
    def weighted_linear(*, w_embed: float, w_attribute: float) -> Callable[[Ranks], Ranks]:
        return lambda ranks: _weighted_linear(ranks, w_embed, w_attribute)

    @staticmethod
    def rrf(*, rrf_k: int, rrf_w: float) -> Callable[[Ranks], Ranks]:
        return lambda ranks: _rrf(ranks, rrf_k, rrf_w)

    @staticmethod
    def rrf_with_attribute_rank(*, rrf_k: int, rrf_w: float) -> Callable[[Ranks], Ranks]:
        return lambda ranks: _rrf_with_attribute_rank(ranks, rrf_k, rrf_w)

    @staticmethod
    def by_method(
        method: str,
        *,
        rrf_k: int,
        rrf_w: float,
        w_embed: float,
        w_attribute: float,
    ) -> Callable[[Ranks], Ranks]:
        """Dispatch on the configured fusion-method name.

        An unrecognised ``method`` raises :class:`InvalidInputError` so the
        caller surfaces a precise (exit-code-2) error rather than a bare
        ``ValueError``.
        """
        if method == "weighted_linear":
            return fuse.weighted_linear(w_embed=w_embed, w_attribute=w_attribute)
        if method == "rrf":
            return fuse.rrf(rrf_k=rrf_k, rrf_w=rrf_w)
        if method == "rrf_with_attribute_rank":
            return fuse.rrf_with_attribute_rank(rrf_k=rrf_k, rrf_w=rrf_w)
        raise InvalidInputError(
            f"Unknown fusion_method: {method!r}. Must be 'weighted_linear', 'rrf', or 'rrf_with_attribute_rank'"
        )


# =============================================================================
# Consecutive-chunk merging (ported from _fusion.merge_consecutive_results)
# =============================================================================


def merge_consecutive(ranks: Ranks) -> Ranks:
    """Merge consecutive/overlapping chunks from the same sensor into one hit.

    Hits without both a parseable start and end timestamp are routed to a
    "no timestamp" bucket and left un-merged; the rest are grouped per sensor,
    sorted by parsed datetime (not raw string, which sorts wrongly across mixed
    ``Z``/``+00:00`` encodings), and merged when they overlap in time and their
    current scores are within :data:`SIMILARITY_RATIO_THRESHOLD`. The merged
    hit's current score is the group average; ``object_ids`` union in
    first-seen order. The result is sorted by current score, descending.
    """
    if not ranks.hits:
        return ranks
    key = ranks.score_key

    timestamped: list[Hit] = []
    no_timestamp: list[Hit] = []
    for h in ranks.hits:
        if not h.start_time or not h.end_time:
            no_timestamp.append(h)
            continue
        try:
            iso8601_to_datetime(h.start_time)
            iso8601_to_datetime(h.end_time)
            timestamped.append(h)
        except (ValueError, TypeError) as e:
            logger.warning(f"Skipping merge for hit with malformed timestamp (sensor={h.sensor_id}): {e}")
            no_timestamp.append(h)

    merged: list[Hit] = list(no_timestamp)
    if not timestamped:
        merged.sort(key=lambda h: h.score(key), reverse=True)
        return ranks.with_hits(merged)

    by_sensor: dict[str, list[Hit]] = {}
    for hit in timestamped:
        by_sensor.setdefault(hit.sensor_id, []).append(hit)

    for sensor_id, sensor_hits in by_sensor.items():
        # All hits here are in ``timestamped``, so start_time/end_time parse.
        sorted_hits = sorted(sensor_hits, key=lambda h: iso8601_to_datetime(h.start_time))

        groups: list[list[Hit]] = []
        group_chunks: list[Hit] = [sorted_hits[0]]
        group_end_dt = iso8601_to_datetime(sorted_hits[0].end_time)

        for hit in sorted_hits[1:]:
            hit_start_dt = iso8601_to_datetime(hit.start_time)
            group_avg = sum(c.score(key) for c in group_chunks) / len(group_chunks)
            # Compatible == the two scores are within (1 - threshold) of each
            # other, measured against the larger magnitude — the relative
            # difference stays correct for zero/negative cosine scores where a
            # raw min/max ratio is meaningless.
            denominator = max(abs(group_avg), abs(hit.score(key)))
            difference = abs(group_avg - hit.score(key))
            compatible = denominator == 0 or (difference / denominator) <= (1.0 - SIMILARITY_RATIO_THRESHOLD)

            if hit_start_dt <= group_end_dt and compatible:
                hit_end_dt = iso8601_to_datetime(hit.end_time)
                if hit_end_dt > group_end_dt:
                    group_end_dt = hit_end_dt
                group_chunks.append(hit)
            else:
                groups.append(group_chunks)
                group_chunks = [hit]
                group_end_dt = iso8601_to_datetime(hit.end_time)
        groups.append(group_chunks)

        for group in groups:
            first = group[0]
            end_dt = max(iso8601_to_datetime(g.end_time) for g in group)
            score = sum(g.score(key) for g in group) / len(group)

            seen_ids: set[str] = set()
            merged_object_ids: list[str] = []
            for g in group:
                for oid in g.object_ids:
                    if oid not in seen_ids:
                        merged_object_ids.append(oid)
                        seen_ids.add(oid)

            merged.append(
                Hit(
                    video_name=first.video_name,
                    description=first.description,
                    start_time=first.start_time,
                    end_time=datetime_to_iso8601(end_dt),
                    sensor_id=sensor_id,
                    screenshot_url=first.screenshot_url,
                    object_ids=tuple(merged_object_ids),
                    scores={**first.scores, key: score},
                )
            )

    merged.sort(key=lambda h: h.score(key), reverse=True)
    return ranks.with_hits(merged)
