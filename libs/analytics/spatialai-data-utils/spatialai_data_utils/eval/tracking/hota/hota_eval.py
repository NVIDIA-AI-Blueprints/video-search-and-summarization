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

"""
Per-class HOTA tracking evaluation using the same approach as mtmc_validation_module.

Converts ground truth (data_infos) and predictions (JSON) to per-class text
files, runs HOTA evaluation independently per class using 3D bounding box IoU matching,
and returns per-class and class-averaged HOTA metrics.
"""

import os
import json
import configparser
import logging
import time
from typing import Dict, List, Any, Optional

import numpy as np
from pyquaternion import Quaternion

from nuscenes.eval.common.utils import quaternion_yaw
from spatialai_data_utils.eval.tracking.hota.evaluate import Evaluator as HOTAEvaluator
from spatialai_data_utils.eval.tracking.hota.datasets.mtmc_challenge_3d_bbox import MTMCChallenge3DBBox
from spatialai_data_utils.eval.tracking.hota.datasets.mtmc_challenge_3d_location import MTMCChallenge3DLocation
from spatialai_data_utils.eval.tracking.hota.metrics.hota import HOTA as HOTAMetric

logger = logging.getLogger(__name__)

HOTA_FIELDS = ["HOTA", "DetA", "AssA", "LocA", "DetRe", "DetPr", "AssRe", "AssPr", "OWTA"]


def _run_trackeval_for_class(
    class_name: str,
    class_dir: str,
    tracker_name: str,
    eval_dist_fcn: str,
) -> Optional[Dict[str, float]]:
    """Run TrackEval HOTA for a single class directory that already has gt/tracker data."""
    gt_dir = os.path.join(class_dir, "gt")
    dataset_config = {
        "GT_FOLDER": gt_dir,
        "TRACKERS_FOLDER": os.path.join(class_dir, "trackers"),
        "OUTPUT_FOLDER": os.path.join(class_dir, "output"),
        "TRACKERS_TO_EVAL": [tracker_name],
        "TRACKER_DISPLAY_NAMES": [f"{tracker_name}({class_name})"],
        "CLASSES_TO_EVAL": ["class"],
        "BENCHMARK": "hota_eval",
        "SPLIT_TO_EVAL": "val",
        "PRINT_CONFIG": False,
        "DO_PREPROC": True,
        "TRACKER_SUB_FOLDER": "data",
        "SKIP_SPLIT_FOL": True,
    }
    eval_config = {
        "USE_PARALLEL": False,
        "PRINT_RESULTS": False,
        "PRINT_ONLY_COMBINED": False,
        "PRINT_COMBINED_SEQ_ONLY": True,
        "PRINT_CONFIG": False,
        "TIME_PROGRESS": False,
        "DISPLAY_LESS_PROGRESS": True,
        "OUTPUT_SUMMARY": False,
        "OUTPUT_EMPTY_CLASSES": True,
        "OUTPUT_DETAILED": False,
        "PLOT_CURVES": False,
    }

    # Suppress TrackEval's verbose logging. TrackEval calls logging.info() on the
    # root logger directly, so we attach a temporary filter rather than changing the
    # root logger's level — this avoids altering isEnabledFor() results seen by
    # other threads.  Note: not fully thread-safe (a concurrent thread could emit a
    # root-level INFO record while the filter is attached), but safe enough for the
    # single-threaded USE_PARALLEL=False path used here.
    root_logger = logging.getLogger()
    _suppress = logging.Filter()
    _suppress.filter = lambda record: record.levelno >= logging.WARNING
    root_logger.addFilter(_suppress)
    try:
        evaluator = HOTAEvaluator(eval_config)
        if eval_dist_fcn == "center_distance":
            dataset = MTMCChallenge3DLocation(dataset_config)
        else:
            dataset = MTMCChallenge3DBBox(dataset_config)
        metrics_list = [HOTAMetric()]
        output_res, _ = evaluator.evaluate([dataset], metrics_list)
        ds_name = dataset.get_name()
        hota_data = output_res[ds_name][tracker_name]["COMBINED_SEQ"]["class"]["HOTA"]
        return {field: float(np.mean(hota_data[field])) for field in HOTA_FIELDS}
    finally:
        root_logger.removeFilter(_suppress)


