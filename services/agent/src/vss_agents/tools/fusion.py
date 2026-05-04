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
"""Generalized N-space ranked-list fusion

Fusion is a ranker only (pure math, no side effects). It does not call ``embed_search``, ``attribute_search``, etc.
These are still left to the coordinator ``search.py`` to orchestrate.

A clip can appear in some lists and not others. "Missing" is equivalent to rank = ∞ in that space -> contributes 0.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from dataclasses import field
from datetime import UTC
from datetime import datetime
from datetime import timedelta
from typing import Literal

from pydantic import AwareDatetime
from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import field_validator

FusionMethod = Literal["rrf", "weighted_linear"]
Aggregation = Literal["max", "mean"]
DEFAULT_CHUNK_SECONDS = 5
DEFAULT_RRF_K = 60


# ---------------------------------------------------------------------------
# Public data contract
# ---------------------------------------------------------------------------


class ChunkKey(BaseModel):
    """Unique identifier for a 5-second video chunk on the snapped grid.

    Note: End of the chunk is intentionally not a field of the key. End is fully derived
    as ``start + chunk_seconds`` post-bucketize.
    """

    # Makes model hashable so it can be used as a ``dict`` key during the outer-join
    model_config = ConfigDict(frozen=True)

    sensor_id: str
    start: AwareDatetime = Field(
        description=(
            "Raw upstream timestamp; bucketize snaps to chunk_seconds grid. "
            "Must be tz-aware (naive rejected; non-UTC coerced to UTC)."
        ),
    )

    @field_validator("start")
    @classmethod
    def _coerce_to_utc(cls, v: datetime) -> datetime:
        """Normalize any tz-aware datetime to UTC.

        Note: ``AwareDatetime`` has already rejected naive non-tz inputs at this point.
        """
        return v.astimezone(UTC)


class RankedChunk(BaseModel):
    """One ranked entry from a single embedding space.

    Pure data model. Fusion is blind to payloads.
    Hence no VST URLs, screenshots, descriptions, object_ids, they live on the
    original search results in ``search.py``.
    """

    model_config = ConfigDict(frozen=True)

    key: ChunkKey

    # Raw score in space-native units (cosine, frame_score, …)
    score: float

    # 1-based position inside its source list
    # Note: Fusion does not compute initial ranks - it receives them
    # (the ranks come baked into the input from upstream search tools)
    # e.g. each embedding space tool -> emits ``RankedList(rank=1..K)``
    rank: int


class RankedList(BaseModel):
    """A ranked list of chunks from one embedding space."""

    model_config = ConfigDict(frozen=True)

    # Kept dynamic (not a literal) for ease of extensibility when consumers add new spaces
    # "embed", "attribute", "caption", "face", ...
    space: str

    # Trust knob for this space (used in RRF / weighted_linear etc.)
    # Makes this space's votes count for more
    weight: float = 1.0
    chunks: list[RankedChunk] = Field(default_factory=list)


class FusionInput(BaseModel):
    """Request body for fusion. Carries only what the math needs."""

    model_config = ConfigDict(frozen=True)

    # N per-space ranked lists from upstream search tools, e.g. [embed, attribute, caption]
    lists: list[RankedList] = Field(default_factory=list)

    # Chunk grid in seconds - drives snap/dedup and merge gap math, e.g. =5 -> 00:03 and 00:04 collapse to one bucket
    chunk_seconds: int = DEFAULT_CHUNK_SECONDS

    # Fusion math
    # - rrf uses ranks (unit-free, robust)
    # - weighted_linear uses min-max normalized raw scores
    method: FusionMethod = "rrf"
    # RRF damping (larger k flattens, smaller k amplifies top ranks), e.g. 60 is the TREC standard
    rrf_k: int = DEFAULT_RRF_K

    # Filter knobs (Pre-fuse)
    # Drop per-space chunks early below a raw-unit threshold, e.g. {"embed": 0.7} -> cosine < 0.7 chunks dropped
    per_space_min_score: dict[str, float] = Field(default_factory=dict)

    # Filter knobs (Post-fuse)
    # Chunk must appear in >=N spaces to survive, e.g. =2 -> at least 2 spaces voted for it
    min_contributing_spaces: int = 1
    # Drop chunks below ratio x theoretical ceiling, e.g. 0.3 -> keep only >=30% of best-possible fused_score
    min_fused_score_ratio: float | None = None
    # OR-exemption - top-N in any space bypasses post-fuse gates here
    # e.g. =3 -> rank <=3 anywhere survives
    keep_if_top_n_in_any_space: int | None = None

    # Merge knobs (applied after filtering and fusion)
    # Collapse touching chunks into one segment, e.g. [0-5]+[5-10] -> [0-10]
    merge_adjacent: bool = True
    # Tolerate up to N missing chunks between merges, e.g. =1 keeps [0-5]+[10-15] as one
    merge_gap_chunks: int = 0
    # How per-chunk fused scores collapse/aggregate into one segment score
    # ``mean`` matches legacy behavior ``search.py``/``_merge_consecutive_results``
    # (sustained events outrank single-chunk spikes)
    # ``max`` opts in to surfacing peak moments instead
    segment_score_aggregation: Aggregation = "mean"

    # End of pipeline knobs
    # Cap the final segments by fused_score, e.g. =5 -> only top 5 returned
    top_k_segments: int | None = 10


class FusedSegment(BaseModel):
    """One merged segment in the fusion output.

             INPUT                               OUTPUT
             ─────                               ───────

    RankedChunk.key  ────────►  fuse() ────►  FusedSegment.member_keys[i]
         │                         ▲                       │
         │                         │                       │
         └──────────► ChunkKey ◄───┴───► ChunkKey ◄────────┘
                      (frozen)              (frozen)
                         ▲                     ▲
                         └─── same value ──────┘
                         identity preserved end-to-end
                         -> enables payload re-join in search.py
    """

    sensor_id: str
    start: datetime  # earliest start of merged members
    end: datetime  # latest end of merged members

    # Rank-derived voting score (not a similarity)
    # Gotcha: Unitless and varies by method (e.g. RRF, rrf_k=60, 3 spaces, weights=1, ceiling ≈ 0.0492)
    # Always interpret and compare fused_score as a *ratio* of this ceiling (see ``_theoretical_ceiling``), not as an absolute value.
    # TODO: maybe revisit model to have this more meaningful ratio for easy interpretation
    fused_score: float

    # Union across member chunks
    # Reflects the breadth of evidence for this segment (i.e. more contributing spaces means more trustworthy)
    contributing_spaces: list[str]

    # Original chunk keys that fed this segment
    member_keys: list[ChunkKey]

    # Convenience field (=len(member_keys))
    # How many 5-second chunks were merged into this segment
    member_chunk_count: int


class FusionOutput(BaseModel):
    """Response body. Already filtered, merged, sorted desc by ``fused_score``."""

    segments: list[FusedSegment] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Internal structures
# ---------------------------------------------------------------------------


@dataclass
class _FusedRow:
    """Outer-join accumulator used inside :func:`fuse` and the global filter pass."""

    key: ChunkKey
    score: float = 0.0
    contributing_spaces: list[str] = field(default_factory=list)
    per_space_ranks: dict[str, int] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


def snap(ts: datetime, chunk_seconds: int = DEFAULT_CHUNK_SECONDS) -> datetime:
    """Snap an arbitrary timestamp down to the chunk-grid floor.

    Different search tools may return chunks at slightly different timestamps
    even when describing the same moment of video. Snapping lines them up onto a deterministic grid
    so the outer-join in :func:`fuse` matches the same chunk across spaces.

    Timezone-shape agnostic: Preserves ``ts.tzinfo`` (works for both naive and tz-aware inputs).

    Example:
    - `embed_search` (Cosmos clip embeddings) -> 00:01:25.300 (sliding window started)
    - `attribute_search` (CV per-frame) -> 00:01:27.100 (bounding box landed)
    -> both snap to 00:01:25

    TODO consumed by ``search.py`` adapters later, maybe make it a separate util?
    """
    epoch = datetime(1970, 1, 1, tzinfo=ts.tzinfo)
    seconds_since_epoch = (ts - epoch).total_seconds()
    snapped = (seconds_since_epoch // chunk_seconds) * chunk_seconds
    return epoch + timedelta(seconds=snapped)


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

    survivors = sorted(best.values(), key=lambda c: c.score, reverse=True)
    reranked = [RankedChunk(key=c.key, score=c.score, rank=i + 1) for i, c in enumerate(survivors)]
    return RankedList(space=rl.space, weight=rl.weight, chunks=reranked)


def apply_per_space_filter(rl: RankedList, per_space_min_score: dict[str, float]) -> RankedList:
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
    survivors = [c for c in rl.chunks if c.score >= threshold]
    survivors.sort(key=lambda c: c.score, reverse=True)
    reranked = [RankedChunk(key=c.key, score=c.score, rank=i + 1) for i, c in enumerate(survivors)]
    return RankedList(space=rl.space, weight=rl.weight, chunks=reranked)


def fuse(
    lists: list[RankedList],
    method: FusionMethod = "rrf",
    rrf_k: int = DEFAULT_RRF_K,
) -> dict[ChunkKey, _FusedRow]:
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
    out: dict[ChunkKey, _FusedRow] = {}

    if method == "rrf":
        for rl in lists:
            for chunk in rl.chunks:
                row = out.setdefault(chunk.key, _FusedRow(key=chunk.key))
                row.score += rl.weight / (rrf_k + chunk.rank)
                if rl.space not in row.contributing_spaces:
                    row.contributing_spaces.append(rl.space)
                row.per_space_ranks[rl.space] = chunk.rank
        return out

    if method == "weighted_linear":
        for rl in lists:
            if not rl.chunks:
                continue
            scores = [c.score for c in rl.chunks]
            lo, hi = min(scores), max(scores)
            spread = hi - lo
            for chunk in rl.chunks:
                norm = 1.0 if spread == 0 else (chunk.score - lo) / spread
                row = out.setdefault(chunk.key, _FusedRow(key=chunk.key))
                row.score += rl.weight * norm
                if rl.space not in row.contributing_spaces:
                    row.contributing_spaces.append(rl.space)
                row.per_space_ranks[rl.space] = chunk.rank
        return out

    raise ValueError(f"Unknown fusion method: {method!r}")


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

    raise ValueError(f"Unknown fusion method: {method!r}")


def apply_global_filters(
    fused: dict[ChunkKey, _FusedRow],
    *,
    min_contributing_spaces: int,
    keep_if_top_n_in_any_space: int | None,
    min_fused_score_ratio: float | None,
    method: FusionMethod,
    rrf_k: int,
    weights: list[float],
) -> dict[ChunkKey, _FusedRow]:
    """Apply the post-fusion filters.

    ``keep_if_top_n_in_any_space`` is an OR exemption: a chunk that ranks ``<= N``
    in at least one space survives even if it would otherwise fail the vote-count
    or score-ratio filters ("strong somewhere" override).

    Example: min_contributing_spaces=2, keep_if_top_n_in_any_space=3
    - C1 (3 spaces voted, ranks [1, 2, 1]) -> kept (passes vote-count gate)
    - C2 (1 space, rank=2)                 -> kept via exemption (top-3 somewhere)
    - C3 (1 space, rank=8)                 -> dropped (no agreement, no top-3 vote)
    """
    threshold: float | None = None
    if min_fused_score_ratio is not None:
        ceiling = _theoretical_ceiling(method, rrf_k, weights)
        threshold = min_fused_score_ratio * ceiling

    out: dict[ChunkKey, _FusedRow] = {}
    for key, row in fused.items():
        is_strong_somewhere = keep_if_top_n_in_any_space is not None and any(
            rank <= keep_if_top_n_in_any_space for rank in row.per_space_ranks.values()
        )
        if is_strong_somewhere:
            out[key] = row
            continue
        if len(row.contributing_spaces) < min_contributing_spaces:
            continue
        if threshold is not None and row.score < threshold:
            continue
        out[key] = row
    return out


def _row_to_segment(row: _FusedRow, chunk_seconds: int) -> FusedSegment:
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


def rows_to_segments(rows: list[_FusedRow], chunk_seconds: int = DEFAULT_CHUNK_SECONDS) -> list[FusedSegment]:
    """Convert fused rows to length-1 segments without merging.

    Used by :func:`run_fusion` when ``merge_adjacent=False``. Preserves the
    incoming row order (callers sort by ``fused_score`` desc beforehand).
    """
    return [_row_to_segment(r, chunk_seconds) for r in rows]


def merge_adjacent(
    rows: list[_FusedRow],
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
    by_sensor: dict[str, list[_FusedRow]] = defaultdict(list)
    for row in rows:
        by_sensor[row.key.sensor_id].append(row)

    segments: list[FusedSegment] = []
    gap_seconds = merge_gap_chunks * chunk_seconds

    for _sensor_id, sensor_rows in by_sensor.items():
        sensor_rows.sort(key=lambda r: r.key.start)
        group: list[_FusedRow] = []
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

    segments.sort(key=lambda s: s.fused_score, reverse=True)
    return segments


def _finalize_group(
    group: list[_FusedRow],
    chunk_seconds: int,
    aggregation: Aggregation,
) -> FusedSegment:
    """Collapse a contiguous run of :class:`_FusedRow` items into one :class:`FusedSegment`.

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
    contributing: list[str] = []
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
#                  │   5. merge_adjacent       (or rows_to_segments)          │
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
    bucketed = [bucketize(rl, inp.chunk_seconds) for rl in inp.lists]
    bucketed = [apply_per_space_filter(rl, inp.per_space_min_score) for rl in bucketed]

    fused = fuse(bucketed, method=inp.method, rrf_k=inp.rrf_k)

    fused = apply_global_filters(
        fused,
        min_contributing_spaces=inp.min_contributing_spaces,
        keep_if_top_n_in_any_space=inp.keep_if_top_n_in_any_space,
        min_fused_score_ratio=inp.min_fused_score_ratio,
        method=inp.method,
        rrf_k=inp.rrf_k,
        weights=[rl.weight for rl in bucketed],
    )

    rows = sorted(fused.values(), key=lambda r: r.score, reverse=True)
    if inp.merge_adjacent:
        segments = merge_adjacent(
            rows,
            chunk_seconds=inp.chunk_seconds,
            merge_gap_chunks=inp.merge_gap_chunks,
            aggregation=inp.segment_score_aggregation,
        )
    else:
        segments = rows_to_segments(rows, chunk_seconds=inp.chunk_seconds)

    if inp.top_k_segments is not None:
        segments = segments[: inp.top_k_segments]

    return FusionOutput(segments=segments)
