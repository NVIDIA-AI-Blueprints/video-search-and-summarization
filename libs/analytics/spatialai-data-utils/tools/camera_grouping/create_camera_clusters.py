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
Camera Clustering Script

This script partitions cameras into spatially compact clusters based on FOV coverage
and spatial proximity. Unlike grouping which finds overlapping camera sets, clustering
assigns ALL cameras to exactly N clusters with minimal spatial scatter.

The clustering algorithm:
1. Greedy initialization: Build clusters by iteratively adding nearest/most-overlapping cameras
2. Unassigned handling: densify/balanced modes handle unassigned cameras (cascade or split)
3. Output: Each camera assigned to exactly one cluster

Use cases:
- Partition camera network into manageable sub-networks
- Create balanced camera assignments for distributed processing
- Group cameras by spatial regions for efficient querying

Output files:
- calibration_<suffix>.json: Calibration file with cluster assignments
- map_plotted_<suffix>.png: Visualization of camera clusters

Usage Examples:
    # Basic usage (visualization on by default)
    python create_camera_clusters.py data/scene --max_camera_per_group 10
    
    # Disable visualization
    python create_camera_clusters.py data/scene --max_camera_per_group 10 --no-visualize
    
    # Override auto-calculated n_clusters
    python create_camera_clusters.py data/scene --max_camera_per_group 10 --n_clusters 5
    
    # Custom settings with visualization disabled
    python create_camera_clusters.py data/scene \\
        --max_camera_per_group 8 \\
        --start_camera_index 5 \\
        --no-visualize
