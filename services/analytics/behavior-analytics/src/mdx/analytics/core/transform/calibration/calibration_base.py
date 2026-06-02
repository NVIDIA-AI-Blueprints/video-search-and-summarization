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

import logging
import os
from enum import Enum
from itertools import groupby
from pathlib import Path
from typing import Any

import cv2
import matplotlib.path as mplPath
import numpy as np
from scipy.spatial.transform import Rotation
from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from mdx.analytics.core.constants import (
    CALIBRATION_ACTION_DELETE,
    CALIBRATION_ACTION_UPSERT,
    CALIBRATION_ACTION_UPSERT_ALL,
    CALIBRATION_DIR,
    CALIBRATION_FILE_PATTERN,
    DEFAULT_COORDINATE,
    DEFAULT_DISTORTION_COEFFICIENTS,
    SENSOR_TYPE_CAMERA,
    SENSOR_TYPE_GROUP,
)
from mdx.analytics.core.schema.config import AppConfig
from mdx.analytics.core.schema.models import (
    ROI,
    Bbox,
    Bbox3d,
    Coordinate,
    Location,
    Message,
    Point2D,
    SensorInfo,
    Tripwire,
)
from mdx.analytics.core.schema.proto import schema_pb2 as nvSchema
from mdx.analytics.core.transform.calibration.calibration_validator import validate as validate_calibration
from mdx.analytics.core.transform.detection.proximity_detection import ProximityDetection
from mdx.analytics.core.utils.distance_util import orientation
from mdx.analytics.core.utils.io_utils import load_json_from_file
from mdx.analytics.core.utils.schema_util import (
    dict_location_to_location,
    list_coordinates_to_coordinates,
    list_rois_to_rois,
    list_tripwires_to_tripwires,
    list_tripwires_to_tripwires_map,
    list_attributes_to_attributes_map,
    nv_bbox3d_to_bbox3d,
    nv_bbox_to_bbox,
    coordinate_to_nv_coordinate,
    location_to_nv_location,
)
from mdx.analytics.core.utils.util import extract_sensor_id

logger = logging.getLogger(__name__)


class CalibrationType(Enum):
    """Enumeration of supported calibration types."""
    IMAGE = "image"
    CARTESIAN = "cartesian"
    GEO = "geo"


