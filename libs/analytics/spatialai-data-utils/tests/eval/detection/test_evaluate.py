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

import json
import os

from spatialai_data_utils.eval.detection.data_classes import DetectionConfig


def _config():
    return DetectionConfig(
        class_range={"person": 50},
        dist_fcn="center_distance",
        dist_ths=[0.5],
        dist_th_tp=0.5,
        min_recall=0.0,
        min_precision=0.0,
        max_boxes_per_sample=500,
        mean_ap_weight=5,
    )


class MetricsStub:
    def serialize(self):
        return {
            "mean_ap": 0.5,
            "mean_dist_aps": {"person": 0.5},
            "label_tp_errors": {
                "person": {
                    "trans_err": 0.1,
                    "scale_err": 0.2,
                    "orient_err": 0.3,
                    "vel_err": 0.4,
                    "attr_err": 0.5,
                }
            },
            "tp_errors": {},
            "tp_scores": {},
            "nd_score": 0.5,
        }


class MetricDataListStub:
    def serialize(self):
        return {"details": []}


def _real_config():
    """Config keyed on the canonical capitalized class name produced by the
    real ``load_boxes_from_jsonl`` (via ``map_sub_class_to_primary_class``)."""
    return DetectionConfig(
        class_range={"Person": 50},
        dist_fcn="center_distance",
        dist_ths=[0.5],
        dist_th_tp=0.5,
        min_recall=0.0,
        min_precision=0.0,
        max_boxes_per_sample=500,
        mean_ap_weight=5,
    )


def _write_jsonl_record(path, *, timestamp, class_type, coords, confidence=None):
    """Write one NVSchema-style detection record to *path* (single JSONL line)."""
    obj = {"type": class_type, "bbox3d": {"coordinates": list(coords)}}
    if confidence is not None:
        obj["bbox3d"]["confidence"] = confidence
    path.write_text(json.dumps({"timestamp": timestamp, "objects": [obj]}) + "\n")


def test_evaluate_detection_per_bev_sensor_writes_outputs(tmp_path):
    """End-to-end: real loader + real evaluator + real save (no patches).

    Per-sensor GT and prediction are identical → AP=1.0 for the only class,
    which is a stronger signal than just "files exist" and would catch the
    orchestrator silently producing empty outputs.
    """
    from spatialai_data_utils.eval.detection import evaluate as evaluate_mod

    base_dir = tmp_path / "sensors"
    box = [0.0, 0.0, 0.0, 0.5, 0.5, 1.8, 0.0, 0.0, 0.0]
    for sensor in ["bev-sensor-1", "bev-sensor-2"]:
        sensor_dir = base_dir / sensor
        sensor_dir.mkdir(parents=True)
        _write_jsonl_record(
            sensor_dir / "gt.json", timestamp="2025-01-01T12:00:00.000Z",
            class_type="person", coords=box,
        )
        _write_jsonl_record(
            sensor_dir / "pred.json", timestamp="2025-01-01T12:00:00.000Z",
            class_type="person", coords=box, confidence=0.9,
        )

    evaluate_mod._run_detection_per_sensor(
        base_dir=str(base_dir),
        config=_real_config(),
        fps=30.0,
        confidence_threshold=0.0,
        ground_truth_frame_offset_secs=0.0,
    )

    for sensor in ["bev-sensor-1", "bev-sensor-2"]:
        out_dir = base_dir / sensor / "output"
        summary_path = out_dir / "metrics_summary.json"
        assert summary_path.is_file()
        assert (out_dir / "metrics_details.json").is_file()
        summary = json.loads(summary_path.read_text())
        assert summary["mean_dist_aps"]["Person"] == 1.0


def test_save_results_writes_json(tmp_path):
    from spatialai_data_utils.eval.detection.evaluate import save_detection_results

    save_detection_results(MetricsStub(), MetricDataListStub(), str(tmp_path))

    assert (tmp_path / "metrics_summary.json").is_file()
    assert (tmp_path / "metrics_details.json").is_file()


def test_evaluate_uses_accumulate_and_calculators(monkeypatch):
    from spatialai_data_utils.eval.detection import evaluate as evaluate_mod

    calls = {"acc": 0, "ap": 0, "tp": 0}

    def fake_accumulate(*args, **kwargs):
        calls["acc"] += 1
        return object()

    def fake_calc_ap(*args, **kwargs):
        calls["ap"] += 1
        return 0.5

    def fake_calc_tp(*args, **kwargs):
        calls["tp"] += 1
        return 0.1

    monkeypatch.setattr(evaluate_mod, "accumulate", fake_accumulate)
    monkeypatch.setattr(evaluate_mod, "calc_ap", fake_calc_ap)
    monkeypatch.setattr(evaluate_mod, "calc_tp", fake_calc_tp)

    evaluate_mod.evaluate_detection(
        gt_boxes=object(),
        pred_boxes=object(),
        config=_config(),
        verbose=False,
        tp_skip_metrics={},
    )

    assert calls["acc"] == 1
    assert calls["ap"] == 1
    assert calls["tp"] == len(evaluate_mod.TP_METRICS)  # tp_skip_metrics={} -> no skipping


def test_evaluate_detection_for_all_bev_sensors_creates_outputs(tmp_path):
    """End-to-end combined-sensor variant: real loader + real evaluator + real save."""
    from spatialai_data_utils.eval.detection import evaluate as evaluate_mod

    box = [0.0, 0.0, 0.0, 0.5, 0.5, 1.8, 0.0, 0.0, 0.0]
    gt_path = tmp_path / "gt.jsonl"
    pred_path = tmp_path / "pred.jsonl"
    _write_jsonl_record(gt_path, timestamp="2025-01-01T12:00:00.000Z",
                        class_type="person", coords=box)
    _write_jsonl_record(pred_path, timestamp="2025-01-01T12:00:00.000Z",
                        class_type="person", coords=box, confidence=0.9)

    output_dir = tmp_path / "combined_out"
    evaluate_mod.evaluate_detection_all_BEV_sensors(
        ground_truth_file=str(gt_path),
        prediction_file=str(pred_path),
        output_dir=str(output_dir),
        config=_real_config(),
        fps=30.0,
        confidence_threshold=0.0,
        ground_truth_frame_offset_secs=0.0,
    )

    summary_path = output_dir / "metrics_summary.json"
    assert summary_path.is_file()
    assert (output_dir / "metrics_details.json").is_file()
    summary = json.loads(summary_path.read_text())
    assert summary["mean_dist_aps"]["Person"] == 1.0


def test_print_detection_results_invokes_save_and_builds_dataframe(
    tmp_path,
    monkeypatch,
):
    from spatialai_data_utils.eval.detection import evaluate as evaluate_mod

    captured = {}

    class DataFrameStub:
        def __init__(self, data, columns):
            captured["data"] = data
            captured["columns"] = columns

        def to_csv(self, path, index=False):
            captured["csv_path"] = path
            captured["index"] = index
            with open(path, "w") as file:
                file.write("stub csv")

    monkeypatch.setattr(evaluate_mod.pd, "DataFrame", DataFrameStub)

    evaluate_mod.save_detection_results(
        MetricsStub(),
        MetricDataListStub(),
        str(tmp_path),
        sensor_id="bev-sensor-1",
    )

    assert captured["data"] == [
        ["bev-sensor-1", "person", "0.500", "0.100", "0.200", "0.300", "0.400", "0.500"]
    ]
    assert captured["columns"] == [
        "sensor_id",
        "object_class",
        "AP",
        "ATE",
        "ASE",
        "AOE",
        "AVE",
        "AAE",
    ]
    assert captured["index"] is False
    assert os.path.basename(captured["csv_path"]) == "detection_metrics.csv"


