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

"""Regression tests for the box conventions of the ``data_infos``-based evaluators.

Ground truth is ``gt_boxes = [x, y, z, d0, d1, d2, yaw]`` (``d0`` along the box x
axis, counter-clockwise yaw).  Predictions are nuScenes results written the way
mmdet3d-style models (Sparse4D ``output_to_nusc_box``) write them:
``size = [d1, d0, d2]`` and ``rotation`` = quaternion of the same yaw.  A perfect
prediction must therefore score perfectly with 3D IoU, and must have zero scale
and orientation error.

Earlier versions negated the GT yaw and read ``size`` unswapped, so an exact
copy of an elongated box only reached IoU 1 at odd multiples of 45 degrees and a
prediction with yaw ``90 deg - yaw`` scored perfectly instead.  The boxes below
are elongated and use yaws away from 45 degrees, so that bug fails these tests.
"""

import json
import math
import os

import numpy as np
import pytest
from pyquaternion import Quaternion

from nuscenes.eval.common.data_classes import EvalBoxes
from nuscenes.eval.common.utils import quaternion_yaw

from spatialai_data_utils.eval.common.box_conventions import (
    nusc_boxes_to_box_dims,
    nusc_size_to_box_dims,
)
from spatialai_data_utils.eval.common.loaders import load_gt
from spatialai_data_utils.eval.detection.data_classes import DetectionBox, DetectionConfig
from spatialai_data_utils.eval.detection.evaluate import AIC24DetEval
from spatialai_data_utils.eval.tracking import data_classes as tracking_dc
from spatialai_data_utils.eval.tracking.aic24_eval import AIC24TrackEval
from spatialai_data_utils.eval.tracking.data_classes import TrackingConfig, TrackingMetricData
from spatialai_data_utils.eval.tracking.hota.hota_eval import evaluate_hota

# Elongated forklift-like boxes (1.2 x 2.9 m footprint) at yaws away from 45 deg.
YAWS = [0.0, math.radians(30), math.radians(90), math.radians(120), math.radians(-160)]
DIMS = (1.2, 2.9, 2.0)
N_FRAMES = 3


def _gt_box(k, yaw):
    """GT box ``k`` of a frame: elongated footprint at ``yaw``, boxes 10 m apart so they never overlap."""
    return [10.0 * k, 5.0, 1.0, DIMS[0], DIMS[1], DIMS[2], yaw]


def _data_infos():
    """``N_FRAMES`` frames of one scene, each with one GT box per entry of ``YAWS`` (stable instance ids)."""
    infos = []
    for f in range(N_FRAMES):
        boxes = np.array([_gt_box(k, yaw) for k, yaw in enumerate(YAWS)], dtype=np.float64)
        infos.append({
            "scene_name": "scene_0",
            "frame_idx": f,
            "token": f"scene_0__{f}",
            "gt_boxes": boxes,
            "gt_names": np.array(["forklift"] * len(YAWS)),
            "gt_velocity": np.zeros((len(YAWS), 3)),
            "instance_inds": np.arange(len(YAWS)),
            "valid_flag": np.ones(len(YAWS), dtype=bool),
        })
    return infos


def _sparse4d_result(g, token, yaw_override=None):
    """One nuScenes result record exactly as Sparse4D's ``output_to_nusc_box`` writes it."""
    yaw = g[6] if yaw_override is None else yaw_override
    return {
        "sample_token": token,
        "translation": [g[0], g[1], g[2]],
        "size": [g[4], g[3], g[5]],                      # dims[[1, 0, 2]]
        "rotation": list(Quaternion(axis=[0, 0, 1], radians=yaw).elements),
        "velocity": [0.0, 0.0],
        "detection_name": "forklift",
        "detection_score": 0.9,
        "attribute_name": "",
        "tracking_name": "forklift",
        "tracking_id": None,
        "tracking_score": 0.9,
    }


def _write_results(tmp_path, infos, yaw_fn=None):
    """Write perfect Sparse4D-style results for ``infos``; ``yaw_fn`` optionally replaces each yaw. Returns the path."""
    results = {}
    for info in infos:
        recs = []
        for k, g in enumerate(info["gt_boxes"]):
            rec = _sparse4d_result(g, info["token"], None if yaw_fn is None else yaw_fn(g[6]))
            rec["tracking_id"] = str(info["instance_inds"][k])
            recs.append(rec)
        results[info["token"]] = recs
    path = os.path.join(str(tmp_path), "results_nusc.json")
    with open(path, "w") as f:
        json.dump({"results": results, "meta": {"use_camera": True, "use_lidar": False, "use_radar": False,
                                                "use_map": False, "use_external": False}}, f)
    return path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def test_nusc_size_to_box_dims_swaps_horizontal_extents():
    """``wlh`` from the result file comes back as ``gt_boxes`` order."""
    assert nusc_size_to_box_dims([2.9, 1.2, 2.0]) == (1.2, 2.9, 2.0)


def test_nusc_boxes_to_box_dims_converts_every_box_in_place():
    """Every box of every sample is converted, in place."""
    boxes = EvalBoxes()
    boxes.add_boxes("a", [DetectionBox(sample_token="a", size=(2.9, 1.2, 2.0), detection_name="forklift")])
    boxes.add_boxes("b", [DetectionBox(sample_token="b", size=(0.5, 0.3, 1.8), detection_name="person"),
                          DetectionBox(sample_token="b", size=(1.0, 2.0, 3.0), detection_name="person")])
    assert nusc_boxes_to_box_dims(boxes) is boxes
    assert [tuple(b.size) for t in ("a", "b") for b in boxes[t]] == [(1.2, 2.9, 2.0), (0.3, 0.5, 1.8), (2.0, 1.0, 3.0)]


