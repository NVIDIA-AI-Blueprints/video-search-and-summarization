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
NVIDIA Schema Data Loader Module

This module provides utilities for loading and parsing tracking and detection results
stored in NVIDIA schema format (JSON-lines). It handles both single-sensor and
multi-sensor tracking data with 3D object information.

Key Features:
- Load NVSchema JSON-lines files with per-frame object data
- Parse 3D object detections with location, scale, and rotation
- Support for multi-sensor systems with sensor ID mapping
- Automatic error handling for JSON parsing issues
- Convert data to standardized dictionary format
- Handle confidence scores and object types

Data Format:
- Input: JSON-lines file where each line represents one frame
- Each frame contains sensor ID, frame ID, and list of detected objects
- Objects include 3D location, bounding box dimensions, orientation, confidence, and type
- Output: Nested dictionary structure [frame_id][sensor_id] -> list of objects

Main Functions:
- load_nvschema: Load and parse tracking/detection results from NVSchema file

Object Attributes:
- person id: Unique identifier for tracked objects
- 3d location: World coordinates [x, y, z]
- 3d bounding box scale: Dimensions [width, length, height]
- 3d bounding box rotation: Euler angles [pitch, roll, yaw]
- confidence: Detection/tracking confidence score
- type: Object class (e.g., 'person', 'vehicle')

Error Handling:
The module includes robust JSON parsing with automatic single-quote to
double-quote conversion for malformed JSON data.

