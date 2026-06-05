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
Camera Grouping Module - Grouping with Duplication Support

This module implements a camera grouping algorithm that differs from clustering in key ways:
1. Fixed group sizes: Each group has EXACTLY its specified number of cameras
2. Flexible group configuration:
   - Single size: n_groups groups, all with the same size
   - Multiple sizes: n_groups groups for EACH size (total = n_groups × len(sizes))
     Example: n_groups=2, sizes=[5,8,6] → 6 groups (2×5, 2×8, 2×6)
3. Duplication allowed: Cameras can be assigned to multiple groups
4. Coverage guarantee: Every camera must be assigned to at least one group (via duplication)
5. Farthest-first seeding: Groups are started from spatially diverse locations

Algorithm Flow:
1. Seed first group from start_camera_index
2. Seed each subsequent group from the farthest unselected camera
3. Build each group by selecting cameras with best overlap/proximity
4. Prioritize unselected cameras; when exhausted, duplicate from already-selected cameras
5. Each group is filled to exactly its specified size
6. After all groups built, verify all cameras are covered (error if not)

Coverage is achieved through duplication during group building, NOT by exceeding group sizes.
If a camera cannot be covered, an error is raised.

Use cases:
- Multi-view reconstruction requiring overlapping camera groups
- Distributed processing with redundant camera coverage
- Ensuring all cameras contribute to at least one processing unit
"""

from __future__ import annotations
import logging
import math
import random
import sys
from typing import List, Dict, Tuple, Optional, Set, Union

from spatialai_data_utils.core.cameras.clustering import (
    CameraClusterHelper,
    CameraFovInfo,
)
from spatialai_data_utils.core.cameras.polygon import (
    parse_polygon,
    find_field_of_view_polygon,
    calculate_camera_frustum_polygon,
)
from spatialai_data_utils.core.cameras.utils import extract_camera_matrices

logger = logging.getLogger(__name__)


class CameraGroupManager:
    """
    Manager class for camera grouping with duplication support.

    Unlike CameraClusterManager which partitions cameras (each in exactly one cluster),
    this class allows cameras to be assigned to multiple groups, ensuring:
    - Each group has EXACTLY cameras_per_group cameras (no more, no less)
    - All cameras appear in at least one group (achieved through duplication)
    - Groups are spatially diverse (farthest-first seeding)

    If any camera cannot be covered through the duplication mechanism, a RuntimeError
    is raised. User should adjust n_groups, cameras_per_group, or thresholds.
    """

    def __init__(self, sensors_data: List[dict]):
        """
        Initialize the group manager with sensor data.

        :param sensors_data: List of sensor dictionaries from calibration data.
        :type sensors_data: List[dict]
        """
        self.sensors_data = sensors_data
        self._camera_info_dict: Dict[int, CameraFovInfo] = {}
        self.groups: Dict[int, List[int]] = {}
        # Track how many groups each camera is assigned to
        self._camera_assignment_counts: Dict[int, int] = {}
        # Track which cameras have been selected at least once
        self._selected_cameras: Set[int] = set()

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
            logger.info("Initializing camera information for grouping using frustum...")
        else:
            logger.info(
                "Initializing camera information for grouping using attributes..."
            )

        for idx, sensor in enumerate(self.sensors_data):
            poly = None
            camera_id = sensor.get("id")

            if not use_frustum:
                try:
                    poly_str = find_field_of_view_polygon(sensor["attributes"])
                    poly = parse_polygon(poly_str)
                except (ValueError, KeyError):
                    logger.warning(
                        f"Field of view polygon not found for sensor {sensor['id']}, "
                        "will fall back to frustum calculation"
                    )
                    pass

            # Fall back to frustum calculation if FOV polygon not available
            if poly is None or use_frustum:
                intrinsic_matrix, extrinsic_matrix = extract_camera_matrices(sensor)

                if intrinsic_matrix is None or extrinsic_matrix is None:
                    logger.error(
                        f"Failed to extract camera matrices for sensor {sensor['id']}"
                    )
                    sys.exit(1)

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
            self._camera_assignment_counts[idx] = 0

        valid_count = sum(
            1
            for info in self._camera_info_dict.values()
            if info.poly is not None and not info.poly.is_empty
        )
        logger.info(
            f"Initialized {len(self._camera_info_dict)} cameras "
            f"({valid_count} with valid FOV polygons)"
        )

    def get_camera_num(self) -> int:
        """Get total number of cameras."""
        return len(self._camera_info_dict)

    def get_camera_polygon(self, camera_idx: int):
        """Get camera's FOV polygon."""
        return self._camera_info_dict[camera_idx].get_poly()

    def get_camera_center_point(self, camera_idx: int):
        """Get camera's FOV center point."""
        return self._camera_info_dict[camera_idx].get_center_point()

    def get_camera_id(self, camera_idx: int) -> str:
        """Get camera's string ID."""
        return self._camera_info_dict[camera_idx].get_camera_id()

    def get_union_polygon(self, camera_idx_list: List[int]):
        """Get the union of FOV polygons for a list of cameras."""
        polygon_list = []
        for camera_idx in camera_idx_list:
            polygon = self.get_camera_polygon(camera_idx)
            if polygon is not None:
                polygon_list.append(polygon)

        return CameraClusterHelper.get_unioned_polygon(polygon_list)

    def get_unselected_cameras(self) -> List[int]:
        """Get list of camera indices not yet assigned to any group."""
        return [
            idx
            for idx in self._camera_info_dict.keys()
            if idx not in self._selected_cameras
        ]

    def get_all_cameras(self) -> List[int]:
        """Get list of all camera indices."""
        return list(self._camera_info_dict.keys())

    def _evaluate_membership(
        self,
        camera_idx: int,
        group_cameras: List[int],
        overlap_threshold: float,
        distance_threshold: float,
    ) -> Tuple[bool, float, float]:
        """
        Evaluate whether a camera satisfies overlap/distance thresholds for a group.

        :param camera_idx: Candidate camera index.
        :param group_cameras: Cameras already in the group.
        :param overlap_threshold: Minimum required overlap (0-1).
        :param distance_threshold: Maximum centroid distance.
        :return: (is_valid, overlap, distance)
        """
        if not group_cameras:
            return True, 0.0, 0.0

        union_polygon = self.get_union_polygon(group_cameras)
        camera_polygon = self.get_camera_polygon(camera_idx)
        overlap = CameraClusterHelper.compute_overlap(union_polygon, camera_polygon)

        cluster_centers = [self.get_camera_center_point(idx) for idx in group_cameras]
        camera_center = self.get_camera_center_point(camera_idx)
        distance = CameraClusterHelper.shortest_distance(
            point_list=cluster_centers, target_point=camera_center
        )

        overlap_ok = overlap_threshold is None or overlap >= overlap_threshold
        distance_ok = distance_threshold is None or distance <= distance_threshold
        if math.isinf(distance_threshold):
            distance_ok = True

        return overlap_ok and distance_ok, overlap, distance

    def _pick_best_camera(
        self,
        candidates: List[int],
        group_cameras: List[int],
        overlap_threshold: float,
        distance_threshold: float,
        randomize: bool = False,
        top_k: int = 3,
        rng: Optional[random.Random] = None,
    ) -> Optional[int]:
        """
        Pick the best camera from candidates based on overlap and distance.

        :param candidates: List of candidate camera indices.
        :param group_cameras: Cameras already in the group.
        :param overlap_threshold: Minimum required overlap.
        :param distance_threshold: Maximum centroid distance.
        :param randomize: If True, randomly select from top-k candidates.
        :param top_k: Number of top candidates to consider for randomization.
        :param rng: Optional local Random instance to use for randomization.
        :return: Best camera index or None.
        """
        valid_candidates = []

        for camera_idx in candidates:
            if camera_idx in group_cameras:
                continue

            is_valid, overlap, distance = self._evaluate_membership(
                camera_idx, group_cameras, overlap_threshold, distance_threshold
            )
            if is_valid:
                valid_candidates.append((overlap, distance, camera_idx))

        if not valid_candidates:
            return None

        # Sort by overlap (desc), then distance (asc)
        valid_candidates.sort(key=lambda x: (-x[0], x[1]))

        if randomize and len(valid_candidates) > 1 and rng is not None:
            # Randomly select from top-k candidates for variety
            top_candidates = valid_candidates[: min(top_k, len(valid_candidates))]
            return rng.choice(top_candidates)[2]

        return valid_candidates[0][2]

    def _pick_farthest_camera(
        self,
        candidates: List[int],
        reference_cameras: List[int],
        randomize: bool = False,
        top_k: int = 3,
        rng: Optional[random.Random] = None,
    ) -> Optional[int]:
        """
        Pick the camera that is farthest from all reference cameras.

        :param candidates: List of candidate camera indices.
        :param reference_cameras: Reference cameras to measure distance from.
        :param randomize: If True, randomly select from top-k farthest candidates.
        :param top_k: Number of top candidates to consider for randomization.
        :param rng: Optional local Random instance to use for randomization.
        :return: Farthest camera index or None.
        """
        if not candidates:
            return None

        if not reference_cameras:
            # No reference - return random candidate if randomize, else first
            if randomize and rng is not None:
                return rng.choice(candidates)
            return candidates[0]

        reference_centers = [
            self.get_camera_center_point(idx) for idx in reference_cameras
        ]

        # Build list of (distance, camera_idx) pairs
        distance_camera_pairs = []
        for camera_idx in candidates:
            camera_center = self.get_camera_center_point(camera_idx)
            # Compute minimum distance to any reference camera
            min_distance = CameraClusterHelper.shortest_distance(
                point_list=reference_centers, target_point=camera_center
            )
            distance_camera_pairs.append((min_distance, camera_idx))

        # Sort by distance (descending)
        distance_camera_pairs.sort(key=lambda x: -x[0])

        if randomize and len(distance_camera_pairs) > 1 and rng is not None:
            # Randomly select from top-k farthest candidates
            top_candidates = distance_camera_pairs[
                : min(top_k, len(distance_camera_pairs))
            ]
            return rng.choice(top_candidates)[1]

        return distance_camera_pairs[0][1]

    def _build_single_group(
        self,
        group_id: int,
        seed_camera: int,
        cameras_per_group: int,
        overlap_threshold: float,
        distance_threshold: float,
        prefer_unselected: bool = True,
        randomize: bool = False,
        rng: Optional[random.Random] = None,
    ) -> List[int]:
        """
        Build a single group starting from a seed camera.

        :param group_id: Group identifier.
        :param seed_camera: Starting camera for the group.
        :param cameras_per_group: Target number of cameras in the group.
        :param overlap_threshold: Minimum FOV overlap required.
        :param distance_threshold: Maximum centroid distance allowed.
        :param prefer_unselected: Prefer cameras not yet in any group.
        :param randomize: Add randomization to camera selection for variety.
        :param rng: Optional local Random instance to use for randomization.
        :return: List of camera indices in the group.
        """
        group = [seed_camera]
        self._selected_cameras.add(seed_camera)
        self._camera_assignment_counts[seed_camera] += 1

        logger.info(
            f"Group {group_id + 1}: Seeded with {self.get_camera_id(seed_camera)}"
        )

        while len(group) < cameras_per_group:
            # First, try to find candidates from unselected cameras
            if prefer_unselected:
                unselected = self.get_unselected_cameras()
                best = self._pick_best_camera(
                    unselected,
                    group,
                    overlap_threshold,
                    distance_threshold,
                    randomize=randomize,
                    rng=rng,
                )
                if best is not None:
                    group.append(best)
                    self._selected_cameras.add(best)
                    self._camera_assignment_counts[best] += 1
                    logger.info(
                        f"Group {group_id + 1}: Added {self.get_camera_id(best)} "
                        f"(unselected, size={len(group)})"
                    )
                    continue

            # If no unselected cameras available or valid, try all cameras (allow duplication)
            all_cameras = self.get_all_cameras()
            best = self._pick_best_camera(
                all_cameras,
                group,
                overlap_threshold,
                distance_threshold,
                randomize=randomize,
                rng=rng,
            )
            if best is not None:
                group.append(best)
                self._selected_cameras.add(best)
                self._camera_assignment_counts[best] += 1
                logger.info(
                    f"Group {group_id + 1}: Added {self.get_camera_id(best)} "
                    f"(duplicate allowed, size={len(group)})"
                )
                continue

            # If still no valid camera, relax constraints and pick closest
            logger.warning(
                f"Group {group_id + 1}: No valid cameras with thresholds, "
                "relaxing constraints"
            )
            best = self._pick_best_camera(
                all_cameras, group, 0.0, float("inf"), randomize=randomize, rng=rng
            )
            if best is not None:
                group.append(best)
                self._selected_cameras.add(best)
                self._camera_assignment_counts[best] += 1
                logger.info(
                    f"Group {group_id + 1}: Added {self.get_camera_id(best)} "
                    f"(relaxed constraints, size={len(group)})"
                )
            else:
                # No more cameras available at all
                logger.error(
                    f"Group {group_id + 1}: Cannot find more cameras, "
                    f"current size={len(group)}"
                )
                break

        return group

    def _is_duplicate_group(
        self,
        new_group: List[int],
        existing_groups: Dict[int, List[int]],
    ) -> bool:
        """
        Check if a new group is a duplicate of any existing group.

        :param new_group: The new group to check.
        :param existing_groups: Dictionary of existing groups.
        :return: True if new_group is a duplicate.
        """
        new_set = set(new_group)
        for existing_group in existing_groups.values():
            if set(existing_group) == new_set:
                return True
        return False

    def create_groups(
        self,
        n_groups: int,
        cameras_per_group: Union[int, List[int]],
        start_camera_index: int = 0,
        use_frustum: bool = False,
        scene_bounds: Tuple[float, float, float, float] = None,
        max_camera_distance: float = 30.0,
        height_range: tuple = (1.0, 3.0),
        image_size: tuple = (1920, 1080),
        overlap_threshold: float = 0.2,
        distance_threshold: float = float("inf"),
        randomize: bool = True,
        max_duplicate_retries: int = 5,
        random_seed: Optional[int] = None,
    ) -> Dict[int, List[int]]:
        """
        Main grouping method: create groups with specified cameras per group.

        Algorithm:
        1. Initialize camera info (FOV polygons, centers)
        2. Seed first group from start_camera_index
        3. For each subsequent group, seed from farthest unselected camera
        4. Build each group by adding cameras with best overlap/proximity
        5. Prioritize unselected cameras; allow duplication when exhausted
        6. Each group is filled to exactly its specified size
        7. Check for duplicate groups and regenerate if needed
        8. Verify all cameras are covered; raise RuntimeError if not

        :param n_groups: Number of groups per size type.
        :param cameras_per_group: Number of cameras per group. Can be:
            - int: Create n_groups groups, all with this size
            - List[int]: Create n_groups groups for EACH size in the list
              (total groups = n_groups * len(list))
              Example: n_groups=2, cameras_per_group=[5, 8, 6] creates:
              - 2 groups with 5 cameras
              - 2 groups with 8 cameras
              - 2 groups with 6 cameras
              - Total: 6 groups
        :param start_camera_index: Index of first camera for seeding.
        :param use_frustum: Use frustum-based FOV calculation.
        :param scene_bounds: Scene bounding box for frustum clipping.
        :param max_camera_distance: Maximum distance for frustum calculation.
        :param height_range: Height range for ground plane intersection.
        :param image_size: Image dimensions for frustum calculation.
        :param overlap_threshold: Minimum FOV overlap (0-1) for group membership.
        :param distance_threshold: Maximum centroid distance for membership.
        :param randomize: Add randomization to camera selection for variety.
        :param max_duplicate_retries: Maximum retries when a duplicate group is generated.
        :param random_seed: Optional seed for random number generator for deterministic results.
        :return: Dictionary mapping group_id to list of camera indices.
        :raises RuntimeError: If any camera cannot be assigned to at least one group.
        """
        # Create a local RNG when randomization is enabled.
        # - random_seed is None: non-deterministic behavior (system entropy)
        # - random_seed provided: deterministic behavior
        rng: Optional[random.Random] = None
        if randomize:
            if random_seed is None:
                rng = random.Random()
                logger.info("Randomization enabled without fixed seed (non-deterministic)")
            else:
                rng = random.Random(random_seed)
                logger.info(f"Random seed set to {random_seed} for deterministic grouping")

        # Validate inputs
        if n_groups <= 0:
            logger.error("n_groups must be positive")
            sys.exit(1)

        # Build group_sizes list based on cameras_per_group type
        if isinstance(cameras_per_group, int):
            if cameras_per_group <= 0:
                logger.error("cameras_per_group must be positive")
                sys.exit(1)
            # Single size: create n_groups groups with this size
            group_sizes = [cameras_per_group] * n_groups
        else:
            # List of sizes: create n_groups groups for EACH size
            for i, size in enumerate(cameras_per_group):
                if size <= 0:
                    logger.error(f"cameras_per_group[{i}] must be positive, got {size}")
                    sys.exit(1)
            # Expand: for each size, create n_groups groups
            group_sizes = []
            for size in cameras_per_group:
                group_sizes.extend([size] * n_groups)

        # Initialize camera info first (needed for get_camera_num())
        self.initialize_camera_info(
            use_frustum=use_frustum,
            scene_bounds=scene_bounds,
            max_camera_distance=max_camera_distance,
            height_range=height_range,
            image_size=image_size,
        )

        num_cameras = self.get_camera_num()

        # Filter group_sizes to avoid guaranteed duplicates:
        # When group size == num_cameras, only one unique group is possible
        filtered_group_sizes = []
        size_counts = {}
        for size in group_sizes:
            if size >= num_cameras:
                # Only allow one group of size >= num_cameras (all cameras)
                if size not in size_counts:
                    size_counts[size] = 0
                if size_counts[size] == 0:
                    filtered_group_sizes.append(num_cameras)  # Cap at num_cameras
                    size_counts[size] += 1
                else:
                    logger.info(
                        f"Skipping duplicate group of size {size} (equals total cameras, "
                        "only one unique combination possible)"
                    )
            else:
                filtered_group_sizes.append(size)

        group_sizes = filtered_group_sizes
        total_groups = len(group_sizes)
        total_slots = sum(group_sizes)
        if num_cameras > 0:
            min_duplication = max(0, (total_slots - num_cameras) / num_cameras)
        else:
            min_duplication = 0

        # Log group configuration
        if isinstance(cameras_per_group, int):
            # Single size: n_groups groups with same size
            logger.info(
                f"Creating {total_groups} groups with {cameras_per_group} cameras each"
            )
        else:
            # Multiple sizes: n_groups groups per size
            logger.info(
                f"Creating {total_groups} groups total "
                f"({n_groups} groups × {len(cameras_per_group)} sizes: {list(cameras_per_group)})"
            )
            logger.info(f"Group sizes: {group_sizes}")
        logger.info(f"Total slots: {total_slots}, available cameras: {num_cameras}")
        if total_slots > num_cameras:
            logger.info(
                f"Camera duplication required: each camera will appear ~{1 + min_duplication:.1f} times on average"
            )

        # Validate start_camera_index
        if not (0 <= start_camera_index < num_cameras):
            logger.warning(
                f"start_camera_index {start_camera_index} out of range, using 0"
            )
            start_camera_index = 0

        # Clear state
        self.groups = {}
        self._selected_cameras = set()
        self._camera_assignment_counts = {
            idx: 0 for idx in self._camera_info_dict.keys()
        }

        # Track all cameras that have been used as group seeds
        seed_cameras = []

        for group_id in range(total_groups):
            retry_count = 0
            group = None

            while retry_count <= max_duplicate_retries:
                # Determine seed camera for this group
                if group_id == 0 and retry_count == 0:
                    seed = start_camera_index
                else:
                    # Pick farthest unselected camera from all previous seeds
                    unselected = self.get_unselected_cameras()
                    if unselected:
                        seed = self._pick_farthest_camera(
                            unselected, seed_cameras, randomize=randomize, rng=rng
                        )
                    else:
                        # All cameras selected, pick farthest from seeds among all cameras
                        seed = self._pick_farthest_camera(
                            self.get_all_cameras(),
                            seed_cameras,
                            randomize=randomize,
                            rng=rng,
                        )

                # Build the group with its specific size
                group = self._build_single_group(
                    group_id=group_id,
                    seed_camera=seed,
                    cameras_per_group=group_sizes[group_id],
                    overlap_threshold=overlap_threshold,
                    distance_threshold=distance_threshold,
                    prefer_unselected=True,
                    randomize=randomize,
                    rng=rng,
                )

                # Check if this group is a duplicate of an existing group
                if self._is_duplicate_group(group, self.groups):
                    retry_count += 1
                    # Revert the assignment counts for this attempt
                    for camera_idx in group:
                        self._camera_assignment_counts[camera_idx] -= 1
                        if self._camera_assignment_counts[camera_idx] == 0:
                            self._selected_cameras.discard(camera_idx)

                    if retry_count <= max_duplicate_retries:
                        logger.info(
                            f"Group {group_id + 1}: Duplicate detected, regenerating "
                            f"(attempt {retry_count}/{max_duplicate_retries})"
                        )
                        continue
                    else:
                        logger.info(
                            f"Group {group_id + 1}: Could not generate unique group "
                            f"after {max_duplicate_retries} retries, skipping"
                        )
                        group = None
                        break

                # Not a duplicate - add the group
                # Track the seed used for this group (for subsequent group seeding)
                seed_cameras.append(seed)
                break

            if group is not None:
                self.groups[len(self.groups)] = group
                logger.info(f"Group {len(self.groups)} completed: {len(group)} cameras")

        # Check coverage: ensure all cameras are assigned to at least one group
        unassigned = [
            idx for idx, count in self._camera_assignment_counts.items() if count == 0
        ]

        if unassigned:
            # Each group must have exactly its specified size.
            # If any camera is unassigned, it means the algorithm couldn't cover
            # all cameras through the group building process (with duplication).
            # This is an error - user should adjust parameters.
            unassigned_ids = [self.get_camera_id(idx) for idx in unassigned]
            error_msg = (
                f"Camera grouping failed: {len(unassigned)} camera(s) not assigned to any group: "
                f"{unassigned_ids}. Group sizes: {group_sizes} (total slots: {total_slots}). "
                "To ensure full coverage, consider:\n"
                "  1. Increase n_groups or cameras_per_group to create more slots\n"
                "  2. Lower --min_overlap_threshold to allow more camera combinations\n"
                "  3. Increase --max_distance_threshold to allow spatially distant cameras"
            )
            logger.error(error_msg)
            raise RuntimeError(error_msg)

        # Log summary
        self._log_summary()

        return self.groups

    def _log_summary(self):
        """Log grouping summary statistics."""
        logger.info("=" * 60)
        logger.info("GROUPING SUMMARY")
        logger.info("=" * 60)

        num_cameras = self.get_camera_num()
        logger.info(f"Total cameras: {num_cameras}")
        logger.info(f"Number of groups: {len(self.groups)}")

        # Camera duplication statistics
        assignment_counts = list(self._camera_assignment_counts.values())
        if assignment_counts:
            avg_assignments = sum(assignment_counts) / len(assignment_counts)
            max_assignments = max(assignment_counts)
            min_assignments = min(assignment_counts)
        logger.info(
            f"Camera assignments: min={min_assignments}, max={max_assignments}, "
            f"avg={avg_assignments:.1f}"
        )

        # Cameras with no assignment (should be 0 after ensure_coverage)
        unassigned_count = sum(1 for c in assignment_counts if c == 0)
        if unassigned_count > 0:
            logger.warning(f"Cameras without any group: {unassigned_count}")

        # Group sizes summary
        group_sizes = [len(cameras) for cameras in self.groups.values()]
        logger.info(f"Group sizes: {group_sizes}")

        logger.info("=" * 60)

    def get_group_list(self) -> List[List[int]]:
        """
        Get groups as a list of camera index lists.

        :return: List where each element is a list of camera indices for a group.
        """
        return [self.groups[group_id] for group_id in sorted(self.groups.keys())]


