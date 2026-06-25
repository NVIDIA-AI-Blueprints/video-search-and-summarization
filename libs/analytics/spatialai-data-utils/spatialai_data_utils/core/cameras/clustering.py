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
Camera Clustering Module

This module clusters cameras by field-of-view (FOV) overlap and spatial proximity.
Unlike grouping.py (which enumerates overlapping sets), this uses:
1) Greedy seeding/growth with overlap+distance thresholds
2) Unassigned handling (densify/balanced modes) with cascade or split logic

Key differences from grouping.py:
- Grouping: Finds all possible groups of a target size with sufficient overlap
- Clustering: Partitions ALL cameras into N clusters with minimal spatial scatter
"""

from __future__ import annotations
import concurrent.futures
import itertools
import logging
import math
import os
import sys
from pathlib import Path
from typing import List, Dict, Tuple, Optional, Set

import numpy as np

from spatialai_data_utils.core.cameras.group_utils import (
    _configure_logging,
    _grid_from_stats_or_default,
    _build_start_indices,
    _build_fine_grid,
    _run_single_config,
    _run_grid,
)
from spatialai_data_utils.core.cameras.polygon import (
    parse_polygon,
    find_field_of_view_polygon,
    calculate_camera_frustum_polygon,
)
from spatialai_data_utils.core.cameras.utils import extract_camera_matrices
from spatialai_data_utils.loaders.calibration import load_calib_json


logger = logging.getLogger(__name__)


class CameraClusterHelper:
    """
    Helper class with static methods for camera clustering operations.
    """

    @classmethod
    def compute_camera_fov_center(cls, poly) -> Tuple[float, float]:
        """
        Calculate a camera FOV's center point from its polygon.

        :param poly: Shapely Polygon representing the camera's FOV.
        :type poly: shapely.Polygon
        :return: (x, y) coordinates of the FOV center.
        :rtype: Tuple[float, float]
        """
        if poly is None or poly.is_empty:
            return (0.0, 0.0)

        centroid = poly.centroid
        return (centroid.x, centroid.y)

    @classmethod
    def compute_overlap(cls, polygon_1, polygon_2) -> float:
        """
        Compute overlap percentage between two FOV polygons.

        :param polygon_1: First FOV polygon.
        :type polygon_1: shapely.Polygon
        :param polygon_2: Second FOV polygon.
        :type polygon_2: shapely.Polygon
        :return: Overlap percentage (0-100).
        :rtype: float
        """
        if polygon_1 is None or polygon_2 is None:
            return 0.0
        if polygon_1.is_empty or polygon_2.is_empty:
            return 0.0

        try:
            V_i = polygon_1.area
            V_j = polygon_2.area
            intersection = polygon_1.intersection(polygon_2)
            V_ij = intersection.area if not intersection.is_empty else 0.0
            union_area = V_i + V_j - V_ij

            if union_area <= 0:
                return 0.0

            P_ij = (V_ij / union_area)

            return P_ij
        except Exception as e:
            logger.warning(f"Error computing overlap: {e}")
            return 0.0

    @classmethod
    def get_unioned_polygon(cls, polygon_list):
        """
        Get the union of multiple polygons.

        :param polygon_list: List of Shapely polygons.
        :type polygon_list: List[shapely.Polygon]
        :return: Union polygon.
        :rtype: Optional[shapely.Polygon]
        """
        from shapely.ops import unary_union

        valid_polygons = [p for p in polygon_list if p is not None and not p.is_empty]
        if not valid_polygons:
            return None

        return unary_union(valid_polygons)

    @classmethod
    def shortest_distance(cls, point_list, target_point) -> float:
        """
        Calculate the shortest distance between a target 2D point and a list of 2D points.

        :param point_list: List of (x, y) tuples or numpy array of shape (n_points, 2).
        :type point_list: List[Tuple[float, float]]
        :param target_point: Target (x, y) tuple.
        :type target_point: Tuple[float, float]
        :return: Minimum distance.
        :rtype: float
        """
        if not point_list:
            return float("inf")

        points = np.array(point_list)
        target = np.array(target_point)

        distances = np.linalg.norm(points - target, axis=1)
        min_distance = np.min(distances)
        return min_distance

    @classmethod
    def longest_distance_sum(cls, point_list) -> float:
        """
        Calculate average maximum distance within points in a cluster.

        :param point_list: List of (x, y) points.
        :type point_list: List[Tuple[float, float]]
        :return: Sum of longest distances from each point.
        :rtype: float
        """
        if not point_list:
            return 0.0

        max_distance_sum = 0
        for point in point_list:
            distance = cls.longest_distance(point_list=point_list, target_point=point)
            max_distance_sum += distance
        return max_distance_sum

    @classmethod
    def longest_distance(cls, point_list, target_point) -> float:
        """
        Calculate the longest distance between a target 2D point and a list of 2D points.

        :param point_list: List of (x, y) tuples.
        :type point_list: List[Tuple[float, float]]
        :param target_point: Target (x, y) tuple.
        :type target_point: Tuple[float, float]
        :return: Maximum distance.
        :rtype: float
        """
        if not point_list:
            return 0.0

        points = np.array(point_list)
        target = np.array(target_point)

        distances = np.linalg.norm(points - target, axis=1)
        max_distance = np.max(distances)
        return max_distance

    @classmethod
    def split_number(cls, N: int, K: int) -> List[int]:
        """
        Split number N into K parts as evenly as possible.

        :param N: Camera Number.
        :type N: int
        :param K: Cluster Number.
        :type K: int
        :return: List of K integers summing to N.
        :rtype: List[int]
        """
        if K <= 0:
            raise ValueError("K must be a positive integer.")
        if N < 0:
            raise ValueError("N must be a non-negative integer.")
        if K > N:
            # Allow more buckets than items by padding zeros.
            parts = [0] * K
            for i in range(N):
                parts[i] = 1
            return parts

        base = N // K
        remainder = N % K

        parts = [base] * K
        for i in range(remainder):
            parts[i] += 1

        return parts


class CameraFovInfo:
    """
    Container for camera FOV information used during clustering.
    """

    def __init__(self, camera_id: str, poly=None, center_point=None, category=None):
        """
        Initialize camera FOV info.

        :param camera_id: Unique camera identifier.
        :type camera_id: str
        :param poly: Shapely polygon representing FOV.
        :type poly: shapely.Polygon or None
        :param center_point: (x, y) center of FOV.
        :type center_point: Tuple[float, float] or None
        :param category: Cluster assignment (integer).
        :type category: int or None
        """
        self.camera_id = camera_id
        self.poly = poly
        self.center_point = center_point
        self.category = category

    def set_center_point(self, target_point):
        self.center_point = target_point

    def set_poly(self, polygon):
        self.poly = polygon

    def set_category(self, target_category):
        self.category = target_category

    def get_center_point(self):
        return self.center_point

    def get_poly(self):
        return self.poly

    def get_category(self):
        return self.category
    
    def get_camera_id(self):
        return self.camera_id


class CameraClusterManager:
    """
    Manager class for camera clustering operations.

    This class coordinates the clustering algorithm which partitions all cameras
    into a specified number of clusters, aiming to minimize spatial scatter within
    each cluster.
    """

    def __init__(self, sensors_data: List[dict]):
        """
        Initialize the cluster manager with sensor data.

        :param sensors_data: List of sensor dictionaries from calibration data.
        :type sensors_data: List[dict]
        """
        self.sensors_data = sensors_data
        self._camera_info_dict: Dict[int, CameraFovInfo] = {}
        self.clusters: Dict[int, List[int]] = {}

    def initialize_camera_info(
        self,
        use_frustum: bool = False,
        scene_bounds: Tuple[float, float, float, float] = None,
        max_camera_distance: float = 30.0,
        height_range: tuple = (1.0, 3.0),
        image_size: tuple = (1920, 1080),
    ):
        """
        Initialize camera info by extracting FOV polygons and computing centers.
        """
        if use_frustum:
            logger.info(
                "Initializing camera information for clustering using frustum..."
            )
        else:
            logger.info(
                "Initializing camera information for clustering using attributes..."
            )

        for idx, sensor in enumerate(self.sensors_data):
            poly = None
            camera_id = sensor.get("id")

            if not use_frustum:
                try:
                    poly_str = find_field_of_view_polygon(sensor["attributes"])
                    poly = parse_polygon(poly_str)
                except (ValueError, KeyError):
                    # fieldOfViewPolygon not found in attributes, will fall back to frustum
                    logger.warning(
                        f"Field of view polygon not found for sensor {sensor['id']}, will fall back to frustum calculation"
                    )
                    pass

            # Fall back to frustum calculation if FOV polygon not available or use_frustum is True
            if poly is None or use_frustum:
                intrinsic_matrix, extrinsic_matrix = extract_camera_matrices(sensor)

                # Check if camera matrices were extracted successfully
                if intrinsic_matrix is None or extrinsic_matrix is None:
                    logger.error(
                        f"Failed to extract camera matrices for sensor {sensor['id']}"
                    )
                    poly = None
                    sys.exit(1)
                else:
                    poly = calculate_camera_frustum_polygon(
                        intrinsic_matrix,
                        extrinsic_matrix,
                        height_range=height_range,
                        image_size=image_size,
                        scene_bounds=scene_bounds,
                        max_distance=max_camera_distance,
                    )

            # Compute center point
            center_point = CameraClusterHelper.compute_camera_fov_center(poly)

            # Store camera info
            self._camera_info_dict[idx] = CameraFovInfo(
                camera_id=camera_id, poly=poly, center_point=center_point, category=None
            )

        valid_count = sum(
            1
            for info in self._camera_info_dict.values()
            if info.poly is not None and not info.poly.is_empty
        )
        logger.info(
            f"Initialized {len(self._camera_info_dict)} cameras "
            f"({valid_count} with valid FOV polygons)"
        )

    def all_categorized(self) -> bool:
        """Check if all cameras have been assigned to clusters."""
        for camera_info in self._camera_info_dict.values():
            if camera_info.get_category() is None:
                return False
        return True

    def get_camera_num(self) -> int:
        """Get total number of cameras."""
        return len(self._camera_info_dict)

    def get_uncategorized_camera_indices(self) -> List[int]:
        """Get list of camera indices without cluster assignment."""
        result = []
        for idx, camera_info in self._camera_info_dict.items():
            if camera_info.get_category() is None:
                result.append(idx)
        return result

    def categorize_camera(self, camera_idx: int, category: int):
        """Assign a camera to a cluster."""
        camera_info = self._camera_info_dict[camera_idx]
        camera_info.set_category(category)

    def get_camera_polygon(self, camera_idx: int):
        """Get camera's FOV polygon."""
        camera_info = self._camera_info_dict[camera_idx]
        return camera_info.get_poly()

    def get_camera_center_point(self, camera_idx: int):
        """Get camera's FOV center point."""
        camera_info = self._camera_info_dict[camera_idx]
        return camera_info.get_center_point()

    def get_union_polygon(self, camera_idx_list: List[int]):
        """Get the union of FOV polygons for a list of cameras."""
        polygon_list = []
        for camera_idx in camera_idx_list:
            polygon = self.get_camera_polygon(camera_idx)
            if polygon is not None:
                polygon_list.append(polygon)

        return CameraClusterHelper.get_unioned_polygon(polygon_list)

    def get_max_overlap_camera(
        self, target_camera_idx_list: List[int]
    ) -> Tuple[float, int]:
        """
        Find uncategorized camera with maximum FOV overlap to the target group.

        :param target_camera_idx_list: List of camera indices in the target group.
        :type target_camera_idx_list: List[int]
        :return: (max_overlap, camera_index) tuple.
        :rtype: Tuple[float, int]
        """
        max_overlap = 0
        uncategorized = self.get_uncategorized_camera_indices()

        if not uncategorized:
            return 0, None

        max_overlap_camera = uncategorized[0]
        union_polygon = self.get_union_polygon(target_camera_idx_list)

        for camera_idx in uncategorized:
            camera_polygon = self.get_camera_polygon(camera_idx)
            overlap = CameraClusterHelper.compute_overlap(union_polygon, camera_polygon)

            if overlap > max_overlap:
                max_overlap = overlap
                max_overlap_camera = camera_idx

        return max_overlap, max_overlap_camera

    def get_min_overlap_camera(
        self, target_camera_idx_list: List[int]
    ) -> Tuple[float, List[int]]:
        """
        Find uncategorized camera with minimum FOV overlap to the target group.

        :param target_camera_idx_list: List of camera indices in the target group.
        :type target_camera_idx_list: List[int]
        :return: (min_overlap, candidate_idx_list) tuple.
        :rtype: Tuple[float, List[int]]
        """
        min_overlap = float("inf")
        uncategorized = self.get_uncategorized_camera_indices()

        if not uncategorized:
            return min_overlap, []

        union_polygon = self.get_union_polygon(target_camera_idx_list)

        min_candidates = []
        for camera_idx in uncategorized:
            camera_polygon = self.get_camera_polygon(camera_idx)
            overlap = CameraClusterHelper.compute_overlap(union_polygon, camera_polygon)

            if overlap < min_overlap:
                min_overlap = overlap
                min_candidates = [camera_idx]
            elif overlap == min_overlap:
                min_candidates.append(camera_idx)

        return min_overlap, min_candidates

    def get_closest_center_camera(self, target_camera_idx_list: List[int]) -> int:
        """
        Find uncategorized camera with center point closest to the target group.

        :param target_camera_idx_list: List of camera indices in the target group.
        :type target_camera_idx_list: List[int]
        :return: Camera index.
        :rtype: int
        """
        min_distance = float("inf")
        uncategorized = self.get_uncategorized_camera_indices()

        if not uncategorized:
            return None

        candidate = uncategorized[0]
        camera_center_list = [
            self.get_camera_center_point(idx) for idx in target_camera_idx_list
        ]

        for camera_idx in uncategorized:
            camera_center = self.get_camera_center_point(camera_idx)
            distance = CameraClusterHelper.shortest_distance(
                target_point=camera_center, point_list=camera_center_list
            )

            if distance < min_distance:
                min_distance = distance
                candidate = camera_idx

        return candidate

    def get_furthest_center_camera(self, target_camera_idx_list: List[int]) -> int:
        """
        Find uncategorized camera with center point furthest from the target group.

        :param target_camera_idx_list: List of camera indices in the target group.
        :type target_camera_idx_list: List[int]
        :return: Camera index.
        :rtype: int
        """
        max_distance = -1
        uncategorized = self.get_uncategorized_camera_indices()

        if not uncategorized:
            return None

        candidate = uncategorized[0]
        camera_center_list = [
            self.get_camera_center_point(idx) for idx in target_camera_idx_list
        ]

        for camera_idx in uncategorized:
            camera_center = self.get_camera_center_point(camera_idx)
            distance = CameraClusterHelper.shortest_distance(
                target_point=camera_center, point_list=camera_center_list
            )

            if distance > max_distance:
                max_distance = distance
                candidate = camera_idx

        return candidate

    def pick_closest_camera(
        self,
        target_camera_idx_list: List[int],
        overlap_threshold: float = 0.0,
        distance_threshold: float = float("inf"),
    ) -> Optional[int]:
        """
        Pick the closest camera toward the target camera group that satisfies thresholds.

        :param target_camera_idx_list: List of camera indices in target group.
        :type target_camera_idx_list: List[int]
        :param overlap_threshold: Minimum overlap percentage required.
        :type overlap_threshold: float
        :param distance_threshold: Maximum centroid distance allowed.
        :type distance_threshold: float
        :return: Selected camera index or None if no candidate passes thresholds.
        :rtype: Optional[int]
        """
        if not target_camera_idx_list:
            return None

        candidates = []
        uncategorized = self.get_uncategorized_camera_indices()
        for camera_idx in uncategorized:
            is_valid, overlap, distance = self._evaluate_membership_scores(
                camera_idx,
                target_camera_idx_list,
                overlap_threshold,
                distance_threshold,
            )
            if not is_valid:
                continue
            candidates.append((overlap, distance, camera_idx))

        if not candidates:
            return None

        candidates.sort(key=lambda item: (-item[0], item[1]))
        return candidates[0][2]

    def pick_furthest_camera(self, target_camera_idx_list: List[int]) -> int:
        """
        Pick the furthest camera from the target camera group.

        Prioritizes cameras with no FOV overlap, then falls back to spatial distance.

        :param target_camera_idx_list: List of camera indices in target group.
        :type target_camera_idx_list: List[int]
        :return: Selected camera index.
        :rtype: int
        """
        min_overlap, candidates = self.get_min_overlap_camera(target_camera_idx_list)
        if not candidates:
            return None

        # Choose furthest (by center) among the min-overlap candidates
        target_centers = [
            self.get_camera_center_point(idx) for idx in target_camera_idx_list
        ]
        max_dist = -1.0
        furthest_idx = candidates[0]
        for camera_idx in candidates:
            center = self.get_camera_center_point(camera_idx)
            dist = CameraClusterHelper.shortest_distance(
                point_list=target_centers, target_point=center
            )
            if dist > max_dist:
                max_dist = dist
                furthest_idx = camera_idx

        return furthest_idx

    def _evaluate_membership_scores(
        self,
        camera_idx: int,
        target_camera_idx_list: List[int],
        overlap_threshold: float,
        distance_threshold: float,
    ) -> Tuple[bool, float, float]:
        """
        Evaluate whether a camera satisfies overlap/distance thresholds for a target cluster.

        :param camera_idx: Candidate camera index.
        :type camera_idx: int
        :param target_camera_idx_list: Cameras already in the cluster.
        :type target_camera_idx_list: List[int]
        :param overlap_threshold: Minimum required overlap (0-100).
        :type overlap_threshold: float
        :param distance_threshold: Maximum centroid distance.
        :type distance_threshold: float
        :return: (is_valid, overlap, distance)
        :rtype: Tuple[bool, float, float]
        """
        if not target_camera_idx_list:
            # No reference members: treat as valid to avoid spurious reassignments
            return True, 0.0, 0.0

        union_polygon = self.get_union_polygon(target_camera_idx_list)
        camera_polygon = self.get_camera_polygon(camera_idx)
        overlap = CameraClusterHelper.compute_overlap(union_polygon, camera_polygon)

        cluster_centers = [
            self.get_camera_center_point(idx) for idx in target_camera_idx_list
        ]
        camera_center = self.get_camera_center_point(camera_idx)
        distance = CameraClusterHelper.shortest_distance(
            point_list=cluster_centers, target_point=camera_center
        )
        
        overlap_ok = overlap_threshold is None or overlap >= overlap_threshold
        distance_ok = distance_threshold is None or distance <= distance_threshold
        if math.isinf(distance_threshold):
            distance_ok = True

        return overlap_ok and distance_ok, overlap, distance

    def generate_cluster_assignments(self) -> List[int]:
        """
        Generate cluster assignment list aligned with sensor indices.

        :return: List where index i contains the cluster ID for sensor i.
        :rtype: List[int]
        """
        num_cameras = len(self.sensors_data)
        assignments = [None] * num_cameras

        # Reorder clusters by size (desc) and remap ids so id 0 is largest
        sorted_clusters = sorted(
            self.clusters.items(), key=lambda item: len(item[1]), reverse=True
        )
        remap = {old_id: new_id for new_id, (old_id, _) in enumerate(sorted_clusters)}

        # Update internal clusters to the new id ordering
        self.clusters = {remap[old_id]: idxs for old_id, idxs in sorted_clusters}

        # Build assignments using remapped ids
        for cluster_id, camera_indices in self.clusters.items():
            for camera_idx in camera_indices:
                assignments[camera_idx] = cluster_id

        return assignments

    def get_cluster_num(self) -> int:
        """Get number of clusters."""
        return len(self.clusters.keys())

    def evaluate_point_scatter(self, camera_idx_list: List[int]) -> float:
        """
        Evaluate spatial scatter (compactness) of a cluster.

        Lower values indicate more compact clusters.

        :param camera_idx_list: List of camera indices in the cluster.
        :type camera_idx_list: List[int]
        :return: Scatter metric (average longest distance).
        :rtype: float
        """
        if not camera_idx_list:
            return 0.0

        point_list = [self.get_camera_center_point(idx) for idx in camera_idx_list]
        value = CameraClusterHelper.longest_distance_sum(point_list) / len(point_list)
        return value

    def seed_clusters_and_get_unassigned_cameras(
        self,
        n_clusters,
        start_camera_index: int = 0,
        overlap_threshold: float = 0.0,
        distance_threshold: float = float("inf"),
    ) -> List[int]:
        """
        Seed initial clusters and return any cameras that could not be placed.

        Builds clusters iteratively by adding cameras with maximum overlap/proximity
        to the current cluster, then starting new clusters with cameras furthest from
        existing clusters.

        :param n_clusters: Number of clusters to create.
        :type n_clusters: int
        :param start_camera_index: Index of first camera to seed clustering.
        :type start_camera_index: int
        """
        logger.info(f"Seeding {n_clusters} clusters (overlap/distance guided)...")

        # Clear existing clusters
        self.clusters = {}

        # Reset categories
        for camera_info in self._camera_info_dict.values():
            camera_info.set_category(None)

        # Get total camera count and split evenly
        camera_num = self.get_camera_num()
        cluster_length_list = CameraClusterHelper.split_number(camera_num, n_clusters)

        logger.info(f"Initialized cluster sizes: {cluster_length_list}")

        # Initialize cluster storage
        cluster_index = 0
        clusters: Dict[int, List[int]] = {}
        for i in range(len(cluster_length_list)):
            clusters[i] = []

        # Seed first cluster with starting camera
        first_camera = start_camera_index
        clusters[cluster_index].append(first_camera)
        self.categorize_camera(camera_idx=first_camera, category=cluster_index)

        # Iteratively build clusters
        while not self.all_categorized() and cluster_index < len(cluster_length_list):
            # Fill current cluster to target size
            while (
                len(clusters[cluster_index]) < cluster_length_list[cluster_index]
                and not self.all_categorized()
            ):
                cluster_cameras = clusters[cluster_index]
                camera_idx = self.pick_closest_camera(
                    cluster_cameras,
                    overlap_threshold=overlap_threshold,
                    distance_threshold=distance_threshold,
                )
                
                if camera_idx is None:
                    # No candidate meets thresholds for this cluster
                    break

                cluster_cameras.append(camera_idx)
                self.categorize_camera(camera_idx=camera_idx, category=cluster_index)
                logger.info(
                    f"Init assign: {self._camera_info_dict[camera_idx].get_camera_id()} -> cluster {cluster_index + 1} (size {len(cluster_cameras)})"
                )
            
            # Start new cluster with furthest camera that exists
            if not self.all_categorized():
                if cluster_index + 1 >= len(cluster_length_list):
                    # No more cluster slots; exit loop and mark remaining as unassigned
                    break
                cluster_cameras = clusters[cluster_index]
                temp_idx = self.pick_furthest_camera(cluster_cameras)

                if temp_idx is None:
                    # No more candidates
                    break

                cluster_index += 1
                clusters[cluster_index].append(temp_idx)
                self.categorize_camera(camera_idx=temp_idx, category=cluster_index)
                logger.info(
                    f"Init seed new cluster {cluster_index + 1} with {self._camera_info_dict[temp_idx].get_camera_id()} (size 1)"
                )

        self.clusters = clusters

        # Collect unassigned cameras
        unassigned_indices = self.get_uncategorized_camera_indices()

        # Log cluster sizes summary
        cluster_sizes = [len(cameras) for cameras in self.clusters.values()]
        logger.info(f"Cluster sizes: {cluster_sizes}")

        return unassigned_indices

    def cluster_cameras(
        self,
        n_clusters,
        start_camera_index: int = 0,
        use_frustum: bool = False,
        scene_bounds: Tuple[float, float, float, float] = None,
        max_camera_distance: float = 30.0,
        height_range: tuple = (1.0, 3.0),
        image_size: tuple = (1920, 1080),
        mode: str = "densify",
        overlap_threshold: float = 0.0,
        distance_threshold: float = float("inf"),
        max_cluster_size: Optional[int] = None,
        max_cascade_depth: int = 3,
        enable_unassigned_processing: bool = True,
        global_stats: Optional[Dict[str, Optional[float]]] = None,
        warn_thresholds: bool = True,
    ) -> List[int]:
        """
        Main clustering method: partition cameras into N clusters.

        Uses greedy initialization followed by iterative refinement to create
        spatially compact camera clusters.

        :param n_clusters: Number of clusters to create.
        :type n_clusters: int
        :param start_camera_index: Index of starting camera for seeding.
        :type start_camera_index: int
        :param use_frustum: Whether to synthesize FOV polygons from frustums.
        :type use_frustum: bool
        :param mode: 'balanced' (threshold-balanced) or 'densify' (maximize cluster fill).
        :type mode: str
        :param overlap_threshold: Minimum overlap percentage (0-100) for membership.
        :type overlap_threshold: float
        :param distance_threshold: Maximum centroid distance allowed for membership.
        :type distance_threshold: float
        :param max_cluster_size: Maximum desired cluster size.
        :type max_cluster_size: int or None
        :param max_cascade_depth: Max recursion depth for cascade reassignment.
        :type max_cascade_depth: int
        :return: List of cluster assignments (cluster ID for each camera).
        :rtype: List[int]
        """
    
        mode_lower = mode.lower()
        if mode_lower == "balanced":
            mode_lower = "balanced"
        elif mode_lower == "densify":
            mode_lower = "densify"
        else:
            logger.error(f"Unknown clustering mode '{mode}', falling back to 'balanced'")
            sys.exit(1)

        if max_cluster_size is None or max_cluster_size <= 0:
            logger.error(
                f"max_cluster_size must be greater than 0"
            )
            sys.exit(1)

        # Initialize camera info
        self.initialize_camera_info(
            use_frustum=use_frustum,
            scene_bounds=scene_bounds,
            max_camera_distance=max_camera_distance,
            height_range=height_range,
            image_size=image_size,
        )

        # Log global stats: prefer caller-provided stats; only warn when computed here
        stats = global_stats if global_stats is not None else self.compute_global_stats()
        log_global_stats(
            stats,
            overlap_threshold,
            distance_threshold,
            warn=warn_thresholds if global_stats is None else False,
        )

        # Initial seeding
        unassigned_indices = self.seed_clusters_and_get_unassigned_cameras(
            n_clusters,
            start_camera_index=start_camera_index,
            overlap_threshold=overlap_threshold,
            distance_threshold=distance_threshold,
        )
        
        # Perform unassigned handling and overflow management
        if enable_unassigned_processing:
            self._process_unassigned_cameras(
                mode=mode_lower,
                overlap_threshold=overlap_threshold,
                distance_threshold=distance_threshold,
                max_cluster_size=max_cluster_size,
                max_cascade_depth=max_cascade_depth,
                start_camera_index=start_camera_index,
                use_frustum=use_frustum,
                scene_bounds=scene_bounds,
                max_camera_distance=max_camera_distance,
                height_range=height_range,
                image_size=image_size,
                initial_unassigned_indices=unassigned_indices,
            )

        # Generate assignment list
        assignments = self.generate_cluster_assignments()

        logger.info(
            f"Clustering complete: {len(self.clusters)} clusters created (mode={mode_lower})"
        )
        return assignments

    def _process_unassigned_cameras(
        self,
        mode: str,
        overlap_threshold: float,
        distance_threshold: float,
        max_cluster_size: int,
        max_cascade_depth: int,
        start_camera_index: int,
        use_frustum: bool,
        scene_bounds: Optional[Tuple[float, float, float, float]],
        max_camera_distance: float,
        height_range: tuple,
        image_size: tuple,
        initial_unassigned_indices: Optional[List[int]] = None,
    ):
        """
        Handle unassigned cameras after initial clustering.
        """
        if initial_unassigned_indices is None:
            unassigned_indices = self._identify_unassigned_candidates(
                overlap_threshold=overlap_threshold,
                distance_threshold=distance_threshold,
                max_cluster_size=max_cluster_size,
            )
        else:
            unassigned_indices = initial_unassigned_indices

        if not unassigned_indices:
            logger.info("No unassigned cameras detected")
            return

        unassigned_camera_ids = [self._camera_info_dict[idx].get_camera_id() for idx in unassigned_indices]
        logger.info(f"Detected {len(unassigned_indices)} unassigned cameras requiring post-processing: {unassigned_camera_ids}")

        # Assign unassigned cameras to clusters
        if mode == "densify":
            self._assign_unassigned_cameras_densify(
                unassigned_indices=unassigned_indices,
                overlap_threshold=overlap_threshold,
                distance_threshold=distance_threshold,
                max_cluster_size=max_cluster_size,
                max_cascade_depth=max_cascade_depth,
            )
        else:
            overflow_clusters = self._assign_unassigned_cameras_balanced(
                unassigned_indices=unassigned_indices,
                overlap_threshold=overlap_threshold,
                distance_threshold=distance_threshold,
                max_cluster_size=max_cluster_size,
            )
            if overflow_clusters:
                self._split_overflow_clusters(
                    overflow_clusters=overflow_clusters,
                    overlap_threshold=overlap_threshold,
                    distance_threshold=distance_threshold,
                    max_cluster_size=max_cluster_size,
                    start_camera_index=start_camera_index,
                    use_frustum=use_frustum,
                    scene_bounds=scene_bounds,
                    max_camera_distance=max_camera_distance,
                    height_range=height_range,
                    image_size=image_size,
                )

    def _identify_unassigned_candidates(
        self,
        overlap_threshold: float,
        distance_threshold: float,
        max_cluster_size: int,
    ) -> List[Dict]:
        """
        Find cameras that do NOT satisfy thresholds for their current cluster.
        """
        unassigned_indices = []
        for camera_idx, info in self._camera_info_dict.items():
            current_cluster = info.get_category()
            if current_cluster is None:
                continue

            # Check if current membership meets thresholds
            current_members = [
                idx for idx in self.clusters.get(current_cluster, []) if idx != camera_idx
            ]
            is_valid_here, overlap, distance = self._evaluate_membership_scores(
                camera_idx,
                current_members if current_members else [],
                overlap_threshold,
                distance_threshold,
            )

            if not is_valid_here:
                unassigned_indices.append(camera_idx)

        return unassigned_indices

    def _find_best_cluster_for_camera(
        self,
        camera_idx: int,
        overlap_threshold: float,
        distance_threshold: float,
        exclude_cluster: Optional[int] = None,
        require_capacity: bool = False,
        max_cluster_size: Optional[int] = None,
    ) -> Tuple[Optional[int], float, float]:
        """
        Determine the best cluster for the specified camera respecting thresholds.
        """
        best_cluster = None
        best_overlap = -1.0
        best_distance = float("inf")

        for cluster_id, camera_indices in self.clusters.items():
            if exclude_cluster is not None and cluster_id == exclude_cluster:
                continue
            if require_capacity and max_cluster_size is not None:
                if len(camera_indices) >= max_cluster_size:
                    continue

            is_valid, overlap, distance = self._evaluate_membership_scores(
                camera_idx,
                camera_indices,
                overlap_threshold,
                distance_threshold,
            )

            if not is_valid:
                continue
            
            if overlap > best_overlap or (
                overlap == best_overlap and distance < best_distance
            ):
                best_cluster = cluster_id
                best_overlap = overlap
                best_distance = distance

        return best_cluster, best_overlap, best_distance

    def _assign_unassigned_cameras_balanced(
        self,
        unassigned_indices: List[int],
        overlap_threshold: float,
        distance_threshold: float,
        max_cluster_size: int,
    ) -> Set[int]:
        """
        Batch assignment (AND thresholds): try all, then singleton fallback, then overflow check.
        """
        overflow_clusters: Set[int] = set()
        remaining = list(unassigned_indices)
        while remaining:
            remaining_list = []
            progress = False
            for camera_idx in remaining:
                best_cluster, _, _ = self._find_best_cluster_for_camera(
                    camera_idx,
                    overlap_threshold=overlap_threshold,
                    distance_threshold=distance_threshold,
                    exclude_cluster=None,
                    require_capacity=False,
                    max_cluster_size=max_cluster_size,
                )

                if best_cluster is not None:
                    self._move_camera_to_cluster(camera_idx, best_cluster)
                    logger.info(
                        f"Balanced unassigned: {self._camera_info_dict[camera_idx].get_camera_id()} -> cluster {best_cluster + 1} (size {len(self.clusters[best_cluster])})"
                    )
                    progress = True
                    continue

                remaining_list.append(camera_idx)

            if not progress and remaining_list:
                camera_idx = remaining_list.pop(0)
                self._create_singleton_cluster(camera_idx)
                logger.info(
                    f"Balanced mode: no valid target for camera {self._camera_info_dict[camera_idx].get_camera_id()}, created singleton cluster {self._camera_info_dict[camera_idx].get_category() + 1}"
                )

            remaining = remaining_list

        # After all inserts, compute overflow once
        for cid, members in self.clusters.items():
            if len(members) > max_cluster_size:
                overflow_clusters.add(cid)

        return overflow_clusters

    def _split_overflow_clusters(
        self,
        overflow_clusters: Set[int],
        overlap_threshold: float,
        distance_threshold: float,
        max_cluster_size: int,
        start_camera_index: int,
        use_frustum: bool,
        scene_bounds: Optional[Tuple[float, float, float, float]],
        max_camera_distance: float,
        height_range: tuple,
        image_size: tuple,
        max_cascade_depth: int = 3,
    ):
        """
        Split oversized clusters using localized reclustering.
        """
        if not overflow_clusters:
            return

        logger.info(
            f"Balanced mode: splitting {len(overflow_clusters)} overflow cluster(s)"
        )

        next_cluster_id = max(self.clusters.keys(), default=-1) + 1

        for cluster_id in sorted(overflow_clusters):
            camera_indices = self.clusters.get(cluster_id, [])
            if len(camera_indices) <= max_cluster_size:
                continue

            subset_sensors = [self.sensors_data[idx] for idx in camera_indices]
            subset_manager = CameraClusterManager(subset_sensors)

            subset_manager.cluster_cameras(
                n_clusters=math.ceil(len(camera_indices) / max(1, max_cluster_size)),
                start_camera_index=0,
                use_frustum=use_frustum,
                scene_bounds=scene_bounds,
                max_camera_distance=max_camera_distance,
                height_range=height_range,
                image_size=image_size,
                mode="densify",
                overlap_threshold=overlap_threshold,
                distance_threshold=distance_threshold,
                max_cluster_size=max_cluster_size,
                max_cascade_depth=max_cascade_depth,
                enable_unassigned_processing=False,
                warn_thresholds=False,
            )

            new_clusters = {}
            for local_cluster_id, members in subset_manager.clusters.items():
                if not members:
                    continue
                global_indices = [camera_indices[i] for i in members]
                new_clusters[local_cluster_id] = global_indices

            if not new_clusters:
                continue

            # Replace original cluster with first new cluster, append others
            first = True
            for _, member_list in sorted(new_clusters.items()):
                if first:
                    self.clusters[cluster_id] = member_list
                    for idx in member_list:
                        self.categorize_camera(idx, cluster_id)
                    ids = [self._camera_info_dict[idx].get_camera_id() for idx in member_list]
                    logger.info(
                        f"Balanced split: cluster {cluster_id + 1} rebuilt with {len(member_list)} cameras - {ids}"
                    )
                    first = False
                else:
                    new_cluster_id = next_cluster_id
                    next_cluster_id += 1
                    self.clusters[new_cluster_id] = member_list
                    for idx in member_list:
                        self.categorize_camera(idx, new_cluster_id)
                    logger.info(
                        f"Balanced mode: created new cluster {new_cluster_id} via split of cluster {cluster_id}"
                    )
                    ids = [self._camera_info_dict[idx].get_camera_id() for idx in member_list]
                    logger.info(
                        f"Balanced split: cluster {new_cluster_id + 1} created with {len(member_list)} cameras - {ids}"
                    )

        logger.info(
            f"Balanced mode: overflow splitting complete, total clusters={len(self.clusters)}"
        )

    def _assign_unassigned_cameras_densify(
        self,
        unassigned_indices: List[int],
        overlap_threshold: float,
        distance_threshold: float,
        max_cluster_size: int,
        max_cascade_depth: int = 3,
    ):
        """
        Densify mode assignment with capacity awareness and cascade fallback (AND thresholds).
        """

        remaining = list(unassigned_indices)

        while remaining:
            remaining_list = []
            progress = False
            for camera_idx in remaining:
                # 1) Prefer non-full clusters that satisfy thresholds
                best_cluster, overlap, distance = self._find_best_cluster_for_camera(
                    camera_idx,
                    overlap_threshold=overlap_threshold,
                    distance_threshold=distance_threshold,
                    exclude_cluster=None,
                    require_capacity=True,
                    max_cluster_size=max_cluster_size,
                )
                    
                if best_cluster is not None:
                    self._move_camera_to_cluster(camera_idx, best_cluster)
                    logger.info(
                        f"Densify unassigned: {self._camera_info_dict[camera_idx].get_camera_id()} -> cluster {best_cluster + 1} (size {len(self.clusters[best_cluster])})"
                    )
                    progress = True
                    continue

                # 2) Try full clusters via cascade
                full_target, _, _ = self._find_best_cluster_for_camera(
                    camera_idx,
                    overlap_threshold=overlap_threshold,
                    distance_threshold=distance_threshold,
                    exclude_cluster=None,
                    require_capacity=False,
                    max_cluster_size=max_cluster_size,
                )

                if full_target is not None:
                    success = self._attempt_cascade_assignment(
                        camera_idx=camera_idx,
                        target_cluster=full_target,
                        overlap_threshold=overlap_threshold,
                        distance_threshold=distance_threshold,
                        max_cluster_size=max_cluster_size,
                        max_cascade_depth=max_cascade_depth,
                        current_depth=0,
                        visited=None,
                    )
                    if success:
                        logger.info(
                            f"Densify cascade: placed {self._camera_info_dict[camera_idx].get_camera_id()} into cluster {full_target + 1}"
                        )
                        progress = True
                        continue

                # 3) Keep for next pass / fallback
                remaining_list.append(camera_idx)

            # If no progress, force singleton for the first remaining to unblock
            if not progress and remaining_list:
                camera_idx = remaining_list.pop(0)
                self._create_singleton_cluster(camera_idx)
                logger.info(
                    f"Densify mode: no valid target for camera {self._camera_info_dict[camera_idx].get_camera_id()}, created singleton cluster {self._camera_info_dict[camera_idx].get_category() + 1}"
                )

            remaining = remaining_list

    def _attempt_cascade_assignment(
        self,
        camera_idx: int,
        target_cluster: int,
        overlap_threshold: float,
        distance_threshold: float,
        max_cluster_size: int,
        max_cascade_depth: int = 3,
        current_depth: int = 0,
        visited: Optional[Set[Tuple[int, int]]] = None,
    ) -> bool:
        """
        Recursive helper for cascade reassignment (densify mode).
        """
        if visited is None:
            visited = set()

        key = (camera_idx, target_cluster)
        if key in visited:
            return False
        visited.add(key)

        if target_cluster not in self.clusters:
            return False

        cluster_members = self.clusters[target_cluster]
        if camera_idx in cluster_members:
            return True

        if len(cluster_members) < max_cluster_size:
            self._move_camera_to_cluster(camera_idx, target_cluster)
            logger.info(
                f"Densify cascade: placed {self._camera_info_dict[camera_idx].get_camera_id()} into cluster {target_cluster + 1}"
            )
            return True

        if current_depth >= max_cascade_depth:
            return False

        eviction_candidates = self._rank_eviction_candidates(
            cluster_id=target_cluster,
            overlap_threshold=overlap_threshold,
            distance_threshold=distance_threshold,
            exclude_camera=camera_idx,
        )

        for candidate_idx in eviction_candidates:
            destination_order = self._rank_destination_clusters(
                candidate_idx=candidate_idx,
                overlap_threshold=overlap_threshold,
                distance_threshold=distance_threshold,
                max_cluster_size=max_cluster_size,
                current_cluster=target_cluster,
            )

            for dest_cluster in destination_order:
                next_depth = (
                    current_depth
                    + (1 if len(self.clusters[dest_cluster]) >= max_cluster_size else 0)
                )
                if next_depth > max_cascade_depth:
                    continue

                success = self._attempt_cascade_assignment(
                    camera_idx=candidate_idx,
                    target_cluster=dest_cluster,
                    overlap_threshold=overlap_threshold,
                    distance_threshold=distance_threshold,
                    max_cluster_size=max_cluster_size,
                    max_cascade_depth=max_cascade_depth,
                    current_depth=next_depth,
                    visited=visited
                )

                if success:
                    self._move_camera_to_cluster(camera_idx, target_cluster)
                    return True

        return False

    def _rank_eviction_candidates(
        self,
        cluster_id: int,
        overlap_threshold: float,
        distance_threshold: float,
        exclude_camera: Optional[int] = None,
    ) -> List[int]:
        """
        Order potential eviction candidates based on how well they can fit elsewhere.
        """
        candidates = []
        for camera_idx in self.clusters.get(cluster_id, []):
            if camera_idx == exclude_camera:
                continue
            best_cluster, overlap, distance = self._find_best_cluster_for_camera(
                camera_idx,
                overlap_threshold=overlap_threshold,
                distance_threshold=distance_threshold,
                exclude_cluster=cluster_id,
                require_capacity=False,
                max_cluster_size=None,
            )
            if best_cluster is None:
                continue
            candidates.append((overlap, distance, camera_idx))

        candidates.sort(key=lambda item: (-item[0], item[1]))
        return [camera_idx for _, _, camera_idx in candidates]

    def _rank_destination_clusters(
        self,
        candidate_idx: int,
        overlap_threshold: float,
        distance_threshold: float,
        max_cluster_size: int,
        current_cluster: int,
    ) -> List[int]:
        """
        Order potential destination clusters for a candidate camera.
        """
        not_full = []
        full = []

        for cluster_id, members in self.clusters.items():
            if cluster_id == current_cluster:
                continue
            is_valid, overlap, distance = self._evaluate_membership_scores(
                candidate_idx, members, overlap_threshold, distance_threshold
            )
            if not is_valid:
                continue
            entry = (cluster_id, overlap, distance)
            if len(members) < max_cluster_size:
                not_full.append(entry)
            else:
                full.append(entry)

        not_full.sort(key=lambda item: (-item[1], item[2]))
        full.sort(key=lambda item: (-item[1], item[2]))

        return [cluster_id for cluster_id, _, _ in not_full + full]

    def _move_camera_to_cluster(self, camera_idx: int, target_cluster: int):
        """
        Move a camera to the target cluster, updating bookkeeping.
        """
        current_cluster = self._camera_info_dict[camera_idx].get_category()
        if current_cluster == target_cluster:
            return

        if current_cluster is not None and current_cluster in self.clusters:
            if camera_idx in self.clusters[current_cluster]:
                self.clusters[current_cluster].remove(camera_idx)

        if target_cluster not in self.clusters:
            self.clusters[target_cluster] = []

        if camera_idx not in self.clusters[target_cluster]:
            self.clusters[target_cluster].append(camera_idx)

        self.categorize_camera(camera_idx=camera_idx, category=target_cluster)

    def _create_singleton_cluster(self, camera_idx: int):
        """
        Create a new singleton cluster for the specified camera.
        """
        current_cluster = self._camera_info_dict[camera_idx].get_category()
        if current_cluster is not None and current_cluster in self.clusters:
            if camera_idx in self.clusters[current_cluster]:
                self.clusters[current_cluster].remove(camera_idx)

        new_cluster_id = max(self.clusters.keys(), default=-1) + 1
        self.clusters[new_cluster_id] = [camera_idx]
        self.categorize_camera(camera_idx=camera_idx, category=new_cluster_id)

    def compute_global_stats(self) -> Dict[str, Optional[float]]:
        """
        Compute global min/max overlap and distance across all camera pairs.

        Returns dict keys:
          - distance_min, distance_max (meters)
          - overlap_min, overlap_max (ratio 0-1)
        """
        centers = [info.get_center_point() for info in self._camera_info_dict.values()]
        polys = [info.get_poly() for info in self._camera_info_dict.values()]

        min_dist = float("inf")
        max_dist = 0.0
        dist_found = False

        min_overlap = float("inf")
        max_overlap = 0.0
        overlap_found = False

        for i in range(len(centers)):
            for j in range(i + 1, len(centers)):
                if centers[i] is not None and centers[j] is not None:
                    d = CameraClusterHelper.shortest_distance(
                        [centers[i]], centers[j]
                    )
                    min_dist = min(min_dist, d)
                    max_dist = max(max_dist, d)
                    dist_found = True
                if polys[i] is not None and polys[j] is not None:
                    o = CameraClusterHelper.compute_overlap(polys[i], polys[j])
                    min_overlap = min(min_overlap, o)
                    max_overlap = max(max_overlap, o)
                    overlap_found = True

        return {
            "distance_min": min_dist if dist_found else None,
            "distance_max": max_dist if dist_found else None,
            "overlap_min": min_overlap if overlap_found else None,
            "overlap_max": max_overlap if overlap_found else None,
        }


