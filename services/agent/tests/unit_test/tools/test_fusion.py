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
"""Unit tests for the pure fusion script (commit 1).

Test in isolation - pydantic models, pure functions, filter knobs, and
the pure fusion pipeline on the worked-example fixture.
"""

from datetime import UTC
from datetime import datetime
from datetime import timedelta
import math
from zoneinfo import ZoneInfo

from pydantic import ValidationError
import pytest

from vss_agents.tools.fusion import ChunkKey
from vss_agents.tools.fusion import FusionInput
from vss_agents.tools.fusion import RankedChunk
from vss_agents.tools.fusion import RankedList
from vss_agents.tools.fusion import apply_global_filters
from vss_agents.tools.fusion import apply_per_space_filter
from vss_agents.tools.fusion import bucketize
from vss_agents.tools.fusion import compute_score_threshold
from vss_agents.tools.fusion import fuse
from vss_agents.tools.fusion import merge_adjacent_rows
from vss_agents.tools.fusion import run_fusion
from vss_agents.tools.fusion import snap

# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _ts(seconds: int) -> datetime:
    """Build a UTC datetime offset by ``seconds`` from a fixed epoch.

    All tests use 2025-01-01T00:00:00Z as the anchor so test fixtures stay
    readable as offsets in seconds.
    """
    return datetime(2025, 1, 1, 0, 0, 0, tzinfo=UTC) + timedelta(seconds=seconds)


def _chunk(sensor: str, start_seconds: int, score: float, rank: int) -> RankedChunk:
    return RankedChunk(
        key=ChunkKey(sensor_id=sensor, start=_ts(start_seconds)),
        score=score,
        rank=rank,
    )


# Worked-example fixture: warehouse/ladder query.
# Top embed/attribute results converge on warehouse_01 @ 00:01:25 (ranks 1, 2)
# and 00:01:30 (ranks 2, 1); attribute also has a noisy hit on dock @ 00:05:00.
_W = "warehouse_01"
_D = "dock"
_W_125 = 85  # 00:01:25 -> 85 s offset from 00:00:00
_W_130 = 90
_W_135 = 95
_D_310 = 190  # 00:03:10 -> 190 s
_D_500 = 300  # 00:05:00 -> 300 s


def _warehouse_embed_list() -> RankedList:
    return RankedList(
        space="embed",
        weight=1.0,
        chunks=[
            _chunk(_W, _W_125, 0.84, 1),
            _chunk(_W, _W_130, 0.81, 2),
            _chunk(_W, _W_135, 0.78, 3),
            _chunk(_D, _D_310, 0.62, 4),
        ],
    )


def _warehouse_attribute_list() -> RankedList:
    return RankedList(
        space="attribute",
        weight=0.5,
        chunks=[
            _chunk(_W, _W_130, 0.71, 1),
            _chunk(_W, _W_125, 0.69, 2),
            _chunk(_D, _D_500, 0.41, 3),
        ],
    )


# ---------------------------------------------------------------------------
# snap()
# ---------------------------------------------------------------------------


class TestSnap:
    """Snap an arbitrary timestamp down to the chunk-grid floor."""

    def test_snaps_below_chunk_boundary(self):
        # 00:01:27.500 with chunk=5 -> 00:01:25
        ts = _ts(85) + timedelta(seconds=2.5)
        assert snap(ts, 5) == _ts(85)

    def test_already_snapped_is_noop(self):
        assert snap(_ts(90), 5) == _ts(90)

    def test_preserves_tzinfo_for_aware_input(self):
        # Non-UTC tz-aware input -> tzinfo preserved on the snapped output.
        paris = ZoneInfo("Europe/Paris")
        ts = datetime(2025, 1, 1, 0, 1, 27, 500_000, tzinfo=paris)
        snapped = snap(ts, 5)
        assert snapped.tzinfo is paris
        assert snapped == datetime(2025, 1, 1, 0, 1, 25, tzinfo=paris)

    def test_naive_datetime_rejected(self):
        # Tightened contract: naive datetime in -> ValueError out.
        naive = datetime(2025, 1, 1, 0, 1, 27, 500_000)
        with pytest.raises(ValueError):
            snap(naive, 5)

    def test_negative_offsets_floor_correctly(self):
        # 1969 (pre-epoch) edge: start = -10s, chunk=5 -> -10s (already on grid).
        ts = datetime(1969, 12, 31, 23, 59, 50, tzinfo=UTC)
        assert snap(ts, 5) == ts


# ---------------------------------------------------------------------------
# bucketize()
# ---------------------------------------------------------------------------


