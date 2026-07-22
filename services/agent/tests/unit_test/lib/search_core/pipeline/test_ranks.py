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
"""Algebra tests: frozen Ranks/Hit values, chaining, union-by-identity."""

from dataclasses import FrozenInstanceError

import pytest

from lib.search_core.pipeline.ranks import Hit
from lib.search_core.pipeline.ranks import Ranks
from lib.search_core.pipeline.ranks import merge_hits


def _hit(name: str = "v1", sensor: str = "cam1", start: str = "s", end: str = "e", **scores: float) -> Hit:
    return Hit(video_name=name, sensor_id=sensor, start_time=start, end_time=end, scores=dict(scores))


class TestHit:
    def test_frozen(self):
        hit = _hit(embed=0.5)
        with pytest.raises(FrozenInstanceError):
            hit.video_name = "other"  # type: ignore[misc]

    def test_score_read_defaults(self):
        hit = _hit(embed=0.5)
        assert hit.score("embed") == 0.5
        assert hit.score("missing") == 0.0
        assert hit.score("missing", default=-1.0) == -1.0

    def test_with_scores_appends_and_keeps_original(self):
        hit = _hit(embed=0.5)
        enriched = hit.with_scores(attribute=0.3)
        assert enriched.scores == {"embed": 0.5, "attribute": 0.3}
        assert hit.scores == {"embed": 0.5}  # original untouched

    def test_with_scores_conflict_keeps_max(self):
        assert _hit(embed=0.5).with_scores(embed=0.3).score("embed") == 0.5
        assert _hit(embed=0.3).with_scores(embed=0.5).score("embed") == 0.5

    def test_identity_key(self):
        assert _hit().key() == ("v1", "cam1", "s", "e", ())

    def test_identity_distinguishes_objects_in_same_window(self):
        a = Hit(video_name="v", sensor_id="c", start_time="s", end_time="e", object_ids=("7",))
        b = Hit(video_name="v", sensor_id="c", start_time="s", end_time="e", object_ids=("8",))
        assert a.key() != b.key()


class TestMergeHits:
    def test_scores_union_and_metadata_gap_fill(self):
        a = Hit(video_name="v", sensor_id="c", start_time="s", end_time="e", scores={"embed": 0.5})
        b = Hit(
            video_name="v",
            sensor_id="c",
            start_time="s",
            end_time="e",
            screenshot_url="shot",
            description="d",
            object_ids=("7",),
            scores={"attribute": 0.4},
        )
        merged = merge_hits(a, b)
        assert merged.scores == {"embed": 0.5, "attribute": 0.4}
        assert merged.screenshot_url == "shot"
        assert merged.description == "d"
        assert merged.object_ids == ("7",)

    def test_object_ids_union_preserves_order(self):
        a = _hit()
        b = _hit()
        a = Hit(**{**a.__dict__, "object_ids": ("1", "2")})
        b = Hit(**{**b.__dict__, "object_ids": ("2", "3")})
        assert merge_hits(a, b).object_ids == ("1", "2", "3")


class TestRanksChaining:
    def test_pipe_and_or_are_equivalent(self):
        ranks = Ranks.empty().with_hits([_hit(embed=1.0)])

        def double(r: Ranks) -> Ranks:
            return r.with_hits(h.with_scores(doubled=h.score("embed") * 2) for h in r.hits)

        assert (ranks | double).hits[0].score("doubled") == 2.0
        assert ranks.pipe(double).hits[0].score("doubled") == 2.0

    def test_messages_append_only(self):
        ranks = Ranks.empty().with_message("first").with_message("second")
        assert ranks.messages == ("first", "second")

    def test_empty_seed(self):
        seed = Ranks.empty()
        assert seed.hits == () and seed.legs == {} and seed.messages == () and seed.score_key == ""


class TestUnion:
    def test_appends_new_candidates_in_leg_order(self):
        embed_hits = [_hit(start="1", embed=0.9), _hit(start="2", embed=0.8)]
        ranks = Ranks.empty().union("embed", embed_hits)
        assert [h.start_time for h in ranks.hits] == ["1", "2"]
        assert ranks.legs["embed"] == (embed_hits[0].key(), embed_hits[1].key())

    def test_overlapping_candidate_merges_not_duplicates(self):
        ranks = Ranks.empty().union("embed", [_hit(start="1", embed=0.9)])
        ranks = ranks.union("attribute", [_hit(start="1", attribute=0.4), _hit(start="2", attribute=0.3)])
        assert len(ranks.hits) == 2
        merged = ranks.hits[0]
        assert merged.scores == {"embed": 0.9, "attribute": 0.4}
        # both legs' orderings recorded
        assert set(ranks.legs) == {"embed", "attribute"}
        assert len(ranks.legs["attribute"]) == 2

    def test_union_does_not_mutate_input(self):
        seed = Ranks.empty().union("embed", [_hit(start="1", embed=0.9)])
        seed.union("attribute", [_hit(start="1", attribute=0.4)])
        assert seed.hits[0].scores == {"embed": 0.9}
        assert "attribute" not in seed.legs
