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
BEV Group Origin Calculation Module

This module provides functions for calculating Bird's Eye View (BEV) group origins and
dimensions from camera field of view (FOV) data. It supports multiple approaches for
FOV generation and handles both attribute-based and frustum-based polygon computation.

Key Features:
- Calculate group origin (centroid) and bounding box from camera FOVs
- Support for multiple FOV sources:
  * Pre-computed polygons from calibration attributes
  * Frustum-based calculation from camera intrinsic/extrinsic matrices
  * Hybrid approach with automatic fallback
- Polygon dilation for robust boundary estimation
- Batch processing of all groups in calibration data
- Automatic image size detection from calibration attributes

Main Functions:
- calculate_group_origin: Calculate origin from attribute-based FOV polygons
- calculate_group_origin_from_frustum: Calculate origin using frustum intersection
- calculate_group_origin_hybrid: Flexible approach with automatic FOV source selection
- calculate_and_update_group_origins: Batch process all groups in calibration data

Origin Calculation:
- Origin: Centroid (center point) of the union of all camera FOVs in the group
- Dimensions: Bounding box [x_min, y_min, x_max, y_max] of the FOV union
- Dilation: Expand FOVs by specified distance to create buffer zones

Coordinate System:
- World coordinates in meters
- Origin represents the BEV coordinate system center for the group
- Dimensions define the spatial extent of the group's coverage area

Typical Workflow:
1. Load calibration data with grouped sensors
2. For each group, extract or calculate FOV polygons for member cameras
3. Union and dilate the polygons to form group coverage area
4. Calculate centroid (origin) and bounding box (dimensions)
5. Update group metadata with origin and dimensions
6. Save updated calibration data