# ===========================================================
# Coverage supplement (merged from test_evaluate_coverage.py)
# ===========================================================

"""Coverage supplement for ``eval.detection.evaluate`` — pins the
remaining branches:

* ``render_detection_results`` matplotlib path (PR + TP + dist_pr
  curves).
* ``AIC24DetEval`` ``verbose=True`` + ``main(render_curves=True)``
  drivers.
* ``evaluate_detection_per_BEV_sensor``: ``TypeError`` for bad
  ``config`` type.
* ``_run_detection_per_sensor``: non-directory entry skip and
  missing-gt/pred warn paths.
"""

import json
import logging
import os

import matplotlib

matplotlib.use("Agg")

import pytest  # noqa: E402

from spatialai_data_utils.eval.detection.data_classes import (  # noqa: E402
    DetectionConfig,
    DetectionMetricDataList,
    DetectionMetrics,
)
from spatialai_data_utils.eval.detection.evaluate import (  # noqa: E402
    AIC24DetEval,
    _run_detection_per_sensor,
    evaluate_detection,
    evaluate_detection_per_BEV_sensor,
    render_detection_results,
)

# --- additional imports for migrated chunks ---
import csv
import math
import numpy as np
from pyquaternion import Quaternion  # noqa: F401  (re-exported by migrated chunks)
from nuscenes.eval.common.data_classes import EvalBoxes
from nuscenes.eval.common.utils import center_distance
from nuscenes.eval.detection.algo import accumulate, calc_ap, calc_tp  # noqa: F401
from spatialai_data_utils.eval.common.utils import iou_3d
from spatialai_data_utils.eval.detection.data_classes import DetectionBox


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _two_class_config(class_names=("Person", "Forklift")):
    """Two classes by default — upstream ``summary_plot`` does
    ``plt.subplots(nrows=n_classes, ncols=2)``, which returns a 1D
    array when ``n_classes=1`` and then crashes on ``axes[ind, 0]``.
    Using two classes keeps the returned array 2D."""
    return DetectionConfig(
        class_range={cn: 50 for cn in class_names},
        dist_fcn="center_distance",
        dist_ths=[0.5, 1.0],
        dist_th_tp=0.5,
        min_recall=0.0,
        min_precision=0.0,
        max_boxes_per_sample=500,
        mean_ap_weight=5,
    )


def _write_jsonl(path, *, timestamp, class_type, coords, confidence=None):
    obj = {"type": class_type, "bbox3d": {"coordinates": list(coords)}}
    if confidence is not None:
        obj["bbox3d"]["confidence"] = confidence
    path.write_text(json.dumps({"timestamp": timestamp, "objects": [obj]}) + "\n")


def _evaluated_metric_and_md(tmp_path):
    """Run a tiny perfect-match evaluation and return the metrics
    + metric_data_list ready for ``render_detection_results``."""
    gt = tmp_path / "gt.json"
    pred = tmp_path / "pred.json"
    box = [0.0, 0.0, 0.0, 0.5, 0.5, 1.8, 0.0, 0.0, 0.0]
    _write_jsonl(gt, timestamp="2025-01-01T12:00:00.000Z",
                  class_type="person", coords=box)
    _write_jsonl(pred, timestamp="2025-01-01T12:00:00.000Z",
                  class_type="person", coords=box, confidence=0.9)
    from spatialai_data_utils.eval.detection.loaders import load_boxes_from_jsonl
    gt_b, pred_b = load_boxes_from_jsonl(str(gt), str(pred), fps=30,
                                          confidence_threshold=0.0)
    return evaluate_detection(gt_b, pred_b, _two_class_config(), verbose=False)


# ---------------------------------------------------------------------------
# render_detection_results — matplotlib PDF writes
# ---------------------------------------------------------------------------


@pytest.mark.filterwarnings(
    "ignore:Attempting to set identical low and high ylims:UserWarning"
)
def test_render_detection_results_writes_summary_and_per_class_pdfs(
    tmp_path, monkeypatch,
):
    """Drive the renderer end-to-end and assert the expected PDFs
    landed under *plot_dir*: a summary, one PR + one TP per class,
    and one dist_pr per dist_th.

    Note: upstream ``nuscenes.eval.detection.render`` hardcodes
    ``DETECTION_NAMES`` + ``PRETTY_DETECTION_NAMES`` from the
    nuScenes ontology (car / truck / ...). Patch both for the
    duration of the test so our warehouse-class metric data is
    actually plotted.

    The ``filterwarnings`` decorator silences an upstream-nuScenes
    matplotlib warning: ``class_tp_curve`` calls ``ax.set_ylim(0,
    ylimit)`` and ``ylimit`` collapses to ``0`` for the placeholder
    ``Forklift`` class (which has no TP errors in our minimal
    fixture). Matplotlib then warns "identical low and high ylims".
    That's an upstream behaviour, not a regression worth surfacing
    on every test run."""
    metrics, md_list = _evaluated_metric_and_md(tmp_path)
    cfg = _two_class_config()
    plot_dir = tmp_path / "plots"

    # Patch the render module's class lists + colors map to our
    # warehouse class set. ``DETECTION_COLORS`` is consumed by
    # ``dist_pr_curve`` for the per-class colour cycle.
    from nuscenes.eval.detection import render as ns_render
    monkeypatch.setattr(ns_render, "DETECTION_NAMES", ["Person", "Forklift"])
    monkeypatch.setattr(
        ns_render, "PRETTY_DETECTION_NAMES",
        {"Person": "Person", "Forklift": "Forklift"},
    )
    monkeypatch.setattr(
        ns_render, "DETECTION_COLORS",
        {"Person": "C0", "Forklift": "C1"},
    )

    render_detection_results(metrics, md_list, cfg, str(plot_dir))

    files = sorted(p.name for p in plot_dir.glob("*.pdf"))
    assert "summary.pdf" in files
    # One PR + one TP per class
    for cls in cfg.class_names:
        assert f"{cls}_pr.pdf" in files
        assert f"{cls}_tp.pdf" in files
    # One dist_pr per dist_th
    for dist_th in cfg.dist_ths:
        assert f"dist_pr_{dist_th}.pdf" in files


# ---------------------------------------------------------------------------
# AIC24DetEval — verbose + render_curves drivers
# ---------------------------------------------------------------------------