def log_global_stats(
    stats: Dict[str, Optional[float]],
    overlap_threshold: float,
    distance_threshold: float,
    warn: bool = True,
):
    """
    Log global distance/overlap ranges and emit threshold warnings.
    """
    if stats.get("distance_min") is not None:
        logger.info(
            f"Global distance range: min={stats['distance_min']:.2f}m, max={stats['distance_max']:.2f}m"
        )
        if warn:
            if distance_threshold < stats["distance_min"]:
                logger.warning(
                    f"distance_threshold={distance_threshold} is BELOW global min={stats['distance_min']}; it may reject all candidates."
                )
            elif distance_threshold > stats["distance_max"]:
                logger.warning(
                    f"distance_threshold={distance_threshold} exceeds global max={stats['distance_max']}; distance filter will likely pass for all pairs."
                )
    else:
        logger.warning("Global distance range: unavailable (no valid centers)")

    if stats.get("overlap_min") is not None:
        logger.info(
            f"Global overlap range: min={stats['overlap_min']:.2f}, max={stats['overlap_max']:.2f}"
        )
        if warn:
            if overlap_threshold < stats["overlap_min"]:
                logger.warning(
                    f"overlap_threshold={overlap_threshold} is BELOW global min={stats['overlap_min']}; overlap filter will likely pass for all pairs."
                )
            elif overlap_threshold > stats["overlap_max"]:
                logger.warning(
                    f"overlap_threshold={overlap_threshold} exceeds global max={stats['overlap_max']}; it may reject all candidates."
                )
    else:
        logger.warning("Global overlap range: unavailable (no valid polygons)")


