# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

import json
import logging
import time
from typing import Any

import cv2
import numpy as np
import requests

from mdx.analytics.core.constants import ROI_TYPE_BUFFER_ZONE
from mdx.analytics.core.schema.config import AppConfig
from mdx.analytics.core.schema.models import Bbox, Bbox3d, Coordinate, Location
from mdx.analytics.core.transform.calibration.calibration_base import CalibrationBase, CalibrationType
from mdx.analytics.core.utils.distance_util import get_lat_lon

logger = logging.getLogger(__name__)


class CalibrationE(CalibrationBase):
    """
    CalibrationE class that reads the calibration information from a JSON config file
    and instantiates the transform matrix, ROI, etc., per sensor.

    This class extends CalibrationBase to provide specific implementations for:
    - Perspective transformation of coordinates
    - Homography map loading for global ROIs
    - Bounding box transformations
    - Frame transformations with ROI and safety detection

    :ivar AppConfig config: Configuration object for the application.
    :ivar str calibration_file_path: Path to the calibration configuration file.
    :ivar dict[str, Any] calibration_info: Dictionary containing the parsed calibration configuration.
    :ivar list[SensorInfo] sensors: List of sensors read from the calibration config file.
    :ivar list[ROI] global_rois: List of global ROIs read from the calibration config file.
    :ivar list[Tripwire] global_tripwires: List of global tripwires read from the calibration config file.
    :ivar dict[str, SensorInfo] sensor_map: Map of sensorId to sensor.
    :ivar dict[str, ROI] global_roi_map: Map of ROI IDs to ROI objects.
    :ivar dict[str, Tripwire] global_tripwire_map: Map of tripwire IDs to Tripwire objects.
    :ivar list[ROI] global_buffer_zones: List of global buffer zones read from the calibration config file.
    :ivar dict[str, tuple[tuple[float, float], tuple[float, float], list[list[float]], list[list[float]]]] homography_map_global_roi:
    Map of ROI IDs to tuples containing:
    - origin point (x,y)
    - dimensions (width,height)
    - homography matrix
    - inverse homography matrix

    Examples::
        >>> config = AppConfig()
        >>> calibration = CalibrationE(config, "calibration.json")
        >>> print(f"Initialized calibration with {len(calibration.sensors)} sensors")
    """

    def _load_data(self) -> None:
        """
        Load sensors, and build mappings for sensor and homography data.

        :return: None

        Examples::
            >>> calibration = CalibrationBase(config, calibration_file_path)
            >>> calibration._load_data()
            >>> print(f"Loaded {len(calibration.sensors)} sensors")
        """
        super()._load_data()
        self.global_buffer_zones = [
            roi for roi in self.global_rois if roi.type == ROI_TYPE_BUFFER_ZONE
        ] or self.global_rois
        self.homography_map_global_roi = self._load_homography_map_global_roi()

    def _load_homography_map_global_roi(self) -> dict[
        str,
        tuple[tuple[float, float], tuple[float, float], list[list[float]], list[list[float]]],
    ]:
        """
        Initialize a mapping containing ROI ID to transformation information.

        Each ROI entry contains:
        - origin_point: tuple[float, float] - The origin point (x,y) of the ROI in global coordinates
        - dimensions: tuple[float, float] - The width and height of the ROI
        - hmatrix: list[list[float]] - homography matrix that transforms global coordinates to ROI-centric coordinates
        - hmatrix_inv: list[list[float]] - homography matrix that transforms back to global coordinates

        The homography matrices are computed using RANSAC with a reprojection threshold of 3 pixels.

        :return dict[str, tuple[tuple[float, float], tuple[float, float], list[list[float]], list[list[float]]]]: A dictionary mapping ROI IDs to their transformation information

        Examples::
            >>> calibration = CalibrationE(config, calibration_file_path)
            >>> homography_map = calibration._load_homography_map_global_roi()
            >>> for roi_id, (origin, dims, h_matrix, h_inv) in homography_map.items():
            ...     print(f"ROI {roi_id}: origin={origin}, dimensions={dims}")
        """
        homography_map = {}
        for roi in self.global_buffer_zones:
            # assume global rois are four points rectangles
            roi_id, roi_coords = roi.id, roi.roiCoordinates
            pt1_roi = (roi_coords[0].x, roi_coords[0].y)
            pt2_roi = (roi_coords[1].x, roi_coords[1].y)
            pt3_roi = (roi_coords[2].x, roi_coords[2].y)
            pt4_roi = (roi_coords[3].x, roi_coords[3].y)

            dist1_horizontal = np.linalg.norm(np.array(pt1_roi) - np.array(pt2_roi))
            dist2_horizontal = np.linalg.norm(np.array(pt3_roi) - np.array(pt4_roi))
            dist1_vertical = np.linalg.norm(np.array(pt2_roi) - np.array(pt3_roi))
            dist2_vertical = np.linalg.norm(np.array(pt4_roi) - np.array(pt1_roi))

            roi_width = 0.5 * (dist1_horizontal + dist2_horizontal)
            roi_height = 0.5 * (dist1_vertical + dist2_vertical)
            roi_dimension = (roi_width, roi_height)

            roi_global_coordinate_array = np.array([pt1_roi, pt2_roi, pt3_roi, pt4_roi])
            roi_centric_coordinate_array = np.array(
                [
                    (0.0, 0.0),
                    (roi_width, 0.0),
                    (roi_width, roi_height),
                    (0.0, roi_height),
                ]
            )

            hmatrix, _ = cv2.findHomography(
                roi_global_coordinate_array,
                roi_centric_coordinate_array,
                method=cv2.RANSAC,
                ransacReprojThreshold=3,
            )
            hmatrix_inv, _ = cv2.findHomography(
                roi_centric_coordinate_array,
                roi_global_coordinate_array,
                method=cv2.RANSAC,
                ransacReprojThreshold=3,
            )
            del roi_global_coordinate_array
            del roi_centric_coordinate_array

            hmatrix = hmatrix.tolist()
            hmatrix_inv = hmatrix_inv.tolist()

            # Store the location, homography matrix, and polygon map in the result dictionary
            homography_map[roi_id] = (pt1_roi, roi_dimension, hmatrix, hmatrix_inv)

        return homography_map

    def transform_bbox(self, bbox: Bbox | Bbox3d, sensor_id: str) -> tuple[Coordinate, Location]:
        """
        Transform image coordinates bbox to real-world coordinates and location.

        :param Bbox | Bbox3d bbox: The bounding box to transform
        :param str sensor_id: The sensor ID to use for transformation
        :return tuple[Coordinate, Location]: Tuple containing the transformed coordinate and location

        Examples::
            >>> calibration = CalibrationE(config, calibration_file_path)
            >>> bbox = Bbox(leftX=100, rightX=200, topY=50, bottomY=150) | Bbox3d(coordinates=[100, 200, 50, 30, 40, 20, 0, 0, 0, 0, 0, 0])
            >>> coord, loc = calibration.transform_bbox(bbox, "sensor1")
            >>> print(f"Transformed to: {coord.x}, {coord.y} at {loc.latitude}, {loc.longitude}")
            >>> print(f"Transformed to: {coord.x}, {coord.y}, {coord.z} at {loc.latitude}, {loc.longitude}")
        """
        if self.config.in_3d_mode:
            if not isinstance(bbox, Bbox3d):
                raise ValueError(f"3D mode requires a Bbox3d object, got {type(bbox).__name__}")
            if len(bbox.coordinates) < 6:
                raise ValueError("3D bounding box must have at least 6 numbers for euclidean transformation in 3d mode")
            px, py, pz, _, _, height = bbox.coordinates[:6]
            # center of bottom of bbox3d
            pz = pz - height / 2
            coordinate = Coordinate(x=px, y=py, z=pz)
            latlon_origin = self.sensor_map[sensor_id].origin if self.contains(sensor_id) else Location(lat=0.0, lon=0.0)
        else:
            if not isinstance(bbox, Bbox):
                raise ValueError(f"2D mode requires a Bbox object, got {type(bbox).__name__}")
            px, py = (bbox.rightX + bbox.leftX) / 2.0, max(bbox.topY, bbox.bottomY)
            if self.contains(sensor_id):
                latlon_origin, h_matrix = self.sensor_map[sensor_id].origin, self.sensor_map[sensor_id].homography
                global_coords = self.perspective_transform(px, py, h_matrix)
                coordinate = Coordinate(x=global_coords[0], y=global_coords[1]) if global_coords else Coordinate(x=px, y=py)
            else:
                latlon_origin = Location(lat=0.0, lon=0.0, alt=0.0)
                coordinate = Coordinate(x=px, y=py)

        latlon = get_lat_lon(latlon_origin, coordinate)
        return coordinate, latlon

    def transform_bbox3d_to_global_rois(self, bbox3d: Bbox3d) -> dict[str, list[Coordinate]]:
        """
        Transform 3D bounding box corners to coordinates relative to each global ROI.

        :param Bbox3d bbox3d: The 3D bounding box to transform
        :return dict[str, list[Coordinate]]: Dictionary mapping ROI IDs to lists of transformed coordinates

        Examples::
            >>> calibration = CalibrationE(config, calibration_file_path)
            >>> bbox3d = Bbox3d(coordinates=[100, 200, 50, 30, 40, 20, 0, 0, 0, 0, 0, 0])
            >>> roi_coords = calibration.transform_bbox3d_to_global_rois(bbox3d)
            >>> for roi_id, coords in roi_coords.items():
            ...     print(f"ROI {roi_id} coordinates: {[f'({c.x}, {c.y})' for c in coords]}")
        """
        corners_3d = self.box3d_to_corners3d(bbox3d)
        corners_2d = corners_3d[:, :2]
        # fetch the bottom plane points in order: (xmin, ymin),(xmax,ymin),(xmax,ymax),(xmin,ymax)
        corners_2d = corners_2d[[0, 4, 7, 3], :]

        roi_coords_map = {}
        for buffer_zone_id, buffer_zone_info in self.homography_map_global_roi.items():
            _, _, hmatrix, _ = buffer_zone_info
            roi_coords = []
            for px, py in corners_2d:
                roi_coord_x_y = self.perspective_transform(px, py, hmatrix)
                if roi_coord_x_y:
                    coordinate = Coordinate(x=roi_coord_x_y[0], y=roi_coord_x_y[1])
                else:
                    coordinate = Coordinate(x=px, y=py)
                roi_coords.append(coordinate)
            roi_coords_map[buffer_zone_id] = roi_coords

        return roi_coords_map

    def box3d_to_corners3d(self, bbox3d: Bbox3d) -> np.ndarray:
        """
        Convert a 3D bounding box to its 8 corner points in 3D space.

        :param Bbox3d bbox3d: The 3D bounding box to convert
        :return np.ndarray: Array of 8 corner points in 3D space

        Examples::
            >>> calibration = CalibrationE(config, calibration_file_path)
            >>> bbox3d = Bbox3d(coordinates=[100, 200, 50, 30, 40, 20, 0, 0, 0, 0, 0, 0])
            >>> corners = calibration.box3d_to_corners3d(bbox3d)
            >>> print(f"3D corners shape: {corners.shape}")
        """
        X, Y, Z, W, L, H, PITCH, ROLL, YAW, VX, VY, VZ = list(range(12))  # index

        box3d_data = np.array(bbox3d.coordinates)

        corners_norm = np.stack(np.unravel_index(np.arange(8), [2] * 3), axis=1)
        corners_norm = corners_norm[[0, 1, 3, 2, 4, 5, 7, 6]]
        # use relative origin [0.5, 0.5, 0.5]
        corners_norm = corners_norm - np.array([0.5, 0.5, 0.5])
        corners = box3d_data[[W, L, H]] * corners_norm.reshape([1, 8, 3])

        # rotate around z axis
        rot_cos = np.cos(box3d_data[YAW])
        rot_sin = np.sin(box3d_data[YAW])
        rot_mat = np.tile(np.eye(3)[None], (1, 1, 1))
        rot_mat[:, 0, 0] = rot_cos
        rot_mat[:, 0, 1] = -rot_sin
        rot_mat[:, 1, 0] = rot_sin
        rot_mat[:, 1, 1] = rot_cos
        corners = (rot_mat[:, None] @ corners[..., None]).squeeze(axis=-1)
        corners += box3d_data[:3]

        return corners[0]

    @property
    def calibration_type(self) -> CalibrationType:
        """Return CARTESIAN calibration type."""
        return CalibrationType.CARTESIAN


