#!/usr/bin/env python3

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
BEV Group Origin Calculator

This script calculates the BEV (Bird's Eye View) origin and dimensions for camera
groups. It updates the calibration data with group metadata (origin, dimensions)
and saves it to a new file.

Input can be either:
- A dataset folder containing calibration.json and Top.png
- A direct path to a calibration JSON file

Key Features:
- Requires calibration files WITH groups (from camera clustering)
- Uses frustum-based FOV generation by default (works without fieldOfViewPolygon data)
- Optional: Prefer existing FOV from calibration with --prefer-existing-fov flag
- Calculates group bounds from camera FOV unions
- Optional visualization (uses black background if map file not provided)

Use this tool when you:
- Have grouped calibration and need to add origin/dimensions metadata
- Have calibration without FOV polygons (will calculate from camera matrices)
- Want to visualize camera groups on a map

Prerequisites:
- Input calibration file must contain 'group' information for all sensors
- Run camera clustering first if your calibration file lacks group data

Output:
- Updated calibration file with complete group metadata (origin, dimensions)
- Optional visualization of groups on map

Usage Examples:
    # Using dataset folder (auto-detects calibration.json and Top.png)
    python calculate_origin.py data/scene

    # Using direct path to calibration JSON file
    python calculate_origin.py data/scene/calibration_clustered.json

    # Prefer existing FOV from calibration file, fall back to frustum
    python calculate_origin.py data/scene --prefer-existing-fov

    # Custom height range for ground plane intersection
    python calculate_origin.py data/scene --height-range 0.5 2.5

    # Filter to process only specific sensors
    python calculate_origin.py data/scene --sensor-names Camera1,Camera2,Camera3

    # Specify output file
    python calculate_origin.py data/scene --output data/scene/calibration_with_origin.json

    # Overwrite the original calibration file
    python calculate_origin.py data/scene --overwrite

    # Include map visualization
    python calculate_origin.py data/scene --visualize
"""

import argparse
import logging
import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from spatialai_data_utils.core.cameras.bev import (
    calculate_group_origins_from_calibration,
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Calculate BEV group origins and dimensions for camera groups.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Using dataset folder (auto-detects calibration.json and Top.png)
  python %(prog)s data/scene
  
  # Using direct path to calibration JSON file
  python %(prog)s data/scene/calibration_clustered.json
  
  # Prefer existing FOV from calibration file, fall back to frustum
  python %(prog)s data/scene --prefer-existing-fov
  
  # Custom height range for ground plane intersection
  python %(prog)s data/scene --height-range 0.5 2.5
  
  # Filter to process only specific sensors
  python %(prog)s data/scene --sensor-names Camera1,Camera2,Camera3
  
  # Specify output file
  python %(prog)s data/scene -o calibration_with_origins.json
  
  # Overwrite the original file
  python %(prog)s data/scene --overwrite
  
  # Include map visualization
  python %(prog)s data/scene --visualize
        """,
    )

    # Required arguments
    parser.add_argument(
        "input_calibration",
        type=str,
        help="Path to dataset folder (containing calibration.json) or direct path to calibration JSON file",
    )

    # Output configuration
    parser.add_argument(
        "-o",
        "--output",
        type=str,
        default=None,
        help="Output calibration file path (default: input_with_origins.json)",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite the input calibration file (mutually exclusive with --output)",
    )

    # Optional metadata
    parser.add_argument(
        "--map_file",
        type=str,
        default=None,
        help="Path to map image for visualization (uses black background if not provided)",
    )

    # Processing options
    parser.add_argument(
        "--dilation",
        type=float,
        default=1.0,
        help="Dilation distance in meters for group bounds calculation (default: 1.0)",
    )
    parser.add_argument(
        "--height-range",
        type=float,
        nargs=2,
        default=[1.0, 3.0],
        metavar=("MIN", "MAX"),
        help="Height range (min, max) in meters for ground plane intersection (default: 1.0 3.0)",
    )
    parser.add_argument(
        "--prefer-existing-fov",
        action="store_true",
        help="Prefer existing FOV from calibration file, fall back to frustum calculation if not available (default: use frustum-based FOV generation)",
    )
    parser.add_argument(
        "--sensor-names",
        type=str,
        default=None,
        metavar="SENSOR_NAMES",
        help="Filter calibration data to process only specified sensor names (comma-separated list, e.g., Camera_01,Camera_02,Camera_03)",
    )
    parser.add_argument(
        "--max-sensors-per-group",
        type=int,
        default=None,
        help="Maximum number of sensors allowed per group (for future camera grouping functionality)",
    )
    parser.add_argument(
        "--n-sensor-groups",
        type=int,
        default=1,
        help="Number of sensor groups to create when group field is missing from input. If 1, assign all sensors to 'bev-sensor-1'. If >1, placeholder for future camera clustering algorithm (default: 1)",
    )
    parser.add_argument(
        "--scene-bounds",
        type=float,
        nargs=4,
        default=None,
        metavar=("MIN_X", "MIN_Y", "MAX_X", "MAX_Y"),
        help="Scene bounds (min_x, min_y, max_x, max_y) in meters to clip frustum polygons (optional)",
    )
    parser.add_argument(
        "--max-camera-distance",
        type=float,
        default=30.0,
        help="Maximum distance in meters from camera center to constrain frustum polygons (default: 30.0)",
    )
    parser.add_argument(
        "--visualize",
        action="store_true",
        help="Generate visualization of groups (uses black background if map file not provided)",
    )

    parser.add_argument(
        "--vis_separate_images",
        action="store_true",
        help="Generate separate visualization images per group instead of a single combined image (default: combined)",
    )

    args = parser.parse_args()

    # Validate argument dependencies
    if args.overwrite and args.output is not None:
        parser.error("--overwrite and --output are mutually exclusive")

    # Parse sensor names if provided (comma-separated)
    sensor_names = None
    if args.sensor_names:
        sensor_names = [name.strip() for name in args.sensor_names.split(",")]

    # Parse scene bounds if provided
    scene_bounds = None
    if args.scene_bounds:
        scene_bounds = tuple(args.scene_bounds)

    # Call the main function
    calculate_group_origins_from_calibration(
        input_calibration=args.input_calibration,
        output=args.output,
        overwrite=args.overwrite,
        map_file=args.map_file,
        dilation=args.dilation,
        height_range=tuple(args.height_range),
        prefer_existing_fov=args.prefer_existing_fov,
        sensor_names=sensor_names,
        max_sensors_per_group=args.max_sensors_per_group,
        n_sensor_groups=args.n_sensor_groups,
        scene_bounds=scene_bounds,
        max_camera_distance=args.max_camera_distance,
        visualize=args.visualize,
        vis_separate_images=args.vis_separate_images,
    )
