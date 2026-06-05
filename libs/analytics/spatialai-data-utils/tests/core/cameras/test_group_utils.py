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

"""Tests for the testable surface of ``core.cameras.group_utils``.

Covers the small public helpers that don't require a full sensor /
calibration tree:

* ``parse_moves`` — CLI ``"cam:group"`` parser.
* ``apply_group_reassignments`` — in-place mutator with strict / warn
  modes for missing cameras or target groups.

…and the private grid / sampling helpers used by the clustering
parameter search:

* ``_linspace``, ``_default_grid``, ``_grid_from_stats_or_default``,
  ``_build_fine_grid``, ``_build_start_indices``, ``_configure_logging``.

The heavy orchestrator (``reassign_camera_groups_from_calibration``)
and clustering-loop helpers (``_run_single_config`` / ``_run_grid``)
need a real calibration + clustering manager and are out of scope for
this file; they are covered indirectly by
``test_origin_calculation.py`` and the existing camera-clustering tests.
"""

import logging
import math

import pytest

from spatialai_data_utils.core.cameras import group_utils as gu


# ---------------------------------------------------------------------------
# parse_moves
# ---------------------------------------------------------------------------


class TestParseMoves:
    def test_parses_single_pair(self):
        assert gu.parse_moves(["Camera_01:bev-sensor-1"]) == [
            ("Camera_01", "bev-sensor-1"),
        ]

    def test_parses_multiple_pairs_preserving_order(self):
        out = gu.parse_moves(["A:g1", "B:g2", "C:g1"])
        assert out == [("A", "g1"), ("B", "g2"), ("C", "g1")]

    def test_strips_whitespace_around_camera_and_group(self):
        assert gu.parse_moves(["  Camera_01 : bev-sensor-1  "]) == [
            ("Camera_01", "bev-sensor-1"),
        ]

    def test_only_first_colon_splits_so_group_can_contain_colons(self):
        assert gu.parse_moves(["A:g:has:colons"]) == [("A", "g:has:colons")]

    def test_missing_colon_raises(self):
        with pytest.raises(ValueError, match="Invalid --move format"):
            gu.parse_moves(["A_b"])

    def test_empty_camera_or_group_raises(self):
        with pytest.raises(ValueError, match="empty"):
            gu.parse_moves([":g"])
        with pytest.raises(ValueError, match="empty"):
            gu.parse_moves(["A:"])


# ---------------------------------------------------------------------------
# apply_group_reassignments
# ---------------------------------------------------------------------------


def _calib_with_groups():
    """Three sensors: two already in group g1, one in group g2."""
    return {
        "sensors": [
            {"id": "A", "group": {"name": "g1", "alias": "areaA"}},
            {"id": "B", "group": {"name": "g1", "alias": "areaA"}},
            {"id": "C", "group": {"name": "g2", "alias": "areaC"}},
        ],
    }


class TestApplyGroupReassignments:
    def test_moves_a_camera_to_an_existing_group(self):
        calib = _calib_with_groups()
        updated, warnings = gu.apply_group_reassignments(
            calib, moves=[("C", "g1")], strict=False,
        )
        assert updated == 1
        assert warnings == []
        # C now carries g1's template group dict.
        c = next(s for s in calib["sensors"] if s["id"] == "C")
        assert c["group"]["name"] == "g1"
        assert c["group"]["alias"] == "areaA"

    def test_unknown_camera_warns_in_non_strict_mode(self):
        calib = _calib_with_groups()
        updated, warnings = gu.apply_group_reassignments(
            calib, moves=[("Z", "g1")], strict=False,
        )
        assert updated == 0
        assert len(warnings) == 1 and "Camera 'Z'" in warnings[0]

    def test_unknown_camera_raises_in_strict_mode(self):
        calib = _calib_with_groups()
        with pytest.raises(KeyError, match="Camera 'Z'"):
            gu.apply_group_reassignments(
                calib, moves=[("Z", "g1")], strict=True,
            )

    def test_unknown_target_group_warns_in_non_strict_mode(self):
        calib = _calib_with_groups()
        updated, warnings = gu.apply_group_reassignments(
            calib, moves=[("A", "g99")], strict=False,
        )
        assert updated == 0
        assert len(warnings) == 1 and "Target group 'g99'" in warnings[0]

    def test_unknown_target_group_raises_in_strict_mode(self):
        calib = _calib_with_groups()
        with pytest.raises(KeyError, match="Target group 'g99'"):
            gu.apply_group_reassignments(
                calib, moves=[("A", "g99")], strict=True,
            )

    def test_multiple_moves_partial_success_in_non_strict_mode(self):
        calib = _calib_with_groups()
        updated, warnings = gu.apply_group_reassignments(
            calib,
            moves=[("A", "g2"), ("Z", "g1"), ("B", "g99")],
            strict=False,
        )
        assert updated == 1  # only A->g2 succeeded
        assert len(warnings) == 2


