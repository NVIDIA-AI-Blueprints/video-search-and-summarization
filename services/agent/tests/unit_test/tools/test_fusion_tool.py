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

from vss_agents.tools.fusion import ChunkKey
from vss_agents.tools.fusion import FusionConfig
from vss_agents.tools.fusion import FusionInput
from vss_agents.tools.fusion import FusionOutput
from vss_agents.tools.fusion import RankedChunk
from vss_agents.tools.fusion import RankedList
from vss_agents.tools.fusion import _merge_config_defaults
from vss_agents.tools.fusion import fusion

# ---------------------------------------------------------------------------
# Fixture helpers (mirrors test_fusion.py for cross-test consistency)
# ---------------------------------------------------------------------------


def _ts(seconds: int) -> datetime:
    """Build a UTC datetime offset by ``seconds`` from 2025-01-01T00:00:00Z."""
    return datetime(2025, 1, 1, 0, 0, 0, tzinfo=UTC) + timedelta(seconds=seconds)


def _chunk(sensor: str, start_seconds: int, score: float, rank: int, *, chunk_seconds: int = 5) -> RankedChunk:
    start = _ts(start_seconds)
    return RankedChunk(
        key=ChunkKey(
            sensor_id=sensor,
            start=start,
            end=start + timedelta(seconds=chunk_seconds),
        ),
        score=score,
        rank=rank,
    )


