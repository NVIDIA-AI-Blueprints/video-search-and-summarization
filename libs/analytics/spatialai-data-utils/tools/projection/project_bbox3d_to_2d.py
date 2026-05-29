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
3D Bounding Box Corner Projection Tool (NVSchema in / NVSchema out)

Standalone CLI that projects the 3D bounding boxes in an NVSchema
JSON-lines file onto a target camera image plane, keeping only the
boxes visible in that camera's image.  Each visible NVSchema object
has its existing ``"bbox3d"`` block augmented with a proto-native
``bbox3d.info`` ``map<string, string>`` carrying the target sensor id
and the ``json.dumps``-encoded 8 projected corners.

All projection uses pure-numpy geometry (no ``mmdet3d`` dependency).

Input (``--nvschema_path``):
    An NVSchema JSON-lines file as produced by
    :func:`spatialai_data_utils.loaders.nvschema.load_nvschema`::

        {"id": "0", "sensorId": "Camera", "objects": [<NVSchema obj>, ...]}
        {"id": "0", "sensorId": "Camera_01", "objects": [...]}
        ...

    Each object must carry a ``"bbox3d"`` block with ``"coordinates":
    [x, y, z, w, l, h, pitch, roll, yaw]`` — any object missing this
    field raises ``KeyError``.

    Output (``--output_path``):
        A JSON-lines file that mirrors the input line-for-line with each
        visible object enriched by::

            "bbox3d": {
                "coordinates": [...],   # unchanged
                "embedding":   [...],   # unchanged
                "confidence":  float,   # unchanged
                "info": {
                    "sensorId": "<target-camera>",
                    "vertices": "[[x0, y0], ..., [x7, y7]]"  # json.dumps'd
                    # any pre-existing info keys are preserved
                }
            }

        ``info`` values are strings (per the NVSchema proto
        ``map<string, string>``).  ``sensorId`` mirrors the top-level
        NVSchema frame's camelCase key; ``vertices`` is a
        ``json.dumps``-encoded ``(8, 2)`` corner array (the 2D
        projection of the 3D cuboid's corners) that downstream
        consumers must ``json.loads`` before numeric use.

        Boxes not visible on the target camera (corners behind the camera
        or fully outside the image) are dropped from each line.  The
        outer ``"id"`` and ``"sensorId"`` fields are preserved so the
        output file keeps the same frame/observing-camera structure as
        the input — NVSchema files typically record the same world-space
        objects once per observing camera in a BEV sensor group, and
        this tool does not deduplicate across rows.

    Example usage::

        python tools/projection/project_bbox3d_to_2d.py \\
            --sensor_id       Camera_01 \\
            --calib_path      data/mtmc/Scene/calibration.json \\
            --nvschema_path   data/mtmc/Scene/ground_truth_nvschema.json \\
            --output_path     /tmp/projected_on_Camera_01.jsonl \\
            --image_size 1920 1080
    """

import argparse
import json
import logging
import os

from spatialai_data_utils.constants import IMAGE_SIZE, KEY_IMAGE_SIZE
from spatialai_data_utils.core.geometry.projection import project_bev_objects_bbox_in_image
from spatialai_data_utils.loaders.calibration import load_calib_into_dict

logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments for the corner-projection tool."""
    parser = argparse.ArgumentParser(
        description=(
            "Project NVSchema 3D bounding boxes to 8 corner 2D pixel "
            "coordinates for a target camera. Input and output are both "
            "NVSchema JSON-lines; visible objects gain bbox3d.info.sensorId + bbox3d.info.vertices "
            "field and invisible ones are dropped."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--sensor_id", type=str, required=True,
        help="Target camera name to project onto (must exist in calib JSON).",
    )
    parser.add_argument(
        "--calib_path", type=str, required=True,
        help="Path to the scene's calibration JSON file.",
    )
    parser.add_argument(
        "--nvschema_path", type=str, required=True,
        help="Input NVSchema JSON-lines file to project.",
    )
    parser.add_argument(
        "--output_path", type=str, required=True,
        help="Output NVSchema JSON-lines file (each visible object's "
             "bbox3d.info map gets 'sensorId' + 'vertices').",
    )
    parser.add_argument(
        "--image_size", type=int, nargs=2, default=None,
        metavar=("WIDTH", "HEIGHT"),
        help=f"Override the camera image size used for the visibility "
             f"check.  If omitted, the per-sensor 'frameWidth' / "
             f"'frameHeight' fields from the calibration JSON are used; "
             f"if those are also missing, falls back to the package "
             f"default {tuple(IMAGE_SIZE)}.",
    )
    parser.add_argument(
        "--origin", type=float, nargs=3, default=(0.5, 0.5, 0.5),
        metavar=("OX", "OY", "OZ"),
        help="Box origin in (w, l, h) fractions. Default (0.5, 0.5, 0.5) "
             "is geometric centre. Pass '0.5 0.5 0.0' for the legacy "
             "centre-of-bottom-face convention.",
    )
    parser.add_argument(
        "--recentering", action="store_true",
        help="Apply group-origin recentering to the calibration before "
             "projecting.",
    )
    return parser.parse_args()


def main() -> None:
    """Project an NVSchema JSON-lines file line-by-line onto one camera."""
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    args = parse_args()

    # Load the full calibration first so that a missing --sensor_id can be
    # reported against *all* available sensors in the file.
    # ``project_bev_objects_bbox_in_image`` indexes ``calib_dict[sensor_id]``
    # internally, so passing the full flat dict is sufficient — no need
    # to materialise a singleton wrapper.
    calib_dict = load_calib_into_dict(
        args.calib_path,
        recentering=args.recentering,
    )
    if args.sensor_id not in calib_dict:
        raise KeyError(
            f"Sensor '{args.sensor_id}' not found in {args.calib_path}. "
            f"Available: {sorted(calib_dict.keys())}"
        )

    origin = tuple(args.origin)

    # Image-size precedence: CLI override > per-sensor calibration field >
    # package default.  The calibration loader populates "image size" from
    # the sensor's ``frameWidth`` / ``frameHeight`` attributes when present.
    sensor_size = calib_dict[args.sensor_id].get(KEY_IMAGE_SIZE)
    if args.image_size is not None:
        image_size = tuple(args.image_size)
        logger.info(
            "Image size: %s (from --image_size; overrides calibration)",
            image_size,
        )
    elif sensor_size is not None:
        image_size = (int(sensor_size[0]), int(sensor_size[1]))
        logger.info(
            "Image size: %s (from calibration %r)",
            image_size, args.sensor_id,
        )
    else:
        image_size = tuple(IMAGE_SIZE)
        logger.info(
            "Image size: %s (package default — neither --image_size nor "
            "calibration provided one)",
            image_size,
        )

    os.makedirs(os.path.dirname(args.output_path) or ".", exist_ok=True)

    n_lines = 0
    n_boxes_in = 0
    n_boxes_out = 0

    with open(args.nvschema_path) as fin, open(args.output_path, "w") as fout:
        for line in fin:
            line = line.strip()
            if not line:
                continue

            frame = json.loads(line)
            objects = frame.get("objects", [])

            enriched = project_bev_objects_bbox_in_image(
                sensor_id=args.sensor_id,
                calib_dict=calib_dict,
                bev_objects=objects,
                origin=origin,
                image_size=image_size,
            )

            n_lines += 1
            n_boxes_in += len(objects)
            n_boxes_out += len(enriched)

            frame["objects"] = enriched
            fout.write(json.dumps(frame) + "\n")

    logger.info(
        "Processed %d line(s): %d boxes in -> %d visible boxes out "
        "on '%s' (image_size=%s).",
        n_lines, n_boxes_in, n_boxes_out, args.sensor_id, image_size,
    )
    logger.info("Saved projected NVSchema to %s", args.output_path)


if __name__ == "__main__":
    main()
