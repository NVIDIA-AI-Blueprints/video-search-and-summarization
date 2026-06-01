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
Camera Grouping Script (with Duplication Support)

This script creates camera groups based on overlapping fields of view (FOV).
Unlike clustering which partitions cameras (each in exactly one cluster),
this grouping algorithm:
1. Allows cameras to appear in multiple groups (duplication)
2. Requires user-specified number of groups AND cameras per group
3. Supports multiple group size types (creates n_groups for each size)
4. Guarantees all cameras appear in at least one group
5. Uses farthest-first seeding for spatial diversity
6. Detects and regenerates duplicate groups automatically
7. Randomization by default; use --random_seed for deterministic results

The grouping algorithm:
1. Seed first group from start_camera_index
2. Seed each subsequent group from the farthest unselected camera
3. Build each group by selecting cameras with best FOV overlap/proximity
4. When unselected cameras are exhausted, duplicate from already-selected cameras
5. Each group has exactly its specified number of cameras
6. Check for duplicate groups and regenerate if found

Use cases:
- Multi-view reconstruction requiring overlapping camera groups
- Distributed processing with redundant camera coverage
- Ensuring all cameras contribute to at least one processing unit

Output files:
- calibration_<suffix>.json: Calibration file with group assignments
- map_plotted_<suffix>.png: Visualization of camera groups

Usage Examples:
    # Basic usage: 5 groups with 8 cameras each
    python create_camera_groups.py data/scene --n_groups 5 --cameras_per_group 8
    
    # Auto mode: automatically create groups with sizes 1, 2, 3, ..., n_sensors
    python create_camera_groups.py data/scene --auto
    
    # Multiple size types: 2 groups × 3 sizes = 6 total groups
    # (2 groups with 5 cameras, 2 groups with 8 cameras, 2 groups with 6 cameras)
    python create_camera_groups.py data/scene --n_groups 2 --cameras_per_group 5 8 6
    
    # With visualization
    python create_camera_groups.py data/scene --n_groups 5 --cameras_per_group 8 --visualize
    
    # Custom FOV overlap threshold
    python create_camera_groups.py data/scene --n_groups 5 --cameras_per_group 8 \\
        --min_overlap_threshold 0.3 --visualize