# ---------------------------------------------------------------------------
# _linspace
# ---------------------------------------------------------------------------


class TestLinspace:
    def test_endpoints_match_for_num_greater_than_one(self):
        out = gu._linspace(0.0, 10.0, 5)
        assert out[0] == pytest.approx(0.0)
        assert out[-1] == pytest.approx(10.0)
        assert len(out) == 5

    def test_step_is_uniform(self):
        out = gu._linspace(0.0, 8.0, 5)
        steps = [out[i + 1] - out[i] for i in range(len(out) - 1)]
        assert all(s == pytest.approx(2.0) for s in steps)

    def test_num_equals_one_returns_single_start_value(self):
        assert gu._linspace(3.0, 9.0, 1) == [3.0]

    def test_num_equals_zero_returns_single_start_value(self):
        assert gu._linspace(3.0, 9.0, 0) == [3.0]


# ---------------------------------------------------------------------------
# _default_grid
# ---------------------------------------------------------------------------


class TestDefaultGrid:
    def test_user_values_take_precedence(self):
        user = [0.1, 0.2, 0.3]
        assert gu._default_grid(user, "overlap") == user
        assert gu._default_grid(user, "distance") == user

    def test_overlap_default_is_unit_range(self):
        out = gu._default_grid([], "overlap")
        assert out == [0.0, 0.05, 0.1, 0.2, 0.3, 0.4, 0.5]

    def test_distance_default_in_meters(self):
        out = gu._default_grid([], "distance")
        assert out == [4.0, 6.0, 8.0, 10.0, 12.0, 15.0, 20.0, 30.0]

    def test_unknown_name_falls_back_to_empty_input(self):
        assert gu._default_grid([], "unknown") == []


# ---------------------------------------------------------------------------
# _grid_from_stats_or_default
# ---------------------------------------------------------------------------


class TestGridFromStatsOrDefault:
    def test_user_values_short_circuit_with_none_bounds(self):
        grid, bounds = gu._grid_from_stats_or_default(
            [0.1, 0.2], "overlap", stats={},
        )
        assert grid == [0.1, 0.2]
        assert bounds == (None, None)

    def test_overlap_from_finite_stats_is_clamped_to_unit(self):
        grid, (vmin, vmax) = gu._grid_from_stats_or_default(
            [], "overlap", stats={"overlap_min": -0.5, "overlap_max": 1.5},
        )
        assert all(0.0 <= v <= 1.0 for v in grid)
        assert vmin == -0.5 and vmax == 1.5

    def test_distance_from_finite_stats_clamped_to_nonneg(self):
        grid, _ = gu._grid_from_stats_or_default(
            [], "distance", stats={"distance_min": -5.0, "distance_max": 20.0},
        )
        assert all(v >= 0.0 for v in grid)

    def test_missing_or_non_finite_stats_fall_back_to_default(self):
        grid, bounds = gu._grid_from_stats_or_default(
            [], "overlap", stats={},
        )
        # Default overlap grid is returned, bounds are (None, None)
        assert grid == gu._default_grid([], "overlap")
        assert bounds == (None, None)

    def test_reversed_min_max_is_swapped_defensively(self):
        """If somehow vmin > vmax (defensive guard), the function
        swaps them before building the grid."""
        grid, (_vmin, _vmax) = gu._grid_from_stats_or_default(
            [], "distance",
            stats={"distance_min": 50.0, "distance_max": 10.0},
        )
        # After swap: min=10, max=50 -> grid endpoints reflect that
        assert grid[0] == pytest.approx(10.0)
        assert grid[-1] == pytest.approx(50.0)