class TestBucketize:
    """Snap + dedupe (max-score-wins) + recompute ranks."""

    def test_unsnapped_clips_collapse_to_same_bucket(self):
        # Clips at 0.0s / 2.4s / 4.9s with chunk_seconds=5 all
        # collapse to bucket [0, 5); max score wins; ranks recomputed.
        rl = RankedList(
            space="embed",
            weight=1.0,
            chunks=[
                _chunk("s", 0, 0.50, 1),
                # Build a non-snapped start to verify bucketize snaps it down.
                RankedChunk(
                    key=ChunkKey(
                        sensor_id="s",
                        start=_ts(0) + timedelta(milliseconds=2400),
                    ),
                    score=0.90,
                    rank=2,
                ),
                RankedChunk(
                    key=ChunkKey(
                        sensor_id="s",
                        start=_ts(0) + timedelta(milliseconds=4900),
                    ),
                    score=0.30,
                    rank=3,
                ),
            ],
        )
        out = bucketize(rl, 5)
        assert len(out.chunks) == 1
        survivor = out.chunks[0]
        assert survivor.key.start == _ts(0)
        assert survivor.score == 0.90  # max wins
        assert survivor.rank == 1

    def test_idempotent_for_already_snapped_input(self):
        # Feeding an already-snapped RankedList through bucketize
        # produces an output structurally equal to the input.
        rl = _warehouse_embed_list()
        out = bucketize(rl, 5)
        assert out.space == rl.space
        assert out.weight == rl.weight
        assert [c.key for c in out.chunks] == [c.key for c in rl.chunks]
        assert [c.score for c in out.chunks] == [c.score for c in rl.chunks]
        assert [c.rank for c in out.chunks] == [c.rank for c in rl.chunks]

    def test_recomputes_ranks_after_dedupe(self):
        # Two raw hits collapse into bucket A; one raw hit lands in bucket B.
        # After dedupe + sort by score desc, ranks must be 1, 2.
        rl = RankedList(
            space="embed",
            weight=1.0,
            chunks=[
                _chunk("s", 0, 0.30, 1),  # bucket [0, 5) - loses to 0.5
                _chunk("s", 5, 0.40, 2),  # bucket [5, 10)
                RankedChunk(
                    key=ChunkKey(
                        sensor_id="s",
                        start=_ts(0) + timedelta(milliseconds=3000),
                    ),
                    score=0.50,
                    rank=3,
                ),
            ],
        )
        out = bucketize(rl, 5)
        assert len(out.chunks) == 2
        assert out.chunks[0].score == 0.50
        assert out.chunks[0].rank == 1
        assert out.chunks[1].score == 0.40
        assert out.chunks[1].rank == 2

    def test_does_not_merge_across_sensors(self):
        rl = RankedList(
            space="embed",
            weight=1.0,
            chunks=[
                _chunk("a", 0, 0.5, 1),
                _chunk("b", 0, 0.6, 2),
            ],
        )
        out = bucketize(rl, 5)
        assert len(out.chunks) == 2
        sensors = {c.key.sensor_id for c in out.chunks}
        assert sensors == {"a", "b"}


# ---------------------------------------------------------------------------
# fuse()
# ---------------------------------------------------------------------------


class TestFuseRRF:
    """RRF outer-join: ``fused = Σ_i  w_i / (rrf_k + rank_i)``."""

    def test_warehouse_ladder_two_space_rrf(self):
        # End-to-end with exact values on the warehouse/ladder fixture
        fused = fuse(
            [_warehouse_embed_list(), _warehouse_attribute_list()],
            method="rrf",
            rrf_k=60,
        )
        rows = sorted(fused.values(), key=lambda r: r.score, reverse=True)

        # warehouse_01 @ 00:01:25 - ranks 1 (embed) and 2 (attribute)
        top = rows[0]
        assert top.key.sensor_id == _W
        assert top.key.start == _ts(_W_125)
        assert top.score == pytest.approx(1 / 61 + 0.5 / 62, abs=1e-5)
        assert top.score == pytest.approx(0.02446, abs=1e-4)
        assert sorted(top.contributing_spaces) == ["attribute", "embed"]
        assert top.per_space_ranks == {"embed": 1, "attribute": 2}

        # warehouse_01 @ 00:01:30 - ranks 2 (embed) and 1 (attribute)
        second = rows[1]
        assert second.key.start == _ts(_W_130)
        assert second.score == pytest.approx(1 / 62 + 0.5 / 61, abs=1e-5)
        assert second.score == pytest.approx(0.02433, abs=1e-4)

        # warehouse_01 @ 00:01:35 - embed-only
        assert rows[2].score == pytest.approx(1 / 63, abs=1e-5)
        assert rows[2].contributing_spaces == ["embed"]

        # dock @ 00:03:10 - embed-only rank 4
        assert rows[3].score == pytest.approx(1 / 64, abs=1e-5)

        # dock @ 00:05:00 - attribute-only rank 3
        assert rows[4].score == pytest.approx(0.5 / 63, abs=1e-5)
        assert rows[4].contributing_spaces == ["attribute"]

    def test_missing_rank_contributes_zero(self):
        # A chunk in space A but absent from space B: the B contribution is 0.
        a_only = RankedList(space="A", weight=1.0, chunks=[_chunk("s", 0, 0.5, 1)])
        b_only = RankedList(space="B", weight=1.0, chunks=[_chunk("s", 100, 0.5, 1)])
        fused = fuse([a_only, b_only], method="rrf", rrf_k=60)
        # Two distinct keys, each scored only by its source space.
        assert len(fused) == 2
        for row in fused.values():
            assert row.score == pytest.approx(1 / 61, abs=1e-5)
            assert len(row.contributing_spaces) == 1


