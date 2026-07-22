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
"""Ranker tests: fusion math parity with the retired ``_fusion`` module."""

import pytest

from lib.search_core.errors import InvalidInputError
from lib.search_core.pipeline import rank
from lib.search_core.pipeline.ranks import Hit
from lib.search_core.pipeline.ranks import Ranks


def _hit(
    name: str = "v1",
    *,
    sensor: str = "cam1",
    start: str = "2025-01-01T00:00:00Z",
    end: str = "2025-01-01T00:00:05Z",
    object_ids: tuple[str, ...] = (),
    **scores: float,
) -> Hit:
    return Hit(
        video_name=name,
        sensor_id=sensor,
        start_time=start,
        end_time=end,
        object_ids=object_ids,
        scores=dict(scores),
    )


def _ranks(*hits: Hit, score_key: str = "embed") -> Ranks:
    return Ranks.empty().with_hits(hits, score_key=score_key)


class TestGenericRankers:
    def test_rank_by_orders_descending_and_sets_key(self):
        ranks = _ranks(_hit("a", embed=0.2), _hit("b", embed=0.9)) | rank.rank_by("embed")
        assert [h.video_name for h in ranks.hits] == ["b", "a"]
        assert ranks.score_key == "embed"

    def test_filter_by_any_key(self):
        ranks = _ranks(_hit("a", embed=0.2), _hit("b", embed=0.9)) | rank.filter_by("embed", minimum=0.5)
        assert [h.video_name for h in ranks.hits] == ["b"]

    def test_take(self):
        ranks = _ranks(*(_hit(str(i), embed=1.0 - i / 10) for i in range(5))) | rank.take(2)
        assert len(ranks.hits) == 2

    def test_top_percent_keeps_within_fraction_of_max(self):
        ranks = _ranks(_hit("a", embed=1.0), _hit("b", embed=0.8), _hit("c", embed=0.3))
        kept = ranks | rank.top_percent(0.5)
        assert [h.video_name for h in kept.hits] == ["a", "b"]

    @pytest.mark.parametrize("pct", [None, 0.0, 1.0, 1.5])
    def test_top_percent_noop_out_of_range(self, pct):
        ranks = _ranks(_hit("a", embed=1.0), _hit("b", embed=0.1))
        assert len((ranks | rank.top_percent(pct)).hits) == 2

    def test_top_percent_noop_on_non_positive_max(self):
        # For a non-positive max the threshold would sit above the max and drop
        # every hit (including the top one) — the stage must leave the set alone.
        ranks = _ranks(_hit("a", embed=-0.2), _hit("b", embed=-0.9))
        assert len((ranks | rank.top_percent(0.5)).hits) == 2

    def test_rankers_are_total_on_empty(self):
        empty = Ranks.empty()
        for stage in (
            rank.rank_by("embed"),
            rank.filter_by("x", minimum=0.1),
            rank.take(3),
            rank.top_percent(0.5),
            rank.merge_consecutive,
        ):
            assert stage(empty).hits == ()


class TestFusion:
    def test_weighted_linear(self):
        ranks = _ranks(
            _hit("low_embed_high_attr", embed=0.1, attribute=1.0),
            _hit("high_embed_no_attr", embed=0.9),
        ) | rank.fuse.weighted_linear(w_embed=0.35, w_attribute=0.55)
        # 0.35*0.1 + 0.55*1.0 = 0.585  vs  0.35*0.9 = 0.315
        assert [h.video_name for h in ranks.hits] == ["low_embed_high_attr", "high_embed_no_attr"]
        assert ranks.hits[0].score("fusion") == pytest.approx(0.585)
        assert ranks.score_key == "fusion"
        # provenance survives fusion
        assert ranks.hits[0].score("embed") == pytest.approx(0.1)

    def test_rrf_uses_embed_rank_plus_attribute_boost(self):
        ranks = _ranks(
            _hit("first_embed", embed=0.9, attribute=0.0),
            _hit("second_embed", embed=0.5, attribute=1.0),
        ) | rank.fuse.rrf(rrf_k=60, rrf_w=0.5)
        # first: 1/(1+60) = 0.01639 ; second: 1/(2+60) + 0.5 = 0.51613
        assert [h.video_name for h in ranks.hits] == ["second_embed", "first_embed"]
        assert ranks.hits[0].score("fusion") == pytest.approx(1.0 / 62 + 0.5)
        assert ranks.hits[1].score("fusion") == pytest.approx(1.0 / 61)

    def test_rrf_with_attribute_rank_combines_both_ranks(self):
        ranks = _ranks(
            _hit("a", embed=0.9, attribute=0.1),
            _hit("b", embed=0.5, attribute=0.9),
        ) | rank.fuse.rrf_with_attribute_rank(rrf_k=60, rrf_w=0.5)
        # a: 1/61 + 0.5*(1/62) ; b: 1/62 + 0.5*(1/61)
        score_a = 1.0 / 61 + 0.5 / 62
        score_b = 1.0 / 62 + 0.5 / 61
        assert ranks.hits[0].video_name == "a"
        assert ranks.hits[0].score("fusion") == pytest.approx(score_a)
        assert ranks.hits[1].score("fusion") == pytest.approx(score_b)

    def test_by_method_dispatch_and_unknown(self):
        stage = rank.fuse.by_method("rrf", rrf_k=60, rrf_w=0.5, w_embed=0.35, w_attribute=0.55)
        assert stage(_ranks(_hit("a", embed=0.5))).score_key == "fusion"
        with pytest.raises(InvalidInputError):
            rank.fuse.by_method("nope", rrf_k=60, rrf_w=0.5, w_embed=0.35, w_attribute=0.55)

    def test_missing_attribute_scores_default_to_zero(self):
        ranks = _ranks(_hit("a", embed=0.5)) | rank.fuse.weighted_linear(w_embed=1.0, w_attribute=1.0)
        assert ranks.hits[0].score("fusion") == pytest.approx(0.5)


