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

"""Tests for ``core.post_processing.filters``.

Pins both helpers:

* ``filter_dets_by_conf`` — the four score-field branches
  (detection_score / tracking_score / confidence / fallback) and
  the inclusive threshold semantics (``score >= thresh`` keeps).
* ``filter_by_box_2d_size`` — the strict ``>`` threshold semantics on
  both width and height.
"""

from spatialai_data_utils.core.post_processing.filters import (
    filter_by_box_2d_size,
    filter_dets_by_conf,
)


# ---------------------------------------------------------------------------
# filter_dets_by_conf
# ---------------------------------------------------------------------------


def test_filter_by_detection_score_field():
    out = filter_dets_by_conf(
        [{"detection_score": 0.9}, {"detection_score": 0.3}], thresh=0.5,
    )
    assert out == [0]


def test_filter_by_tracking_score_field():
    out = filter_dets_by_conf(
        [{"tracking_score": 0.8}, {"tracking_score": 0.1}], thresh=0.5,
    )
    assert out == [0]


def test_filter_by_confidence_field():
    out = filter_dets_by_conf(
        [{"confidence": 0.7}, {"confidence": 0.4}], thresh=0.5,
    )
    assert out == [0]


def test_filter_falls_back_to_one_when_no_score_field_present():
    """An object with none of the three score keys is assumed to have
    score 1.0 (so any reasonable threshold keeps it). This is the
    documented fallback — explicit so callers don't drop legitimately
    confident detections lacking score metadata."""
    out = filter_dets_by_conf(
        [{"box": [0, 0, 1, 1]}, {"box": [2, 2, 3, 3]}], thresh=0.99,
    )
    assert out == [0, 1]


def test_filter_uses_first_matching_field_in_priority_order():
    """When multiple score fields are present, ``detection_score``
    wins over ``tracking_score`` over ``confidence`` (the if/elif
    cascade order)."""
    out = filter_dets_by_conf(
        [{"detection_score": 0.1, "tracking_score": 0.9, "confidence": 0.99}],
        thresh=0.5,
    )
    assert out == []  # detection_score 0.1 < 0.5


def test_filter_threshold_is_inclusive():
    """Boxes whose score exactly equals the threshold are kept."""
    out = filter_dets_by_conf(
        [{"detection_score": 0.5}, {"detection_score": 0.5}], thresh=0.5,
    )
    assert out == [0, 1]


def test_filter_empty_list_returns_empty_list():
    assert filter_dets_by_conf([], thresh=0.5) == []


# ---------------------------------------------------------------------------
# filter_by_box_2d_size
# ---------------------------------------------------------------------------


def test_filter_by_size_drops_below_threshold():
    dets = [
        ["person", [10, 20, 50, 80], 0.9],   # w=40, h=60 -> kept
        ["person", [100, 110, 105, 115], 0.8],  # w=5, h=5 -> dropped
        ["person", [200, 210, 250, 270], 0.95],  # w=50, h=60 -> kept
    ]
    out = filter_by_box_2d_size(dets, size_thresh=(10, 10))
    assert len(out) == 2
    assert out[0][1] == [10, 20, 50, 80]
    assert out[1][1] == [200, 210, 250, 270]


def test_filter_by_size_threshold_is_strict_greater_than():
    """A box with width or height exactly equal to the threshold is
    dropped (strict ``>``). Pin this to catch silent drift toward
    inclusive comparison."""
    on_edge_w = [["person", [0, 0, 10, 100], 0.9]]  # w=10 exactly
    assert filter_by_box_2d_size(on_edge_w, size_thresh=(10, 10)) == []
    on_edge_h = [["person", [0, 0, 100, 10], 0.9]]  # h=10 exactly
    assert filter_by_box_2d_size(on_edge_h, size_thresh=(10, 10)) == []


def test_filter_by_size_both_dimensions_must_pass():
    """Width AND height must both exceed the threshold."""
    only_width_ok = [["person", [0, 0, 100, 5], 0.9]]   # w=100, h=5
    only_height_ok = [["person", [0, 0, 5, 100], 0.9]]  # w=5, h=100
    assert filter_by_box_2d_size(only_width_ok, size_thresh=(10, 10)) == []
    assert filter_by_box_2d_size(only_height_ok, size_thresh=(10, 10)) == []


def test_filter_by_size_empty_input_returns_empty():
    assert filter_by_box_2d_size([], size_thresh=(10, 10)) == []
