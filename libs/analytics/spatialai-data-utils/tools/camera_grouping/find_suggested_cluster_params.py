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
Parameter search helper for camera clustering.

Given a calibration assets directory and a max cameras-per-group constraint,
this script sweeps overlap/distance thresholds and seed indices to suggest
settings that produce compact, capacity-respecting clusters.

It reports the best-scoring configuration (lower is better) and a short
ranked list of alternatives so users can pick a reasonable starting point.
"""

import argparse
import logging

from spatialai_data_utils.core.cameras.clustering import find_suggested_cluster_params

logger = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(
        description="Grid search helper to suggest clustering thresholds.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "input_calibration",
        type=str,
        help="Path to dataset folder containing calibration.json (and optionally Top.png).",
    )
    parser.add_argument(
        "--max_camera_per_group",
        type=int,
        required=True,
        help="Capacity per cluster; also used to derive n_clusters when not provided.",
    )
    parser.add_argument(
        "--mode",
        choices=["balanced", "densify"],
        default="densify",
        help="Clustering mode to evaluate.",
    )
    parser.add_argument(
        "--prefer_existing_fov",
        action="store_true",
        help="Use existing FOV polygons in calibration file instead of calculating from frustum. Default is to calculate from frustum.",
    )
    parser.add_argument(
        "--max_camera_distance",
        type=float,
        default=30.0,
        help="Max distance (meters) for frustum calculation.",
    )
    parser.add_argument(
        "--height_range",
        type=float,
        nargs=2,
        default=[1.0, 8.0],
        metavar=("MIN", "MAX"),
        help="Height range (meters) for ground plane intersection.",
    )
    parser.add_argument(
        "--image_size",
        type=int,
        nargs=2,
        default=[1920, 1080],
        metavar=("WIDTH", "HEIGHT"),
        help="Image size used for frustum calculation.",
    )
    parser.add_argument(
        "--max_cascade_depth",
        type=int,
        default=3,
        help="Max recursion depth for densify-mode cascade.",
    )
    parser.add_argument(
        "--overlap_grid",
        type=float,
        nargs="+",
        help="List of overlap thresholds (0-1) to search; defaults to a small sweep if omitted.",
    )
    parser.add_argument(
        "--distance_grid",
        type=float,
        nargs="+",
        help="List of centroid distance thresholds (meters) to search; defaults to a small sweep if omitted.",
    )
    parser.add_argument(
        "--start_index_grid",
        type=int,
        nargs="+",
        help="Seed camera indices to try; defaults to the first few cameras.",
    )
    parser.add_argument(
        "--start_index_seed",
        type=int,
        default=None,
        help="Random seed for auto-generated start camera indices (enables random sampling); ignored if start_index_grid is provided.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose logging (default is quiet).",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=0,
        help="Number of parallel workers for the sweep (0=auto cpu_count, 1=disable parallelism).",
    )
    parser.add_argument(
        "--top_k",
        type=int,
        default=5,
        help="How many top candidates to print.",
    )

    # Default is quiet; user opts-in to verbose
    args = parser.parse_args()

    # Ensure this script always emits its own summaries at INFO level
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setLevel(logging.INFO)
        handler.setFormatter(
            logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
        )
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False

    results = find_suggested_cluster_params(args, verbose=args.verbose)
    if not results:
        logger.error("No successful clustering results.")
        return

    # Always print the ranked suggestions, regardless of verbose flag
    top_k = max(1, args.top_k)
    best = results[0]
    logger.log(logging.INFO, "Best parameters (lowest score):")
    logger.log(
        logging.INFO,
        "  start_camera_index=%s, overlap_threshold=%.3f, distance_threshold=%.2f, "
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

    if len(results) > 1:
        logger.log(logging.INFO, "Top %d candidates:", min(top_k, len(results)))
        for idx, entry in enumerate(results[:top_k], start=1):
            logger.log(
                logging.INFO,
                "  #%d: start=%s, overlap=%.3f, dist=%.2f | score=%.3f | "
                "unassigned=%d, overflow=%.1f, scatter=%.3f | n_clusters=%d",
                idx,
                entry["start_camera_index"],
                entry["overlap_threshold"],
                entry["distance_threshold"],
                entry["score"],
                entry["unassigned_count"],
                entry["overflow"],
                entry["scatter_mean"],
                entry["n_clusters"],
            )


if __name__ == "__main__":
    main()