class TestFuseWeightedLinear:
    """``weighted_linear``: per-space min-max norm + weighted sum."""

    def test_warehouse_ladder_two_space_weighted_linear(self):
        # End-to-end with exact values on the warehouse/ladder fixture
        fused = fuse(
            [_warehouse_embed_list(), _warehouse_attribute_list()],
            method="weighted_linear",
        )
        rows = sorted(fused.values(), key=lambda r: r.score, reverse=True)

        # warehouse_01 @ 00:01:25 - embed top + attribute strong
        assert rows[0].key.start == _ts(_W_125)
        assert rows[0].score == pytest.approx(1.0 + 0.5 * (0.69 - 0.41) / 0.30, abs=1e-5)
        assert rows[0].score == pytest.approx(1.46667, abs=1e-4)
        assert sorted(rows[0].contributing_spaces) == ["attribute", "embed"]

        # warehouse_01 @ 00:01:30 - both strong, attribute tops here
        assert rows[1].key.start == _ts(_W_130)
        assert rows[1].score == pytest.approx((0.81 - 0.62) / 0.22 + 0.5 * 1.0, abs=1e-5)
        assert rows[1].score == pytest.approx(1.36364, abs=1e-4)

        # warehouse_01 @ 00:01:35 - embed only, mid-list normalization
        assert rows[2].score == pytest.approx((0.78 - 0.62) / 0.22, abs=1e-5)
        assert rows[2].contributing_spaces == ["embed"]

        # dock chunks both hit the per-space normalization floor -> score=0.0
        assert rows[3].score == pytest.approx(0.0, abs=1e-9)
        assert rows[4].score == pytest.approx(0.0, abs=1e-9)

    def test_normalization_invariance(self):
        # Multiplying one space's raw scores by 100 must not
        # change order under weighted_linear.
        original = RankedList(
            space="A",
            weight=1.0,
            chunks=[
                _chunk("s", 0, 0.50, 1),
                _chunk("s", 5, 0.40, 2),
                _chunk("s", 10, 0.30, 3),
            ],
        )
        scaled = RankedList(
            space="A",
            weight=1.0,
            chunks=[
                _chunk("s", 0, 50.0, 1),
                _chunk("s", 5, 40.0, 2),
                _chunk("s", 10, 30.0, 3),
            ],
        )
        a = fuse([original], method="weighted_linear")
        b = fuse([scaled], method="weighted_linear")
        a_order = [r.key.start for r in sorted(a.values(), key=lambda r: r.score, reverse=True)]
        b_order = [r.key.start for r in sorted(b.values(), key=lambda r: r.score, reverse=True)]
        assert a_order == b_order

    def test_constant_score_space_normalizes_to_one(self):
        # All scores equal -> spread=0 -> norm=1.0 for every chunk (no NaN).
        rl = RankedList(
            space="A",
            weight=1.0,
            chunks=[_chunk("s", 0, 0.5, 1), _chunk("s", 5, 0.5, 2)],
        )
        fused = fuse([rl], method="weighted_linear")
        for row in fused.values():
            assert row.score == pytest.approx(1.0)

    def test_unknown_method_raises(self):
        with pytest.raises(ValueError):
            fuse([], method="bogus", rrf_k=60)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# apply_per_space_filter()
# ---------------------------------------------------------------------------


class TestApplyPerSpaceFilter:
    """Drop below-threshold chunks AND recompute ranks."""

    def test_filters_in_raw_score_units(self):
        # per_space_min_score filters before fusion (raw-score units).
        rl = _warehouse_attribute_list()
        out = apply_per_space_filter(rl, {"attribute": 0.5})
        # 0.41 attribute on dock gets dropped; 0.69 / 0.71 survive.
        kept_scores = [c.score for c in out.chunks]
        assert 0.41 not in kept_scores
        assert sorted(kept_scores) == [0.69, 0.71]

    def test_recomputes_ranks_after_drop(self):
        # Two of three chunks pass; surviving ranks must be 1 and 2.
        rl = RankedList(
            space="A",
            weight=1.0,
            chunks=[
                _chunk("s", 0, 0.10, 1),  # drops
                _chunk("s", 5, 0.90, 2),  # survives, becomes rank 1
                _chunk("s", 10, 0.50, 3),  # survives, becomes rank 2
            ],
        )
        out = apply_per_space_filter(rl, {"A": 0.3})
        assert [c.rank for c in out.chunks] == [1, 2]
        assert [c.score for c in out.chunks] == [0.90, 0.50]

    def test_no_threshold_for_space_passes_through(self):
        rl = _warehouse_attribute_list()
        out = apply_per_space_filter(rl, {"unrelated": 0.99})
        assert len(out.chunks) == len(rl.chunks)
        assert [c.score for c in out.chunks] == [c.score for c in rl.chunks]


# ---------------------------------------------------------------------------
# apply_global_filters()
# ---------------------------------------------------------------------------