Typical Usage:
1. Load tracking results from NVSchema JSON-lines file
2. Access detections by frame ID and sensor ID
3. Extract 3D object information for downstream processing
4. Use for evaluation, visualization, or data conversion tasks
"""

import json
import logging
import os

from spatialai_data_utils.constants import (
    KEY_BBOX3D,
    KEY_BBOX_ROTATION_3D,
    KEY_BBOX_SCALE_3D,
    KEY_CONFIDENCE,
    KEY_COORDINATES,
    KEY_LOCATION_3D,
    KEY_NVSCHEMA_ID,
    KEY_OBJECT_ID,
    KEY_TYPE,
)
from spatialai_data_utils.core.boxes.box_3d import check_nvschema_coords_len

logger = logging.getLogger(__name__)

NVSCHEMA_FORMAT = "nvschema"
GT_JSON_FORMAT = "gt_json_aicity"
_SUPPORTED_OUTPUT_FORMATS = (NVSCHEMA_FORMAT, GT_JSON_FORMAT)


def nvschema_obj_to_gt_json_aicity(obj):
    """Flatten a single raw NVSchema object dict to the legacy gt_json_aicity shape.

    Raw NVSchema stores 3D box data nested inside
    ``bbox3d.coordinates``; the first nine values are the canonical
    ``[x, y, z, w, l, h, pitch, roll, yaw]`` tuple prescribed by the
    ``Bbox3d`` proto (see
    :func:`spatialai_data_utils.core.boxes.box_3d.check_nvschema_coords_len`).
    Trailing extras beyond index 8 (e.g. velocity components) are
    permitted by the validator and silently ignored here.

    The gt_json_aicity convention expects the box split across flat top-level
    keys including a 3-element ``"3d bounding box rotation": [pitch,
    roll, yaw]``.

    :param obj: Raw NVSchema object dict with at least ``bbox3d.coordinates``.
    :type obj: dict
    :return: gt_json_aicity-formatted dict with flat ``"3d location"``,
        ``"3d bounding box scale"``, ``"3d bounding box rotation"``,
        ``"object id"``, ``"confidence"``, ``"type"`` keys.  Any extra
        top-level NVSchema fields are preserved as-is.
    :rtype: dict
    :raises KeyError: If ``obj["bbox3d"]["coordinates"]`` is missing.
    :raises ValueError: If ``bbox3d.coordinates`` has fewer than 9 values.
    """
    try:
        coords = obj[KEY_BBOX3D][KEY_COORDINATES]
    except (KeyError, TypeError) as exc:
        raise KeyError(
            "NVSchema object missing 'bbox3d.coordinates'; cannot convert to gt_json_aicity"
        ) from exc
    # Length must be >= 9; trailing extras are allowed.
    check_nvschema_coords_len(coords)

    # Best-effort int coercion of the string NVSchema id.
    raw_id = obj.get(KEY_NVSCHEMA_ID)
    try:
        obj_id = int(raw_id) if raw_id is not None else -1
    except (TypeError, ValueError):
        obj_id = raw_id  # keep original string if non-numeric

    gt = {
        KEY_OBJECT_ID: obj_id,
        KEY_LOCATION_3D: list(coords[0:3]),
        KEY_BBOX_SCALE_3D: list(coords[3:6]),
        KEY_BBOX_ROTATION_3D: list(coords[6:9]),  # [pitch, roll, yaw]
        KEY_CONFIDENCE: obj.get(KEY_CONFIDENCE, 1.0),
        KEY_TYPE: obj.get(KEY_TYPE, "unknown"),
    }
    # Preserve any additional top-level fields the caller may rely on
    # (e.g. custom metadata), without overwriting the flattened ones.
    for k, v in obj.items():
        if k in (KEY_BBOX3D,) or k in gt:
            continue
        gt[k] = v
    return gt


def iter_frame_rows(file_path):
    """Stream an NVSchema JSON-lines file as per-row normalised dicts.

    Sibling of :func:`load_nvschema` for callers that need the
    one-row-per-yield mapping rather than the
    ``{frame_id: {sensor_id: [objects, ...]}}`` collapse.  Each
    yielded dict carries the per-frame fields downstream NVSchema
    consumers typically need::

        {
            "frame_id":  int,                  # int(row["id"])
            "sensor_id": str,                  # row["sensorId"]
            "timestamp": Optional[str],        # row.get("timestamp")
            "objects":   list[dict],           # row.get("objects", [])
            "info":      dict[str, str],       # row.get("info") or {}
        }

    The optional top-level ``info`` map (``{cam_name: ISO_timestamp}``)
    is normalised to ``{}`` for rows that omit it, so consumers can
    treat it uniformly without an existence check on every row.
    Same single-quote → double-quote fix-up
    :func:`load_nvschema` carries is applied before giving up on a
    line, so mildly-malformed exports still parse.

    Used by:
    - ``tools/visualization/draw_3dbbox.py`` — the per-row CLI's
      ``_iter_frame_rows`` is now a thin wrapper around this
      function (the original CLI-private helper was promoted to the
      library so other per-row NVSchema consumers — e.g. the
      projection CLI under ``tools/projection/`` or future
      evaluation tools — share the same parsing / normalisation
      logic).

    Unlike :func:`load_nvschema` (which collapses same-(frame,
    sensor) rows into a single object list and discards
    ``timestamp`` / ``info``), this generator preserves every input
    line untouched so callers can drive per-row flows that depend
    on the full row metadata (timestamp-based image lookup,
    info-driven per-camera dispatch, …).

    :param file_path: Path to the NVSchema JSON-lines file.
    :type file_path: str
    :return: Iterator yielding one normalised per-row dict at a time.
    :rtype: Iterator[dict]
    """
    with open(file_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                # Same single-quote -> double-quote fix-up that
                # ``load_nvschema`` carries.  Mildly malformed
                # exports still parse.
                row = json.loads(line.replace("'", '"'))
            yield {
                "frame_id":  int(row["id"]),
                "sensor_id": row["sensorId"],
                "timestamp": row.get("timestamp"),
                "objects":   row.get("objects", []),
                "info":      row.get("info") or {},
            }


def load_nvschema(file_path, output_format=NVSCHEMA_FORMAT):
    """Load detection/tracking results from an NVSchema JSON-lines file.

    Each line of the input file is one frame recorded by one observing
    camera within a BEV sensor group::

        {"id": <frame_id>, "sensorId": <camera>, "objects": [<obj>, ...]}

    The same world-space object may appear on multiple lines of the
    same frame — once per observing camera that could see it — with
    **byte-identical** ``bbox3d.coordinates`` (all cameras in a BEV
    sensor group share a common world coordinate frame).  This loader
    preserves that structure: the returned dict is keyed by
    ``(frame_id, sensorId)``, so cross-camera duplicates are retained
    verbatim.  Callers that want a deduplicated per-frame view should
    merge by ``"id"`` within each frame.

    Two output formats are supported via *output_format*:

    * ``"nvschema"`` (default): object dicts are preserved **verbatim** in
      their native NVSchema shape::

          {
              "id": str,                # raw NVSchema id (string)
              "type": str,
              "confidence": float,
              "coordinate": {"x", "y", "z"},
              "bbox3d": {
                  "coordinates": [x, y, z, w, l, h, pitch, roll, yaw],  # 9-value
                  "embedding":   [...],
                  "confidence":  float,
              },
          }

    * ``"gt_json_aicity"``: each object is flattened via
      :func:`nvschema_obj_to_gt_json_aicity` to the legacy gt_json_aicity shape with
      flat keys ``"3d location"``, ``"3d bounding box scale"``,
      ``"3d bounding box rotation"``, ``"object id"``, ``"confidence"``,
      ``"type"``.  Use this when feeding results into older modules that
      still consume gt_json_aicity (e.g.
      :func:`spatialai_data_utils.loaders.ground_truth.process_bbox3d_gt`,
      sparse4d loaders, mtmc evaluation).

    Attempts a single-quote -> double-quote fix-up for malformed JSON
    lines before giving up.

    :param file_path: Path to the NVSchema JSON-lines file.
    :type file_path: str
    :param output_format: ``"nvschema"`` (raw, default) or ``"gt_json_aicity"``
        (flattened legacy format).
    :type output_format: str
    :return: Nested dict ``{frame_id (int): {sensor_id (str): [obj, ...]}}``.
    :rtype: dict
    :raises ValueError: If *output_format* is not one of the supported values.
    """
    if output_format not in _SUPPORTED_OUTPUT_FORMATS:
        raise ValueError(
            f"output_format must be one of {_SUPPORTED_OUTPUT_FORMATS}; "
            f"got {output_format!r}"
        )

    metadata_scene = {}
    with open(file_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                frame_info = json.loads(line)
            except json.JSONDecodeError:
                # Try the common single-quote malformed-JSON fix-up.
                fixed_line = line.replace("'", '"')
                frame_info = json.loads(fixed_line)

            frame_id = int(frame_info["id"])
            sensor_id = frame_info["sensorId"]
            objects = frame_info["objects"]
            if output_format == GT_JSON_FORMAT:
                objects = [nvschema_obj_to_gt_json_aicity(o) for o in objects]

            if frame_id not in metadata_scene:
                metadata_scene[frame_id] = {}
            if sensor_id not in metadata_scene[frame_id]:
                metadata_scene[frame_id][sensor_id] = []
            metadata_scene[frame_id][sensor_id].extend(objects)
    return metadata_scene


def load_nvschemas(result_dir, scene_names, output_format=NVSCHEMA_FORMAT):
    """
    Load NVschema results for multiple scenes from a directory.

    Iterates through `scene_names`, constructs the expected file path for each scene
    (assuming the file name matches the scene name) within `result_dir`, loads each
    scene's data using `load_nvschema`, and combines them into a single dictionary.
    Includes basic error handling for file loading.

    :param result_dir: Path to the directory containing per-scene NVschema files.
    :type result_dir: str
    :param scene_names: A list of scene names (which should match filenames) to load.
    :type scene_names: list[str]
    :param output_format: Passed through to :func:`load_nvschema`; either
        ``"nvschema"`` (raw, default) or ``"gt_json_aicity"`` (flattened legacy shape).
    :type output_format: str
    :return: A dictionary mapping scene names to the loaded results dictionaries
             (output of `load_nvschema` for each scene).  Scenes whose file
             cannot be loaded are **skipped** (logged, not raised) so the
             batch always returns for the scenes that did succeed.
    :rtype: dict
    """
    logger.info("Loading NVSchema results from %s ...", result_dir)

    results_dict_new = {}
    for scene_name in scene_names:
        json_path = os.path.join(result_dir, scene_name)
        try:
            results_dict_scene = load_nvschema(json_path, output_format=output_format)
        except (FileNotFoundError, PermissionError) as exc:
            # Access-level problems: not present or can't read.  Log briefly
            # (no stack trace) and skip — these are expected "missing scene"
            # situations in partial-dataset batches.
            logger.warning(
                "Skipping scene %r: cannot access %s (%s)",
                scene_name, json_path, exc,
            )
            continue
        except json.JSONDecodeError:
            # Malformed content — log the full traceback so the user can
            # track down the bad line, then skip.
            logger.exception(
                "Skipping scene %r: malformed JSON at %s",
                scene_name, json_path,
            )
            continue
        except Exception:
            # Anything else is unexpected — surface it with a stack trace
            # but don't abort the whole batch.
            logger.exception(
                "Skipping scene %r: unexpected error loading %s",
                scene_name, json_path,
            )
            continue

        if results_dict_scene:
            results_dict_new[scene_name] = results_dict_scene

    return results_dict_new
