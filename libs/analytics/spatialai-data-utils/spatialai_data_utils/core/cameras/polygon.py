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
Polygon and Frustum Generation Module

This module contains functions for handling field of view (FOV) polygons and
generating frustum-based polygons from camera calibration data.

Functions for:
- Parsing polygon strings (WKT format) to Shapely objects
- Extracting FOV polygons from sensor attributes
- Calculating frustum polygons from camera intrinsic/extrinsic matrices
- Computing scene bounds for polygon clipping
"""

import logging
import numpy as np
from shapely import wkt
from shapely.geometry import Polygon

from spatialai_data_utils.core.cameras.utils import extract_camera_matrices

logger = logging.getLogger(__name__)

# Constants
FIELD_OF_VIEW_POLYGON_ATTR = "fieldOfViewPolygon"


def parse_polygon(poly_str):
    """
    Parse a polygon string into a Shapely Polygon object.

    This function takes a string representation of a polygon (typically in WKT format)
    and converts it into a Shapely Polygon object for geometric operations.

    :param poly_str: String representation of a polygon in WKT format.
    :type poly_str: str
    :return: Shapely Polygon object, or None if parsing fails.
    :rtype: shapely.Polygon or None
    """
    try:
        return wkt.loads(poly_str)
    except Exception as e:
        logger.error(f"Error parsing polygon: {e}")
        return None


def find_field_of_view_polygon(attributes):
    """
    Find the field of view polygon string in sensor attributes.

    This function searches through a list of sensor attributes and returns the value
    of the 'fieldOfViewPolygon' attribute if found.

    :param attributes: List of attribute dictionaries from sensor data.
    :type attributes: list
    :return: WKT string value of the 'fieldOfViewPolygon' attribute.
    :rtype: str
    :raises ValueError: If 'fieldOfViewPolygon' not found in attributes.
    """
    for attribute in attributes:
        if attribute["name"] == FIELD_OF_VIEW_POLYGON_ATTR:
            return attribute["value"]
    raise ValueError(f"{FIELD_OF_VIEW_POLYGON_ATTR} not found in attributes")


def calculate_camera_frustum_polygon(
    intrinsic_matrix,
    extrinsic_matrix,
    height_range=(1.0, 3.0),
    image_size=(1920, 1080),
    scene_bounds=None,
    max_distance=30.0,
):
    """
    Calculate the field of view polygon by intersecting camera frustum with ground plane.

    This function computes the camera frustum using intrinsic and extrinsic matrices,
    then finds the intersection with the ground plane at different heights to create
    a polygon representing the camera's field of view. This is useful when FOV polygons
    are not available in calibration data.

    Algorithm:
    1. Sample 25 points from image in a 5x5 grid for dense, robust coverage
    2. Convert sample points to world coordinate rays using camera matrices
    3. Intersect rays with ground planes at different heights
    4. Clamp ray distances to max_distance to prevent infinite polygons
    5. Create convex hull from intersection points
    6. Optionally clip to scene bounds

    :param intrinsic_matrix: 3x3 camera intrinsic matrix (K).
    :type intrinsic_matrix: numpy.ndarray or list
    :param extrinsic_matrix: 3x4 or 4x4 camera extrinsic matrix (world to camera).
    :type extrinsic_matrix: numpy.ndarray or list
    :param height_range: Tuple of (min_height, max_height) for ground plane intersection (default: 1.0-3.0m).
    :type height_range: tuple
    :param image_size: Tuple of (width, height) for image dimensions in pixels (default: 1920x1080).
    :type image_size: tuple
    :param scene_bounds: Optional tuple of (min_x, min_y, max_x, max_y) to clip the frustum polygon.
    :type scene_bounds: tuple or None
    :param max_distance: Maximum distance in meters from camera center to constrain the frustum (default: 30.0m).
    :type max_distance: float
    :return: Shapely Polygon object representing the field of view, or None if calculation fails.
    :rtype: shapely.Polygon or None
    """
    # Convert to numpy arrays if needed
    K = np.array(intrinsic_matrix)
    if len(K.shape) == 2 and K.shape[0] == 3 and K.shape[1] == 3:
        # Extend to 4x4 for homogeneous coordinates
        K_ext = np.eye(4)
        K_ext[:3, :3] = K
        K = K_ext

    # Handle extrinsic matrix format (convert 3x4 to 4x4 if needed)
    extrinsic = np.array(extrinsic_matrix)
    if extrinsic.shape == (3, 4):
        extrinsic_4x4 = np.eye(4)
        extrinsic_4x4[:3] = extrinsic
        extrinsic = extrinsic_4x4

    # Get camera center in world coordinates
    # For world-to-camera extrinsic matrix, camera center is -R^T * t
    R = extrinsic[:3, :3]
    t = extrinsic[:3, 3]
    camera_center = -R.T @ t

    # Debug: Check if camera is at a reasonable height
    if camera_center[2] < 0.5:
        logger.warning(
            f"Camera center is very low (z={camera_center[2]:.2f}m), frustum may not intersect ground planes"
        )

    # Define image sample points: dense grid for better coverage
    width, height = image_size

    # Create dense sampling grid: 5x5 = 25 points across the image
    # Grid pattern (each • represents a sample point):
    #   •  •  •  •  •   (row 0: top edge - may point upward for tilted cameras)
    #   •  •  •  •  •   (row 1: upper-middle)
    #   •  •  •  •  •   (row 2: center - most likely to hit ground)
    #   •  •  •  •  •   (row 3: lower-middle)
    #   •  •  •  •  •   (row 4: bottom edge - most likely to hit ground)
    # This dense sampling ensures we get valid ground intersections even when
    # top corners point upward (backward rays) for tilted/upward-looking cameras
    grid_size = 5  # 5x5 grid = 25 sample points
    sample_points_list = []
    for row in range(grid_size):
        for col in range(grid_size):
            x = col * width / (grid_size - 1)
            y = row * height / (grid_size - 1)
            sample_points_list.append([x, y, 1])

    image_sample_points = np.array(sample_points_list).T

    # Convert to normalized camera coordinates
    K_inv = np.linalg.inv(K[:3, :3])
    ray_dirs_cam = K_inv @ image_sample_points

    # Convert to world coordinates (ray directions in world frame)
    R_inv = R.T  # Inverse rotation (camera to world)
    ray_dirs_world = R_inv @ ray_dirs_cam

    # Find intersections with ground planes at different heights
    ground_points = []

    # Create a list of heights to check
    if isinstance(height_range, tuple) and len(height_range) == 2:
        # Create multiple height levels between min and max
        min_height, max_height = height_range
        heights = [min_height, max_height]
        # Add intermediate heights for better coverage
        if max_height - min_height > 1.0:
            mid_height = (min_height + max_height) / 2
            heights.append(mid_height)
    else:
        heights = list(height_range)

    # Intersect each image corner ray with ground planes
    debug_ray_info = []  # Track ray intersection details for debugging

    for height_idx, height in enumerate(heights):
        height_points = []
        num_sample_points = ray_dirs_world.shape[1]
        for i in range(num_sample_points):  # For each sample point ray
            ray_dir = ray_dirs_world[:, i]
            # Generate point name based on grid position (row, col)
            row = i // grid_size
            col = i % grid_size
            # Give special names to corners for easier understanding
            if row == 0 and col == 0:
                point_name = "corner-TL"
            elif row == 0 and col == grid_size - 1:
                point_name = "corner-TR"
            elif row == grid_size - 1 and col == 0:
                point_name = "corner-BL"
            elif row == grid_size - 1 and col == grid_size - 1:
                point_name = "corner-BR"
            else:
                point_name = f"grid[{row},{col}]"

            # Ray equation: P = camera_center + t * ray_dir
            # Ground plane: z = height
            # Solve for t: camera_center[2] + t * ray_dir[2] = height
            if abs(ray_dir[2]) > 1e-6:  # Avoid division by zero
                t = (height - camera_center[2]) / ray_dir[2]
                if t > 0:  # Only consider forward rays
                    intersection_point = camera_center + t * ray_dir

                    # Check distance from camera center and clamp to max_distance
                    distance = np.linalg.norm(intersection_point - camera_center)
                    if distance <= max_distance:
                        height_points.append(
                            [intersection_point[0], intersection_point[1]]
                        )
                    else:
                        # Add point at max_distance along the ray
                        clamped_point = camera_center + (max_distance / distance) * (
                            intersection_point - camera_center
                        )
                        height_points.append([clamped_point[0], clamped_point[1]])
                else:
                    # Ray pointing backward (t < 0)
                    if (
                        height_idx == 0 and len(debug_ray_info) < 20
                    ):  # Log more rays for denser grid
                        debug_ray_info.append(f"{point_name}: backward ray (t={t:.2f})")
            else:
                # Ray parallel to ground (ray_dir[2] ≈ 0)
                if height_idx == 0 and len(debug_ray_info) < 20:
                    debug_ray_info.append(
                        f"{point_name}: parallel to ground (ray_z={ray_dir[2]:.6f})"
                    )

        # Collect all intersection points from this height
        # (We'll check total count later, not per-height)
        ground_points.extend(height_points)
        if (
            len(height_points) < num_sample_points and height_idx == 0
        ):  # Only log for first height to avoid spam
            logger.debug(
                f"{len(height_points)}/{num_sample_points} image sample rays intersected ground at height {height}m"
            )

    # Show ray debugging info if some rays didn't intersect
    num_sample_points = ray_dirs_world.shape[1]
    if (
        len(ground_points) < len(heights) * num_sample_points * 0.8
    ):  # Less than 80% of possible rays
        if debug_ray_info:
            logger.debug("Ray issues detected:")
            for info in debug_ray_info:
                logger.debug(f"  {info}")

    # If we have valid intersection points, create a polygon
    if len(ground_points) >= 3:
        # Remove duplicates with 10cm tolerance
        unique_points = []
        for point in ground_points:
            is_duplicate = False
            for existing_point in unique_points:
                if (
                    np.linalg.norm(np.array(point) - np.array(existing_point)) < 0.1
                ):  # 10cm tolerance
                    is_duplicate = True
                    break
            if not is_duplicate:
                unique_points.append(point)

        if len(unique_points) >= 3:
            try:
                # Create polygon and get convex hull
                polygon = Polygon(unique_points)
                polygon = polygon.convex_hull

                # Clip to scene bounds if provided
                if scene_bounds is not None:
                    min_x, min_y, max_x, max_y = scene_bounds
                    scene_box = Polygon(
                        [[min_x, min_y], [max_x, min_y], [max_x, max_y], [min_x, max_y]]
                    )
                    original_area = polygon.area
                    polygon = polygon.intersection(scene_box)

                    # Ensure we still have a valid polygon after clipping
                    if polygon.is_empty or polygon.area < 1e-6:
                        logger.warning(
                            f"Frustum polygon was clipped to empty by scene bounds (original area: {original_area:.1f}m²)"
                        )
                        return None

                return polygon
            except Exception as e:
                logger.error(f"Error creating polygon from frustum points: {e}")
                return None
        else:
            logger.warning(
                f"Only {len(unique_points)} unique intersection points found (need ≥3)"
            )
            return None
    else:
        logger.warning(
            f"Only {len(ground_points)} total ground intersection points found (need ≥3)"
        )
        logger.debug(f"Checked {len(heights)} height levels: {heights}m")
        logger.debug(
            f"Camera center at: ({camera_center[0]:.1f}, {camera_center[1]:.1f}, {camera_center[2]:.1f})m"
        )

        # Show ray debugging info
        if debug_ray_info:
            logger.debug("Ray issues detected:")
            for info in debug_ray_info:
                logger.debug(f"  {info}")

        logger.info("Possible causes:")
        logger.info("  - Camera pointing away from or above ground planes")
        logger.info("  - Camera height outside the specified height_range (1.0-3.0m)")
        logger.info("  - Camera looking mostly horizontally")
        logger.info(
            f"Suggestions: Try increasing --max_camera_distance (current: {max_distance}m) or adjusting height_range"
        )
        return None


def calculate_scene_bounds_from_calibration(
    calibration_data, map_width=None, map_height=None
):
    """
    Calculate scene bounds from calibration data.

    This function determines the world coordinate bounds of the scene using either:
    1. Translation and scale factor from the first sensor + map dimensions
    2. Camera positions with a margin (fallback method)

    :param calibration_data: Dictionary containing sensor calibration data with 'sensors' key.
    :type calibration_data: dict
    :param map_width: Width of the map image in pixels (optional).
    :type map_width: int or None
    :param map_height: Height of the map image in pixels (optional).
    :type map_height: int or None
    :return: Tuple of (min_x, min_y, max_x, max_y) representing scene bounds in world coordinates.
    :rtype: tuple or None
    """
    # Get the first sensor to extract global coordinate system info
    first_sensor = calibration_data["sensors"][0]

    # Method 1: Use translation and scale factor with map dimensions
    if (
        "translationToGlobalCoordinates" in first_sensor
        and "scaleFactor" in first_sensor
    ):
        translation = first_sensor["translationToGlobalCoordinates"]
        scale = first_sensor["scaleFactor"]

        # If map dimensions are provided, use them to calculate bounds
        if map_width is not None and map_height is not None:
            # Origin in world coordinates
            origin_x = -translation["x"]
            origin_y = -translation["y"]

            # Size in world coordinates
            width_world = map_width / scale
            height_world = map_height / scale

            return (origin_x, origin_y, origin_x + width_world, origin_y + height_world)

    # Method 2: Fallback - calculate bounds from camera positions
    camera_positions = []
    for sensor in calibration_data["sensors"]:
        try:
            intrinsic_matrix, extrinsic_matrix = extract_camera_matrices(sensor)
            if extrinsic_matrix is not None:
                extrinsic = np.array(extrinsic_matrix)
                if extrinsic.shape == (3, 4):
                    extrinsic_4x4 = np.eye(4)
                    extrinsic_4x4[:3] = extrinsic
                    extrinsic = extrinsic_4x4

                R = extrinsic[:3, :3]
                t = extrinsic[:3, 3]
                camera_center = -R.T @ t
                camera_positions.append([camera_center[0], camera_center[1]])
        except Exception:
            continue

    if len(camera_positions) > 0:
        positions = np.array(camera_positions)
        min_x, min_y = positions.min(axis=0)
        max_x, max_y = positions.max(axis=0)

        # Add margin around cameras (100 meters)
        margin = 100.0
        return (min_x - margin, min_y - margin, max_x + margin, max_y + margin)

    # If all else fails, return None (no bounds)
    return None


def extract_sensor_fov_polygons(
    sensors,
    prefer_existing_fov=False,
    height_range=(1.0, 3.0),
    scene_bounds=None,
    max_distance=30.0,
):
    """
    Extract or calculate FOV polygons for a list of sensors.

    This function retrieves FOV polygons from sensor calibration data, either by:
    1. Parsing existing FOV polygons from sensor attributes (if prefer_existing_fov=True), or
    2. Calculating FOV polygons from camera frustum using intrinsic/extrinsic matrices

    The function also computes the overlap ratio as a quality metric for multi-camera coverage.

    :param sensors: List of sensor dictionaries with calibration data.
    :type sensors: list of dict
    :param prefer_existing_fov: If True, prefer FOV from calibration attributes over frustum calculation.
    :type prefer_existing_fov: bool
    :param height_range: Height range (min, max) in meters for ground plane intersection when calculating frustum.
    :type height_range: tuple of (float, float)
    :param scene_bounds: Optional (min_x, min_y, max_x, max_y) to clip frustum polygons.
    :type scene_bounds: tuple or None
    :param max_distance: Maximum distance in meters from camera center to constrain the frustum (default: 30.0m).
    :type max_distance: float
    :return: Tuple of (polygons, overlap_ratio) where polygons is a list of Shapely Polygon objects
             (or None for failed extractions) and overlap_ratio is a float (0.0-1.0) indicating
             the fraction of coverage area visible to multiple cameras.
    :rtype: tuple of (list, float)

    Example:
        >>> sensors = calibration_data["sensors"]
        >>> polygons, overlap_ratio = extract_sensor_fov_polygons(sensors, prefer_existing_fov=True)
        >>> print(f"Extracted {len([p for p in polygons if p])} valid polygons")
        >>> print(f"Group overlap: {overlap_ratio:.1%}")
    """
    polygons = []

    for sensor in sensors:
        polygon = None

        # Try to get polygon from attributes if prefer_existing_fov is True
        if prefer_existing_fov and "attributes" in sensor:
            try:
                poly_str = find_field_of_view_polygon(sensor["attributes"])
                polygon = parse_polygon(poly_str)
            except (ValueError, Exception) as e:
                logger.debug(
                    f"Failed to get FOV from attributes for sensor {sensor.get('id', 'unknown')}: {e}"
                )
                # Fall back to frustum calculation

        # If still no polygon, calculate from frustum
        if polygon is None:
            try:
                intrinsic_matrix, extrinsic_matrix = extract_camera_matrices(sensor)
                if intrinsic_matrix is not None and extrinsic_matrix is not None:
                    polygon = calculate_camera_frustum_polygon(
                        intrinsic_matrix,
                        extrinsic_matrix,
                        height_range=height_range,
                        scene_bounds=scene_bounds,
                        max_distance=max_distance,
                    )
            except Exception as e:
                logger.warning(
                    f"Failed to extract FOV for sensor {sensor.get('id', 'unknown')}: {e}"
                )

        polygons.append(polygon)

    # Calculate overlap ratio as a quality metric
    overlap_ratio = calculate_overlap_ratio(polygons)

    return polygons, overlap_ratio


def calculate_overlap_ratio(polygons):
    """
    Calculate the ratio of overlapped FOV area to total union FOV area.

    For a group of polygons, this function computes what fraction of the total
    coverage area is visible to multiple cameras (i.e., overlapped). This is useful for
    assessing overlap quality in multi-camera systems. Higher values indicate better
    multi-camera coverage.

    Algorithm:
    1. Calculate the union of all input polygons (total coverage area)
    2. For each polygon, calculate the area visible only to that polygon
       (i.e., the area not covered by any other polygon)
    3. Sum all single-view areas
    4. Return the ratio: overlap_area / total_union_area = 1.0 - (single_view_area / total_union_area)

    :param polygons: List of Shapely Polygon objects representing camera FOVs.
                     None or empty polygons in the list are automatically filtered out.
    :type polygons: List[shapely.Polygon or None]
    :return: Ratio of overlapped area to total union area (0.0 to 1.0).
             Returns 0.0 for single polygon (no overlap possible).
             Returns 0.0 if union area is too small or calculation fails.
    :rtype: float

    Example:
        >>> # Two overlapping cameras
        >>> poly1 = Polygon([(0, 0), (10, 0), (10, 10), (0, 10)])
        >>> poly2 = Polygon([(5, 0), (15, 0), (15, 10), (5, 10)])
        >>> ratio = calculate_overlap_ratio([poly1, poly2])
        >>> # ratio will be around 0.33 (150 total area, 50 overlap)
    """
    # Filter out None and empty polygons
    valid_polygons = [
        poly for poly in polygons if poly is not None and not poly.is_empty
    ]

    # Edge cases
    if len(valid_polygons) == 0:
        logger.warning("No valid polygons provided for overlap ratio calculation")
        return 0.0

    if len(valid_polygons) == 1:
        # Single polygon means no overlap possible
        return 0.0

    try:
        # Calculate union of all polygons
        union_poly = valid_polygons[0]
        for poly in valid_polygons[1:]:
            union_poly = union_poly.union(poly)

        total_union_area = union_poly.area

        if total_union_area < 1e-6:
            # Union area too small
            logger.warning(f"Union area too small: {total_union_area}")
            return 0.0

        # Calculate area visible to only one polygon
        single_view_area = 0.0

        for i in range(len(valid_polygons)):
            current_poly = valid_polygons[i]

            # Get union of all OTHER polygons
            other_polygons = [
                valid_polygons[j] for j in range(len(valid_polygons)) if j != i
            ]

            if len(other_polygons) == 0:
                # This shouldn't happen since we checked len >= 2, but be safe
                single_view_area += current_poly.area
            else:
                # Calculate union of other polygons
                other_union = other_polygons[0]
                for poly in other_polygons[1:]:
                    other_union = other_union.union(poly)

                # Area only visible to this polygon = this polygon minus others' union
                only_this_poly = current_poly.difference(other_union)
                single_view_area += only_this_poly.area

        # Calculate overlap ratio (inverse of single-view ratio)
        single_view_ratio = single_view_area / total_union_area
        overlap_ratio = 1.0 - single_view_ratio

    except (AttributeError, TypeError, ValueError) as e:
        logger.exception(f"Error calculating overlap ratio: {e}")
        return 0.0
    else:
        return overlap_ratio


def fill_sensor_fov_polygons(
    sensors,
    use_frustum=False,
    scene_bounds=None,
    max_camera_distance=30.0,
    height_range=(1.0, 3.0),
    image_size=(1920, 1080),
):
    """
    Fill empty or regenerate FOV polygons for sensors using frustum calculation.

    This function iterates through sensors and fills in missing FOV polygons
    (fieldOfViewPolygon attribute) using camera frustum calculation. It can either:
    - Fill only empty/missing polygons (use_frustum=False)
    - Regenerate all polygons from frustum (use_frustum=True)

    The function modifies sensor attributes in-place.

    :param sensors: List of sensor dictionaries with calibration data.
    :type sensors: list of dict
    :param use_frustum: If True, always regenerate polygons from frustum.
                        If False, only fill empty/missing polygons.
    :type use_frustum: bool
    :param scene_bounds: Optional (min_x, min_y, max_x, max_y) to clip frustum polygons.
    :type scene_bounds: tuple or None
    :param max_camera_distance: Maximum distance in meters for frustum calculation.
    :type max_camera_distance: float
    :param height_range: Height range (min, max) in meters for ground plane intersection.
    :type height_range: tuple of (float, float)
    :param image_size: Image dimensions (width, height) in pixels for frustum calculation.
    :type image_size: tuple of (int, int)
    :return: Tuple of (filled_count, skipped_count, missing_attr_count) where:
             - filled_count: Number of polygons filled/regenerated
             - skipped_count: Number of existing polygons skipped
             - missing_attr_count: Number of sensors missing fieldOfViewPolygon attribute
    :rtype: tuple of (int, int, int)

    Example:
        >>> sensors = calibration_data["sensors"]
        >>> filled, skipped, missing = fill_sensor_fov_polygons(
        ...     sensors,
        ...     use_frustum=False,
        ...     max_camera_distance=30.0
        ... )
        >>> logger.info(f"Filled {filled} polygons, skipped {skipped} existing")
    """
    filled_count = 0
    skipped_count = 0
    missing_attr_count = 0

    for sensor in sensors:
        camera_id = sensor.get("id", None)
        if not camera_id:
            logger.warning("Sensor missing 'id' key, skipping.")
            continue

        found_polygon = False
        for attr in sensor.get("attributes", []):
            if attr.get("name") == FIELD_OF_VIEW_POLYGON_ATTR:
                found_polygon = True
                poly_value = attr.get("value", None)

                try:
                    if use_frustum:
                        # Always regenerate polygon when use_frustum is True
                        poly = _generate_fov_polygon_from_frustum(
                            sensor,
                            scene_bounds=scene_bounds,
                            max_camera_distance=max_camera_distance,
                            height_range=height_range,
                            image_size=image_size,
                        )
                        if poly is not None:
                            attr["value"] = wkt.dumps(poly)
                            filled_count += 1
                        else:
                            logger.warning(
                                f"Failed to generate polygon for sensor {camera_id}"
                            )
                    else:
                        # Only fill if polygon is empty/missing
                        if not poly_value or str(poly_value).strip() == "":
                            poly = _generate_fov_polygon_from_frustum(
                                sensor,
                                scene_bounds=scene_bounds,
                                max_camera_distance=max_camera_distance,
                                height_range=height_range,
                                image_size=image_size,
                            )
                            if poly is not None:
                                attr["value"] = wkt.dumps(poly)
                                filled_count += 1
                            else:
                                logger.warning(
                                    f"Failed to generate polygon for sensor {camera_id}"
                                )
                        else:
                            skipped_count += 1
                except Exception as e:
                    logger.error(
                        f"Exception generating polygon for sensor {camera_id}: {e}"
                    )
                break  # Found the attribute, no need to check further

        if not found_polygon:
            logger.warning(
                f"Sensor {camera_id} missing '{FIELD_OF_VIEW_POLYGON_ATTR}' attribute."
            )
            missing_attr_count += 1

    return filled_count, skipped_count, missing_attr_count


def _generate_fov_polygon_from_frustum(
    sensor,
    scene_bounds=None,
    max_camera_distance=30.0,
    height_range=(1.0, 3.0),
    image_size=(1920, 1080),
):
    """
    Generate FOV polygon from camera frustum for a single sensor.

    Internal helper function that extracts camera matrices and calculates
    the frustum polygon.

    :param sensor: Sensor dictionary with calibration data.
    :type sensor: dict
    :param scene_bounds: Optional (min_x, min_y, max_x, max_y) to clip frustum polygon.
    :type scene_bounds: tuple or None
    :param max_camera_distance: Maximum distance in meters for frustum calculation.
    :type max_camera_distance: float
    :param height_range: Height range (min, max) in meters for ground plane intersection.
    :type height_range: tuple of (float, float)
    :param image_size: Image dimensions (width, height) in pixels for frustum calculation.
    :type image_size: tuple of (int, int)
    :return: Shapely Polygon object or None if generation fails.
    :rtype: shapely.Polygon or None
    """
    intrinsic_matrix, extrinsic_matrix = extract_camera_matrices(sensor)

    if intrinsic_matrix is None or extrinsic_matrix is None:
        logger.warning(
            f"Failed to extract camera matrices for sensor {sensor.get('id', 'unknown')}"
        )
        return None

    return calculate_camera_frustum_polygon(
        intrinsic_matrix,
        extrinsic_matrix,
        height_range=height_range,
        image_size=image_size,
        scene_bounds=scene_bounds,
        max_distance=max_camera_distance,
    )


def update_sensor_fov_attributes(
    sensors, polygons, update_only_if_frustum_generated=True
):
    """
    Update sensor fieldOfViewPolygon attributes with generated polygons.

    This function updates or adds the 'fieldOfViewPolygon' attribute for each sensor
    with the corresponding polygon in WKT (Well-Known Text) format. This is useful
    when FOV polygons are generated from camera frustums and need to be persisted
    in the calibration data.

    :param sensors: List of sensor dictionaries with calibration data.
    :type sensors: list of dict
    :param polygons: List of Shapely Polygon objects corresponding to each sensor.
                     Must have the same length as sensors list.
    :type polygons: list of (shapely.Polygon or None)
    :param update_only_if_frustum_generated: If True, only update if polygon doesn't exist
                                              in attributes (i.e., was frustum-generated).
                                              If False, always update/overwrite existing FOV.
    :type update_only_if_frustum_generated: bool
    :return: Number of sensors updated with new FOV polygons.
    :rtype: int
    :raises ValueError: If sensors and polygons lists have different lengths.

    Example:
        >>> sensors = calibration_data["sensors"]
        >>> polygons, _ = extract_sensor_fov_polygons(sensors, prefer_existing_fov=False)
        >>> updated_count = update_sensor_fov_attributes(sensors, polygons)
        >>> logger.info(f"Updated {updated_count} sensors with frustum-based FOV polygons")
    """
    if len(sensors) != len(polygons):
        raise ValueError(
            f"Sensors list length ({len(sensors)}) must match polygons list length ({len(polygons)})"
        )

    updated_count = 0

    for sensor, poly in zip(sensors, polygons):
        # Skip if polygon is None or empty
        if poly is None or poly.is_empty:
            continue

        # Check if sensor already has a fieldOfViewPolygon attribute
        has_existing_fov = False
        if "attributes" in sensor:
            for attr in sensor["attributes"]:
                if isinstance(attr, dict) and attr.get("name") == "fieldOfViewPolygon":
                    has_existing_fov = True

                    # Update existing attribute based on mode
                    if not update_only_if_frustum_generated:
                        attr["value"] = poly.wkt
                        updated_count += 1
                        logger.debug(
                            f"Updated existing FOV for sensor {sensor.get('id', 'unknown')}"
                        )
                    else:
                        logger.debug(
                            f"Skipping sensor {sensor.get('id', 'unknown')} - already has FOV attribute"
                        )
                    break

        # If no existing FOV attribute found, add it
        if not has_existing_fov:
            if "attributes" not in sensor:
                sensor["attributes"] = []

            sensor["attributes"].append(
                {"name": "fieldOfViewPolygon", "value": poly.wkt}
            )
            updated_count += 1
            logger.debug(f"Added new FOV for sensor {sensor.get('id', 'unknown')}")

    if updated_count > 0:
        logger.info(
            f"Updated {updated_count} sensor(s) with frustum-based FOV polygons"
        )

    return updated_count