class TestGlobalFilters:
    """Vote-count gate, top-N exemption, ratio floor."""

    def _two_space_fused(self):
        # fixture, post-fusion (no merging). Convenient input for
        # global-filter tests.
        bucketed = [bucketize(_warehouse_embed_list(), 5), bucketize(_warehouse_attribute_list(), 5)]
        return fuse(bucketed, method="rrf", rrf_k=60)

    def test_min_contributing_spaces_2_drops_single_space_hits(self):
        fused = self._two_space_fused()
        out = apply_global_filters(
            fused,
            min_contributing_spaces=2,
            keep_if_top_n_in_any_space=None,
            score_threshold=None,
        )
        # Only the two warehouse_01 chunks have contributions from both spaces.
        kept_starts = {row.key.start for row in out.values()}
        assert kept_starts == {_ts(_W_125), _ts(_W_130)}

    def test_keep_if_top_n_in_any_space_exempts_single_space_hits(self):
        # keep_if_top_n_in_any_space=5 keeps chunks ranked <=5 in at
        # least one space. Combined with min_contributing_spaces=2 (which
        # would otherwise drop them), this exemption rescues them.
        fused = self._two_space_fused()
        out = apply_global_filters(
            fused,
            min_contributing_spaces=2,
            keep_if_top_n_in_any_space=5,
            score_threshold=None,
        )
        # All 5 chunks have rank <= 5 in at least one space (max rank is 4).
        assert len(out) == 5

    def test_score_threshold_drops_below_floor(self):
        # With k=60, w=[1.0, 0.5], ratio=0.5 -> threshold = 0.5 * 0.02459 = 0.01230.
        fused = self._two_space_fused()
        lists = [_warehouse_embed_list(), _warehouse_attribute_list()]
        threshold = compute_score_threshold("rrf", rrf_k=60, lists=lists, fraction=0.5)
        out = apply_global_filters(
            fused,
            min_contributing_spaces=1,
            keep_if_top_n_in_any_space=None,
            score_threshold=threshold,
        )
        # Cliff: warehouse_01 chunks (cross-validated, ~0.024) survive;
        # warehouse_01 @ 00:01:35 (~0.0159), dock @ 00:03:10 (~0.0156),
        # dock @ 00:05:00 (~0.0079) all fall below the 0.01230 threshold
        # only if it is between them. Compute and assert:
        kept = sorted(out.values(), key=lambda r: r.score, reverse=True)
        for row in kept:
            assert row.score >= threshold
        dropped_keys = set(fused.keys()) - {row.key for row in kept}
        for key in dropped_keys:
            assert fused[key].score < threshold

    def test_top_n_exemption_overrides_score_ratio(self):
        # Strong single-space hits are kept even when score_threshold
        # would drop them - "strong somewhere" override.
        fused = self._two_space_fused()
        lists = [_warehouse_embed_list(), _warehouse_attribute_list()]
        out = apply_global_filters(
            fused,
            min_contributing_spaces=2,
            keep_if_top_n_in_any_space=1,  # rank-1 anywhere -> keep
            # would otherwise drop nearly everything
            score_threshold=compute_score_threshold("rrf", rrf_k=60, lists=lists, fraction=0.99),
        )
        # Each space's rank-1 chunk must survive; vote-count and ratio
        # filters are bypassed for top-N exempt rows.
        kept_starts = {row.key.start for row in out.values()}
        assert _ts(_W_125) in kept_starts  # rank 1 in embed
        assert _ts(_W_130) in kept_starts  # rank 1 in attribute


class TestComputeScoreThreshold:
    """compute_score_threshold returns a fraction of the maximum possible fused score (ceiling)."""

    @staticmethod
    def _list(space: str, weight: float) -> RankedList:
        return RankedList(space=space, weight=weight, chunks=[_chunk(_W, _W_125, 0.5, 1)])

    def test_rrf_ceiling(self):
        # ceiling = Σ w_i / (k + 1); fraction=1.0 returns the full ceiling.
        lists = [self._list("a", 1.0), self._list("b", 0.5)]
        assert compute_score_threshold("rrf", rrf_k=60, lists=lists, fraction=1.0) == pytest.approx(1.5 / 61)

    def test_weighted_linear_ceiling(self):
        # ceiling = Σ w_i (max-normalized); fraction=1.0 returns the full ceiling.
        lists = [self._list("a", 1.0), self._list("b", 0.5), self._list("c", 0.4)]
        assert compute_score_threshold("weighted_linear", rrf_k=60, lists=lists, fraction=1.0) == pytest.approx(1.9)

    def test_fraction_scales_ceiling(self):
        # fraction=0.5 -> threshold is half the ceiling.
        lists = [self._list("a", 1.0)]
        assert compute_score_threshold("rrf", rrf_k=60, lists=lists, fraction=0.5) == pytest.approx(0.5 / 61)

    def test_empty_lists_excluded_from_ceiling(self):
        # A list with no chunks contributes 0 to every fused score, so its weight
        # MUST NOT count toward the ceiling. Otherwise the threshold over-tightens
        # and drops legitimate survivors.
        populated = self._list("a", 1.0)
        empty = RankedList(space="b", weight=1.0, chunks=[])
        # Buggy ceiling would be (1.0 + 1.0) / 61 = 2/61.
        # Fixed ceiling skips the empty list -> 1.0 / 61 = 1/61.
        assert compute_score_threshold("rrf", rrf_k=60, lists=[populated, empty], fraction=1.0) == pytest.approx(1 / 61)

    def test_zero_weight_does_not_inflate_ceiling(self):
        # A weight=0 list is a "disabled space": adds 0 to every fused score, so
        # it must add 0 to the ceiling - matching the disabled-space intent end-to-end.
        lists = [self._list("a", 1.0), self._list("b", 0.0)]
        assert compute_score_threshold("rrf", rrf_k=60, lists=lists, fraction=1.0) == pytest.approx(1 / 61)


# ---------------------------------------------------------------------------
# merge_adjacent_rows()
# ---------------------------------------------------------------------------


def _row(sensor: str, start_seconds: int, score: float, contributing=("embed",)):
    """Construct a FusedRow for merge tests without going through fuse()."""
    from vss_agents.tools.fusion import FusedRow

    return FusedRow(
        key=ChunkKey(sensor_id=sensor, start=_ts(start_seconds)),
        score=score,
        contributing_spaces=list(contributing),
        per_space_ranks=dict.fromkeys(contributing, 1),
    )


