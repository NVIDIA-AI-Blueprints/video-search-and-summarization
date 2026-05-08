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
"""Generalized N-space ranked-list fusion.

Fusion is a ranker only (pure math, no side effects). It does not call
``embed_search``, ``attribute_search``, etc. These are still left to the
coordinator ``search.py`` to orchestrate.

Fusion never crosses sensors, ChunkKey = (sensor_id, snapped_start), so chunks
from different video sources are independent rows in every stage of the pipeline.

A clip can appear in some lists and not others. "Missing" is equivalent to
rank = infinity in that space, which contributes 0.
"""

from collections import defaultdict
from collections.abc import Iterable
from datetime import datetime
from datetime import timedelta
import logging
from typing import assert_never

from lib.fusion.fusion_models import DEFAULT_RRF_K
from lib.fusion.fusion_models import Aggregation
from lib.fusion.fusion_models import FusedRow
from lib.fusion.fusion_models import FusedSegment
from lib.fusion.fusion_models import FusionInput
from lib.fusion.fusion_models import FusionMethod
from lib.fusion.fusion_models import FusionOutput
from lib.fusion.ranking_models import DEFAULT_CHUNK_SECONDS
from lib.fusion.ranking_models import ChunkKey
from lib.fusion.ranking_models import EmbeddingSpaceName
from lib.fusion.ranking_models import RankedChunk
from lib.fusion.ranking_models import RankedList
from lib.fusion.ranking_models import snap
from lib.fusion.ranking_models import validate_chunk_seconds

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


def _validate_rrf_k(rrf_k: int) -> None:
    """Invariant for callers that bypass :class:`FusionInput`."""
    if rrf_k <= 0:
        raise ValueError(f"rrf_k must be > 0, got {rrf_k!r}")


def _score_with_tiebreak(score: float, sensor_id: str, start: datetime) -> tuple[float, str, datetime]:
    """Deterministic sort key for the fusion pipeline.

    Ties on raw score are broken by ``(sensor_id, start)`` so identical inputs
    in any order produce identical rank assignments. Without an explicit
    tie-breaker, Python's stable sort would preserve input-list order, leaking
    non-determinism to downstream consumers of fusion.
    """
    # -score makes high scores come first hence DESC, and tie breakers like sensor_id stays ASC
    return (-score, sensor_id, start)


def _rerank_by_score(rl: RankedList, survivors: Iterable[RankedChunk]) -> RankedList:
    """Reorders chunks by importance for fusion."""
    sorted_chunks = sorted(
        survivors,
        key=lambda c: _score_with_tiebreak(c.score, c.key.sensor_id, c.key.start),
    )
    reranked = [RankedChunk(key=c.key, score=c.score, rank=i + 1) for i, c in enumerate(sorted_chunks)]
    return RankedList(space=rl.space, chunks=reranked)


def bucketize(rl: RankedList, chunk_seconds: int = DEFAULT_CHUNK_SECONDS) -> RankedList:
    """Snap timestamps onto the ``chunk_seconds`` grid, dedupe, recompute ranks.

    Idempotent for already-snapped inputs (the case for trusted callers like
    ``search.py``'s per-space adapters). Stays in place as a defensive safety
    net for naive HTTP callers (eval scripts, notebooks) that may post
    unsnapped raw timestamps.

    Within a single space, multiple raw hits that snap to the same
    :class:`ChunkKey` are deduped with **max-score-wins** semantics.
    Ranks are then reassigned 1-based by descending score among the survivors,
    so the output is a valid input to :func:`fuse`.

    Example:
    - 3 raw embed hits on cam-1 at 25.3s, 27.1s, 29.5s with scores [0.9, 0.7, 0.95]
    - all snap to the same chunk [00:25, 00:30] on the 5s grid
    -> 1 chunk: score=0.95 (max wins), rank=1
    """
    best: dict[ChunkKey, RankedChunk] = {}
    for chunk in rl.chunks:
        snapped_key = ChunkKey(
            sensor_id=chunk.key.sensor_id,
            start=snap(chunk.key.start, chunk_seconds),
        )
        existing = best.get(snapped_key)
        if existing is None or chunk.score > existing.score:
            best[snapped_key] = RankedChunk(key=snapped_key, score=chunk.score, rank=0)

    return _rerank_by_score(rl, best.values())