This module is essential for BEV multi-camera tracking systems as it defines the
coordinate frames and spatial extents for each camera group.
"""

import logging

from shapely.geometry import MultiPolygon
from shapely.ops import unary_union

from spatialai_data_utils.core.cameras.utils import extract_camera_matrices
from spatialai_data_utils.core.cameras.polygon import (
    find_field_of_view_polygon,
    parse_polygon,
    calculate_camera_frustum_polygon,
    extract_sensor_fov_polygons,
    update_sensor_fov_attributes,
)

logger = logging.getLogger(__name__)


def calculate_group_origin(sensors, sensor_ids, dilation_distance=1.0):
    """
    Calculate the origin and dimensions of a group of sensors.

    This function takes a dictionary of sensors and a list of sensor IDs.
    It finds the field of view polygon for each sensor and dilates it by a
    constant distance.

    :param sensors: Dictionary of sensors.
    :type sensors: dict
    :param sensor_ids: List of sensor IDs.
    :type sensor_ids: list
    :param dilation_distance: Distance to dilate the polygon.
    :type dilation_distance: float
    :return: Tuple of origin and dimensions.
    :rtype: tuple
    """
    group_union_polygon = []
    for sensor_id in sensor_ids:
        polygon_str = find_field_of_view_polygon(sensors[sensor_id]["attributes"])
        polygon = parse_polygon(polygon_str)
        if polygon is None:
            continue

        if isinstance(polygon, MultiPolygon):
            buffered_polygons = [
                poly.buffer(dilation_distance) for poly in polygon.geoms
            ]
            polygon = MultiPolygon(buffered_polygons)
        else:
            polygon = polygon.buffer(dilation_distance)
        group_union_polygon.append(polygon)

    group_union_polygon = unary_union(group_union_polygon)
    if group_union_polygon:
        # Calculate the centroid (center)
        centroid = group_union_polygon.centroid
        center_x, center_y = centroid.x, centroid.y
        # Calculate the bounding box (x_min, y_min, x_max, y_max)
        x_min, y_min, x_max, y_max = group_union_polygon.bounds
    else:
        return [0, 0], [0, 0, 0, 0]

    return [center_x, center_y], [x_min, y_min, x_max, y_max]


def calculate_group_origin_from_frustum(
    sensors,
    sensor_ids,
    height_range=(1.0, 3.0),
    image_size=(1920, 1080),
    dilation_distance=1.0,
):
    """
    Calculate the origin and dimensions of a group of sensors using camera frustum intersection.

    This function calculates the field of view polygon for each sensor by intersecting
    camera frustums with ground planes at different heights. It's designed for cases
    where polygon data is not available in calibration files.
    
    Generated FOV polygons are automatically saved to sensor attributes for future use.

    :param sensors: Dictionary of sensors containing calibration data.
    :type sensors: dict
    :param sensor_ids: List of sensor IDs to process.
    :type sensor_ids: list
    :param height_range: Tuple of (min_height, max_height) for ground plane intersection (default: 1.0-3.0m).
    :type height_range: tuple
    :param image_size: Tuple of (width, height) for image dimensions.
    :type image_size: tuple
    :param dilation_distance: Distance to dilate the polygon.
    :type dilation_distance: float
    :return: Tuple of origin and dimensions.
    :rtype: tuple
    """
    group_union_polygon = []
    generated_polygons = []  # Keep track of original polygons (before buffering) for updating attributes
    sensors_list = []  # Keep track of sensors in order

    for sensor_id in sensor_ids:
        sensor = sensors[sensor_id]
        sensors_list.append(sensor)
        intrinsic, extrinsic = extract_camera_matrices(sensor)

        if intrinsic is not None and extrinsic is not None:
            try:
                polygon = calculate_camera_frustum_polygon(
                    intrinsic, extrinsic, height_range, image_size
                )
                if polygon is not None:
                    logger.info(
                        f"Generated polygon from camera matrices for sensor {sensor_id}"
                    )
                    
                    # Store original polygon for attribute update
                    generated_polygons.append(polygon)

                    # Apply buffering for union calculation
                    buffered_polygon = polygon
                    if isinstance(polygon, MultiPolygon):
                        buffered_polygons = [
                            poly.buffer(dilation_distance) for poly in polygon.geoms
                        ]
                        buffered_polygon = MultiPolygon(buffered_polygons)
                    else:
                        buffered_polygon = polygon.buffer(dilation_distance)
                    group_union_polygon.append(buffered_polygon)
                else:
                    logger.warning(f"Failed to generate polygon for sensor {sensor_id}")
                    generated_polygons.append(None)
            except Exception as e:
                logger.error(
                    f"Error calculating frustum polygon for sensor {sensor_id}: {e}"
                )
                generated_polygons.append(None)
                continue
        else:
            logger.warning(f"Could not extract camera matrices for sensor {sensor_id}")
            generated_polygons.append(None)
            continue
    
    # Update sensor attributes with generated frustum polygons
    if generated_polygons:
        updated_count = update_sensor_fov_attributes(
            sensors_list,
            generated_polygons,
            update_only_if_frustum_generated=True
        )
        if updated_count > 0:
            logger.debug(f"Updated {updated_count} sensor(s) in group with frustum-based FOV polygons")

    # Calculate union and bounds
    if group_union_polygon:
        group_union_polygon = unary_union(group_union_polygon)
        # Calculate the centroid (center)
        centroid = group_union_polygon.centroid
        center_x, center_y = centroid.x, centroid.y
        # Calculate the bounding box (x_min, y_min, x_max, y_max)
        x_min, y_min, x_max, y_max = group_union_polygon.bounds

        logger.info("Group origin calculated using frustum-based polygon generation")
        return [center_x, center_y], [x_min, y_min, x_max, y_max]
    else:
        logger.warning("No valid polygons found for any sensors in the group")
        return [0, 0], [0, 0, 0, 0]


def calculate_group_origin_hybrid(
    sensors,
    sensor_ids,
    height_range=(1.0, 3.0),
    image_size=(1920, 1080),
    dilation_distance=1.0,
    use_frustum=True,
    scene_bounds=None,
    max_distance=30.0,
):
    """
    Calculate the origin and dimensions of a group of sensors with hybrid approach.

    By default, this function uses frustum-based FOV generation. If use_frustum is False,
    it first tries to find the field of view polygon for each sensor from attributes,
    and falls back to frustum calculation if not available.

    :param sensors: Dictionary of sensors.
    :type sensors: dict
    :param sensor_ids: List of sensor IDs.
    :type sensor_ids: list
    :param height_range: Tuple of (min_height, max_height) for ground plane intersection.
    :type height_range: tuple
    :param image_size: Tuple of (width, height) for image dimensions when calculating frustum.
    :type image_size: tuple
    :param dilation_distance: Distance to dilate the polygon.
    :type dilation_distance: float
    :param use_frustum: If True (default), use frustum-based FOV generation. If False, try attributes first then fall back to frustum.
    :type use_frustum: bool
    :param scene_bounds: Optional (min_x, min_y, max_x, max_y) to clip frustum polygons.
    :type scene_bounds: tuple or None
    :param max_distance: Maximum distance in meters from camera center to constrain frustum (default: 30.0m).
    :type max_distance: float
    :return: Tuple of origin and dimensions.
    :rtype: tuple
    """
    group_union_polygon = []
    
    # Extract sensors list from dictionary for the given sensor_ids
    sensors_list = [sensors[sid] for sid in sensor_ids]
    
    # Extract FOV polygons using the reusable function
    # Note: prefer_existing_fov is the inverse of use_frustum
    polygons, _ = extract_sensor_fov_polygons(
        sensors_list,
        prefer_existing_fov=not use_frustum,
        height_range=height_range,
        scene_bounds=scene_bounds,
        max_distance=max_distance,
    )
    
    # Update sensor attributes with generated frustum polygons (if using frustum mode)
    if use_frustum:
        updated_count = update_sensor_fov_attributes(
            sensors_list,
            polygons,
            update_only_if_frustum_generated=True
        )
        if updated_count > 0:
            logger.debug(f"Updated {updated_count} sensor(s) in group with frustum-based FOV polygons")
    
    # Process polygons with dilation
    for polygon in polygons:
        if polygon is not None:
            if isinstance(polygon, MultiPolygon):
                buffered_polygons = [
                    poly.buffer(dilation_distance) for poly in polygon.geoms
                ]
                polygon = MultiPolygon(buffered_polygons)
            else:
                polygon = polygon.buffer(dilation_distance)
            group_union_polygon.append(polygon)

    # Calculate union and bounds
    if group_union_polygon:
        group_union_polygon = unary_union(group_union_polygon)
        # Calculate the centroid (center)
        centroid = group_union_polygon.centroid
        center_x, center_y = centroid.x, centroid.y
        # Calculate the bounding box (x_min, y_min, x_max, y_max)
        x_min, y_min, x_max, y_max = group_union_polygon.bounds

        if use_frustum:
            logger.info(
                "Group origin calculated using frustum-based polygon generation"
            )
        else:
            logger.info(
                "Group origin calculated using attribute-based FOV polygons"
            )

        return [center_x, center_y], [x_min, y_min, x_max, y_max]
    else:
        logger.warning("No valid polygons found for any sensors in the group")
        return [0, 0], [0, 0, 0, 0]


def calculate_and_update_group_origins(
    calibration_data,
    dilation_distance=1.0,
    height_range=(1.0, 3.0),
    image_size=(1920, 1080),
    use_frustum=True,
    scene_bounds=None,
    max_distance=30.0,
):
    """
    Calculate BEV group origins and dimensions for all groups in calibration data.
    
    When use_frustum=True, this function also updates sensor fieldOfViewPolygon attributes
    with the generated frustum-based polygons, ensuring they are persisted in the calibration data.

    :param calibration_data: Dictionary containing sensor calibration data. All sensors must have 'group' information.
    :type calibration_data: dict
    :param dilation_distance: Distance to dilate FOV polygons when calculating bounds (default: 1.0m).
    :type dilation_distance: float
    :param height_range: Tuple of (min_height, max_height) for ground plane intersection (default: (1.0, 3.0)).
    :type height_range: tuple
    :param image_size: Tuple of (width, height) for default image dimensions (default: (1920, 1080)). Used as fallback if not found in attributes.
    :type image_size: tuple
    :param use_frustum: If True (default), use frustum-based FOV generation and update sensor attributes. 
                        If False, try attributes first then fall back to frustum.
    :type use_frustum: bool
    :param scene_bounds: Optional (min_x, min_y, max_x, max_y) to clip frustum polygons.
    :type scene_bounds: tuple or None
    :param max_distance: Maximum distance in meters from camera center to constrain frustum (default: 30.0m).
    :type max_distance: float
    :return: Updated calibration data with group origins and dimensions (and updated FOV attributes if use_frustum=True).
    :rtype: dict
    :raises ValueError: If any sensor is missing 'group' information.

    Note: Uses hybrid approach. By default uses frustum-based FOV generation.
    If use_frustum=False, tries to get FOV polygons from attributes first,
    automatically falls back to frustum calculation if not available.
    Image size is extracted from calibration data, with fallback to the image_size parameter.
    When use_frustum=True, generated FOV polygons are saved to sensor attributes for future use.
    """
    # Group sensors by their group name
    groups_dict = {}
    for sensor in calibration_data["sensors"]:
        if "group" not in sensor:
            raise ValueError(
                f"Sensor {sensor['id']} has no group information. All sensors must have groups."
            )

        group_name = sensor["group"]["name"]
        if group_name not in groups_dict:
            groups_dict[group_name] = []
        groups_dict[group_name].append(sensor)

    logger.info(f"Found {len(groups_dict)} groups in calibration file")
    if use_frustum:
        logger.info("Using frustum-based FOV generation mode")
    else:
        logger.info(
            "Using hybrid mode: Will try FOV from attributes first, fall back to frustum if unavailable"
        )

    # Count sensors with FOV attributes before processing (if using frustum mode)
    sensors_with_fov_before = 0
    if use_frustum:
        for sensor in calibration_data["sensors"]:
            if "attributes" in sensor:
                for attr in sensor["attributes"]:
                    if isinstance(attr, dict) and attr.get("name") == "fieldOfViewPolygon":
                        sensors_with_fov_before += 1
                        break
    
    # Calculate origin and dimensions for each group
    for group_name, sensors_in_group in groups_dict.items():
        logger.info(
            f"Calculating origin for {group_name} ({len(sensors_in_group)} cameras)..."
        )

        # Create a temporary dict mapping sensor index to sensor data
        sensors_dict = {i: sensor for i, sensor in enumerate(sensors_in_group)}
        sensor_indices = list(range(len(sensors_in_group)))

        # Extract image size from the first sensor in the group, use parameter default if not found
        sensor_image_size = image_size  # Use parameter default
        if sensors_in_group:
            first_sensor = sensors_in_group[0]

            # Try to get image size from attributes
            if "attributes" in first_sensor:
                attrs = first_sensor["attributes"]
                frame_width = None
                frame_height = None

                # Check if attributes is a list (array of name-value pairs)
                if isinstance(attrs, list):
                    for attr in attrs:
                        if isinstance(attr, dict):
                            name = attr.get("name", "")
                            value = attr.get("value", "")
                            if name == "frameWidth" and value:
                                try:
                                    frame_width = int(value)
                                except (ValueError, TypeError):
                                    pass
                            elif name == "frameHeight" and value:
                                try:
                                    frame_height = int(value)
                                except (ValueError, TypeError):
                                    pass

                    if frame_width and frame_height:
                        sensor_image_size = (frame_width, frame_height)
                        logger.debug(
                            f"Using image size from attributes: {sensor_image_size[0]}x{sensor_image_size[1]}"
                        )
                # Check if attributes is a dict
                elif isinstance(attrs, dict):
                    if "frameWidth" in attrs and "frameHeight" in attrs:
                        try:
                            frame_width = int(attrs["frameWidth"])
                            frame_height = int(attrs["frameHeight"])
                            sensor_image_size = (frame_width, frame_height)
                            logger.debug(
                                f"Using image size from attributes: {sensor_image_size[0]}x{sensor_image_size[1]}"
                            )
                        except (ValueError, TypeError):
                            pass

        logger.debug(
            f"Image size: {sensor_image_size[0]}x{sensor_image_size[1]} for group {group_name}"
        )

        try:
            # Use hybrid approach based on use_frustum parameter
            origin, dimensions = calculate_group_origin_hybrid(
                sensors_dict,
                sensor_indices,
                height_range=height_range,
                image_size=sensor_image_size,
                dilation_distance=dilation_distance,
                use_frustum=use_frustum,
                scene_bounds=scene_bounds,
                max_distance=max_distance,
            )

            # Update all sensors in this group with the calculated origin and dimensions
            for sensor in sensors_in_group:
                sensor["group"]["origin"] = origin
                sensor["group"]["dimensions"] = dimensions

            logger.info(f"  Origin: [{origin[0]:.2f}, {origin[1]:.2f}]")
            logger.info(
                f"  Dimensions: [{dimensions[0]:.2f}, {dimensions[1]:.2f}, {dimensions[2]:.2f}, {dimensions[3]:.2f}]"
            )

        except Exception as e:
            logger.error(
                f"Error calculating origin for {group_name}: {e}", exc_info=True
            )
            # Set default values
            logger.warning(f"Setting default values for {group_name}")
            for sensor in sensors_in_group:
                sensor["group"]["origin"] = [0, 0]
                sensor["group"]["dimensions"] = [0, 0, 0, 0]

    # Report total FOV updates if using frustum mode
    if use_frustum:
        sensors_with_fov_after = 0
        for sensor in calibration_data["sensors"]:
            if "attributes" in sensor:
                for attr in sensor["attributes"]:
                    if isinstance(attr, dict) and attr.get("name") == "fieldOfViewPolygon":
                        sensors_with_fov_after += 1
                        break
        
        newly_updated = sensors_with_fov_after - sensors_with_fov_before
        if newly_updated > 0:
            logger.info(f"✓ Updated {newly_updated} sensor(s) with frustum-based FOV polygons")

    return calibration_data
