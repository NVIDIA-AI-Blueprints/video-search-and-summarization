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

import argparse
import json
import logging
import math
import os
import statistics
import time

import numpy as np
import requests
from shapely import wkt
from shapely.geometry import Point, Polygon

from mdx.analytics.core.schema.config import AppConfig
from mdx.analytics.core.transform.calibration.calibration_e import CalibrationE, CalibrationES
from mdx.analytics.core.utils.io_utils import ValidateFile, load_json_from_file, validate_file_path


class USDSearch:
    """
    A controller module for searching and analyzing USD (Universal Scene Description) scene elements.

    The class supports loading data either from JSON files or through API endpoints.
    Controlled by configuration settings.

    This class provides functionality to:
    - Search and retrieve pallet information
    - Analyze buffer zones and their dimensions
    - Find optimal camera positions for viewing specific areas
    - Map relationships between cameras, BEVs (Bird's Eye View), and buffer zones
    - Process proximity alerts and frame alerts
    - Calculate camera coverage and visibility scores

    :param str config_path: Path to the app config file.
    :param str | None calibration_path: Path to the calibration file in JSON format.
    :param str | None asset_json_path: Path to the asset JSON file.
    :ivar AppConfig config: Application configuration object.
    :ivar CalibrationE calibration: Calibration module for coordinate transformation.
    :ivar dict asset_dict: Dictionary containing asset information.
    :ivar str prim_query_base_url: Base URL for primitive queries.
    :ivar list prim_pallets: List of pallet primitives.
    :ivar list prim_buffer_zones: List of buffer zone primitives.
    :ivar list prim_cameras: List of camera primitives.
    :ivar dict pallet_size_map: Mapping of pallet IDs to their sizes.
    :ivar dict buffer_zone_to_camera_map: Mapping of buffer zones to cameras.
    :ivar dict bev_camera_map: Mapping of BEVs to cameras.
    :ivar dict camera_fov_coverage_map: Mapping of cameras to their FOV coverage.

    Examples::
        >>> usd_search = USDSearch("path/to/config.json")
        >>> pallet_size = usd_search.get_pallet_size("Pallet_A1")
        >>> best_camera = usd_search.get_best_camera_on_buffer_zone("buffer_zone_1")
    """

    def __init__(self, config_path: str, calibration_path: str | None = None, asset_json_path: str | None = None) -> None:
        # Make sure the config file exists
        valid_config_path = validate_file_path(config_path)
        if not os.path.exists(valid_config_path):
            logging.error(f"ERROR: The indicated config file `{valid_config_path}` does NOT exist.")
            exit(1)
        self.config = AppConfig(**load_json_from_file(valid_config_path))
        logging.info(f"Read config from {valid_config_path}\n")

        self.api_retry_max_cnt = self.config.api_retry_max_count
        self.api_retry_max_time_sec = self.config.api_retry_max_time_seconds

        if calibration_path:
            print(f"initialize calibration from file: {calibration_path}")
            self.calibration = CalibrationE(self.config, calibration_file_path=calibration_path)
        else:
            print("initialize calibration from API")
            self.calibration = CalibrationES(self.config)

        self.asset_json_path = asset_json_path
        self.use_asset_json = self.config.get_bool_app_config("useAssetJson")
        if self.use_asset_json:
            if asset_json_path:
                self.asset_dict = load_json_from_file(self.asset_json_path)
            else:
                self.asset_dict = self._fetch_asset_from_api()
        self.prim_query_base_url = self._get_ags_base_url()
        self.prim_pallets = []
        self.prim_buffer_zones = []
        self.prim_cameras = []
        self.pallet_size_map = {}
        self.buffer_zone_to_camera_map = {}
        self.bev_camera_map = {}
        self.camera_fov_coverage_map = {}
        self._initial_fetch()

    def get_all_pallet_ids(self) -> list:
        """
        Get the list of all available pallet IDs.

        This method:
        1. Checks if pallet size map exists, fetches if not
        2. Retrieves all pallet IDs from the size map
        3. Returns list of pallet identifiers

        :return list[str]: List of all available pallet IDs.

        Examples::
            >>> usd_search = USDSearch("config.json")
            >>> pallet_ids = usd_search.get_all_pallet_ids()
            >>> print(f"Available pallets: {pallet_ids}")
        """
        if not self.pallet_size_map:
            self.prim_pallets = self._fetch_pallets()
            self.pallet_size_map = self._fetch_pallet_size(self.prim_pallets)
        return list(self.pallet_size_map.keys())

    def get_pallet_size(self, pallet_id: str) -> float | None:
        """
        Get the size of a pallet given its ID.

        This method:
        1. Checks if pallet size map exists, fetches if not
        2. Looks up the pallet ID in the size map
        3. Returns the size or None if not found

        :param str pallet_id: The ID of the pallet to look up.
        :return float | None: The size of the pallet, or None if not found.

        Examples::
            >>> usd_search = USDSearch("config.json")
            >>> size = usd_search.get_pallet_size("Pallet_A1")
            >>> print(f"Pallet size: {size}")
        """
        if not self.pallet_size_map:
            self.prim_pallets = self._fetch_pallets()
            self.pallet_size_map = self._fetch_pallet_size(self.prim_pallets)

        if pallet_id in self.pallet_size_map:
            return self.pallet_size_map[pallet_id]
        else:
            print(f"{pallet_id} is not found in the usd file")
            print(f"available pallet_ids are: {self.pallet_size_map.keys()}")
        return None

    def get_buffer_zone_dimensions(self, buffer_zone_id: str) -> list:
        """
        Get the dimensions of a buffer zone given its ID.

        This method:
        1. Retrieves available ROIs from calibration
        2. Searches for the specified buffer zone
        3. Returns coordinates if found, empty list if not

        :param str buffer_zone_id: The ID of the buffer zone to look up.
        :return list[list[float]]: List of [x,y] coordinates defining the buffer zone dimensions, or empty list if not found.

        Examples::
            >>> usd_search = USDSearch("config.json")
            >>> dimensions = usd_search.get_buffer_zone_dimensions("zone_1")
            >>> print(f"Zone dimensions: {dimensions}")
        """
        available_rois = []
        rois_info = self.calibration.global_buffer_zones
        for roi_info in rois_info:
            roi_id = roi_info.id
            available_rois.append(roi_id)
            if roi_id == buffer_zone_id:
                return [[pt.x, pt.y] for pt in roi_info.roiCoordinates]
        print(f"{buffer_zone_id} is not found in the usd/calibration file")
        print(f"available buffer_zone_ids are: {available_rois}")
        return []

    def get_cameras_on_bev(self, bev_id: str) -> list:
        """
        Get list of cameras associated with a BEV (Bird's Eye View) ID.

        This method:
        1. Checks if BEV camera map exists, fetches if not
        2. Looks up the BEV ID in the camera map
        3. Returns associated cameras or empty list if not found

        :param str bev_id: The BEV ID to look up.
        :return list[str]: List of camera IDs associated with the BEV, or empty list if not found.

        Examples::
            >>> usd_search = USDSearch("config.json")
            >>> cameras = usd_search.get_cameras_on_bev("BEV_1")
            >>> print(f"Cameras on BEV: {cameras}")
        """
        if not self.bev_camera_map:
            self.bev_camera_map = self._fetch_bev_groups()
        if bev_id in self.bev_camera_map:
            return self.bev_camera_map[bev_id]
        else:
            print(f"{bev_id} is not found in the usd/calibration file")
            print(f"available bev_ids are: {self.bev_camera_map.keys()}")
        return []

    def get_best_camera_on_bev(self, bev_id: str) -> str | None:
        """
        Get the best camera for a given BEV ID based on FOV coverage.

        This method:
        1. Gets list of cameras associated with BEV
        2. Checks FOV coverage for each camera
        3. Returns camera with largest coverage area

        :param str bev_id: The BEV ID to analyze.
        :return str | None: ID of the best camera, or None if no cameras found.

        Examples::
            >>> usd_search = USDSearch("config.json")
            >>> best_camera = usd_search.get_best_camera_on_bev("BEV_1")
            >>> print(f"Best camera: {best_camera}")
        """
        cameras_on_bev = self.get_cameras_on_bev(bev_id)
        if not self.camera_fov_coverage_map:
            self.camera_fov_coverage_map = self._fetch_camera_fov_coverage()
        if cameras_on_bev:
            best_camera_id = cameras_on_bev[0]
            best_camera_fov_coverage = 0.0
            for camera_id, camera_fov_coverage in self.camera_fov_coverage_map.items():
                if camera_id in cameras_on_bev and camera_fov_coverage > best_camera_fov_coverage:
                    best_camera_id = camera_id
                    best_camera_fov_coverage = camera_fov_coverage
            return best_camera_id
        else:
            print(f"no cameras found under the provided bev_id: {bev_id}")
        return None

    def get_best_camera_list_on_bev_list(self, bev_id_list: list = []) -> list:
        """
        Get the best camera for each BEV (Bird's Eye View) in a list.

        This method:
        1. Iterates over the provided list of BEV IDs
        2. For each BEV, finds the best camera (using default coordinates 0, 0)
        3. Returns a list of best camera IDs for each BEV

        :param list bev_id_list: List of BEV IDs to process.
        :return list[str]: List of best camera IDs for each BEV in the input list.

        Examples::
            >>> usd_search = USDSearch("config.json")
            >>> bev_ids = ["BEV_1", "BEV_2"]
            >>> best_cameras = usd_search.get_best_camera_list_on_bev_list(bev_ids)
            >>> print(f"Best cameras: {best_cameras}")
        """
        if not self.bev_camera_map:
            self.bev_camera_map = self._fetch_bev_groups()
        bev_list = self.bev_camera_map.keys()
        if len(bev_id_list) > 0:
            bev_list = bev_id_list
        else:
            print("no bev groups provided, trying to find the best camera for every available bev groups")
        best_camera_list = []
        for bev_id in bev_list:
            best_camera = self.get_best_camera_on_bev(bev_id)
            best_camera_list.append(best_camera)
        return best_camera_list

    def get_best_camera_on_proximity_data(self, proximity_data: dict, frames_from_file: bool = False) -> str | None:
        """
        Get best camera for viewing proximity alert data.

        This method:
        1. Loads proximity data from file if specified
        2. Gets list of cameras for the BEV
        3. Calculates camera scores based on proximity clusters
        4. Returns camera with highest score

        :param dict proximity_data: Dictionary containing proximity alert data.
        :param bool frames_from_file: If True, loads proximity data from file path.
        :return str | None: ID of best camera for viewing the proximity alerts, or None if not found.

        Examples::
            >>> usd_search = USDSearch("config.json")
            >>> best_camera = usd_search.get_best_camera_on_proximity_data(proximity_data)
            >>> print(f"Best camera: {best_camera}")
        """
        if frames_from_file:
            proximity_data = load_json_from_file(proximity_data)
        frames_data = proximity_data["alerts"]
        # assume data is already in order (descending) from mdxAPI
        if len(frames_data) > 0:
            frame_data = frames_data[0]
            bev_id = frame_data["sensorId"]
            clusters = frame_data["proximity"]["clusters"]
            camera_list = self.get_cameras_on_bev(bev_id)

            if len(camera_list) > 0:
                best_camera_id = camera_list[0]
                best_camera_score = 0.0
                for camera_id in camera_list:
                    camera_score = self._compute_camera_score_on_proximity_data(camera_id, clusters)
                    if camera_score > best_camera_score:
                        best_camera_score = camera_score
                        best_camera_id = camera_id
                if best_camera_score <= 0:
                    print("There are no available cameras that can see any of the proximity centers")
                return best_camera_id
        return None

    def get_best_camera_on_frame_alerts(self, mdx_frame_alerts: dict, frames_from_file: bool = False) -> str | None:
        """
        Get best camera for viewing frame alerts.

        This method:
        1. Loads frame alerts from file if specified
        2. Gets list of cameras for the BEV
        3. Calculates camera scores based on object positions
        4. Returns camera with highest score

        :param dict mdx_frame_alerts: Dictionary containing frame alert data.
        :param bool frames_from_file: If True, loads alert data from file path.
        :return str | None: ID of best camera for viewing the alerts, or None if not found.

        Examples::
            >>> usd_search = USDSearch("config.json")
            >>> best_camera = usd_search.get_best_camera_on_frame_alerts(frame_alerts)
            >>> print(f"Best camera: {best_camera}")
        """
        if frames_from_file:
            mdx_frame_alerts = load_json_from_file(mdx_frame_alerts)
        frames_data = mdx_frame_alerts["alerts"]
        # assume data is already in order (descending) from mdxAPI
        if len(frames_data) > 0:
            frame_data = frames_data[0]
            bev_id = frame_data["sensorId"]
            camera_list = self.get_cameras_on_bev(bev_id)

            objects_all = frame_data["objects"]
            objects_all_map = {obj["id"]: obj for obj in objects_all}
            objects_id_involved = []

            if "proximity" in frame_data:
                cluster_info = frame_data["proximity"]["info"]
                proximity_violation_objects_all_clusters = cluster_info["proximityViolationObjects"].split("|")
                for proximity_violation_objects in proximity_violation_objects_all_clusters:
                    proximity_violation_object_id_list = proximity_violation_objects.split(",")
                    for proximity_violation_object_id in proximity_violation_object_id_list:
                        if proximity_violation_object_id not in objects_id_involved:
                            objects_id_involved.append(proximity_violation_object_id)
            if "confinedArea" in frame_data:
                for obj_id in frame_data["confinedArea"]["objectIds"]:
                    if obj_id not in objects_id_involved:
                        objects_id_involved.append(obj_id)
            if "restrictedArea" in frame_data:
                restricted_areas = frame_data["restrictedArea"]
                for restricted_area in restricted_areas:
                    for obj_id in restricted_area["objectIds"]:
                        if obj_id not in objects_id_involved:
                            objects_id_involved.append(obj_id)
            objects_involved = [objects_all_map[obj_id] for obj_id in objects_id_involved]

            if len(camera_list) > 0:
                best_camera_id = camera_list[0]
                best_camera_score = 0.0
                for camera_id in camera_list:
                    camera_score = self._compute_camera_score_on_objects(camera_id, objects_involved)
                    if camera_score > best_camera_score:
                        best_camera_score = camera_score
                        best_camera_id = camera_id
                if best_camera_score <= 0:
                    print("There are no available cameras that can see any of the proximity centers")
                return best_camera_id
        return None

    def _compute_camera_score_on_proximity_data(
        self, camera_id: str, clusters: list, camera_frame_size=(1920, 1080)
    ) -> float:
        """
        Compute camera score based on proximity data clusters.

        This method:
        1. Transforms camera corners to 3D space
        2. Creates a polygon from camera corners
        3. Calculates scores for each cluster point
        4. Returns weighted score based on number of points and distances

        :param str camera_id: Camera ID to evaluate.
        :param list clusters: List of proximity data clusters.
        :param tuple camera_frame_size: Camera frame dimensions (width, height). Defaults to (1920, 1080).
        :return float: Score indicating how well camera covers the proximity clusters.

        Examples::
            >>> score = usd_search._compute_camera_score_on_proximity_data("camera_1", clusters)
            >>> print(f"Camera score: {score}")
        """
        scores = []
        points_in_3d = []
        hmatrix = self.calibration.sensor_map[camera_id].homography
        camera_corners = [
            (0, 0),
            (camera_frame_size[0], 0),
            (camera_frame_size[0], camera_frame_size[1]),
            (0, camera_frame_size[1]),
        ]
        camera_corners_3d = [self.calibration.perspective_transform(x, y, hmatrix) for x, y in camera_corners]
        camera_corners_3d_poly = Polygon(camera_corners_3d)
        for cluster in clusters:
            points = cluster["points"]
            point = (points[0]["x"], points[0]["y"])
            points_in_3d.append(point)
        for point_3d in points_in_3d:
            PT = Point(point_3d)
            multiplier = 1
            if not (camera_corners_3d_poly.contains(PT) or camera_corners_3d_poly.touches(PT)):
                multiplier = -1
            scores.append(multiplier * camera_corners_3d_poly.boundary.distance(PT))
        if len(scores) > 0:
            positive_numbers = [n for n in scores if n > 0]
            if positive_numbers:
                # consider both number of center covered and localtion, giving large weight to number
                score = len(positive_numbers) + 0.01 * min(positive_numbers)
                return score
            else:
                negative_numbers = [n for n in scores if n <= 0]
                if negative_numbers:
                    score = max(negative_numbers)
                    return score
        return 0.0

    def _compute_camera_score_on_objects(self, camera_id: str, objects: list, camera_frame_size=(1920, 1080)) -> float:
        """
        Compute camera score based on object positions.

        This method:
        1. Transforms camera corners to 3D space
        2. Creates a polygon from camera corners
        3. Calculates scores for each object point
        4. Returns weighted score based on number of points and distances

        :param str camera_id: Camera ID to evaluate.
        :param list objects: List of objects with 3D coordinates.
        :param tuple camera_frame_size: Camera frame dimensions (width, height). Defaults to (1920, 1080).
        :return float: Score indicating how well camera covers the objects.

        Examples::
            >>> score = usd_search._compute_camera_score_on_objects("camera_1", objects)
            >>> print(f"Camera score: {score}")
        """
        scores = []
        points_in_3d = []
        hmatrix = self.calibration.sensor_map[camera_id].homography
        camera_corners = [
            (0, 0),
            (camera_frame_size[0], 0),
            (camera_frame_size[0], camera_frame_size[1]),
            (0, camera_frame_size[1]),
        ]
        camera_corners_3d = [self.calibration.perspective_transform(x, y, hmatrix) for x, y in camera_corners]
        camera_corners_3d_poly = Polygon(camera_corners_3d)
        for obj in objects:
            # 3D mode
            x = obj["bbox3d"]["coordinates"][0]
            y = obj["bbox3d"]["coordinates"][1]
            point = (x, y)
            points_in_3d.append(point)
        for point_3d in points_in_3d:
            PT = Point(point_3d)
            multiplier = 1
            if not (camera_corners_3d_poly.contains(PT) or camera_corners_3d_poly.touches(PT)):
                multiplier = -1
            scores.append(multiplier * camera_corners_3d_poly.boundary.distance(PT))
        if len(scores) > 0:
            positive_numbers = [n for n in scores if n > 0]
            if positive_numbers:
                # consider both number of center covered and localtion, giving large weight to number
                score = len(positive_numbers) + 0.01 * min(positive_numbers)
                return score
            else:
                negative_numbers = [n for n in scores if n <= 0]
                if negative_numbers:
                    score = max(negative_numbers)
                    return score
        return 0.0

    def get_best_camera_on_buffer_zone(self, buffer_zone_id: str) -> str | None:
        """
        Get best camera for viewing a specific buffer zone.

        This method:
        1. Gets list of cameras that can see the buffer zone
        2. Sorts cameras by visibility score
        3. Returns camera with highest score

        :param str buffer_zone_id: ID of buffer zone to analyze.
        :return str | None: ID of best camera for viewing the buffer zone, or None if not found.

        Examples::
            >>> usd_search = USDSearch("config.json")
            >>> best_camera = usd_search.get_best_camera_on_buffer_zone("buffer_zone_1")
            >>> print(f"Best camera: {best_camera}")
        """
        if not self.buffer_zone_to_camera_map:
            self.buffer_zone_to_camera_map = self._find_camera_visibility_on_buffer_zone()
        if buffer_zone_id in self.buffer_zone_to_camera_map:
            cam_list = self.buffer_zone_to_camera_map[buffer_zone_id]
            cam_list_sorted = sorted(cam_list, key=lambda x: x[1])  # ascending order
            return cam_list_sorted[-1][0]
        else:
            print(f"{buffer_zone_id} is not found in the usd/calibration file")
            print(f"available buffer_zone_ids are: {self.buffer_zone_to_camera_map.keys()}")
        return None

    def get_best_camera_on_buffer_zones(self, buffer_zones: list = []) -> str | None:
        """
        Get best camera for viewing multiple buffer zones.

        This method:
        1. Gets lists of cameras for each buffer zone
        2. Finds cameras that can see all zones
        3. Calculates combined score for each camera
        4. Returns camera with highest score

        :param list buffer_zones: List of buffer zone IDs. If empty, analyzes all buffer zones.
        :return str | None: ID of best camera for viewing all specified buffer zones, or None if not found.

        Examples::
            >>> usd_search = USDSearch("config.json")
            >>> best_camera = usd_search.get_best_camera_on_buffer_zones(["zone_1", "zone_2"])
            >>> print(f"Best camera: {best_camera}")
        """
        if not self.buffer_zone_to_camera_map:
            self.buffer_zone_to_camera_map = self._find_camera_visibility_on_buffer_zone()
        camera_lists = []
        if len(buffer_zones) > 0:
            for buffer_zone_id in buffer_zones:
                if buffer_zone_id not in self.buffer_zone_to_camera_map:
                    print(f"{buffer_zone_id} is not found in the usd/calibration file")
                    print(f"available buffer_zone_ids are: {self.buffer_zone_to_camera_map.keys()}")
                else:
                    camera_lists.append(self.buffer_zone_to_camera_map[buffer_zone_id])
        else:
            print("no buffer zones provided, trying to find camera to fully cover all available buffer zones")
            for buffer_zone_id, cam_list in self.buffer_zone_to_camera_map.items():
                camera_lists.append(cam_list)
        common_cameras = self._find_common_cameras(camera_lists)
        if len(common_cameras) > 0:
            best_camera_id = common_cameras[0]
            best_camera_score = 0.0
            for camera_id in common_cameras:
                camera_score = self._compute_camera_score_on_buffer_zone(camera_id, camera_lists)
                if camera_score > best_camera_score:
                    best_camera_score = camera_score
                    best_camera_id = camera_id
            return best_camera_id
        else:
            print("there is no camera that can fully see all the provided buffer zones")
        return None

    def get_zone_camera_bev_on_buffer_zones(self, buffer_zones: list = []) -> tuple[list, list, list, list]:
        """
        Get mapping of buffer zones to cameras and BEVs.

        This method:
        1. Gets buffer zone to BEV mapping
        2. Groups buffer zones by BEV
        3. Gets best camera for each group
        4. Returns lists of zones, cameras, BEVs, and areas

        :param list buffer_zones: List of buffer zone IDs. If empty, analyzes all buffer zones.
        :return tuple[list[list[str]], list[str], list[str], list[str]]: Tuple containing (buffer_zones_list, camera_list, bev_id_list, area_id_list).

        Examples::
            >>> usd_search = USDSearch("config.json")
            >>> zones, cameras, bevs, areas = usd_search.get_zone_camera_bev_on_buffer_zones()
            >>> print(f"Zones: {zones}, Cameras: {cameras}, BEVs: {bevs}, Areas: {areas}")
        """
        if not self.buffer_zone_to_camera_map:
            self.buffer_zone_to_camera_map = self._find_camera_visibility_on_buffer_zone()

        buffer_zone_to_bev_map = self._get_buffer_zone_to_bev_map()
        buffer_zone_groups = {}
        queried_buffer_zones = self.buffer_zone_to_camera_map.keys() if len(buffer_zones) == 0 else buffer_zones
        for buffer_zone_id in queried_buffer_zones:
            bev_id_list = buffer_zone_to_bev_map[buffer_zone_id]
            bev_id = bev_id_list[0]
            if bev_id not in buffer_zone_groups:
                buffer_zone_groups[bev_id] = []
            buffer_zone_groups[bev_id].append(buffer_zone_id)
        bev_id_list = []
        camera_list = []
        buffer_zones_list = []
        area_id_list = []

        bev_to_area_map = self._get_bev_to_area_map()

        for bev_id, buffer_zone_list in buffer_zone_groups.items():
            buffer_zones_list.append(buffer_zone_list)
            bev_id_list.append(bev_id)
            if bev_id in bev_to_area_map:
                area_id = bev_to_area_map[bev_id]
                area_id_list.append(area_id)
            camera_list.append(self.get_best_camera_on_buffer_zones(buffer_zone_list))

        return buffer_zones_list, camera_list, bev_id_list, area_id_list

    def get_bev_on_areas(self, areas: list = []) -> tuple[list, list]:
        """
        Get BEV IDs associated with area IDs.

        This method:
        1. Gets area to BEV mapping
        2. Filters by provided areas if specified
        3. Returns lists of BEV IDs and area IDs

        :param list areas: List of area IDs. If empty, analyzes all areas.
        :return tuple[list[str], list[str]]: Tuple containing (bev_id_list, area_id_list).

        Examples::
            >>> usd_search = USDSearch("config.json")
            >>> bevs, areas = usd_search.get_bev_on_areas(["area_1", "area_2"])
            >>> print(f"BEVs: {bevs}, Areas: {areas}")
        """
        area_to_bev_map = self._get_area_to_bev_map()
        queried_areas = list(area_to_bev_map.keys()) if len(areas) == 0 else areas
        queried_areas.sort()
        bev_id_list = []
        area_id_list = []
        for area_id in queried_areas:
            if area_id in area_to_bev_map:
                bev_id_list.append(area_to_bev_map[area_id])
                area_id_list.append(area_id)
            else:
                print(f"{area_id} is not found in the usd/calibration file")
                print(f"available area_ids are: {area_to_bev_map.keys()}")
        return bev_id_list, area_id_list

    def get_zone_camera_bev_on_areas(self, areas: list = []) -> tuple[list, list, list, list]:
        """
        Get mapping of areas to buffer zones, cameras and BEVs.

        This method:
        1. Gets BEVs for specified areas
        2. Gets buffer zones for each BEV
        3. Gets best camera for each buffer zone group
        4. Returns lists of zones, cameras, BEVs, and areas

        :param list areas: List of area IDs. If empty, analyzes all areas.
        :return tuple[list[list[str]], list[str], list[str], list[str]]: Tuple containing (buffer_zones_list, camera_list, bev_id_list, area_id_list).

        Examples::
            >>> usd_search = USDSearch("config.json")
            >>> zones, cameras, bevs, areas = usd_search.get_zone_camera_bev_on_areas()
            >>> print(f"Zones: {zones}, Cameras: {cameras}, BEVs: {bevs}, Areas: {areas}")
        """
        bev_id_list, area_id_list = self.get_bev_on_areas(areas)
        bev_to_buffer_zone_map = self._get_bev_to_buffer_zone_map()
        buffer_zones_list = [bev_to_buffer_zone_map[bev_id] for bev_id in bev_id_list]
        camera_list = [self.get_best_camera_on_buffer_zones(buffer_zone_list) for buffer_zone_list in buffer_zones_list]
        return buffer_zones_list, camera_list, bev_id_list, area_id_list

    def _get_bev_to_area_map(self) -> dict:
        """
        Create mapping from BEV IDs to area IDs.

        Returns:
            dict: Dictionary mapping BEV IDs to area IDs
        """
        bev_to_area_map = {}
        for sensor_info in self.calibration.sensors:
            if sensor_info.type == "camera":
                group_info = sensor_info.group
                if group_info:
                    group_type = group_info["type"]
                    if group_type == "bev":
                        bev_id = group_info["name"]
                        area_id = group_info["alias"]
                        if bev_id not in bev_to_area_map:
                            bev_to_area_map[bev_id] = area_id
        return bev_to_area_map

    def _get_area_to_bev_map(self) -> dict:
        """
        Create mapping from area IDs to BEV IDs.

        Returns:
            dict: Dictionary mapping area IDs to BEV IDs
        """
        area_to_bev_map = {}
        bev_to_area_map = self._get_bev_to_area_map()
        for bev_id, area_id in bev_to_area_map.items():
            if area_id not in area_to_bev_map:
                area_to_bev_map[area_id] = bev_id
        return area_to_bev_map

    def _get_buffer_zone_to_bev_map(self) -> dict:
        """
        Create mapping from buffer zone IDs to BEV IDs.

        Returns:
            dict: Dictionary mapping buffer zone IDs to lists of BEV IDs
        """
        buffer_zone_to_bev_map = {}
        for global_roi in self.calibration.global_buffer_zones:
            buffer_zone_to_bev_map[global_roi.id] = global_roi.groups
        return buffer_zone_to_bev_map

    def _get_bev_to_buffer_zone_map(self) -> dict:
        """
        Create mapping from BEV IDs to buffer zone IDs.

        Returns:
            dict: Dictionary mapping BEV IDs to lists of buffer zone IDs
        """
        bev_to_buffer_zone_map = {}
        buffer_zone_to_bev_map = self._get_buffer_zone_to_bev_map()
        for buffer_zone_id, bev_list in buffer_zone_to_bev_map.items():
            for bev in bev_list:
                if bev not in bev_to_buffer_zone_map:
                    bev_to_buffer_zone_map[bev] = []
                bev_to_buffer_zone_map[bev].append(buffer_zone_id)
        return bev_to_buffer_zone_map

    def _find_common_cameras(self, camera_lists: list) -> list:
        """
        Find cameras that appear in all provided camera lists.

        This method:
        1. Extracts camera IDs from each list
        2. Finds intersection of all camera ID sets
        3. Returns sorted list of common cameras

        :param list camera_lists: List of lists containing camera information.
        :return list[str]: List of camera IDs that appear in all input lists.

        Examples::
            >>> common_cameras = usd_search._find_common_cameras([cam_list1, cam_list2])
            >>> print(f"Common cameras: {common_cameras}")
        """
        list_of_lists = []
        for camera_list in camera_lists:
            camera_id_list = [camera_info[0] for camera_info in camera_list]
            list_of_lists.append(camera_id_list)
        if not list_of_lists:
            return []

        common_elements = set(list_of_lists[0])
        for lst in list_of_lists[1:]:
            common_elements.intersection_update(lst)
        common_elements = sorted(list(common_elements))
        return common_elements

    def _compute_camera_score_on_buffer_zone(
        self, camera_id: str, camera_lists: list, camera_frame_size=(1920, 1080)
    ) -> float:
        """
        Compute camera score based on buffer zone coverage.

        This method:
        1. Collects visibility sizes for the camera across all buffer zones
        2. Calculates size and balance scores
        3. Returns combined score

        :param str camera_id: Camera ID to evaluate.
        :param list camera_lists: List of camera information for buffer zones.
        :param tuple camera_frame_size: Camera frame dimensions (width, height). Defaults to (1920, 1080).
        :return float: Score indicating how well camera covers the buffer zones.

        Examples::
            >>> score = usd_search._compute_camera_score_on_buffer_zone("camera_1", camera_lists)
            >>> print(f"Buffer zone score: {score}")
        """
        all_buffer_zone_viz_size = []
        for camera_info_on_buffer_zone in camera_lists:
            for camera_name, buffer_zone_viz_size in camera_info_on_buffer_zone:
                if camera_name == camera_id:
                    all_buffer_zone_viz_size.append(buffer_zone_viz_size)

        buffer_zone_viz_size_score = sum(all_buffer_zone_viz_size) / (camera_frame_size[0] * camera_frame_size[1])
        buffer_zone_viz_balance_score = min(all_buffer_zone_viz_size) / max(all_buffer_zone_viz_size)
        buffer_zone_score = buffer_zone_viz_size_score * buffer_zone_viz_balance_score

        return buffer_zone_score

    def _find_camera_visibility_on_buffer_zone(self, view_size=(1920, 1080)) -> dict:
        """
        Determine which cameras can see each buffer zone.

        This method:
        1. Iterates through all buffer zones
        2. For each zone, checks visibility from each camera
        3. Returns mapping of buffer zones to visible cameras with scores

        :param tuple view_size: Camera view dimensions (width, height). Defaults to (1920, 1080).
        :return dict[str, list[tuple[str, float]]]: Dictionary mapping buffer zone IDs to lists of (camera_id, visibility_score) tuples.

        Examples::
            >>> visibility_map = usd_search._find_camera_visibility_on_buffer_zone()
            >>> print(f"Visibility map: {visibility_map}")
        """
        buffer_zone_to_camera_map = {}
        rois_info = self.calibration.global_buffer_zones
        for roi_info in rois_info:
            roi_id = roi_info.id
            roi_corners_3d = [[pt.x, pt.y] for pt in roi_info.roiCoordinates]
            if roi_id not in buffer_zone_to_camera_map:
                buffer_zone_to_camera_map[roi_id] = []
            for sensor_id in self.calibration.sensor_map.keys():
                sensor_info = self.calibration.sensor_map.get(sensor_id)
                if sensor_info.type == "camera":
                    hmatrix = self.calibration.sensor_map[sensor_id].homography
                    hmatrix_inv = np.linalg.inv(np.array(hmatrix))
                    hmatrix_inv = hmatrix_inv.tolist()
                    roi_corners_camera = [
                        self.calibration.perspective_transform(px, py, hmatrix_inv) for px, py in roi_corners_3d
                    ]
                    roi_fully_visible = True
                    for px, py in roi_corners_camera:
                        if not self._pt_in_camera_view(px, py, view_size):
                            roi_fully_visible = False
                            break
                    if roi_fully_visible:
                        geometry = Polygon(roi_corners_camera)
                        buffer_zone_to_camera_map[roi_id].append((sensor_id, round(geometry.area, 2)))
        return buffer_zone_to_camera_map

    def _pt_in_camera_view(self, px: float, py: float, view_size=(1920, 1080)) -> bool:
        """
        Check if a point is within camera view bounds.

        This method:
        1. Checks if point coordinates are within view dimensions
        2. Returns True if point is visible, False otherwise

        :param float px: X coordinate of point.
        :param float py: Y coordinate of point.
        :param tuple view_size: Camera view dimensions (width, height). Defaults to (1920, 1080).
        :return bool: True if point is within view bounds, False otherwise.

        Examples::
            >>> is_visible = usd_search._pt_in_camera_view(100, 100)
            >>> print(f"Point visible: {is_visible}")
        """
        if px < 0 or px > view_size[0] or py < 0 or py > view_size[1]:
            return False
        return True

    def _get_ags_base_url(self, limit: int = 100) -> str | None:
        """
        Get base URL for AGS (Asset Graph Service) queries.

        This method:
        1. Gets AGS server URL and USD path from config
        2. Constructs base URL with query parameters

        :param int limit: Maximum number of results to return. Defaults to 100.
        :return str | None: Base URL for AGS queries, or None if AGS base URL or USD path is not configured.

        Examples::
            >>> base_url = usd_search._get_ags_base_url()
            >>> print(f"Base URL: {base_url}")
        """
        prim_query_base_url = f"""{self.config.api_ags_base_url}/asset_graph/usd/prims?scene_url={self.config.usd_file_path}&limit={limit}"""
        if self.config.usd_file_path == "None" or self.config.api_ags_base_url == "None":
            prim_query_base_url = None
        return prim_query_base_url

    def _get_pallet_query_url(self, base_url: str | None) -> str | None:
        """
        Construct query URL for pallet primitives.

        This method:
        1. Takes base URL
        2. Adds pallet-specific filter

        :param str | None base_url: Base URL for AGS queries, or None.
        :return str | None: Complete URL for querying pallet primitives, or None if base_url is None.

        Examples::
            >>> pallet_url = usd_search._get_pallet_query_url(base_url)
            >>> print(f"Pallet URL: {pallet_url}")
        """
        if not base_url:
            return None
        return f"""{base_url}&properties_filter=class=pallet"""

    def _get_buffer_zone_query_url(self, base_url: str | None) -> str | None:
        """
        Construct query URL for buffer zone primitives.

        This method:
        1. Takes base URL
        2. Adds buffer zone-specific filter

        :param str | None base_url: Base URL for AGS queries, or None.
        :return str | None: Complete URL for querying buffer zone primitives, or None if base_url is None.

        Examples::
            >>> buffer_zone_url = usd_search._get_buffer_zone_query_url(base_url)
            >>> print(f"Buffer zone URL: {buffer_zone_url}")
        """
        if not base_url:
            return None
        return f"""{base_url}&properties_filter=class=tape"""

    def _get_camera_query_url(self, base_url: str | None) -> str | None:
        """
        Construct query URL for camera primitives.

        This method:
        1. Takes base URL
        2. Adds camera-specific filter

        :param str | None base_url: Base URL for AGS queries, or None.
        :return str | None: Complete URL for querying camera primitives, or None if base_url is None.

        Examples::
            >>> camera_url = usd_search._get_camera_query_url(base_url)
            >>> print(f"Camera URL: {camera_url}")
        """
        if not base_url:
            return None
        return f"""{base_url}&prim_type=Camera"""

    def _initial_fetch(self) -> None:
        """
        Initialize class by fetching all necessary data from APIs or files.

        This method:
        1. Fetches pallet information
        2. Fetches buffer zone information
        3. Fetches camera information
        4. Creates pallet size mappings
        5. Creates buffer zone to camera mappings
        6. Creates BEV camera mappings
        7. Creates camera FOV coverage mappings

        :return None: None

        Examples::
            >>> usd_search = USDSearch("config.json")
            >>> usd_search._initial_fetch()
        """
        self.prim_pallets = self._fetch_pallets()
        self.prim_buffer_zones = self._fetch_buffer_zones()
        self.prim_cameras = self._fetch_cameras()
        self.pallet_size_map = self._fetch_pallet_size(self.prim_pallets)
        self.buffer_zone_to_camera_map = self._find_camera_visibility_on_buffer_zone()
        self.bev_camera_map = self._fetch_bev_groups()
        self.camera_fov_coverage_map = self._fetch_camera_fov_coverage()

    def _fetch_bev_groups(self) -> dict:
        """
        Create mapping of BEV (Bird's Eye View) IDs to their associated cameras.

        This method:
        1. Processes calibration sensor information
        2. Groups cameras by their BEV assignments
        3. Returns mapping of BEV IDs to camera lists

        :return dict[str, list[str]]: Dictionary mapping BEV IDs to lists of camera IDs.

        Examples::
            >>> bev_groups = usd_search._fetch_bev_groups()
            >>> print(f"BEV groups: {bev_groups}")
        """
        bev_camera_map = {}
        for sensor_info in self.calibration.sensors:
            if sensor_info.type == "camera":
                group_info = sensor_info.group
                if group_info:
                    group_type = group_info["type"]
                    if group_type == "bev":
                        bev_id = group_info["name"]
                        if bev_id not in bev_camera_map:
                            bev_camera_map[bev_id] = []
                        bev_camera_map[bev_id].append(sensor_info.id)
        return bev_camera_map

    def _fetch_camera_fov_coverage(self) -> dict:
        """
        Calculate field of view (FOV) coverage area for each camera.

        This method:
        1. Processes camera sensor information
        2. Computes FOV area using WKT polygon definition
        3. Returns mapping of cameras to coverage areas

        :return dict[str, float]: Dictionary mapping camera IDs to their FOV coverage areas.

        Examples::
            >>> fov_coverage = usd_search._fetch_camera_fov_coverage()
            >>> print(f"FOV coverage: {fov_coverage}")
        """
        camera_fov_coverage_map = {}
        for sensor_info in self.calibration.sensors:
            if sensor_info.type == "camera":
                sensor_id = sensor_info.id
                if sensor_id not in camera_fov_coverage_map:
                    camera_fov_coverage_map[sensor_id] = 0.0
                attributes = sensor_info.attributes
                if attributes:
                    fov_wkt_string = attributes.get("fieldOfViewPolygon")
                    if fov_wkt_string:
                        shapely_geometry = wkt.loads(fov_wkt_string)
                        fov_area = shapely_geometry.area
                        camera_fov_coverage_map[sensor_id] = fov_area
        return camera_fov_coverage_map

    def _fetch_prims(self, query_url: str | None) -> list:
        """
        Fetch primitives from AGS API with retry logic.

        This method:
        1. Attempts to fetch data from API
        2. Retries on failure up to configured limits
        3. Returns list of primitives or empty list on failure

        :param str query_url: URL for AGS query.
        :return list[Dict]: List of primitives returned by query.

        Examples::
            >>> prims = usd_search._fetch_prims(query_url)
            >>> print(f"Primitives: {prims}")
        """
        if query_url:
            retry_cnt = 0
            retry_time_sec = 0.0
            start = time.time()
            while retry_cnt < self.api_retry_max_cnt and retry_time_sec < self.api_retry_max_time_sec:
                try:
                    response = requests.get(query_url, timeout=5)
                    if response.status_code == 200:
                        return json.loads(response.text)
                    else:
                        logging.warning(
                            "WARNING: load asset from AGS API end point failed -- "
                            "WARNING: will retry if not reached limit -- "
                            f"status code: {response.status_code}"
                        )
                        time.sleep(1)
                        retry_cnt += 1
                        retry_time_sec = time.time() - start
                except requests.exceptions.RequestException as e:
                    logging.warning(
                        "WARNING: load asset from AGS API end point failed -- "
                        "WARNING: will retry if not reached limit -- "
                        f"message: {str(e)}"
                    )
                    time.sleep(1)
                    retry_cnt += 1
                    retry_time_sec = time.time() - start
            logging.warning("WARNING: load asset from AGS API end point failed. Retry limit reached.")
        else:
            logging.warning("WARNING: AGS API end point or usd path is not provided")
        return []

    def _load_asset_json(self, asset_json_path: str) -> list:
        """
        Load asset information from JSON file.

        This method:
        1. Opens and reads JSON file
        2. Returns list of assets or empty list if file not found

        :param str asset_json_path: Path to asset JSON file.
        :return list[Dict]: List of assets from JSON file.

        Examples::
            >>> assets = usd_search._load_asset_json("assets.json")
            >>> print(f"Assets: {assets}")
        """
        if asset_json_path:
            with open(asset_json_path) as f:
                prims = json.load(f)
            return prims
        else:
            print("asset_json_path is not provided")
        return []

    def _fetch_asset_from_api(self) -> dict:
        """
        Fetch asset information from MDX API with retry logic.

        This method:
        1. Attempts to fetch data from MDX API
        2. Retries on failure up to configured limits
        3. Returns asset information or empty dict on failure

        :return Dict: Asset information from API.

        Examples::
            >>> assets = usd_search._fetch_asset_from_api()
            >>> print(f"Assets: {assets}")
        """
        mdx_api_base = self.config.api_mdx_base_url
        asset_query_url = f"""{mdx_api_base}/config/usd-assets"""
        retry_cnt = 0
        retry_time_sec = 0.0
        start = time.time()
        while retry_cnt < self.api_retry_max_cnt and retry_time_sec < self.api_retry_max_time_sec:
            try:
                response = requests.get(asset_query_url, timeout=5)
                if response.status_code == 200:
                    return json.loads(response.text)
                else:
                    logging.warning(
                        "WARNING: load asset from MDX API end point failed -- "
                        "WARNING: will retry if not reached limit -- "
                        f"status code: {response.status_code}"
                    )
                    time.sleep(1)
                    retry_cnt += 1
                    retry_time_sec = time.time() - start
            except requests.exceptions.RequestException as e:
                logging.warning(
                    "WARNING: load asset from MDX API end point failed -- "
                    "WARNING: will retry if not reached limit -- "
                    f"message: {str(e)}"
                )
                time.sleep(1)
                retry_cnt += 1
                retry_time_sec = time.time() - start
        logging.warning("WARNING: load asset from MDX API end point failed. Retry limit reached.")
        return {}

    def _fetch_pallets(self) -> list:
        """
        Fetch pallet information from either JSON file or API.

        This method:
        1. Checks if using asset JSON
        2. Processes data from appropriate source
        3. Returns list of pallet information

        :return list[Dict]: List of pallet information.

        Examples::
            >>> pallets = usd_search._fetch_pallets()
            >>> print(f"Pallets: {pallets}")
        """
        if self.use_asset_json:
            prims = self.asset_dict["assets"] if "assets" in self.asset_dict else []
            for prim in prims:
                prim["usd_path"] = prim["usdPath"]
                prim["bbox_dimension_x"] = prim["bbox"]["dimension"]["x"]
                prim["bbox_dimension_y"] = prim["bbox"]["dimension"]["y"]
                prim["bbox_dimension_z"] = prim["bbox"]["dimension"]["z"]
            return prims
        else:
            base_url = self.prim_query_base_url
            pallet_query_url = self._get_pallet_query_url(base_url)
            return self._fetch_prims(pallet_query_url)

    def _fetch_buffer_zones(self) -> list:
        """
        Fetch buffer zone information from API.

        This method:
        1. Constructs buffer zone query URL
        2. Fetches data from API
        3. Returns list of buffer zone information

        :return list[Dict]: List of buffer zone information.

        Examples::
            >>> buffer_zones = usd_search._fetch_buffer_zones()
            >>> print(f"Buffer zones: {buffer_zones}")
        """
        base_url = self.prim_query_base_url
        buffer_zone_query_url = self._get_buffer_zone_query_url(base_url)
        return self._fetch_prims(buffer_zone_query_url)

    def _fetch_cameras(self) -> list:
        """
        Fetch camera information from API.

        This method:
        1. Constructs camera query URL
        2. Fetches data from API
        3. Returns list of camera information

        :return list[Dict]: List of camera information.

        Examples::
            >>> cameras = usd_search._fetch_cameras()
            >>> print(f"Cameras: {cameras}")
        """
        base_url = self.prim_query_base_url
        camera_query_url = self._get_camera_query_url(base_url)
        return self._fetch_prims(camera_query_url)

    def _fetch_pallet_size(self, prim_pallets: list) -> dict:
        """
        Calculate pallet sizes from primitive information.

        This method:
        1. Processes pallet primitives
        2. Calculates area and size for each pallet
        3. Returns mapping of pallet IDs to sizes

        :param list prim_pallets: List of pallet primitives.
        :return dict[str, float]: Dictionary mapping pallet IDs to their sizes.

        Examples::
            >>> pallet_sizes = usd_search._fetch_pallet_size(pallets)
            >>> print(f"Pallet sizes: {pallet_sizes}")
        """
        pallet_map = {}
        for pallet in prim_pallets:
            pallet_name = pallet["usd_path"].split("/")[-1]
            if pallet_name not in pallet_map:
                pallet_map[pallet_name] = []
            width = pallet["bbox_dimension_x"]
            length = pallet["bbox_dimension_y"]
            area = width * length
            pallet_size = round(math.sqrt(area), 2)
            pallet_map[pallet_name].append(pallet_size)
        for key in pallet_map:
            pallet_map[key] = statistics.mode(pallet_map[key])

        return pallet_map

    def get_sensor_to_roi_map(self, sensor_type: str | None = None) -> dict:
        """
        Create mapping from sensors to their ROIs (Regions of Interest).

        This method:
        1. Processes sensor information from calibration
        2. Filters by sensor type if specified
        3. Creates mapping of sensors to ROI IDs

        :param str | None sensor_type: Filter by sensor type ("camera" or "group").
        :return dict[str, list[str]]: Dictionary mapping sensor IDs to lists of ROI IDs.

        Examples::
            >>> usd_search = USDSearch("config.json")
            >>> sensor_map = usd_search.get_sensor_to_roi_map(sensor_type="camera")
            >>> print(f"Sensor to ROI map: {sensor_map}")
        """
        sensor_to_roi_map = {}
        for sensor_id, sensor_info in self.calibration.sensor_map.items():
            rois = sensor_info.rois
            roi_id_list = [roi.id for roi in rois]
            if not sensor_type:
                sensor_to_roi_map[sensor_id] = roi_id_list
            else:
                if sensor_info.type == sensor_type:
                    if sensor_type == "camera":
                        sensor_to_roi_map[sensor_id] = roi_id_list
                    if sensor_type == "group":
                        group_info = sensor_info.group
                        if group_info:
                            if "alias" in group_info:
                                area_id = group_info["alias"]
                                sensor_to_roi_map[area_id] = roi_id_list
                        else:
                            sensor_to_roi_map[sensor_id] = roi_id_list
        return sensor_to_roi_map


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        type=validate_file_path,
        default="configs/usd_search_config_sample.json",
        action=ValidateFile,
        help="The input app config file",
    )
    parser.add_argument(
        "--calibration",
        type=validate_file_path,
        default="configs/calibration_space_utilization.json",
        action=ValidateFile,
        help="The input app calibration file",
    )
    parser.add_argument(
        "--asset",
        type=validate_file_path,
        default="configs/asset_surf_warehouse.json",
        action=ValidateFile,
        help="The input asset json file",
    )

    args = parser.parse_args()
    # usd_search_tool = USDSearch(args.config)
    usd_search_tool = USDSearch(args.config, args.calibration)
    # usd_search_tool = USDSearch(args.config, args.calibration, args.asset)
    # usd_search_tool = USDSearch(args.config, asset_json_path=args.asset)

    pallet_ids = usd_search_tool.get_all_pallet_ids()
    print("pallet_ids:", pallet_ids)

    start = time.time()
    pallet_size = usd_search_tool.get_pallet_size(pallet_id="Pallet_A1")
    print("pallet_size:", pallet_size)
    end = time.time()
    pallet_query_time = round(end - start, 2)
    print(f"pallet_query_time: {pallet_query_time} sec")

    start = time.time()
    buffer_zone_dimensions = usd_search_tool.get_buffer_zone_dimensions(buffer_zone_id="buffer_zone_1")
    print("buffer_zone_dimensions:", buffer_zone_dimensions)
    end = time.time()
    buffer_zone_query_time = round(end - start, 2)
    print(f"buffer_zone_query_time: {buffer_zone_query_time} sec")

    start = time.time()
    best_camera = usd_search_tool.get_best_camera_on_buffer_zone(buffer_zone_id="buffer_zone_1")
    print("best camera on buffer zone:", best_camera)
    end = time.time()
    camera_query_time = round(end - start, 2)
    print(f"camera_query_time: {camera_query_time} sec")

    start = time.time()
    best_camera = usd_search_tool.get_best_camera_on_buffer_zones(buffer_zones=[])
    # best_camera = usd_search_tool.get_best_camera_on_buffer_zones(buffer_zones=['buffer_zone_1', 'buffer_zone_3'])
    print("best camera on buffer zones:", best_camera)
    end = time.time()
    camera_query_time = round(end - start, 2)
    print(f"camera_query_time: {camera_query_time} sec")

    # best_camera_list = usd_search_tool.get_best_camera_list_on_bev_list(bev_id_list=['bev-sensor-1'])
    best_camera_list = usd_search_tool.get_best_camera_list_on_bev_list(bev_id_list=[])
    print("best_camera_list:", best_camera_list)

    # proximity_data = load_json_from_file('configs/social_distancing_sample_data.txt')
    proximity_data = load_json_from_file("configs/frame_alerts_3d_sample_data.txt")
    best_camera = usd_search_tool.get_best_camera_on_proximity_data(proximity_data)
    print("best camera on proximity data:", best_camera)

    frame_alerts = load_json_from_file("configs/frame_alerts_3d_sample_data.txt")
    best_camera = usd_search_tool.get_best_camera_on_frame_alerts(frame_alerts)
    print("best camera on frame_alerts:", best_camera)

    ## generalize to multiple bevs
    print("================")
    print("multiple bev supporting functions:")
    buffer_zones_list, camera_id_list, bev_id_list, area_id_list = usd_search_tool.get_zone_camera_bev_on_buffer_zones(
        buffer_zones=[]
    )
    print("buffer_zones_list:", buffer_zones_list)
    print("camera_id_list:", camera_id_list)
    print("bev_id_list:", bev_id_list)
    print("area_id_list:", area_id_list)

    print("-----------------")
    bev_id_list, area_id_list = usd_search_tool.get_bev_on_areas(areas=[])
    print("bev_id_list:", bev_id_list)
    print("area_id_list:", area_id_list)

    print("-----------------")
    buffer_zones_list, camera_id_list, bev_id_list, area_id_list = usd_search_tool.get_zone_camera_bev_on_areas(
        areas=[]
    )
    print("buffer_zones_list:", buffer_zones_list)
    print("camera_id_list:", camera_id_list)
    print("bev_id_list:", bev_id_list)
    print("area_id_list:", area_id_list)

    ## ROI related
    print("-----------------")
    sensor_to_roi_map = usd_search_tool.get_sensor_to_roi_map(sensor_type=None)
    print("sensor_to_roi_map:", sensor_to_roi_map)
