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

import logging
import math
import time
from datetime import datetime
from typing import Any

import cv2
import numpy as np
from scipy.spatial import ConvexHull
from shapely.geometry import MultiPolygon, Polygon

from mdx.analytics.core.schema.config import SpaceAnalyticsConfig
from mdx.analytics.core.schema.models import (
    Bbox3d,
    Message,
    Point2D,
    PolygonCoords,
    PolygonHole,
    SpaceUtilization,
    SpaceUtilizationLayouts,
    SpaceUtilizationMetrics,
)
from mdx.analytics.core.schema.proto import schema_pb2 as nvSchema
from mdx.analytics.core.schema.proto import ext_pb2 as extSchema
from mdx.analytics.core.transform.calibration.calibration_dynamic import DynamicCalibration
from mdx.analytics.core.transform.calibration.calibration_e import CalibrationE
from mdx.analytics.core.utils.genetic_algorithm import genetic_algorithm
from mdx.analytics.core.utils.greedy_search import convert_pallet_bottom_left_loc_to_polygon, greedy_search

logger = logging.getLogger(__name__)


class SpaceAnalyzer:
    """A class for analyzing space utilization in warehouse environments.

    This class provides functionality for analyzing space usage, detecting safety violations,
    and visualizing various spatial metrics in warehouse environments. It handles coordinate
    transformations, pallet placement optimization, and visualization of space utilization.

    :ivar SpaceAnalyticsConfig config: Configuration object containing analysis parameters
    :ivar CalibrationE | DynamicCalibration calibration: Calibration object for coordinate transformations

    Example::
        >>> config = SpaceAnalyticsConfig(gridSize=0.1, unsafeSize=0.5)
        >>> analyzer = SpaceAnalyzer(config, calibration)
        >>> results = analyzer.analyze(msg_map, pallet_width=1.0)
    """

    def __init__(self, config: SpaceAnalyticsConfig, calibration: CalibrationE) -> None:
        """Initialize the SpaceAnalyzer.

        :param SpaceAnalyticsConfig config: Configuration object containing analysis parameters
        :param CalibrationE calibration: Calibration object for coordinate transformations
        """
        self.config = config
        self.calibration = calibration
        self._import_parameters_from_config(config)
        self._set_buffer_zone_dimension()

    def _import_parameters_from_config(self, config: SpaceAnalyticsConfig) -> None:
        """Import analysis parameters from configuration.

        :param SpaceAnalyticsConfig config: Configuration object containing analysis parameters
        :return: None
        """
        if config:
            self.grid_size = config.gridSize
            self.unsafe_size = config.unsafeSize
            self.targets = config.targetObjects
            self.use_GA = config.useGA
            self.population_size = config.populationSizeGA
            self.generations = config.numGenerationsGA

    def _set_buffer_zone_dimension(self) -> None:
        """Set dimensions for buffer zones.

        This method initializes buffer zone dimensions and geometries based on calibration data.
        It stores width, height, and polygon geometry for each buffer zone in the homography map.

        :return: None

        Example::
            >>> analyzer._set_buffer_zone_dimension()
            >>> # Buffer zones are initialized with dimensions from calibration data
        """
        self.buffer_zone_dimensions = {}
        for buffer_zone_id in self.calibration.homography_map_global_roi:
            width, height = self.calibration.homography_map_global_roi[buffer_zone_id][1]
            geometry = Polygon([(0.0, 0.0), (width, 0.0), (width, height), (0.0, height)])
            self.buffer_zone_dimensions[buffer_zone_id] = {"width": width, "height": height, "geometry": geometry}

    def get_event_time(self, msg_map: dict[str, list[Message]], last_action_time: float | None = None) -> float:
        """Get the latest event timestamp from a dictionary of messages.

        :param dict[str, list[Message]] msg_map: Dictionary mapping message IDs to lists of messages
        :param float | None last_action_time: Optional fallback timestamp if no messages found

        :return float: Latest event timestamp in seconds since epoch
        """
        latest_messages = []
        for _, messages in msg_map.items():
            sorted_messages = sorted(list(messages), key=lambda x: x.timestamp)
            latest_message = sorted_messages[-1]
            latest_messages.append(latest_message)
        if len(latest_messages) > 0:
            sorted_latest_messages = sorted(list(latest_messages), key=lambda x: x.timestamp)
            latest_timestamp = sorted_latest_messages[-1].timestamp

            timestamp_seconds = time.mktime(latest_timestamp.timetuple())
            event_time = timestamp_seconds + latest_timestamp.microsecond / 1000000.0
            return event_time
        return last_action_time

    def get_event_time_from_frames(self, frames: list[Any]) -> float:
        """Get the latest event timestamp from a list of frames.

        :param list[Any] frames: List of frame objects containing timestamp information
        :return float: Latest event timestamp in seconds since epoch
        """
        sorted_frames = sorted(list(frames), key=lambda x: (x.timestamp.seconds, x.timestamp.nanos))
        latest_timestamp = sorted_frames[-1].timestamp
        event_time = latest_timestamp.seconds + latest_timestamp.nanos / 1e9
        return event_time

    def get_existing_pallets(self, msg_map: dict[str, list[Message]]) -> tuple[dict[str, list[Polygon]], float]:
        """Extract existing pallet polygons from message data.

        :param dict[str, list[Message]] msg_map: Dictionary mapping message IDs to lists of messages
        :return tuple[dict[str, list[Polygon]], float]: Tuple containing:
            - Dictionary mapping zone IDs to lists of pallet polygons
            - Latest timestamp from messages
        """
        latest_messages = []
        for _, messages in msg_map.items():
            sorted_messages = sorted(list(messages), key=lambda x: x.timestamp)
            latest_message = sorted_messages[-1]
            latest_messages.append(latest_message)

        existing_pallets_zone_map = {}
        latest_timestamp = None

        if len(latest_messages) > 0:
            sorted_latest_messages = sorted(list(latest_messages), key=lambda x: x.timestamp)
            latest_timestamp = sorted_latest_messages[-1].timestamp

            existing_pallets_zone_map = {}
            for roi_id in self.calibration.homography_map_global_roi:
                existing_pallets_zone_map[roi_id] = {"polygon": [], "bbox3d_coords": []}
            for msg in latest_messages:
                if msg.object.type in self.targets:
                    bbox3d = msg.object.bbox3d
                    roi_coords_map = self.calibration.transform_bbox3d_to_global_rois(bbox3d)
                    for buffer_zone_id, list_coords in roi_coords_map.items():
                        pallet = Polygon([(coord.x, coord.y) for coord in list_coords])
                        if self.buffer_zone_dimensions[buffer_zone_id]["geometry"].intersects(pallet):
                            existing_pallets_zone_map[buffer_zone_id]["polygon"].append(pallet)
                            existing_pallets_zone_map[buffer_zone_id]["bbox3d_coords"].append(bbox3d.coordinates)

        return existing_pallets_zone_map, latest_timestamp

    def analyze(
        self, msg_map: dict[str, list[Message]], pallet_width: float = 1.0, target_zones: list[Any] = []
    ) -> list[Any]:
        """Analyze space utilization from message data.

        This method processes message data to analyze space utilization in specified zones,
        calculating metrics such as occupied space, free space, and maximum additional pallets
        that can be placed. It supports both genetic algorithm and greedy search approaches
        for pallet placement optimization.

        :param dict[str, list[Message]] msg_map: Dictionary mapping message IDs to lists of messages
        :param float pallet_width: Width of pallets in meters
        :param list[Any] target_zones: List of zone IDs to analyze, empty list means analyze all zones
        :return list[Any]: List of space utilization analysis results in both native and dictionary formats

        Example::
            >>> msg_map = {"camera1": [message1, message2]}
            >>> results = analyzer.analyze(msg_map, pallet_width=1.0, target_zones=["zone1"])
        """
        outputs_nv = []
        outputs_dict = []

        existing_pallets_zone_map, latest_timestamp = self.get_existing_pallets(msg_map)
        for buffer_zone_id, poly_n_bbox in existing_pallets_zone_map.items():

            existing_pallets = poly_n_bbox["polygon"]
            existing_bbox3ds = poly_n_bbox["bbox3d_coords"]
            if len(target_zones) > 0 and buffer_zone_id not in target_zones:
                continue

            area_width = self.buffer_zone_dimensions[buffer_zone_id]["width"]
            area_height = self.buffer_zone_dimensions[buffer_zone_id]["height"]

            best_solution = []
            max_num_pallet = 0
            if self.use_GA:
                for pallet_number in range(1, 21):
                    solution = genetic_algorithm(
                        area_width,
                        area_height,
                        pallet_width,
                        pallet_number,
                        existing_pallets,
                        self.grid_size,
                        population_size=self.population_size,
                        generations=self.generations,
                        use_good_init=1,
                        strategy=1,
                    )
                    if solution.fitness == 0:
                        max_num_pallet = pallet_number
                        best_solution = solution
                    else:
                        break
                if best_solution:
                    best_solution = [convert_pallet_bottom_left_loc_to_polygon(loc) for loc in best_solution.genome]
            else:
                best_solution = greedy_search(area_width, area_height, pallet_width, existing_pallets, self.grid_size)
                max_num_pallet = len(best_solution)

            (
                buffer_area,
                occupied_area,
                free_area,
                utilizable_free_area,
                unsafe_flag,
                unsafe_pallets,
                unsafe_pallet_cnt,
            ) = self.compute_space_metrics(area_width, area_height, existing_pallets, best_solution, existing_bbox3ds)

            total_space = buffer_area.area
            free_space = free_area.area
            occupied_space = occupied_area.area
            utilizable_free_space = sum([area.area for area in utilizable_free_area])
            free_space_quality = utilizable_free_space / free_space
            space_utilization = occupied_space / total_space

            logger.info(f"    metrics for buffer zone: {buffer_zone_id}")
            logger.info(f"        total space: {total_space} squaure meters")
            logger.info(f"        occupied space: {occupied_space} squaure meters")
            logger.info(f"            space utilization: {space_utilization}")
            logger.info(f"        free space: {free_space} squaure meters")
            logger.info(f"        max_num_pallet can fit in given existing pallets: {max_num_pallet}")
            logger.info(f"        utilizable free space: {utilizable_free_space} squaure meters")
            logger.info(f"            free space quality: {free_space_quality}")

            free_area_coord_list_roi = self.convert_geo_to_coord_list(free_area)
            utilizable_free_area_coord_list_roi = self.convert_poly_list_to_coord_list(utilizable_free_area)

            free_area_coord_list_3d = self.transfer_coord_list_from_roi_to_3d(free_area_coord_list_roi, buffer_zone_id)
            utilizable_free_area_coord_list_3d = self.transfer_coord_list_from_roi_to_3d(
                utilizable_free_area_coord_list_roi, buffer_zone_id
            )

            output_dict = {
                "id": buffer_zone_id,
                "timestamp": latest_timestamp,
                "metrics": {
                    "spaceOccupied": round(occupied_space, 2),
                    "freeSpace": round(free_space, 2),
                    "totalSpace": round(total_space, 2),
                    "spaceUtilization": round(space_utilization, 2),
                    "numExtraPallets": max_num_pallet,
                    "utilizableFreeSpace": round(utilizable_free_space, 2),
                    "freeSpaceQuality": round(free_space_quality, 2),
                    "isUnsafe": unsafe_flag,
                    "unsafePalletCnt": unsafe_pallet_cnt,
                },
                "sensors": [],
                "layouts": {
                    "freeSpace": free_area_coord_list_3d,
                    "utilizableFreeSpace": utilizable_free_area_coord_list_3d,
                },
                "bboxes": {"occupiedObj": existing_bbox3ds, "unsafeObj": unsafe_pallets},
            }
            outputs_dict.append(output_dict)

            # output_class = self.convert_space_utilization_dict_to_class(output_dict)
            output_nv = self.convert_space_utilization_dict_to_nv(output_dict)
            outputs_nv.append(output_nv)

            # # block of console prints for debug purpose
            # print('buffer_area:', buffer_area)
            # print('free_area:', free_area)
            # print('occupied_area:', occupied_area)
            # print('utilizable_free_area:', utilizable_free_area)

        return outputs_nv, outputs_dict

    def compute_space_metrics(
        self,
        area_width: float,
        area_height: float,
        existing_pallets: list[Polygon],
        solution: list[Polygon],
        existing_bbox3ds: list[Any],
    ) -> tuple[Polygon, Polygon, Polygon, list[Polygon], bool, list[Any], int]:
        """Compute space utilization metrics for a given area.

        This method calculates various space utilization metrics including buffer area,
        occupied space, free space, and utilizable free space. It also detects unsafe
        conditions where pallets are placed too close to boundaries or other pallets.

        :param float area_width: Width of area in meters
        :param float area_height: Height of area in meters
        :param list[Polygon] existing_pallets: List of existing pallet polygons
        :param list[Polygon] solution: List of proposed pallet placement polygons
        :param list[Any] existing_bbox3ds: List of 3D bounding boxes for existing pallets
        :return tuple[Polygon, Polygon, Polygon, list[Polygon], bool, list[Any], int]: Tuple containing:
            - Buffer area polygon
            - Occupied space polygon
            - Free space polygon
            - List of utilizable free space polygons
            - Boolean indicating if unsafe conditions exist
            - List of unsafe pallet bounding boxes
            - Count of unsafe pallets

        Example::
            >>> metrics = analyzer.compute_space_metrics(
            ...     area_width=10.0,
            ...     area_height=10.0,
            ...     existing_pallets=[pallet1, pallet2],
            ...     solution=[new_pallet1, new_pallet2],
            ...     existing_bbox3ds=[bbox1, bbox2]
            ... )
        """
        buffer_area = Polygon([(0, 0), (area_width, 0), (area_width, area_height), (0, area_height)])
        unsafe_flag = False
        unsafe_pallets = []
        unsafe_pallet_cnt = 0
        occupied_space = Polygon()

        for poly, bbox3d in zip(existing_pallets, existing_bbox3ds):
            is_poly_unsafe = False
            occupied_space = occupied_space.union(poly)
            unsafe_portion = poly.difference(buffer_area)
            if unsafe_portion.area > self.unsafe_size:
                buffer_area = buffer_area.difference(unsafe_portion)
                is_poly_unsafe = True
                unsafe_flag = True
            if is_poly_unsafe:
                unsafe_pallet_cnt += 1
                unsafe_pallets.append(bbox3d)

        occupied_space = buffer_area.intersection(occupied_space)
        free_space = buffer_area.difference(occupied_space)

        # utilizable_free_space = Polygon()
        # for poly in solution:
        #     utilizable_free_space = utilizable_free_space.union(poly)
        utilizable_free_space = []
        for poly in solution:
            utilizable_free_space.append(poly)

        return (
            buffer_area,
            occupied_space,
            free_space,
            utilizable_free_space,
            unsafe_flag,
            unsafe_pallets,
            unsafe_pallet_cnt,
        )

    def convert_geo_to_coord_list(self, geo: Polygon | MultiPolygon) -> list[list[list[tuple[float, float]]]]:
        """Convert a geometry object to a list of coordinate lists.

        This method converts a Shapely Polygon or MultiPolygon geometry object into a nested list
        structure containing coordinate tuples. For Polygons, it includes both exterior and interior
        ring coordinates. For MultiPolygons, it processes each constituent polygon separately.

        :param Polygon | MultiPolygon geo: Polygon or MultiPolygon geometry object to convert
        :return list[list[list[tuple[float, float]]]]: Nested list structure containing coordinate tuples for all rings

        Example::
            >>> polygon = Polygon([(0, 0), (1, 0), (1, 1), (0, 1)])
            >>> coords = analyzer.convert_geo_to_coord_list(polygon)
            >>> # Returns: [[[(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]]]
        """
        coord_list = []
        if isinstance(geo, Polygon):
            poly_list = self.convert_polygon_to_list(geo)
            coord_list.append(poly_list)
        elif isinstance(geo, MultiPolygon):
            for poly in geo.geoms:
                poly_list = self.convert_polygon_to_list(poly)
                coord_list.append(poly_list)

        return coord_list

    def convert_poly_list_to_coord_list(self, poly_list: list[Polygon]) -> list[list[list[tuple[float, float]]]]:
        """Convert a list of polygons to a list of coordinate lists.

        This method processes a list of Shapely Polygon objects and converts each one into
        a nested list structure containing coordinate tuples for both exterior and interior rings.

        :param list[Polygon] poly_list: List of Polygon objects to convert
        :return list[list[list[tuple[float, float]]]]: Nested list structure containing coordinate tuples for all polygons

        Example::
            >>> polygons = [Polygon([(0, 0), (1, 0), (1, 1)]), Polygon([(2, 2), (3, 2), (3, 3)])]
            >>> coords = analyzer.convert_poly_list_to_coord_list(polygons)
        """
        coord_list = []
        for poly in poly_list:
            poly_list = self.convert_polygon_to_list(poly)
            coord_list.append(poly_list)

        return coord_list

    def convert_polygon_to_list(self, poly: Polygon) -> list[list[tuple[float, float]]]:
        """Convert a single polygon to a list of coordinate lists.

        This method extracts both exterior and interior ring coordinates from a Shapely Polygon
        object and organizes them into a nested list structure.

        :param Polygon poly: Polygon object to convert
        :return list[list[tuple[float, float]]]: List containing coordinate lists for exterior and interior rings

        Example::
            >>> polygon = Polygon([(0, 0), (1, 0), (1, 1), (0, 1)],
            ...                  holes=[[(0.2, 0.2), (0.8, 0.2), (0.8, 0.8), (0.2, 0.8)]])
            >>> coords = analyzer.convert_polygon_to_list(polygon)
        """
        poly_list = []
        exterior_coords = list(poly.exterior.coords)
        poly_list.append(exterior_coords)
        del exterior_coords

        for interior in poly.interiors:
            interior_coords = list(interior.coords)
            poly_list.append(interior_coords)
            del interior_coords

        return poly_list

    def convert_coord_list_to_class(self, coord_list: list[list[list[tuple[float, float]]]]) -> list[PolygonCoords]:
        """Convert coordinate lists to PolygonCoords objects.

        This method transforms a nested list of coordinate tuples into a list of PolygonCoords
        objects, which include both exterior coordinates and interior holes.

        :param list[list[list[tuple[float, float]]]] coord_list: Nested list structure containing coordinate tuples
        :return list[PolygonCoords]: List of PolygonCoords objects representing the polygons

        Example::
            >>> coords = [[[(0, 0), (1, 0), (1, 1)], [(0.2, 0.2), (0.8, 0.2), (0.8, 0.8)]]]
            >>> polygon_coords = analyzer.convert_coord_list_to_class(coords)
        """
        output = []
        for poly_coord in coord_list:
            exterior_coord = poly_coord[0]
            interriors_coord = poly_coord[1:]

            output_class = PolygonCoords(
                coordinates=[Point2D(x=px, y=py) for px, py in exterior_coord],
                holes=[
                    PolygonHole(coordinates=[Point2D(x=px, y=py) for px, py in interior_coord])
                    for interior_coord in interriors_coord
                ],
            )
            output.append(output_class)
        return output

    def convert_coord_list_to_nv(self, coord_list: list[list[list[tuple[float, float]]]]) -> list[nvSchema.Polygon]:
        """Convert coordinate lists to protobuf Polygon objects.

        This method transforms a nested list of coordinate tuples into a list of protobuf
        Polygon objects, which include both exterior coordinates and interior holes.

        :param list[list[list[tuple[float, float]]]] coord_list: Nested list structure containing coordinate tuples
        :return list[nvSchema.Polygon]: List of protobuf Polygon objects

        Example::
            >>> coords = [[[(0, 0), (1, 0), (1, 1)], [(0.2, 0.2), (0.8, 0.2), (0.8, 0.8)]]]
            >>> nv_polygons = analyzer.convert_coord_list_to_nv(coords)
        """
        output = []
        for poly_coord in coord_list:
            exterior_coord = poly_coord[0]
            interriors_coord = poly_coord[1:]

            output_class = nvSchema.Polygon(
                coordinates=[nvSchema.Point2D(x=px, y=py) for px, py in exterior_coord],
                holes=[
                    nvSchema.PolygonHole(coordinates=[nvSchema.Point2D(x=px, y=py) for px, py in interior_coord])
                    for interior_coord in interriors_coord
                ],
            )
            output.append(output_class)
        return output

    def transfer_coord_list(
        self, coord_list: list[list[list[tuple[float, float]]]], hmatrix: list[list[float]]
    ) -> list[list[list[tuple[float, float]]]]:
        """Transform coordinate lists using a homography matrix.

        This method applies a homography transformation to a nested list of coordinate tuples,
        transforming each point according to the provided homography matrix.

        :param list[list[list[tuple[float, float]]]] coord_list: Nested list structure containing coordinate tuples to transform
        :param list[list[float]] hmatrix: 3x3 homography transformation matrix
        :return list[list[list[tuple[float, float]]]]: Transformed coordinate lists with the same structure as input

        Example::
            >>> coords = [[[(0, 0), (1, 0), (1, 1)]]]
            >>> hmatrix = [[1, 0, 0], [0, 1, 0], [0, 0, 1]]
            >>> transformed = analyzer.transfer_coord_list(coords, hmatrix)
        """
        coord_list_new = []
        for poly_list in coord_list:
            poly_list_new = []
            exterior = poly_list[0]
            interiors = poly_list[1:]
            exterior_new = [self.calibration.perspective_transform(px, py, hmatrix) for px, py in exterior]
            poly_list_new.append(exterior_new)
            for interior in interiors:
                interior_new = [self.calibration.perspective_transform(px, py, hmatrix) for px, py in interior]
                poly_list_new.append(interior_new)
            coord_list_new.append(poly_list_new)

        return coord_list_new

    def transfer_coord_list_from_roi_to_3d(
        self, coord_list: list[list[list[tuple[float, float]]]], buffer_zone_id: str
    ) -> list[list[list[tuple[float, float]]]]:
        """Transform coordinate lists from ROI space to 3D world space.

        This method transforms coordinates from the Region of Interest (ROI) coordinate system
        to the 3D world coordinate system using the inverse homography matrix for the specified
        buffer zone.

        :param list[list[list[tuple[float, float]]]] coord_list: Nested list structure containing coordinate tuples in ROI space
        :param str buffer_zone_id: ID of the buffer zone for transformation
        :return list[list[list[tuple[float, float]]]]: Transformed coordinate lists in 3D world space

        Example::
            >>> roi_coords = [[[(0, 0), (1, 0), (1, 1)]]]
            >>> world_coords = analyzer.transfer_coord_list_from_roi_to_3d(roi_coords, "zone1")
        """
        _, _, _, hmatrix_inv = self.calibration.homography_map_global_roi[buffer_zone_id]
        coord_list_new = self.transfer_coord_list(coord_list, hmatrix_inv)

        return coord_list_new

    def transfer_coord_list_from_3d_to_image(
        self, coord_list: list[list[list[tuple[float, float]]]], sensor_id: str
    ) -> list[list[list[tuple[float, float]]]]:
        """Transform coordinate lists from 3D world space to image space.

        This method transforms coordinates from the 3D world coordinate system to the 2D image
        coordinate system using the inverse homography matrix for the specified sensor.

        :param list[list[list[tuple[float, float]]]] coord_list: Nested list structure containing coordinate tuples in 3D world space
        :param str sensor_id: ID of the camera sensor for transformation
        :return list[list[list[tuple[float, float]]]]: Transformed coordinate lists in image space

        Example::
            >>> world_coords = [[[(0, 0, 0), (1, 0, 0), (1, 1, 0)]]]
            >>> image_coords = analyzer.transfer_coord_list_from_3d_to_image(world_coords, "camera1")
        """
        hmatrix = self.calibration.sensor_map[sensor_id].homography
        hmatrix_inv = np.linalg.inv(np.array(hmatrix))
        hmatrix_inv = hmatrix_inv.tolist()
        coord_list_new = self.transfer_coord_list(coord_list, hmatrix_inv)

        return coord_list_new

    def overlay_area_on_image(
        self,
        area_coord_list_3d: list[list[list[tuple[float, float]]]],
        image: np.ndarray,
        sensor_id: str,
        color: tuple[int, int, int] = (0, 255, 0),
    ) -> np.ndarray:
        """Overlay area coordinates on an image.

        This method overlays polygon areas defined by 3D coordinates onto an image by first
        transforming the coordinates to image space and then drawing the polygons with the
        specified color.

        :param list[list[list[tuple[float, float]]]] area_coord_list_3d: Nested list structure containing 3D coordinate tuples
        :param np.ndarray image: Input image to overlay on
        :param str sensor_id: ID of the camera sensor for coordinate transformation
        :param tuple[int, int, int] color: RGB color tuple for the overlay (default: green)
        :return np.ndarray: Image with overlaid areas

        Example::
            >>> coords = [[[(0, 0, 0), (1, 0, 0), (1, 1, 0)]]]
            >>> image = analyzer.overlay_area_on_image(coords, image, "camera1", color=(0, 255, 0))
        """
        area_coord_list_image = self.transfer_coord_list_from_3d_to_image(area_coord_list_3d, sensor_id)
        for poly_coords_list in area_coord_list_image:
            poly_coords_list = [np.array(poly, dtype=np.int32) for poly in poly_coords_list if len(poly) > 0]
            if len(poly_coords_list) > 0:
                cv2.fillPoly(
                    image, poly_coords_list, color=(int(color[0] * 0.5), int(color[1] * 0.5), int(color[2] * 0.5))
                )
                cv2.polylines(image, poly_coords_list, isClosed=True, color=color, thickness=2)

        return image

    def _annotate_buffer_zone(self, image: np.ndarray, sensor_id: str, buffer_zone_id: str) -> np.ndarray:
        """Annotate buffer zone on an image.

        This method adds a text label for a buffer zone on the image, positioned near the
        buffer zone boundary. The text is drawn with a black outline and white fill for
        better visibility.

        :param np.ndarray image: Input image to annotate
        :param str sensor_id: ID of the camera sensor for coordinate transformation
        :param str buffer_zone_id: ID of the buffer zone to annotate
        :return np.ndarray: Image with buffer zone annotation

        Example::
            >>> image = analyzer._annotate_buffer_zone(image, "camera1", "zone1")
        """
        buffer_zone_coords = []
        for buffer_zone_info in self.calibration.global_buffer_zones:
            if buffer_zone_info.id == buffer_zone_id:
                buffer_zone_coords = buffer_zone_info.roiCoordinates
        buffer_zone_coords_3d = [[coord.x, coord.y] for coord in buffer_zone_coords]
        hmatrix = self.calibration.sensor_map[sensor_id].homography
        hmatrix = np.linalg.inv(np.array(hmatrix))
        hmatrix = hmatrix.tolist()
        buffer_zone_coords_image = [
            self.calibration.perspective_transform(px, py, hmatrix) for px, py in buffer_zone_coords_3d
        ]
        text_location = (1920, 1080)
        for px, py in buffer_zone_coords_image:
            if py > 20 and py < text_location[1] + 8:
                text_location = (int(px), int(py) - 8)
        cv2.putText(image, buffer_zone_id, text_location, cv2.FONT_HERSHEY_SIMPLEX, 1.1, (0, 0, 0), 6, cv2.LINE_AA)
        cv2.putText(
            image, buffer_zone_id, text_location, cv2.FONT_HERSHEY_SIMPLEX, 1.1, (255, 255, 255), 2, cv2.LINE_AA
        )

        return image

    def overlay_free_space_on_image(
        self,
        free_area_coord_list_3d: list[list[list[tuple[float, float]]]],
        utilizable_free_area_coord_list_3d: list[list[list[tuple[float, float]]]],
        occupied_bbox3ds: list[Any],
        image: np.ndarray,
        sensor_id: str,
        buffer_zone_id: str,
        opacity: float = 0.6,
        cam_font_size: float = 2.0,
        title: str | None = None,
        advanced_overlay: bool = True,
    ) -> np.ndarray:
        """Overlay free space visualization on an image.

        This method creates a visual representation of free and utilizable space on an image,
        with options for advanced overlay rendering including transparency and color coding.
        It also displays occupied areas and buffer zone annotations.

        :param list[list[list[tuple[float, float]]]] free_area_coord_list_3d: List of 3D coordinate lists defining free areas
        :param list[list[list[tuple[float, float]]]] utilizable_free_area_coord_list_3d: List of 3D coordinate lists defining utilizable free areas
        :param list[Any] occupied_bbox3ds: List of occupied 3D bounding boxes
        :param np.ndarray image: Input image
        :param str sensor_id: ID of the camera sensor
        :param str buffer_zone_id: ID of the buffer zone
        :param float opacity: Opacity of the overlay (0-1)
        :param float cam_font_size: Font size for annotations
        :param str | None title: Optional title to display
        :param bool advanced_overlay: Whether to use advanced overlay rendering
        :return np.ndarray: Image with free space visualization overlay

        Example::
            >>> image = analyzer.overlay_free_space_on_image(
            ...     free_area_coord_list_3d,
            ...     utilizable_free_area_coord_list_3d,
            ...     occupied_bbox3ds,
            ...     image,
            ...     sensor_id="camera1",
            ...     buffer_zone_id="zone1",
            ...     opacity=0.6,
            ...     title="Space Utilization"
            ... )
        """
        if advanced_overlay:
            overlay = np.zeros(image.shape)
            overlay = self.overlay_area_on_image(free_area_coord_list_3d, overlay, sensor_id, color=(255, 0, 0))
            overlay = self.overlay_area_on_image(
                utilizable_free_area_coord_list_3d, overlay, sensor_id, color=(0, 255, 0)
            )
            camera_params = self.calibration.get_cam_params(sensor_id)
            for bbox3d_coords in occupied_bbox3ds:
                bbox3d = Bbox3d(coordinates=bbox3d_coords)
                corners_3d = self.calibration.box3d_to_corners3d(bbox3d)
                corners_camera = self.world_to_image_projection(corners_3d.reshape(-1, 3), camera_params)
                overlay = self.paint_bbox3d_on_img(overlay, corners_camera, color=(0, 0, 0))
            alpha_channel = np.ones(overlay.shape[:2])
            for i in range(alpha_channel.shape[0]):
                for j in range(alpha_channel.shape[1]):
                    if overlay[i, j, 0] + overlay[i, j, 1] + overlay[i, j, 2] == 0:
                        alpha_channel[i, j] = 0
            alpha_channel *= opacity
            alpha_inv = 1.0 - alpha_channel
            for c in range(3):
                image[:, :, c] = alpha_channel * overlay[:, :, c] + alpha_inv * image[:, :, c]
        else:
            image = self.overlay_area_on_image(free_area_coord_list_3d, image, sensor_id, color=(255, 0, 0))
            image = self.overlay_area_on_image(utilizable_free_area_coord_list_3d, image, sensor_id, color=(0, 255, 0))
        image = self._annotate_buffer_zone(image, sensor_id, buffer_zone_id)
        title_text = title if title else sensor_id
        image = self.annotate_title(image, title_text, cam_font_size)
        return image

    def overlay_bbox3d_on_image(
        self,
        unsafe_bbox3ds: list[Any],
        image: np.ndarray,
        sensor_id: str,
        buffer_zone_id: str,
        cam_font_size: float,
        title: str | None = None,
    ) -> np.ndarray:
        """Overlay 3D bounding boxes on an image.

        This method visualizes unsafe 3D bounding boxes on an image, including buffer zone
        annotation and optional title. It projects 3D coordinates to 2D image space using
        camera parameters.

        :param unsafe_bbox3ds: List of unsafe 3D bounding boxes to visualize (list[Any])
        :param image: Input image to draw on (np.ndarray)
        :param sensor_id: ID of the camera sensor for coordinate transformation (str)
        :param buffer_zone_id: ID of the buffer zone to annotate (str)
        :param cam_font_size: Font size for text annotations (float)
        :param title: Optional title to display on the image (default: None) (str | None)
        :return np.ndarray: Image with 3D bounding boxes and annotations

        Example::
            >>> image = analyzer.overlay_bbox3d_on_image(
            ...     unsafe_bbox3ds,
            ...     image,
            ...     "camera1",
            ...     "zone1",
            ...     2.0,
            ...     title="Unsafe Objects"
            ... )
        """
        image = self._annotate_buffer_zone(image, sensor_id, buffer_zone_id)
        camera_params = self.calibration.get_cam_params(sensor_id)
        for bbox3d_coords in unsafe_bbox3ds:
            bbox3d = Bbox3d(coordinates=bbox3d_coords)
            corners_3d = self.calibration.box3d_to_corners3d(bbox3d)
            corners_camera = self.world_to_image_projection(corners_3d.reshape(-1, 3), camera_params)
            image = self.plot_rect3d_on_img(image, corners_camera, color=(0, 0, 255), thickness=2)
        title_text = title if title else sensor_id
        image = self.annotate_title(image, title_text, cam_font_size)
        return image

    def find_convex_hull(self, points: np.ndarray) -> np.ndarray | None:
        """Compute the convex hull of a set of 2D points.

        This method calculates the convex hull of a set of 2D points using scipy's ConvexHull
        implementation. The convex hull is the smallest convex polygon that contains all the points.

        :param points: Array of 2D point coordinates (np.ndarray)
        :return np.ndarray | None: Array of convex hull vertices in counterclockwise order, or None if fewer than 3 points

        Example::
            >>> points = np.array([[0, 0], [1, 0], [1, 1], [0, 1]])
            >>> hull = analyzer.find_convex_hull(points)
        """
        points = np.asarray(points)

        if points.shape[0] < 3:
            return None
        hull = ConvexHull(points)
        return points[hull.vertices]

    def paint_bbox3d_on_img(
        self, img: np.ndarray, corners: np.ndarray, color: tuple[int, int, int] = (0, 0, 0)
    ) -> np.ndarray:
        """Paint a 3D bounding box on an image.

        This method draws a filled polygon representing the convex hull of a 3D bounding box's
        projected corners on the image. The polygon is filled with a semi-transparent version
        of the specified color.

        :param np.ndarray img: Input image to draw on
        :param np.ndarray corners: Array of 2D corner coordinates for the bounding box
        :param tuple[int, int, int] color: RGB color tuple for the bounding box (default: black)
        :return np.ndarray: Image with painted bounding box

        Example::
            >>> corners = np.array([[0, 0], [100, 0], [100, 100], [0, 100]])
            >>> image = analyzer.paint_bbox3d_on_img(image, corners, color=(0, 0, 255))
        """
        convex_hull_points = self.find_convex_hull(corners)
        poly_coords_list = [np.array(convex_hull_points, dtype=np.int32)]
        if len(poly_coords_list) > 0:
            cv2.fillPoly(img, poly_coords_list, color=(int(color[0] * 0.5), int(color[1] * 0.5), int(color[2] * 0.5)))

        return img

    def plot_rect3d_on_img(
        self,
        img: np.ndarray,
        corners: np.ndarray,
        text: str | None = None,
        color: tuple[int, int, int] = (0, 255, 0),
        thickness: int = 2,
    ) -> np.ndarray:
        """Plot a 3D rectangle on an image.

        This method draws the edges of a 3D rectangle on the image by connecting its corners
        with lines. Optionally, it can also display text near the rectangle.

        :param np.ndarray img: Input image to draw on
        :param np.ndarray corners: Array of 2D corner coordinates for the rectangle
        :param str | None text: Optional text to display with the rectangle
        :param tuple[int, int, int] color: RGB color tuple for the rectangle (default: green)
        :param int thickness: Line thickness in pixels (default: 2)
        :return np.ndarray: Image with plotted rectangle

        Example::
            >>> corners = np.array([[0, 0], [100, 0], [100, 100], [0, 100]])
            >>> image = analyzer.plot_rect3d_on_img(image, corners, text="Box1", color=(0, 0, 255))
        """
        img_draw = np.copy(img)
        line_indices = (
            (0, 1),
            (0, 3),
            (0, 4),
            (2, 1),
            (2, 3),
            (2, 6),
            (4, 5),
            (4, 7),
            (6, 5),
            (6, 7),
            (1, 5),
            (3, 7),
        )
        h, w = img.shape[:2]

        for start, end in line_indices:
            if (
                (corners[start, 1] >= h or corners[start, 1] < 0) or (corners[start, 0] >= w or corners[start, 0] < 0)
            ) and ((corners[end, 1] >= h or corners[end, 1] < 0) or (corners[end, 0] >= w or corners[end, 0] < 0)):
                continue

            cv2.line(
                img_draw,
                (corners[start, 0], corners[start, 1]),
                (corners[end, 0], corners[end, 1]),
                color,
                thickness,
                cv2.LINE_AA,
            )

        # print text for each box
        if text is not None:
            cv2.putText(
                img_draw,
                text,
                corners[5][:2],
                cv2.FONT_HERSHEY_SIMPLEX,
                2.0,  # scale
                (255, 255, 255),
                3,  # thickness
                cv2.LINE_AA,
            )

        return img_draw

    def world_to_image_projection(self, corners_3d: np.ndarray, camera_params: dict[str, Any]) -> np.ndarray:
        """Project 3D world coordinates to 2D image coordinates.

        This method performs perspective projection of 3D world coordinates onto a 2D image
        plane using camera parameters including rotation, translation, intrinsic matrix,
        and distortion coefficients.

        :param np.ndarray corners_3d: Array of 3D world coordinates
        :param dict[str, Any] camera_params: Dictionary of camera parameters including intrinsics and extrinsics
        :return np.ndarray: Array of 2D image coordinates
        :raises ValueError: If camera parameters are invalid or missing

        Example::
            >>> corners_3d = np.array([[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]])
            >>> camera_params = {
            ...     'rotation_vector': [[0, 0, 0]],
            ...     'translation_vector': [[0, 0, 0]],
            ...     'intrinsic_matrix': [[1000, 0, 500], [0, 1000, 500], [0, 0, 1]],
            ...     'distortion_coefficients': [[0, 0, 0, 0, 0]]
            ... }
            >>> corners_2d = analyzer.world_to_image_projection(corners_3d, camera_params)
        """
        rotation_vector = np.array(camera_params["rotation_vector"], dtype=np.float32)
        translation_vector = np.array(camera_params["translation_vector"], dtype=np.float32)
        intrinsic_matrix = np.array(camera_params["intrinsic_matrix"], dtype=np.float32)
        distortion_coefficients = np.array(camera_params["distortion_coefficients"], dtype=np.float32)
        corners_camera, jacobian = cv2.projectPoints(
            corners_3d, rotation_vector, translation_vector, intrinsic_matrix, distortion_coefficients
        )
        corners_camera = corners_camera[:, 0, :].astype(int)
        return corners_camera

    def datetime_to_str(self, dt: datetime) -> str:
        """Convert datetime object to ISO format string.

        This method converts a datetime object to an ISO 8601 formatted string with
        millisecond precision and 'Z' timezone indicator.

        :param dt: Datetime object to convert (datetime)
        :return str: ISO format string with millisecond precision

        Example::
            >>> dt = datetime(2024, 1, 1, 12, 0, 0, 500000)
            >>> result = analyzer.datetime_to_str(dt)
            >>> # Returns: "2024-01-01T12:00:00.500Z"
        """
        return dt.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"

    def convert_space_utilization_dict_to_class(self, output_dict: dict[str, Any]) -> SpaceUtilization:
        """Convert space utilization dictionary to a SpaceUtilization class instance.

        This method transforms a dictionary containing space utilization metrics and layouts
        into a structured SpaceUtilization class instance, including metrics for occupied space,
        free space, and utilizable areas.

        :param output_dict: Dictionary containing space utilization data (dict[str, Any])
        :return SpaceUtilization: SpaceUtilization class instance containing the converted data

        Example::
            >>> data_dict = {
            ...     "metrics": {
            ...         "spaceOccupied": 100.0,
            ...         "freeSpace": 200.0,
            ...         "totalSpace": 300.0,
            ...         "utilization": 33.33
            ...     },
            ...     "layouts": {
            ...         "freeSpace": [[(0, 0), (1, 0), (1, 1), (0, 1)]],
            ...         "utilizableFreeSpace": [[(0, 0), (1, 0), (1, 1), (0, 1)]]
            ...     }
            ... }
            >>> space_util = analyzer.convert_space_utilization_dict_to_class(data_dict)
        """
        metrics_output_class = SpaceUtilizationMetrics(
            spaceOccupied=output_dict["metrics"]["spaceOccupied"],
            freeSpace=output_dict["metrics"]["freeSpace"],
            totalSpace=output_dict["metrics"]["totalSpace"],
            spaceUtilization=output_dict["metrics"]["spaceUtilization"],
            numExtraPallets=output_dict["metrics"]["numExtraPallets"],
            utilizableFreeSpace=output_dict["metrics"]["utilizableFreeSpace"],
            freeSpaceQuality=output_dict["metrics"]["freeSpaceQuality"],
            isUnsafe=output_dict["metrics"]["isUnsafe"],
        )
        free_area_class = self.convert_coord_list_to_class(output_dict["layouts"]["freeSpace"])
        utilizable_free_area_class = self.convert_coord_list_to_class(output_dict["layouts"]["utilizableFreeSpace"])
        layouts_output_class = SpaceUtilizationLayouts(
            freeSpace=free_area_class, utilizableFreeSpace=utilizable_free_area_class
        )
        output_class = SpaceUtilization(
            id=output_dict["id"],
            timestamp=output_dict["timestamp"],
            metrics=metrics_output_class,
            layouts=layouts_output_class,
        )

        return output_class

    def convert_space_utilization_dict_to_nv(self, output_dict: dict[str, Any]) -> extSchema.SpaceUtilization:
        """Convert a space utilization dictionary to protobuf format.

        This method converts a dictionary containing space utilization data into protobuf
        format, including metrics and layout information for free and utilizable space.

        :param dict[str, Any] output_dict: Dictionary containing space utilization data
        :return extSchema.SpaceUtilization: SpaceUtilization object in protobuf format

        Example::
            >>> data_dict = {
            ...     "metrics": {
            ...         "spaceOccupied": 100.0,
            ...         "freeSpace": 200.0,
            ...         "totalSpace": 300.0,
            ...         "utilization": 33.33
            ...     },
            ...     "layouts": {
            ...         "freeSpace": [[(0, 0), (1, 0), (1, 1), (0, 1)]],
            ...         "utilizableFreeSpace": [[(0, 0), (1, 0), (1, 1), (0, 1)]]
            ...     }
            ... }
            >>> nv_space_util = analyzer.convert_space_utilization_dict_to_nv(data_dict)
        """
        metrics_nv = extSchema.SpaceUtilizationMetrics(
            spaceOccupied=output_dict["metrics"]["spaceOccupied"],
            freeSpace=output_dict["metrics"]["freeSpace"],
            totalSpace=output_dict["metrics"]["totalSpace"],
            spaceUtilization=output_dict["metrics"]["spaceUtilization"],
            numExtraPallets=output_dict["metrics"]["numExtraPallets"],
            utilizableFreeSpace=output_dict["metrics"]["utilizableFreeSpace"],
            freeSpaceQuality=output_dict["metrics"]["freeSpaceQuality"],
            isUnsafe=output_dict["metrics"]["isUnsafe"],
        )
        free_area_nv = self.convert_coord_list_to_nv(output_dict["layouts"]["freeSpace"])
        utilizable_free_area_nv = self.convert_coord_list_to_nv(output_dict["layouts"]["utilizableFreeSpace"])
        layouts_nv = extSchema.SpaceUtilizationLayouts(
            freeSpace=free_area_nv, utilizableFreeSpace=utilizable_free_area_nv
        )
        output_nv = extSchema.SpaceUtilization(
            id=output_dict["id"], timestamp=output_dict["timestamp"], metrics=metrics_nv, layouts=layouts_nv
        )

        return output_nv

    def draw_bboxes(
        self,
        msg_map: dict[str, list[Message]],
        image: np.ndarray,
        sensor_id: str,
        color: tuple[int, int, int] = (0, 0, 255),
        cam_font_size: float = 2.0,
        title: str | None = None,
    ) -> np.ndarray:
        """Draw bounding boxes on an image from message data.

        This method processes message data to draw 3D bounding boxes on an image, using camera
        parameters for projection and applying specified visual styling.

        :param msg_map: Dictionary mapping message IDs to lists of messages (dict[str, list[Message]])
        :param image: Input image to draw on (np.ndarray)
        :param sensor_id: ID of the camera sensor for coordinate transformation (str)
        :param color: RGB color tuple for the bounding boxes (default: red) (tuple[int, int, int])
        :param cam_font_size: Font size for text annotations (default: 2.0) (float)
        :param title: Optional title to display on the image (default: None) (str | None)
        :return np.ndarray: Image with drawn bounding boxes and annotations

        Example::
            >>> msg_map = {"camera1": [message1, message2]}
            >>> image = analyzer.draw_bboxes(
            ...     msg_map, image, "camera1",
            ...     color=(0, 0, 255), title="Detected Objects"
            ... )
        """
        camera_params = self.calibration.get_cam_params(sensor_id)

        latest_messages = []
        for _, messages in msg_map.items():
            sorted_messages = sorted(list(messages), key=lambda x: x.timestamp)
            latest_message = sorted_messages[-1]
            latest_messages.append(latest_message)

        if len(latest_messages) > 0:
            for msg in latest_messages:
                if msg.object.type in self.targets:
                    bbox3d = msg.object.bbox3d
                    corners_3d = self.calibration.box3d_to_corners3d(bbox3d)
                    corners_camera = self.world_to_image_projection(corners_3d.reshape(-1, 3), camera_params)
                    image = self.plot_rect3d_on_img(image, corners_camera, color=color, thickness=2)
        title_text = title if title else sensor_id
        image = self.annotate_title(image, title_text, cam_font_size)
        return image

    def draw_circle_pts_in_perspective(
        self, center: tuple[float, float], radius: float, hmatrix: list[list[float]], num_pts: int
    ) -> list[tuple[float, float]]:
        """Generate points for a circle in perspective view.

        This method generates points around a circle and transforms them using a homography
        matrix to create a perspective view of the circle.

        :param center: Center point coordinates (x, y) (tuple[float, float])
        :param radius: Circle radius (float)
        :param hmatrix: Homography transformation matrix (list[list[float]])
        :param num_pts: Number of points to generate around the circle (int)
        :return list[tuple[float, float]]: List of transformed circle point coordinates

        Example::
            >>> center = (100.0, 100.0)
            >>> hmatrix = [[1, 0, 0], [0, 1, 0], [0, 0, 1]]
            >>> points = analyzer.draw_circle_pts_in_perspective(center, 50.0, hmatrix, 32)
        """
        pts = self.draw_circle_pts(center, radius, num_pts)
        pts = [self.calibration.perspective_transform(px, py, hmatrix) for px, py in pts]
        return pts

    def draw_circle_pts(self, center: tuple[float, float], radius: float, num_pts: int) -> list[tuple[float, float]]:
        """Generate points for a circle.

        This method generates evenly spaced points around a circle using parametric equations.

        :param center: Center point coordinates (x, y) (tuple[float, float])
        :param radius: Circle radius (float)
        :param num_pts: Number of points to generate around the circle (int)
        :return list[tuple[float, float]]: List of circle point coordinates

        Example::
            >>> center = (100.0, 100.0)
            >>> points = analyzer.draw_circle_pts(center, 50.0, 32)
        """
        x0, y0 = center
        angle_increment = math.pi * 2 / num_pts
        pts = []
        for i in range(num_pts):
            angle = i * angle_increment
            x = x0 + radius * math.cos(angle)
            y = y0 + radius * math.sin(angle)
            pts.append((x, y))
        return pts

    def annotate_title(self, image: np.ndarray, title: str, font_size: float = 2.0) -> np.ndarray:
        """Add a title annotation to an image.

        This method adds a title text to the top of an image with a black outline and white fill
        for better visibility against any background.

        :param image: Input image to annotate (np.ndarray)
        :param title: Text to display as title (str)
        :param font_size: Font size for the title text (default: 2.0) (float)
        :return np.ndarray: Image with title annotation

        Example::
            >>> image = analyzer.annotate_title(image, "Space Analysis", 2.0)
        """
        textSize = cv2.getTextSize(text=title, fontFace=cv2.FONT_HERSHEY_SIMPLEX, fontScale=font_size, thickness=8)
        # ((720, 88), 40)
        height = textSize[0][1] + 20
        cv2.putText(image, title, (20, height), cv2.FONT_HERSHEY_SIMPLEX, font_size, (0, 0, 0), 8, cv2.LINE_AA)
        cv2.putText(image, title, (20, height), cv2.FONT_HERSHEY_SIMPLEX, font_size, (255, 255, 255), 3, cv2.LINE_AA)
        return image

    def draw_proximity(
        self,
        frame_data: dict[str, Any],
        image: np.ndarray,
        sensor_id: str,
        center_obj_type: str = "NovaCarter",
        thickness: int = 3,
        in_3d_mode: bool = True,
    ) -> np.ndarray:
        """Draw proximity detection visualization on an image.

        This method visualizes proximity detection results by drawing circles around detected
        objects and highlighting safety violations. It supports both 2D and 3D visualization modes.

        :param frame_data: Dictionary containing frame analysis data (dict[str, Any])
        :param image: Input image to draw on (np.ndarray)
        :param sensor_id: ID of the camera sensor for coordinate transformation (str)
        :param center_obj_type: Type of object to center proximity detection around (default: 'NovaCarter') (str)
        :param thickness: Line thickness in pixels (default: 3) (int)
        :param in_3d_mode: Whether to use 3D or 2D visualization mode (default: True) (bool)
        :return np.ndarray: Image with proximity detection visualization

        Example::
            >>> frame_data = {
            ...     "proximity": {
            ...         "threshold": 1.5,
            ...         "clusters": [{"points": [{"x": 100, "y": 100}]}],
            ...         "info": {"proximityViolationObjects": "obj1,obj2"}
            ...     },
            ...     "objects": [{"id": "obj1", "type": "NovaCarter"}]
            ... }
            >>> image = analyzer.draw_proximity(frame_data, image, "camera1")
        """
        if in_3d_mode:
            camera_params = self.calibration.get_cam_params(sensor_id)
        hmatrix = self.calibration.sensor_map[sensor_id].homography
        hmatrix_inv = np.linalg.inv(np.array(hmatrix))
        hmatrix_inv = hmatrix_inv.tolist()
        # num_detected_violations = frame_data["proximityDetections"]
        radius = frame_data["proximity"]["threshold"]
        clusters = frame_data["proximity"]["clusters"]
        info = frame_data["proximity"]["info"]
        objects_all = frame_data["objects"]
        objects_all_map = {obj["id"]: obj for obj in objects_all}
        proximity_violation_objects_all_clusters = info["proximityViolationObjects"].split("|")

        cluster_idx = 0
        for cluster in clusters:
            points = cluster["points"]
            color = (0, 255, 0)
            if len(points) > 1:
                color = (0, 0, 255)
            point = points[0]
            circle_center = [point["x"], point["y"]]
            pts = self.draw_circle_pts_in_perspective(circle_center, radius, hmatrix_inv, num_pts=32)
            pts = np.array(pts).astype(int).reshape((-1, 1, 2))
            ellipse_pts_in_perspective = [pts]
            circle_center = self.calibration.perspective_transform(point["x"], point["y"], hmatrix_inv)
            cv2.circle(image, (int(circle_center[0]), int(circle_center[1])), 9, (255, 255, 255), -1)
            cv2.circle(image, (int(circle_center[0]), int(circle_center[1])), 6, (255, 0, 0), -1)
            # cv2.drawContours(image, ellipse_pts_in_perspective, 0, (255, 255, 255), 5)
            cv2.drawContours(image, ellipse_pts_in_perspective, 0, color, thickness)

            objects_id_involved = proximity_violation_objects_all_clusters[cluster_idx].split(",")
            objects_involved = [objects_all_map[obj_id] for obj_id in objects_id_involved]
            for obj in objects_involved:
                obj_type = obj["type"]
                color_bbox = (0, 0, 255)
                if obj_type == center_obj_type:
                    color_bbox = (255, 0, 0)
                if in_3d_mode:
                    bbox3d_dict = obj["bbox3d"]
                    bbox3d = Bbox3d(coordinates=bbox3d_dict["coordinates"])
                    corners_3d = self.calibration.box3d_to_corners3d(bbox3d)
                    corners_camera = self.world_to_image_projection(corners_3d.reshape(-1, 3), camera_params)
                    image = self.plot_rect3d_on_img(image, corners_camera, color=color_bbox, thickness=thickness)
                else:
                    bbox2d = obj["bbox"]
                    bbox2d_corners = [
                        (bbox2d["leftX"], bbox2d["bottomY"]),
                        (bbox2d["rightX"], bbox2d["bottomY"]),
                        (bbox2d["rightX"], bbox2d["topY"]),
                        (bbox2d["leftX"], bbox2d["topY"]),
                    ]
                    bbox2d_corners = np.array(bbox2d_corners).astype(int).reshape((-1, 1, 2))
                    image = cv2.polylines(image, [bbox2d_corners], isClosed=True, color=color_bbox, thickness=thickness)

            cluster_idx += 1
        return image

    def draw_confined_area(
        self,
        frame_data: dict[str, Any],
        image: np.ndarray,
        sensor_id: str,
        bbox_color: tuple[int, int, int] = (0, 0, 255),
        bbox_thickness: int = 3,
        roi_color: tuple[int, int, int] = (0, 0, 255),
        roi_thickness: int = 3,
        in_3d_mode: bool = True,
    ) -> np.ndarray:
        """Draw confined area visualization on an image.

        This method visualizes confined areas by drawing ROI boundaries and bounding boxes
        for objects that violate confinement rules. It supports both 2D and 3D visualization modes.

        :param frame_data: Dictionary containing frame analysis data (dict[str, Any])
        :param image: Input image to draw on (np.ndarray)
        :param sensor_id: ID of the camera sensor for coordinate transformation (str)
        :param bbox_color: RGB color tuple for bounding boxes (default: red) (tuple[int, int, int])
        :param bbox_thickness: Bounding box line thickness in pixels (default: 3) (int)
        :param roi_color: RGB color tuple for ROI boundaries (default: red) (tuple[int, int, int])
        :param roi_thickness: ROI boundary line thickness in pixels (default: 3) (int)
        :param in_3d_mode: Whether to use 3D or 2D visualization mode (default: True) (bool)
        :return np.ndarray: Image with confined area visualization

        Example::
            >>> frame_data = {
            ...     "confinedArea": {"objectIds": ["obj1", "obj2"]},
            ...     "objects": [{"id": "obj1", "type": "Pallet"}]
            ... }
            >>> image = analyzer.draw_confined_area(frame_data, image, "camera1")
        """
        if in_3d_mode:
            camera_params = self.calibration.get_cam_params(sensor_id)
        # rois = self.calibration.global_rois
        rois = self.calibration.sensor_map[sensor_id].rois
        confined_area_data = frame_data["confinedArea"]
        objects_all = frame_data["objects"]
        objects_all_map = {obj["id"]: obj for obj in objects_all}
        violation_object_id_list = confined_area_data["objectIds"]
        violation_object_type_list = []
        violation_object_list = [objects_all_map[obj_id] for obj_id in violation_object_id_list]
        for obj in violation_object_list:
            obj_type = obj["type"]
            if obj_type not in violation_object_type_list:
                violation_object_type_list.append(obj_type)
        for roi in rois:
            relevant_roi_flag = False
            for obj_type in violation_object_type_list:
                if obj_type in roi.confinedObjectTypes:
                    relevant_roi_flag = True
            if relevant_roi_flag:
                image = self.draw_roi(image, sensor_id, roi, color=roi_color, thickness=roi_thickness)
        for obj in violation_object_list:
            if in_3d_mode:
                bbox3d_dict = obj["bbox3d"]
                bbox3d = Bbox3d(coordinates=bbox3d_dict["coordinates"])
                corners_3d = self.calibration.box3d_to_corners3d(bbox3d)
                corners_camera = self.world_to_image_projection(corners_3d.reshape(-1, 3), camera_params)
                image = self.plot_rect3d_on_img(image, corners_camera, color=bbox_color, thickness=bbox_thickness)
            else:
                bbox2d = obj["bbox"]
                bbox2d_corners = [
                    (bbox2d["leftX"], bbox2d["bottomY"]),
                    (bbox2d["rightX"], bbox2d["bottomY"]),
                    (bbox2d["rightX"], bbox2d["topY"]),
                    (bbox2d["leftX"], bbox2d["topY"]),
                ]
                bbox2d_corners = np.array(bbox2d_corners).astype(int).reshape((-1, 1, 2))
                image = cv2.polylines(
                    image, [bbox2d_corners], isClosed=True, color=bbox_color, thickness=bbox_thickness
                )

        return image

    def draw_restricted_area(
        self,
        frame_data: dict[str, Any],
        image: np.ndarray,
        sensor_id: str,
        bbox_color: tuple[int, int, int] = (0, 0, 255),
        bbox_thickness: int = 3,
        roi_color: tuple[int, int, int] = (0, 0, 255),
        roi_thickness: int = 3,
        in_3d_mode: bool = True,
    ) -> np.ndarray:
        """Draw restricted area visualization on an image.

        This method visualizes restricted areas by drawing ROI boundaries and bounding boxes
        for objects that violate area restrictions. It supports both 2D and 3D visualization modes.

        :param frame_data: Dictionary containing frame analysis data (dict[str, Any])
        :param image: Input image to draw on (np.ndarray)
        :param sensor_id: ID of the camera sensor for coordinate transformation (str)
        :param bbox_color: RGB color tuple for bounding boxes (default: red) (tuple[int, int, int])
        :param bbox_thickness: Bounding box line thickness in pixels (default: 3) (int)
        :param roi_color: RGB color tuple for ROI boundaries (default: red) (tuple[int, int, int])
        :param roi_thickness: ROI boundary line thickness in pixels (default: 3) (int)
        :param in_3d_mode: Whether to use 3D or 2D visualization mode (default: True) (bool)
        :return np.ndarray: Image with restricted area visualization

        Example::
            >>> frame_data = {
            ...     "restrictedArea": [{"roiId": "zone1", "objectIds": ["obj1"]}],
            ...     "objects": [{"id": "obj1", "type": "Pallet"}]
            ... }
            >>> image = analyzer.draw_restricted_area(frame_data, image, "camera1")
        """
        if in_3d_mode:
            camera_params = self.calibration.get_cam_params(sensor_id)
        # rois = self.calibration.global_rois
        rois = self.calibration.sensor_map[sensor_id].rois
        restricted_area_data_all = frame_data["restrictedArea"]
        objects_all = frame_data["objects"]
        objects_all_map = {obj["id"]: obj for obj in objects_all}
        for restricted_area_data in restricted_area_data_all:
            roi_id = restricted_area_data["roiId"]
            # for global_roi in global_rois:
            for roi in rois:
                if roi.id == roi_id:
                    image = self.draw_roi(image, sensor_id, roi, color=roi_color, thickness=roi_thickness)
            violation_object_id_list = restricted_area_data["objectIds"]
            violation_object_list = [objects_all_map[obj_id] for obj_id in violation_object_id_list]
            for obj in violation_object_list:
                if in_3d_mode:
                    bbox3d_dict = obj["bbox3d"]
                    bbox3d = Bbox3d(coordinates=bbox3d_dict["coordinates"])
                    corners_3d = self.calibration.box3d_to_corners3d(bbox3d)
                    corners_camera = self.world_to_image_projection(corners_3d.reshape(-1, 3), camera_params)
                    image = self.plot_rect3d_on_img(image, corners_camera, color=bbox_color, thickness=bbox_thickness)
                else:
                    bbox2d = obj["bbox"]
                    bbox2d_corners = [
                        (bbox2d["leftX"], bbox2d["bottomY"]),
                        (bbox2d["rightX"], bbox2d["bottomY"]),
                        (bbox2d["rightX"], bbox2d["topY"]),
                        (bbox2d["leftX"], bbox2d["topY"]),
                    ]
                    bbox2d_corners = np.array(bbox2d_corners).astype(int).reshape((-1, 1, 2))
                    image = cv2.polylines(
                        image, [bbox2d_corners], isClosed=True, color=bbox_color, thickness=bbox_thickness
                    )

        return image

    def draw_roi(
        self, image: np.ndarray, sensor_id: str, roi: Any, color: tuple[int, int, int] = (0, 0, 255), thickness: int = 5
    ) -> np.ndarray:
        """Draw a region of interest (ROI) on an image using perspective transformation.

        This method draws the boundary of a region of interest on an image by transforming
        its 3D world coordinates to image coordinates using the camera's homography matrix.

        :param image: Input image to draw on (np.ndarray)
        :param sensor_id: ID of the camera sensor for coordinate transformation (str)
        :param roi: ROI object containing roiCoordinates attribute with 3D world coordinates (Any)
        :param color: RGB color tuple for drawing the ROI boundary (default: red) (tuple[int, int, int])
        :param thickness: Line thickness in pixels for drawing the ROI boundary (default: 5) (int)
        :return np.ndarray: Image with ROI boundary drawn

        Example::
            >>> roi = ROI(id="zone1", roiCoordinates=[Point2D(x=0, y=0), Point2D(x=1, y=1)])
            >>> image = analyzer.draw_roi(image, "camera1", roi, color=(0, 255, 0))
        """
        hmatrix = self.calibration.sensor_map[sensor_id].homography
        hmatrix_inv = np.linalg.inv(np.array(hmatrix))
        hmatrix_inv = hmatrix_inv.tolist()
        roi_corners_3d = [[pt.x, pt.y] for pt in roi.roiCoordinates]
        roi_corners_image = [self.calibration.perspective_transform(px, py, hmatrix_inv) for px, py in roi_corners_3d]
        roi_corners_image = np.array(roi_corners_image).astype(int).reshape((-1, 1, 2))
        cv2.polylines(image, [roi_corners_image], isClosed=True, color=color, thickness=thickness)
        return image
