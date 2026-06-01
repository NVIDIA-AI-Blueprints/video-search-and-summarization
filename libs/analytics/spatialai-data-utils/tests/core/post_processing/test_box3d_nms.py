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

"""Tests for ``core.post_processing.box3d_nms.circle_nms``.

Pins three guarantees relied on by BEV post-processing:

1. Output is ordered highest-score-first (caller treats it as a rank).
2. The distance threshold is a **squared** Euclidean distance — passing
   ``thresh = r ** 2`` suppresses peers within radius ``r``.
3. ``post_max_size`` caps the kept-set length (i.e. it is a hard limit,
   not just a hint).
"""

import numpy as np

from spatialai_data_utils.core.post_processing.box3d_nms import circle_nms


def test_well_separated_detections_all_kept_in_score_order():
    """Three far-apart detections — none should suppress each other and
    output must be ordered highest-score-first."""
    dets = np.array([
        [10.0, 20.0, 0.7],   # index 0 -> mid score
        [30.0, 40.0, 0.9],   # index 1 -> top score
        [-50.0, -50.0, 0.5], # index 2 -> low score
    ])
    keep = circle_nms(dets, thresh=1.0, post_max_size=10)
    assert keep == [1, 0, 2]


def test_nearby_lower_score_detection_is_suppressed():
    """Two detections within ``sqrt(thresh)`` of each other — only the
    higher-score one survives."""
    dets = np.array([
        [10.0, 20.0, 0.9],   # kept
        [10.5, 20.3, 0.8],   # squared dist = 0.34, < 1.0 -> suppressed
        [30.0, 40.0, 0.85],  # far away -> kept
    ])
    keep = circle_nms(dets, thresh=1.0, post_max_size=10)
    assert keep == [0, 2]


def test_threshold_is_squared_distance():
    """At ``r = 2``, points 1.5 m apart (squared dist = 2.25) survive
    with ``thresh = 1.0`` (radius 1 m) but are suppressed with
    ``thresh = 4.0`` (radius 2 m)."""
    dets = np.array([
        [0.0, 0.0, 0.9],
        [1.5, 0.0, 0.8],  # 1.5 m away  -> squared dist 2.25
    ])
    assert circle_nms(dets, thresh=1.0, post_max_size=10) == [0, 1]
    assert circle_nms(dets, thresh=4.0, post_max_size=10) == [0]


def test_post_max_size_caps_output():
    """``post_max_size`` is a hard cap on the returned list length."""
    dets = np.array(
        [[float(10 * i), float(10 * i), 1.0 - 0.01 * i] for i in range(20)]
    )
    keep = circle_nms(dets, thresh=1.0, post_max_size=5)
    assert len(keep) == 5
    # Top-5 by score correspond to the first 5 rows (scores decrease with i).
    assert keep == [0, 1, 2, 3, 4]


def test_post_max_size_runs_nms_before_capping():
    """The cap is applied *after* NMS, not before — supplying many
    near-duplicates should still leave us with the well-separated set,
    not just the top-K by score."""
    dets = np.array([
        [0.0, 0.0, 0.99],
        [0.1, 0.1, 0.98],  # near-duplicate of 0 -> suppressed
        [0.2, 0.2, 0.97],  # near-duplicate of 0 -> suppressed
        [50.0, 50.0, 0.5], # far away, low score -> kept
    ])
    keep = circle_nms(dets, thresh=1.0, post_max_size=2)
    assert keep == [0, 3]