def get_camera_fov_polygon(
    sensor,
    scene_bounds: Optional[Tuple[float, float, float, float]] = None,
    max_camera_distance: float = 30.0,
):
    """
    Get or generate FOV polygon for a camera sensor.

    Calculate FOV polygon from camera frustum.

    :param sensor: Sensor data dictionary containing attributes and camera matrices.
    :type sensor: dict
    :param max_camera_distance: Maximum distance in meters for frustum calculation.
    :type max_camera_distance: float
    :return: Shapely Polygon object or None if generation fails.
    :rtype: shapely.Polygon or None
    """

    # Fall back to frustum calculation if FOV polygon not available or use_frustum is True
    logger.info("Starting to calculate FOV polygon using frustum")
    intrinsic_matrix, extrinsic_matrix = extract_camera_matrices(sensor)

    # Check if camera matrices were extracted successfully
    poly = None
    if intrinsic_matrix is None or extrinsic_matrix is None:
        logger.warning(f"Failed to extract camera matrices for sensor {sensor['id']}")
    else:
        poly = calculate_camera_frustum_polygon(
            intrinsic_matrix,
            extrinsic_matrix,
            scene_bounds=scene_bounds,
            max_distance=max_camera_distance,
        )
    return poly


def cluster_cameras_from_calibration(
    calibration_data: dict,
    n_clusters: int,
    start_camera_index: int = 0,
    use_frustum: bool = False,
    scene_bounds: Tuple[float, float, float, float] = None,
    max_camera_distance: float = 30.0,
    height_range: tuple = (1.0, 3.0),
    image_size: tuple = (1920, 1080),
    max_cluster_size: Optional[int] = None,
    mode: str = "densify",
    overlap_threshold: float = 0.0,
    distance_threshold: float = float("inf"),
    max_cascade_depth: int = 3,
) -> dict:
    """
    Cluster cameras from calibration data.

    This is the main entry point for camera clustering. It takes calibration data
    and partitions all cameras into N spatially compact clusters.

    :param max_cluster_size: Maximum desired cluster size (defaults to ceil(N / clusters)).
    :type max_cluster_size: int or None
    :param mode: 'balanced' for threshold-balanced clusters, 'densify' for filling clusters.
    :type mode: str
    :param overlap_threshold: Minimum overlap percentage (0-100) required for membership.
    :type overlap_threshold: float
    :param distance_threshold: Maximum centroid distance for membership.
    :type distance_threshold: float
    :param max_cascade_depth: Max recursion depth for densify-mode cascade.
    :type max_cascade_depth: int
    """
    sensors = calibration_data.get("sensors", [])

    if not sensors:
        logger.warning("No sensors found in calibration data")
        return {"assignments": [], "clusters": {}, "n_clusters": 0, "sensor_ids": []}

    logger.info(f"Starting camera clustering for {len(sensors)} cameras...")
    logger.info(
        f"Parameters: n_clusters={n_clusters}, start_camera_index={start_camera_index}, mode={mode} "
        f"overlap_threshold={overlap_threshold}, distance_threshold={distance_threshold}, "
        f"max_cluster_size={max_cluster_size}, max_cascade_depth={max_cascade_depth}"
    )

    # Defensive: Validate start_camera_index in range
    if not (0 <= start_camera_index < len(sensors)):
        logger.warning(
            f"start_camera_index {start_camera_index} is out of range for {len(sensors)} sensors. "
            "Defaulting to 0."
        )
        start_camera_index = 0

    try:
        manager = CameraClusterManager(sensors)
    except Exception as e:
        logger.error(f"Failed to create CameraClusterManager: {e}")
        return {"assignments": [], "clusters": {}, "n_clusters": 0, "sensor_ids": []}

    try:
        assignments = manager.cluster_cameras(
            n_clusters,
            start_camera_index=start_camera_index,
            use_frustum=use_frustum,
            scene_bounds=scene_bounds,
            max_camera_distance=max_camera_distance,
            height_range=height_range,
            image_size=image_size,
            mode=mode,
            overlap_threshold=overlap_threshold,
            distance_threshold=distance_threshold,
            max_cluster_size=max_cluster_size,
            max_cascade_depth=max_cascade_depth,
        )
    except Exception as e:
        logger.error(f"Error during cluster_cameras: {e}")
        return {
            "assignments": [],
            "clusters": {},
            "n_clusters": 0,
            "sensor_ids": [s.get("id", f"camera_{i}") for i, s in enumerate(sensors)],
        }

    # Defensive: assignments should be same length as sensors
    if len(assignments) != len(sensors):
        logger.error(
            f"Assignment list length ({len(assignments)}) does not match number of sensors ({len(sensors)})."
        )

    # Defensive: check for Nones in assignments
    none_indices = [i for i, val in enumerate(assignments) if val is None]
    if none_indices:
        logger.error(f"Assignments contain None for indices: {none_indices}")

    # Convert to group format
    clusters_dict = {}
    for camera_idx, cluster_id in enumerate(assignments):
        # Defensive: allow for None assignments
        if cluster_id is None:
            continue
        if cluster_id not in clusters_dict:
            clusters_dict[cluster_id] = []
        clusters_dict[cluster_id].append(camera_idx)

    result = {
        "assignments": assignments,
        "clusters": clusters_dict,
        "n_clusters": len(clusters_dict),
        "sensor_ids": [s.get("id", f"camera_{i}") for i, s in enumerate(sensors)],
    }

    # Log empty clusters warning (defensively)
    all_cluster_ids = set(range(n_clusters)) | set(clusters_dict.keys())
    for cluster_id in sorted(all_cluster_ids):
        camera_indices = clusters_dict.get(cluster_id, [])
        if not camera_indices:
            logger.warning(f"  Cluster {cluster_id} is empty.")

    return result