def test_aic24_main_with_verbose_and_render_curves(tmp_path, caplog, monkeypatch):
    """``verbose=True`` + ``render_curves=True`` exercises the four
    verbose-log branches plus the ``render`` matplotlib path.

    The matplotlib renderer hardcodes nuScenes class names; patch
    them so the test's warehouse-class metrics actually render. Use
    two classes to side-step a 1D-vs-2D ``axes`` shape bug in
    ``summary_plot``."""
    from nuscenes.eval.detection import render as ns_render
    monkeypatch.setattr(ns_render, "DETECTION_NAMES", ["Person", "Forklift"])
    monkeypatch.setattr(
        ns_render, "PRETTY_DETECTION_NAMES",
        {"Person": "Person", "Forklift": "Forklift"},
    )
    monkeypatch.setattr(
        ns_render, "DETECTION_COLORS",
        {"Person": "C0", "Forklift": "C1"},
    )

    gt = tmp_path / "gt.json"
    box = [0.0, 0.0, 0.0, 0.5, 0.5, 1.8, 0.0, 0.0, 0.0]
    _write_jsonl(gt, timestamp="2025-01-01T12:00:00.000Z",
                  class_type="person", coords=box)
    # Prediction file in nuScenes result-list shape (load_prediction
    # consumes ``{"meta": ..., "results": {token: [...]}}``).
    pred = tmp_path / "pred.json"
    pred.write_text(json.dumps({
        "meta": {"use_camera": True, "use_lidar": False, "use_radar": False,
                  "use_map": False, "use_external": False},
        "results": {
            "scene__1": [{
                "sample_token": "scene__1",
                "translation": [0.0, 0.0, 0.0],
                "size": [0.5, 0.5, 1.8],
                "rotation": [1.0, 0.0, 0.0, 0.0],
                "velocity": [0.0, 0.0],
                "detection_name": "Person",
                "detection_score": 0.9,
                "attribute_name": "",
            }],
        },
    }))

    # data_infos in the shape load_gt expects (with the matching
    # sample token).
    data_infos = [{
        "token": "scene__1", "scene_name": "scene", "frame_idx": 1,
        "gt_boxes": [[0.0, 0.0, 0.0, 0.5, 0.5, 1.8, 0.0]],
        "gt_names": ["Person"], "gt_velocity": [[0.0, 0.0]],
        "instance_inds": [1],
    }]

    out_dir = tmp_path / "out"
    with caplog.at_level(logging.INFO):
        evaluator = AIC24DetEval(
            data_infos=data_infos,
            config=_two_class_config(),
            result_path=str(pred),
            output_dir=str(out_dir),
            verbose=True,
        )
        summary = evaluator.main(render_curves=True)

    assert (out_dir / "metrics_summary.json").is_file()
    assert (out_dir / "metrics_details.json").is_file()
    assert (out_dir / "plots" / "summary.pdf").is_file()
    assert "Saving metrics to" in caplog.text
    # 2-class config (Person + Forklift placeholder for the renderer's
    # 2D-subplot requirement) with only Person data -> Person AP=1.0,
    # Forklift AP=0.0, mean_ap=0.5.
    assert summary["mean_dist_aps"]["Person"] == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# evaluate_detection_per_BEV_sensor — bad config type raises
# ---------------------------------------------------------------------------


def test_evaluate_per_bev_sensor_raises_on_invalid_config_type(tmp_path):
    """Passing a ``config`` that's neither a ``DetectionConfig``
    instance nor a dict accepted by ``DetectionConfig.deserialize``
    raises ``TypeError`` with a guidance message."""
    # We don't even need real GT/pred/calibration files because the
    # config-type guard runs after fps/output-dir but before any
    # data loading. Provide a calibration with a valid fps so the
    # function reaches the config check.
    calib = tmp_path / "calib.json"
    calib.write_text(json.dumps({
        "sensors": [{
            "id": "Camera_01",
            "group": {"name": "bev-sensor-1"},
            "attributes": [{"name": "fps", "value": "10"}],
        }],
    }))
    with pytest.raises(TypeError, match="DetectionConfig instance"):
        evaluate_detection_per_BEV_sensor(
            ground_truth_file=str(tmp_path / "gt.jsonl"),
            prediction_file=str(tmp_path / "pred.jsonl"),
            calibration_file=str(calib),
            output_root_dir=str(tmp_path / "out"),
            config="not a config",  # ← triggers the raise
        )


# ---------------------------------------------------------------------------
# _run_detection_per_sensor — non-dir entry + missing-gt/pred branches
# ---------------------------------------------------------------------------


def test_run_per_sensor_skips_non_dir_entries(tmp_path):
    """A file (rather than a sub-directory) sitting in *base_dir*
    should be silently skipped — only sub-directories represent
    BEV sensors."""
    base_dir = tmp_path / "sensors"
    base_dir.mkdir()
    # Stray file alongside what would normally be a sensor dir.
    (base_dir / "stray.txt").write_text("hello")
    # No actual sensor dirs -> the function loops 0 times after the
    # skip and returns cleanly.
    _run_detection_per_sensor(
        str(base_dir), _two_class_config(), fps=10.0,
        confidence_threshold=0.0, ground_truth_frame_offset_secs=0.0,
    )  # no raise


def test_run_per_sensor_logs_missing_gt_or_pred(tmp_path, caplog):
    """Each sensor sub-dir needs both ``gt.json`` and ``pred.json``.
    If either is missing the function logs and skips."""
    base_dir = tmp_path / "sensors"
    # Sensor with only gt.json -> "Prediction data not found"
    only_gt = base_dir / "bev-1"
    only_gt.mkdir(parents=True)
    (only_gt / "gt.json").write_text("{}\n")
    # Sensor with only pred.json -> "Ground truth data not found"
    only_pred = base_dir / "bev-2"
    only_pred.mkdir(parents=True)
    (only_pred / "pred.json").write_text("{}\n")
    with caplog.at_level(logging.INFO):
        _run_detection_per_sensor(
            str(base_dir), _two_class_config(), fps=10.0,
            confidence_threshold=0.0, ground_truth_frame_offset_secs=0.0,
        )
    assert "Prediction data not found" in caplog.text
    assert "Ground truth data not found" in caplog.text


# --- helpers shared across the test classes below ---
def _ec_make_det_box(sample_token, translation, size, rotation=(1, 0, 0, 0),
                  detection_name="person", detection_score=-1.0):
    return DetectionBox(
        sample_token=sample_token,
        translation=tuple(translation),
        size=tuple(size),
        rotation=tuple(rotation),
        velocity=(0.0, 0.0),
        detection_name=detection_name,
        detection_score=float(detection_score),
    )

def _ec_build_eval_boxes(box_specs):
    """Build EvalBoxes from a list of (sample_token, box_kwargs) pairs."""
    eb = EvalBoxes()
    grouped = {}
    for token, kwargs in box_specs:
        grouped.setdefault(token, []).append(kwargs)
    for token, kw_list in grouped.items():
        boxes = [_ec_make_det_box(token, **kw) for kw in kw_list]
        eb.add_boxes(token, boxes)
    return eb

def _ec_make_config(class_names, dist_fcn, dist_ths=None, dist_th_tp=None):
    """Build a minimal DetectionConfig for testing."""
    if dist_ths is None:
        dist_ths = [0.5, 1.0, 2.0, 4.0] if dist_fcn == "center_distance" else [0.3, 0.5, 0.7]
    if dist_th_tp is None:
        dist_th_tp = dist_ths[1]
    return DetectionConfig(
        class_range={c: 100 for c in class_names},
        dist_fcn=dist_fcn,
        dist_ths=dist_ths,
        dist_th_tp=dist_th_tp,
        min_recall=0.1,
        min_precision=0.1,
        max_boxes_per_sample=500,
        mean_ap_weight=5,
    )

