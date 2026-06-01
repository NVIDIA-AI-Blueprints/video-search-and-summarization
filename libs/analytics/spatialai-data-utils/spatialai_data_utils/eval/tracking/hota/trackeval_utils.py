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
TrackEval orchestration utilities.

Provides functions to convert GT/prediction JSONL files into MOT-format text,
set up TrackEval folder structures, and run per-BEV-sensor or combined
tracking evaluation using HOTA/CLEAR/Identity metrics.
"""

import json
import logging
import os
from datetime import datetime
from typing import Any, Dict, List, Tuple

import spatialai_data_utils.eval.tracking.hota as trackeval
from spatialai_data_utils.eval.common.classes import CLASS_LIST
from spatialai_data_utils.eval.common.preprocessing import (
    split_files_per_class,
    split_files_per_sensor_and_class,
)
from spatialai_data_utils.utils.filesystem_utils import make_dir
from spatialai_data_utils.loaders.calibration import fetch_fps_from_calibration


def prepare_ground_truth_file(
    input_file_path: str,
    output_file_path: str,
    fps: int,
    ground_truth_frame_offset_secs: float,
) -> None:
    """
    Convert a ground truth JSONL file into MOT text format for evaluation.

    :param input_file_path: Path to the input ground truth JSONL file.
    :param output_file_path: Path where the output MOT file will be saved.
    :param fps: Frame rate of the videos.
    :param ground_truth_frame_offset_secs: Temporal offset in seconds applied to GT frames.
    """
    map_frame_id_to_objects: Dict[int, Dict[int, List[float]]] = {}
    map_large_object_id_to_small_object_ids: Dict[str, int] = {}
    next_object_id = 1
    offset_frames = round(ground_truth_frame_offset_secs * fps)

    with open(input_file_path) as f:
        for line_number, line in enumerate(f):
            if '"' not in line and "'" in line:
                line = line.replace("'", '"')

            data = json.loads(line)
            data["sensorId"] = data["sensorId"].split("/")[-1]

            current_timestamp = datetime.strptime(
                data["timestamp"], "%Y-%m-%dT%H:%M:%S.%fZ"
            )
            if line_number == 0:
                base_timestamp = current_timestamp
                raw_frame_id = 1
            else:
                raw_frame_id = (
                    round((current_timestamp - base_timestamp).total_seconds() * fps) + 1
                )

            if raw_frame_id <= offset_frames:
                continue

            frame_id = raw_frame_id - offset_frames

            for obj_data in data["objects"]:
                if frame_id not in map_frame_id_to_objects:
                    map_frame_id_to_objects[frame_id] = {}

                object_id_str = obj_data["id"]
                if object_id_str not in map_large_object_id_to_small_object_ids:
                    map_large_object_id_to_small_object_ids[object_id_str] = next_object_id
                    next_object_id += 1
                object_id = map_large_object_id_to_small_object_ids[object_id_str]

                if object_id not in map_frame_id_to_objects[frame_id]:
                    map_frame_id_to_objects[frame_id][object_id] = []
                bbox3d = obj_data["bbox3d"]["coordinates"]
                map_frame_id_to_objects[frame_id][object_id].append(bbox3d)

    with open(output_file_path, "w") as output_file:
        for frame_id in sorted(map_frame_id_to_objects.keys()):
            for object_id in sorted(map_frame_id_to_objects[frame_id].keys()):
                x, y, z, width, length, height, pitch, roll, yaw = (
                    map_frame_id_to_objects[frame_id][object_id][0]
                )
                output_file.write(
                    f"{frame_id} {object_id} 1 "
                    f"{x:.5f} {y:.5f} {z:.5f} "
                    f"{width:.5f} {length:.5f} {height:.5f} "
                    f"{pitch:.5f} {roll:.5f} {yaw:.5f}\n"
                )


def prepare_prediction_file(
    input_file_path: str,
    output_file_path: str,
    fps: float,
    rtls_delay_sec: float,
) -> None:
    """
    Convert a prediction JSONL file into MOT text format for evaluation.

    :param input_file_path: Path to the input prediction JSONL file.
    :param output_file_path: Path where the output MOT file will be saved.
    :param fps: Frame rate of the videos in frames per second.
    :param rtls_delay_sec: Total RTLS delay in seconds, typically the sum of
        ``rtlsLocationWindowSec + rtlsSmoothingWindowSec`` from the MTMC app
        config.  The reported timestamp on each prediction sits at the end
        of those windows; the position itself is the smoothed/aggregated
        value over the window, which best lines up with the **midpoint** of
        the window.  The frame ID is therefore shifted back by half that
        delay (see ``frame_id`` calculation below) to align predictions
        with their effective ground-truth frames.  Pass ``0`` to disable
        the shift.
    """
    map_frame_id_to_objects: Dict[int, Dict[int, List[float]]] = {}
    map_large_object_id_to_small_object_ids: Dict[str, int] = {}
    next_object_id = 1

    with open(input_file_path) as f:
        for line_number, line in enumerate(f):
            line = line.rstrip()
            if line.startswith("b'"):
                line = line[2:-1]
            if '"' not in line and "'" in line:
                line = line.replace("'", '"')

            line_data = json.loads(line)

            # Parse the timestamp BEFORE the empty-objects skip so
            # ``base_timestamp`` is always set on line 0 even when
            # line 0 has no detections.  Skipping with a ``continue``
            # before this block used to leave ``base_timestamp``
            # unbound, so the next non-empty line tripped
            # ``UnboundLocalError`` (mirroring the layout already
            # used by :func:`prepare_ground_truth_file`).
            current_timestamp = datetime.strptime(
                line_data["timestamp"], "%Y-%m-%dT%H:%M:%S.%fZ"
            )
            if line_number == 0:
                base_timestamp = current_timestamp
                raw_frame_id = 1
            else:
                raw_frame_id = (
                    round((current_timestamp - base_timestamp).total_seconds() * fps) + 1
                )

            if len(line_data["objects"]) == 0:
                continue

            # Half of ``rtls_delay_sec`` (in seconds) shifts the frame ID
            # back to the midpoint of the RTLS smoothing/location window
            # that produced this prediction.  See the parameter docstring
            # above for the full rationale.  Units: ``rtls_delay_sec`` in
            # seconds, ``fps`` in frames/sec, ``raw_frame_id`` in frames.
            frame_id = int(raw_frame_id - ((rtls_delay_sec / 2) * fps))
            if frame_id <= 0:
                continue

            for object_info in line_data["objects"]:
                if len(object_info["bbox3d"]["coordinates"]) == 0:
                    continue

                bbox3d = object_info["bbox3d"]["coordinates"]

                if frame_id not in map_frame_id_to_objects:
                    map_frame_id_to_objects[frame_id] = {}

                object_id_str = object_info["id"]
                if object_id_str not in map_large_object_id_to_small_object_ids:
                    map_large_object_id_to_small_object_ids[object_id_str] = next_object_id
                    next_object_id += 1
                object_id = map_large_object_id_to_small_object_ids[object_id_str]

                if object_id not in map_frame_id_to_objects[frame_id]:
                    map_frame_id_to_objects[frame_id][object_id] = []
                map_frame_id_to_objects[frame_id][object_id].append(bbox3d)

    with open(output_file_path, "w") as output_file:
        for frame_id in sorted(map_frame_id_to_objects.keys()):
            for object_id in sorted(map_frame_id_to_objects[frame_id].keys()):
                coords = map_frame_id_to_objects[frame_id][object_id][0]
                if len(coords) == 9:
                    x, y, z, width, length, height, pitch, roll, yaw = coords
                elif len(coords) == 12:
                    x, y, z, width, length, height, pitch, roll, yaw = coords[:9]
                else:
                    logging.error("Incorrect number of elements in bbox3d coordinates.")
                    raise ValueError(f"Expected 9 or 12 coordinates, got {len(coords)}")
                output_file.write(
                    f"{frame_id} {object_id} 1 "
                    f"{x:.5f} {y:.5f} {z:.5f} "
                    f"{width:.5f} {length:.5f} {height:.5f} "
                    f"{pitch:.5f} {roll:.5f} {yaw:.5f}\n"
                )


def make_seq_maps_file(
    seq_maps_dir_path: str,
    sensor_ids: List[str],
    benchmark: str,
    split_to_eval: str,
) -> None:
    """Create a sequence-maps file used by the TrackEval library."""
    make_dir(seq_maps_dir_path)
    seq_maps_file_name = benchmark + "-" + split_to_eval + ".txt"
    seq_maps_file_path = os.path.join(seq_maps_dir_path, seq_maps_file_name)
    with open(seq_maps_file_path, "w") as f:
        f.write("name\n")
        for sensor_id in sensor_ids:
            f.write(sensor_id + "\n")


def setup_evaluation_configs(
    results_dir_path: str, eval_type: str, num_cores: int
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """
    Set up evaluation configurations for TrackEval.

    :param results_dir_path: Path to the folder that stores the results.
    :param eval_type: Type of evaluation (``"bbox"`` or ``"location"``).
    :param num_cores: Number of parallel cores for evaluation.
    :return: ``(dataset_config, eval_config)``
    """
    eval_config = trackeval.evaluate.Evaluator.get_default_eval_config()
    eval_config["PRINT_CONFIG"] = False
    eval_config["USE_PARALLEL"] = True
    eval_config["NUM_PARALLEL_CORES"] = num_cores

    if eval_type == "bbox":
        dataset_config = trackeval.datasets.MTMCChallenge3DBBox.get_default_dataset_config()
    elif eval_type == "location":
        dataset_config = trackeval.datasets.MTMCChallenge3DLocation.get_default_dataset_config()
    else:
        raise ValueError(f"Unknown eval_type: {eval_type}")

    dataset_config["DO_PREPROC"] = False
    dataset_config["SPLIT_TO_EVAL"] = "all"
    evaluation_dir_path = os.path.join(results_dir_path, "evaluation")
    make_dir(evaluation_dir_path)
    dataset_config["GT_FOLDER"] = os.path.join(evaluation_dir_path, "gt")
    dataset_config["TRACKERS_FOLDER"] = os.path.join(evaluation_dir_path, "scores")
    dataset_config["PRINT_CONFIG"] = False

    return dataset_config, eval_config


def make_seq_ini_file(
    gt_dir: str, camera: str, seq_length: int, fps: float = 30.0,
) -> None:
    """Create a sequence INI file used by the TrackEval library.

    :param gt_dir: Directory in which to write ``seqinfo.ini``.
    :param camera: Sequence name (typically the input-file type).
    :param seq_length: Number of frames in the sequence.
    :param fps: Frame rate to record under ``frameRate=``.  Defaults to
        ``30.0`` for backwards compatibility with callers that have not
        yet started threading the calibration FPS through.
    """
    ini_file_path = os.path.join(gt_dir, "seqinfo.ini")
    with open(ini_file_path, "w") as f:
        f.write("[Sequence]\n")
        f.write(f"name={camera}\n")
        f.write("imDir=img1\n")
        f.write(f"frameRate={fps}\n")
        f.write(f"seqLength={seq_length}\n")
        f.write("imWidth=1920\n")
        f.write("imHeight=1080\n")
        f.write("imExt=.jpg\n")


def prepare_evaluation_folder(
    dataset_config: Dict[str, Any],
    input_file_type: str,
    fps: float = 30.0,
    seq_length: int = 20000,
) -> Tuple[str, str]:
    """
    Prepare the evaluation folder structure required for TrackEval.

    :param dataset_config: Dataset configuration dictionary.
    :param input_file_type: Type label for the input (used as sequence name).
    :param fps: Frame rate forwarded to :func:`make_seq_ini_file`.
        Defaults to ``30.0`` for backwards compatibility.
    :param seq_length: ``seqLength`` written to ``seqinfo.ini``.  Should be
        an upper bound on the number of frames in ``gt.txt`` /
        ``<tracker>.txt`` (typically the same ``num_frames_to_eval`` used by
        :func:`split_files_per_class` /
        :func:`split_files_per_sensor_and_class`).  TrackEval iterates
        ``range(seq_length)`` when loading raw data, so an inflated value
        wastes work; a too-small value silently truncates evaluation.
        Defaults to ``20000`` for backwards compatibility.
    :return: ``(pred_file_path, gt_file_path)``
    """
    sensor_ids = sorted([input_file_type])

    seq_maps_dir_path = os.path.join(dataset_config["GT_FOLDER"], "seqmaps")
    make_seq_maps_file(
        seq_maps_dir_path, sensor_ids, dataset_config["BENCHMARK"], dataset_config["SPLIT_TO_EVAL"]
    )

    mot_version = dataset_config["BENCHMARK"] + "-" + dataset_config["SPLIT_TO_EVAL"]
    gt_root_dir_path = os.path.join(dataset_config["GT_FOLDER"], mot_version)
    gt_rtls_dir_path = os.path.join(gt_root_dir_path, input_file_type)
    make_dir(gt_rtls_dir_path)
    gt_output_dir_path = os.path.join(gt_rtls_dir_path, "gt")
    make_dir(gt_output_dir_path)
    gt_file_path = os.path.join(gt_output_dir_path, "gt.txt")

    make_seq_ini_file(
        gt_rtls_dir_path, camera=input_file_type, seq_length=seq_length, fps=fps,
    )

    pred_dir_path = os.path.join(
        dataset_config["TRACKERS_FOLDER"], mot_version, "data", "data"
    )
    make_dir(pred_dir_path)
    pred_file_path = os.path.join(pred_dir_path, f"{input_file_type}.txt")

    return pred_file_path, gt_file_path


def run_evaluation(
    gt_file: str,
    prediction_file: str,
    dataset_config: Dict[str, Any],
    eval_config: Dict[str, Any],
    eval_type: str,
) -> Any:
    """
    Execute the tracking evaluation using TrackEval.

    :param gt_file: Ground truth file path.
    :param prediction_file: Prediction file path.
    :param dataset_config: Dataset configuration dictionary.
    :param eval_config: Evaluation configuration dictionary.
    :param eval_type: Type of evaluation (``"bbox"`` or ``"location"``).
    :return: Evaluation results.
    """
    metrics_config = {"METRICS": ["HOTA", "CLEAR", "Identity"], "PRINT_CONFIG": False}
    config = {**eval_config, **dataset_config, **metrics_config}
    eval_config = {k: v for k, v in config.items() if k in eval_config.keys()}
    dataset_config = {k: v for k, v in config.items() if k in dataset_config.keys()}
    metrics_config = {k: v for k, v in config.items() if k in metrics_config.keys()}

    evaluator = trackeval.evaluate.Evaluator(eval_config)
    if eval_type == "bbox":
        dataset_list = [trackeval.datasets.MTMCChallenge3DBBox(dataset_config)]
    elif eval_type == "location":
        dataset_list = [trackeval.datasets.MTMCChallenge3DLocation(dataset_config)]
    else:
        raise ValueError(f"Unknown eval_type: {eval_type}")

    metrics_list = []
    for metric in [trackeval.metrics.HOTA, trackeval.metrics.CLEAR, trackeval.metrics.Identity]:
        if metric.get_name() in metrics_config["METRICS"]:
            metrics_list.append(metric(metrics_config))
    if len(metrics_list) == 0:
        raise ValueError("No metric selected for evaluation.")

    return evaluator.evaluate(dataset_list, metrics_list)


# ---------------------------------------------------------------------------
# Per-BEV-sensor orchestration
# ---------------------------------------------------------------------------


def _setup_tracking_output(
    calibration_file: str,
    output_root_dir: str,
    subdir_name: str = "all_sensors",
) -> Tuple[str, float]:
    """
    Shared scaffolding for the per-sensor and all-sensors tracking entry points.

    Creates the output directory and fetches FPS from the calibration file.

    :param calibration_file: Path to the calibration JSON used to fetch FPS.
    :param output_root_dir: Root directory under which to create the output
        sub-directory.
    :param subdir_name: Name of the sub-directory created under
        ``output_root_dir``.  Distinct values per caller make
        ``ls output_root_dir/`` self-explanatory when both per-sensor and
        all-sensors flows have run for the same scene.  Defaults to
        ``"all_sensors"`` for backwards compatibility with the previous
        hard-coded literal.
    :return: ``(output_directory, fps)``.
    """
    logging.info("Computing tracking results...")
    output_directory = os.path.join(output_root_dir, subdir_name)
    os.makedirs(output_directory, exist_ok=True)

    fps = fetch_fps_from_calibration(calibration_file)
    logging.info(f"Fetched FPS {fps} from calibration file: {calibration_file}.")

    return output_directory, fps


def evaluate_tracking_per_BEV_sensor(
    ground_truth_file: str,
    prediction_file: str,
    calibration_file: str,
    eval_options: str,
    output_root_dir: str,
    confidence_threshold: float,
    num_cores: int,
    input_file_type: str,
    num_frames_to_eval: int,
    ground_truth_frame_offset_secs: float,
    map_camera_name_to_bev_name: Dict[str, List[str]],
) -> None:
    """
    Evaluate tracking per BEV sensor.

    Splits GT/pred files into ``{output}/{bev_sensor}/{class}/`` directories
    via :func:`split_files_per_sensor_and_class`, then runs tracking
    evaluation for each class within each sensor directory.

    :param map_camera_name_to_bev_name: Mapping
        ``{camera_id: [bev_sensor_names]}`` produced by
        :func:`spatialai_data_utils.loaders.calibration.get_camera_name_to_bev_name_map`,
        used to fan out each ground-truth row over its BEV groups.  This is
        the layout :func:`_run_tracking_per_sensor` expects.
    """
    output_directory, fps = _setup_tracking_output(
        calibration_file, output_root_dir, subdir_name="per_sensor",
    )

    split_files_per_sensor_and_class(
        ground_truth_file,
        prediction_file,
        output_directory,
        map_camera_name_to_bev_name,
        confidence_threshold,
        num_frames_to_eval,
        ground_truth_frame_offset_secs,
        fps,
    )

    _run_tracking_per_sensor(
        output_directory, eval_options, num_cores,
        input_file_type, ground_truth_frame_offset_secs, fps,
        num_frames_to_eval,
    )


def _run_tracking_per_sensor(
    base_dir: str,
    eval_options: str,
    num_cores: int,
    input_file_type: str,
    ground_truth_frame_offset_secs: float,
    fps: float,
    num_frames_to_eval: int,
) -> None:
    """Run tracking evaluation for each sensor/class sub-directory."""
    for sensor_name in sorted(os.listdir(base_dir)):
        sensor_dir = os.path.join(base_dir, sensor_name)
        if not os.path.isdir(sensor_dir):
            continue

        for class_name in CLASS_LIST:
            class_dir = os.path.join(sensor_dir, class_name)
            if not os.path.isdir(class_dir):
                logging.warning(
                    f"Class folder '{class_name}' not found for sensor '{sensor_name}'. Skipping."
                )
                continue

            gt_path = os.path.join(class_dir, "gt.json")
            pred_path = os.path.join(class_dir, "pred.json")
            output_dir = os.path.join(class_dir, "output")

            if not os.path.exists(gt_path) or not os.path.exists(pred_path):
                if not os.path.exists(gt_path):
                    logging.info(f"Ground truth data not found for sensor: {sensor_name}")
                if not os.path.exists(pred_path):
                    logging.info(f"Prediction data not found for sensor {sensor_name}")
                continue

            logging.info("--------------------------------------------------------------")
            logging.info(f"Evaluating BEV sensor: {sensor_name} on class {class_name}...")

            dataset_config, eval_config = setup_evaluation_configs(
                output_dir, eval_options, num_cores
            )
            output_pred_file, output_gt_file = prepare_evaluation_folder(
                dataset_config, input_file_type, fps,
                seq_length=num_frames_to_eval,
            )
            logging.info("Completed setup for evaluation library.")

            prepare_ground_truth_file(
                gt_path, output_gt_file, fps, ground_truth_frame_offset_secs
            )
            logging.info(f"Completed parsing ground-truth file {gt_path}.")

            prepare_prediction_file(pred_path, output_pred_file, fps, 0)
            logging.info(f"Completed parsing prediction file {pred_path}.")

            run_evaluation(
                output_gt_file, output_pred_file, dataset_config, eval_config, eval_options
            )

    logging.info("--------------------------------------------------------------")


def evaluate_tracking_all_BEV_sensors(
    ground_truth_file: str,
    prediction_file: str,
    calibration_file: str,
    eval_options: str,
    output_root_dir: str,
    confidence_threshold: float,
    num_cores: int,
    input_file_type: str,
    num_frames_to_eval: int,
    ground_truth_frame_offset_secs: float,
) -> None:
    """
    Evaluate tracking for all BEV sensors combined (no per-sensor split).

    Splits GT/pred files into ``{output}/{class}/`` directories via
    :func:`split_files_per_class`, then runs tracking evaluation for each
    class.  This is the layout :func:`_run_tracking_all_sensors` expects.
    """
    output_directory, fps = _setup_tracking_output(
        calibration_file, output_root_dir, subdir_name="all_sensors",
    )

    split_files_per_class(
        ground_truth_file, prediction_file, output_directory,
        confidence_threshold, num_frames_to_eval, ground_truth_frame_offset_secs, fps,
    )

    _run_tracking_all_sensors(
        output_directory,
        eval_options, num_cores, input_file_type, ground_truth_frame_offset_secs, fps,
        num_frames_to_eval,
    )


def _run_tracking_all_sensors(
    output_directory: str,
    eval_options: str,
    num_cores: int,
    input_file_type: str,
    ground_truth_frame_offset_secs: float,
    fps: float,
    num_frames_to_eval: int,
) -> None:
    """Run tracking evaluation for all classes in the output directory."""
    for class_name in CLASS_LIST:
        class_dir = os.path.join(output_directory, class_name)

        if not os.path.isdir(class_dir):
            logging.warning(f"Skipping class folder '{class_name}' as it was not found.")
            logging.info("--------------------------------")
            continue

        logging.info(f"Evaluating all BEV sensors on class {class_name}.")

        gt_path = os.path.join(class_dir, "gt.json")
        pred_path = os.path.join(class_dir, "pred.json")

        dataset_config, eval_config = setup_evaluation_configs(
            output_directory, eval_options, num_cores
        )
        output_pred_file, output_gt_file = prepare_evaluation_folder(
            dataset_config, input_file_type, fps,
            seq_length=num_frames_to_eval,
        )
        logging.info("Completed setup for evaluation library.")

        prepare_ground_truth_file(
            gt_path, output_gt_file, fps, ground_truth_frame_offset_secs
        )
        logging.info(f"Completed parsing ground-truth file {gt_path}.")

        prepare_prediction_file(pred_path, output_pred_file, fps, 0)
        logging.info(f"Completed parsing prediction file {pred_path}.")

        run_evaluation(
            output_gt_file, output_pred_file, dataset_config, eval_config, eval_options
        )

    logging.info("--------------------------------------------------------------")