def test_load_gt_keeps_gt_boxes_order_and_counter_clockwise_yaw():
    """``load_gt`` keeps every GT box, its ``gt_boxes`` dims order and its counter-clockwise yaw."""
    infos = _data_infos()[:1]
    loaded = load_gt(infos, DetectionBox)[infos[0]["token"]]
    assert len(loaded) == len(infos[0]["gt_boxes"])
    for box, g in zip(loaded, infos[0]["gt_boxes"], strict=True):
        assert tuple(box.size) == pytest.approx(tuple(g[3:6]))
        assert quaternion_yaw(Quaternion(box.rotation)) == pytest.approx(math.atan2(math.sin(g[6]), math.cos(g[6])))


# ---------------------------------------------------------------------------
# End to end: perfect Sparse4D-style predictions
# ---------------------------------------------------------------------------


def test_hota_iou3d_perfect_predictions_score_one(tmp_path):
    """HOTA with 3D IoU: perfect Sparse4D-style predictions score 1."""
    infos = _data_infos()
    res = evaluate_hota(infos, _write_results(tmp_path, infos), str(tmp_path / "out"),
                        ["forklift"], verbose=False, eval_dist_fcn="iou_3d")
    assert res["per_class"]["forklift"]["HOTA"] == pytest.approx(1.0, abs=1e-6)
    assert res["per_class"]["forklift"]["LocA"] == pytest.approx(1.0, abs=1e-6)


def test_hota_iou3d_does_not_reward_mirrored_yaw(tmp_path):
    """Yaw ``90 deg - yaw`` is wrong for every box here, so it must not score perfectly."""
    infos = _data_infos()
    path = _write_results(tmp_path, infos, yaw_fn=lambda yaw: math.pi / 2 - yaw)
    res = evaluate_hota(infos, path, str(tmp_path / "out"), ["forklift"], verbose=False, eval_dist_fcn="iou_3d")
    assert res["per_class"]["forklift"]["LocA"] < 0.9


def _det_config(dist_fcn):
    """Minimal forklift ``DetectionConfig`` for ``center_distance`` (metres) or ``iou_3d`` (1 - IoU) matching."""
    ths = [0.5, 1.0, 2.0, 4.0] if dist_fcn == "center_distance" else [0.3, 0.5, 0.7]
    return DetectionConfig(class_range={"forklift": 100}, dist_fcn=dist_fcn, dist_ths=ths, dist_th_tp=ths[1],
                           min_recall=0.1, min_precision=0.1, max_boxes_per_sample=500, mean_ap_weight=5)


@pytest.mark.parametrize("dist_fcn", ["center_distance", "iou_3d"])
def test_detection_perfect_predictions_have_full_ap_and_zero_box_errors(tmp_path, dist_fcn):
    """AIC24DetEval: perfect predictions get AP 1 and zero translation, scale and orientation error."""
    infos = _data_infos()
    summary = AIC24DetEval(infos, config=_det_config(dist_fcn), result_path=_write_results(tmp_path, infos),
                           output_dir=str(tmp_path / "out"), verbose=False).main(render_curves=False)
    assert all(ap == pytest.approx(1.0) for ap in summary["label_aps"]["forklift"].values())
    errors = summary["label_tp_errors"]["forklift"]
    assert errors["trans_err"] == pytest.approx(0.0, abs=1e-6)
    assert errors["scale_err"] == pytest.approx(0.0, abs=1e-6)
    assert errors["orient_err"] == pytest.approx(0.0, abs=1e-6)


@pytest.fixture
def _restore_tracking_globals():
    """``TrackingConfig`` registers ``TRACKING_NAMES`` / ``TrackingMetricData.nelem`` module-wide."""
    saved_nelem, saved_names = TrackingMetricData.nelem, list(tracking_dc.TRACKING_NAMES)
    yield
    TrackingMetricData.nelem = saved_nelem
    tracking_dc.TRACKING_NAMES = saved_names


def _track_config():
    """Minimal forklift ``TrackingConfig`` with 3D IoU matching (TP at IoU >= 0.5)."""
    return TrackingConfig(
        tracking_names=["forklift"], pretty_tracking_names={"forklift": "Forklift"},
        class_range={"forklift": 100}, dist_fcn="iou_3d", dist_th_tp=0.5, min_recall=0.1,
        max_boxes_per_sample=500,
        metric_worst={"amota": 0.0, "amotp": 1.0, "recall": 0.0, "motar": 0.0, "mota": 0.0, "motp": 1.0,
                      "faf": 500, "gt": -1, "tp": 0, "mt": 0, "ml": -1, "fp": -1, "fn": -1, "ids": -1,
                      "frag": -1, "tid": 20.0, "lgd": 20.0},
        num_thresholds=4,
    )


@pytest.mark.parametrize("mirror_yaw, perfect", [(False, True), (True, False)])
def test_amota_iou3d_perfect_predictions_score_one(tmp_path, _restore_tracking_globals, mirror_yaw, perfect):
    """AIC24TrackEval with 3D IoU: perfect Sparse4D-style tracks reach AMOTA 1; mirrored-yaw tracks do not."""
    infos = _data_infos()
    path = _write_results(tmp_path, infos, yaw_fn=(lambda yaw: math.pi / 2 - yaw) if mirror_yaw else None)
    summary = AIC24TrackEval(infos, config=_track_config(), result_path=path, output_dir=str(tmp_path / "out"),
                             verbose=False).main(render_curves=False)
    if perfect:
        assert summary["label_metrics"]["amota"]["forklift"] == pytest.approx(1.0)
    else:
        assert summary["label_metrics"]["amota"]["forklift"] < 0.9