def apply_per_space_filter(rl: RankedList, per_space_min_score: dict[EmbeddingSpaceName, float]) -> RankedList:
    """Drop below-threshold chunks for one space and recompute ranks.

    Gotcha: Score survives a drop. Rank does not - it is a relative property in the list.
    Hence rank must be reassigned after dropping chunks.
    (e.g. a chunk that was rank 5 with two dropped above must become rank 3, otherwise :func:`fuse` uses stale ranks).

    Threshold is keyed by space name; spaces missing from the dict get no filter (raw scores pass
    through unchanged).

    Example: per_space_min_score = {"embed": 0.70}
    - input embed list: 5 chunks with scores [0.92, 0.85, 0.62, 0.71, 0.45], ranks 1..5
    - drop the two below 0.70 (scores 0.62 and 0.45)
    -> 3 chunks with scores [0.92, 0.85, 0.71], ranks re-densified to 1, 2, 3
    """
    threshold = per_space_min_score.get(rl.space)
    if threshold is None:
        return rl
    return _rerank_by_score(rl, (c for c in rl.chunks if c.score >= threshold))


def _ranked_list_summary(rl: RankedList) -> str:
    """Compact count/range summary for fusion diagnostics."""
    if not rl.chunks:
        return "count=0"
    scores = [c.score for c in rl.chunks]
    return f"count={len(rl.chunks)}, min_score={min(scores):.4f}, max_score={max(scores):.4f}"


def fuse(
    lists: list[RankedList],
    weights: dict[EmbeddingSpaceName, float],
    method: FusionMethod = "rrf",
    rrf_k: int = DEFAULT_RRF_K,
) -> dict[ChunkKey, FusedRow]:
    """Outer-join ``lists`` on :class:`ChunkKey` and compute the fused score.

    1) ``rrf``: ``fused = Σ_i  w_i / (rrf_k + rank_i)``. Missing rank -> 0.

        Intuition: Throw away scores because they are not comparable across different embedding spaces.
        Every space gets a single ballot. First preference, second preference, third preference. Whoever appears highest on the most ballots wins.
        i.e. ``reward(rank) = 1 / (rrf_k + rank)``. Sum the rewards across judges. Highest sum wins.

        The constant ``rrf_k`` (=60) is a softener. Otherwise clip #1 would steamroll everything. 60 squashes the curve.

        query ─┬─► [embed model: Cosmos]    ─► ranked list A:  C1, C2, C3
            │
            └─► [attribute model: CV]    ─► ranked list B:  C2, C1, C4
                                                │
                                                ▼
                                            RRF fuses A + B
                                                │
                                                ▼
                                            C1 > C2 > C3 > C4

    2) ``weighted_linear``: per-space min-max normalize raw scores into ``[0, 1]``,
        then ``fused = Σ_i  w_i * norm_score_i``. Missing -> 0.

        Intuition: Scores are from different embedding spaces so we have to normalize them.
        Every space rates each chunk on a 0-1 scale, and we average those ratings.

    Returns a dict keyed by the snapped :class:`ChunkKey` carrying the fused score
    plus per-space rank witnesses (used by global filters like ``keep_if_top_n_in_any_space``).
    The dict is pre-sort; callers sort by ``score`` descending before merging.
    """
    _validate_rrf_k(rrf_k)
    out: dict[ChunkKey, FusedRow] = {}

    if method == "rrf":
        for rl in lists:
            w = weights[rl.space]
            for chunk in rl.chunks:
                row = out.setdefault(chunk.key, FusedRow(key=chunk.key))
                row.score += w / (rrf_k + chunk.rank)
                if rl.space not in row.contributing_spaces:
                    row.contributing_spaces.append(rl.space)
                row.per_space_ranks[rl.space] = chunk.rank
        return out

    if method == "weighted_linear":
        for rl in lists:
            if not rl.chunks:
                continue
            w = weights[rl.space]
            scores = [c.score for c in rl.chunks]
            lo, hi = min(scores), max(scores)
            spread = hi - lo
            for chunk in rl.chunks:
                norm = 1.0 if spread == 0 else (chunk.score - lo) / spread
                row = out.setdefault(chunk.key, FusedRow(key=chunk.key))
                row.score += w * norm
                if rl.space not in row.contributing_spaces:
                    row.contributing_spaces.append(rl.space)
                row.per_space_ranks[rl.space] = chunk.rank
        return out

    if method == "rrf_with_attribute_rank":
        # Temporary branch to support the integration with legacy path
        # TODO: drop this conditional and the ``rrf_with_attribute_rank`` literal from
        # ``FusionMethod`` once the legacy path is retired.
        raise ValueError(
            "fusion method 'rrf_with_attribute_rank' is legacy-only and is not supported "
            "by generalized fusion search. Use 'rrf' instead, it already operates "
            "over both embed and attribute ranked lists."
        )

    assert_never(method)