class TestMergeConsecutive:
    def test_overlapping_same_sensor_similar_scores_merge(self):
        ranks = (
            _ranks(
                _hit("v", start="2025-01-01T00:00:00Z", end="2025-01-01T00:00:05Z", embed=0.80, object_ids=("1", "2")),
                _hit("v", start="2025-01-01T00:00:04Z", end="2025-01-01T00:00:09Z", embed=0.82, object_ids=("2", "3")),
            )
            | rank.merge_consecutive
        )
        assert len(ranks.hits) == 1
        merged = ranks.hits[0]
        assert merged.start_time == "2025-01-01T00:00:00Z"
        assert merged.end_time.startswith("2025-01-01T00:00:09")
        assert merged.score("embed") == pytest.approx(0.81)
        assert merged.object_ids == ("1", "2", "3")

    def test_dissimilar_scores_do_not_merge(self):
        ranks = (
            _ranks(
                _hit("v", start="2025-01-01T00:00:00Z", end="2025-01-01T00:00:05Z", embed=0.9),
                _hit("v", start="2025-01-01T00:00:04Z", end="2025-01-01T00:00:09Z", embed=0.2),
            )
            | rank.merge_consecutive
        )
        assert len(ranks.hits) == 2

    def test_different_sensors_do_not_merge(self):
        ranks = (
            _ranks(
                _hit("v", sensor="cam1", start="2025-01-01T00:00:00Z", end="2025-01-01T00:00:05Z", embed=0.8),
                _hit("v", sensor="cam2", start="2025-01-01T00:00:04Z", end="2025-01-01T00:00:09Z", embed=0.8),
            )
            | rank.merge_consecutive
        )
        assert len(ranks.hits) == 2

    def test_non_overlapping_do_not_merge(self):
        ranks = (
            _ranks(
                _hit("v", start="2025-01-01T00:00:00Z", end="2025-01-01T00:00:05Z", embed=0.8),
                _hit("v", start="2025-01-01T00:01:00Z", end="2025-01-01T00:01:05Z", embed=0.8),
            )
            | rank.merge_consecutive
        )
        assert len(ranks.hits) == 2

    def test_malformed_timestamps_kept_unmerged(self):
        ranks = (
            _ranks(
                _hit("bad", start="not-a-time", end="also-bad", embed=0.9),
                _hit("v", start="2025-01-01T00:00:00Z", end="2025-01-01T00:00:05Z", embed=0.8),
            )
            | rank.merge_consecutive
        )
        assert len(ranks.hits) == 2
        assert ranks.hits[0].video_name == "bad"  # sorted by score desc

    def test_result_sorted_by_current_score(self):
        ranks = (
            _ranks(
                _hit("low", start="2025-01-01T01:00:00Z", end="2025-01-01T01:00:05Z", embed=0.2),
                _hit("high", start="2025-01-01T02:00:00Z", end="2025-01-01T02:00:05Z", embed=0.9),
            )
            | rank.merge_consecutive
        )
        assert [h.video_name for h in ranks.hits] == ["high", "low"]

    def test_merges_on_fusion_key_after_fusion(self):
        ranks = (
            _ranks(
                _hit("v", start="2025-01-01T00:00:00Z", end="2025-01-01T00:00:05Z", embed=0.9, attribute=0.5),
                _hit("v", start="2025-01-01T00:00:04Z", end="2025-01-01T00:00:09Z", embed=0.85, attribute=0.5),
            )
            | rank.fuse.weighted_linear(w_embed=0.5, w_attribute=0.5)
            | rank.merge_consecutive
        )
        assert ranks.score_key == "fusion"
        assert len(ranks.hits) == 1