def _ec_make_nuscenes_result_json(pred_boxes_dict, tmp_dir):
    """Write a results_nusc.json with tracking-format predictions and return its path."""
    results = {}
    for token, annos in pred_boxes_dict.items():
        results[token] = annos
    data = {
        "results": results,
        "meta": {"use_camera": True, "use_lidar": False, "use_radar": False,
                 "use_map": False, "use_external": False},
    }
    path = os.path.join(tmp_dir, "results_nusc.json")
    with open(path, "w") as f:
        json.dump(data, f)
    return path

# ===================================================================
# Detection AP with center_distance matching
# ===================================================================
class TestCenterDistanceDetectionAP:
    """Tests for the accumulate + calc_ap pipeline using center_distance."""

    def test_perfect_detection(self):
        """Identical GT and predictions should yield AP = 1.0."""
        tokens = [f"sample_{i}" for i in range(5)]
        gt_specs = []
        pred_specs = []
        for t in tokens:
            gt_specs.append((t, dict(translation=[0, 0, 0], size=[2, 2, 2],
                                     detection_name="person")))
            pred_specs.append((t, dict(translation=[0, 0, 0], size=[2, 2, 2],
                                       detection_name="person", detection_score=0.9)))

        gt_boxes = _ec_build_eval_boxes(gt_specs)
        pred_boxes = _ec_build_eval_boxes(pred_specs)

        md = accumulate(gt_boxes, pred_boxes, "person", center_distance, dist_th=2.0)
        ap = calc_ap(md, min_recall=0.1, min_precision=0.1)
        assert ap > 0.99, f"Expected perfect AP, got {ap}"

    def test_no_predictions(self):
        """No predictions → AP = 0."""
        gt_specs = [("s1", dict(translation=[0, 0, 0], size=[1, 1, 1],
                                detection_name="person"))]
        gt_boxes = _ec_build_eval_boxes(gt_specs)
        pred_boxes = EvalBoxes()
        pred_boxes.add_boxes("s1", [])

        md = accumulate(gt_boxes, pred_boxes, "person", center_distance, dist_th=2.0)
        ap = calc_ap(md, min_recall=0.1, min_precision=0.1)
        assert ap == 0.0

    def test_no_ground_truth(self):
        """No GT of the target class → AP = 0 (no_predictions sentinel)."""
        gt_boxes = EvalBoxes()
        gt_boxes.add_boxes("s1", [])
        pred_specs = [("s1", dict(translation=[0, 0, 0], size=[1, 1, 1],
                                  detection_name="person", detection_score=0.9))]
        pred_boxes = _ec_build_eval_boxes(pred_specs)

        md = accumulate(gt_boxes, pred_boxes, "person", center_distance, dist_th=2.0)
        ap = calc_ap(md, min_recall=0.1, min_precision=0.1)
        assert ap == 0.0

    def test_far_prediction_is_false_positive(self):
        """A prediction far from GT should be a false positive → lower AP."""
        gt_specs = [("s1", dict(translation=[0, 0, 0], size=[1, 1, 1],
                                detection_name="person"))]
        pred_specs = [("s1", dict(translation=[100, 100, 0], size=[1, 1, 1],
                                  detection_name="person", detection_score=0.9))]
        gt_boxes = _ec_build_eval_boxes(gt_specs)
        pred_boxes = _ec_build_eval_boxes(pred_specs)

        md = accumulate(gt_boxes, pred_boxes, "person", center_distance, dist_th=2.0)
        ap = calc_ap(md, min_recall=0.1, min_precision=0.1)
        assert ap == 0.0

    def test_close_prediction_is_true_positive(self):
        """A prediction within the dist threshold should be a TP."""
        gt_specs = [("s1", dict(translation=[0, 0, 0], size=[1, 1, 1],
                                detection_name="person"))]
        pred_specs = [("s1", dict(translation=[0.5, 0, 0], size=[1, 1, 1],
                                  detection_name="person", detection_score=0.9))]
        gt_boxes = _ec_build_eval_boxes(gt_specs)
        pred_boxes = _ec_build_eval_boxes(pred_specs)

        md = accumulate(gt_boxes, pred_boxes, "person", center_distance, dist_th=2.0)
        ap = calc_ap(md, min_recall=0.1, min_precision=0.1)
        assert ap > 0.99

    def test_multi_class_separation(self):
        """AP should be computed per class; FPs from another class shouldn't affect the target."""
        gt_specs = [
            ("s1", dict(translation=[0, 0, 0], size=[1, 1, 1], detection_name="person")),
            ("s1", dict(translation=[10, 0, 0], size=[2, 2, 2], detection_name="forklift")),
        ]
        pred_specs = [
            ("s1", dict(translation=[0, 0, 0], size=[1, 1, 1],
                        detection_name="person", detection_score=0.9)),
            ("s1", dict(translation=[10, 0, 0], size=[2, 2, 2],
                        detection_name="forklift", detection_score=0.8)),
        ]
        gt_boxes = _ec_build_eval_boxes(gt_specs)
        pred_boxes = _ec_build_eval_boxes(pred_specs)

        md_person = accumulate(gt_boxes, pred_boxes, "person", center_distance, dist_th=2.0)
        ap_person = calc_ap(md_person, min_recall=0.1, min_precision=0.1)
        assert ap_person > 0.99

        md_forklift = accumulate(gt_boxes, pred_boxes, "forklift", center_distance, dist_th=2.0)
        ap_forklift = calc_ap(md_forklift, min_recall=0.1, min_precision=0.1)
        assert ap_forklift > 0.99

    def test_multiple_samples(self):
        """AP across multiple samples with mixed correct/incorrect detections."""
        gt_specs = [
            ("s1", dict(translation=[0, 0, 0], size=[1, 1, 1], detection_name="person")),
            ("s2", dict(translation=[5, 5, 0], size=[1, 1, 1], detection_name="person")),
            ("s3", dict(translation=[10, 10, 0], size=[1, 1, 1], detection_name="person")),
        ]
        pred_specs = [
            ("s1", dict(translation=[0, 0, 0], size=[1, 1, 1],
                        detection_name="person", detection_score=0.95)),
            ("s2", dict(translation=[5, 5, 0], size=[1, 1, 1],
                        detection_name="person", detection_score=0.85)),
            # s3: prediction too far away
            ("s3", dict(translation=[50, 50, 0], size=[1, 1, 1],
                        detection_name="person", detection_score=0.5)),
        ]
        gt_boxes = _ec_build_eval_boxes(gt_specs)
        pred_boxes = _ec_build_eval_boxes(pred_specs)

        md = accumulate(gt_boxes, pred_boxes, "person", center_distance, dist_th=2.0)
        ap = calc_ap(md, min_recall=0.1, min_precision=0.1)
        # 2 out of 3 TPs; AP should be above 0 but below 1
        assert 0.0 < ap < 1.0

    def test_duplicate_predictions_only_one_matches(self):
        """Two predictions for the same GT: only the highest-confidence one should match."""
        gt_specs = [("s1", dict(translation=[0, 0, 0], size=[1, 1, 1],
                                detection_name="person"))]
        pred_specs = [
            ("s1", dict(translation=[0.1, 0, 0], size=[1, 1, 1],
                        detection_name="person", detection_score=0.9)),
            ("s1", dict(translation=[0.2, 0, 0], size=[1, 1, 1],
                        detection_name="person", detection_score=0.8)),
        ]
        gt_boxes = _ec_build_eval_boxes(gt_specs)
        pred_boxes = _ec_build_eval_boxes(pred_specs)

        md = accumulate(gt_boxes, pred_boxes, "person", center_distance, dist_th=2.0)
        ap = calc_ap(md, min_recall=0.1, min_precision=0.1)
        # 1 TP + 1 FP; AP should reflect the drop
        assert 0.0 < ap < 1.0

    def test_config_dist_fcn_callable(self):
        """DetectionConfig.dist_fcn_callable should return center_distance for 'center_distance'."""
        config = _ec_make_config(["person"], "center_distance")
        assert config.dist_fcn_callable is center_distance

    def test_full_detection_metrics_pipeline(self):
        """End-to-end: build DetectionMetrics from accumulate+calc_ap like AIC24DetEval.evaluate."""
        class_names = ["person", "forklift"]
        config = _ec_make_config(class_names, "center_distance")

        gt_specs = [
            ("s1", dict(translation=[0, 0, 0], size=[1, 1, 1], detection_name="person")),
            ("s1", dict(translation=[5, 0, 0], size=[2, 3, 2], detection_name="forklift")),
            ("s2", dict(translation=[0, 0, 0], size=[1, 1, 1], detection_name="person")),
        ]
        pred_specs = [
            ("s1", dict(translation=[0.1, 0, 0], size=[1, 1, 1],
                        detection_name="person", detection_score=0.95)),
            ("s1", dict(translation=[5.1, 0, 0], size=[2, 3, 2],
                        detection_name="forklift", detection_score=0.85)),
            ("s2", dict(translation=[0, 0, 0], size=[1, 1, 1],
                        detection_name="person", detection_score=0.90)),
        ]
        gt_boxes = _ec_build_eval_boxes(gt_specs)
        pred_boxes = _ec_build_eval_boxes(pred_specs)

        metric_data_list = DetectionMetricDataList()
        for class_name in class_names:
            for dist_th in config.dist_ths:
                md = accumulate(gt_boxes, pred_boxes, class_name,
                                config.dist_fcn_callable, dist_th)
                metric_data_list.set(class_name, dist_th, md)

        metrics = DetectionMetrics(config)
        for class_name in class_names:
            for dist_th in config.dist_ths:
                metric_data = metric_data_list[(class_name, dist_th)]
                ap = calc_ap(metric_data, config.min_recall, config.min_precision)
                metrics.add_label_ap(class_name, dist_th, ap)

        assert metrics.mean_ap > 0.5
        for class_name in class_names:
            assert metrics.mean_dist_aps[class_name] > 0.0