"""

import argparse
import json
import logging
from pathlib import Path

from spatialai_data_utils.core.cameras.bev import (
    create_camera_groups_from_calibration,
)

logger = logging.getLogger(__name__)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Create camera groups with duplication support based on overlapping fields of view.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Auto mode: create groups with sizes 1, 2, ..., min(n_sensors, 18) (default max=18)
  python %(prog)s data/scene --auto
  
  # Auto mode with custom max sensors per group (e.g., 12)
  python %(prog)s data/scene --auto --max_sensors_per_group 12
  
  # Create 5 groups with 8 cameras each
  python %(prog)s data/scene --n_groups 5 --cameras_per_group 8
  
  # Multiple size types: 2 groups × 3 sizes = 6 total groups
  # (2 groups with 5 cameras, 2 groups with 8 cameras, 2 groups with 6 cameras)
  python %(prog)s data/scene --n_groups 2 --cameras_per_group 5 8 6
  
  # With visualization
  python %(prog)s data/scene --n_groups 5 --cameras_per_group 8 --visualize
  
  # Custom overlap threshold
  python %(prog)s data/scene --n_groups 5 --cameras_per_group 8 --min_overlap_threshold 0.3

Notes:
  - Cameras can appear in multiple groups (duplication allowed)
  - All cameras are guaranteed to appear in at least one group
  - Groups are seeded from spatially diverse locations
  - Duplicate groups are detected and regenerated automatically
  - Randomization is applied by default; use --random_seed for deterministic results
  - In auto mode, max_sensors_per_group (default 18) limits the largest group size
  - Single value: creates n_groups groups, all with that size
  - Multiple values: creates n_groups groups for EACH size (total = n_groups × count)
        """,
    )

    # Required arguments
    parser.add_argument(
        "input_calibration",
        type=str,
        help="Path to calibration.json or directory containing calibration.json and Top.png",
    )

    parser.add_argument(
        "--auto",
        action="store_true",
        help="Auto mode: automatically set cameras_per_group to [1, 2, 3, ..., min(n_sensors, max_sensors_per_group)], "
        "creating groups for each possible size. Use with --n_groups to control "
        "how many groups per size (default: 1). Overrides --cameras_per_group only.",
    )

    parser.add_argument(
        "--n_groups",
        type=int,
        default=1,
        help="Number of groups to create per size (default: 1).",
    )

    parser.add_argument(
        "--cameras_per_group",
        type=int,
        nargs="+",
        default=None,
        help="Number of cameras per group. Single value: all groups have that size. "
        "Multiple values: creates n_groups for EACH size (total = n_groups × count). "
        "Example: --n_groups 2 --cameras_per_group 5 8 6 creates 6 groups "
        "(2×5-cam, 2×8-cam, 2×6-cam). Ignored when --auto is used.",
    )

    parser.add_argument(
        "--max_sensors_per_group",
        type=int,
        default=18,
        help="Maximum number of sensors per group (default: 18). "
        "In auto mode, limits cameras_per_group to [1, 2, ..., min(n_sensors, max_sensors_per_group)].",
    )

    # Output configuration
    parser.add_argument(
        "--output_suffix",
        type=str,
        default="grouped",
        help="Suffix for output files (default: 'grouped')",
    )

    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Optional: Output path for the grouped calibration file. If not provided, "
        "the output will be saved in the same directory as the input calibration file.",
    )

    parser.add_argument(
        "--map_file",
        type=str,
        default=None,
        help="Path to map image for visualization (uses black background if not provided)",
    )

    # Grouping parameters
    parser.add_argument(
        "--start_camera_index",
        type=int,
        default=0,
        help="Starting camera index for seeding the first group (default: 0)",
    )

    parser.add_argument(
        "--min_overlap_threshold",
        type=float,
        default=0.2,
        help="Minimum required FOV overlap (0-1) when evaluating camera membership (default: 0.2)",
    )

    parser.add_argument(
        "--max_distance_threshold",
        type=float,
        default=float("inf"),
        help="Maximum allowed centroid distance (meters) when evaluating camera membership (default: inf)",
    )

    # FOV calculation options
    parser.add_argument(
        "--prefer_existing_fov",
        action="store_true",
        help="Use existing FOV polygons in calibration file instead of calculating from frustum. "
        "Default is to calculate from frustum.",
    )

    parser.add_argument(
        "--max_camera_distance",
        type=float,
        default=30.0,
        help="Maximum effective distance in meters for frustum calculation (default: 30.0)",
    )

    parser.add_argument(
        "--height_range",
        type=float,
        nargs=2,
        default=[1.0, 3.0],
        metavar=("MIN", "MAX"),
        help="Height range (min, max) in meters for ground plane intersection (default: 1.0 3.0)",
    )

    parser.add_argument(
        "--image_size",
        type=int,
        nargs=2,
        default=[1920, 1080],
        metavar=("WIDTH", "HEIGHT"),
        help="Image dimensions (width, height) in pixels for frustum calculation (default: 1920 1080)",
    )

    # Group region parameters
    parser.add_argument(
        "--dilation",
        type=float,
        default=8.0,
        help="Buffer distance in meters for group bounding boxes (default: 8.0)",
    )

    # Visualization options
    parser.add_argument(
        "--visualize",
        action="store_true",
        help="Generate visualization of camera groups on the map",
    )

    parser.add_argument(
        "--vis_no_camera_id_labels",
        action="store_true",
        help="Disable drawing camera IDs on the visualization",
    )

    parser.add_argument(
        "--vis_combined",
        action="store_true",
        help="Generate a single combined visualization instead of separate images per group (default: separate images for grouping)",
    )

    parser.add_argument(
        "--no_randomize",
        action="store_true",
        help="Disable randomization in camera selection (default: randomization enabled)",
    )

    parser.add_argument(
        "--max_duplicate_retries",
        type=int,
        default=5,
        help="Maximum retries when a duplicate group is generated (default: 5)",
    )

    parser.add_argument(
        "--random_seed",
        type=int,
        default=None,
        help="Random seed for deterministic results (default: None for non-deterministic)",
    )

    args = parser.parse_args()

    # Configure logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    # Extract arguments
    input_calibration = args.input_calibration
    output_suffix = args.output_suffix
    output_path = args.output
    map_file = args.map_file
    start_camera_index = args.start_camera_index
    overlap_threshold = args.min_overlap_threshold
    distance_threshold = args.max_distance_threshold
    use_frustum = not args.prefer_existing_fov
    max_camera_distance = args.max_camera_distance
    height_range = tuple(args.height_range)
    image_size = tuple(args.image_size)
    dilation = args.dilation
    visualize = args.visualize
    label_camera_ids = not args.vis_no_camera_id_labels
    randomize = not args.no_randomize
    max_duplicate_retries = args.max_duplicate_retries
    random_seed = args.random_seed

    # Handle --auto mode: automatically determine cameras_per_group from scene
    n_groups = args.n_groups
    max_sensors_per_group = args.max_sensors_per_group
    
    if args.auto:
        # Load calibration to count sensors
        input_path = Path(input_calibration)
        if input_path.is_dir():
            calib_file = input_path / "calibration.json"
        else:
            calib_file = input_path
        
        with open(calib_file, "r") as f:
            calib_data = json.load(f)
        
        n_sensors = len(calib_data.get("sensors", []))
        if n_sensors == 0:
            logger.error("No sensors found in calibration file")
            raise SystemExit(1)
        
        # Auto mode: create groups with sizes 1, 2, 3, ..., min(n_sensors, max_sensors_per_group)
        max_group_size = min(n_sensors, max_sensors_per_group)
        cameras_per_group = list(range(1, max_group_size + 1))
        logger.info(f"Auto mode: detected {n_sensors} sensors in scene")
        logger.info(f"Auto mode: max_sensors_per_group = {max_sensors_per_group}")
        logger.info(f"Auto mode: cameras_per_group = [1, 2, ..., {max_group_size}]")
    else:
        # Handle cameras_per_group: single value -> int, multiple values -> list
        if args.cameras_per_group is None:
            logger.error("--cameras_per_group is required when --auto is not used")
            raise SystemExit(1)
        cameras_per_group_list = args.cameras_per_group
        if len(cameras_per_group_list) == 1:
            cameras_per_group = cameras_per_group_list[0]  # Single int for uniform sizes
        else:
            cameras_per_group = cameras_per_group_list  # List for variable sizes

    # Log configuration
    logger.info("=" * 80)
    logger.info("Camera Grouping (with Duplication Support)")
    logger.info("=" * 80)
    logger.info(f"Input: {input_calibration}")
    logger.info(f"Mode: {'auto' if args.auto else 'manual'}")
    logger.info(f"n_groups: {n_groups}")
    if isinstance(cameras_per_group, int):
        logger.info(f"cameras_per_group: {cameras_per_group}")
        logger.info(f"Total groups: {n_groups}")
    else:
        total_groups = n_groups * len(cameras_per_group)
        logger.info(
            f"cameras_per_group: {cameras_per_group} ({len(cameras_per_group)} sizes)"
        )
        logger.info(
            f"Total groups: {total_groups} ({n_groups} groups × {len(cameras_per_group)} sizes)"
        )
    logger.info(f"start_camera_index: {start_camera_index}")
    logger.info(f"overlap_threshold: {overlap_threshold}")
    logger.info(f"distance_threshold: {distance_threshold}")
    logger.info(f"use_frustum: {use_frustum}")
    logger.info(f"randomize: {randomize}")
    logger.info(f"max_duplicate_retries: {max_duplicate_retries}")
    logger.info(f"random_seed: {random_seed}")
    logger.info("=" * 80)

    # vis_separate_images: default True for grouping, --vis_combined sets it to False
    vis_separate_images = not args.vis_combined

    try:
        create_camera_groups_from_calibration(
            input_calibration=input_calibration,
            n_groups=n_groups,
            cameras_per_group=cameras_per_group,
            map_file=Path(map_file) if map_file else None,
            output=output_path,
            output_suffix=output_suffix,
            start_camera_index=start_camera_index,
            dilation=dilation,
            use_frustum=use_frustum,
            max_camera_distance=max_camera_distance,
            height_range=height_range,
            image_size=image_size,
            visualize=visualize,
            label_camera_ids=label_camera_ids,
            vis_separate_images=vis_separate_images,
            overlap_threshold=overlap_threshold,
            distance_threshold=distance_threshold,
            randomize=randomize,
            max_duplicate_retries=max_duplicate_retries,
            random_seed=random_seed,
        )
        logger.info("✓ Completed processing successfully!")
    except Exception as e:
        logger.exception(f"✗ Failed to process: {e}")