def _theoretical_ceiling(
    method: FusionMethod,
    rrf_k: int,
    weights: list[float],
) -> float:
    """Compute the maximum achievable fused score for ``min_fused_score_ratio``.

    - RRF ceiling = Σ w_i / (k + 1) (rank 1 in every space)
    - Weighted_linear ceiling = Σ w_i (max-normalized score 1 in every space)
    """
    if method == "rrf":
        return sum(w / (rrf_k + 1) for w in weights)
    if method == "weighted_linear":
        return sum(weights)
    if method == "rrf_with_attribute_rank":
        # Temporary branch to support the integration with legacy path
        # TODO: drop alongside the ``fuse`` carve-out when the legacy path is retired.
        raise ValueError(
            "fusion method 'rrf_with_attribute_rank' is legacy-only and is not supported "
            "by the generalized fusion engine, use 'rrf' instead."
        )

    assert_never(method)


def compute_score_threshold(
    method: FusionMethod,
    rrf_k: int,
    lists: list[RankedList],
    weights: dict[EmbeddingSpaceName, float],
    fraction: float = 0.5,
) -> float:
    """Returns meaningful score cutoff.

    Example: Setting fraction=0.6 applies a threshold at 60% of the theoretical maximum
    score ("ceiling"). Since fused_score is unitless and depends on the fusion method and
    weights, using a fraction ensures the threshold remains meaningful. This helps calibrate
    filtering based on "how close to the best possible score" a chunk came.
    """
    _validate_rrf_k(rrf_k)
    # Skip empty lists. They contribute 0, so counting their weight inflates the ceiling.
    contributing_weights = [weights[rl.space] for rl in lists if rl.chunks]
    return _theoretical_ceiling(method, rrf_k, contributing_weights) * fraction