# ===================================================================
# Detection AP with 3D IoU matching
# ===================================================================
class TestIoU3DDetectionAP:
    """Tests for the accumulate + calc_ap pipeline using iou_3d distance."""

    def test_identical_boxes_perfect_ap(self):
        """Identical GT and predictions → 1 - IoU = 0 distance → AP = 1.0 for any threshold."""
        tokens = [f"sample_{i}" for i in range(3)]
        gt_specs = []
        pred_specs = []
        for t in tokens:
            gt_specs.append((t, dict(translation=[0, 0, 0], size=[2, 2, 2],
                                     detection_name="person")))
            pred_specs.append((t, dict(translation=[0, 0, 0], size=[2, 2, 2],
                                       detection_name="person", detection_score=0.9)))

        gt_boxes = _ec_build_eval_boxes(gt_specs)
        pred_boxes = _ec_build_eval_boxes(pred_specs)

        # iou_3d returns 1 - IoU; threshold 0.5 means IoU > 0.5
        md = accumulate(gt_boxes, pred_boxes, "person", iou_3d, dist_th=0.5)
        ap = calc_ap(md, min_recall=0.1, min_precision=0.1)
        assert ap > 0.99

    def test_non_overlapping_boxes_zero_ap(self):
        """Non-overlapping boxes → IoU = 0, distance = 1.0 → FP for any threshold < 1."""
        gt_specs = [("s1", dict(translation=[0, 0, 0], size=[1, 1, 1],
                                detection_name="person"))]
        pred_specs = [("s1", dict(translation=[100, 100, 100], size=[1, 1, 1],
                                  detection_name="person", detection_score=0.9))]
        gt_boxes = _ec_build_eval_boxes(gt_specs)
        pred_boxes = _ec_build_eval_boxes(pred_specs)

        md = accumulate(gt_boxes, pred_boxes, "person", iou_3d, dist_th=0.5)
        ap = calc_ap(md, min_recall=0.1, min_precision=0.1)
        assert ap == 0.0

    def test_partial_overlap_threshold_sensitivity(self):
        """Half-overlapping boxes should pass a loose threshold but fail a strict one."""
        # Two boxes shifted by 1m along x: IoU ~= 1/3 → distance ~= 2/3
        gt_specs = [("s1", dict(translation=[0, 0, 0], size=[2, 2, 2],
                                detection_name="person"))]
        pred_specs = [("s1", dict(translation=[1, 0, 0], size=[2, 2, 2],
                                  detection_name="person", detection_score=0.9))]
        gt_boxes = _ec_build_eval_boxes(gt_specs)
        pred_boxes = _ec_build_eval_boxes(pred_specs)

        # Loose threshold (dist < 0.8 → IoU > 0.2): should pass
        md_loose = accumulate(gt_boxes, pred_boxes, "person", iou_3d, dist_th=0.8)
        ap_loose = calc_ap(md_loose, min_recall=0.1, min_precision=0.1)
        assert ap_loose > 0.99

        # Strict threshold (dist < 0.3 → IoU > 0.7): should fail
        md_strict = accumulate(gt_boxes, pred_boxes, "person", iou_3d, dist_th=0.3)
        ap_strict = calc_ap(md_strict, min_recall=0.1, min_precision=0.1)
        assert ap_strict == 0.0

    def test_rotated_box_self_match(self):
        """A rotated box matched against itself should have IoU = 1."""
        q = Quaternion(axis=[0, 0, 1], angle=math.pi / 4)
        rot = (q.w, q.x, q.y, q.z)
        gt_specs = [("s1", dict(translation=[5, 5, 0], size=[3, 1, 2],
                                rotation=rot, detection_name="person"))]
        pred_specs = [("s1", dict(translation=[5, 5, 0], size=[3, 1, 2],
                                  rotation=rot, detection_name="person",
                                  detection_score=0.9))]
        gt_boxes = _ec_build_eval_boxes(gt_specs)
        pred_boxes = _ec_build_eval_boxes(pred_specs)

        md = accumulate(gt_boxes, pred_boxes, "person", iou_3d, dist_th=0.5)
        ap = calc_ap(md, min_recall=0.1, min_precision=0.1)
        assert ap > 0.99

    def test_config_dist_fcn_callable_iou(self):
        """DetectionConfig.dist_fcn_callable should return iou_3d for 'iou_3d'."""
        config = _ec_make_config(["person"], "iou_3d")
        assert config.dist_fcn_callable is iou_3d

    def test_multi_class_iou_matching(self):
        """Per-class AP with iou_3d: each class independently matched."""
        gt_specs = [
            ("s1", dict(translation=[0, 0, 0], size=[2, 2, 2], detection_name="person")),
            ("s1", dict(translation=[20, 0, 0], size=[4, 2, 3], detection_name="forklift")),
        ]
        pred_specs = [
            ("s1", dict(translation=[0, 0, 0], size=[2, 2, 2],
                        detection_name="person", detection_score=0.9)),
            ("s1", dict(translation=[20, 0, 0], size=[4, 2, 3],
                        detection_name="forklift", detection_score=0.85)),
        ]
        gt_boxes = _ec_build_eval_boxes(gt_specs)
        pred_boxes = _ec_build_eval_boxes(pred_specs)

        for cls in ["person", "forklift"]:
            md = accumulate(gt_boxes, pred_boxes, cls, iou_3d, dist_th=0.5)
            ap = calc_ap(md, min_recall=0.1, min_precision=0.1)
            assert ap > 0.99, f"AP for {cls} should be ~1.0, got {ap}"

    def test_full_iou3d_metrics_pipeline(self):
        """End-to-end DetectionMetrics using iou_3d distance."""
        class_names = ["person"]
        config = _ec_make_config(class_names, "iou_3d", dist_ths=[0.3, 0.5, 0.7], dist_th_tp=0.5)

        gt_specs = [
            ("s1", dict(translation=[0, 0, 0], size=[2, 2, 2], detection_name="person")),
            ("s2", dict(translation=[0, 0, 0], size=[2, 2, 2], detection_name="person")),
        ]
        pred_specs = [
            ("s1", dict(translation=[0, 0, 0], size=[2, 2, 2],
                        detection_name="person", detection_score=0.9)),
            ("s2", dict(translation=[0.5, 0, 0], size=[2, 2, 2],
                        detection_name="person", detection_score=0.8)),
        ]
        gt_boxes = _ec_build_eval_boxes(gt_specs)
        pred_boxes = _ec_build_eval_boxes(pred_specs)

        metric_data_list = DetectionMetricDataList()
        for class_name in class_names:
            for dist_th in config.dist_ths:
                md = accumulate(gt_boxes, pred_boxes, class_name,
                                config.dist_fcn_callable, dist_th)
                metric_data_list.set(class_name, dist_th, md)

        metrics = DetectionMetrics(config)
        for class_name in class_names:
            for dist_th in config.dist_ths:
                metric_data = metric_data_list[(class_name, dist_th)]
                ap = calc_ap(metric_data, config.min_recall, config.min_precision)
                metrics.add_label_ap(class_name, dist_th, ap)

        assert metrics.mean_ap > 0.0
        serialized = metrics.serialize()
        assert "mean_ap" in serialized
        assert "mean_dist_aps" in serialized

    def test_iou3d_score_ordering_matters(self):
        """Higher-confidence predictions should be matched first, affecting AP."""
        gt_specs = [
            ("s1", dict(translation=[0, 0, 0], size=[2, 2, 2], detection_name="person")),
        ]
        # Two predictions: one overlaps, one doesn't. The high-confidence one is the FP.
        pred_specs = [
            ("s1", dict(translation=[100, 0, 0], size=[2, 2, 2],
                        detection_name="person", detection_score=0.95)),
            ("s1", dict(translation=[0, 0, 0], size=[2, 2, 2],
                        detection_name="person", detection_score=0.5)),
        ]
        gt_boxes = _ec_build_eval_boxes(gt_specs)
        pred_boxes = _ec_build_eval_boxes(pred_specs)

        md = accumulate(gt_boxes, pred_boxes, "person", iou_3d, dist_th=0.5)
        ap = calc_ap(md, min_recall=0.1, min_precision=0.1)
        # First sorted pred (score=0.95) is FP, second (score=0.5) is TP → precision dips first
        assert 0.0 < ap < 1.0