def evaluate_hota(
    data_infos: List[Dict[str, Any]],
    result_path: str,
    output_dir: str,
    class_names: List[str],
    tracker_name: str = "sparse4d",
    verbose: bool = True,
    eval_dist_fcn: str = "iou_3d",
) -> Dict[str, Dict[str, float]]:
    """
    Run per-class HOTA tracking evaluation.

    Same approach as mtmc_validation_module: for each class, writes separate GT/prediction
    text files per scene, runs HOTA evaluation independently, then returns per-class and
    averaged results.

    :param data_infos: List of sample info dicts from the dataset, each containing:
        - "scene_name": str
        - "token": str
        - "gt_boxes": array of shape (N, 7+) with [x, y, z, w, l, h, yaw, ...]
        - "gt_names": list of class name strings
        - "instance_inds": list of track IDs
        - "valid_flag": (optional) list of booleans
    :param result_path: Path to the tracking results JSON file.
    :param output_dir: Directory to write HOTA eval artifacts (text files, results).
    :param class_names: List of class names to evaluate (e.g., ["person", "forklift"]).
    :param tracker_name: Name for the tracker in HOTA output. Defaults to "sparse4d".
    :param verbose: Whether to print progress and results.
    :param eval_dist_fcn: "iou_3d" for 3D bounding box IoU matching,
        "center_distance" for Euclidean center distance matching.
    :return: Dictionary with two levels:
        - "per_class": {class_name: {field: value}} for each class
        - "average": {field: value} averaged across classes
        All values are in [0, 1] scale (not multiplied by 100).
    """
    if eval_dist_fcn not in {"iou_3d", "center_distance"}:
        raise ValueError(
            f"Invalid eval_dist_fcn: {eval_dist_fcn}; expected 'iou_3d' or 'center_distance'"
        )

    hota_dir = os.path.join(output_dir, "hota_eval")
    os.makedirs(hota_dir, exist_ok=True)

    # --- Step 1: Build scene -> frame mapping from data_infos ---
    scene_frames = {}
    for sample in data_infos:
        scene = sample["scene_name"]
        if scene not in scene_frames:
            scene_frames[scene] = []
        scene_frames[scene].append(sample)

    scene_token_to_frame_id = {}
    scene_lengths = {}
    for scene, frames in scene_frames.items():
        scene_lengths[scene] = len(frames)
        for idx, frame in enumerate(frames):
            scene_token_to_frame_id[frame["token"]] = (scene, idx + 1)

    scenes_str = ", ".join(sorted(scene_frames.keys()))
    if verbose:
        logger.info(f"\n{'=' * 90}")
        logger.info(f"  HOTA Tracking Evaluation  (dist_fcn={eval_dist_fcn})")
        logger.info(f"  Scenes: {scenes_str}")
        logger.info(f"{'=' * 90}")

    # --- Step 2: Load predictions ---
    with open(result_path, "r") as f:
        pred_data = json.load(f)
    pred_results = pred_data["results"]

    # --- Step 3: Build per-class, per-scene GT and prediction lines ---
    gt_by_class = {cls: {scene: [] for scene in scene_frames} for cls in class_names}
    for scene in scene_frames:
        for frame_idx, sample in enumerate(scene_frames[scene]):
            frame_id = frame_idx + 1
            gt_boxes = sample["gt_boxes"]
            gt_names = sample["gt_names"]
            instance_inds = sample["instance_inds"]
            valid_flag = sample.get("valid_flag", [True] * len(gt_boxes))
            for anno_id in range(len(gt_boxes)):
                if not valid_flag[anno_id]:
                    continue
                name = gt_names[anno_id]
                if name is None or name not in class_names:
                    continue
                box = gt_boxes[anno_id]
                track_id = instance_inds[anno_id]
                x, y, z = box[0], box[1], box[2]
                w, l, h = box[3], box[4], box[5]
                yaw = -box[6]  # negate to match the convention used in loaders.py
                line = f"{frame_id},{track_id},-1,{x},{y},{z},{w},{l},{h},0,0,{yaw}"
                gt_by_class[name][scene].append(line)

    pred_by_class = {cls: {scene: [] for scene in scene_frames} for cls in class_names}
    for token, annos in pred_results.items():
        if token not in scene_token_to_frame_id:
            continue
        scene, frame_id = scene_token_to_frame_id[token]
        for anno in annos:
            track_name = anno.get("tracking_name", anno.get("detection_name", ""))
            if track_name not in class_names:
                continue
            tx, ty, tz = anno["translation"]
            sw, sl, sh = anno["size"]
            q = anno["rotation"]
            yaw = quaternion_yaw(Quaternion(q))
            track_id = anno.get("tracking_id", "0")
            line = f"{frame_id},{track_id},-1,{tx},{ty},{tz},{sw},{sl},{sh},0,0,{yaw}"
            pred_by_class[track_name][scene].append(line)

    # --- Step 4: Write data files and run HOTA evaluation per class ---
    class_results = {}
    no_gt_classes = set()

    for class_name in class_names:
        class_dir = os.path.join(hota_dir, class_name)
        gt_dir = os.path.join(class_dir, "gt")
        tracker_dir = os.path.join(class_dir, "trackers", tracker_name, "data")
        os.makedirs(tracker_dir, exist_ok=True)

        has_gt = any(len(lines) > 0 for lines in gt_by_class[class_name].values())
        if not has_gt:
            no_gt_classes.add(class_name)
            class_results[class_name] = None
            if verbose:
                logger.info(f"  {class_name:<20s}  skipped (no GT)")
            continue

        for scene in scene_frames:
            scene_gt_dir = os.path.join(gt_dir, scene, "gt")
            os.makedirs(scene_gt_dir, exist_ok=True)

            with open(os.path.join(scene_gt_dir, "gt.txt"), "w") as f:
                f.write("\n".join(gt_by_class[class_name][scene]))

            ini_path = os.path.join(gt_dir, scene, "seqinfo.ini")
            config = configparser.ConfigParser()
            config["Sequence"] = {"seqLength": str(scene_lengths[scene])}
            with open(ini_path, "w") as f:
                config.write(f)

            pred_file = os.path.join(tracker_dir, f"{scene}.txt")
            with open(pred_file, "w") as f:
                f.write("\n".join(pred_by_class[class_name][scene]))

        seqmap_dir = os.path.join(gt_dir, "seqmaps")
        os.makedirs(seqmap_dir, exist_ok=True)
        seqmap_path = os.path.join(seqmap_dir, "hota_eval-val.txt")
        with open(seqmap_path, "w") as f:
            f.write("name\n")
            for scene in scene_frames:
                f.write(f"{scene}\n")

        t0 = time.time()
        try:
            class_results[class_name] = _run_trackeval_for_class(
                class_name, class_dir, tracker_name, eval_dist_fcn,
            )
            elapsed = time.time() - t0
            if verbose:
                logger.info(f"  {class_name:<20s}  done  ({elapsed:.1f}s)")
        except Exception as e:
            elapsed = time.time() - t0
            logger.warning(f"HOTA evaluation failed for class '{class_name}': {e}", exc_info=True)
            class_results[class_name] = None
            if verbose:
                logger.info(f"  {class_name:<20s}  FAILED ({elapsed:.1f}s): {e}")

    # --- Step 5: Compute class-averaged metrics and print summary table ---
    valid_results = {name: v for name, v in class_results.items() if v is not None}

    results = {"per_class": class_results, "average": {}}

    if len(valid_results) > 0:
        for field in HOTA_FIELDS:
            values = [metrics[field] for metrics in valid_results.values()]
            results["average"][field] = float(np.mean(values))

    if verbose:
        _print_summary_table(results, class_names, no_gt_classes)

    # --- Step 6: Save results to JSON ---
    summary_path = os.path.join(output_dir, "hota_metrics_summary.json")
    summary = {
        "eval_dist_fcn": eval_dist_fcn,
        "scenes": sorted(scene_frames.keys()),
        "class_names": class_names,
        "per_class": {},
        "average": {},
    }
    for cls_name in class_names:
        cls_metrics = class_results.get(cls_name)
        if cls_metrics is not None:
            summary["per_class"][cls_name] = {
                f: round(cls_metrics[f] * 100, 4) for f in HOTA_FIELDS
            }
        else:
            summary["per_class"][cls_name] = None
    for f in HOTA_FIELDS:
        if f in results["average"]:
            summary["average"][f] = round(results["average"][f] * 100, 4)
    with open(summary_path, "w") as fp:
        json.dump(summary, fp, indent=2)
    if verbose:
        logger.info(f"  HOTA metrics saved to: {summary_path}")

    return results


def _print_summary_table(
    results: Dict,
    class_names: List[str],
    no_gt_classes: set,
) -> None:
    """Log a formatted HOTA summary table."""
    header = f"  {'Class':<20s}" + "".join(f"{f:>9s}" for f in HOTA_FIELDS)
    sep = "  " + "-" * (len(header) - 2)
    logger.info(sep)
    logger.info(header)
    logger.info(sep)
    for class_name in class_names:
        metrics = results["per_class"].get(class_name)
        if metrics is not None:
            vals = "".join(f"{metrics[f]*100:9.2f}" for f in HOTA_FIELDS)
            logger.info(f"  {class_name:<20s}{vals}")
        else:
            reason = "no GT" if class_name in no_gt_classes else "FAILED"
            logger.info(f"  {class_name:<20s}{'--':>9s}  ({reason})")
    logger.info(sep)
    if results["average"]:
        avg_vals = "".join(f"{results['average'][f]*100:9.2f}" for f in HOTA_FIELDS)
        logger.info(f"  {'AVERAGE':<20s}{avg_vals}")
    else:
        logger.info(f"  {'AVERAGE':<20s}  N/A (no valid classes)")
    logger.info("")