class TestMergeAdjacent:
    """Coalesce contiguous chunks per sensor; aggregate score."""

    def test_three_contiguous_merge_with_default_mean(self):
        # 3 contiguous 5s chunks -> one 15s segment with score = mean(member).
        rows = [
            _row("s", 0, 0.10),
            _row("s", 5, 0.20),
            _row("s", 10, 0.30),
        ]
        segments = merge_adjacent_rows(rows, chunk_seconds=5, merge_gap_chunks=0, aggregation="mean")
        assert len(segments) == 1
        seg = segments[0]
        assert seg.start == _ts(0)
        assert seg.end == _ts(15)
        assert seg.member_chunk_count == 3
        assert seg.fused_score == pytest.approx((0.10 + 0.20 + 0.30) / 3)

    def test_three_contiguous_merge_with_max_aggregation(self):
        # Same fixture; flipping aggregation to "max" inverts ranking.
        rows = [
            _row("s", 0, 0.10),
            _row("s", 5, 0.20),
            _row("s", 10, 0.30),
        ]
        segments = merge_adjacent_rows(rows, chunk_seconds=5, merge_gap_chunks=0, aggregation="max")
        assert len(segments) == 1
        assert segments[0].fused_score == pytest.approx(0.30)

    def test_aggregation_default_mean_promotes_sustained_over_spike(self):
        # Inverse fixture: 4-chunk sustained at 0.025 each (mean=0.025)
        # vs single spike 0.024. Under default `mean`, sustained wins.
        sustained = [_row("a", i * 5, 0.025) for i in range(4)]
        spike = [_row("b", 0, 0.024)]
        segments = merge_adjacent_rows(sustained + spike, chunk_seconds=5, merge_gap_chunks=0, aggregation="mean")
        assert len(segments) == 2
        # Output is sorted desc by fused_score -> sustained first.
        assert segments[0].sensor_id == "a"
        assert segments[0].fused_score == pytest.approx(0.025)
        assert segments[1].sensor_id == "b"
        assert segments[1].fused_score == pytest.approx(0.024)

    def test_aggregation_max_promotes_spike_over_sustained(self):
        # Same fixture under aggregation="max": spike wins.
        sustained = [_row("a", i * 5, 0.020) for i in range(4)]  # max=0.020
        spike = [_row("b", 0, 0.030)]
        segments = merge_adjacent_rows(sustained + spike, chunk_seconds=5, merge_gap_chunks=0, aggregation="max")
        assert len(segments) == 2
        assert segments[0].sensor_id == "b"
        assert segments[0].fused_score == pytest.approx(0.030)

    def test_does_not_merge_across_sensors(self):
        # Two sensors with same timestamps must stay as two separate segments.
        rows = [_row("a", 0, 0.5), _row("b", 0, 0.4)]
        segments = merge_adjacent_rows(rows, chunk_seconds=5, merge_gap_chunks=0)
        assert len(segments) == 2
        assert {s.sensor_id for s in segments} == {"a", "b"}

    def test_gap_above_merge_gap_chunks_does_not_merge(self):
        # Two chunks with a 5s gap (one empty chunk between them); with
        # merge_gap_chunks=0 they must stay separate.
        rows = [_row("s", 0, 0.5), _row("s", 10, 0.4)]
        segments = merge_adjacent_rows(rows, chunk_seconds=5, merge_gap_chunks=0)
        assert len(segments) == 2

    def test_gap_within_merge_gap_chunks_merges(self):
        # Same gap as above but merge_gap_chunks=1 -> merge into one segment
        # of length 15s (start of first -> end of second).
        rows = [_row("s", 0, 0.5), _row("s", 10, 0.4)]
        segments = merge_adjacent_rows(rows, chunk_seconds=5, merge_gap_chunks=1)
        assert len(segments) == 1
        assert segments[0].start == _ts(0)
        assert segments[0].end == _ts(15)
        assert segments[0].member_chunk_count == 2

    def test_unions_contributing_spaces(self):
        rows = [
            _row("s", 0, 0.5, contributing=("embed",)),
            _row("s", 5, 0.4, contributing=("attribute",)),
            _row("s", 10, 0.3, contributing=("embed", "caption")),
        ]
        segments = merge_adjacent_rows(rows, chunk_seconds=5, merge_gap_chunks=0)
        assert len(segments) == 1
        assert sorted(segments[0].contributing_spaces) == ["attribute", "caption", "embed"]

    def test_member_keys_preserve_order(self):
        rows = [_row("s", 0, 0.1), _row("s", 5, 0.2), _row("s", 10, 0.3)]
        segments = merge_adjacent_rows(rows, chunk_seconds=5, merge_gap_chunks=0)
        assert [k.start for k in segments[0].member_keys] == [_ts(0), _ts(5), _ts(10)]

    def test_ties_resolved_deterministically_by_sensor_then_start(self):
        """Two segments tied on ``fused_score`` must keep the same final order
        regardless of input row order. Pins the deterministic tie-breaker on
        the final ``segments.sort`` inside :func:`merge_adjacent_rows`.
        """
        forward = [_row("cam-1", 0, 0.5), _row("cam-2", 0, 0.5)]
        reversed_rows = list(reversed(forward))

        seg_forward = merge_adjacent_rows(forward, chunk_seconds=5, merge_gap_chunks=0)
        seg_reversed = merge_adjacent_rows(reversed_rows, chunk_seconds=5, merge_gap_chunks=0)

        assert [s.sensor_id for s in seg_forward] == ["cam-1", "cam-2"]
        assert [s.sensor_id for s in seg_reversed] == ["cam-1", "cam-2"]


# ---------------------------------------------------------------------------
# run_fusion() - end-to-end pipeline
# ---------------------------------------------------------------------------