def group_cameras_from_calibration(
    calibration_data: dict,
    n_groups: int,
    cameras_per_group: Union[int, List[int]],
    start_camera_index: int = 0,
    use_frustum: bool = False,
    scene_bounds: Tuple[float, float, float, float] = None,
    max_camera_distance: float = 30.0,
    height_range: tuple = (1.0, 3.0),
    image_size: tuple = (1920, 1080),
    overlap_threshold: float = 0.2,
    distance_threshold: float = float("inf"),
    randomize: bool = True,
    max_duplicate_retries: int = 5,
    random_seed: Optional[int] = None,
) -> dict:
    """
    Group cameras from calibration data with duplication support.

    This is the main entry point for camera grouping. It differs from clustering in that:
    - Cameras can appear in multiple groups
    - Group sizes are fixed by the user
    - All cameras are guaranteed to be in at least one group

    :param calibration_data: Dictionary with 'sensors' key containing sensor data.
    :param n_groups: Number of groups per size type.
    :param cameras_per_group: Number of cameras per group. Can be:
        - int: Create n_groups groups, all with this size
        - List[int]: Create n_groups groups for EACH size in the list
          (total groups = n_groups * len(list))
          Example: n_groups=2, cameras_per_group=[5, 8, 6] creates 6 groups
    :param start_camera_index: Index of first camera for seeding.
    :param use_frustum: Use frustum-based FOV calculation.
    :param scene_bounds: Scene bounding box for frustum clipping.
    :param max_camera_distance: Maximum distance for frustum calculation.
    :param height_range: Height range for ground plane intersection.
    :param image_size: Image dimensions for frustum calculation.
    :param overlap_threshold: Minimum FOV overlap (0-1) for group membership.
    :param distance_threshold: Maximum centroid distance for membership.
    :param randomize: Add randomization to camera selection for variety.
    :param max_duplicate_retries: Maximum retries when a duplicate group is generated.
    :param random_seed: Optional seed for random number generator for deterministic results.
    :return: Dictionary with 'groups', 'n_groups', 'sensor_ids', 'camera_assignments'.
    """
    sensors = calibration_data.get("sensors", [])

    if not sensors:
        logger.warning("No sensors found in calibration data")
        return {
            "groups": {},
            "n_groups": 0,
            "sensor_ids": [],
            "camera_assignments": {},
        }

    logger.info(f"Starting camera grouping for {len(sensors)} cameras...")
    logger.info(
        f"Parameters: n_groups={n_groups}, cameras_per_group={cameras_per_group}, "
        f"start_camera_index={start_camera_index}, overlap_threshold={overlap_threshold}, "
        f"distance_threshold={distance_threshold}, randomize={randomize}, "
        f"max_duplicate_retries={max_duplicate_retries}, random_seed={random_seed}"
    )

    # Validate start_camera_index
    if not (0 <= start_camera_index < len(sensors)):
        logger.warning(
            f"start_camera_index {start_camera_index} out of range for {len(sensors)} sensors, "
            "using 0"
        )
        start_camera_index = 0

    try:
        manager = CameraGroupManager(sensors)
    except Exception as e:
        logger.error(f"Failed to create CameraGroupManager: {e}")
        return {
            "groups": {},
            "n_groups": 0,
            "sensor_ids": [s.get("id", f"camera_{i}") for i, s in enumerate(sensors)],
            "camera_assignments": {},
        }

    try:
        groups = manager.create_groups(
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
    except RuntimeError:
        # Re-raise RuntimeError (e.g., camera coverage failure) to caller
        raise
    except Exception as e:
        logger.error(f"Error during create_groups: {e}")
        return {
            "groups": {},
            "n_groups": 0,
            "sensor_ids": [s.get("id", f"camera_{i}") for i, s in enumerate(sensors)],
            "camera_assignments": {},
        }

    # Build camera assignments (camera_idx -> list of group_ids)
    camera_assignments = {idx: [] for idx in range(len(sensors))}
    for group_id, camera_indices in groups.items():
        for camera_idx in camera_indices:
            camera_assignments[camera_idx].append(group_id)

    result = {
        "groups": groups,
        "n_groups": len(groups),
        "sensor_ids": [s.get("id", f"camera_{i}") for i, s in enumerate(sensors)],
        "camera_assignments": camera_assignments,
        "group_list": manager.get_group_list(),
    }

    return result
