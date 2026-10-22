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
Preprocessing utilities for MTMC evaluation JSONL files.

This module splits ground-truth and prediction JSONL files by sensor and/or
class for downstream detection and tracking evaluators.

Main Components:
- split_files_per_sensor_and_class: Split GT and prediction JSONL files by
  sensor and primary object class.
- split_files_per_class: Split GT and prediction JSONL files by primary
  object class.
- split_files_by_sensor: Split GT and prediction JSONL files by sensor.
- _safe_frame_id: Parse and validate frame ids while skipping malformed rows.

Preprocessing:
- Load newline-delimited JSON records from GT and prediction files.
- Normalize sub-classes to primary classes through
  ``map_sub_class_to_primary_class``.
- Filter prediction objects by confidence threshold.
- Drop embedding payloads before evaluation.
- Apply optional ground-truth frame offsets for delayed GT streams.

Related helpers:
- Generic filesystem helpers live in
  :mod:`spatialai_data_utils.utils.filesystem_utils`.
- String sanitization lives in
  :mod:`spatialai_data_utils.utils.string_utils`.
"""

import os
import json
import logging
import copy
from typing import Any
from spatialai_data_utils.eval.common.classes import map_sub_class_to_primary_class


def _safe_frame_id(data: Any, source: str) -> Any:
    """Return ``int(data["id"])`` or ``None`` (with a warning) for invalid ids.

    JSON-lines records consumed by the splitters carry an ``id`` field that
    is supposed to be an integer frame index, but the file may be malformed
    (missing key, ``null``, non-numeric). Rather than crashing the whole
    split, log and skip the offending record.

    :param data: A JSON-decoded record from a GT or prediction line.
    :param source: Short label used in the warning message
        (e.g. ``"ground truth"``, ``"prediction"``) for traceability.
    :return: The frame id as an ``int``, or ``None`` if it cannot be parsed.
    """
    raw = data.get("id")
    if raw is None:
        logging.warning(f"Skipping {source} record without 'id' field: {data!r}")
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        logging.warning(
            f"Skipping {source} record with non-numeric 'id'={raw!r}: {data!r}"
        )
        return None


def split_files_per_sensor_and_class(
    gt_path: str,
    pred_path: str,
    output_base_dir: str,
    map_camera_name_to_bev_name,
    confidence_threshold: float = 0.0,
    num_frames_to_eval: int = 20000,
    ground_truth_frame_offset_secs: float = 0.0,
    fps: float = 30.0,
):
    """
    Splits GT and Pred files by sensor and class, saving them into separate directories.

    :param gt_path: Path to the ground truth JSON file.
    :param pred_path: Path to the predictions JSON file.
    :param output_base_dir: Base directory to save split files.
    """
    # Create output base directory
    os.makedirs(output_base_dir, exist_ok=True)

    # Keep track of unique sensor IDs
    gt_sensors = set()
    pred_sensors = set()

    # Calculate the offset in frames as prediction may be ahead of ground truth
    gt_offset_frames = round(ground_truth_frame_offset_secs * fps)

    # Function to process and write objects
    def process_objects_gt(
        data, output_writers, sensors, output_base_dir, map_camera_name_to_bev_name
    ):
        cam_sensor_name = data.get("sensorId")
        # Convert camera id to BEV sensor id
        bev_sensor_names = map_camera_name_to_bev_name[cam_sensor_name]
        for bev_sensor_name in bev_sensor_names:
            # Reset per BEV sensor; otherwise objects accumulate across
            # bev_sensor_names and a sensor's gt.json ends up with
            # duplicates (its own objects + every prior sensor's).
            data_dict_per_class: dict = {}
            sensors.add(bev_sensor_name)
            for index, obj in enumerate(data.get("objects", [])):
                class_name = obj.get("type")
                if class_name.lower() not in map_sub_class_to_primary_class:
                    logging.warning(
                        f"Class {class_name} not found in valid class names."
                    )
                    continue

                class_name = map_sub_class_to_primary_class[class_name.lower()]

                if class_name not in data_dict_per_class:
                    data_dict_per_class[class_name] = copy.deepcopy(data)
                    data_dict_per_class[class_name]["objects"] = []
                sensor_class_dir = os.path.join(
                    output_base_dir, bev_sensor_name, class_name
                )
                os.makedirs(sensor_class_dir, exist_ok=True)

                output_file_path = os.path.join(sensor_class_dir, "gt.json")
                if (bev_sensor_name, class_name) not in output_writers:
                    output_writers[(bev_sensor_name, class_name)] = open(
                        output_file_path, "w"
                    )

                data_dict_per_class[class_name]["objects"].append(
                    data["objects"][index]
                )

            for class_name in data_dict_per_class.keys():
                output_writers[(bev_sensor_name, class_name)].write(
                    json.dumps(data_dict_per_class[class_name]) + "\n"
                )

    def process_objects_pred(
        data, output_writers, sensors, output_base_dir, confidence_threshold
    ):
        sensor_name = data.get("sensorId")
        sensors.add(sensor_name)
        data_dict_per_class = {}

        for index, obj in enumerate(data.get("objects")):
            class_name = obj.get("type")

            if class_name.lower() not in map_sub_class_to_primary_class:
                logging.warning(f"Class {class_name} not found in valid class names.")
                continue

            class_name = map_sub_class_to_primary_class[class_name.lower()]

            if "bbox3d" in obj:
                conf = obj["bbox3d"].get("confidence")
                if float(conf) < confidence_threshold:
                    continue

            # remove embedding before evaluation
            data["objects"][index]["embedding"] = {}
            data["objects"][index]["bbox3d"]["embedding"] = [{}]

            if class_name not in data_dict_per_class:
                data_dict_per_class[class_name] = copy.deepcopy(data)
                data_dict_per_class[class_name]["objects"] = []
            sensor_class_dir = os.path.join(output_base_dir, sensor_name, class_name)
            os.makedirs(sensor_class_dir, exist_ok=True)

            output_file_path = os.path.join(sensor_class_dir, "pred.json")
            if (sensor_name, class_name) not in output_writers:
                output_writers[(sensor_name, class_name)] = open(output_file_path, "w")

            data_dict_per_class[class_name]["objects"].append(data["objects"][index])

        for class_name in data_dict_per_class.keys():
            output_writers[(sensor_name, class_name)].write(
                json.dumps(data_dict_per_class[class_name]) + "\n"
            )

    # Process GT data
    sensor_class_gt_writers: dict = {}
    try:
        with open(gt_path, "r") as gt_file:
            for line in gt_file:
                if '"' not in line and "'" in line:
                    line = line.replace("'", '"')
                data = json.loads(line)
                frame_id = _safe_frame_id(data, "ground truth")
                if frame_id is None:
                    continue
                if frame_id >= num_frames_to_eval + gt_offset_frames:  # frame id starts from 0
                    continue
                process_objects_gt(
                    data,
                    sensor_class_gt_writers,
                    gt_sensors,
                    output_base_dir,
                    map_camera_name_to_bev_name,
                )
    finally:
        for writer in sensor_class_gt_writers.values():
            if writer is not None:
                writer.close()

    logging.info(f"Found BEV sensors in ground truth: {', '.join(sorted(gt_sensors))}")

    # Process Pred data
    sensor_class_pred_writers: dict = {}
    try:
        with open(pred_path, "r") as pred_file:
            for line in pred_file:
                if '"' not in line and "'" in line:
                    line = line.replace("'", '"')
                data = json.loads(line)
                frame_id = _safe_frame_id(data, "prediction")
                if frame_id is None:
                    continue
                if frame_id >= num_frames_to_eval:  # frame id starts from 0
                    continue
                process_objects_pred(
                    data,
                    sensor_class_pred_writers,
                    pred_sensors,
                    output_base_dir,
                    confidence_threshold,
                )
    finally:
        for writer in sensor_class_pred_writers.values():
            if writer is not None:
                writer.close()

    logging.info(f"Found BEV sensors in predictions: {', '.join(sorted(pred_sensors))}")


def split_files_per_class(
    gt_path: str,
    pred_path: str,
    output_base_dir: str,
    confidence_threshold: float = 0.0,
    num_frames_to_eval: int = 20000,
    ground_truth_frame_offset_secs: float = 0.0,
    fps: float = 30.0,
):
    """
    Splits GT and Pred files per class, saving them into separate directories.

    :param gt_path: Path to the ground truth JSON file.
    :param pred_path: Path to the predictions JSON file.
    :param output_base_dir: Base directory to save split files.
    """
    os.makedirs(output_base_dir, exist_ok=True)

    gt_classes = set()
    pred_classes = set()

    gt_offset_frames = round(ground_truth_frame_offset_secs * fps)

    def process_objects_pred(data, output_writers, output_base_dir, class_set):
        data_dict_per_class = {}
        for index, obj in enumerate(data.get("objects", [])):
            class_name = obj.get("type")
            if class_name.lower() not in map_sub_class_to_primary_class:
                logging.warning(f"Class {class_name} not found in valid class names.")
                continue

            class_name = map_sub_class_to_primary_class[class_name.lower()]

            # Apply confidence filter BEFORE allocating the per-class entry,
            # otherwise classes with no surviving objects still emit a line
            # with ``"objects": []`` (matches the ordering already used in
            # ``split_files_per_sensor_and_class.process_objects_pred``).
            if "bbox3d" in obj:
                conf = obj["bbox3d"].get("confidence")
                if float(conf) < confidence_threshold:
                    continue

            data["objects"][index]["embedding"] = {}
            data["objects"][index]["bbox3d"]["embedding"] = [{}]

            if class_name not in data_dict_per_class:
                data_dict_per_class[class_name] = copy.deepcopy(data)
                data_dict_per_class[class_name]["objects"] = []

            class_set.add(class_name)
            class_dir = os.path.join(output_base_dir, class_name)
            os.makedirs(class_dir, exist_ok=True)

            if class_name not in output_writers:
                output_file_path = os.path.join(class_dir, "pred.json")
                output_writers[class_name] = open(output_file_path, "w")

            data_dict_per_class[class_name]["objects"].append(data["objects"][index])

        for class_name in data_dict_per_class.keys():
            output_writers[class_name].write(json.dumps(data_dict_per_class[class_name]) + "\n")

    def process_objects_gt(data, output_writers, output_base_dir, class_set):
        data_dict_per_class = {}
        for index, obj in enumerate(data.get("objects", [])):
            class_name = obj.get("type")

            if class_name.lower() not in map_sub_class_to_primary_class:
                logging.warning(f"Class {class_name} not found in valid class names.")
                continue

            class_name = map_sub_class_to_primary_class[class_name.lower()]

            if class_name not in data_dict_per_class:
                data_dict_per_class[class_name] = copy.deepcopy(data)
                data_dict_per_class[class_name]["objects"] = []

            class_set.add(class_name)
            class_dir = os.path.join(output_base_dir, class_name)
            os.makedirs(class_dir, exist_ok=True)

            if class_name not in output_writers:
                output_file_path = os.path.join(class_dir, "gt.json")
                output_writers[class_name] = open(output_file_path, "w")

            data_dict_per_class[class_name]["objects"].append(data["objects"][index])

        for class_name in data_dict_per_class.keys():
            output_writers[class_name].write(json.dumps(data_dict_per_class[class_name]) + "\n")

    class_gt_writers: dict = {}
    try:
        with open(gt_path, "r") as gt_file:
            for line in gt_file:
                if '"' not in line and "'" in line:
                    line = line.replace("'", '"')
                data = json.loads(line)
                frame_id = _safe_frame_id(data, "ground truth")
                if frame_id is None:
                    continue
                if frame_id >= num_frames_to_eval + gt_offset_frames:
                    continue
                process_objects_gt(data, class_gt_writers, output_base_dir, gt_classes)
    finally:
        for writer in class_gt_writers.values():
            if writer is not None:
                writer.close()

    logging.info(f"Found classes {', '.join(sorted(gt_classes))} in ground truth.")

    class_pred_writers: dict = {}
    try:
        with open(pred_path, "r") as pred_file:
            for line in pred_file:
                if '"' not in line and "'" in line:
                    line = line.replace("'", '"')
                data = json.loads(line)
                frame_id = _safe_frame_id(data, "prediction")
                if frame_id is None:
                    continue
                if frame_id >= num_frames_to_eval:
                    continue
                process_objects_pred(data, class_pred_writers, output_base_dir, pred_classes)
    finally:
        for writer in class_pred_writers.values():
            if writer is not None:
                writer.close()

    logging.info(f"Found classes {', '.join(sorted(pred_classes))} in prediction.")


def split_files_by_sensor(
    gt_path: str,
    pred_path: str,
    output_base_dir: str,
    map_camera_name_to_bev_name,
    confidence_threshold,
    num_frames_to_eval,
    ground_truth_frame_offset_secs: float = 0.0,
    fps: float = 30.0,
):
    """
    Splits GT and Pred files by sensor and saves them into separate directories.

    Mirrors the temporal-offset handling of
    :func:`split_files_per_sensor_and_class` and
    :func:`split_files_per_class`: when *ground_truth_frame_offset_secs*
    is non-zero (e.g. predictions arrive ahead of ground truth by some
    fixed delay), the ground-truth window is shifted by
    ``round(ground_truth_frame_offset_secs * fps)`` frames so the same
    *num_frames_to_eval* of GT and prediction frames participate in
    evaluation.

    :param gt_path: Path to the ground truth JSON file.
    :param pred_path: Path to the predictions JSON file.
    :param output_base_dir: Base directory to save split files.
    :param map_camera_name_to_bev_name: ``{camera_id: [bev_sensor_names]}``
        mapping used to fan out each ground-truth row over its BEV groups.
    :param confidence_threshold: Drop prediction objects with
        ``bbox3d.confidence`` below this threshold.
    :param num_frames_to_eval: Number of frames to keep, starting from
        frame 0 for predictions and from ``gt_offset_frames`` for GT.
    :param ground_truth_frame_offset_secs: Temporal offset (in seconds)
        applied to GT frame selection.  Defaults to ``0.0``.
    :param fps: Frame rate used to convert the offset into frames.
        Defaults to ``30.0``.
    """
    os.makedirs(output_base_dir, exist_ok=True)

    gt_sensors = set()
    pred_sensors = set()

    gt_offset_frames = round(ground_truth_frame_offset_secs * fps)

    sensor_gt_writers: dict = {}
    try:
        with open(gt_path, "r") as gt_file:
            for line in gt_file:
                if '"' not in line and "'" in line:
                    line = line.replace("'", '"')

                data = json.loads(line)

                frame_id = _safe_frame_id(data, "ground truth")
                if frame_id is None:
                    continue
                if frame_id >= num_frames_to_eval + gt_offset_frames:
                    continue

                cam_sensor_name = data["sensorId"]

                bev_sensor_names = map_camera_name_to_bev_name[cam_sensor_name]
                for bev_sensor_name in bev_sensor_names:
                    gt_sensors.add(bev_sensor_name)
                    sensor_dir = os.path.join(output_base_dir, bev_sensor_name)
                    os.makedirs(sensor_dir, exist_ok=True)
                    gt_file_path = os.path.join(sensor_dir, "gt.json")

                    if bev_sensor_name not in sensor_gt_writers:
                        sensor_gt_writers[bev_sensor_name] = open(gt_file_path, "w")

                    sensor_gt_writers[bev_sensor_name].write(json.dumps(data) + "\n")
    finally:
        for writer in sensor_gt_writers.values():
            if writer is not None:
                writer.close()

    logging.info(f"Found BEV sensors: {', '.join(sorted(gt_sensors))} in ground truth file.")

    sensor_pred_writers: dict = {}
    try:
        with open(pred_path, "r") as pred_file:
            for line in pred_file:
                if '"' not in line and "'" in line:
                    line = line.replace("'", '"')
                data = json.loads(line)

                frame_id = _safe_frame_id(data, "prediction")
                if frame_id is None:
                    continue
                if frame_id >= num_frames_to_eval:
                    continue

                sensor_name = data["sensorId"]
                pred_sensors.add(sensor_name)
                sensor_dir = os.path.join(output_base_dir, sensor_name)
                os.makedirs(sensor_dir, exist_ok=True)

                if sensor_name not in sensor_pred_writers:
                    pred_file_path = os.path.join(sensor_dir, "pred.json")
                    sensor_pred_writers[sensor_name] = open(pred_file_path, "w")

                filtered_objects = []
                for obj in data["objects"]:
                    confidence = obj["bbox3d"]["confidence"]
                    if confidence >= confidence_threshold:
                        filtered_objects.append(obj)

                data["objects"] = filtered_objects

                sensor_pred_writers[sensor_name].write(json.dumps(data) + "\n")
    finally:
        for writer in sensor_pred_writers.values():
            if writer is not None:
                writer.close()

    logging.info(f"Found BEV sensors: {', '.join(sorted(pred_sensors))} in prediction file.")