# ===================================================================
# HOTA tracking with center-distance matching (eval_dist_fcn="center_distance")

class TestEvaluateDetectionFunction:
    """Tests for the unified evaluate_detection() wrapper."""

    def test_basic_evaluation(self):
        """evaluate_detection returns correct metrics for a simple case."""
        from spatialai_data_utils.eval.detection.evaluate import evaluate_detection

        class_names = ["person", "forklift"]
        config = _ec_make_config(class_names, "center_distance")

        gt_specs = [
            ("s1", dict(translation=[0, 0, 0], size=[1, 1, 1], detection_name="person")),
            ("s1", dict(translation=[5, 0, 0], size=[2, 3, 2], detection_name="forklift")),
            ("s2", dict(translation=[0, 0, 0], size=[1, 1, 1], detection_name="person")),
        ]
        pred_specs = [
            ("s1", dict(translation=[0.1, 0, 0], size=[1, 1, 1],
                        detection_name="person", detection_score=0.95)),
            ("s1", dict(translation=[5.1, 0, 0], size=[2, 3, 2],
                        detection_name="forklift", detection_score=0.85)),
            ("s2", dict(translation=[0, 0, 0], size=[1, 1, 1],
                        detection_name="person", detection_score=0.90)),
        ]
        gt_boxes = _ec_build_eval_boxes(gt_specs)
        pred_boxes = _ec_build_eval_boxes(pred_specs)

        metrics, md_list = evaluate_detection(
            gt_boxes, pred_boxes, config, verbose=False,
        )
        assert metrics.mean_ap > 0.5
        for cn in class_names:
            assert metrics.mean_dist_aps[cn] > 0.0
        assert metrics.eval_time is not None

    def test_tp_skip_metrics_default(self):
        """Default tp_skip_metrics sets attr_err and vel_err to NaN for all classes."""
        from spatialai_data_utils.eval.detection.evaluate import evaluate_detection

        config = _ec_make_config(["person"], "center_distance")
        gt = _ec_build_eval_boxes([
            ("s1", dict(translation=[0, 0, 0], size=[1, 1, 1], detection_name="person")),
        ])
        pred = _ec_build_eval_boxes([
            ("s1", dict(translation=[0.1, 0, 0], size=[1, 1, 1],
                        detection_name="person", detection_score=0.9)),
        ])
        metrics, _ = evaluate_detection(gt, pred, config, verbose=False)

        assert np.isnan(metrics.get_label_tp("person", "attr_err"))
        assert np.isnan(metrics.get_label_tp("person", "vel_err"))
        assert not np.isnan(metrics.get_label_tp("person", "trans_err"))
        assert not np.isnan(metrics.get_label_tp("person", "scale_err"))
        assert not np.isnan(metrics.get_label_tp("person", "orient_err"))

    def test_tp_skip_metrics_custom(self):
        """Custom tp_skip_metrics allows per-class NaN control."""
        from spatialai_data_utils.eval.detection.evaluate import evaluate_detection

        config = _ec_make_config(["person"], "center_distance")
        gt = _ec_build_eval_boxes([
            ("s1", dict(translation=[0, 0, 0], size=[1, 1, 1], detection_name="person")),
        ])
        pred = _ec_build_eval_boxes([
            ("s1", dict(translation=[0.1, 0, 0], size=[1, 1, 1],
                        detection_name="person", detection_score=0.9)),
        ])
        skip = {"person": {"orient_err", "attr_err", "vel_err"}}
        metrics, _ = evaluate_detection(gt, pred, config, verbose=False, tp_skip_metrics=skip)

        assert np.isnan(metrics.get_label_tp("person", "orient_err"))
        assert np.isnan(metrics.get_label_tp("person", "attr_err"))
        assert np.isnan(metrics.get_label_tp("person", "vel_err"))
        assert not np.isnan(metrics.get_label_tp("person", "trans_err"))
        assert not np.isnan(metrics.get_label_tp("person", "scale_err"))

    def test_tp_skip_metrics_empty(self):
        """Empty tp_skip_metrics computes all TP metrics (nothing skipped)."""
        from spatialai_data_utils.eval.detection.evaluate import evaluate_detection

        config = _ec_make_config(["person"], "center_distance")
        gt = _ec_build_eval_boxes([
            ("s1", dict(translation=[0, 0, 0], size=[1, 1, 1], detection_name="person")),
        ])
        pred = _ec_build_eval_boxes([
            ("s1", dict(translation=[0.1, 0, 0], size=[1, 1, 1],
                        detection_name="person", detection_score=0.9)),
        ])
        metrics, _ = evaluate_detection(gt, pred, config, verbose=False, tp_skip_metrics={})

        for metric_name in ["trans_err", "scale_err", "orient_err", "vel_err", "attr_err"]:
            assert not np.isnan(metrics.get_label_tp("person", metric_name))

