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
Detection box loaders for MTMC JSONL files.

Provides functions to load ground truth and prediction boxes from JSONL-format
files used in the MTMC pipeline, converting timestamps to frame IDs and
remapping class names.
"""

import json
import logging
import math
import os
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Dict, Set, Tuple

try:
    from nuscenes.eval.common.data_classes import EvalBoxes
except ModuleNotFoundError as exc:
    # Only rewrite when nuscenes itself is absent; let other failures (e.g.
    # nuscenes installed but cv2 missing) propagate without masking.
    if exc.name == "nuscenes":
        from spatialai_data_utils.utils.optional_dependencies import nuscenes_import_error
        raise nuscenes_import_error(__name__) from exc
    raise

from spatialai_data_utils.eval.detection.data_classes import DetectionBox
from spatialai_data_utils.eval.common.classes import map_sub_class_to_primary_class
from spatialai_data_utils.core.geometry.rotation import euler_to_quaternion
from spatialai_data_utils.utils.datetime_utils import parse_timestamp


def _parse_detection_timestamp(ts: str) -> datetime:
    """Parse a detection timestamp as naive UTC for frame-id arithmetic."""
    dt = parse_timestamp(ts)
    if dt.tzinfo is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _find_base_timestamp(gt_path: str, pred_path: str) -> datetime:
    """Return the earliest timestamp across the first line of each file."""
    timestamps = []
    for path in (gt_path, pred_path):
        if os.path.exists(path) and os.path.getsize(path) > 0:
            with open(path) as f:
                line = f.readline()
                if line:
                    if '"' not in line and "'" in line:
                        line = line.replace("'", '"')
                    data = json.loads(line)
                    timestamps.append(_parse_detection_timestamp(data["timestamp"]))
    if not timestamps:
        message = (
            "No parseable timestamps were found in GT or prediction files "
            f"({gt_path}, {pred_path})."
        )
        logging.error(message)
        raise ValueError(message)
    return min(timestamps)


def load_boxes_from_jsonl(
    gt_path: str,
    pred_path: str,
    fps: float,
    confidence_threshold: float = 0.0,
    ground_truth_frame_offset_secs: float = 0.0,
) -> Tuple[EvalBoxes, EvalBoxes]:
    """
    Load GT and prediction boxes from MTMC JSONL files.

    Timestamps are converted to integer frame IDs based on *fps* and the
    earliest timestamp found across both files.

    :param gt_path: Path to the ground truth JSONL file.
    :param pred_path: Path to the prediction JSONL file.
    :param fps: Frames per second (used to convert timestamps to frame IDs).
    :param confidence_threshold: Minimum confidence to keep a prediction box.
    :param ground_truth_frame_offset_secs: Temporal offset applied to GT frames.
    :return: ``(gt_boxes, pred_boxes)``
    :raises FileNotFoundError: If either ``gt_path`` or ``pred_path`` does not
        exist.  Both files are required; ``_find_base_timestamp`` is lenient
        about empty files but the per-file load loops below would otherwise
        raise a bare ``FileNotFoundError`` from ``open(...)`` mid-pipeline,
        after one of the load-progress ``logging.info`` lines has already
        misleadingly announced the file.
    :raises ValueError: If neither file contains a parseable timestamp.
    """
    for path, label in ((gt_path, "ground truth"), (pred_path, "prediction")):
        if not os.path.exists(path):
            raise FileNotFoundError(
                f"{label.capitalize()} JSONL file not found: {path}"
            )

    base_ts = _find_base_timestamp(gt_path, pred_path)
    ts_to_frame: Dict[str, str] = {}
    gt_offset = timedelta(seconds=ground_truth_frame_offset_secs)

    def _get_frame_id(ts_str: str) -> str:
        if ts_str in ts_to_frame:
            return ts_to_frame[ts_str]
        current = _parse_detection_timestamp(ts_str)
        fid = str(math.ceil((current - base_ts).total_seconds() * fps) + 1)
        ts_to_frame[ts_str] = fid
        return fid

    # --- Load predictions ---------------------------------------------------
    pred_boxes = EvalBoxes()
    # Use the prediction-side ``frame_id`` set as the alignment key for
    # filtering GT below.  The previous timestamp-string set could not
    # support a non-zero ``ground_truth_frame_offset_secs``: a GT row's
    # raw timestamp was shifted (post-conversion) by ``gt_offset_frames``,
    # which left ``gt_boxes`` and ``pred_boxes`` indexed under different
    # ``sample_token`` values for the same physical instant — every
    # downstream evaluator therefore saw all detections as FN/FP under
    # any non-zero offset.  Matching on frame_id (after applying the
    # offset to the GT timestamp via ``timedelta``, *before* the
    # frame-id conversion) keeps both sides on the same key without
    # depending on the exact timestamp string format.
    prediction_frame_ids: Set[str] = set()

    logging.info(f"Loading prediction boxes from {pred_path}...")
    with open(pred_path) as f:
        for line_number, line in enumerate(f):
            if '"' not in line and "'" in line:
                line = line.replace("'", '"')
            data = json.loads(line)

            ts = data["timestamp"]
            frame_id = _get_frame_id(ts)
            prediction_frame_ids.add(frame_id)

            box_list = []
            for obj in data["objects"]:
                if obj["type"].lower() not in map_sub_class_to_primary_class:
                    logging.warning(
                        f"Skipped invalid class '{obj['type']}' at line {line_number} from prediction file."
                    )
                    continue
                class_name = map_sub_class_to_primary_class[obj["type"].lower()]
                coords = obj["bbox3d"]["coordinates"]
                conf = obj["bbox3d"].get("confidence", 1.0)
                if float(conf) < confidence_threshold:
                    continue
                # NVSchema canonical bbox3d.coordinates layout is
                # [x, y, z, w, l, h, pitch, roll, yaw] (see
                # core/boxes/box_3d.py PITCH=6, ROLL=7, YAW=8).
                quaternion = euler_to_quaternion(coords[6], coords[7], coords[8])
                box_list.append(
                    DetectionBox(
                        sample_token=frame_id,
                        translation=coords[:3],
                        size=coords[3:6],
                        rotation=quaternion,
                        detection_name=class_name,
                        detection_score=float(conf),
                    )
                )
            pred_boxes.add_boxes(frame_id, box_list)

    logging.info(f"Found {len(prediction_frame_ids)} prediction frame ids.")

    # --- Load ground truth --------------------------------------------------
    gt_boxes = EvalBoxes()
    seen_objects: Dict[str, Set[tuple]] = defaultdict(set)

    logging.info(f"Loading ground truth boxes from {gt_path}...")
    with open(gt_path) as f:
        for line_number, line in enumerate(f):
            if '"' not in line and "'" in line:
                line = line.replace("'", '"')
            data = json.loads(line)

            ts = data["timestamp"]
            # Apply the GT temporal offset *before* converting to a
            # frame_id so the GT and prediction sides end up under the
            # same ``sample_token`` for the same physical instant.  When
            # ``ground_truth_frame_offset_secs == 0`` this is a no-op
            # and the adjusted timestamp string round-trips through
            # ``strftime`` to its canonical form.
            if gt_offset:
                adjusted_ts_str = (
                    _parse_detection_timestamp(ts) - gt_offset
                ).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
            else:
                adjusted_ts_str = ts
            frame_id = _get_frame_id(adjusted_ts_str)
            if frame_id not in prediction_frame_ids:
                continue

            box_list = []
            for obj in data["objects"]:
                if obj["type"].lower() not in map_sub_class_to_primary_class:
                    logging.warning(
                        f"Skipped invalid class '{obj['type']}' at line {line_number} from ground truth file."
                    )
                    continue

                coords_key = tuple(obj["bbox3d"]["coordinates"][:9])
                if coords_key in seen_objects[frame_id]:
                    continue
                seen_objects[frame_id].add(coords_key)

                class_name = map_sub_class_to_primary_class[obj["type"].lower()]
                coords = obj["bbox3d"]["coordinates"]
                # NVSchema canonical bbox3d.coordinates layout is
                # [x, y, z, w, l, h, pitch, roll, yaw] (see
                # core/boxes/box_3d.py PITCH=6, ROLL=7, YAW=8).
                quaternion = euler_to_quaternion(coords[6], coords[7], coords[8])
                # GT samples don't have a meaningful detection score: AP /
                # PR ranks predictions by score and just matches GT
                # geometrically.  Use the codebase-wide ``-1.0`` sentinel
                # so a stray GT confidence (which sometimes arrives as a
                # JSON string like "0.95") doesn't end up sorted into a
                # PR curve and so equality checks against boxes loaded
                # from any other GT path still hold.
                box_list.append(
                    DetectionBox(
                        sample_token=frame_id,
                        translation=coords[:3],
                        size=coords[3:6],
                        rotation=quaternion,
                        detection_name=class_name,
                        detection_score=-1.0,
                    )
                )
            gt_boxes.add_boxes(frame_id, box_list)

    logging.info(
        f"Loaded {len(gt_boxes.sample_tokens)} matched ground truth timestamps."
    )
    return gt_boxes, pred_boxes