class TestRunFusion:
    """Full pipeline end-to-end"""

    def test_warehouse_ladder_end_to_end_default_mean_aggregation(self):
        inp = FusionInput(
            lists=[_warehouse_embed_list(), _warehouse_attribute_list()],
            chunk_seconds=5,
            method="rrf",
            rrf_k=60,
            per_space_min_score={"embed": 0.0, "attribute": 0.3},
            min_contributing_spaces=1,
            top_k_segments=10,
            merge_adjacent=True,
            merge_gap_chunks=0,
            # default segment_score_aggregation="mean"
        )
        out = run_fusion(inp)

        # Warehouse_01 merges to one 15s segment;
        # dock @ 00:03:10 and dock @ 00:05:00 stay as two separate 5s segments.
        assert len(out.segments) == 3

        # First segment is the merged warehouse run, score = mean of three
        # member RRF voting scores.
        seg = out.segments[0]
        assert seg.sensor_id == _W
        assert seg.start == _ts(_W_125)
        assert seg.end == _ts(_W_135 + 5)  # 00:01:40
        assert seg.member_chunk_count == 3
        assert sorted(seg.contributing_spaces) == ["attribute", "embed"]
        member_scores = [
            1 / 61 + 0.5 / 62,  # @ 00:01:25 - embed rank 1, attribute rank 2
            1 / 62 + 0.5 / 61,  # @ 00:01:30 - embed rank 2, attribute rank 1
            1 / 63,  # @ 00:01:35 - embed rank 3 only
        ]
        assert seg.fused_score == pytest.approx(sum(member_scores) / 3, abs=1e-5)

        # Member keys are the original snapped chunk keys, in time order.
        assert [k.start for k in seg.member_keys] == [
            _ts(_W_125),
            _ts(_W_130),
            _ts(_W_135),
        ]

        # Other segments are the unmerged dock chunks, sorted by score desc.
        sensor_ids = [s.sensor_id for s in out.segments[1:]]
        assert sensor_ids == [_D, _D]
        # dock @ 00:03:10 (embed-only, score=1/64) > dock @ 00:05:00 (attr-only, score=0.5/63).
        assert out.segments[1].start == _ts(_D_310)
        assert out.segments[2].start == _ts(_D_500)

    def test_per_space_min_score_drops_noisy_dock_attribute_hit(self):
        # Same fixture but with per_space_min_score={"attribute": 0.5}: the
        # 0.41 dock-attribute hit gets dropped before fusion.
        inp = FusionInput(
            lists=[_warehouse_embed_list(), _warehouse_attribute_list()],
            chunk_seconds=5,
            method="rrf",
            rrf_k=60,
            per_space_min_score={"attribute": 0.5},
            top_k_segments=10,
        )
        out = run_fusion(inp)
        # dock @ 00:05:00 was attribute-only at 0.41 -> filtered out.
        sensor_starts = {(s.sensor_id, s.start) for s in out.segments}
        assert (_D, _ts(_D_500)) not in sensor_starts

    def test_min_fused_score_ratio_ignores_emptied_lists_in_ceiling(self):
        # Regression: a list emptied by per_space_min_score (or bucketize dedupe)
        # contributes 0 to every fused score; its weight must NOT count toward
        # the ceiling, otherwise min_fused_score_ratio over-tightens and drops
        # legitimate survivors.
        #
        # Setup: 2 lists [embed w=1.0, attribute w=1.0] under RRF (k=60).
        # per_space_min_score wipes the entire attribute list.
        # The lone embed rank-1 hit fuses to score 1/61 ~= 0.01639.
        #
        #   buggy ceiling = (1.0 + 1.0) / 61 = 2/61   -> threshold 0.6 * 2/61 = 1.2/61 -> DROP (1/61 < 1.2/61)
        #   fixed ceiling = 1.0 / 61         = 1/61   -> threshold 0.6 * 1/61 = 0.6/61 -> KEEP (1/61 > 0.6/61)
        embed = RankedList(
            space="embed",
            weight=1.0,
            chunks=[_chunk(_W, _W_125, 0.9, 1)],
        )
        attribute = RankedList(
            space="attribute",
            weight=1.0,
            chunks=[_chunk(_W, _W_125, 0.5, 1)],
        )
        inp = FusionInput(
            lists=[embed, attribute],
            chunk_seconds=5,
            method="rrf",
            rrf_k=60,
            per_space_min_score={"attribute": 0.99},
            min_fused_score_ratio=0.6,
        )
        out = run_fusion(inp)

        assert len(out.segments) == 1
        assert out.segments[0].sensor_id == _W
        assert out.segments[0].fused_score == pytest.approx(1 / 61)
        assert out.segments[0].contributing_spaces == ["embed"]

    def test_min_fused_score_ratio_zero_is_no_op(self):
        # ratio=0.0 -> threshold=0; every non-negative fused score passes, matching the None baseline.
        common = {
            "lists": [_warehouse_embed_list(), _warehouse_attribute_list()],
            "top_k_segments": None,
        }
        baseline = run_fusion(FusionInput(**common, min_fused_score_ratio=None))
        zero = run_fusion(FusionInput(**common, min_fused_score_ratio=0.0))
        assert [s.fused_score for s in zero.segments] == [s.fused_score for s in baseline.segments]

    def test_min_fused_score_ratio_one_keeps_only_ceiling_equivalent(self):
        # ratio=1.0 -> threshold=ceiling; only chunks scoring at the ceiling survive
        # (off-by-one guard: filter uses score < threshold, so equality keeps).
        embed = RankedList(
            space="embed",
            weight=1.0,
            chunks=[
                _chunk(_W, _W_125, 0.9, 1),  # rank 1 in BOTH spaces -> ties ceiling
                _chunk(_D, _D_310, 0.5, 2),
            ],
        )
        attribute = RankedList(
            space="attribute",
            weight=1.0,
            chunks=[
                _chunk(_W, _W_125, 0.9, 1),  # rank 1 in BOTH spaces -> ties ceiling
                _chunk(_D, _D_500, 0.5, 2),
            ],
        )
        inp = FusionInput(
            lists=[embed, attribute],
            method="rrf",
            rrf_k=60,
            min_fused_score_ratio=1.0,
            merge_adjacent=False,
        )
        out = run_fusion(inp)
        assert len(out.segments) == 1
        assert out.segments[0].sensor_id == _W
        assert out.segments[0].start == _ts(_W_125)
        assert out.segments[0].fused_score == pytest.approx(2 / 61)

    def test_min_contributing_spaces_2_keeps_only_warehouse(self):
        inp = FusionInput(
            lists=[_warehouse_embed_list(), _warehouse_attribute_list()],
            min_contributing_spaces=2,
            top_k_segments=10,
        )
        out = run_fusion(inp)
        # Only warehouse_01 has cross-validation; merges into one segment.
        # The lone warehouse_01 @ 00:01:35 chunk (embed-only) is dropped, so
        # the merged segment shrinks to 10s.
        assert len(out.segments) == 1
        seg = out.segments[0]
        assert seg.sensor_id == _W
        assert seg.member_chunk_count == 2
        assert seg.start == _ts(_W_125)
        assert seg.end == _ts(_W_130 + 5)  # 00:01:35

    def test_min_contributing_spaces_zero_keeps_single_space_hits(self):
        # 0 disables the vote-count gate; output matches the default min=1 baseline
        # (every chunk has at least one contributing space).
        common = {
            "lists": [_warehouse_embed_list(), _warehouse_attribute_list()],
            "merge_adjacent": False,
            "top_k_segments": None,
        }
        baseline = run_fusion(FusionInput(**common, min_contributing_spaces=1))
        zero = run_fusion(FusionInput(**common, min_contributing_spaces=0))
        assert {s.start for s in zero.segments} == {s.start for s in baseline.segments}

    def test_top_k_segments_caps_response_length(self):
        # top_k_segments=10 caps response length even if more
        # segments survive filtering. Inverse: cap at 1 should truncate.
        inp = FusionInput(
            lists=[_warehouse_embed_list(), _warehouse_attribute_list()],
            top_k_segments=1,
        )
        out = run_fusion(inp)
        assert len(out.segments) == 1

    def test_merge_adjacent_false_yields_one_segment_per_chunk(self):
        inp = FusionInput(
            lists=[_warehouse_embed_list(), _warehouse_attribute_list()],
            merge_adjacent=False,
            top_k_segments=10,
        )
        out = run_fusion(inp)
        # 5 unique chunks across the two spaces, no merging.
        assert len(out.segments) == 5
        for seg in out.segments:
            assert seg.member_chunk_count == 1
            assert seg.end - seg.start == timedelta(seconds=5)

    def test_empty_lists_produce_empty_output(self):
        out = run_fusion(FusionInput(lists=[]))
        assert out.segments == []

    def test_single_list_fusion_preserves_input_ranking(self):
        # Degenerate case: 1 list. Every chunk has 1 contributing space (passes default
        # min=1), and fused scores reduce to RRF for that single space (sorted by rank).
        inp = FusionInput(
            lists=[_warehouse_embed_list()],
            method="rrf",
            rrf_k=60,
            merge_adjacent=False,
            top_k_segments=None,
        )
        out = run_fusion(inp)
        assert [s.fused_score for s in out.segments] == pytest.approx([1 / 61, 1 / 62, 1 / 63, 1 / 64])
        for seg in out.segments:
            assert seg.contributing_spaces == ["embed"]

    def test_descending_sort_by_fused_score(self):
        out = run_fusion(
            FusionInput(
                lists=[_warehouse_embed_list(), _warehouse_attribute_list()],
                merge_adjacent=False,
                top_k_segments=10,
            )
        )
        scores = [s.fused_score for s in out.segments]
        assert scores == sorted(scores, reverse=True)

    def test_top_k_segments_none_returns_all_survivors(self):
        out = run_fusion(
            FusionInput(
                lists=[_warehouse_embed_list(), _warehouse_attribute_list()],
                top_k_segments=None,
                merge_adjacent=False,
            )
        )
        # No cap -> all 5 unique chunks survive default min_contributing_spaces=1.
        assert len(out.segments) == 5

    def test_tied_rrf_score_winner_independent_of_input_list_order(self):
        """Regression: tied fused_score must resolve via deterministic
        tie-breaker, not via dict insertion order from the input lists.

        Setup: two equally-weighted spaces with rank 1 ↔ 2 swapped across
        sensors. Under RRF with k=60, w=1, both chunks score exactly
        1/61 + 1/62 - i.e. they tie. Without a deterministic tie-breaker,
        whichever list is first determines the dict insertion order, which
        then determines the winner of ``top_k_segments=1`` because
        Python's ``sorted`` is stable.

        Ensure this is deterministic via a tie-breaker.
        """
        embed = RankedList(
            space="embed",
            weight=1.0,
            chunks=[
                _chunk("cam-1", 0, 0.9, 1),
                _chunk("cam-2", 0, 0.8, 2),
            ],
        )
        attribute = RankedList(
            space="attribute",
            weight=1.0,
            chunks=[
                _chunk("cam-2", 0, 0.9, 1),
                _chunk("cam-1", 0, 0.8, 2),
            ],
        )

        fwd = run_fusion(FusionInput(lists=[embed, attribute], merge_adjacent=False, top_k_segments=1))
        rev = run_fusion(FusionInput(lists=[attribute, embed], merge_adjacent=False, top_k_segments=1))

        assert len(fwd.segments) == 1
        assert len(rev.segments) == 1
        # cam-1 wins both ways via deterministic tie-break (cam-1 < cam-2 lex).
        assert fwd.segments[0].sensor_id == "cam-1"
        assert rev.segments[0].sensor_id == "cam-1"
        # Sanity: the two chunks really did tie on fused_score.
        assert fwd.segments[0].fused_score == pytest.approx(1 / 61 + 1 / 62)

    def test_same_moment_two_timezones_fuse_into_one_segment(self):
        """End-to-end regression: same wall moment from two spaces in
        different tz shapes must produce ONE FusedSegment (not two)
        contributed by both spaces. Pins the silent miss-merge fix.
        """
        ts_utc = datetime(2025, 1, 1, 0, 1, 25, tzinfo=UTC)
        ts_paris = datetime(2025, 1, 1, 1, 1, 25, tzinfo=ZoneInfo("Europe/Paris"))

        embed = RankedList(
            space="embed",
            chunks=[
                RankedChunk(
                    key=ChunkKey(sensor_id="cam-1", start=ts_utc),
                    score=0.9,
                    rank=1,
                )
            ],
        )
        attribute = RankedList(
            space="attribute",
            chunks=[
                RankedChunk(
                    key=ChunkKey(sensor_id="cam-1", start=ts_paris),
                    score=0.8,
                    rank=1,
                )
            ],
        )

        out = run_fusion(FusionInput(lists=[embed, attribute]))

        assert len(out.segments) == 1
        assert set(out.segments[0].contributing_spaces) == {"embed", "attribute"}


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class TestRankedListContract:
    """Lock in important contracts for `:class:`RankedList``."""

    def test_negative_weight_rejected(self):
        with pytest.raises(ValidationError):
            RankedList(space="embed", weight=-0.1)

    @pytest.mark.parametrize("bad_weight", [math.nan, math.inf, -math.inf])
    def test_non_finite_weight_rejected(self, bad_weight):
        """Loud failure: NaN/Inf weight in -> ValidationError out.

        A NaN weight propagates through every fused_score and the
        theoretical ceiling. An Inf weight steamrolls every score gate.
        Reject at the model boundary so downstream math stays trusting.
        """
        with pytest.raises(ValidationError):
            RankedList(space="embed", weight=bad_weight)