class TestSaveDetectionResults:
    """Tests for save_detection_results."""

    def test_writes_json_and_csv_files(self, tmp_path):
        """save_detection_results writes JSON files and the legacy metrics CSV."""
        from spatialai_data_utils.eval.detection.evaluate import (
            evaluate_detection,
            save_detection_results,
        )

        config = _ec_make_config(["person"], "center_distance")
        gt = _ec_build_eval_boxes([
            ("s1", dict(translation=[0, 0, 0], size=[1, 1, 1], detection_name="person")),
        ])
        pred = _ec_build_eval_boxes([
            ("s1", dict(translation=[0.1, 0, 0], size=[1, 1, 1],
                        detection_name="person", detection_score=0.9)),
        ])
        metrics, md_list = evaluate_detection(gt, pred, config, verbose=False)

        output_dir = str(tmp_path / "results")
        summary = save_detection_results(
            metrics,
            md_list,
            output_dir,
            meta={"test": True},
            sensor_id="bev-sensor-1",
        )

        summary_path = os.path.join(output_dir, "metrics_summary.json")
        details_path = os.path.join(output_dir, "metrics_details.json")
        csv_path = os.path.join(output_dir, "detection_metrics.csv")
        assert os.path.isfile(summary_path)
        assert os.path.isfile(details_path)
        assert os.path.isfile(csv_path)

        with open(summary_path) as f:
            loaded_summary = json.load(f)
        assert "mean_ap" in loaded_summary
        assert loaded_summary["meta"] == {"test": True}
        assert loaded_summary["mean_ap"] == summary["mean_ap"]

        with open(details_path) as f:
            loaded_details = json.load(f)
        assert len(loaded_details) > 0

        with open(csv_path, newline="") as f:
            rows = list(csv.DictReader(f))
        assert rows == [
            {
                "sensor_id": "bev-sensor-1",
                "object_class": "person",
                "AP": f"{summary['mean_dist_aps']['person']:.3f}",
                "ATE": f"{summary['label_tp_errors']['person']['trans_err']:.3f}",
                "ASE": f"{summary['label_tp_errors']['person']['scale_err']:.3f}",
                "AOE": f"{summary['label_tp_errors']['person']['orient_err']:.3f}",
                "AVE": f"{summary['label_tp_errors']['person']['vel_err']:.3f}",
                "AAE": f"{summary['label_tp_errors']['person']['attr_err']:.3f}",
            }
        ]

    def test_writes_without_meta(self, tmp_path):
        """save_detection_results works without meta parameter."""
        from spatialai_data_utils.eval.detection.evaluate import (
            evaluate_detection,
            save_detection_results,
        )

        config = _ec_make_config(["person"], "center_distance")
        gt = _ec_build_eval_boxes([
            ("s1", dict(translation=[0, 0, 0], size=[1, 1, 1], detection_name="person")),
        ])
        pred = _ec_build_eval_boxes([
            ("s1", dict(translation=[0.1, 0, 0], size=[1, 1, 1],
                        detection_name="person", detection_score=0.9)),
        ])
        metrics, md_list = evaluate_detection(gt, pred, config, verbose=False)

        output_dir = str(tmp_path / "results_no_meta")
        summary = save_detection_results(metrics, md_list, output_dir)

        with open(os.path.join(output_dir, "metrics_summary.json")) as f:
            loaded = json.load(f)
        assert "meta" not in loaded
        assert "mean_ap" in loaded


