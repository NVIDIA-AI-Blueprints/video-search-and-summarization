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

# Copyright 2021 Motional
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# Adapted from nuScenes dev-kit.
# Original code by Holger Caesar & Oscar Beijbom, 2018.

"""
Unified detection evaluation module.

Provides pure-function evaluation (no I/O mixed with metric computation),
result saving, rendering, printing, and per-BEV-sensor orchestration.

Data loading is handled by separate loader functions:
- ``nuscenes.eval.common.loaders.load_prediction`` for nuScenes-format result JSON
- ``spatialai_data_utils.eval.common.loaders.load_gt`` for in-memory data_infos
- ``spatialai_data_utils.eval.detection.loaders`` for MTMC JSONL files
"""

import json
import logging
import os
import time
from typing import Any, Dict, Optional, Set, Tuple, Union

import numpy as np
import pandas as pd

from nuscenes.eval.common.data_classes import EvalBoxes
from nuscenes.eval.detection.algo import accumulate, calc_ap, calc_tp
from nuscenes.eval.detection.constants import TP_METRICS
from nuscenes.eval.detection.render import (
    class_pr_curve,
    class_tp_curve,
    dist_pr_curve,
    summary_plot,
)

from nuscenes.eval.common.loaders import load_prediction

from spatialai_data_utils.configs.eval.detection import DET_CONFIG_IOU3D
from spatialai_data_utils.eval.common.loaders import load_gt
from spatialai_data_utils.eval.common.preprocessing import split_files_by_sensor
from spatialai_data_utils.eval.detection.data_classes import (
    DetectionBox,
    DetectionConfig,
    DetectionMetricDataList,
    DetectionMetrics,
)
from spatialai_data_utils.eval.detection.loaders import load_boxes_from_jsonl
from spatialai_data_utils.loaders.calibration import (
    fetch_fps_from_calibration,
    get_camera_name_to_bev_name_map,
)


def evaluate_detection(
    gt_boxes: EvalBoxes,
    pred_boxes: EvalBoxes,
    config: DetectionConfig,
    verbose: bool = True,
    tp_skip_metrics: Optional[Dict[str, Set[str]]] = None,
) -> Tuple[DetectionMetrics, DetectionMetricDataList]:
    """
    Pure detection evaluation: computes mAP and TP metrics from pre-loaded boxes.

    :param gt_boxes: Ground-truth boxes grouped by sample token.
    :param pred_boxes: Predicted boxes grouped by sample token.
    :param config: Evaluation configuration (class names, distance thresholds, etc.).
    :param verbose: Whether to print progress messages.
    :param tp_skip_metrics: Optional mapping from class name (or ``"*"`` for all
        classes) to a set of TP metric names that should be set to NaN instead of
        computed.  Defaults to ``{"*": {"attr_err", "vel_err"}}`` (skip attribute
        and velocity errors for every class).
    :return: ``(metrics, metric_data_list)``
    """
    if tp_skip_metrics is None:
        tp_skip_metrics = {"*": {"attr_err", "vel_err"}}

    start_time = time.time()

    if verbose:
        logging.info("Accumulating metric data...")
    metric_data_list = DetectionMetricDataList()
    for class_name in config.class_names:
        for dist_th in config.dist_ths:
            md = accumulate(
                gt_boxes,
                pred_boxes,
                class_name,
                config.dist_fcn_callable,
                dist_th,
            )
            metric_data_list.set(class_name, dist_th, md)

    if verbose:
        logging.info("Calculating metrics...")
    metrics = DetectionMetrics(config)
    for class_name in config.class_names:
        for dist_th in config.dist_ths:
            metric_data = metric_data_list[(class_name, dist_th)]
            ap = calc_ap(metric_data, config.min_recall, config.min_precision)
            metrics.add_label_ap(class_name, dist_th, ap)

        global_skip = tp_skip_metrics.get("*", set())
        class_skip = tp_skip_metrics.get(class_name, set())
        skip = global_skip | class_skip

        for metric_name in TP_METRICS:
            metric_data = metric_data_list[(class_name, config.dist_th_tp)]
            if metric_name in skip:
                tp = np.nan
            else:
                tp = calc_tp(metric_data, config.min_recall, metric_name)
            metrics.add_label_tp(class_name, metric_name, tp)

    metrics.add_runtime(time.time() - start_time)
    return metrics, metric_data_list