# ---------------------------------------------------------------------------
# _build_fine_grid
# ---------------------------------------------------------------------------


class TestBuildFineGrid:
    def test_returns_empty_when_bounds_missing(self):
        assert gu._build_fine_grid(0.5, None, 1.0, "overlap") == []
        assert gu._build_fine_grid(0.5, 0.0, None, "overlap") == []

    def test_returns_singleton_when_span_is_zero(self):
        assert gu._build_fine_grid(0.5, 0.5, 0.5, "overlap") == [0.5]

    def test_centered_best_value_emits_seven_around_it(self):
        out = gu._build_fine_grid(0.5, 0.0, 1.0, "overlap")
        # Centered branch uses deltas (-3..3) -> up to 7 values
        assert 1 <= len(out) <= 7
        # All within [0, 1] (clamped)
        assert all(0.0 <= v <= 1.0 for v in out)

    def test_boundary_best_value_biases_inward(self):
        """When best is at the left edge, only inward deltas are
        used so we don't generate out-of-range candidates."""
        out = gu._build_fine_grid(0.0, 0.0, 1.0, "overlap")
        assert all(v >= 0.0 for v in out)
        assert out == sorted(out)


# ---------------------------------------------------------------------------
# _build_start_indices
# ---------------------------------------------------------------------------


class TestBuildStartIndices:
    def test_user_grid_is_filtered_to_valid_range(self):
        out = gu._build_start_indices([0, 3, 99, -1, 5], num_sensors=6)
        assert out == [0, 3, 5]

    def test_empty_grid_with_small_num_sensors_returns_all_indices(self):
        out = gu._build_start_indices([], num_sensors=3)
        assert out == [0, 1, 2]

    def test_empty_grid_with_large_num_sensors_picks_evenly(self):
        out = gu._build_start_indices([], num_sensors=20)
        # Deterministic even sampling targets 10 picks
        assert len(out) <= 10
        assert all(0 <= idx < 20 for idx in out)
        assert out == sorted(out)

    def test_empty_grid_with_seed_returns_deterministic_random_sample(self):
        a = gu._build_start_indices([], num_sensors=20, seed=42)
        b = gu._build_start_indices([], num_sensors=20, seed=42)
        assert a == b


# ---------------------------------------------------------------------------
# _configure_logging
# ---------------------------------------------------------------------------


def test_configure_logging_smoke_runs_without_error():
    """Sanity check: ``_configure_logging`` toggles logger levels and
    handlers but shouldn't raise for typical input. Wraps the calls
    in try/finally so the module-level ``gu.logger`` is restored to
    its pre-test handlers / level / propagate state — otherwise this
    test bleeds logger config into every subsequent test that uses
    the same logger."""

    class Args:
        pass

    # Snapshot the original logger state so we can restore it later.
    saved_handlers = list(gu.logger.handlers)
    saved_level = gu.logger.level
    saved_propagate = gu.logger.propagate

    try:
        # Reset our module logger to a fresh handler-free state.
        gu.logger.handlers.clear()
        gu.logger.setLevel(logging.NOTSET)

        gu._configure_logging(Args(), verbose=False)
        # Quiet branch leaves the module logger at INFO with propagate=False.
        assert gu.logger.level == logging.INFO
        assert gu.logger.propagate is False

        # Reset and try the verbose branch as well.
        gu.logger.handlers.clear()
        gu.logger.propagate = True
        gu._configure_logging(Args(), verbose=True)
        assert gu.logger.level == logging.INFO
    finally:
        gu.logger.handlers.clear()
        for h in saved_handlers:
            gu.logger.addHandler(h)
        gu.logger.setLevel(saved_level)
        gu.logger.propagate = saved_propagate