"""

import sys
import argparse
import logging

from spatialai_data_utils.core.cameras.bev import (
    create_camera_clusters_from_calibration,
)
from spatialai_data_utils.core.cameras.clustering import find_suggested_cluster_params

logger = logging.getLogger(__name__)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Create camera clusters by partitioning cameras into spatially compact groups.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Basic usage (visualization on by default)
  python %(prog)s data/scene --max_camera_per_group 10
  
  # Disable visualization
  python %(prog)s data/scene --max_camera_per_group 10 --no_visualize
  
  # Override auto-calculated n_clusters
  python %(prog)s data/scene --max_camera_per_group 10 --n_clusters 5
  
  # Custom settings with visualization disabled
  python %(prog)s data/scene --max_camera_per_group 8 --start_camera_index 5 --no_visualize
        """,
    )

    # Required arguments
    parser.add_argument(
        "input_calibration",
        type=str,
        help="Path to calibration.json or directory containing calibration.json and Top.png",
    )

    parser.add_argument(
        "--max_camera_per_group",
        type=int,
        required=True,
        help="Maximum cameras per cluster. Automatically calculates n_clusters based on total camera count. Takes priority over --n_clusters if both are provided",
    )

    parser.add_argument(
        "--map_file",
        type=str,
        default=None,
        help="Path to map image for visualization (uses black background if not provided)",
    )

    # Clustering parameters
    parser.add_argument(
        "--n_clusters",
        type=int,
        default=None,
        help="Optional: Override auto-calculated number of clusters. By default, n_clusters is computed from total cameras / max_camera_per_group",
    )

    # Frustum-based FOV calculation options
    parser.add_argument(
        "--prefer_existing_fov",
        action="store_true",
        help="Use existing FOV polygons in calibration file instead of calculating from frustum. Default is to calculate from frustum.",
    )

    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Optional: Output path for the clustered calibration file. If not provided, the output will be saved in the same directory as the input calibration file.",
    )

    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite the input calibration file if it exists",
    )

    parser.add_argument(
        "--start_camera_index",
        type=int,
        default=0,
        help="Starting camera index for seeding (default: 0)",
    )

    parser.add_argument(
        "--mode",
        type=str,
        choices=["balanced", "densify"],
        default="densify",
        help="Clustering mode: 'balanced' enforces thresholds/splitting, 'densify' prioritizes full clusters with cascade (default: densify)",
    )

    parser.add_argument(
        "--min_overlap_threshold",
        type=float,
        default=0.2,
        help="Minimum required FOV overlap (0-1) when evaluating camera membership",
    )

    parser.add_argument(
        "--max_distance_threshold",
        type=float,
        default=8.0,
        help="Maximum allowed centroid distance (meters) when evaluating camera membership",
    )

    parser.add_argument(
        "--max_cascade_depth",
        type=int,
        default=3,
        help="Maximum recursion depth for performance-mode cascade reassignment",
    )

    # Output configuration
    parser.add_argument(
        "--output_suffix",
        type=str,
        default="clustered",
        help="Suffix for output files (default: 'clustered')",
    )

    parser.add_argument(
        "--sensor_names",
        type=str,
        nargs="+",
        default=None,
        help="Optional: List of sensor names to use for clustering. If not provided, all sensors will be processed.",
    )

    parser.add_argument(
        "--dilation",
        type=float,
        default=8.0,
        help="Buffer distance in meters for cluster bounding boxes (default: 8.0)",
    )

    parser.add_argument(
        "--max_camera_distance",
        type=float,
        default=30.0,
        help="Maximum effective distance in meters for frustum calculation (prevents infinite rays)",
    )

    parser.add_argument(
        "--height_range",
        type=float,
        nargs=2,
        default=[1.0, 8.0],
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

    parser.add_argument(
        "--vis_no_camera_id_labels",
        action="store_true",
        help="Disable drawing camera IDs on the visualization.",
    )

    parser.add_argument(
        "--vis_separate_images",
        action="store_true",
        help="Generate separate visualization images per cluster instead of a single combined image (default: combined)",
    )

    parser.add_argument(
        "--disable_param_tuning",
        action="store_true",
        help=(
            "Disable auto-tune clustering parameters (start_camera_index, overlap_threshold, "
            "distance_threshold) using find_suggested_cluster_params and override inputs."
        ),
    )

    parser.add_argument(
        "--tuning_overlap_grid",
        type=float,
        nargs="+",
        default=None,
        help="Optional: custom overlap thresholds (0-1) to search when auto-tuning.",
    )

    parser.add_argument(
        "--tuning_distance_grid",
        type=float,
        nargs="+",
        default=None,
        help="Optional: custom centroid distance thresholds (meters) to search when auto-tuning.",
    )

    parser.add_argument(
        "--tuning_start_index_grid",
        type=int,
        nargs="+",
        default=None,
        help="Optional: seed camera indices to try when auto-tuning.",
    )

    parser.add_argument(
        "--tuning_start_index_seed",
        type=int,
        default=None,
        help="Random seed for auto-generated start camera indices when not providing --tuning_start_index_grid.",
    )

    parser.add_argument(
        "--tuning_workers",
        type=int,
        default=0,
        help="Number of parallel workers for auto-tuning (0=auto cpu_count, 1=disable parallelism).",
    )

    args = parser.parse_args()

    # Configure logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    # Extract arguments
    input_calibration = args.input_calibration
    n_clusters = args.n_clusters
    max_camera_per_group = args.max_camera_per_group
    map_file = args.map_file
    use_frustum = not args.prefer_existing_fov
    start_camera_index = args.start_camera_index
    mode = args.mode
    overlap_threshold = args.min_overlap_threshold
    distance_threshold = args.max_distance_threshold
    max_cascade_depth = args.max_cascade_depth
    overwrite = args.overwrite
    output_path = args.output
    output_suffix = args.output_suffix
    sensor_names = args.sensor_names
    dilation = args.dilation
    max_camera_distance = args.max_camera_distance
    height_range = tuple(args.height_range)
    image_size = tuple(args.image_size)

    # Process the dataset
    logger.info(f"{'=' * 80}")
    logger.info(f"Processing dataset: {input_calibration}")
    logger.info(f"max_camera_per_group: {max_camera_per_group}")
    if n_clusters is not None:
        logger.info(f"n_clusters (for reference only): {n_clusters}")
    logger.info(f"Start camera index: {start_camera_index}")
    logger.info(f"{'=' * 80}\n")

    if not args.disable_param_tuning:
        logger.info("Auto-tuning cluster parameters...")
        # Map tune_* CLI options to the expected sweep args
        args.overlap_grid = args.tuning_overlap_grid
        args.distance_grid = args.tuning_distance_grid
        args.start_index_grid = args.tuning_start_index_grid
        args.start_index_seed = args.tuning_start_index_seed
        args.workers = args.tuning_workers

        cluster_params = find_suggested_cluster_params(args)
        if not cluster_params:
            logger.error("No successful clustering results.")
            sys.exit(1)

        best = cluster_params[0]
        logger.log(
            logging.INFO,
            "Suggested cluster parameters: start_camera_index=%s, overlap_threshold=%.3f, distance_threshold=%.2f, "
            "score=%.3f, unassigned=%d, overflow=%.1f, scatter_mean=%.3f, n_clusters=%d",
            best["start_camera_index"],
            best["overlap_threshold"],
            best["distance_threshold"],
            best["score"],
            best["unassigned_count"],
            best["overflow"],
            best["scatter_mean"],
            best["n_clusters"],
        )

        start_camera_index = best["start_camera_index"]
        overlap_threshold = best["overlap_threshold"]
        distance_threshold = best["distance_threshold"]

        logger.info(
            f"Using suggested cluster parameters: start_camera_index={start_camera_index} instead of inital value {args.start_camera_index}, overlap_threshold={overlap_threshold} instead of inital value {args.min_overlap_threshold}, distance_threshold={distance_threshold} instead of inital value {args.max_distance_threshold}"
        )

    try:
        # Construct output path from input_calibration and output_suffix
        create_camera_clusters_from_calibration(
            input_calibration=input_calibration,
            max_camera_per_group=max_camera_per_group,
            map_file=map_file,
            output=output_path,
            output_suffix=output_suffix,
            overwrite=overwrite,
            n_clusters=n_clusters,
            start_camera_index=start_camera_index,
            dilation=dilation,
            use_frustum=use_frustum,
            max_camera_distance=max_camera_distance,
            height_range=height_range,
            image_size=image_size,
            sensor_names=sensor_names,
            visualize=True,
            label_camera_ids=not args.vis_no_camera_id_labels,
            vis_separate_images=args.vis_separate_images,
            mode=mode,
            overlap_threshold=overlap_threshold,
            distance_threshold=distance_threshold,
            max_cascade_depth=max_cascade_depth,
        )
        logger.info(f"✓ Completed processing: {input_calibration}\n")
    except Exception as e:
        logger.exception(f"✗ Failed to process {input_calibration}: {e}")