def save_detection_results(
    metrics: DetectionMetrics,
    md_list: DetectionMetricDataList,
    output_dir: str,
    meta: Optional[Dict[str, Any]] = None,
    sensor_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Serialize and write detection metrics to JSON files.

    :param metrics: High-level evaluation results.
    :param md_list: Raw accumulated metric data.
    :param output_dir: Directory to write ``metrics_summary.json`` and
        ``metrics_details.json``.
    :param meta: Optional metadata dict to include in the summary.
    :param sensor_id: Optional sensor id. When provided, also write the
        legacy ``detection_metrics.csv`` consumed by the S3 aggregation step.
    :return: The metrics summary dict.
    """
    os.makedirs(output_dir, exist_ok=True)
    metrics_summary = metrics.serialize()
    if meta is not None:
        metrics_summary["meta"] = meta.copy()
    with open(os.path.join(output_dir, "metrics_summary.json"), "w") as f:
        json.dump(metrics_summary, f, indent=2)
    with open(os.path.join(output_dir, "metrics_details.json"), "w") as f:
        json.dump(md_list.serialize(), f, indent=2)
    if sensor_id is not None:
        _write_detection_metrics_csv(metrics_summary, output_dir, sensor_id)
    return metrics_summary


def _write_detection_metrics_csv(
    metrics_summary: Dict[str, Any],
    output_dir: str,
    sensor_id: str,
) -> None:
    """Write the legacy per-class detection metrics CSV for one sensor."""
    class_aps = metrics_summary["mean_dist_aps"]
    class_tps = metrics_summary["label_tp_errors"]

    data = []
    for class_name in class_aps.keys():
        data.append([
            sensor_id,
            class_name,
            f"{class_aps[class_name]:.3f}",
            f"{class_tps[class_name]['trans_err']:.3f}",
            f"{class_tps[class_name]['scale_err']:.3f}",
            f"{class_tps[class_name]['orient_err']:.3f}",
            f"{class_tps[class_name]['vel_err']:.3f}",
            f"{class_tps[class_name]['attr_err']:.3f}",
        ])

    df = pd.DataFrame(
        data,
        columns=["sensor_id", "object_class", "AP", "ATE", "ASE", "AOE", "AVE", "AAE"],
    )
    print(df)
    df.to_csv(os.path.join(output_dir, "detection_metrics.csv"), index=False)


def render_detection_results(
    metrics: DetectionMetrics,
    md_list: DetectionMetricDataList,
    config: DetectionConfig,
    plot_dir: str,
) -> None:
    """Render PR and TP curves to *plot_dir*."""
    os.makedirs(plot_dir, exist_ok=True)

    def savepath(name):
        return os.path.join(plot_dir, name + ".pdf")

    summary_plot(
        md_list,
        metrics,
        min_precision=config.min_precision,
        min_recall=config.min_recall,
        dist_th_tp=config.dist_th_tp,
        savepath=savepath("summary"),
    )
    for detection_name in config.class_names:
        class_pr_curve(
            md_list,
            metrics,
            detection_name,
            config.min_precision,
            config.min_recall,
            savepath=savepath(detection_name + "_pr"),
        )
        class_tp_curve(
            md_list,
            metrics,
            detection_name,
            config.min_recall,
            config.dist_th_tp,
            savepath=savepath(detection_name + "_tp"),
        )
    for dist_th in config.dist_ths:
        dist_pr_curve(
            md_list,
            metrics,
            dist_th,
            config.min_precision,
            config.min_recall,
            savepath=savepath("dist_pr_" + str(dist_th)),
        )


def print_detection_summary(metrics_summary: Dict[str, Any]) -> None:
    """Print a human-readable detection metrics summary to stdout."""
    print("mAP: %.4f" % metrics_summary["mean_ap"])
    err_name_mapping = {
        "trans_err": "mATE",
        "scale_err": "mASE",
        "orient_err": "mAOE",
        "vel_err": "mAVE",
        "attr_err": "mAAE",
    }
    for tp_name, tp_val in metrics_summary["tp_errors"].items():
        print("%s: %.4f" % (err_name_mapping[tp_name], tp_val))
    print("NDS: %.4f" % metrics_summary["nd_score"])
    if "eval_time" in metrics_summary:
        print("Eval time: %.1fs" % metrics_summary["eval_time"])

    print()
    print("Per-class results:")
    print("Object Class\tAP\tATE\tASE\tAOE\tAVE\tAAE")
    class_aps = metrics_summary["mean_dist_aps"]
    class_tps = metrics_summary["label_tp_errors"]
    for class_name in class_aps.keys():
        print(
            "%s\t%.3f\t%.3f\t%.3f\t%.3f\t%.3f\t%.3f"
            % (
                class_name,
                class_aps[class_name],
                class_tps[class_name]["trans_err"],
                class_tps[class_name]["scale_err"],
                class_tps[class_name]["orient_err"],
                class_tps[class_name]["vel_err"],
                class_tps[class_name]["attr_err"],
            )
        )


# ---------------------------------------------------------------------------
# AIC24DetEval: class-based wrapper for external consumers
# ---------------------------------------------------------------------------


class AIC24DetEval:
    """
    AICity Challenge 2024 detection evaluation class.

    Thin wrapper around the standalone functions in this module that preserves
    the original class-based API for external consumers.

    :param data_infos: List of sample dicts containing ground truth information.
    :param config: A DetectionConfig object specifying evaluation parameters.
    :param result_path: Path to the prediction results JSON file.
    :param output_dir: Folder path to save plots and metric results.
    :param verbose: Whether to print status messages to stdout.
    """

    def __init__(
        self,
        data_infos,
        config: DetectionConfig,
        result_path: str,
        output_dir: Optional[str] = None,
        verbose: bool = True,
    ):
        self.data_infos = data_infos
        self.result_path = result_path
        self.output_dir = output_dir
        self.verbose = verbose
        self.cfg = config

        assert os.path.exists(result_path), "Error: The result file does not exist!"

        self.plot_dir = os.path.join(self.output_dir, "plots")
        os.makedirs(self.output_dir, exist_ok=True)
        os.makedirs(self.plot_dir, exist_ok=True)

        if verbose:
            logging.info("Initializing detection evaluation")
        self.pred_boxes, self.meta = load_prediction(
            self.result_path,
            self.cfg.max_boxes_per_sample,
            DetectionBox,
            verbose=verbose,
        )
        self.gt_boxes = load_gt(self.data_infos, DetectionBox, verbose=verbose)

        assert set(self.pred_boxes.sample_tokens) == set(self.gt_boxes.sample_tokens), (
            "Samples in split doesn't match samples in predictions."
        )
        self.sample_tokens = self.gt_boxes.sample_tokens

    def evaluate(self) -> Tuple[DetectionMetrics, DetectionMetricDataList]:
        """Run the detection evaluation.

        Computes all five TP metrics (``trans_err``, ``scale_err``,
        ``orient_err``, ``vel_err``, ``attr_err``) for every class — this
        is what the pre-refactor ``AIC24DetEval.evaluate`` produced in
        practice.  The original branched on ``traffic_cone`` /
        ``barrier`` to skip some TP metrics, but those classes are
        nuScenes-only and don't appear in any AIC24 / MTMC class set
        (warehouse classes such as ``Person``, ``Forklift``,
        ``Nova_Carter``, ``Transporter``), so the skip map never
        triggered.  Pass ``tp_skip_metrics={}`` to make that behaviour
        explicit and override the standalone
        :func:`evaluate_detection` default
        ``{"*": {"attr_err", "vel_err"}}`` (which targets the BEV /
        MTMC pipeline, not the AIC24 detection wrapper).
        """
        return evaluate_detection(
            self.gt_boxes, self.pred_boxes, self.cfg, verbose=self.verbose,
            tp_skip_metrics={},
        )

    def render(self, metrics: DetectionMetrics, md_list: DetectionMetricDataList) -> None:
        """Render PR and TP curves."""
        render_detection_results(metrics, md_list, self.cfg, self.plot_dir)

    def main(self, render_curves: bool = True) -> Dict[str, Any]:
        """Execute the full evaluation pipeline: evaluate, render, and save results."""
        metrics, metric_data_list = self.evaluate()

        if render_curves:
            self.render(metrics, metric_data_list)

        if self.verbose:
            logging.info("Saving metrics to: %s" % self.output_dir)

        summary = save_detection_results(
            metrics, metric_data_list, self.output_dir, meta=self.meta,
        )
        print_detection_summary(summary)
        return summary


# ---------------------------------------------------------------------------
# Per-BEV-sensor orchestration (moved from eval/mtmc/detection/evaluate.py)
# ---------------------------------------------------------------------------


def evaluate_detection_per_BEV_sensor(
    ground_truth_file: str,
    prediction_file: str,
    calibration_file: str,
    output_root_dir: str,
    confidence_threshold: float = 0.0,
    num_frames_to_eval: int = 200000,
    ground_truth_frame_offset_secs: float = 0.0,
    config: Optional[Union[DetectionConfig, Dict[str, Any]]] = None,
) -> None:
    """
    Evaluate detection per BEV sensor.

    Splits GT/pred files by BEV sensor, then runs detection evaluation
    independently for each sensor.

    :param ground_truth_file: Path to the ground truth JSONL file.
    :param prediction_file: Path to the prediction JSONL file.
    :param calibration_file: Path to the calibration JSON file.
    :param output_root_dir: Root directory for output files.
    :param confidence_threshold: Minimum confidence to keep predictions.
    :param num_frames_to_eval: Maximum number of frames to evaluate.
    :param ground_truth_frame_offset_secs: GT time offset in seconds.
    :param config: Detection-evaluation preset, either an already-built
        :class:`DetectionConfig` or a plain ``dict`` (as exported from
        :mod:`spatialai_data_utils.configs.eval.detection`). When omitted
        defaults to
        :data:`spatialai_data_utils.configs.eval.detection.DET_CONFIG_IOU3D`
        (3D-IoU matching), which matches the legacy ``DetConfigs`` default.
        Pass
        :data:`spatialai_data_utils.configs.eval.detection.DET_CONFIG_CENTER_DISTANCE`
        for the MTMC validation+evaluation centre-distance protocol.
        Any other type raises :class:`TypeError` at call time.
    """
    logging.info("Computing detection results...")

    output_directory = os.path.join(output_root_dir, "detection_results")
    os.makedirs(output_directory, exist_ok=True)

    fps = fetch_fps_from_calibration(calibration_file)
    logging.info(f"Fetched FPS {fps} from calibration file: {calibration_file}.")

    if config is None:
        config = DET_CONFIG_IOU3D
    if isinstance(config, dict):
        config = DetectionConfig.deserialize(config)
    if not isinstance(config, DetectionConfig):
        raise TypeError(
            "evaluate_detection_per_BEV_sensor: `config` must be a "
            "DetectionConfig instance or a dict accepted by "
            "DetectionConfig.deserialize (e.g. "
            "spatialai_data_utils.configs.eval.detection.DET_CONFIG_IOU3D / "
            "DET_CONFIG_CENTER_DISTANCE); got "
            f"{type(config).__name__}."
        )

    map_camera_name_to_bev_name = get_camera_name_to_bev_name_map(calibration_file)
    split_files_by_sensor(
        ground_truth_file,
        prediction_file,
        output_directory,
        map_camera_name_to_bev_name,
        confidence_threshold,
        num_frames_to_eval,
        # Forward the offset window so split_files_by_sensor's internal
        # gt_offset_frames = round(ground_truth_frame_offset_secs * fps)
        # is honoured; without these two args the splitter silently used
        # its 0.0/30.0 defaults and truncated GT to num_frames_to_eval,
        # degrading recall whenever the user passed a non-zero offset.
        ground_truth_frame_offset_secs=ground_truth_frame_offset_secs,
        fps=fps,
    )

    _run_detection_per_sensor(
        output_directory,
        config,
        fps,
        confidence_threshold,
        ground_truth_frame_offset_secs,
    )


def _run_detection_per_sensor(
    base_dir: str,
    config: DetectionConfig,
    fps: float,
    confidence_threshold: float,
    ground_truth_frame_offset_secs: float,
) -> None:
    """Run detection evaluation for each sensor sub-directory in *base_dir*."""
    for sensor_name in sorted(os.listdir(base_dir)):
        sensor_dir = os.path.join(base_dir, sensor_name)
        if not os.path.isdir(sensor_dir):
            continue

        gt_path = os.path.join(sensor_dir, "gt.json")
        pred_path = os.path.join(sensor_dir, "pred.json")
        output_dir = os.path.join(sensor_dir, "output")

        if not os.path.exists(gt_path) or not os.path.exists(pred_path):
            if not os.path.exists(gt_path):
                logging.info(f"Ground truth data not found for sensor: {sensor_name}")
            if not os.path.exists(pred_path):
                logging.info(f"Prediction data not found for sensor {sensor_name}")
            continue

        print("--------------------------------------------------------------")
        logging.info(f"Evaluating: {sensor_name}...")

        gt_boxes, pred_boxes = load_boxes_from_jsonl(
            gt_path, pred_path, fps, confidence_threshold, ground_truth_frame_offset_secs,
        )
        metrics, md_list = evaluate_detection(gt_boxes, pred_boxes, config, verbose=True)
        summary = save_detection_results(metrics, md_list, output_dir, sensor_id=sensor_name)

        logging.info(
            f"Detection mAP for BEV sensor {sensor_name} is {summary['mean_ap']:.3f}"
        )
        logging.info(f"Results saved for BEV sensor: {sensor_name} at {output_dir}")

    print("--------------------------------------------------------------")


def evaluate_detection_all_BEV_sensors(
    ground_truth_file: str,
    prediction_file: str,
    output_dir: str,
    config: DetectionConfig,
    fps: float,
    confidence_threshold: float,
    ground_truth_frame_offset_secs: float,
) -> None:
    """Evaluate detection for all BEV sensors combined (no per-sensor split)."""
    logging.info("Evaluating all BEV sensors...")
    gt_boxes, pred_boxes = load_boxes_from_jsonl(
        ground_truth_file, prediction_file, fps,
        confidence_threshold, ground_truth_frame_offset_secs,
    )
    metrics, md_list = evaluate_detection(gt_boxes, pred_boxes, config, verbose=True)
    summary = save_detection_results(metrics, md_list, output_dir, sensor_id="Combined BEV")

    logging.info(
        f"Detection mAP for Combined BEV is {summary['mean_ap']:.3f}"
    )
    logging.info(f"Results saved for all combined BEV groups at {output_dir}")