def _two_space_input() -> FusionInput:
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
    return FusionInput(lists=[embed, attribute])


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
    async def test_config_space_weights_drive_fused_scores(self, mock_builder):
        """``FusionConfig.space_weights`` is the source of truth when no override is sent.

        Two runs with the same input and no ``space_weight_overrides``. Run A boosts
        attribute via config; run B leaves attribute absent (-> 1.0 fallback inside
        fuse). The asymmetric boost MUST move the top fused score, proving the
        config dict actually flows through to the math.
        """
        config_with_attr_boost = FusionConfig(space_weights={"embed": 1.0, "attribute": 0.5})
        config_no_attr_boost = FusionConfig(space_weights={"embed": 1.0})

        inner_a = await self._get_inner_fn(config_with_attr_boost, mock_builder)
        inner_b = await self._get_inner_fn(config_no_attr_boost, mock_builder)

        out_a = await inner_a(_two_space_input())
        out_b = await inner_b(_two_space_input())

        top_a = max(out_a.segments, key=lambda s: s.fused_score).fused_score
        top_b = max(out_b.segments, key=lambda s: s.fused_score).fused_score
        assert top_a != top_b, "config.space_weights did not move the top fused score"

    @pytest.mark.asyncio
    async def test_space_weight_overrides_patch_individual_keys(self, mock_builder):
        """``space_weight_overrides`` patches selected keys; missing keys keep config values.

        Per-key merge contract: caller posts ``{"embed": 0.8}``; ``attribute``
        keeps its config value (0.5). End-to-end through the wrapper.
        """
        config = FusionConfig(space_weights={"embed": 1.0, "attribute": 0.5})
        inner = await self._get_inner_fn(config, mock_builder)

        # Baseline: no override -> uses config as-is.
        out_baseline = await inner(_two_space_input())
        # Override only embed: attribute keeps the config 0.5.
        inp = _two_space_input()
        inp = inp.model_copy(update={"space_weight_overrides": {"embed": 0.8}})
        out_patched = await inner(inp)

        top_baseline = max(out_baseline.segments, key=lambda s: s.fused_score).fused_score
        top_patched = max(out_patched.segments, key=lambda s: s.fused_score).fused_score
        # Embed dropped from 1.0 -> 0.8, attribute unchanged. Top score should drop.
        assert top_patched < top_baseline, "space_weight_overrides did not patch embed weight"

    @pytest.mark.asyncio
    async def test_space_weight_overrides_full_replace(self, mock_builder):
        """Caller can override every key via ``space_weight_overrides``.

        Sweep-style usage: eval posts ``{"embed": 0.5, "attribute": 0.5}`` to
        force both spaces to a non-config value. RRF top-score ceiling is
        ``2 * (0.5 / 61) ~= 0.0164``; assert we are within it (proving
        config's ``space_weights={"embed": 9.99, ...}`` did NOT leak through).
        """
        config = FusionConfig(space_weights={"embed": 9.99, "attribute": 9.99})
        inner = await self._get_inner_fn(config, mock_builder)

        inp = _two_space_input()
        inp = inp.model_copy(update={"space_weight_overrides": {"embed": 0.5, "attribute": 0.5}})
        out = await inner(inp)

        top = max(out.segments, key=lambda s: s.fused_score).fused_score
        assert top <= 2 * (0.5 / 61) + 1e-9, "config.space_weights leaked past space_weight_overrides"

    @pytest.mark.asyncio
    async def test_no_overrides_means_config_only(self, mock_builder):
        """``space_weight_overrides=None`` -> identical output to no override at all.

        Locks the contract that ``None`` is a no-op (not "wipe weights to {}").
        """
        config = FusionConfig(space_weights={"embed": 1.0, "attribute": 0.5})
        inner = await self._get_inner_fn(config, mock_builder)

        out_implicit = await inner(_two_space_input())
        inp_explicit = _two_space_input().model_copy(update={"space_weight_overrides": None})
        out_explicit = await inner(inp_explicit)

        top_a = max(out_implicit.segments, key=lambda s: s.fused_score).fused_score
        top_b = max(out_explicit.segments, key=lambda s: s.fused_score).fused_score
        assert top_a == pytest.approx(top_b)

    @pytest.mark.asyncio
    async def test_space_weights_default_applied_to_unlisted_space(self, mock_builder):
        """An undeclared space picks up ``FusionConfig.space_weights_default``.

        Sends a 2-space request where ``embed`` is declared (weight=1.0) but
        ``attribute`` is intentionally absent from both ``space_weights`` and
        ``space_weight_overrides``. Two configs with different defaults
        (1.0 vs 0.0) must produce different top fused scores, proving the knob
        actually feeds into the math.
        """
        config_neutral = FusionConfig(space_weights={"embed": 1.0}, space_weights_default=1.0)
        config_zero = FusionConfig(space_weights={"embed": 1.0}, space_weights_default=0.0)

        inner_neutral = await self._get_inner_fn(config_neutral, mock_builder)
        inner_zero = await self._get_inner_fn(config_zero, mock_builder)

        out_neutral = await inner_neutral(_two_space_input())
        out_zero = await inner_zero(_two_space_input())

        # With default=0.0, attribute contributions vanish -> only embed-only
        # chunks score, and the top fused score must be strictly lower than
        # the default=1.0 run where attribute also votes.
        top_neutral = max(out_neutral.segments, key=lambda s: s.fused_score).fused_score
        top_zero = max(out_zero.segments, key=lambda s: s.fused_score).fused_score
        assert top_zero < top_neutral, "space_weights_default did not flow through to fuse"

    def test_default_space_weights_default_is_neutral(self):
        """``FusionConfig.space_weights_default`` defaults to 1.0 (neutral)."""
        assert FusionConfig().space_weights_default == 1.0

    @pytest.mark.asyncio
    async def test_three_space_fusion_via_wrapper(self, mock_builder):
        """N=3 spaces flows through the wrapper end-to-end.

        Proves the registered tool is not hard-coded to 2 lists: a third
        ``caption`` space participates and its winning chunk shows up in the
        fused output. Also verifies ``contributing_spaces`` reflects the
        per-segment provenance the wrapper passes through.
        """
        config = FusionConfig(space_weights={"embed": 1.0, "attribute": 0.5, "caption": 0.7})
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
                RankedList(
                    space="caption",
                    chunks=[_chunk("warehouse_01", 90, 0.65, 1), _chunk("dock", 300, 0.55, 2)],
                ),
            ]
        )
        out = await inner(inp)

        assert len(out.segments) >= 1
        # warehouse_01 @ 85-95 should consolidate; provenance must reflect all
        # spaces that contributed to it (at minimum embed + attribute + caption
        # for the @ 90 chunk).
        contributing = {sp for seg in out.segments for sp in seg.contributing_spaces}
        assert {"embed", "attribute", "caption"}.issubset(contributing)

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
        inp = FusionInput(lists=[])  # nothing else set

        merged = _merge_config_defaults(inp, config)

        assert merged.rrf_k == 42
        assert merged.top_k_segments == 3
        assert merged.method == "weighted_linear"
        # lists is the request payload, never overridden
        assert merged.lists == []

    def test_set_fields_kept_from_request(self):
        config = FusionConfig(rrf_k=42, top_k_segments=3)
        inp = FusionInput(lists=[], rrf_k=10, top_k_segments=99)

        merged = _merge_config_defaults(inp, config)

        assert merged.rrf_k == 10
        assert merged.top_k_segments == 99

    def test_returns_same_instance_when_no_overlay_needed(self):
        """All mirrored fields explicitly set in request -> no copy needed."""
        config = FusionConfig()
        inp = FusionInput(
            lists=[],
            chunk_seconds=5,
            method="rrf",
            rrf_k=60,
            per_space_min_score={},
            min_contributing_spaces=1,
            keep_if_top_n_in_any_space=None,
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
        inp = FusionInput(lists=[], min_contributing_spaces=2)

        merged = _merge_config_defaults(inp, config)

        assert merged.min_contributing_spaces == 2  # request wins
        assert merged.rrf_k == 42  # config wins
        assert merged.top_k_segments == 99  # config wins


# ---------------------------------------------------------------------------
# Config defaults integrity (read by search.py orchestrator later)
# ---------------------------------------------------------------------------


class TestFusionConfigDefaults:
    """Verify the YAML-facing defaults haven't drifted from the plan."""

    def test_default_payload_merge_priority(self):
        """attribute outranks embed; unlisted spaces (e.g. caption) silently default to 99.

        Read by search.py (not fusion itself) when joining payloads back onto
        FusedSegment.member_keys. Lower value = higher priority.
        """
        config = FusionConfig()
        prio = config.payload_merge_priority

        assert prio == {"attribute": 0, "embed": 1}
        assert prio["attribute"] < prio["embed"]
        # `caption` is intentionally absent so it falls back to the implicit 99.
        assert "caption" not in prio

    def test_default_space_weights(self):
        config = FusionConfig()
        assert config.space_weights == {"embed": 1.0, "attribute": 0.5}

    def test_default_method_is_rrf(self):
        config = FusionConfig()
        assert config.method == "rrf"
        assert config.rrf_k == 60

    def test_default_merge_settings(self):
        config = FusionConfig()
        assert config.merge_adjacent is True
        assert config.merge_gap_chunks == 0
        assert config.segment_score_aggregation == "mean"

    def test_default_filter_settings(self):
        config = FusionConfig()
        assert config.min_contributing_spaces == 1
        assert config.keep_if_top_n_in_any_space is None
        assert config.min_fused_score_ratio is None
        assert config.top_k_segments == 10