class TestRankedChunkContract:
    """Lock in important contracts for :class:`RankedChunk`."""

    @pytest.mark.parametrize("bad_score", [math.nan, math.inf, -math.inf])
    def test_non_finite_score_rejected(self, bad_score):
        """Loud failure: NaN/Inf score in -> ValidationError out.

        Reject at the model boundary instead so every downstream call site can trust the input.
        """
        with pytest.raises(ValidationError):
            RankedChunk(
                key=ChunkKey(sensor_id="s", start=_ts(0)),
                score=bad_score,
                rank=1,
            )


class TestFusionInputContract:
    """Lock in important contracts for :class:`FusionInput`."""

    @pytest.mark.parametrize("bad_ratio", [math.nan, math.inf, -math.inf])
    def test_non_finite_min_fused_score_ratio_rejected(self, bad_ratio):
        """Loud failure: NaN/Inf ratio in -> ValidationError out."""
        with pytest.raises(ValidationError):
            FusionInput(min_fused_score_ratio=bad_ratio)

    def test_top_k_segments_none_accepted(self):
        """``top_k_segments=None`` means "no cap" and must remain valid."""
        FusionInput(top_k_segments=None)

    def test_min_contributing_spaces_zero_accepted(self):
        """``min_contributing_spaces=0`` disables the gate and must remain valid."""
        FusionInput(min_contributing_spaces=0)

    def test_keep_if_top_n_in_any_space_none_accepted(self):
        """``keep_if_top_n_in_any_space=None`` disables the exemption and must remain valid."""
        FusionInput(keep_if_top_n_in_any_space=None)


class TestChunkKeyTimezoneContract:
    """Pin the tz-awareness contract on ChunkKey.start"""

    def test_naive_datetime_rejected_at_construction(self):
        """Loud failure: naive datetime in -> ValidationError out."""
        naive = datetime(2025, 1, 1, 12, 0, 0)
        assert naive.tzinfo is None  # sanity: this really is naive

        with pytest.raises(ValidationError):
            ChunkKey(sensor_id="cam-1", start=naive)

    def test_same_moment_two_timezones_produce_identical_keys(self):
        """Two ChunkKeys built from the same wall moment in different tz must
        be ``==`` AND hash-equal (so they collide in fuse()'s dict join)."""
        ts_utc = datetime(2025, 1, 1, 12, 0, 0, tzinfo=UTC)
        ts_paris = datetime(2025, 1, 1, 13, 0, 0, tzinfo=ZoneInfo("Europe/Paris"))

        key_utc = ChunkKey(sensor_id="cam-1", start=ts_utc)
        key_paris = ChunkKey(sensor_id="cam-1", start=ts_paris)

        assert key_utc == key_paris
        assert hash(key_utc) == hash(key_paris)