class CalibrationES(CalibrationE):
    """
    CalibrationES class that reads calibration information from an API endpoint.

    This class extends CalibrationE to provide API-based calibration loading functionality.
    It maintains all the functionality of CalibrationE but loads calibration data from
    a remote API endpoint instead of a local file.

    :param AppConfig config: Configuration object for the application.
    :param dict[str, Any] calibration_info: Dictionary containing the parsed calibration configuration.
    :param list[SensorInfo] sensors: List of sensors read from the calibration config file.
    :param list[ROI] global_rois: List of global ROIs read from the calibration config file.
    :param list[Tripwire] global_tripwires: List of global tripwires read from the calibration config file.
    :param dict[str, SensorInfo] sensor_map: Map of sensorId to sensor.
    :param dict[str, ROI] global_roi_map: Map of ROI IDs to ROI objects.
    :param dict[str, Tripwire] global_tripwire_map: Map of tripwire IDs to Tripwire objects.
    :param list[ROI] global_buffer_zones: List of global buffer zones read from the calibration config file.
    :param dict[str, tuple[tuple[float, float], tuple[float, float], list[list[float]], list[list[float]]]] homography_map_global_roi:
    Map of ROI IDs to tuples containing:
    - origin point (x,y)
    - dimensions (width,height)
    - homography matrix
    - inverse homography matrix

    Examples::
        >>> config = AppConfig()
        >>> calibration = CalibrationES(config)
        >>> print(f"Initialized API-based calibration with {len(calibration.sensors)} sensors")
    """

    def __init__(self, config: AppConfig) -> None:
        """
        Initialize the CalibrationES instance with API-based configuration loading.

        :param AppConfig config: Configuration object containing API endpoint settings
        :raises requests.exceptions.RequestException: If API requests fail after retries
        :return: None

        Examples::
            >>> config = AppConfig()
            >>> calibration = CalibrationES(config)
            >>> print(f"Initialized with API endpoint: {config.api_mdx_base_url}")
        """
        self.config = config
        self.calibration_info = self._load_calibration(config)
        self._load_data()

    def _load_calibration(self, config: AppConfig) -> dict[str, Any]:
        """
        Load calibration information from the configured API endpoint.

        This method attempts to load calibration data from the API with retry logic.
        It will retry up to the configured maximum number of times or until the
        maximum retry time is reached.

        :param AppConfig config: Configuration object containing API settings
        :return dict[str, Any]: Dictionary containing the calibration configuration
        :raises requests.exceptions.RequestException: If all retry attempts fail

        Examples::
            >>> config = AppConfig()
            >>> calibration = CalibrationES(config)
            >>> calibration_info = calibration._load_calibration(config)
            >>> print(f"Loaded calibration for {len(calibration_info.get('sensors', []))} sensors")
        """
        api_retry_max_cnt = config.api_retry_max_count
        api_retry_max_time_sec = config.api_retry_max_time_seconds
        mdx_api_base = config.api_mdx_base_url
        calibration_query_url = f"{mdx_api_base}/config/calibration"
        retry_cnt = 0
        retry_time_sec = 0.0
        start = time.time()
        while retry_cnt < api_retry_max_cnt and retry_time_sec < api_retry_max_time_sec:
            try:
                response = requests.get(calibration_query_url, timeout=5)
                if response.status_code == 200:
                    return json.loads(response.text)
                else:
                    logger.warning(
                        f"Load calibration from API end point failed, will retry if not reached limit\n"
                        f"  status code: {response.status_code}"
                    )
                    time.sleep(1)
                    retry_cnt += 1
                    retry_time_sec = time.time() - start
            except requests.exceptions.RequestException as e:
                logger.warning(
                    f"Load calibration from API end point failed, will retry if not reached limit\n"
                    f"  message: {str(e)}"
                )
                time.sleep(1)
                retry_cnt += 1
                retry_time_sec = time.time() - start
        logger.warning("Load calibration from API end point failed. Retry limit reached.")
        return {}
