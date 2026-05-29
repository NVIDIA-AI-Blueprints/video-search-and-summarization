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

"""Tests for ``eval.tracking.loaders``.

Pins the three public helpers:

* ``interpolate_tracking_boxes`` — linear interp of translation /
  size / velocity / ego_translation / score and Slerp on rotation.
* ``interpolate_tracks`` — fills in missing timestamps **inside** each
  track's observed time window (endpoints are not extrapolated).
* ``create_tracks`` — groups boxes by scene + timestamp, averages
  per-track score on the prediction side (gt=False), runs
  interpolation, and on the prediction side returns timestamp-sorted
  per-scene dicts.
"""

from collections import defaultdict

import numpy as np
import pytest

from nuscenes.eval.common.data_classes import EvalBoxes

from spatialai_data_utils.eval.tracking import data_classes as dc
from spatialai_data_utils.eval.tracking.data_classes import (
    TrackingBox,
    TrackingConfig,
    TrackingMetricData,
)
from spatialai_data_utils.eval.tracking.loaders import (
    create_tracks,
    interpolate_tracking_boxes,
    interpolate_tracks,
)


# ---------------------------------------------------------------------------
# Test setup — TrackingBox needs TRACKING_NAMES populated; do it via a real
# TrackingConfig so the test stays close to the actual call path.
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _register_tracking_names():
    saved_nelem = TrackingMetricData.nelem
    saved_names = list(dc.TRACKING_NAMES)
    TrackingConfig(
        tracking_names=["person"],
        pretty_tracking_names={"person": "Person"},
        class_range={"person": 50},
        dist_fcn="center_distance",
        dist_th_tp=2.0,
        min_recall=0.1,
        max_boxes_per_sample=500,
        metric_worst={},
        num_thresholds=4,
    )
    yield
    TrackingMetricData.nelem = saved_nelem
    dc.TRACKING_NAMES = saved_names


def _box(*, sample_token, translation, tracking_id, tracking_score=0.5,
         rotation=(1.0, 0.0, 0.0, 0.0), size=(1.0, 2.0, 1.8),
         velocity=(0.0, 0.0), ego_translation=(0.0, 0.0, 0.0)):
    return TrackingBox(
        sample_token=sample_token,
        translation=translation,
        size=size,
        rotation=rotation,
        velocity=velocity,
        ego_translation=ego_translation,
        tracking_id=tracking_id,
        tracking_name="person",
        tracking_score=tracking_score,
    )


# ---------------------------------------------------------------------------
# interpolate_tracking_boxes
# ---------------------------------------------------------------------------


def test_interpolate_tracking_boxes_midpoint():
    left = _box(sample_token="scene_A__0", translation=(0.0, 0.0, 0.0),
                tracking_id="42", tracking_score=0.4)
    right = _box(sample_token="scene_A__2", translation=(10.0, 20.0, 0.0),
                 tracking_id="42", tracking_score=0.8)
    mid = interpolate_tracking_boxes(left, right, right_ratio=0.5)
    np.testing.assert_allclose(mid.translation, (5.0, 10.0, 0.0))
    assert mid.tracking_score == pytest.approx(0.6)


def test_interpolate_tracking_boxes_inherits_right_token_and_id():
    """``sample_token`` and ``tracking_id`` come from the right box."""
    left = _box(sample_token="scene_A__0", translation=(0, 0, 0), tracking_id="42")
    right = _box(sample_token="scene_A__9", translation=(1, 1, 1), tracking_id="42")
    out = interpolate_tracking_boxes(left, right, right_ratio=0.3)
    assert out.sample_token == "scene_A__9"
    assert out.tracking_id == "42"
    assert out.tracking_name == "person"


def test_interpolate_tracking_boxes_slerp_rotation_for_identity_inputs():
    """Interpolating between identity quaternions yields the identity."""
    left = _box(sample_token="scene_A__0", translation=(0, 0, 0), tracking_id="42")
    right = _box(sample_token="scene_A__2", translation=(0, 0, 0), tracking_id="42")
    mid = interpolate_tracking_boxes(left, right, right_ratio=0.5)
    np.testing.assert_allclose(mid.rotation, (1.0, 0.0, 0.0, 0.0), atol=1e-9)


# ---------------------------------------------------------------------------
# interpolate_tracks
# ---------------------------------------------------------------------------


def test_interpolate_tracks_fills_missing_internal_timestamps():
    """Track present at t=0 and t=2 but missing at t=1 — interpolation
    must insert a box at t=1 with translation halfway between the
    endpoints."""
    tracks = defaultdict(list, {
        0: [_box(sample_token="A__0", translation=(0, 0, 0), tracking_id="1")],
        1: [],  # missing for track 1
        2: [_box(sample_token="A__2", translation=(10, 20, 0), tracking_id="1")],
    })
    out = interpolate_tracks(tracks)
    assert len(out[1]) == 1
    np.testing.assert_allclose(out[1][0].translation, (5.0, 10.0, 0.0))