def apply_global_filters(
    fused: dict[ChunkKey, FusedRow],
    *,
    min_contributing_spaces: int,
    keep_if_top_n_in_any_space: int | None,
    score_threshold: float | None,
    required_spaces: list[EmbeddingSpaceName] | None = None,
) -> dict[ChunkKey, FusedRow]:
    """Apply the post-fusion filters.

    Filter precedence (per row):
    1. ``required_spaces`` - hard gate, no exemption. Drops a row if any listed
       space is missing from ``contributing_spaces``. Evaluated first so the
       "strong somewhere" exemption below cannot rescue an anchor-violating row.
    2. ``keep_if_top_n_in_any_space`` - OR exemption. A row that ranks ``<= N``
       in at least one space bypasses the vote-count and score-ratio filters.
    3. ``min_contributing_spaces`` and ``score_threshold`` - vote-count and
       ratio gates.
    TODO: Revisit to streamline, clarify params/order

    Example: min_contributing_spaces=2, keep_if_top_n_in_any_space=3
    - C1 (3 spaces voted, ranks [1, 2, 1]) -> kept (passes vote-count gate)
    - C2 (1 space, rank=2)                 -> kept via exemption (top-3 somewhere)
    - C3 (1 space, rank=8)                 -> dropped (no agreement, no top-3 vote)

    Example: required_spaces=["embed"], keep_if_top_n_in_any_space=3
    - C4 (attribute only, rank=1)          -> dropped (anchor missing, exemption ignored)
    - C5 (embed+attribute, ranks [4, 1])   -> kept (anchor present, exemption applies)
    """
    out: dict[ChunkKey, FusedRow] = {}
    required = set(required_spaces or ())
    for key, row in fused.items():
        if required and not required.issubset(row.contributing_spaces):
            # Hard anchor gate evaluated before any exemption.
            continue
        is_strong_somewhere = keep_if_top_n_in_any_space is not None and any(
            rank <= keep_if_top_n_in_any_space for rank in row.per_space_ranks.values()
        )
        if is_strong_somewhere:
            out[key] = row
            continue
        if len(row.contributing_spaces) < min_contributing_spaces:
            continue
        if score_threshold is not None and row.score < score_threshold:
            continue
        out[key] = row
    return out


def _row_to_segment(row: FusedRow, chunk_seconds: int) -> FusedSegment:
    """Wrap a single fused row as a length-1 segment (no merging)."""
    return FusedSegment(
        sensor_id=row.key.sensor_id,
        start=row.key.start,
        end=row.key.start + timedelta(seconds=chunk_seconds),
        fused_score=row.score,
        member_chunk_count=1,
        contributing_spaces=list(row.contributing_spaces),
        member_keys=[row.key],
    )


def rows_to_segments(rows: list[FusedRow], chunk_seconds: int = DEFAULT_CHUNK_SECONDS) -> list[FusedSegment]:
    """Convert fused rows to length-1 segments without merging.

    Used by :func:`run_fusion` when ``merge_adjacent=False``. Preserves the
    incoming row order (callers sort by ``fused_score`` desc beforehand).
    """
    validate_chunk_seconds(chunk_seconds)
    return [_row_to_segment(r, chunk_seconds) for r in rows]


def merge_adjacent_rows(
    rows: list[FusedRow],
    chunk_seconds: int = DEFAULT_CHUNK_SECONDS,
    merge_gap_chunks: int = 0,
    aggregation: Aggregation = "mean",
) -> list[FusedSegment]:
    """Coalesce contiguous (or near-contiguous) chunks per sensor.

    Group by ``sensor_id``, sort by ``start``, walk left -> right and merge when
    ``next.start - (prev.start + chunk_seconds) <= merge_gap_chunks * chunk_seconds``.
    per-segment ``fused_score`` via ``aggregation`` and union the
    ``contributing_spaces`` across members.

    The output is sorted descending by ``fused_score`` so callers don't need
    to re-sort before applying ``top_k_segments``.

    Example: merge_gap_chunks=0
    - 4 surviving chunks on cam-1: [0-5], [5-10], [10-15], [25-30]
    - first three touch end->start -> merged into one segment [0-15]
    - [25-30] has a 10s gap (2 missing chunks) -> stays alone
    -> 2 segments
    """
    validate_chunk_seconds(chunk_seconds)
    if merge_gap_chunks < 0:
        raise ValueError(f"merge_gap_chunks must be >= 0, got {merge_gap_chunks!r}")

    by_sensor: dict[str, list[FusedRow]] = defaultdict(list)
    for row in rows:
        by_sensor[row.key.sensor_id].append(row)

    segments: list[FusedSegment] = []
    gap_seconds = merge_gap_chunks * chunk_seconds

    for _sensor_id, sensor_rows in by_sensor.items():
        sensor_rows.sort(key=lambda r: r.key.start)
        group: list[FusedRow] = []
        prev_end: datetime | None = None
        for row in sensor_rows:
            if prev_end is not None:
                gap = (row.key.start - prev_end).total_seconds()
                if gap > gap_seconds:
                    segments.append(_finalize_group(group, chunk_seconds, aggregation))
                    group = []
            group.append(row)
            prev_end = row.key.start + timedelta(seconds=chunk_seconds)
        if group:
            segments.append(_finalize_group(group, chunk_seconds, aggregation))

    segments.sort(key=lambda s: _score_with_tiebreak(s.fused_score, s.sensor_id, s.start))
    return segments


