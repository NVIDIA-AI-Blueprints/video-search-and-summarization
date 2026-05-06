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
"""Integration tests for the fusion NAT tool wrapper.

These tests exercise the registered ``fusion`` function end-to-end through the
NAT registration boundary, the same shape a FastAPI request through
``POST /api/v1/fusion`` follows once the agent is running.

"""

from datetime import UTC
from datetime import datetime
from datetime import timedelta
from unittest.mock import MagicMock

import pytest

from vss_agents.data_models.ranking import ChunkKey
from vss_agents.data_models.ranking import RankedChunk
from vss_agents.data_models.ranking import RankedList
from vss_agents.tools.fusion import FusionConfig
from vss_agents.tools.fusion import FusionInput
from vss_agents.tools.fusion import FusionOutput
from vss_agents.tools.fusion import _merge_config_defaults
from vss_agents.tools.fusion import fusion

# ---------------------------------------------------------------------------
# Fixture helpers (mirrors test_fusion.py for cross-test consistency)
# ---------------------------------------------------------------------------


def _ts(seconds: int) -> datetime:
    """Build a UTC datetime offset by ``seconds`` from 2025-01-01T00:00:00Z."""
    return datetime(2025, 1, 1, 0, 0, 0, tzinfo=UTC) + timedelta(seconds=seconds)


def _chunk(sensor: str, start_seconds: int, score: float, rank: int) -> RankedChunk:
    return RankedChunk(
        key=ChunkKey(sensor_id=sensor, start=_ts(start_seconds)),
        score=score,
        rank=rank,
    )


def _two_space_input(space_weights: dict[str, float] | None = None) -> FusionInput:
    """Two ranked lists with overlap on warehouse_01 @ 00:01:25 / 00:01:30.

    ``RankedList`` is a pure data carrier.
    """
    embed = RankedList(
        space="embed",
        chunks=[
            _chunk("warehouse_01", 85, 0.92, 1),
            _chunk("warehouse_01", 90, 0.88, 2),
            _chunk("warehouse_01", 95, 0.71, 3),
        ],
    )
    attribute = RankedList(
        space="attribute",
        chunks=[
            _chunk("warehouse_01", 90, 0.81, 1),
            _chunk("warehouse_01", 85, 0.74, 2),
            _chunk("dock", 300, 0.41, 3),
        ],
    )
    return FusionInput(
        lists=[embed, attribute],
        space_weights=space_weights or {"embed": 1.0, "attribute": 0.5},
    )


# ---------------------------------------------------------------------------
# Registration boundary
# ---------------------------------------------------------------------------


class TestFusionRegistration:
    """Verify the ``@register_function`` wrapper produces a usable FunctionInfo."""

    @pytest.fixture
    def config(self) -> FusionConfig:
        return FusionConfig()

    @pytest.fixture
    def mock_builder(self) -> MagicMock:
        return MagicMock()

    @pytest.mark.asyncio
    async def test_yields_function_info_with_correct_schemas(self, config, mock_builder):
        gen = fusion.__wrapped__(config, mock_builder)
        function_info = await gen.__anext__()

        assert function_info is not None
        assert function_info.single_fn is not None
        assert function_info.input_schema is FusionInput
        assert function_info.single_output_schema is FusionOutput
        # description should be the inner _fusion docstring (carries the math one-liner)
        assert function_info.description is not None
        assert "ranker" in function_info.description.lower()


# ---------------------------------------------------------------------------
# End-to-end invocation through the registered single_fn
# ---------------------------------------------------------------------------


