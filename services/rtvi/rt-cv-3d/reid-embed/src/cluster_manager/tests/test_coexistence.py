# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

"""Tests for :mod:`cluster_manager.coexistence`."""

from __future__ import annotations

from typing import Dict

import numpy as np

from cluster_manager.coexistence import detect_coexistence_conflicts
from cluster_manager.types import SubTracklet


def _make_sub(name: str, object_id: int, frames: list[int], sensors: list[str]) -> SubTracklet:
    n = len(frames)
    return SubTracklet(
        name=name,
        batch=0,
        object_id=object_id,
        sub_id=0,
        vecs=np.zeros((n, 4), dtype=np.float32),
        frame_ids=np.asarray(frames, dtype=np.int64),
        sensor_ids=sensors,
        entry_ids=list(range(n)),
    )


def _bbox(key: tuple, lx: float, ty: float, rx: float, by: float) -> tuple:
    return (key, {"leftX": lx, "topY": ty, "rightX": rx, "bottomY": by})


class TestDetectCoexistenceConflicts:
    def test_empty_candidates(self) -> None:
        assert detect_coexistence_conflicts({}, [], {}, frame_gap=30, min_pairs=5, max_iou=0.1) == set()

    def test_same_cam_simultaneous_nonoverlapping_is_conflict(self) -> None:
        sub_a = _make_sub("A", object_id=1, frames=[10, 11, 12, 13, 14, 15], sensors=["cam1"] * 6)
        sub_b = _make_sub("B", object_id=2, frames=[10, 11, 12, 13, 14, 15], sensors=["cam1"] * 6)
        tracking: Dict[tuple, dict[str, float]] = {}
        # Non-overlapping bboxes on all 6 frames for both
        for f in range(10, 16):
            tracking[(1, "cam1", f)] = {"leftX": 0, "topY": 0, "rightX": 5, "bottomY": 5}
            tracking[(2, "cam1", f)] = {"leftX": 100, "topY": 100, "rightX": 105, "bottomY": 105}
        subs = {"A": sub_a, "B": sub_b}
        conflicts = detect_coexistence_conflicts(subs, ["A", "B"], tracking, frame_gap=2, min_pairs=5, max_iou=0.1)
        assert conflicts == {frozenset({"A", "B"})}

    def test_same_cam_overlapping_bboxes_is_not_conflict(self) -> None:
        sub_a = _make_sub("A", object_id=1, frames=[10, 11, 12], sensors=["cam1"] * 3)
        sub_b = _make_sub("B", object_id=2, frames=[10, 11, 12], sensors=["cam1"] * 3)
        tracking: Dict[tuple, dict[str, float]] = {}
        for f in range(10, 13):
            tracking[(1, "cam1", f)] = {"leftX": 0, "topY": 0, "rightX": 10, "bottomY": 10}
            # Almost identical bbox → high IoU
            tracking[(2, "cam1", f)] = {"leftX": 0, "topY": 0, "rightX": 10, "bottomY": 10}
        subs = {"A": sub_a, "B": sub_b}
        conflicts = detect_coexistence_conflicts(subs, ["A", "B"], tracking, frame_gap=2, min_pairs=1, max_iou=0.1)
        assert conflicts == set()

    def test_different_cameras_never_conflict(self) -> None:
        sub_a = _make_sub("A", object_id=1, frames=[10, 11, 12], sensors=["cam1"] * 3)
        sub_b = _make_sub("B", object_id=2, frames=[10, 11, 12], sensors=["cam2"] * 3)
        tracking: Dict[tuple, dict[str, float]] = {}
        for f in range(10, 13):
            tracking[(1, "cam1", f)] = {"leftX": 0, "topY": 0, "rightX": 5, "bottomY": 5}
            tracking[(2, "cam2", f)] = {"leftX": 100, "topY": 100, "rightX": 105, "bottomY": 105}
        subs = {"A": sub_a, "B": sub_b}
        assert detect_coexistence_conflicts(subs, ["A", "B"], tracking, frame_gap=2, min_pairs=1, max_iou=0.1) == set()

    def test_min_pairs_threshold_enforced(self) -> None:
        # Only 3 low-IoU hits, need min_pairs=5
        sub_a = _make_sub("A", object_id=1, frames=[10, 11, 12], sensors=["cam1"] * 3)
        sub_b = _make_sub("B", object_id=2, frames=[10, 11, 12], sensors=["cam1"] * 3)
        tracking: Dict[tuple, dict[str, float]] = {}
        for f in range(10, 13):
            tracking[(1, "cam1", f)] = {"leftX": 0, "topY": 0, "rightX": 5, "bottomY": 5}
            tracking[(2, "cam1", f)] = {"leftX": 100, "topY": 100, "rightX": 105, "bottomY": 105}
        subs = {"A": sub_a, "B": sub_b}
        # frame_gap=0 restricts comparisons to exact frame equality → 3 hits
        assert detect_coexistence_conflicts(subs, ["A", "B"], tracking, frame_gap=0, min_pairs=5, max_iou=0.1) == set()

    def test_missing_tracking_entries_ignored(self) -> None:
        sub_a = _make_sub("A", object_id=1, frames=[10, 11], sensors=["cam1"] * 2)
        sub_b = _make_sub("B", object_id=2, frames=[10, 11], sensors=["cam1"] * 2)
        # Empty tracking data
        assert (
            detect_coexistence_conflicts(
                {"A": sub_a, "B": sub_b},
                ["A", "B"],
                {},
                frame_gap=2,
                min_pairs=1,
                max_iou=0.1,
            )
            == set()
        )

    def test_frame_gap_widens_window(self) -> None:
        sub_a = _make_sub("A", object_id=1, frames=[10, 12, 14, 16, 18], sensors=["cam1"] * 5)
        sub_b = _make_sub("B", object_id=2, frames=[11, 13, 15, 17, 19], sensors=["cam1"] * 5)
        tracking: Dict[tuple, dict[str, float]] = {}
        for f in [10, 12, 14, 16, 18]:
            tracking[(1, "cam1", f)] = {"leftX": 0, "topY": 0, "rightX": 5, "bottomY": 5}
        for f in [11, 13, 15, 17, 19]:
            tracking[(2, "cam1", f)] = {"leftX": 100, "topY": 0, "rightX": 105, "bottomY": 5}
        subs = {"A": sub_a, "B": sub_b}
        # With frame_gap=1, each fa has 1-2 matching fb's → plenty of hits
        conflicts = detect_coexistence_conflicts(subs, ["A", "B"], tracking, frame_gap=1, min_pairs=3, max_iou=0.1)
        assert conflicts == {frozenset({"A", "B"})}

    def test_only_candidate_names_are_considered(self) -> None:
        sub_a = _make_sub("A", object_id=1, frames=[10], sensors=["cam1"])
        sub_b = _make_sub("B", object_id=2, frames=[10], sensors=["cam1"])
        sub_c = _make_sub("C", object_id=3, frames=[10], sensors=["cam1"])
        tracking = {
            (1, "cam1", 10): {"leftX": 0, "topY": 0, "rightX": 5, "bottomY": 5},
            (2, "cam1", 10): {"leftX": 100, "topY": 0, "rightX": 105, "bottomY": 5},
            (3, "cam1", 10): {"leftX": 200, "topY": 0, "rightX": 205, "bottomY": 5},
        }
        subs = {"A": sub_a, "B": sub_b, "C": sub_c}
        # Only consider A and B; C should not appear in any pair
        conflicts = detect_coexistence_conflicts(subs, ["A", "B"], tracking, frame_gap=0, min_pairs=1, max_iou=0.1)
        for pair in conflicts:
            assert "C" not in pair