def _finalize_group(
    group: list[FusedRow],
    chunk_seconds: int,
    aggregation: Aggregation,
) -> FusedSegment:
    """Collapse a contiguous run of :class:`FusedRow` items into one :class:`FusedSegment`.

    Example: aggregation="mean"
    - 3 contiguous rows on cam-1: scores [0.045, 0.040, 0.038], spaces [embed,attr], [embed,caption], [embed]
    - fused_score = (0.045 + 0.040 + 0.038) / 3 = 0.041
    - contributing_spaces = union -> [embed, attr, caption]
    -> 1 segment: [00:00 - 00:15], fused_score=0.041
    """
    member_scores = [r.score for r in group]
    if aggregation == "max":
        fused_score = max(member_scores)
    elif aggregation == "mean":
        fused_score = sum(member_scores) / len(member_scores)
    else:
        raise ValueError(f"Unknown aggregation: {aggregation!r}")

    # Dedupe loop to keep unique contributing spaces
    # Order-preserving for stability
    contributing: list[EmbeddingSpaceName] = []
    for row in group:
        for space in row.contributing_spaces:
            if space not in contributing:
                contributing.append(space)

    sensor_id = group[0].key.sensor_id
    start = group[0].key.start
    end = group[-1].key.start + timedelta(seconds=chunk_seconds)
    return FusedSegment(
        sensor_id=sensor_id,
        start=start,
        end=end,
        fused_score=fused_score,
        member_chunk_count=len(group),
        contributing_spaces=contributing,
        member_keys=[r.key for r in group],
    )


# ---------------------------------------------------------------------------
# Entrypoint and end-to-end pipeline:
#
#                          ┌─ RankedList(space="embed",     chunks=[RC,RC,…])
#  e.g. search.py          │
#  builds N lists ───────► ├─ RankedList(space="attribute", chunks=[RC,RC,…])
#                          │
#                          └─ RankedList(space="caption",   chunks=[RC,RC,…])
#                                              │
#                                              ▼
#                                   ┌────────────────┐
#                                   │  FusionInput   │
#                                   └────────┬───────┘
#                                            │
#                                            ▼
#                  ┌──────────────────────────────────────────────────────────┐
#                  │ run_fusion(inp):                                         │
#                  │   1. bucketize            (snap + dedupe)                │
#                  │   2. apply_per_space_filter                              │
#                  │   3. fuse                 (RRF or weighted_linear)       │
#                  │   4. apply_global_filters                                │
#                  │   5. merge_adjacent_rows  (or rows_to_segments)          │
#                  │   6. sort desc by fused_score, top_k cut                 │
#                  └────────────────────────────┬─────────────────────────────┘
#                                               │
#                                               ▼
#                                        ┌────────────────┐
#                                        │  FusionOutput  │
#                                        └────────┬───────┘
#                                                 │
#                                                 ▼
#                      ┌─ FusedSegment(member_keys=[CK,CK,CK])  fused_score=0.0483  (~98% of ceiling)
#                      │
#   search.py   ◄──────┤─ FusedSegment(member_keys=[CK,CK])     fused_score=0.0309  (~63% of ceiling)
#   re-joins           │
#   payload via        ├─ FusedSegment(member_keys=[CK])        fused_score=0.0143  (~29% of ceiling)
#   ChunkKey           │
#                      └─ … up to top_k_segments
#
# ---------------------------------------------------------------------------