class TestFusionInvocation:
    """End-to-end: post a FusionInput, get a FusionOutput back."""

    @pytest.fixture
    def mock_builder(self) -> MagicMock:
        return MagicMock()

    async def _get_inner_fn(self, config: FusionConfig, mock_builder: MagicMock):
        gen = fusion.__wrapped__(config, mock_builder)
        function_info = await gen.__anext__()
        return function_info.single_fn

    @pytest.mark.asyncio
    async def test_two_space_rrf_with_default_config(self, mock_builder):
        """Default config + 2 lists -> overlapping chunks fuse, dock filtered by per_space_min_score."""
        config = FusionConfig(per_space_min_score={"attribute": 0.5})
        inner = await self._get_inner_fn(config, mock_builder)

        out: FusionOutput = await inner(_two_space_input())

        assert isinstance(out, FusionOutput)
        assert len(out.segments) >= 1
        # warehouse_01 must dominate; dock @ 0.41 was below the per_space_min_score=0.5 cutoff
        sensors = {seg.sensor_id for seg in out.segments}
        assert "warehouse_01" in sensors
        assert "dock" not in sensors
        # output is sorted desc by fused_score
        scores = [seg.fused_score for seg in out.segments]
        assert scores == sorted(scores, reverse=True)

    @pytest.mark.asyncio
    async def test_request_field_overrides_config(self, mock_builder):
        """When the request explicitly sets a field, that value wins over config."""
        config = FusionConfig(rrf_k=60, top_k_segments=10)
        inner = await self._get_inner_fn(config, mock_builder)

        # Request narrows top_k_segments to 1; config's 10 should be ignored.
        inp = _two_space_input()
        inp = inp.model_copy(update={"top_k_segments": 1})
        out = await inner(inp)

        assert len(out.segments) == 1

    @pytest.mark.asyncio
    async def test_unset_request_field_inherits_config(self, mock_builder):
        """A field absent from the request is overlaid from config defaults."""
        # Config caps to 1 segment; request does not mention top_k_segments
        config = FusionConfig(top_k_segments=1)
        inner = await self._get_inner_fn(config, mock_builder)

        inp = _two_space_input()
        # FusionInput's own default for top_k_segments is 10, but caller did not
        # set it explicitly so config's 1 must win.
        assert "top_k_segments" not in inp.model_fields_set

        out = await inner(inp)
        assert len(out.segments) == 1

    @pytest.mark.asyncio
    async def test_input_space_weights_drive_fused_scores(self, mock_builder):
        """``FusionInput.space_weights`` is the only source of truth for weights.

        Two runs with the same lists but different ``space_weights`` dicts MUST
        produce different top fused scores - proving the per-call weights flow
        through end-to-end through the registered wrapper.
        """
        config = FusionConfig()
        inner = await self._get_inner_fn(config, mock_builder)

        out_attr_high = await inner(_two_space_input(space_weights={"embed": 1.0, "attribute": 1.0}))
        out_attr_low = await inner(_two_space_input(space_weights={"embed": 1.0, "attribute": 0.1}))

        top_high = max(out_attr_high.segments, key=lambda s: s.fused_score).fused_score
        top_low = max(out_attr_low.segments, key=lambda s: s.fused_score).fused_score
        assert top_high != top_low, "space_weights did not move the top fused score"

    @pytest.mark.asyncio
    async def test_missing_space_weight_uses_config_default(self, mock_builder):
        """Safety net: missing keys in ``space_weights`` get filled from config.

        Caller weights only ``embed``; the wrapper fills ``attribute`` from
        ``FusionConfig.space_weights_default``. Naive HTTP callers (eval
        scripts, debug notebooks) get sensible output instead of a 500.
        """
        config = FusionConfig(space_weights_default=1.0)
        inner = await self._get_inner_fn(config, mock_builder)

        # 'attribute' deliberately missing - wrapper should fill it with 1.0.
        inp = _two_space_input(space_weights={"embed": 1.0})
        out = await inner(inp)

        assert isinstance(out, FusionOutput)
        assert len(out.segments) >= 1
        # 'attribute' contributed despite no explicit weight - confirms fill-in.
        contributing = {sp for seg in out.segments for sp in seg.contributing_spaces}
        assert "attribute" in contributing

    @pytest.mark.asyncio
    async def test_space_weights_default_changes_fused_output(self, mock_builder):
        """``FusionConfig.space_weights_default`` actually flows through to fuse.

        Two configs with different defaults (1.0 vs 0.0); request leaves
        ``attribute`` unweighted. With default=0.0 the attribute votes
        contribute 0 to fused scores, so the top score MUST be lower than
        with default=1.0. Proves the knob isn't dead code.
        """
        config_neutral = FusionConfig(space_weights_default=1.0)
        config_zero = FusionConfig(space_weights_default=0.0)
        inner_neutral = await self._get_inner_fn(config_neutral, mock_builder)
        inner_zero = await self._get_inner_fn(config_zero, mock_builder)

        inp_neutral = _two_space_input(space_weights={"embed": 1.0})
        inp_zero = _two_space_input(space_weights={"embed": 1.0})
        out_neutral = await inner_neutral(inp_neutral)
        out_zero = await inner_zero(inp_zero)

        top_neutral = max(out_neutral.segments, key=lambda s: s.fused_score).fused_score
        top_zero = max(out_zero.segments, key=lambda s: s.fused_score).fused_score
        assert top_zero < top_neutral, "space_weights_default did not flow through to fuse"

    @pytest.mark.asyncio
    async def test_multi_space_provenance_via_wrapper(self, mock_builder):
        """Per-segment provenance reflects every contributing space end-to-end.

        With ``EmbeddingSpaceName`` closed to ``{"embed", "attribute"}``,
        we exercise the wrapper at N = 2 (the current ceiling).
        """
        config = FusionConfig()
        inner = await self._get_inner_fn(config, mock_builder)

        inp = FusionInput(
            lists=[
                RankedList(
                    space="embed",
                    chunks=[_chunk("warehouse_01", 85, 0.92, 1), _chunk("warehouse_01", 90, 0.88, 2)],
                ),
                RankedList(
                    space="attribute",
                    chunks=[_chunk("warehouse_01", 85, 0.74, 1), _chunk("warehouse_01", 90, 0.81, 2)],
                ),
            ],
            space_weights={"embed": 1.0, "attribute": 0.5},
        )
        out = await inner(inp)

        assert len(out.segments) >= 1
        # warehouse_01 @ 85-95 should consolidate; provenance must reflect both
        # spaces that contributed to it.
        contributing = {sp for seg in out.segments for sp in seg.contributing_spaces}
        assert {"embed", "attribute"}.issubset(contributing)

    @pytest.mark.asyncio
    async def test_post_fuse_filters_exercised_via_wrapper(self, mock_builder):
        """All four post-fuse filter knobs flow through the wrapper.

        Sets every filter knob via config (none in the request) so this also
        re-exercises the config -> request overlay for filter fields:
        ``min_contributing_spaces``, ``min_fused_score_ratio``,
        ``keep_if_top_n_in_any_space``, ``top_k_segments``.
        """
        config = FusionConfig(
            min_contributing_spaces=2,
            min_fused_score_ratio=0.5,
            keep_if_top_n_in_any_space=1,
            top_k_segments=3,
            per_space_min_score={},  # disable the pre-fuse filter for this run
        )
        inner = await self._get_inner_fn(config, mock_builder)

        out = await inner(_two_space_input())

        assert len(out.segments) <= 3
        # Every surviving segment either has 2+ contributing spaces OR is the top-1
        # (rank 1 in `embed` is warehouse_01 @ 85; rank 1 in `attribute` is
        # warehouse_01 @ 90 - both are 2-space anyway, so the keep_if knob is a
        # belt-and-suspenders pass here).
        for seg in out.segments:
            assert len(seg.contributing_spaces) >= 2

    @pytest.mark.asyncio
    async def test_merge_adjacent_disabled_via_config(self, mock_builder):
        """``merge_adjacent=False`` from config -> contiguous chunks stay separate.

        Without merging, each 5s chunk surfaces as its own segment; flipping the
        flag back on (default) collapses them. End-to-end through the wrapper.
        """
        # The default helper has 3 contiguous embed chunks (85, 90, 95) on warehouse_01.
        config_off = FusionConfig(merge_adjacent=False, per_space_min_score={})
        config_on = FusionConfig(merge_adjacent=True, per_space_min_score={})

        inner_off = await self._get_inner_fn(config_off, mock_builder)
        inner_on = await self._get_inner_fn(config_on, mock_builder)

        out_off = await inner_off(_two_space_input())
        out_on = await inner_on(_two_space_input())

        assert len(out_off.segments) > len(out_on.segments), (
            "disabling merge_adjacent must produce more (un-coalesced) segments"
        )


