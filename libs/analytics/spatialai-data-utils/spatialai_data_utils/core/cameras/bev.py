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
BEV (Bird's Eye View) Camera Grouping and Origin Calculation

This module provides high-level functions for BEV camera grouping and origin calculation.
It orchestrates workflows that span multiple modules (grouping.py, origin.py, clustering.py)
without creating circular dependencies.
"""

import logging
import math
import sys
from pathlib import Path
from typing import List, Optional, Union

from spatialai_data_utils.core.cameras.utils import (
    discover_map_file,
    load_map_data,
    save_calibration_data,
)
from spatialai_data_utils.loaders.calibration import load_calib_json
from spatialai_data_utils.utils.string_utils import natural_sort_key
from spatialai_data_utils.core.cameras.filtering import filter_sensors_by_names
from spatialai_data_utils.core.cameras.origin import calculate_and_update_group_origins
from spatialai_data_utils.core.cameras.polygon import (
    extract_sensor_fov_polygons,
    calculate_scene_bounds_from_calibration,
    fill_sensor_fov_polygons,
)
from spatialai_data_utils.core.cameras.clustering import cluster_cameras_from_calibration
from spatialai_data_utils.core.cameras.grouping import group_cameras_from_calibration
from spatialai_data_utils.core.cameras.group_utils import add_group_info_to_sensors
from spatialai_data_utils.core.cameras.calibration_fields import add_region_field, update_tripwire_roi_groups

logger = logging.getLogger(__name__)


def calculate_group_origins_from_calibration(
    input_calibration,
    output=None,
    overwrite=False,
    map_file=None,
    dilation=1.0,
    height_range=(1.0, 3.0),
    prefer_existing_fov=False,
    sensor_names=None,
    visualize=False,
    vis_separate_images=False,
    # Grouping parameters (used if groups are missing)
    n_sensor_groups=1,
    max_sensors_per_group=None,
    scene_bounds=None,
    max_camera_distance=30.0,
):
    """
    Calculate BEV group origins and dimensions for camera groups from calibration file.

    This function processes a calibration file with camera groups and calculates the
    BEV (Bird's Eye View) origin and dimensions for each group. If group information
    is missing and n_sensor_groups=1, all sensors are assigned to a single group.
    For n_sensor_groups > 1, run the camera clustering algorithm first.

    :param input_calibration: Path to scene directory containing calibration.json,
        or direct path to a calibration JSON file.
    :type input_calibration: str
    :param output: Output calibration file path (default: input_with_origins.json).
    :type output: str or None
    :param overwrite: Overwrite the input calibration file (mutually exclusive with output).
    :type overwrite: bool
    :param map_file: Path to map image for visualization (required for visualize=True).
    :type map_file: str or None
    :param dilation: Dilation distance in meters for group bounds calculation.
    :type dilation: float
    :param height_range: Height range (min, max) in meters for ground plane intersection.
    :type height_range: tuple of (float, float)
    :param prefer_existing_fov: Prefer existing FOV from calibration file over frustum calculation.
    :type prefer_existing_fov: bool
    :param sensor_names: Filter to process only specified sensor names (list of sensor IDs).
    :type sensor_names: list of str or None
    :param visualize: Generate visualization of groups on map (requires map_file).
    :type visualize: bool
    :param n_sensor_groups: Number of sensor groups when group field is missing. If 1 (default),
        assigns all sensors to 'bev-sensor-1'. If >1, requires running camera clustering first.
    :type n_sensor_groups: int
    :param max_sensors_per_group: Maximum number of sensors per group (validation only).
    :type max_sensors_per_group: int or None
    :param scene_bounds: Optional (min_x, min_y, max_x, max_y) to clip frustum polygons.
    :type scene_bounds: tuple or None
    :param max_camera_distance: Maximum distance for frustum calculation (default: 30.0m).
    :type max_camera_distance: float
    :return: Path to the output calibration file.
    :rtype: Path
    :raises SystemExit: If validation fails or required data is missing.
    """
    # Validate argument dependencies
    if overwrite and output is not None:
        logger.error("overwrite and output arguments are mutually exclusive")
        sys.exit(1)

    # Determine output file path
    input_path = Path(input_calibration)
    if overwrite:
        if input_path.is_dir():
            output_path = input_path / "calibration.json"
        else:
            output_path = input_path
    elif output is None:
        if input_path.is_dir():
            output_path = input_path / "calibration_with_origins.json"
        else:
            output_path = input_path.parent / f"{input_path.stem}_with_origins.json"
    else:
        output_path = Path(output)

    # Load calibration data
    logger.info(f"Loading calibration data from: {input_calibration}")
    try:
        calibration_data = load_calib_json(
            input_path, load_original=True, validate=True,
        )
    except Exception as e:
        logger.error(f"Error loading calibration file: {e}")
        sys.exit(1)

    if "sensors" not in calibration_data:
        logger.error("Calibration file must contain 'sensors' field")
        sys.exit(1)

    original_sensor_count = len(calibration_data["sensors"])
    logger.info(f"Loaded {original_sensor_count} sensors")

    # Filter sensors by name if specified
    if sensor_names is not None:
        calibration_data = filter_sensors_by_names(calibration_data, sensor_names)
        if calibration_data is None:
            sys.exit(1)

        # If sensor count changed, clear groups so they get recalculated
        if len(calibration_data["sensors"]) < original_sensor_count:
            cleared = 0
            for sensor in calibration_data["sensors"]:
                if "group" in sensor:
                    del sensor["group"]
                    cleared += 1
            if cleared > 0:
                logger.info(
                    f"Cleared group info from {cleared} sensor(s) - "
                    "groups will be recalculated for filtered set"
                )

    # Validate: if n_sensor_groups=1 and max_sensors_per_group is set,
    # check that the number of cameras doesn't exceed the limit
    if n_sensor_groups == 1 and max_sensors_per_group is not None:
        num_cameras = len(calibration_data["sensors"])
        if num_cameras > max_sensors_per_group:
            logger.error(
                f"Number of cameras ({num_cameras}) exceeds maximum sensors per group "
                f"({max_sensors_per_group}) when n_sensor_groups=1."
            )
            logger.error(
                "Either increase max_sensors_per_group, set n_sensor_groups > 1, "
                "or filter cameras using sensor_names parameter."
            )
            sys.exit(1)

    # Check if all sensors have group information
    # Note: If ANY sensor is missing group field, ALL sensors will be reassigned to groups
    sensors_without_groups = [
        sensor["id"] for sensor in calibration_data["sensors"] if "group" not in sensor
    ]

    # Validate existing groups if all sensors have group information
    if not sensors_without_groups:
        # Count sensors per group
        group_sensor_counts = {}
        for sensor in calibration_data["sensors"]:
            group_name = sensor["group"]["name"]
            group_sensor_counts[group_name] = group_sensor_counts.get(group_name, 0) + 1

        existing_group_count = len(group_sensor_counts)

        # Validation 1: Check if existing group count matches n_sensor_groups
        if existing_group_count != n_sensor_groups:
            logger.error(
                f"Existing groups count ({existing_group_count}) does not match "
                f"n_sensor_groups parameter ({n_sensor_groups})."
            )
            logger.error(f"Existing groups: {list(group_sensor_counts.keys())}")
            logger.error(
                "Either update n_sensor_groups to match existing groups, "
                "or clear group info to re-cluster."
            )
            sys.exit(1)

        # Validation 2: Check max_sensors_per_group for each existing group
        if max_sensors_per_group is not None:
            groups_exceeding_limit = {
                name: count
                for name, count in group_sensor_counts.items()
                if count > max_sensors_per_group
            }
            if groups_exceeding_limit:
                logger.error(
                    f"The following groups exceed max_sensors_per_group ({max_sensors_per_group}):"
                )
                for group_name, count in groups_exceeding_limit.items():
                    logger.error(f"  - {group_name}: {count} sensors")
                logger.error(
                    "Either increase max_sensors_per_group or re-cluster with different parameters."
                )
                sys.exit(1)

        logger.info(
            f"Validated {existing_group_count} existing group(s) with sensor counts: "
            f"{group_sensor_counts}"
        )

    if sensors_without_groups:
        logger.warning(
            f"Found {len(sensors_without_groups)} sensor(s) without 'group' information: {sensors_without_groups}"
        )

        # Handle grouping based on n_sensor_groups parameter
        if n_sensor_groups == 1:
            # Assign all sensors to bev-sensor-1
            logger.info("Assigning all sensors to 'bev-sensor-1' (n_sensor_groups=1)")
            for sensor in calibration_data["sensors"]:
                sensor["group"] = {
                    "name": "bev-sensor-1",
                    "alias": "area-1",
                    "type": "bev",
                    # origin and dimensions will be calculated later
                }
            logger.info(
                f"✓ Assigned {len(calibration_data['sensors'])} sensors to 'bev-sensor-1'"
            )

            # Extract FOV polygons and calculate overlap ratio to assess multi-camera coverage quality
            logger.info("Extracting FOV polygons for sensor group...")
            polygons, overlap_ratio = extract_sensor_fov_polygons(
                calibration_data["sensors"],
                prefer_existing_fov=prefer_existing_fov,
                height_range=height_range,
                scene_bounds=scene_bounds,
                max_distance=max_camera_distance,
            )
            valid_polygon_count = len([p for p in polygons if p is not None])
            logger.info(
                f"Extracted {valid_polygon_count}/{len(polygons)} valid FOV polygons"
            )
            logger.info(f"Overlap ratio for 'bev-sensor-1': {overlap_ratio:.2%}")

            # Warn if overlap is too low
            if overlap_ratio < 0.2:
                logger.warning(
                    f"⚠️  Low overlap detected! Only {overlap_ratio:.1%} of the coverage area is visible to multiple cameras."
                )
                logger.warning(
                    "This may result in poor multi-camera tracking performance."
                )
                logger.warning(
                    "Consider repositioning cameras or adjusting n_sensor_groups for better coverage."
                )
        else:
            # Camera clustering algorithm must be run separately first
            logger.error(
                f"n_sensor_groups={n_sensor_groups} specified, but camera clustering must be run first."
            )
            logger.error(
                "Please run the camera clustering algorithm before using this feature:"
            )
            logger.error(
                "  python tools/camera_grouping/create_camera_clusters.py --help"
            )
            sys.exit(1)

    # Calculate/update origins for all groups (whether they existed or were just assigned)
    logger.info("=" * 80)
    logger.info("Calculating BEV Group Origins and Dimensions")
    logger.info("=" * 80)

    calibration_data = calculate_and_update_group_origins(
        calibration_data,
        dilation_distance=dilation,
        height_range=height_range,
        use_frustum=not prefer_existing_fov,
        scene_bounds=scene_bounds,
        max_distance=max_camera_distance,
    )

    # Fill/correct tripwire and ROI groups
    group_to_sensors = {}
    for sensor in calibration_data["sensors"]:
        gname = sensor["group"]["name"]
        group_to_sensors.setdefault(gname, set()).add(sensor["id"])
    update_tripwire_roi_groups(calibration_data, group_to_sensors)

    # Save updated calibration data
    logger.info("=" * 80)
    logger.info(f"Saving updated calibration to: {output_path}")
    logger.info("=" * 80)

    save_calibration_data(calibration_data, str(output_path))
    logger.info("✓ Successfully saved calibration file")

    # Generate visualization if requested
    if visualize:
        try:
            from spatialai_data_utils.visualization.camera_groups import plot_sensor_groups

            # Create a dedicated subfolder for visualization files
            vis_folder = output_path.parent / f"{output_path.stem}_vis"
            vis_folder.mkdir(parents=True, exist_ok=True)
            output_map_file = str(vis_folder / "map.png")

            if map_file is None:
                logger.info(
                    f"Generating visualization with black background in: {vis_folder}"
                )
            else:
                logger.info(f"Generating visualizations in: {vis_folder}")
            # Get scene name from input path
            scene_name = (
                input_path.parent.name if input_path.is_file() else input_path.name
            )
            plot_sensor_groups(
                calibration_data["sensors"],
                map_file,
                output_map_file,
                separate_images=vis_separate_images,
                scene_name=scene_name,
                algorithm_name="Origin Calculation",
            )
            logger.info("✓ Successfully generated visualization")
        except Exception as e:
            logger.error(f"Error generating visualization: {e}")

    logger.info("=" * 80)
    logger.info("✓ Completed!")
    logger.info("=" * 80)

    return output_path


def create_camera_clusters_from_calibration(
    input_calibration: str,
    max_camera_per_group: int,
    map_file: Optional[Path] = None,
    output: Optional[str] = None,
    output_suffix: str = "clustered",
    overwrite: bool = False,
    n_clusters: Optional[int] = None,
    start_camera_index: int = 0,
    dilation: float = 8.0,
    use_frustum: bool = False,
    max_camera_distance: float = 30.0,
    height_range: tuple = (1.0, 3.0),
    image_size: tuple = (1920, 1080),
    sensor_names: Optional[List[str]] = None,
    visualize: bool = False,
    label_camera_ids: bool = False,
    vis_separate_images: bool = False,
    mode: str = "densify",
    overlap_threshold: float = 0.0,
    distance_threshold: float = float("inf"),
    max_cascade_depth: int = 3,
) -> Path:
    """
    Create camera clusters from calibration file using spatial clustering algorithm.

    This function processes a calibration file and partitions all cameras into N
    spatially compact clusters based on FOV overlap and spatial proximity. It uses
    a greedy initialization followed by iterative refinement to minimize spatial
    scatter within each cluster.

    The workflow:
    1. Load calibration data from JSON file
    2. Optionally filter sensors by name
    3. Run clustering algorithm to partition cameras
    4. Add group information to sensors (origin, dimensions)
    5. Add region and place metadata
    6. Save updated calibration file
    7. Optionally generate visualization

    :param input_calibration: Path to calibration.json or directory containing calibration.json and Top.png (directly or in an ``images/`` subfolder).
    :type input_calibration: str
    :param output: Output calibration file path (default: input_clustered.json).
    :type output: str or None
    :param overwrite: Overwrite the input calibration file (mutually exclusive with output).
    :type overwrite: bool
    :param max_camera_per_group: Maximum cameras per cluster. Used to automatically calculate
        n_clusters based on sensor count.
    :type max_camera_per_group: int
    :param map_file: Path to map image for visualization/region metadata. Defaults to Top.png next to the calibration file/directory, or in an ``images/`` subfolder.
    :type map_file: Path or None
    :param n_clusters: Optional override for number of clusters. If not provided, calculated
        from total cameras / max_camera_per_group.
    :type n_clusters: int or None
    :param start_camera_index: Index of starting camera for seeding the clustering.
    :type start_camera_index: int
    :param dilation: Distance in meters to dilate cluster bounding boxes.
    :type dilation: float
    :param use_frustum: Use frustum-based FOV calculation instead of attributes.
    :type use_frustum: bool
    :param max_camera_distance: Maximum distance in meters for frustum calculation.
    :type max_camera_distance: float
    :param height_range: Height range (min, max) in meters for ground plane intersection.
    :type height_range: tuple of (float, float)
    :param image_size: Image dimensions (width, height) in pixels for frustum calculation.
    :type image_size: tuple of (int, int)
    :param sensor_names: Filter to process only specified sensor names (list of sensor IDs).
    :type sensor_names: list of str or None
    :param visualize: Generate visualization of clusters on map (requires map_file).
    :type visualize: bool
    :param mode: 'balanced' keeps clusters threshold-consistent, 'densify' prioritizes filling clusters.
    :type mode: str
    :param overlap_threshold: Minimum overlap percentage (0-100) required for membership.
    :type overlap_threshold: float
    :param distance_threshold: Maximum centroid distance for membership (meters).
    :type distance_threshold: float
    :param max_cascade_depth: Maximum recursion depth for performance-mode cascade reassignment.
    :type max_cascade_depth: int
    :return: Path to the output calibration file.
    :rtype: Path
    :raises SystemExit: If validation fails or required data is missing.
    """
    # Validate argument dependencies
    try:
        if overwrite and output is not None:
            raise ValueError("overwrite and output arguments are mutually exclusive")
    except Exception:
        logger.exception("Invalid argument combination for output handling.")
        sys.exit(1)

    # Determine output file path
    input_path = Path(input_calibration)
    if overwrite:
        if input_path.is_dir():
            output_path = input_path / "calibration.json"
        else:
            output_path = input_path
    elif output is None:
        if input_path.is_dir():
            output_path = input_path / f"calibration_{output_suffix}.json"
        else:
            output_path = input_path.parent / f"calibration_{output_suffix}.json"
    else:
        output_path = Path(output)
        if input_path.is_dir() and output_path.suffix == "":
            output_path = output_path / f"calibration_{output_suffix}.json"

    # Load calibration data
    logger.info("=" * 80)
    logger.info("Camera Clustering Pipeline")
    logger.info("=" * 80)
    logger.info(f"Loading calibration data from: {input_calibration}")

    # Load calibration data
    try:
        calibration_data = load_calib_json(
            input_path, load_original=True, validate=True,
        )
    except Exception:
        logger.exception("Error loading calibration file: %s", input_calibration)
        sys.exit(1)

    # Validate calibration data structure and inputs
    try:
        if "sensors" not in calibration_data:
            raise KeyError("Calibration file must contain 'sensors' field")

        logger.info(f"Loaded {len(calibration_data['sensors'])} sensors")

        # Filter sensors by name if specified
        if sensor_names is not None:
            calibration_data = filter_sensors_by_names(calibration_data, sensor_names)
            if calibration_data is None:
                raise ValueError("Sensor filtering failed or returned no sensors.")
            logger.info(f"Filtered to {len(calibration_data['sensors'])} sensors")

        # Validate and calculate n_clusters based on max_camera_per_group
        num_sensors = len(calibration_data["sensors"])
        # Save original sensor IDs for summary (before any modifications)
        original_sensor_ids = []
        for i, s in enumerate(calibration_data["sensors"]):
            if "id" not in s:
                raise KeyError(f"Sensor entry at index {i} is missing an 'id' field.")
            original_sensor_ids.append(s["id"])

        # 1. Validate n_clusters if explicitly provided
        if n_clusters is not None and n_clusters < 1:
            raise ValueError("n_clusters must be at least 1")
    except Exception:
        logger.exception("Calibration validation failed for %s", input_calibration)
        sys.exit(1)

    # Auto-discover Top.png (scene root or images/ subfolder) if not provided
    if map_file is None:
        discovered_map = discover_map_file(input_path)
        if discovered_map is not None:
            map_file = discovered_map
            logger.info(f"Auto-discovered map file: {map_file}")

    map_file, map_image, map_width, map_height = load_map_data(map_file)

    # Track single cluster mode to skip clustering algorithm
    single_cluster_mode = False

    # 2. If n_clusters is None, auto-calculate from max_camera_per_group
    if n_clusters is None:
        n_clusters = math.ceil(num_sensors / max_camera_per_group)
        logger.info(
            f"Auto-calculated n_clusters: {num_sensors} sensors / {max_camera_per_group} max per group = {n_clusters} clusters"
        )
        if n_clusters == 1:
            # Auto-calculated to single cluster - skip clustering algorithm
            logger.info(
                f"Single cluster mode: all {num_sensors} sensors will be assigned to bev-sensor-1"
            )
            single_cluster_mode = True
    elif n_clusters == 1:
        # Single cluster mode: validate max_camera_per_group constraint
        try:
            if num_sensors > max_camera_per_group:
                raise ValueError(
                    f"Number of sensors ({num_sensors}) exceeds max_camera_per_group ({max_camera_per_group}) "
                    f"but n_clusters is 1. Either increase max_camera_per_group or set n_clusters > 1."
                )
        except Exception:
            logger.exception(
                "Cluster size validation failed for n_clusters=1 in %s",
                input_calibration,
            )
            sys.exit(1)
        else:
            # All cameras fit in a single cluster - will skip clustering algorithm
            logger.info(
                f"Single cluster mode: all {num_sensors} sensors will be assigned to bev-sensor-1 "
                f"(within max_camera_per_group={max_camera_per_group})"
            )
            single_cluster_mode = True
    else:
        # 3. n_clusters > 1: calculate and warn if override needed
        calculated_clusters = math.ceil(num_sensors / max_camera_per_group)
        if calculated_clusters > n_clusters:
            logger.warning(
                f"max_camera_per_group ({max_camera_per_group}) requires at least {calculated_clusters} clusters "
                f"for {num_sensors} sensors, but n_clusters was set to {n_clusters}. "
                f"Using {calculated_clusters} clusters instead."
            )
            n_clusters = calculated_clusters
        logger.info(
            f"Using max_camera_per_group={max_camera_per_group}: {num_sensors} sensors -> {n_clusters} clusters"
        )

    # Validate n_clusters doesn't exceed sensor count
    try:
        if n_clusters > num_sensors:
            raise ValueError(
                f"Requested n_clusters ({n_clusters}) exceeds sensor count ({num_sensors})."
            )
    except Exception:
        logger.exception(
            "Cluster count validation failed for calibration at %s", input_calibration
        )
        sys.exit(1)

    # Calculate scene bounds from map image if available
    scene_bounds = None
    if map_file and map_width is not None and map_height is not None:
        scene_bounds = calculate_scene_bounds_from_calibration(
            calibration_data, map_width=map_width, map_height=map_height
        )
        logger.info(f"Loaded map image: {map_width}x{map_height} pixels")
        logger.info(f"Scene bounds: {scene_bounds}")
    else:
        # Fall back to calibration-derived bounds when map is missing or unreadable
        scene_bounds = calculate_scene_bounds_from_calibration(calibration_data)

    # Fill empty FOV polygons using frustum calculation
    logger.info("=" * 80)
    logger.info("Processing FOV Polygons")
    logger.info("=" * 80)

    filled_count, skipped_count, missing_count = fill_sensor_fov_polygons(
        calibration_data["sensors"],
        use_frustum=use_frustum,
        scene_bounds=scene_bounds,
        max_camera_distance=max_camera_distance,
        height_range=height_range,
        image_size=image_size,
    )

    logger.info(
        f"✓ Filled {filled_count} FOV polygons, skipped {skipped_count} existing"
    )
    if missing_count > 0:
        logger.warning(
            f"⚠️  {missing_count} sensors missing 'fieldOfViewPolygon' attribute"
        )

    # Build cluster_list - either via algorithm or direct assignment for single cluster
    if single_cluster_mode:
        # Skip clustering algorithm - assign all sensors to a single cluster
        logger.info("=" * 80)
        logger.info("Single Cluster Mode - Skipping Clustering Algorithm")
        logger.info("=" * 80)
        cluster_list = [list(range(num_sensors))]
        logger.info(f"✓ Assigned all {num_sensors} sensors to bev-sensor-1")
    else:
        # Run clustering algorithm
        logger.info("=" * 80)
        logger.info("Running Camera Clustering Algorithm")
        logger.info("=" * 80)
        logger.info("Parameters:")
        logger.info(f"  n_clusters: {n_clusters}")
        logger.info(f"  start_camera_index: {start_camera_index}")
        logger.info(f"  use_frustum: {use_frustum}")
        logger.info(f"  max_camera_distance: {max_camera_distance}m")
        logger.info(f"  height_range: {height_range}m")
        logger.info(f"  image_size: {image_size}px")
        logger.info(f"  dilation: {dilation}m")
        logger.info(f"  mode: {mode}")
        logger.info(f"  overlap_threshold: {overlap_threshold}")
        logger.info(f"  distance_threshold: {distance_threshold}")
        logger.info(f"  max_cascade_depth: {max_cascade_depth}")

        clustering_result = cluster_cameras_from_calibration(
            calibration_data,
            n_clusters,
            start_camera_index=start_camera_index,
            use_frustum=use_frustum,
            scene_bounds=scene_bounds,
            max_camera_distance=max_camera_distance,
            height_range=height_range,
            image_size=image_size,
            max_cluster_size=max_camera_per_group,
            mode=mode,
            overlap_threshold=overlap_threshold,
            distance_threshold=distance_threshold,
            max_cascade_depth=max_cascade_depth,
        )

        # Check clustering result
        if clustering_result["n_clusters"] == 0:
            logger.error("Clustering failed: no clusters created")
            sys.exit(1)

        logger.info(f"✓ Created {clustering_result['n_clusters']} clusters")

        # Convert cluster result to list format for add_group_info_to_sensors
        cluster_list = []
        for cluster_id in sorted(clustering_result["clusters"].keys()):
            cluster_list.append(clustering_result["clusters"][cluster_id])

    # Add group information to sensors (origin, dimensions)
    logger.info("=" * 80)
    logger.info("Adding Group Information to Sensors")
    logger.info("=" * 80)

    new_sensors = add_group_info_to_sensors(
        calibration_data,
        cluster_list,
        dilation_distance=dilation,
        use_frustum=use_frustum,
        height_range=height_range,
        image_size=image_size,
        max_distance=max_camera_distance,
    )
    calibration_data["sensors"] = new_sensors

    # Fill/correct tripwire and ROI groups
    group_to_sensors = {}
    for sensor in new_sensors:
        gname = sensor["group"]["name"]
        group_to_sensors.setdefault(gname, set()).add(sensor["id"])
    update_tripwire_roi_groups(calibration_data, group_to_sensors)

    # Add region and place metadata if map file is available
    if map_file is not None and map_width is not None and map_height is not None:
        add_region_field(new_sensors, map_width, map_height)
        logger.info("✓ Added region metadata")
    else:
        logger.warning("Map dimensions unavailable; cannot add region metadata.")

    # Save updated calibration data
    logger.info("=" * 80)
    logger.info(f"Saving clustered calibration to: {output_path}")
    logger.info("=" * 80)

    save_calibration_data(calibration_data, str(output_path))
    logger.info("✓ Successfully saved calibration file")

    # Generate visualization if requested
    if visualize:
        try:
            from spatialai_data_utils.visualization.camera_groups import plot_sensor_groups

            # Create a dedicated subfolder for visualization files
            vis_folder = output_path.parent / f"{output_path.stem}_vis"
            vis_folder.mkdir(parents=True, exist_ok=True)
            output_map_file = str(vis_folder / "map.png")

            # Use map_file if it exists, otherwise use black background
            actual_map_file = (
                str(map_file)
                if map_file is not None and map_image is not None
                else None
            )
            if actual_map_file is None:
                logger.info(
                    "No map file available, using black background for visualization"
                )

            logger.info(f"Generating visualizations in: {vis_folder}")

            # Get scene name from input path
            scene_name = (
                input_path.parent.name if input_path.is_file() else input_path.name
            )
            plot_sensor_groups(
                calibration_data["sensors"],
                actual_map_file,
                output_map_file,
                label_ids=label_camera_ids,
                separate_images=vis_separate_images,
                scene_name=scene_name,
                algorithm_name="Camera Clustering",
            )
            logger.info("✓ Successfully generated visualization")
        except Exception as e:
            logger.error(f"Error generating visualization: {e}")

    # Print clustering summary
    logger.info("=" * 80)
    logger.info("CLUSTERING SUMMARY")
    logger.info("=" * 80)
    logger.info(f"Total cameras: {num_sensors}")
    logger.info(f"Number of clusters: {len(cluster_list)}")

    for cluster_idx, camera_indices in enumerate(cluster_list):
        camera_ids = [original_sensor_ids[i] for i in camera_indices]
        # Sort camera IDs naturally for better readability
        sorted_camera_ids = sorted(camera_ids, key=natural_sort_key)
        logger.info(f"  Cluster {cluster_idx + 1}: {len(camera_indices)} cameras")
        logger.info(f"    Cameras: {', '.join(sorted_camera_ids)}")

    logger.info("=" * 80)
    logger.info("✓ Completed!")
    logger.info("=" * 80)

    return output_path


def create_camera_groups_from_calibration(
    input_calibration: str,
    n_groups: int,
    cameras_per_group: Union[int, List[int]],
    map_file: Optional[Path] = None,
    output: Optional[str] = None,
    output_suffix: str = "grouped",
    start_camera_index: int = 0,
    dilation: float = 8.0,
    use_frustum: bool = False,
    max_camera_distance: float = 30.0,
    height_range: tuple = (1.0, 3.0),
    image_size: tuple = (1920, 1080),
    visualize: bool = False,
    label_camera_ids: bool = False,
    vis_separate_images: bool = True,
    overlap_threshold: float = 0.2,
    distance_threshold: float = float("inf"),
    randomize: bool = True,
    max_duplicate_retries: int = 5,
    random_seed: Optional[int] = None,
) -> Path:
    """
    Create camera groups from calibration file with duplication support.

    This function processes a calibration file and creates camera groups with
    specified cameras per group. Unlike clustering:
    - Cameras can appear in multiple groups (duplication allowed)
    - Each group has exactly its specified number of cameras
    - All cameras are guaranteed to appear in at least one group
    - Groups are spatially diverse (farthest-first seeding)

    The workflow:
    1. Load calibration data from JSON file
    2. Optionally filter sensors by name
    3. Run grouping algorithm with duplication support
    4. Add group information to sensors (origin, dimensions)
    5. Add region and place metadata
    6. Save updated calibration file
    7. Optionally generate visualization

    :param input_calibration: Path to calibration.json or directory containing calibration.json.
    :type input_calibration: str
    :param n_groups: Number of groups per size type.
    :type n_groups: int
    :param cameras_per_group: Number of cameras per group. Can be:
        - int: Create n_groups groups, all with this size
        - List[int]: Create n_groups groups for EACH size in the list
          (total groups = n_groups * len(list))
          Example: n_groups=2, cameras_per_group=[5, 8, 6] creates 6 groups
    :type cameras_per_group: int or List[int]
    :param map_file: Path to map image for visualization/region metadata.
    :type map_file: Path or None
    :param output: Output calibration file path.
    :type output: str or None
    :param output_suffix: Suffix for output files.
    :type output_suffix: str
    :param start_camera_index: Index of starting camera for seeding.
    :type start_camera_index: int
    :param dilation: Distance in meters to dilate group bounding boxes.
    :type dilation: float
    :param use_frustum: Use frustum-based FOV calculation.
    :type use_frustum: bool
    :param max_camera_distance: Maximum distance for frustum calculation.
    :type max_camera_distance: float
    :param height_range: Height range for ground plane intersection.
    :type height_range: tuple of (float, float)
    :param image_size: Image dimensions for frustum calculation.
    :type image_size: tuple of (int, int)
    :param visualize: Generate visualization of groups on map.
    :type visualize: bool
    :param label_camera_ids: Label camera IDs on visualization.
    :type label_camera_ids: bool
    :param overlap_threshold: Minimum FOV overlap (0-1) for group membership.
    :type overlap_threshold: float
    :param distance_threshold: Maximum centroid distance for membership.
    :type distance_threshold: float
    :param randomize: Add randomization to camera selection for variety.
    :type randomize: bool
    :param max_duplicate_retries: Maximum retries when a duplicate group is generated.
    :type max_duplicate_retries: int
    :param random_seed: Optional seed for random number generator for deterministic results.
    :type random_seed: int or None
    :return: Path to the output calibration file.
    :rtype: Path
    :raises SystemExit: If validation fails or required data is missing.
    """
    # Determine output file path
    input_path = Path(input_calibration)
    if output is None:
        if input_path.is_dir():
            output_path = input_path / f"calibration_{output_suffix}.json"
        else:
            output_path = input_path.parent / f"calibration_{output_suffix}.json"
    else:
        output_path = Path(output)
        if input_path.is_dir() and output_path.suffix == "":
            output_path = output_path / f"calibration_{output_suffix}.json"

    # Load calibration data
    logger.info("=" * 80)
    logger.info("Camera Grouping Pipeline (with Duplication Support)")
    logger.info("=" * 80)
    logger.info(f"Loading calibration data from: {input_calibration}")

    try:
        calibration_data = load_calib_json(
            input_path, load_original=True, validate=True,
        )
    except Exception:
        logger.exception("Error loading calibration file: %s", input_calibration)
        sys.exit(1)

    # Validate calibration data structure
    try:
        if "sensors" not in calibration_data:
            raise KeyError("Calibration file must contain 'sensors' field")

        logger.info(f"Loaded {len(calibration_data['sensors'])} sensors")

        num_sensors = len(calibration_data["sensors"])

        # Save original sensor IDs for summary
        original_sensor_ids = []
        for i, s in enumerate(calibration_data["sensors"]):
            if "id" not in s:
                raise KeyError(f"Sensor entry at index {i} is missing an 'id' field.")
            original_sensor_ids.append(s["id"])

        # Validate grouping parameters
        if n_groups <= 0:
            raise ValueError("n_groups must be positive")

        # Compute group_sizes based on cameras_per_group type
        if isinstance(cameras_per_group, int):
            if cameras_per_group <= 0:
                raise ValueError("cameras_per_group must be positive")
            # Single size: n_groups groups with same size
            group_sizes = [cameras_per_group] * n_groups
            total_groups = n_groups
        else:
            # List of sizes: n_groups groups for EACH size
            for i, size in enumerate(cameras_per_group):
                if size <= 0:
                    raise ValueError(
                        f"cameras_per_group[{i}] must be positive, got {size}"
                    )
            # Expand: for each size, create n_groups groups
            group_sizes = []
            for size in cameras_per_group:
                group_sizes.extend([size] * n_groups)
            total_groups = len(group_sizes)

        total_slots = sum(group_sizes)
        if total_slots < num_sensors:
            if isinstance(cameras_per_group, int):
                raise ValueError(
                    f"Insufficient group capacity: {n_groups} groups × {cameras_per_group} cameras = "
                    f"{total_slots} slots, but {num_sensors} cameras need coverage. "
                    f"Increase n_groups or cameras_per_group to at least {num_sensors} total slots."
                )
            else:
                raise ValueError(
                    f"Insufficient group capacity: {total_groups} groups "
                    f"({n_groups} groups × {len(cameras_per_group)} sizes) = "
                    f"{total_slots} slots, but {num_sensors} cameras need coverage. "
                    f"Increase n_groups or add larger sizes to cameras_per_group."
                )
        elif total_slots > num_sensors:
            avg_duplication = total_slots / num_sensors
            logger.info(
                f"Camera duplication required: {total_slots} slots for {num_sensors} cameras "
                f"(~{avg_duplication:.1f}× average)"
            )

    except Exception:
        logger.exception("Calibration validation failed for %s", input_calibration)
        sys.exit(1)

    # Auto-discover Top.png (scene root or images/ subfolder) if not provided
    if map_file is None:
        discovered_map = discover_map_file(input_path)
        if discovered_map is not None:
            map_file = discovered_map
            logger.info(f"Auto-discovered map file: {map_file}")

    map_file, map_image, map_width, map_height = load_map_data(map_file)

    # Calculate scene bounds
    scene_bounds = None
    if map_file and map_width is not None and map_height is not None:
        scene_bounds = calculate_scene_bounds_from_calibration(
            calibration_data, map_width=map_width, map_height=map_height
        )
        logger.info(f"Loaded map image: {map_width}x{map_height} pixels")
        logger.info(f"Scene bounds: {scene_bounds}")
    else:
        scene_bounds = calculate_scene_bounds_from_calibration(calibration_data)

    # Fill empty FOV polygons using frustum calculation
    logger.info("=" * 80)
    logger.info("Processing FOV Polygons")
    logger.info("=" * 80)

    filled_count, skipped_count, missing_count = fill_sensor_fov_polygons(
        calibration_data["sensors"],
        use_frustum=use_frustum,
        scene_bounds=scene_bounds,
        max_camera_distance=max_camera_distance,
        height_range=height_range,
        image_size=image_size,
    )

    logger.info(
        f"✓ Filled {filled_count} FOV polygons, skipped {skipped_count} existing"
    )
    if missing_count > 0:
        logger.warning(
            f"⚠️  {missing_count} sensors missing 'fieldOfViewPolygon' attribute"
        )

    # Run grouping algorithm
    logger.info("=" * 80)
    logger.info("Running Camera Grouping Algorithm")
    logger.info("=" * 80)
    logger.info("Parameters:")
    logger.info(f"  n_groups: {n_groups}")
    logger.info(f"  cameras_per_group: {cameras_per_group}")
    logger.info(f"  start_camera_index: {start_camera_index}")
    logger.info(f"  use_frustum: {use_frustum}")
    logger.info(f"  max_camera_distance: {max_camera_distance}m")
    logger.info(f"  height_range: {height_range}m")
    logger.info(f"  image_size: {image_size}px")
    logger.info(f"  dilation: {dilation}m")
    logger.info(f"  overlap_threshold: {overlap_threshold}")
    logger.info(f"  distance_threshold: {distance_threshold}")
    logger.info(f"  randomize: {randomize}")
    logger.info(f"  max_duplicate_retries: {max_duplicate_retries}")
    logger.info(f"  random_seed: {random_seed}")

    grouping_result = group_cameras_from_calibration(
        calibration_data,
        n_groups=n_groups,
        cameras_per_group=cameras_per_group,
        start_camera_index=start_camera_index,
        use_frustum=use_frustum,
        scene_bounds=scene_bounds,
        max_camera_distance=max_camera_distance,
        height_range=height_range,
        image_size=image_size,
        overlap_threshold=overlap_threshold,
        distance_threshold=distance_threshold,
        randomize=randomize,
        max_duplicate_retries=max_duplicate_retries,
        random_seed=random_seed,
    )

    # Check grouping result
    if grouping_result["n_groups"] == 0:
        logger.error("Grouping failed: no groups created")
        sys.exit(1)

    logger.info(f"✓ Created {grouping_result['n_groups']} groups")

    # Get group list
    group_list = grouping_result["group_list"]

    # Add group information to sensors (origin, dimensions)
    logger.info("=" * 80)
    logger.info("Adding Group Information to Sensors")
    logger.info("=" * 80)

    new_sensors = add_group_info_to_sensors(
        calibration_data,
        group_list,
        dilation_distance=dilation,
        use_frustum=use_frustum,
        height_range=height_range,
        image_size=image_size,
        max_distance=max_camera_distance,
    )
    calibration_data["sensors"] = new_sensors

    # Add region and place metadata if map file is available
    if map_file is not None and map_width is not None and map_height is not None:
        add_region_field(new_sensors, map_width, map_height)
        logger.info("✓ Added region metadata")
    else:
        logger.warning("Map dimensions unavailable; cannot add region metadata.")

    # Save updated calibration data
    logger.info("=" * 80)
    logger.info(f"Saving grouped calibration to: {output_path}")
    logger.info("=" * 80)

    save_calibration_data(calibration_data, str(output_path))
    logger.info("✓ Successfully saved calibration file")

    # Generate visualization if requested
    if visualize:
        try:
            from spatialai_data_utils.visualization.camera_groups import plot_sensor_groups

            # Create a dedicated subfolder for visualization files
            vis_folder = output_path.parent / f"{output_path.stem}_vis"
            vis_folder.mkdir(parents=True, exist_ok=True)
            output_map_file = str(vis_folder / "map.png")

            actual_map_file = (
                str(map_file)
                if map_file is not None and map_image is not None
                else None
            )
            if actual_map_file is None:
                logger.info(
                    "No map file available, using black background for visualization"
                )

            logger.info(f"Generating visualizations in: {vis_folder}")

            # Get scene name from input path
            scene_name = (
                input_path.parent.name if input_path.is_file() else input_path.name
            )
            plot_sensor_groups(
                calibration_data["sensors"],
                actual_map_file,
                output_map_file,
                label_ids=label_camera_ids,
                separate_images=vis_separate_images,
                scene_name=scene_name,
                algorithm_name="Camera Grouping",
            )
            logger.info("✓ Successfully generated visualization")
        except Exception as e:
            logger.error(f"Error generating visualization: {e}")

    # Print grouping summary
    logger.info("=" * 80)
    logger.info("GROUPING SUMMARY")
    logger.info("=" * 80)
    logger.info(f"Total cameras: {num_sensors}")
    logger.info(f"Number of groups: {len(group_list)}")
    logger.info(f"Cameras per group: {cameras_per_group}")

    # Calculate duplication statistics
    camera_assignments = grouping_result["camera_assignments"]
    assignment_counts = [len(groups) for groups in camera_assignments.values()]
    if assignment_counts:
        avg_assignments = sum(assignment_counts) / len(assignment_counts)
        max_assignments = max(assignment_counts)
        min_assignments = min(assignment_counts)
        logger.info(
            f"Camera duplication: min={min_assignments}, max={max_assignments}, "
            f"avg={avg_assignments:.1f} groups per camera"
        )

    for group_idx, camera_indices in enumerate(group_list):
        camera_ids = [original_sensor_ids[i] for i in camera_indices]
        # Sort camera IDs naturally for better readability
        sorted_camera_ids = sorted(camera_ids, key=natural_sort_key)
        logger.info(f"  Group {group_idx + 1}: {len(camera_indices)} cameras")
        logger.info(f"    Cameras: {', '.join(sorted_camera_ids)}")

    logger.info("=" * 80)
    logger.info("✓ Completed!")
    logger.info("=" * 80)

    return output_path