def test_interpolate_tracks_does_not_extrapolate_outside_observed_window():
    """The function only interpolates between observed endpoints —
    timestamps outside the track's range are left as-is (no boxes
    appended)."""
    tracks = defaultdict(list, {
        0: [],  # before first observation
        1: [_box(sample_token="A__1", translation=(0, 0, 0), tracking_id="1")],
        2: [_box(sample_token="A__2", translation=(2, 0, 0), tracking_id="1")],
        3: [],  # after last observation
    })
    out = interpolate_tracks(tracks)
    assert out[0] == []
    assert out[3] == []


# ---------------------------------------------------------------------------
# create_tracks
# ---------------------------------------------------------------------------


def _eval_boxes_from(boxes):
    eb = EvalBoxes()
    for b in boxes:
        eb.add_boxes(b.sample_token, [b])
    return eb


def test_create_tracks_groups_by_scene_then_timestamp():
    boxes = [
        _box(sample_token="sceneA__0", translation=(0, 0, 0), tracking_id="1"),
        _box(sample_token="sceneA__1", translation=(1, 0, 0), tracking_id="1"),
        _box(sample_token="sceneB__0", translation=(0, 0, 0), tracking_id="2"),
    ]
    data_infos = [
        {"scene_name": "sceneA", "frame_idx": 0},
        {"scene_name": "sceneA", "frame_idx": 1},
        {"scene_name": "sceneB", "frame_idx": 0},
    ]
    tracks = create_tracks(_eval_boxes_from(boxes), data_infos, gt=True)
    assert set(tracks.keys()) == {"sceneA", "sceneB"}
    assert set(tracks["sceneA"].keys()) == {0, 1}
    assert set(tracks["sceneB"].keys()) == {0}


def test_create_tracks_averages_score_per_track_for_predictions():
    """On the prediction side (``gt=False``), all boxes that share a
    ``tracking_id`` get the same averaged score across the track —
    used downstream by ``compute_thresholds`` so per-frame score noise
    doesn't dominate the recall sweep."""
    boxes = [
        _box(sample_token="A__0", translation=(0, 0, 0), tracking_id="42",
             tracking_score=0.2),
        _box(sample_token="A__1", translation=(1, 0, 0), tracking_id="42",
             tracking_score=0.8),
    ]
    data_infos = [
        {"scene_name": "A", "frame_idx": 0},
        {"scene_name": "A", "frame_idx": 1},
    ]
    tracks = create_tracks(_eval_boxes_from(boxes), data_infos, gt=False)
    scores = sorted(b.tracking_score for fr in tracks["A"].values() for b in fr)
    assert scores == pytest.approx([0.5, 0.5])


def test_create_tracks_does_not_override_gt_scores():
    """On the GT side scores are left untouched — they remain ``-1.0``
    sentinels (or whatever the caller provided)."""
    boxes = [
        _box(sample_token="A__0", translation=(0, 0, 0), tracking_id="42",
             tracking_score=0.2),
        _box(sample_token="A__1", translation=(1, 0, 0), tracking_id="42",
             tracking_score=0.8),
    ]
    data_infos = [
        {"scene_name": "A", "frame_idx": 0},
        {"scene_name": "A", "frame_idx": 1},
    ]
    tracks = create_tracks(_eval_boxes_from(boxes), data_infos, gt=True)
    scores = sorted(b.tracking_score for fr in tracks["A"].values() for b in fr)
    assert scores == [pytest.approx(0.2), pytest.approx(0.8)]


def test_create_tracks_interpolates_missing_internal_frames():
    """``create_tracks`` runs ``interpolate_tracks`` per scene, so a
    track present at frame 0 and 2 but absent at frame 1 gets an
    interpolated box at 1."""
    boxes = [
        _box(sample_token="A__0", translation=(0, 0, 0), tracking_id="42"),
        _box(sample_token="A__2", translation=(10, 20, 0), tracking_id="42"),
    ]
    data_infos = [
        {"scene_name": "A", "frame_idx": 0},
        {"scene_name": "A", "frame_idx": 1},  # listed but no boxes
        {"scene_name": "A", "frame_idx": 2},
    ]
    tracks = create_tracks(_eval_boxes_from(boxes), data_infos, gt=True)
    assert len(tracks["A"][1]) == 1
    np.testing.assert_allclose(tracks["A"][1][0].translation, (5.0, 10.0, 0.0))


def test_create_tracks_prediction_timestamps_are_sorted():
    """For predictions, per-scene timestamps end up in monotonically
    increasing order (downstream ``accumulate_threshold`` expects
    chronological iteration)."""
    boxes = [
        _box(sample_token="A__2", translation=(2, 0, 0), tracking_id="42"),
        _box(sample_token="A__0", translation=(0, 0, 0), tracking_id="42"),
        _box(sample_token="A__1", translation=(1, 0, 0), tracking_id="42"),
    ]
    data_infos = [
        {"scene_name": "A", "frame_idx": 0},
        {"scene_name": "A", "frame_idx": 1},
        {"scene_name": "A", "frame_idx": 2},
    ]
    tracks = create_tracks(_eval_boxes_from(boxes), data_infos, gt=False)
    assert list(tracks["A"].keys()) == [0, 1, 2]