# ---------------------------------------------------------------------------
# _merge_config_defaults helper (unit-level)
# ---------------------------------------------------------------------------


class TestMergeConfigDefaults:
    """Direct tests for the request-vs-config overlay logic."""

    def test_unset_fields_taken_from_config(self):
        config = FusionConfig(rrf_k=42, top_k_segments=3, method="weighted_linear")
        inp = FusionInput(lists=[], space_weights={})  # nothing else set

        merged = _merge_config_defaults(inp, config)

        assert merged.rrf_k == 42
        assert merged.top_k_segments == 3
        assert merged.method == "weighted_linear"
        # lists is the request payload, never overridden
        assert merged.lists == []

    def test_set_fields_kept_from_request(self):
        config = FusionConfig(rrf_k=42, top_k_segments=3)
        inp = FusionInput(lists=[], space_weights={}, rrf_k=10, top_k_segments=99)

        merged = _merge_config_defaults(inp, config)

        assert merged.rrf_k == 10
        assert merged.top_k_segments == 99

    def test_returns_same_instance_when_no_overlay_needed(self):
        """All shared fields explicitly set in request -> no copy needed.

        Note: ``space_weights`` is required (not part of the shared params)
        so it's always set; the fast-path check only inspects shared knobs.
        """
        config = FusionConfig()
        inp = FusionInput(
            lists=[],
            space_weights={},
            chunk_seconds=5,
            method="rrf",
            rrf_k=60,
            per_space_min_score={},
            min_contributing_spaces=1,
            keep_if_top_n_in_any_space=None,
            required_spaces=[],
            min_fused_score_ratio=None,
            top_k_segments=10,
            merge_adjacent=True,
            merge_gap_chunks=0,
            segment_score_aggregation="mean",
        )

        merged = _merge_config_defaults(inp, config)

        # Fast path: identity, not a copy.
        assert merged is inp

    def test_partial_overlay_respects_set_field_set(self):
        """Mix of set and unset fields - only unset ones get config values."""
        config = FusionConfig(rrf_k=42, min_contributing_spaces=5, top_k_segments=99)
        # Only override min_contributing_spaces in the request
        inp = FusionInput(lists=[], space_weights={}, min_contributing_spaces=2)

        merged = _merge_config_defaults(inp, config)

        assert merged.min_contributing_spaces == 2  # request wins
        assert merged.rrf_k == 42  # config wins
        assert merged.top_k_segments == 99  # config wins
