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

"""Tests for ``spatialai_data_utils.configs.eval.detection``.

Covers (1) the two presets exist and only differ on ``dist_fcn``, and
(2) the inline ``class_range`` keys in the config stay in lock-step
with ``eval.common.classes.CLASS_LIST`` (the import cycle forced the
literal to be duplicated; this test catches drift).
"""

from spatialai_data_utils.configs.eval.detection import (
    DET_CONFIG_CENTER_DISTANCE,
    DET_CONFIG_IOU3D,
)
from spatialai_data_utils.eval.common.classes import CLASS_LIST


def test_only_dist_fcn_differs_between_presets():
    diffs = {
        k for k in set(DET_CONFIG_IOU3D) | set(DET_CONFIG_CENTER_DISTANCE)
        if DET_CONFIG_IOU3D.get(k) != DET_CONFIG_CENTER_DISTANCE.get(k)
    }
    assert diffs == {"dist_fcn"}
    assert DET_CONFIG_IOU3D["dist_fcn"] == "iou_3d"
    assert DET_CONFIG_CENTER_DISTANCE["dist_fcn"] == "center_distance"


def test_class_range_matches_class_list():
    assert set(DET_CONFIG_IOU3D["class_range"].keys()) == set(CLASS_LIST), (
        "DET_CONFIG_IOU3D['class_range'] keys drifted from "
        "eval.common.classes.CLASS_LIST. Update the inline literal in "
        "spatialai_data_utils/configs/eval/detection.py to keep the two in sync."
    )


def test_class_range_matches_warehouse_convention():
    """Pin per-class range to the warehouse convention (40 m).

    Earlier revisions shipped ``class_range = 4`` for every class — a 10x
    typo of the canonical warehouse value
    (``configs.object_classes.warehouse.CLASS_RANGE_DICT`` uses 40 m for
    every entry, ``configs.object_classes.default.CLASS_RANGE_DICT``
    uses 40 m for ``person``, and ``data_classes.DetectionConfig``'s
    docstring labels the field "Max detection distance for each class").
    Catch any silent regression back to that value.
    """
    expected_range_m = 40
    bad = {
        cls: rng for cls, rng in DET_CONFIG_IOU3D["class_range"].items()
        if rng != expected_range_m
    }
    assert not bad, (
        f"class_range values should be {expected_range_m} m to match the "
        f"warehouse convention; off-spec entries: {bad}."
    )
