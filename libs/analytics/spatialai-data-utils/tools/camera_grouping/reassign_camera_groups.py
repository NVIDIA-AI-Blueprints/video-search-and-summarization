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
Reassign cameras to existing BEV groups inside a calibration.json.

Usage example:
  python reassign_camera_groups.py data/calibration.json \
    --move cam-01:bev-sensor-2 cam-05:bev-sensor-3 \
    --output data/calibration_reassigned.json

The script:
- loads the calibration JSON
- looks up the target group's full `group` payload from existing sensors
- updates each specified camera's `group` field to that payload
- saves to the output path (defaults to <input>_reassigned.json)
"""

import argparse
import json
import logging
from pathlib import Path
from typing import List, Tuple, Optional

from spatialai_data_utils.core.cameras.group_utils import (
    reassign_camera_groups_from_calibration,
)

logger = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(
        description="Reassign cameras to existing BEV groups in calibration.json",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    parser.add_argument(
        "input_calibration",
        type=str,
        help="Path to calibration.json produced by create_camera_clusters.py",
    )

    parser.add_argument(
        "--move",
        type=str,
        nargs="+",
        required=True,
        help="Mappings of camera_id:group_name (space separated) to reassign.",
    )

    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Output path for updated calibration. Defaults to <input>_reassigned.json",
    )

    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite the input calibration file in-place.",
    )

    parser.add_argument(
        "--strict",
        action="store_true",
        help="Fail if a camera or target group is missing; otherwise skip with warning.",
    )

    parser.add_argument(
        "--prefer_existing_fov",
        action="store_true",
        help="Prefer existing FOV from calibration file, fall back to frustum calculation if not available (default: use frustum-based FOV generation)",
    )

    parser.add_argument(
        "--map_file",
        type=str,
        default=None,
        help="Optional map image (Top.png). If omitted, will look for Top.png next to the calibration file.",
    )

    parser.add_argument(
        "--vis_no_camera_id_labels",
        action="store_true",
        help="When visualizing, draw camera IDs on the map.",
    )

    parser.add_argument(
        "--dilation",
        type=float,
        default=1.0,
        help="Dilation distance (meters) when recomputing group bounds.",
    )

    parser.add_argument(
        "--height_range",
        type=float,
        nargs=2,
        default=[1.0, 3.0],
        metavar=("MIN", "MAX"),
        help="Height range (meters) for ground plane intersection when recomputing origins.",
    )

    parser.add_argument(
        "--image_size",
        type=int,
        nargs=2,
        default=[1920, 1080],
        metavar=("WIDTH", "HEIGHT"),
        help="Image size (pixels) used for frustum-based FOV generation when recomputing origins.",
    )

    parser.add_argument(
        "--max_camera_distance",
        type=float,
        default=30.0,
        help="Max distance (meters) for frustum calculation when recomputing origins.",
    )

    parser.add_argument(
        "--output_suffix",
        type=str,
        default="reassigned",
        help="Suffix for output files (default: 'reassigned')",
    )

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    
    # Delegate the heavy lifting to core helper
    try:
        result_path, warnings = reassign_camera_groups_from_calibration(
            input_calibration=args.input_calibration,
            moves=args.move,
            output=args.output,
            overwrite=args.overwrite,
            strict=args.strict,
            map_file=args.map_file,
            prefer_existing_fov=args.prefer_existing_fov,
            dilation=args.dilation,
            height_range=tuple(args.height_range),
            image_size=tuple(args.image_size),
            max_camera_distance=args.max_camera_distance,
            output_suffix=args.output_suffix,
            label_camera_ids=not args.vis_no_camera_id_labels,
            visualize=True,
        )
        if warnings:
            for w in warnings:
                logger.warning(w)
        logger.info("✓ Completed processing: %s", result_path)
    except Exception as exc:  # noqa: BLE001
        logger.exception("✗ Failed to process %s: %s", args.input_calibration, exc)


if __name__ == "__main__":
    main()