def run_fusion(inp: FusionInput) -> FusionOutput:
    """Run the full pipeline declaratively: bucketize -> filter raw -> fuse -> filter fused -> sort -> merge -> cap etc."""
    weights = inp.space_weights
    raw_counts = {rl.space: len(rl.chunks) for rl in inp.lists}
    bucketed = [bucketize(rl, inp.chunk_seconds) for rl in inp.lists]
    logger.info(
        "fusion: bucketized lists=%s raw_counts=%s bucketed_counts=%s per_space_min_score=%s",
        [rl.space for rl in bucketed],
        raw_counts,
        {rl.space: len(rl.chunks) for rl in bucketed},
        inp.per_space_min_score,
    )

    filtered: list[RankedList] = []
    for rl in bucketed:
        filtered_rl = apply_per_space_filter(rl, inp.per_space_min_score)
        threshold = inp.per_space_min_score.get(rl.space)
        if threshold is not None:
            logger.info(
                "fusion: per-space filter space=%s threshold=%.4f before=(%s) after=(%s) dropped=%d",
                rl.space,
                threshold,
                _ranked_list_summary(rl),
                _ranked_list_summary(filtered_rl),
                len(rl.chunks) - len(filtered_rl.chunks),
            )
        filtered.append(filtered_rl)
    bucketed = filtered

    fused = fuse(
        bucketed,
        weights=weights,
        method=inp.method,
        rrf_k=inp.rrf_k,
    )

    theoretical_max_score = compute_score_threshold(inp.method, inp.rrf_k, bucketed, weights, fraction=1.0)

    score_threshold: float | None = None
    if inp.min_fused_score_ratio is not None:
        score_threshold = theoretical_max_score * inp.min_fused_score_ratio

    pre_global_filter_rows = len(fused)
    fused = apply_global_filters(
        fused,
        min_contributing_spaces=inp.min_contributing_spaces,
        keep_if_top_n_in_any_space=inp.keep_if_top_n_in_any_space,
        score_threshold=score_threshold,
        required_spaces=inp.required_spaces,
    )
    post_global_filter_rows = len(fused)

    rows = sorted(
        fused.values(),
        key=lambda r: _score_with_tiebreak(r.score, r.key.sensor_id, r.key.start),
    )
    if inp.merge_adjacent:
        segments = merge_adjacent_rows(
            rows,
            chunk_seconds=inp.chunk_seconds,
            merge_gap_chunks=inp.merge_gap_chunks,
            aggregation=inp.segment_score_aggregation,
        )
    else:
        segments = rows_to_segments(rows, chunk_seconds=inp.chunk_seconds)

    if inp.top_k_segments is not None:
        segments = segments[: inp.top_k_segments]

    logger.info(
        "fusion: output pre_global_filter_rows=%d post_global_filter_rows=%d segments=%d "
        "top_k_segments=%s theoretical_max_score=%.6f global_filters="
        "{min_contributing_spaces=%s, keep_if_top_n_in_any_space=%s, required_spaces=%s, min_fused_score_ratio=%s}",
        pre_global_filter_rows,
        post_global_filter_rows,
        len(segments),
        inp.top_k_segments,
        theoretical_max_score,
        inp.min_contributing_spaces,
        inp.keep_if_top_n_in_any_space,
        inp.required_spaces,
        inp.min_fused_score_ratio,
    )

    return FusionOutput(segments=segments, theoretical_max_score=theoretical_max_score)