class TestAIC24DetEval:
    """Tests for the AIC24DetEval wrapper class."""

    def test_init_and_evaluate(self, tmp_path):
        """AIC24DetEval can be instantiated with synthetic data and produce valid metrics."""
        from spatialai_data_utils.eval.detection.evaluate import AIC24DetEval

        class_names = ["person", "forklift"]
        config = _ec_make_config(class_names, "center_distance")

        # Build prediction JSON in nuScenes format
        pred_dict = {
            "s1": [
                {"sample_token": "s1", "translation": [0.1, 0, 0], "size": [1, 1, 1],
                 "rotation": [1, 0, 0, 0], "velocity": [0, 0],
                 "detection_name": "person", "detection_score": 0.95, "attribute_name": ""},
                {"sample_token": "s1", "translation": [5.1, 0, 0], "size": [2, 3, 2],
                 "rotation": [1, 0, 0, 0], "velocity": [0, 0],
                 "detection_name": "forklift", "detection_score": 0.85, "attribute_name": ""},
            ],
            "s2": [
                {"sample_token": "s2", "translation": [0, 0, 0], "size": [1, 1, 1],
                 "rotation": [1, 0, 0, 0], "velocity": [0, 0],
                 "detection_name": "person", "detection_score": 0.90, "attribute_name": ""},
            ],
        }
        result_path = _ec_make_nuscenes_result_json(pred_dict, str(tmp_path))

        # Build GT data_infos matching the prediction tokens
        data_infos = [
            {
                "token": "s1", "scene_name": "scene0", "frame_idx": 0,
                "gt_boxes": np.array([[0, 0, 0, 1, 1, 1, 0], [5, 0, 0, 2, 3, 2, 0]]),
                "gt_names": ["person", "forklift"],
                "gt_velocity": np.array([[0, 0], [0, 0]]),
                "valid_flag": [True, True],
                "instance_inds": [0, 1],
            },
            {
                "token": "s2", "scene_name": "scene0", "frame_idx": 1,
                "gt_boxes": np.array([[0, 0, 0, 1, 1, 1, 0]]),
                "gt_names": ["person"],
                "gt_velocity": np.array([[0, 0]]),
                "valid_flag": [True],
                "instance_inds": [0],
            },
        ]

        output_dir = str(tmp_path / "eval_output")
        evaluator = AIC24DetEval(
            data_infos=data_infos,
            config=config,
            result_path=result_path,
            output_dir=output_dir,
            verbose=False,
        )

        assert evaluator.gt_boxes is not None
        assert evaluator.pred_boxes is not None
        assert set(evaluator.gt_boxes.sample_tokens) == set(evaluator.pred_boxes.sample_tokens)

        metrics, md_list = evaluator.evaluate()
        assert metrics.mean_ap > 0.5
        for cn in class_names:
            assert metrics.mean_dist_aps[cn] > 0.0

    def test_main_saves_results(self, tmp_path):
        """AIC24DetEval.main() runs evaluation and writes result files."""
        from spatialai_data_utils.eval.detection.evaluate import AIC24DetEval

        config = _ec_make_config(["person"], "center_distance")
        pred_dict = {
            "s1": [
                {"sample_token": "s1", "translation": [0.1, 0, 0], "size": [1, 1, 1],
                 "rotation": [1, 0, 0, 0], "velocity": [0, 0],
                 "detection_name": "person", "detection_score": 0.9, "attribute_name": ""},
            ],
        }
        result_path = _ec_make_nuscenes_result_json(pred_dict, str(tmp_path))

        data_infos = [
            {
                "token": "s1", "scene_name": "scene0", "frame_idx": 0,
                "gt_boxes": np.array([[0, 0, 0, 1, 1, 1, 0]]),
                "gt_names": ["person"],
                "gt_velocity": np.array([[0, 0]]),
                "valid_flag": [True],
                "instance_inds": [0],
            },
        ]

        output_dir = str(tmp_path / "main_output")
        evaluator = AIC24DetEval(
            data_infos=data_infos,
            config=config,
            result_path=result_path,
            output_dir=output_dir,
            verbose=False,
        )

        summary = evaluator.main(render_curves=False)

        assert "mean_ap" in summary
        assert "meta" in summary
        assert os.path.isfile(os.path.join(output_dir, "metrics_summary.json"))
        assert os.path.isfile(os.path.join(output_dir, "metrics_details.json"))

    def test_evaluate_detection_per_bev_sensor_forwards_offset_and_fps(
        self, tmp_path, monkeypatch,
    ):
        """``evaluate_detection_per_BEV_sensor`` must thread the GT offset
        through to ``split_files_by_sensor``.

        Pre-fix the orchestrator passed only six positional args to
        ``split_files_by_sensor``, so its
        ``ground_truth_frame_offset_secs`` / ``fps`` kwargs silently
        defaulted to ``0.0`` / ``30.0`` regardless of what the user
        supplied — the splitter then truncated GT to
        ``num_frames_to_eval`` and any non-zero offset window of GT was
        dropped on the floor (silent recall regression).
        """
        from spatialai_data_utils.eval.detection import evaluate as evaluate_mod

        # Real calibration JSON (Style C) — exercises the calibration
        # loaders end-to-end with the same fps the assertion expects, so
        # any future regression in those loaders also gets caught here.
        calib_path = tmp_path / "calib.json"
        calib_path.write_text(json.dumps({
            "sensors": [{
                "id": "Camera_01",
                "group": {"name": "bev-sensor-1"},
                "attributes": [{"name": "fps", "value": "25.0"}],
            }],
        }))

        # The point of *this* test is "did the orchestrator forward the
        # right kwargs to split_files_by_sensor?".  That's an interaction
        # assertion, so we fall back to Style B (patch consumer module)
        # for the splitter and the downstream per-sensor loop.
        captured: dict = {}

        def fake_split(*args, **kwargs):
            captured["args"] = args
            captured["kwargs"] = kwargs

        monkeypatch.setattr(
            evaluate_mod, "_run_detection_per_sensor",
            lambda *args, **kwargs: None,
        )
        monkeypatch.setattr(evaluate_mod, "split_files_by_sensor", fake_split)

        evaluate_mod.evaluate_detection_per_BEV_sensor(
            ground_truth_file=str(tmp_path / "gt.jsonl"),
            prediction_file=str(tmp_path / "pred.jsonl"),
            calibration_file=str(calib_path),
            output_root_dir=str(tmp_path / "out"),
            confidence_threshold=0.5,
            num_frames_to_eval=100,
            ground_truth_frame_offset_secs=2.5,
        )

        assert captured["kwargs"].get("ground_truth_frame_offset_secs") == 2.5, (
            "evaluate_detection_per_BEV_sensor must forward "
            "ground_truth_frame_offset_secs to split_files_by_sensor; "
            "without this kwarg the splitter silently drops the GT "
            "offset window."
        )
        assert captured["kwargs"].get("fps") == 25.0, (
            "evaluate_detection_per_BEV_sensor must forward the "
            "calibration-derived fps to split_files_by_sensor (it's the "
            "denominator in gt_offset_frames = round(offset_secs * fps))."
        )

    def test_evaluate_computes_all_five_tp_metrics(self, tmp_path):
        """``AIC24DetEval.evaluate`` must not skip any TP metric.

        Pre-refactor the inline ``traffic_cone`` / ``barrier`` skip map
        never triggered for AIC24 / MTMC class sets (warehouse classes
        like ``person``, ``forklift``, ``Nova_Carter``,
        ``Transporter``), so every class effectively computed all five
        TP metrics: ``trans_err``, ``scale_err``, ``orient_err``,
        ``vel_err``, ``attr_err``.  After the eval-module reorg, the
        standalone :func:`evaluate_detection` defaults to
        ``{"*": {"attr_err", "vel_err"}}`` which is fine for the BEV /
        MTMC pipeline but would silently zero out ``vel_err`` and
        ``attr_err`` for AIC24 consumers — different numeric output for
        the same input.  This test pins that ``AIC24DetEval`` opts out
        of that default by passing ``tp_skip_metrics={}``.
        """
        from spatialai_data_utils.eval.detection.evaluate import AIC24DetEval

        config = _ec_make_config(["person"], "center_distance")
        pred_dict = {
            "s1": [
                {"sample_token": "s1", "translation": [0.1, 0, 0], "size": [1, 1, 1],
                 "rotation": [1, 0, 0, 0], "velocity": [0, 0],
                 "detection_name": "person", "detection_score": 0.9, "attribute_name": ""},
            ],
        }
        result_path = _ec_make_nuscenes_result_json(pred_dict, str(tmp_path))
        data_infos = [
            {
                "token": "s1", "scene_name": "scene0", "frame_idx": 0,
                "gt_boxes": np.array([[0, 0, 0, 1, 1, 1, 0]]),
                "gt_names": ["person"],
                "gt_velocity": np.array([[0, 0]]),
                "valid_flag": [True],
                "instance_inds": [0],
            },
        ]
        evaluator = AIC24DetEval(
            data_infos=data_infos,
            config=config,
            result_path=result_path,
            output_dir=str(tmp_path / "tp_metric_output"),
            verbose=False,
        )
        metrics, _ = evaluator.evaluate()
        person_tps = metrics.serialize()["label_tp_errors"]["person"]
        for tp_name in ("trans_err", "scale_err", "orient_err",
                        "vel_err", "attr_err"):
            assert not np.isnan(person_tps[tp_name]), (
                f"AIC24DetEval.evaluate must compute {tp_name!r} for AIC24 "
                f"classes; got NaN, suggesting the standalone "
                f"evaluate_detection default skip map leaked back in."
            )