class CalibrationBase:
    """
    Base class that reads the calibration information from a JSON config file
    and instantiates the transform matrix, ROI, etc., per sensor.

    This class is extended by CalibrationI, CalibrationE, and etc.

    :ivar AppConfig config: Configuration object for the application.
    :ivar dict[str, Any] calibration_info: Dictionary containing the parsed calibration configuration.
    :ivar list[SensorInfo] sensors: List of sensors read from the calibration config file.
    :ivar list[ROI] global_rois: List of global ROIs read from the calibration config file.
    :ivar list[Tripwire] global_tripwires: List of global tripwires read from the calibration config file.
    :ivar dict[str, SensorInfo] sensor_map: Map of sensorId to sensor.
    :ivar dict[str, ROI] global_roi_map: Map of ROI IDs to ROI objects.
    :ivar dict[str, Tripwire] global_tripwire_map: Map of tripwire IDs to Tripwire objects.
    """

    class CalibrationFileMonitor(FileSystemEventHandler):
        """
        Watch for calibration files appearing in the watched directory and
        trigger reload of the parent :class:`CalibrationBase`.

        Only ``on_moved`` is handled. The producer
        (:class:`CalibrationListener`) always uses atomic rename to
        materialize files (``mkstemp`` + ``os.rename``); ``on_created``
        would fire for the staging ``.<name>.tmp`` (filtered out as a
        dotfile) and for any non-atomic direct write (which would race the
        read against still-being-written content). Restricting to
        ``on_moved`` enforces the atomic-write contract at the dispatch
        layer.

        Operators who need to drop a file in for debugging must ``mv`` from
        outside the watched dir, not ``cp``.

        :ivar CalibrationBase calibration: Reference to the parent calibration object.

        Examples::
            >>> config = AppConfig()
            >>> calibration = CalibrationBase(config, "calibration.json")
            >>> monitor = CalibrationFileMonitor(calibration)
            >>> print("Created file monitor for calibration directory")
        """

        def __init__(self, calibration: "CalibrationBase") -> None:
            """
            Initialize the CalibrationFileMonitor instance.

            :param CalibrationBase calibration: The parent calibration object to monitor
            :return: None

            Examples::
                >>> config = AppConfig()
                >>> calibration = CalibrationBase(config, "calibration.json")
                >>> monitor = CalibrationFileMonitor(calibration)
                >>> print("Initialized file monitor")
            """
            self.calibration = calibration

        def on_moved(self, event: FileSystemEvent) -> None:
            """
            Handle atomic-rename file events.

            :class:`CalibrationListener._atomic_write` stages the JSON to a
            hidden ``.<name>.tmp`` sibling and ``os.rename``\\ s it into
            place; that rename fires here with ``dest_path`` set to the
            final filename.

            Filters dotfiles and non-``.json`` paths (so staging ``.tmp``
            files are silently ignored), then calls
            :meth:`CalibrationBase.reload_data`. ``FileNotFoundError`` (rare
            pruner-vs-read race) is logged and skipped; other exceptions
            are logged via ``.exception()`` so the watchdog thread keeps
            running.

            :param FileSystemEvent event: Watchdog event with ``dest_path``.
            :return: None
            """
            if event.is_directory:
                return
            path = event.dest_path
            name = os.path.basename(path)
            if name.startswith(".") or not name.endswith(".json"):
                return
            logger.info(f"calibration file ready: {path}")
            try:
                self.calibration.reload_data(path)
            except FileNotFoundError:
                # Pruner won the race; rare with the default retention window.
                logger.warning(f"calibration file disappeared during apply: {path}")
            except Exception as e:
                logger.exception(f"failed to apply calibration {name}: {e}")

    def __init__(self, config: AppConfig, calibration_file_path: str | None) -> None:
        """
        Initialize the CalibrationBase instance.

        :param AppConfig config: Configuration object for the application.
        :param str | None calibration_file_path: Path to the calibration configuration file (optional).
        :return: None

        Examples::
            >>> config = AppConfig()
            >>> calibration = CalibrationBase(config, "calibration.json")
            >>> print(f"Initialized calibration with {len(calibration.sensors)} sensors")
        """
        self.config = config
        self.observer = None
        self.calibration_info = self._read_config(calibration_file_path)
        if calibration_file_path:
            # Initial load is treated as upsert-all (full snapshot).
            validate_calibration(self.calibration_info, CALIBRATION_ACTION_UPSERT_ALL)
        logger.info(f"Initial loading calibration information...")
        self._load_data()

    def start_listen(self) -> None:
        """
        Set up a file observer to monitor the calibration directory for new calibration files.

        Watches ``CALIBRATION_DIR`` non-recursively -- the listener never
        creates subdirectories, so ``recursive=False`` avoids picking up
        events from any debug subdir an operator might drop in.

        :return: None

        Examples::
            >>> calibration = CalibrationBase(config, calibration_file_path)
            >>> calibration.start_listen()
            >>> print("Started monitoring calibration directory")
        """
        calibration_dir = Path(CALIBRATION_DIR)
        calibration_dir.mkdir(parents=True, exist_ok=True)
        calibration_file_monitor = self.CalibrationFileMonitor(self)
        self.observer = Observer()
        self.observer.schedule(calibration_file_monitor, str(calibration_dir), recursive=False)
        self.observer.start()
        logger.info(f"Started monitoring {calibration_dir}...")

    def close(self) -> None:
        """
        Stop the file observer and close any monitoring activity.

        ``Observer.join`` is called with a finite timeout so that a
        watchdog dispatcher stuck mid-callback (e.g. inside ``reload_data``
        for a particularly large calibration payload) can't block process
        shutdown indefinitely. If the join times out, a warning is logged;
        the daemon Observer thread will be reaped on process exit.

        :return: None

        Examples::
            >>> calibration = CalibrationBase(config, calibration_file_path)
            >>> calibration.start_listen()
            >>> # ... do some work ...
            >>> calibration.close()
            >>> print("Stopped monitoring calibration directory")
        """
        if self.observer:
            self.observer.stop()
            self.observer.join(timeout=5.0)
            if self.observer.is_alive():
                logger.warning(
                    "calibration observer did not terminate within 5s; "
                    "leaving as daemon -- process exit will reap it"
                )
            else:
                logger.info("Stopped monitoring...")

    def reload_data(self, file_path: str) -> None:
        """
        Reload calibration data based on the action type indicated in the filename.
        Actions can be 'upsert-all', 'upsert', or 'delete'.

        :param str file_path: Path to the updated calibration file.
        :return: None

        Examples::
            >>> calibration = CalibrationBase(config, calibration_file_path)
            >>> calibration.reload_data("calibration_upsert.json")
            >>> print("Reloaded calibration data")
        """
        logger.info("Reloading new calibration information...")
        action = os.path.basename(file_path).split(CALIBRATION_FILE_PATTERN)[0]
        new_calibration_info = self._read_config(file_path)
        # Strict per-action schema gate; raises on violation. The watcher's
        # ``on_moved`` wraps reload_data in a try/except, so a bad payload is
        # logged and the previously-good calibration stays loaded.
        validate_calibration(new_calibration_info, action)
        self.update_calibration_info(new_calibration_info, action)
        self._load_data()

    def _load_data(self) -> None:
        """
        Load sensors, and build mappings for sensor and homography data.

        :return: None

        Examples::
            >>> calibration = CalibrationBase(config, calibration_file_path)
            >>> calibration._load_data()
            >>> print(f"Loaded {len(calibration.sensors)} sensors")
        """
        self.sensor_map, self.global_roi_map, self.global_tripwire_map = self._load_sensors()
        # Always log the number of sensors
        logger.info(f"Loaded {len(self.sensor_map)} sensors in total: {list(self.sensor_map.keys())}")
        if self.global_roi_map:
            logger.info(f"Loaded {len(self.global_roi_map)} global ROIs in total: {list(self.global_roi_map.keys())}")
        if self.global_tripwire_map:
            logger.info(f"Loaded {len(self.global_tripwire_map)} global tripwires in total: {list(self.global_tripwire_map.keys())}")
        self.sensors = list(self.sensor_map.values())
        self.global_rois = list(self.global_roi_map.values())
        self.global_tripwires = list(self.global_tripwire_map.values())

    def update_calibration_info(self, new_info: dict[str, Any], action: str) -> None:
        """
        Update the calibration info with new calibration information.
        Note: Only Upsert/Delete sensors are supported. Upsert/Delete global ROIs/Tripwires are not supported yet.

        :param dict[str, Any] new_info: The new calibration info to update with.
        :param str action: The action to perform ('upsert-all', 'upsert', or 'delete').
        :return: None

        Examples::
            >>> calibration = CalibrationBase(config, calibration_file_path)
            >>> new_info = {"sensors": [{"id": "sensor1", "type": "camera"}]}
            >>> calibration.update_calibration_info(new_info, "upsert")
            >>> print("Updated calibration info")
        """
        if action == CALIBRATION_ACTION_UPSERT_ALL:
            logger.info(f"{action} {len(new_info.get('sensors', []))} sensors")
            self.calibration_info = new_info
            return

        # Upsert/Delete sensors
        if "sensors" in self.calibration_info and "sensors" in new_info:
            sensor_map = {sensor["id"]: sensor for sensor in self.calibration_info["sensors"]}
            logger.info(f"{action} {len(sensor_map)} sensors: {sensor_map.keys()}")
            for sensor in new_info["sensors"]:
                if action == CALIBRATION_ACTION_UPSERT:
                    sensor_map[sensor["id"]] = sensor
                elif action == CALIBRATION_ACTION_DELETE:
                    sensor_map.pop(sensor["id"], None)
            self.calibration_info["sensors"] = list(sensor_map.values())

    def get_cam_params(self, sensor_id: str) -> dict[str, list[Any]]:
        """
        Get camera parameters for a given sensor ID.

        :param str sensor_id: The sensor ID to get camera parameters for.
        :return dict[str, list[Any]]: Dictionary containing camera parameters including:
            - camera_position: 3D position of the camera
            - rotation_vector: Camera rotation vector
            - translation_vector: Camera translation vector
            - rotation_matrix: Camera rotation matrix
            - euler_angles: Euler angles (x, y, z)
            - intrinsic_matrix: Camera intrinsic matrix
            - distortion_coefficients: Camera distortion coefficients

        Examples::
            >>> calibration = CalibrationBase(config, calibration_file_path)
            >>> params = calibration.get_cam_params("sensor1")
            >>> print(f"Camera position: {params['camera_position']}")
            >>> print(f"Rotation matrix: {params['rotation_matrix']}")
        """
        camera_params = {}
        for camera in self.sensors:
            if camera.id != sensor_id:
                continue

            image_coordinates = camera.imageCoordinates
            global_coordinates = camera.globalCoordinates
            intrinsic_matrix = np.array(camera.intrinsicMatrix, dtype=np.float32)
            distortion_coefficients = np.array(DEFAULT_DISTORTION_COEFFICIENTS, dtype=np.float32)

            image_coordinate_array = []
            for coord in image_coordinates:
                image_coordinate_array.append((coord.x, coord.y))
            image_coordinate_array = np.array(image_coordinate_array, dtype=np.float32)

            global_coordinate_array = []
            for coord in global_coordinates:
                if self.config.in_3d_mode:
                    global_coordinate_array.append((coord.x, coord.y, coord.z))
                else:
                    global_coordinate_array.append((coord.x, coord.y, 0.0))
            global_coordinate_array = np.array(global_coordinate_array, dtype=np.float32)

            flag = 0
            # flag = cv2.SOLVEPNP_IPPE
            success, rotation_vector, translation_vector = cv2.solvePnP(
                global_coordinate_array,
                image_coordinate_array,
                intrinsic_matrix,
                distortion_coefficients,
                flags=flag,
            )
            if success:
                rotation_matrix = cv2.Rodrigues(rotation_vector)[0]
                camera_position = -np.matrix(rotation_matrix).T * np.matrix(translation_vector)
                rotation = Rotation.from_matrix(rotation_matrix)
                rotation_z, rotation_y, rotation_x = rotation.as_euler("zyx", degrees=True)

                camera_params = {
                    "camera_position": camera_position.tolist(),
                    "rotation_vector": rotation_vector.tolist(),
                    "translation_vector": translation_vector.tolist(),
                    "rotation_matrix": rotation_matrix.tolist(),
                    "euler_angles": [[rotation_x], [rotation_y], [rotation_z]],
                    "intrinsic_matrix": intrinsic_matrix.tolist(),
                    "distortion_coefficients": distortion_coefficients.tolist(),
                }
        return camera_params

    def filter_frames_by_sensor_id(self, input_frames: list[nvSchema.Frame]) -> list[nvSchema.Frame]:
        """
        Filter out frames whose sensor ID is not in the calibration file.

        :param list[nvSchema.Frame] input_frames: List of frames to filter.
        :return list[nvSchema.Frame]: List of frames that have valid sensor IDs in the calibration.

        Examples::
            >>> calibration = CalibrationBase(config, calibration_file_path)
            >>> frames = [nvSchema.Frame(sensorId="sensor1"), nvSchema.Frame(sensorId="sensor2")]
            >>> filtered_frames = calibration.filter_frames_by_sensor_id(frames)
            >>> print(f"Filtered from {len(frames)} to {len(filtered_frames)} frames")
        """
        filtered_frames = []
        for frame in input_frames:
            sensor_id = extract_sensor_id(frame.sensorId)
            if self.contains(sensor_id):
                filtered_frames.append(frame)
            else:
                logger.warning(f"Sensor {sensor_id} not found in calibration")
        return filtered_frames

    def filter_messages_by_roi(self, input_updated_msgs: list[Message]) -> list[Message]:
        """
        Filter messages to only include those whose objects are within ROI polygons.

        This method checks each message's object coordinates against the ROI polygons
        of the corresponding sensor and only includes messages where the object is
        inside at least one ROI.

        :param list[Message] input_updated_msgs: List of messages to filter
        :return list[Message]: List of messages where objects are within ROI polygons

        Examples::
            >>> calibration = CalibrationBase(config, calibration_file_path)
            >>> messages = [Message(sensor=SensorInfo(id="sensor1"), object=Object(coordinate=Coordinate(x=100, y=200)))]
            >>> filtered = calibration.filter_messages_by_roi(messages)
            >>> print(f"Filtered from {len(messages)} to {len(filtered)} messages")
        """
        filtered_msgs = []
        for msg in input_updated_msgs:
            sensor_id = msg.sensor.id
            if self.contains(sensor_id):
                if self.point_in_polygons(msg.object.coordinate, sensor_id):
                    filtered_msgs.append(msg)

        return filtered_msgs

    def _get_roi_polygons(self, rois: list[ROI]) -> dict[str, mplPath.Path]:
        """
        Create a map of ROI polygons from a list of ROI objects.

        :param list[ROI] rois: List of ROI objects to convert to polygons.
        :return dict[str, mplPath.Path]: Dictionary mapping ROI IDs to their corresponding polygon paths.

        Examples::
            >>> calibration = CalibrationBase(config, calibration_file_path)
            >>> roi_list = [ROI(id="roi1", roiCoordinates=[...]), ROI(id="roi2", roiCoordinates=[...])]
            >>> polygons = calibration._get_roi_polygons(roi_list)
            >>> for roi_id, polygon in polygons.items():
            ...     print(f"ROI {roi_id} has {len(polygon.vertices)} vertices")
        """
        result = {}
        for roi in rois:
            roi_coords: list[tuple[float, float]] = list()
            for coord in roi.roiCoordinates:
                roi_coords.append((coord.x, coord.y))
            result[roi.id] = mplPath.Path(roi_coords)
        return result

    def roi_restricted_types(self, sensor_id: str) -> dict[str, list[str]]:
        """
        Get the restricted object types for each ROI of a sensor.

        :param str sensor_id: The sensor ID to get restricted types for.
        :return dict[str, list[str]]: Dictionary mapping ROI IDs to their restricted object types.

        Examples::
            >>> calibration = CalibrationBase(config, calibration_file_path)
            >>> restricted_types = calibration.roi_restricted_types("sensor1")
            >>> for roi_id, types in restricted_types.items():
            ...     print(f"ROI {roi_id} restricts: {', '.join(types)}")
        """
        sensor = self.sensor_map.get(sensor_id, None)
        if not sensor or not sensor.rois:
            return {}

        result = {}
        for sensor_roi in sensor.rois:
            result[sensor_roi.id] = sensor_roi.restrictedObjectTypes
        return result

    def _roi_confined_types(self, sensor_id: str) -> dict[str, list[str]]:
        """
        Get the confined object types for each ROI of a sensor.

        :param str sensor_id: The sensor ID to get confined types for.
        :return dict[str, list[str]]: Dictionary mapping ROI IDs to their confined object types.

        Examples::
            >>> calibration = CalibrationBase(config, calibration_file_path)
            >>> confined_types = calibration._roi_confined_types("sensor1")
            >>> for roi_id, types in confined_types.items():
            ...     print(f"ROI {roi_id} confines: {', '.join(types)}")
        """
        sensor = self.sensor_map.get(sensor_id, None)
        if not sensor or not sensor.rois:
            return {}

        result = {}
        for sensor_roi in sensor.rois:
            result[sensor_roi.id] = sensor_roi.confinedObjectTypes
        return result

    def _compute_hmatrix(
        self,
        image_coordinates: list[Coordinate],
        global_coordinates: list[Coordinate],
    ) -> list[list[float]] | None:
        """
        Compute the homography matrix from matched image and global coordinates.

        :param list[Coordinate] image_coordinates: List of image coordinates.
        :param list[Coordinate] global_coordinates: List of corresponding global coordinates.
        :return list[list[float]] | None: Computed homography matrix or None if computation fails.
        :raises ValueError: If the number of coordinates is less than 4 or if the lengths of input lists don't match.

        Examples::
            >>> calibration = CalibrationBase(config, calibration_file_path)
            >>> image_coords = [Coordinate(x=0, y=0), Coordinate(x=1, y=0),
            ...                 Coordinate(x=1, y=1), Coordinate(x=0, y=1)]
            >>> global_coords = [Coordinate(x=0, y=0), Coordinate(x=10, y=0),
            ...                  Coordinate(x=10, y=10), Coordinate(x=0, y=10)]
            >>> hmatrix = calibration._compute_hmatrix(image_coords, global_coords)
            >>> if hmatrix:
            ...     print("Homography matrix computed successfully")
        """
        if len(image_coordinates) != len(global_coordinates):
            logger.warning(
                f"The lengths of image coordinates and global coordinates do NOT match -- "
                f"{len(image_coordinates)} != {len(global_coordinates)}."
            )
            return None

        if len(image_coordinates) < 4:
            logger.warning(f"The length of coordinates {len(image_coordinates)} is less than 4.")
            return None

        image_coordinate_array = list()
        for coord in image_coordinates:
            image_coordinate_array.append((coord.x, coord.y))
        image_coordinate_array = np.array(image_coordinate_array)

        global_coordinate_array = list()
        for coord in global_coordinates:
            global_coordinate_array.append((coord.x, coord.y))
        global_coordinate_array = np.array(global_coordinate_array)

        h_matrix, _ = cv2.findHomography(
            image_coordinate_array,
            global_coordinate_array,
            method=cv2.RANSAC,
            ransacReprojThreshold=3,
        )

        if self.config.in_3d_mode:
            h_matrix = self._compute_camera_projection_matrix_3d(image_coordinates, global_coordinates)
            # Homography matrix is the first two rows of the projection matrix
            h_matrix = h_matrix[:, [0, 1, 3]]

        return h_matrix.tolist()

    def perspective_transform(
        self, px: float, py: float, homography: list[list[float]] | None
    ) -> tuple[float, float] | None:
        """
        Transform a 2D pixel coordinate to a location on the ground plane using homography.

        :param float px: 2D pixel location x-coordinate
        :param float py: 2D pixel location y-coordinate
        :param list[list[float]] | None homography: 3x3 homography matrix for transformation
        :return tuple[float, float] | None: Transformed coordinates on the ground plane (x, y) or None if transformation fails

        Examples::
            >>> calibration = CalibrationBase(config, calibration_file_path)
            >>> transformed = calibration.perspective_transform(100, 200, homography_matrix)
            >>> if transformed:
            ...     print(f"Transformed coordinates: {transformed}")
        """
        if not homography:
            return None
        w = homography[2][0] * px + homography[2][1] * py + homography[2][2]
        if w == 0:
            return None
        transformed_x = (homography[0][0] * px + homography[0][1] * py + homography[0][2]) / w
        transformed_y = (homography[1][0] * px + homography[1][1] * py + homography[1][2]) / w
        return transformed_x, transformed_y

    def _compute_camera_projection_matrix_3d(
        self, image_coords: list[Coordinate], global_coords: list[Coordinate]
    ) -> np.ndarray:
        """
        Compute the camera projection matrix for 3D points.

        This method calculates the 3x4 camera projection matrix that maps 3D world
        coordinates to 2D image coordinates using a set of corresponding points.

        :param list[Coordinate] image_coords: List of 2D image coordinates
        :param list[Coordinate] global_coords: List of corresponding 3D world coordinates
        :return np.ndarray: 3x4 camera projection matrix
        :raises ValueError: If the number of points is insufficient or if the points are degenerate

        Examples::
            >>> calibration = CalibrationBase(config, calibration_file_path)
            >>> image_points = [Coordinate(x=0, y=0), Coordinate(x=1, y=0)]
            >>> world_points = [Coordinate(x=0, y=0, z=0), Coordinate(x=1, y=0, z=0)]
            >>> proj_matrix = calibration._compute_camera_projection_matrix_3d(image_points, world_points)
            >>> print(f"Projection matrix shape: {proj_matrix.shape}")
        """
        # Ensure the points are in the correct shape
        image_coordinate_array = list()
        for coord in image_coords:
            image_coordinate_array.append((coord.x, coord.y))
        image_coordinate_array = np.array(image_coordinate_array)

        global_coordinate_array = list()
        for coord in global_coords:
            global_coordinate_array.append((coord.x, coord.y, coord.z))
        global_coordinate_array = np.array(global_coordinate_array)

        three_d_points = np.array(global_coordinate_array, dtype=np.float64)
        two_d_points = np.array(image_coordinate_array, dtype=np.float64)
        num_points = three_d_points.shape[0]
        A = []
        for i in range(num_points):
            X, Y, Z = three_d_points[i, :]
            x, y = two_d_points[i, :]
            A.append([-X, -Y, -Z, -1, 0, 0, 0, 0, x * X, x * Y, x * Z, x])
            A.append([0, 0, 0, 0, -X, -Y, -Z, -1, y * X, y * Y, y * Z, y])
        A = np.array(A)
        U, S, Vh = np.linalg.svd(A)
        L = Vh[-1, :] / Vh[-1, -1]  # Normalize
        camera_projection_matrix = L.reshape(3, 4)

        return camera_projection_matrix

    def contains(self, sensor_id: str) -> bool:
        """
        Check if a sensor ID exists in the sensor map.

        :param str sensor_id: The sensor ID to check for existence.
        :return bool: True if the sensor ID exists in the sensor map, False otherwise.

        Examples::
            >>> calibration = CalibrationBase(config, calibration_file_path)
            >>> if calibration.contains("sensor1"):
            ...     print("Sensor1 exists in the calibration")
            ... else:
            ...     print("Sensor1 not found in calibration")
        """
        return sensor_id in self.sensor_map

    def reprojection_error(
        self,
        image_p: Coordinate | nvSchema.Coordinate,
        expected: Coordinate | nvSchema.Coordinate,
        h_matrix: list[list[float]],
    ) -> float:
        """
        Calculate the reprojection error between an image point and its expected position.

        This method computes the Euclidean distance between the original image point
        and its position after being transformed by the homography matrix and back.

        :param Coordinate | nvSchema.Coordinate image_p: The original image point coordinates
        :param Coordinate | nvSchema.Coordinate expected: The expected position of the point
        :param list[list[float]] h_matrix: The homography matrix used for transformation
        :return float: The reprojection error in pixels

        Examples::
            >>> calibration = CalibrationBase(config, calibration_file_path)
            >>> image_point = (100, 200)
            >>> expected_point = (110, 210)
            >>> h_matrix = [[1, 0, 0], [0, 1, 0], [0, 0, 1]]
            >>> error = calibration.reprojection_error(image_point, expected_point, h_matrix)
            >>> print(f"Reprojection error: {error:.2f} pixels")
        """
        return 0.0

    def transform_bbox(self, bbox: Bbox | Bbox3d, sensor_id: str) -> tuple[Coordinate, Location]:
        """
        Transform a 2d or 3d bounding box from image coordinates to real-world coordinates.

        :param Bbox | Bbox3d bbox: The bounding box to transform.
        :param str sensor_id: The sensor ID to use for transformation.
        :return tuple[Coordinate, Location]: Tuple containing the transformed coordinate and location.
        :raises NotImplementedError: This method must be implemented by child classes.
        """
        raise NotImplementedError("transform_bbox is not implemented")

    def point_in_tripwire(
        self, point: Coordinate | Point2D | nvSchema.Coordinate | nvSchema.Point2D, sensor_id: str, tripwire_id: str
    ) -> bool:
        """
        Check if a point is on the in-direction side of a tripwire. If a point is on the line, it is considered as outside.

        :param Coordinate | Point2D, nvSchema.Coordinate, nvSchema.Point2D point: The point
        :param str sensor_id: The sensor ID
        :param str tripwire_id: The tripwire ID
        :return bool: True if the point is inside the tripwire, False otherwise

        Examples::
            >>> calibration = CalibrationBase(config, calibration_file_path)
            >>> point = Coordinate(x=100, y=200)
            >>> if calibration.point_in_tripwire(point, "sensor1", "tripwire1"):
            ...     print("Point is inside tripwire")
            ... else:
        """
        tripwire = self.sensor_map[sensor_id].tripwires[tripwire_id]
        return tripwire.in_orientation == orientation(tripwire.wires, point)

    def point_in_polygon(
        self, point: Coordinate | Point2D | nvSchema.Coordinate | nvSchema.Point2D, sensor_id: str, roi_id: str
    ) -> bool:
        """
        Check if a point lies within a specified ROI polygon.

        :param Coordinate | Point2D, nvSchema.Coordinate, nvSchema.Point2D point: The point to check, must have x and y attributes.
        :param str sensor_id: The sensor ID containing the ROI.
        :param str roi_id: The ID of the ROI to check against.
        :return bool: True if the point is inside the ROI polygon, False otherwise.
        :raises KeyError: If the sensor_id or roi_id is not found.

        Examples::
            >>> calibration = CalibrationBase(config, calibration_file_path)
            >>> point = Coordinate(x=100, y=200)
            >>> if calibration.point_in_polygon(point, "sensor1", "roi1"):
            ...     print("Point is inside ROI1")
            ... else:
            ...     print("Point is outside ROI1")
        """
        roi_polygon_map = self.sensor_map[sensor_id].roiPolygons
        roi_polygon = roi_polygon_map.get(roi_id)
        return roi_polygon.contains_point((point.x, point.y)) if roi_polygon else False

    def point_in_polygons(
        self, point: Coordinate | Point2D | nvSchema.Coordinate | nvSchema.Point2D, sensor_id: str
    ) -> bool:
        """
        Check if a point is inside roi polygons defined by the sensor.

        :param Coordinate | Point2D, nvSchema.Coordinate, nvSchema.Point2D point: the protobuf coordinate to check
        :param str sensor_id: the sensor id
        :return bool: True if the point is inside the polygon, False otherwise

        Examples::

            if calibration.point_in_polygons(point, sensor_id):
                pass
        """
        roi_polygon_map = self.sensor_map[sensor_id].roiPolygons
        for roi_polygon in roi_polygon_map.values():
            if roi_polygon.contains_point((point.x, point.y)):
                return True
        return False

    def transform(self, msg: Message) -> Message:
        """
        Transform coordinates in a message and update location/place based on configuration.

        :param Message msg: The message containing coordinates to transform.
        :return Message: Updated message with transformed coordinates and updated location/place.

        Examples::
            >>> calibration = CalibrationBase(config, calibration_file_path)
            >>> msg = Message(sensor=SensorInfo(id="sensor1"), object=Object(bbox=Bbox(...)))
            >>> transformed_msg = calibration.transform(msg)
            >>> print(f"Transformed message for sensor {transformed_msg.sensor.id}")
        """
        sensor_description = msg.sensor.id
        sensor_id = extract_sensor_id(sensor_description)
        sensor = msg.sensor.model_copy(update={"id": sensor_id, "description": sensor_description})

        # Update place
        place = None
        if self.contains(sensor_id):
            place_name = "/".join(f"{p['name']}={p['value']}" for p in self.sensor_map[sensor_id].place)
            place = msg.place.model_copy(update={"name": place_name})
        else:
            place = msg.place.model_copy()

        # Transform Bbox
        updated_obj = None  # Dummy message has no objects in simulation mode
        if msg.object is not None:
            updated_obj = msg.object.model_copy()
            if not self.config.use_object_location:
                bbox = msg.object.bbox3d if self.config.in_3d_mode else msg.object.bbox
                coordinate, latlon = self.transform_bbox(bbox, sensor_id)
                updated_obj.location = latlon
                updated_obj.coordinate = coordinate

        # Update message
        updated_msg = msg.model_copy(update={"sensor": sensor, "object": updated_obj, "place": place})

        return updated_msg

    def transform_frame(self, frame: nvSchema.Frame) -> nvSchema.Frame:
        """
        Transform and enhance frames with ROI count, safety detection, and proximity information.

        This method:
        1. Transforms object coordinates
        2. Updates ROI metrics and restricted area violations
        3. Creates field of view (FOV) information
        4. Performs safety proximity detection
        5. Optionally creates compact frame with only important objects

        :param nvSchema.Frame frame: The frame to transform and enhance
        :return nvSchema.Frame: Updated frame with transformed coordinates and enhanced information
        :raises ValueError: If sensor configuration is invalid

        Examples::
            >>> calibration = CalibrationBase(config, calibration_file_path)
            >>> frame = nvSchema.Frame(sensorId="sensor1", objects=[...])
            >>> transformed_frame = calibration.transform_frame(frame)
            >>> print(f"Transformed frame for sensor {transformed_frame.sensorId}")
            >>> print(f"ROI counts: {[roi.count for roi in transformed_frame.rois]}")
        """
        sensor_id = frame.sensorId
        sensor_id = extract_sensor_id(sensor_id)
        proximity_config = self.config.get_sensor_proximity_detection_config(sensor_id)

        updated_objects: list[nvSchema.Object] = []
        for obj in frame.objects:
            bbox = nv_bbox3d_to_bbox3d(obj.bbox3d) if self.config.in_3d_mode else nv_bbox_to_bbox(obj.bbox)
            coor, loc = self.transform_bbox(bbox, sensor_id)
            obj.coordinate.CopyFrom(coordinate_to_nv_coordinate(coor))
            obj.location.CopyFrom(location_to_nv_location(loc))
            updated_objects.append(obj)

        # object id, type, bbox coordinate
        points = [(obj.id, obj.type, obj.coordinate) for obj in updated_objects]

        # Get rois
        rois = self.get_roi_metrics(points, sensor_id)
        self.update_roi_info(rois, sensor_id)  # restrictedAreaViolation info

        # Get fov
        fov = self.get_fov(points)

        # Proximity detection
        # Filter out points that are of center object types
        sd = None
        center_objects = [
            (p, id) for id, type, p in points if type in proximity_config.centerClasses
        ]
        object_id_groups = []  # Initialize to empty list
        if proximity_config.enable:
            # Filter out points that are of surrounding object types
            surrounding_objects: list[tuple[nvSchema.Coordinate, str]] = [
                (p, id) for id, type, p in points if type in proximity_config.surroundingClasses
            ]

            clusters, object_id_groups = ProximityDetection.cluster(
                center_objects, surrounding_objects, proximity_config.threshold
            )
            # Get violating groups (clusters with more than one protected object)
            clusters = [cluster for cluster in clusters if len(cluster.points) > 1]
            object_id_groups = [group for group in object_id_groups if len(group) > 1]
            object_id_groups_str = "|".join(",".join(str(id) for id in group) for group in object_id_groups)

            # Create a SD object to hold the social distancing results
            sd = nvSchema.SD(
                threshold=proximity_config.threshold,
                proximityDetections=len(clusters),
                clusters=clusters,
                info=(
                    {"proximityViolation": "true", "proximityViolationObjects": object_id_groups_str}
                    if len(clusters) > 0
                    else {"proximityViolation": "false"}
                ),
            )

        # Safety confined area violation detection
        confined_area_types, confined_area_object_groups = self.get_confined_area_violation_info(fov, rois, sensor_id)
        if confined_area_types:
            info = (
                {
                    "confinedAreaViolation": "true",
                    "confinedAreaTypes": "|".join(confined_area_types),
                    "confinedAreaViolationObjects": "|".join(
                        ",".join(str(id) for id in group) for group in confined_area_object_groups
                    ),
                }
            )
        else:
            info = {"confinedAreaViolation": "false"}

        # Update the info dictionary with place info
        if self.contains(sensor_id):
            sensor_c = self.sensor_map[sensor_id]
            place_name = "/".join([f"{x['name']}={x['value']}" for x in sensor_c.place])
            info.update({"place": place_name})
        else:
            info.update({"place": ""})

        # Determine whether to include compactObjects or not
        if self.config.compact_frame:
            # Include all important objects in compact objects
            object_ids = set()
            for group in object_id_groups:
                for id in group:
                    object_ids.add(id)
            for group in confined_area_object_groups:
                for id in group:
                    object_ids.add(id)
            for roi in rois:
                for id in roi.objectIds:
                    object_ids.add(id)

            compact_objects = [obj for obj in updated_objects if obj.id in object_ids]
        else:
            compact_objects = updated_objects

        # Return the Frame object
        return nvSchema.Frame(
            version=frame.version,
            id=frame.id,
            timestamp=frame.timestamp,
            sensorId=sensor_id,
            objects=compact_objects,
            fov=fov,
            rois=rois,
            socialDistancing=sd,
            info=info,
        )

    def _read_config(self, file_path: str | None) -> dict[str, Any]:
        """
        Read and parse the calibration configuration from a JSON file.

        :param str | None file_path: Path to the calibration configuration JSON file.
        :return dict[str, Any]: Dictionary containing the parsed calibration configuration.
        :raises FileNotFoundError: If the specified file does not exist.
        :raises json.JSONDecodeError: If the file contains invalid JSON.

        Examples::
            >>> calibration = CalibrationBase(config, calibration_file_path)
            >>> config_data = calibration._read_config("calibration.json")
            >>> print(f"Loaded configuration with {len(config_data.get('sensors', []))} sensors")
        """
        if not file_path:
            return {"sensors": [], "rois": [], "tripwires": []}
        return load_json_from_file(file_path)

    def _update_h_matrix(self, sensor: SensorInfo) -> list[list[float]] | None:
        """
        Update or compute the homography matrix for a given sensor.

        :param SensorInfo sensor: The sensor information object.
        :return list[list[float]] | None: Updated homography matrix or None if computation fails.

        Examples::
            >>> calibration = CalibrationBase(config, calibration_file_path)
            >>> sensor = SensorInfo(id="cam1", type=SENSOR_TYPE_CAMERA)
            >>> h_matrix = calibration._update_h_matrix(sensor)
            >>> if h_matrix:
            ...     print("Homography matrix updated successfully")
        """
        h_matrix = None
        if sensor.type == SENSOR_TYPE_CAMERA:
            h_matrix = sensor.homography
            if h_matrix is None:
                h_matrix = self._compute_hmatrix(sensor.imageCoordinates, sensor.globalCoordinates)
        return h_matrix

    def _load_sensors(self) -> tuple[dict[str, SensorInfo], dict[str, ROI], dict[str, Tripwire]]:
        """
        Load sensors with their associated global ROIs and tripwires from calibration info.

        :return tuple[dict[str, SensorInfo], dict[str, ROI], dict[str, Tripwire]]: Tuple containing:
            - Dictionary mapping sensor IDs to SensorInfo objects
            - Dictionary mapping ROI IDs to ROI objects
            - Dictionary mapping tripwire IDs to Tripwire objects

        Examples::
            >>> calibration = CalibrationBase(config, calibration_file_path)
            >>> sensor_map, roi_map, tripwire_map = calibration._load_sensors()
            >>> print(f"Loaded {len(sensor_map)} sensors, {len(roi_map)} ROIs, {len(tripwire_map)} tripwires")
        """
        calibration_info = self.calibration_info
        sensors: list[SensorInfo] = []
        for sensor_info in calibration_info["sensors"]:
            place = sensor_info.get("place", [])
            if calibration_info.get("city") and calibration_info["city"].strip() != "":
                place = [{"name": "city", "value": calibration_info["city"].strip()}] + place

            group = sensor_info.get("group")
            if group and group.get("name", ""):
                group_sensor = SensorInfo(id=group["name"], type=SENSOR_TYPE_GROUP, place=place, group=group)
                sensors.append(group_sensor)

            sensor = SensorInfo(
                id=sensor_info["id"],
                type=sensor_info["type"],
                place=place,
                group=group,
                translationToGlobalCoordinates=sensor_info.get("translationToGlobalCoordinates", DEFAULT_COORDINATE),
                origin=dict_location_to_location(sensor_info.get("origin", {})),
                geoLocation=dict_location_to_location(sensor_info.get("geoLocation", {})),
                imageCoordinates=list_coordinates_to_coordinates(sensor_info.get("imageCoordinates", [])),
                globalCoordinates=list_coordinates_to_coordinates(sensor_info.get("globalCoordinates", [])),
                homography=sensor_info.get("homography"),
                rois=list_rois_to_rois(sensor_info.get("rois", [])),
                tripwires=list_tripwires_to_tripwires_map(sensor_info.get("tripwires", [])),
                coordinates=sensor_info.get("coordinates"),
                scaleFactor=sensor_info.get("scaleFactor"),
                attributes=list_attributes_to_attributes_map(sensor_info.get("attributes", [])),
                region=sensor_info.get("region"),
                intrinsicMatrix=sensor_info.get("intrinsicMatrix"),
                extrinsicMatrix=sensor_info.get("extrinsicMatrix"),
                cameraMatrix=sensor_info.get("cameraMatrix"),
            )
            # Update homography matrix
            sensor.homography = self._update_h_matrix(sensor)
            sensors.append(sensor)
        sensor_map = {sensor.id: sensor for sensor in sensors}

        global_rois: list[ROI] = list_rois_to_rois(calibration_info.get("rois", []))
        global_roi_map = {roi.id: roi for roi in global_rois}
        # Update sensor_map with global rois
        for roi in global_rois:
            for sensor_id in roi.sensors:
                if sensor_id in sensor_map:
                    sensor_map[sensor_id].rois.append(roi)
                else:
                    sensor_map[sensor_id] = SensorInfo(id=sensor_id, type=SENSOR_TYPE_CAMERA, rois=[roi])
            for group_id in roi.groups:
                if group_id in sensor_map:
                    sensor_map[group_id].rois.append(roi)
                else:
                    sensor_map[group_id] = SensorInfo(
                        id=group_id,
                        type=SENSOR_TYPE_GROUP,
                        place=self.get_group_place(group_id, sensors),
                        rois=[roi],
                    )
        # Update sensor_map with roi polygons
        for sensor_id in sensor_map:
            sensor = sensor_map[sensor_id]
            roi_polygons = self._get_roi_polygons(sensor.rois)
            sensor_map[sensor_id].roiPolygons = roi_polygons

        # Update sensor_map with global tripwires
        global_tripwires: list[Tripwire] = list_tripwires_to_tripwires(calibration_info.get("tripwires", []))
        global_tripwire_map = {tripwire.id: tripwire for tripwire in global_tripwires}
        for tripwire in global_tripwires:
            for sensor_id in tripwire.sensors:
                if sensor_id in sensor_map:
                    sensor_map[sensor_id].tripwires[tripwire.id] = tripwire
                else:
                    sensor_map[sensor_id] = SensorInfo(
                        id=sensor_id, type=SENSOR_TYPE_CAMERA, tripwires={tripwire.id: tripwire}
                    )

            for group_id in tripwire.groups:
                if group_id in sensor_map:
                    sensor_map[group_id].tripwires[tripwire.id] = tripwire
                else:
                    sensor_map[group_id] = SensorInfo(
                        id=group_id,
                        type=SENSOR_TYPE_GROUP,
                        place=self.get_group_place(group_id, sensors),
                        tripwires={tripwire.id: tripwire},
                    )
        return sensor_map, global_roi_map, global_tripwire_map

    def get_roi_metrics(
        self, points: list[tuple[str, str, nvSchema.Coordinate]], sensor_id: str
    ) -> list[nvSchema.TypeMetrics]:
        """
        Get the ROI metrics for a given sensor based on the provided points.

        This method calculates metrics for each ROI in the sensor's field of view,
        including object counts and coordinates for each object type.

        :param list[tuple[str, str, nvSchema.Coordinate]] points:
            List of tuples containing (object_id, object_type, coordinate)
        :param str sensor_id: The sensor ID to get ROI metrics for
        :return list[nvSchema.TypeMetrics]: List of TypeMetrics objects containing ROI information

        Examples::
            >>> calibration = CalibrationBase(config, calibration_file_path)
            >>> points = [("obj1", "person", coordinate1), ("obj2", "car", coordinate2)]
            >>> metrics = calibration.get_roi_metrics(points, "sensor1")
            >>> for metric in metrics:
            ...     print(f"ROI {metric.id}: {metric.type} count={metric.count}")
        """
        roi_metrics = []
        if self.contains(sensor_id):
            roi_polygons = self.sensor_map[sensor_id].roiPolygons
            if roi_polygons is None:
                return roi_metrics
            for roi_id in roi_polygons:
                points_within_roi = [
                    (id, t, pt) for id, t, pt in points if self.point_in_polygon(pt, sensor_id, roi_id)
                ]
                points_within_roi.sort(key=lambda x: x[1])  # sort by object type
                grouped_points_within_roi = groupby(points_within_roi, key=lambda x: x[1])  # group by object type
                for t, pts_group in grouped_points_within_roi:
                    pts = list(pts_group)
                    coors = [p[2] for p in pts]
                    ids = [p[0] for p in pts]
                    roi_metrics.append(
                        nvSchema.TypeMetrics(
                            id=roi_id,
                            type=t,
                            count=len(coors),
                            coordinates=coors,
                            objectIds=ids,
                        )
                    )
        return roi_metrics

    def get_fov(self, points: list[tuple[str, str, nvSchema.Coordinate]]) -> list[nvSchema.TypeMetrics]:
        """
        Get the field of view (FOV) metrics based on the provided points.

        This method calculates metrics for objects in the field of view,
        including object counts and IDs for each object type.

        :param list[tuple[str, str, nvSchema.Coordinate]] points:
            List of tuples containing (object_id, object_type, coordinate)
        :return list[nvSchema.TypeMetrics]: List of TypeMetrics objects containing FOV information

        Examples::
            >>> calibration = CalibrationBase(config, calibration_file_path)
            >>> points = [("obj1", "person", coordinate1), ("obj2", "car", coordinate2)]
            >>> fov_metrics = calibration.get_fov(points)
            >>> for metric in fov_metrics:
            ...     print(f"Type {metric.type}: count={metric.count}")
        """
        fov = []
        # Group points by type and collect object IDs
        type_to_ids = {}
        for obj_id, obj_type, _ in points:
            if obj_type not in type_to_ids:
                type_to_ids[obj_type] = []
            type_to_ids[obj_type].append(obj_id)

        for obj_type, obj_ids in type_to_ids.items():
            fov.append(nvSchema.TypeMetrics(id="", type=obj_type, count=len(obj_ids), objectIds=obj_ids))

        return fov

    def _get_fov_objects_by_type(self, fov: list[nvSchema.TypeMetrics], type: str) -> set[str]:
        """
        Get the set of object IDs for a specific type from the FOV metrics.

        :param list[nvSchema.TypeMetrics] fov: List of FOV metrics containing object information.
        :param str type: The object type to filter by.
        :return set[str]: Set of object IDs for the specified type.

        Examples::
            >>> calibration = CalibrationBase(config, calibration_file_path)
            >>> fov_metrics = calibration.get_fov(points)
            >>> person_ids = calibration._get_fov_objects_by_type(fov_metrics, "person")
            >>> print(f"Found {len(person_ids)} people in FOV")
        """
        for fov_item in fov:
            if fov_item.type == type:
                return set(fov_item.objectIds)
        return set()

    def _get_roi_objects_by_type(
        self, rois: list[nvSchema.TypeMetrics], type: str, roi_confined_types: dict[str, list[str]]
    ) -> set[str]:
        """
        Get the set of object IDs for a specific type from ROI metrics, filtered by confined types.

        :param list[nvSchema.TypeMetrics] rois: List of ROI metrics containing object information.
        :param str type: The object type to filter by.
        :param dict[str, list[str]] roi_confined_types: Dictionary mapping ROI IDs to their confined object types.
        :return set[str]: Set of object IDs for the specified type that are confined to the ROIs.

        Examples::
            >>> calibration = CalibrationBase(config, calibration_file_path)
            >>> roi_metrics = calibration.get_roi_metrics(points, "sensor1")
            >>> confined_types = {"roi1": ["person"], "roi2": ["car"]}
            >>> person_ids = calibration._get_roi_objects_by_type(roi_metrics, "person", confined_types)
            >>> print(f"Found {len(person_ids)} people in confined ROIs")
        """
        object_ids = set()
        for roi in rois:
            if type in roi_confined_types.get(roi.id, []) and roi.type == type:
                object_ids.update(roi.objectIds)
        return object_ids

    def get_group_place(self, group_id: str, sensors: list[SensorInfo]) -> list[dict[str, str]]:
        """
        Get the place information for a given BEV group.

        :param str group_id: The group ID to get place information for.
        :param list[SensorInfo] sensors: List of sensor information objects to search through.
        :return list[dict[str, str]]: List of place dictionaries containing name-value pairs for the group.
        :raises KeyError: If the group ID is not found in any sensor's group information.

        Examples::
            >>> calibration = CalibrationBase(config, calibration_file_path)
            >>> group_place = calibration.get_group_place("group1", sensors)
            >>> print(group_place)
            [{"name": "city", "value": "San Francisco"}, {"name": "building", "value": "Main"}]
        """
        for sensor in sensors:
            if sensor.group["name"] == group_id:
                return sensor.place
        return []

    def update_roi_info(self, rois: list[nvSchema.TypeMetrics], sensor_id: str) -> None:
        """
        Update the ROI information for the sensor with restricted area violations.

        This method checks each ROI against the sensor's restricted object types
        and updates the ROI info with violation status.

        :param list[nvSchema.TypeMetrics] rois: List of ROI metrics to update
        :param str sensor_id: The sensor ID to update ROI info for
        :raises KeyError: If sensor_id is not found in the sensor map
        :return: None

        Examples::
            >>> calibration = CalibrationBase(config, calibration_file_path)
            >>> rois = [nvSchema.TypeMetrics(id="roi1", type="Person", count=2)]
            >>> calibration.update_roi_info(rois, "sensor1")
            >>> print(f"ROI violation status: {rois[0].info.get('restrictedAreaViolation')}")
        """
        roi_restricted_types = self.roi_restricted_types(sensor_id)
        for roi in rois:
            if roi.id in roi_restricted_types:
                if roi.type in roi_restricted_types[roi.id]:
                    roi.info.update({"restrictedAreaViolation": "true"})
                else:
                    roi.info.update({"restrictedAreaViolation": "false"})

    def get_confined_area_violation_info(
        self, fov: list[nvSchema.TypeMetrics], rois: list[nvSchema.TypeMetrics], sensor_id: str
    ) -> tuple[list[str], list[list[str]]]:
        """
        Get information about confined area violations for objects in the field of view.

        This method:
        1. Gets the confined object types for each ROI
        2. Checks which objects are outside their confined areas
        3. Returns the violating object types and their IDs

        :param list[nvSchema.TypeMetrics] fov: List of objects in the field of view
        :param list[nvSchema.TypeMetrics] rois: List of ROI metrics
        :param str sensor_id: The sensor ID to check violations for
        :return tuple[list[str], list[list[str]]]: Tuple containing:
            - List of object types that are violating confined areas
            - List of object ID groups that are violating confined areas

        Examples::
            >>> calibration = CalibrationBase(config, calibration_file_path)
            >>> fov = [nvSchema.TypeMetrics(type="Person", objectIds=["1", "2"])]
            >>> rois = [nvSchema.TypeMetrics(id="roi1", type="Person", objectIds=["1"])]
            >>> types, groups = calibration.get_confined_area_violation_info(fov, rois, "sensor1")
            >>> print(f"Violating types: {types}, Object groups: {groups}")
        """
        roi_confined_types = self._roi_confined_types(sensor_id)
        roi_confined_types_set = {type for types in roi_confined_types.values() for type in types}
        confined_area_types = []
        confined_area_object_groups = []
        for type in roi_confined_types_set:
            fov_objects = self._get_fov_objects_by_type(fov, type)
            roi_objects = self._get_roi_objects_by_type(rois, type, roi_confined_types)
            objects_outside_roi = set(fov_objects) - set(roi_objects)
            if len(objects_outside_roi) > 0:
                confined_area_types.append(type)
                confined_area_object_groups.append(list(objects_outside_roi))

        return confined_area_types, confined_area_object_groups

    @property
    def calibration_type(self) -> CalibrationType:
        """
        Get the calibration type for this calibrator.
        
        Subclasses should override this to return their specific type.
        
        :return CalibrationType: The calibration type enum
        """
        raise NotImplementedError("Subclasses must implement this method")