def find_suggested_cluster_params(args, verbose: bool = False) -> List[Dict[str, float]]:
    _configure_logging(args, verbose)

    calibration_path = Path(args.input_calibration)
    calibration_data = load_calib_json(
        calibration_path, load_original=True, validate=True,
    )
    sensors = calibration_data.get("sensors", [])

    if not sensors:
        logger.error("No sensors found in calibration data.")
        return []

    num_sensors = len(sensors)
    if args.max_camera_per_group <= 0:
        logger.error("max_camera_per_group must be greater than 0.")
        return []
    if args.max_camera_per_group > num_sensors:
        logger.warning(
            "max_camera_per_group (%d) exceeds number of sensors (%d); clustering will produce a single cluster.",
            args.max_camera_per_group,
            num_sensors,
        )
    n_clusters = math.ceil(num_sensors / args.max_camera_per_group)

    # Pre-compute global stats once to derive adaptive grids
    stats = {}
    try:
        stats_manager = CameraClusterManager(sensors)
        stats_manager.initialize_camera_info(
            use_frustum= not args.prefer_existing_fov,
            scene_bounds=None,
            max_camera_distance=args.max_camera_distance,
            height_range=tuple(args.height_range),
            image_size=tuple(args.image_size),
        )
        stats = stats_manager.compute_global_stats()
        logger.info(
            "Observed global ranges: distance=[%s, %s], overlap=[%s, %s]",
            stats.get("distance_min"),
            stats.get("distance_max"),
            stats.get("overlap_min"),
            stats.get("overlap_max"),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to compute global stats; falling back to defaults: %s", exc)

    overlap_grid, overlap_range = _grid_from_stats_or_default(
        args.overlap_grid, "overlap", stats
    )
    distance_grid, distance_range = _grid_from_stats_or_default(
        args.distance_grid, "distance", stats
    )
    start_idx_grid = _build_start_indices(args.start_index_grid, num_sensors, args.start_index_seed)
    # Worker count: 0 means auto
    auto_workers = os.cpu_count() or 1
    workers = args.workers if args.workers and args.workers > 0 else auto_workers

    run_cfg = {
        "use_frustum": not args.prefer_existing_fov,
        "max_camera_distance": args.max_camera_distance,
        "height_range": tuple(args.height_range),
        "image_size": tuple(args.image_size),
        "mode": args.mode,
        "max_cascade_depth": args.max_cascade_depth,
        "global_stats": stats if stats else None,
    }

    results: List[Dict[str, float]] = []
    seen = set()
    _run_grid(
        sensors=sensors,
        n_clusters=n_clusters,
        ov_grid=overlap_grid,
        dist_grid=distance_grid,
        start_idx_grid=start_idx_grid,
        label="coarse",
        results=results,
        seen=seen,
        args=args,
        run_cfg=run_cfg,
        workers=workers,
    )

    # Coarse-to-fine refinement: narrow around the best coarse result when using auto grids
    if results and not args.overlap_grid and not args.distance_grid:
        results.sort(key=lambda r: r["score"])
        best = results[0]
        fine_overlap = _build_fine_grid(
            best["overlap_threshold"], overlap_range[0], overlap_range[1], "overlap"
        )
        fine_distance = _build_fine_grid(
            best["distance_threshold"], distance_range[0], distance_range[1], "distance"
        )
        # Only refine if we actually narrowed
        if fine_overlap and fine_distance:
            logger.info(
                "Starting refine stage around best coarse result: overlap=%.3f, distance=%.2f | refine_overlaps=%s | refine_distances=%s",
                best["overlap_threshold"],
                best["distance_threshold"],
                [round(v, 3) for v in fine_overlap],
                [round(v, 3) for v in fine_distance],
            )
            _run_grid(
                sensors=sensors,
                n_clusters=n_clusters,
                ov_grid=fine_overlap,
                dist_grid=fine_distance,
                start_idx_grid=start_idx_grid,
                label="refine",
                results=results,
                seen=seen,
                args=args,
                run_cfg=run_cfg,
                workers=workers,
            )

    results.sort(key=lambda r: r["score"])
    return results
