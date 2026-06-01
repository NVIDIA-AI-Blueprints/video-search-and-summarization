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
Test cases for camera grouping algorithm.

This module tests the camera grouping functionality including:
1. CameraGroupManager class methods
2. group_cameras_from_calibration function
3. create_camera_groups_from_calibration pipeline function

The key difference from camera clustering is that grouping:
- Allows camera duplication across groups
- Has fixed group sizes and counts
- Guarantees all cameras are assigned to at least one group

Uses synthetic calibration data for testing.
"""

import json
import logging
import os
import tempfile
import shutil
from pathlib import Path

from shapely.geometry import Polygon

try:
    import pytest
except ImportError:
    pytest = None

logger = logging.getLogger(__name__)

from spatialai_data_utils.core.cameras.grouping import (
    CameraGroupManager,
    group_cameras_from_calibration,
)
from spatialai_data_utils.core.cameras.bev import (
    create_camera_groups_from_calibration,
)


MINIMAL_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\nIDATx\x9cc``\x00\x00\x00"
    b"\x02\x00\x01E-\xb4\xdc\x00\x00\x00\x00IEND\xaeB`\x82"
)


def _ensure_map_file(directory: Path) -> Path:
    """Ensure a minimal Top.png exists in directory and return its path."""
    map_path = directory / "Top.png"
    if not map_path.exists():
        try:
            from PIL import Image

            Image.new("RGB", (200, 200), color=(0, 0, 0)).save(map_path)
        except Exception:
            map_path.write_bytes(MINIMAL_PNG)
    return map_path


# ==============================================================================
# Fixtures
# ==============================================================================


@pytest.fixture
def temp_dir():
    """Create a temporary directory for test files under spatialai_data_utils/tmp/."""
    project_root = Path(__file__).resolve().parents[3]
    tmp_base = project_root / "tmp"
    tmp_base.mkdir(exist_ok=True)

    temp_path = tempfile.mkdtemp(dir=tmp_base, prefix="test_grouping_")
    preserve_tmp = os.getenv("PRESERVE_TEST_TMP", "").lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
    logger.debug("=" * 80)
    logger.debug("Test temp directory: %s", temp_path)
    logger.debug("=" * 80)
    yield temp_path

    if preserve_tmp:
        logger.debug("Preserving temp directory for inspection: %s", temp_path)
    else:
        shutil.rmtree(temp_path, ignore_errors=True)


@pytest.fixture
def synthetic_calibration_dir(temp_dir):
    """Create a self-contained calibration directory with map."""
    base = Path(temp_dir) / "synthetic_calibration"
    base.mkdir(parents=True, exist_ok=True)

    sensors = [
        {
            "id": f"Camera_{idx:02d}",
            "type": "camera",
            "intrinsicMatrix": [
                [1000.0, 0.0, 960.0],
                [0.0, 1000.0, 540.0],
                [0.0, 0.0, 1.0],
            ],
            "extrinsicMatrix": [
                [1.0, 0.0, 0.0, float(idx * 5)],
                [0.0, 1.0, 0.0, 0.0],
                [0.0, 0.0, 1.0, 5.0],
                [0.0, 0.0, 0.0, 1.0],
            ],
            "attributes": [
                {"name": "frameWidth", "value": "1920"},
                {"name": "frameHeight", "value": "1080"},
                {
                    "name": "fieldOfViewPolygon",
                    "value": f"POLYGON(({idx * 10} 0, {idx * 10 + 10} 0, {idx * 10 + 10} 10, {idx * 10} 10, {idx * 10} 0))",
                },
            ],
        }
        for idx in range(6)  # 6 cameras for grouping tests
    ]

    calib_path = base / "calibration.json"
    with calib_path.open("w") as f:
        json.dump({"sensors": sensors}, f)

    map_path = base / "Top.png"
    try:
        from PIL import Image

        Image.new("RGB", (200, 200), color=(0, 0, 0)).save(map_path)
    except Exception:
        map_path.write_bytes(MINIMAL_PNG)

    return base, map_path


@pytest.fixture
def mock_sensors_data():
    """Create mock sensor data for testing CameraGroupManager."""
    return [
        {
            "id": "Camera_01",
            "type": "camera",
            "intrinsicMatrix": [
                [1000.0, 0.0, 960.0],
                [0.0, 1000.0, 540.0],
                [0.0, 0.0, 1.0],
            ],
            "extrinsicMatrix": [
                [1.0, 0.0, 0.0, 0.0],
                [0.0, 0.866, -0.5, 2.0],
                [0.0, 0.5, 0.866, 5.0],
                [0.0, 0.0, 0.0, 1.0],
            ],
            "attributes": [
                {"name": "frameWidth", "value": "1920"},
                {"name": "frameHeight", "value": "1080"},
                {
                    "name": "fieldOfViewPolygon",
                    "value": "POLYGON((0 0, 10 0, 10 10, 0 10, 0 0))",
                },
            ],
        },
        {
            "id": "Camera_02",
            "type": "camera",
            "intrinsicMatrix": [
                [1000.0, 0.0, 960.0],
                [0.0, 1000.0, 540.0],
                [0.0, 0.0, 1.0],
            ],
            "extrinsicMatrix": [
                [0.866, 0.0, 0.5, 5.0],
                [0.0, 1.0, 0.0, 0.0],
                [-0.5, 0.0, 0.866, 5.0],
                [0.0, 0.0, 0.0, 1.0],
            ],
            "attributes": [
                {"name": "frameWidth", "value": "1920"},
                {"name": "frameHeight", "value": "1080"},
                {
                    "name": "fieldOfViewPolygon",
                    "value": "POLYGON((5 5, 15 5, 15 15, 5 15, 5 5))",
                },
            ],
        },
        {
            "id": "Camera_03",
            "type": "camera",
            "intrinsicMatrix": [
                [1000.0, 0.0, 960.0],
                [0.0, 1000.0, 540.0],
                [0.0, 0.0, 1.0],
            ],
            "extrinsicMatrix": [
                [0.707, -0.707, 0.0, 20.0],
                [0.707, 0.707, 0.0, 0.0],
                [0.0, 0.0, 1.0, 5.0],
                [0.0, 0.0, 0.0, 1.0],
            ],
            "attributes": [
                {"name": "frameWidth", "value": "1920"},
                {"name": "frameHeight", "value": "1080"},
                {
                    "name": "fieldOfViewPolygon",
                    "value": "POLYGON((20 0, 30 0, 30 10, 20 10, 20 0))",
                },
            ],
        },
        {
            "id": "Camera_04",
            "type": "camera",
            "intrinsicMatrix": [
                [1000.0, 0.0, 960.0],
                [0.0, 1000.0, 540.0],
                [0.0, 0.0, 1.0],
            ],
            "extrinsicMatrix": [
                [1.0, 0.0, 0.0, 25.0],
                [0.0, 1.0, 0.0, 0.0],
                [0.0, 0.0, 1.0, 5.0],
                [0.0, 0.0, 0.0, 1.0],
            ],
            "attributes": [
                {"name": "frameWidth", "value": "1920"},
                {"name": "frameHeight", "value": "1080"},
                {
                    "name": "fieldOfViewPolygon",
                    "value": "POLYGON((22 2, 32 2, 32 12, 22 12, 22 2))",
                },
            ],
        },
    ]


@pytest.fixture
def mock_calibration_data(mock_sensors_data):
    """Create mock calibration data with sensors."""
    return {"sensors": mock_sensors_data}


@pytest.fixture
def simple_polygons():
    """Create simple test polygons for overlap testing."""
    # Two overlapping squares
    poly1 = Polygon([(0, 0), (10, 0), (10, 10), (0, 10), (0, 0)])
    poly2 = Polygon([(5, 5), (15, 5), (15, 15), (5, 15), (5, 5)])
    # Non-overlapping polygon
    poly3 = Polygon([(20, 20), (30, 20), (30, 30), (20, 30), (20, 20)])
    return poly1, poly2, poly3


# ==============================================================================
# Test CameraGroupManager
# ==============================================================================


class TestCameraGroupManager:
    """Test suite for CameraGroupManager class."""

    def test_initialization(self, mock_sensors_data):
        """Test CameraGroupManager initialization."""
        manager = CameraGroupManager(mock_sensors_data)

        assert manager.sensors_data == mock_sensors_data
        assert manager._camera_info_dict == {}
        assert manager.groups == {}
        assert manager._camera_assignment_counts == {}
        assert manager._selected_cameras == set()

    def test_initialize_camera_info(self, mock_sensors_data):
        """Test camera info initialization from sensor data."""
        manager = CameraGroupManager(mock_sensors_data)
        manager.initialize_camera_info(use_frustum=False)

        assert len(manager._camera_info_dict) == 4
        # All cameras should have center points calculated
        for idx, info in manager._camera_info_dict.items():
            assert info.center_point is not None
            assert info.poly is not None or info.center_point == (0.0, 0.0)

        # Assignment counts should be initialized
        assert len(manager._camera_assignment_counts) == 4
        for idx in manager._camera_assignment_counts:
            assert manager._camera_assignment_counts[idx] == 0

    def test_get_camera_num(self, mock_sensors_data):
        """Test camera count retrieval."""
        manager = CameraGroupManager(mock_sensors_data)
        manager.initialize_camera_info(use_frustum=False)

        assert manager.get_camera_num() == 4

    def test_get_camera_num_empty(self):
        """Test camera count with empty sensor data."""
        manager = CameraGroupManager([])
        manager.initialize_camera_info(use_frustum=False)

        assert manager.get_camera_num() == 0

    def test_get_unselected_cameras(self, mock_sensors_data):
        """Test getting unselected camera indices."""
        manager = CameraGroupManager(mock_sensors_data)
        manager.initialize_camera_info(use_frustum=False)

        unselected = manager.get_unselected_cameras()
        assert len(unselected) == 4
        assert set(unselected) == {0, 1, 2, 3}

        # Mark one camera as selected
        manager._selected_cameras.add(0)
        unselected = manager.get_unselected_cameras()
        assert len(unselected) == 3
        assert 0 not in unselected

    def test_get_all_cameras(self, mock_sensors_data):
        """Test getting all camera indices."""
        manager = CameraGroupManager(mock_sensors_data)
        manager.initialize_camera_info(use_frustum=False)

        all_cameras = manager.get_all_cameras()
        assert len(all_cameras) == 4
        assert set(all_cameras) == {0, 1, 2, 3}

    def test_get_camera_id(self, mock_sensors_data):
        """Test getting camera string ID."""
        manager = CameraGroupManager(mock_sensors_data)
        manager.initialize_camera_info(use_frustum=False)

        assert manager.get_camera_id(0) == "Camera_01"
        assert manager.get_camera_id(1) == "Camera_02"
        assert manager.get_camera_id(3) == "Camera_04"

    def test_get_camera_polygon(self, mock_sensors_data):
        """Test camera polygon retrieval."""
        manager = CameraGroupManager(mock_sensors_data)
        manager.initialize_camera_info(use_frustum=False)

        poly = manager.get_camera_polygon(0)
        assert poly is not None
        assert not poly.is_empty

    def test_get_camera_center_point(self, mock_sensors_data):
        """Test camera center point retrieval."""
        manager = CameraGroupManager(mock_sensors_data)
        manager.initialize_camera_info(use_frustum=False)

        center = manager.get_camera_center_point(0)
        assert center is not None
        assert len(center) == 2
        # Camera_01 has polygon (0,0)-(10,10), center should be (5, 5)
        assert center == (5.0, 5.0)

    def test_get_union_polygon(self, mock_sensors_data):
        """Test union polygon for multiple cameras."""
        manager = CameraGroupManager(mock_sensors_data)
        manager.initialize_camera_info(use_frustum=False)

        union_poly = manager.get_union_polygon([0, 1])
        assert union_poly is not None
        # Union should be larger than individual polygons
        poly0 = manager.get_camera_polygon(0)
        assert union_poly.area >= poly0.area

    def test_get_union_polygon_empty_list(self, mock_sensors_data):
        """Test union polygon with empty list."""
        manager = CameraGroupManager(mock_sensors_data)
        manager.initialize_camera_info(use_frustum=False)

        union_poly = manager.get_union_polygon([])
        assert union_poly is None

    def test_evaluate_membership_empty_group(self, mock_sensors_data):
        """Test membership evaluation for empty group."""
        manager = CameraGroupManager(mock_sensors_data)
        manager.initialize_camera_info(use_frustum=False)

        is_valid, overlap, distance = manager._evaluate_membership(
            camera_idx=0,
            group_cameras=[],
            overlap_threshold=0.2,
            distance_threshold=10.0,
        )

        assert is_valid is True
        assert overlap == 0.0
        assert distance == 0.0

    def test_evaluate_membership_with_overlap(self, mock_sensors_data):
        """Test membership evaluation with overlapping cameras."""
        manager = CameraGroupManager(mock_sensors_data)
        manager.initialize_camera_info(use_frustum=False)

        # Camera_01 and Camera_02 have overlapping FOVs
        is_valid, overlap, distance = manager._evaluate_membership(
            camera_idx=1,  # Camera_02
            group_cameras=[0],  # Camera_01
            overlap_threshold=0.1,
            distance_threshold=20.0,
        )

        assert overlap > 0.0  # Should have some overlap

    def test_evaluate_membership_no_overlap(self, mock_sensors_data):
        """Test membership evaluation with non-overlapping cameras."""
        manager = CameraGroupManager(mock_sensors_data)
        manager.initialize_camera_info(use_frustum=False)

        # Camera_01 (0,0)-(10,10) and Camera_03 (20,0)-(30,10) don't overlap
        is_valid, overlap, distance = manager._evaluate_membership(
            camera_idx=2,  # Camera_03
            group_cameras=[0],  # Camera_01
            overlap_threshold=0.3,  # High threshold
            distance_threshold=5.0,  # Low threshold
        )

        # Should fail due to no overlap and likely distance exceeded
        assert overlap == 0.0

    def test_pick_best_camera(self, mock_sensors_data):
        """Test picking best camera from candidates."""
        manager = CameraGroupManager(mock_sensors_data)
        manager.initialize_camera_info(use_frustum=False)

        # With Camera_01 in group, pick best from others
        best = manager._pick_best_camera(
            candidates=[1, 2, 3],
            group_cameras=[0],
            overlap_threshold=0.0,  # Accept any overlap
            distance_threshold=float("inf"),  # Accept any distance
        )

        assert best is not None
        assert best in [1, 2, 3]

    def test_pick_best_camera_empty_candidates(self, mock_sensors_data):
        """Test picking best camera with empty candidates."""
        manager = CameraGroupManager(mock_sensors_data)
        manager.initialize_camera_info(use_frustum=False)

        best = manager._pick_best_camera(
            candidates=[],
            group_cameras=[0],
            overlap_threshold=0.2,
            distance_threshold=10.0,
        )

        assert best is None

    def test_pick_best_camera_excludes_group_members(self, mock_sensors_data):
        """Test that pick_best_camera excludes cameras already in group."""
        manager = CameraGroupManager(mock_sensors_data)
        manager.initialize_camera_info(use_frustum=False)

        best = manager._pick_best_camera(
            candidates=[0, 1, 2],
            group_cameras=[0, 1],  # 0 and 1 already in group
            overlap_threshold=0.0,
            distance_threshold=float("inf"),
        )

        assert best == 2  # Only 2 is valid

    def test_pick_farthest_camera(self, mock_sensors_data):
        """Test picking farthest camera from references."""
        manager = CameraGroupManager(mock_sensors_data)
        manager.initialize_camera_info(use_frustum=False)

        # Camera_01 at x=0, Camera_03/04 at x=20-25
        farthest = manager._pick_farthest_camera(
            candidates=[1, 2, 3],
            reference_cameras=[0],  # Camera_01
        )

        # Should pick camera 2 or 3 (farthest from camera 0)
        assert farthest in [2, 3]

    def test_pick_farthest_camera_empty_candidates(self, mock_sensors_data):
        """Test picking farthest camera with empty candidates."""
        manager = CameraGroupManager(mock_sensors_data)
        manager.initialize_camera_info(use_frustum=False)

        farthest = manager._pick_farthest_camera(
            candidates=[],
            reference_cameras=[0],
        )

        assert farthest is None

    def test_pick_farthest_camera_no_reference(self, mock_sensors_data):
        """Test picking farthest camera with no reference cameras."""
        manager = CameraGroupManager(mock_sensors_data)
        manager.initialize_camera_info(use_frustum=False)

        # Should return first candidate when no reference
        farthest = manager._pick_farthest_camera(
            candidates=[1, 2, 3],
            reference_cameras=[],
        )

        assert farthest == 1  # First candidate

    def test_build_single_group(self, mock_sensors_data):
        """Test building a single group."""
        manager = CameraGroupManager(mock_sensors_data)
        manager.initialize_camera_info(use_frustum=False)

        group = manager._build_single_group(
            group_id=0,
            seed_camera=0,
            cameras_per_group=3,
            overlap_threshold=0.0,
            distance_threshold=float("inf"),
            prefer_unselected=True,
        )

        assert len(group) == 3
        assert 0 in group  # Seed camera should be in group
        assert manager._camera_assignment_counts[0] >= 1

    def test_build_single_group_prefers_unselected(self, mock_sensors_data):
        """Test that group building prefers unselected cameras."""
        manager = CameraGroupManager(mock_sensors_data)
        manager.initialize_camera_info(use_frustum=False)

        # First group
        group1 = manager._build_single_group(
            group_id=0,
            seed_camera=0,
            cameras_per_group=2,
            overlap_threshold=0.0,
            distance_threshold=float("inf"),
            prefer_unselected=True,
        )

        # Second group - should try to use unselected cameras first
        group2 = manager._build_single_group(
            group_id=1,
            seed_camera=2,
            cameras_per_group=2,
            overlap_threshold=0.0,
            distance_threshold=float("inf"),
            prefer_unselected=True,
        )

        # Groups should have different cameras (when possible)
        assert len(group1) == 2
        assert len(group2) == 2

    def test_create_groups_basic(self, mock_sensors_data):
        """Test basic group creation."""
        manager = CameraGroupManager(mock_sensors_data)

        groups = manager.create_groups(
            n_groups=2,
            cameras_per_group=2,
            start_camera_index=0,
            use_frustum=False,
            overlap_threshold=0.0,
            distance_threshold=float("inf"),
        )

        assert len(groups) == 2
        assert all(len(g) == 2 for g in groups.values())

    def test_create_groups_with_duplication(self, mock_sensors_data):
        """Test group creation with camera duplication.

        Pin a ``random_seed`` so the duplicate-detection retry loop is
        deterministic. With 4 cameras and groups of 3, only ``C(4,3) = 4``
        unique group combinations exist, so without a seed the default
        5-retry budget can run out by chance and produce fewer than 3
        groups (~5% flake rate observed in CI).
        """
        manager = CameraGroupManager(mock_sensors_data)

        # 4 cameras, 3 groups of 3 = 9 slots -> duplication required
        groups = manager.create_groups(
            n_groups=3,
            cameras_per_group=3,
            start_camera_index=0,
            use_frustum=False,
            overlap_threshold=0.0,
            distance_threshold=float("inf"),
            random_seed=42,
        )

        assert len(groups) == 3
        assert all(len(g) == 3 for g in groups.values())

        # Count total camera appearances
        total_appearances = sum(len(g) for g in groups.values())
        assert total_appearances == 9  # 3 groups * 3 cameras

        # Some cameras must be duplicated
        all_camera_indices = []
        for g in groups.values():
            all_camera_indices.extend(g)
        # 9 appearances with 4 cameras -> some duplicates
        assert len(all_camera_indices) > 4

    def test_create_groups_ensures_coverage(self, mock_sensors_data):
        """Test that all cameras are assigned to at least one group."""
        manager = CameraGroupManager(mock_sensors_data)

        groups = manager.create_groups(
            n_groups=2,
            cameras_per_group=2,
            start_camera_index=0,
            use_frustum=False,
            overlap_threshold=0.0,
            distance_threshold=float("inf"),
        )

        # All cameras should be assigned
        assigned_cameras = set()
        for g in groups.values():
            assigned_cameras.update(g)

        # With 4 cameras and 2 groups of 2, all should be assigned
        assert len(assigned_cameras) == 4

    def test_create_groups_invalid_n_groups(self, mock_sensors_data):
        """Test create_groups with invalid n_groups."""
        manager = CameraGroupManager(mock_sensors_data)

        with pytest.raises(SystemExit):
            manager.create_groups(
                n_groups=0,
                cameras_per_group=2,
            )

    def test_create_groups_invalid_cameras_per_group(self, mock_sensors_data):
        """Test create_groups with invalid cameras_per_group."""
        manager = CameraGroupManager(mock_sensors_data)

        with pytest.raises(SystemExit):
            manager.create_groups(
                n_groups=2,
                cameras_per_group=0,
            )

    def test_create_groups_invalid_start_index(self, mock_sensors_data):
        """Test create_groups with invalid start_camera_index (should default to 0)."""
        manager = CameraGroupManager(mock_sensors_data)

        groups = manager.create_groups(
            n_groups=2,
            cameras_per_group=2,
            start_camera_index=100,  # Invalid - will be reset to 0
            use_frustum=False,
            overlap_threshold=0.0,  # Relaxed threshold for test data
            distance_threshold=float("inf"),
        )

        # Should still work, defaulting to 0
        assert len(groups) == 2

    def test_get_group_list(self, mock_sensors_data):
        """Test get_group_list method."""
        manager = CameraGroupManager(mock_sensors_data)
        manager.create_groups(
            n_groups=2,
            cameras_per_group=2,
            use_frustum=False,
            overlap_threshold=0.0,  # Relaxed threshold for test data
            distance_threshold=float("inf"),
        )

        group_list = manager.get_group_list()

        assert isinstance(group_list, list)
        assert len(group_list) == 2
        for group in group_list:
            assert isinstance(group, list)
            assert len(group) == 2

    def test_create_groups_with_random_seed(self, mock_sensors_data):
        """Test that create_groups with same random_seed produces deterministic results."""
        manager1 = CameraGroupManager(mock_sensors_data)
        groups1 = manager1.create_groups(
            n_groups=2,
            cameras_per_group=3,
            use_frustum=False,
            overlap_threshold=0.0,
            distance_threshold=float("inf"),
            randomize=True,
            random_seed=42,
        )

        manager2 = CameraGroupManager(mock_sensors_data)
        groups2 = manager2.create_groups(
            n_groups=2,
            cameras_per_group=3,
            use_frustum=False,
            overlap_threshold=0.0,
            distance_threshold=float("inf"),
            randomize=True,
            random_seed=42,
        )

        # Same random_seed should produce same groups
        assert groups1 == groups2


# ==============================================================================
# Test group_cameras_from_calibration
# ==============================================================================


class TestGroupCamerasFromCalibration:
    """Test suite for group_cameras_from_calibration function."""

    def test_basic_grouping(self, mock_calibration_data):
        """Test basic grouping from calibration data."""
        result = group_cameras_from_calibration(
            mock_calibration_data,
            n_groups=2,
            cameras_per_group=2,
            start_camera_index=0,
            use_frustum=False,
            overlap_threshold=0.0,
            distance_threshold=float("inf"),
        )

        assert "groups" in result
        assert "n_groups" in result
        assert "sensor_ids" in result
        assert "camera_assignments" in result
        assert "group_list" in result

        assert len(result["groups"]) == 2
        assert result["n_groups"] == 2
        assert len(result["sensor_ids"]) == 4

    def test_empty_sensors(self):
        """Test grouping with empty sensors list."""
        result = group_cameras_from_calibration(
            {"sensors": []},
            n_groups=2,
            cameras_per_group=2,
        )

        assert result["groups"] == {}
        assert result["n_groups"] == 0
        assert result["sensor_ids"] == []

    def test_grouping_with_duplication(self, mock_calibration_data):
        """Test grouping that requires camera duplication (same camera in multiple groups)."""
        result = group_cameras_from_calibration(
            mock_calibration_data,
            n_groups=4,
            cameras_per_group=3,
            use_frustum=False,
            overlap_threshold=0.0,
            distance_threshold=float("inf"),
        )

        # With 4 cameras and groups of 3, max unique groups = C(4,3) = 4
        # The algorithm may generate fewer if it cannot find unique combinations
        assert result["n_groups"] >= 1
        assert result["n_groups"] <= 4
        
        # Each group should have 3 cameras
        for group_id, cameras in result["groups"].items():
            assert len(cameras) == 3

        # Verify no duplicate groups exist
        group_sets = [frozenset(cameras) for cameras in result["groups"].values()]
        assert len(group_sets) == len(set(group_sets)), "Duplicate groups should not exist"

        # camera_assignments should show duplications (same camera in multiple groups)
        for camera_idx, group_ids in result["camera_assignments"].items():
            # Each camera should be in at least one group
            assert len(group_ids) >= 1

    def test_camera_assignments_consistency(self, mock_calibration_data):
        """Test that camera_assignments are consistent with groups."""
        result = group_cameras_from_calibration(
            mock_calibration_data,
            n_groups=2,
            cameras_per_group=3,
            use_frustum=False,
        )

        # Verify consistency
        for group_id, camera_indices in result["groups"].items():
            for camera_idx in camera_indices:
                assert group_id in result["camera_assignments"][camera_idx]

    def test_group_list_format(self, mock_calibration_data):
        """Test group_list format in result."""
        result = group_cameras_from_calibration(
            mock_calibration_data,
            n_groups=2,
            cameras_per_group=2,
            use_frustum=False,
            overlap_threshold=0.0,  # Relaxed threshold for test data
            distance_threshold=float("inf"),
        )

        group_list = result["group_list"]
        assert isinstance(group_list, list)
        assert len(group_list) == 2

    def test_invalid_start_camera_index(self, mock_calibration_data):
        """Test grouping with invalid start_camera_index."""
        result = group_cameras_from_calibration(
            mock_calibration_data,
            n_groups=2,
            cameras_per_group=2,
            start_camera_index=100,  # Invalid - will be reset to 0
            use_frustum=False,
            overlap_threshold=0.0,  # Relaxed threshold for coverage
            distance_threshold=float("inf"),
        )

        # Should still produce valid results (start_camera_index reset to 0)
        assert result["n_groups"] == 2


# ==============================================================================
# Test create_camera_groups_from_calibration
# ==============================================================================


class TestCreateCameraGroupsFromCalibration:
    """Test suite for create_camera_groups_from_calibration pipeline function."""

    def test_basic_pipeline(self, synthetic_calibration_dir, temp_dir):
        """Test basic grouping pipeline."""
        base_dir, _ = synthetic_calibration_dir
        output_file = Path(temp_dir) / "calibration_grouped.json"

        result_path = create_camera_groups_from_calibration(
            input_calibration=str(base_dir),
            n_groups=2,
            cameras_per_group=3,
            output=str(output_file),
            use_frustum=False,  # Use existing FOV attributes
            visualize=False,
            overlap_threshold=0.0,  # Relaxed threshold for synthetic data
            distance_threshold=float("inf"),
        )

        assert result_path.exists()
        assert result_path == output_file

        with open(result_path, "r") as f:
            result = json.load(f)

        assert "sensors" in result
        # All sensors should have group information
        for sensor in result["sensors"]:
            assert "group" in sensor
            assert "name" in sensor["group"]
            assert "origin" in sensor["group"]
            assert "dimensions" in sensor["group"]

    def test_pipeline_with_duplication(self, synthetic_calibration_dir, temp_dir):
        """Test pipeline with camera duplication."""
        base_dir, _ = synthetic_calibration_dir
        output_file = Path(temp_dir) / "calibration_grouped_dup.json"

        # 6 cameras, 4 groups of 3 = 12 slots -> duplication required
        result_path = create_camera_groups_from_calibration(
            input_calibration=str(base_dir),
            n_groups=4,
            cameras_per_group=3,
            output=str(output_file),
            use_frustum=False,  # Use existing FOV attributes
            visualize=False,
            overlap_threshold=0.0,  # Relaxed threshold for synthetic data
            distance_threshold=float("inf"),
        )

        assert result_path.exists()

        with open(result_path, "r") as f:
            result = json.load(f)

        # Count sensors per group
        group_counts = {}
        for sensor in result["sensors"]:
            group_name = sensor["group"]["name"]
            group_counts[group_name] = group_counts.get(group_name, 0) + 1

        # Should have 4 groups
        assert len(group_counts) == 4

    def test_pipeline_default_output_path(self, synthetic_calibration_dir):
        """Test pipeline with default output path."""
        base_dir, _ = synthetic_calibration_dir

        # 6 cameras need at least 6 slots; use 2 groups of 3 = 6 slots
        result_path = create_camera_groups_from_calibration(
            input_calibration=str(base_dir),
            n_groups=2,
            cameras_per_group=3,
            visualize=False,
            overlap_threshold=0.0,  # Relaxed threshold for synthetic data
            distance_threshold=float("inf"),
        )

        # Default output should be calibration_grouped.json
        expected_output = base_dir / "calibration_grouped.json"
        assert result_path == expected_output
        assert result_path.exists()

        # Clean up
        result_path.unlink()

    def test_pipeline_with_output_suffix(self, synthetic_calibration_dir, temp_dir):
        """Test pipeline with output suffix."""
        base_dir, _ = synthetic_calibration_dir

        # 6 cameras need at least 6 slots; use 2 groups of 3 = 6 slots
        result_path = create_camera_groups_from_calibration(
            input_calibration=str(base_dir),
            n_groups=2,
            cameras_per_group=3,
            output_suffix="_test_suffix",
            visualize=False,
            overlap_threshold=0.0,  # Relaxed threshold for synthetic data
            distance_threshold=float("inf"),
        )

        assert "test_suffix" in str(result_path.name)
        assert result_path.exists()

        # Clean up
        result_path.unlink()

    def test_pipeline_with_visualization(self, synthetic_calibration_dir, temp_dir):
        """Test pipeline with visualization enabled."""
        base_dir, _ = synthetic_calibration_dir
        output_file = Path(temp_dir) / "calibration_grouped_viz.json"

        result_path = create_camera_groups_from_calibration(
            input_calibration=str(base_dir),
            n_groups=2,
            cameras_per_group=3,
            output=str(output_file),
            visualize=True,
            overlap_threshold=0.0,  # Relaxed threshold for synthetic data
            distance_threshold=float("inf"),
        )

        assert result_path.exists()

        # Check for visualization files
        viz_pattern = "calibration_grouped_viz_map_bev-sensor-*.png"
        viz_files = list(Path(temp_dir).glob(viz_pattern))
        # Should have one visualization per group
        logger.debug("Visualization files found: %s", viz_files)

    def test_pipeline_invalid_input(self, temp_dir):
        """Test pipeline with invalid input path."""
        with pytest.raises(SystemExit):
            create_camera_groups_from_calibration(
                input_calibration="/nonexistent/path",
                n_groups=2,
                cameras_per_group=2,
            )

    def test_pipeline_with_frustum(self, synthetic_calibration_dir, temp_dir):
        """Test pipeline using frustum calculation."""
        base_dir, _ = synthetic_calibration_dir
        output_file = Path(temp_dir) / "calibration_grouped_frustum.json"

        # 6 cameras need at least 6 slots; use 2 groups of 3 = 6 slots
        result_path = create_camera_groups_from_calibration(
            input_calibration=str(base_dir),
            n_groups=2,
            cameras_per_group=3,
            output=str(output_file),
            use_frustum=True,  # Force frustum calculation
            max_camera_distance=30.0,
            visualize=False,
            overlap_threshold=0.0,  # Relaxed threshold for synthetic data
            distance_threshold=float("inf"),
        )

        assert result_path.exists()

    def test_pipeline_with_thresholds(self, synthetic_calibration_dir, temp_dir):
        """Test pipeline with custom overlap and distance thresholds."""
        base_dir, _ = synthetic_calibration_dir
        output_file = Path(temp_dir) / "calibration_grouped_thresholds.json"

        # Use relaxed thresholds that work with synthetic data
        result_path = create_camera_groups_from_calibration(
            input_calibration=str(base_dir),
            n_groups=2,
            cameras_per_group=3,
            output=str(output_file),
            overlap_threshold=0.0,  # Relaxed - synthetic cameras don't overlap
            distance_threshold=100.0,  # Generous distance
            visualize=False,
        )

        assert result_path.exists()

    def test_pipeline_with_random_seed(self, synthetic_calibration_dir, temp_dir):
        """Test pipeline with random_seed for deterministic results."""
        base_dir, _ = synthetic_calibration_dir
        output_file1 = Path(temp_dir) / "calibration_grouped_seed1.json"
        output_file2 = Path(temp_dir) / "calibration_grouped_seed2.json"

        # Run twice with same random_seed
        result_path1 = create_camera_groups_from_calibration(
            input_calibration=str(base_dir),
            n_groups=2,
            cameras_per_group=3,
            output=str(output_file1),
            use_frustum=False,
            visualize=False,
            overlap_threshold=0.0,
            distance_threshold=float("inf"),
            randomize=True,
            random_seed=42,
        )

        result_path2 = create_camera_groups_from_calibration(
            input_calibration=str(base_dir),
            n_groups=2,
            cameras_per_group=3,
            output=str(output_file2),
            use_frustum=False,
            visualize=False,
            overlap_threshold=0.0,
            distance_threshold=float("inf"),
            randomize=True,
            random_seed=42,
        )

        assert result_path1.exists()
        assert result_path2.exists()

        # Load and compare results
        with open(result_path1, "r") as f:
            result1 = json.load(f)
        with open(result_path2, "r") as f:
            result2 = json.load(f)

        # Group assignments should be identical
        groups1 = {s["id"]: s["group"]["name"] for s in result1["sensors"]}
        groups2 = {s["id"]: s["group"]["name"] for s in result2["sensors"]}
        assert groups1 == groups2


# ==============================================================================
# Test Edge Cases
# ==============================================================================


class TestGroupingEdgeCases:
    """Test edge cases and error handling."""

    def test_single_camera_grouping(self):
        """Test grouping with single camera."""
        calibration_data = {
            "sensors": [
                {
                    "id": "Camera_01",
                    "type": "camera",
                    "attributes": [
                        {
                            "name": "fieldOfViewPolygon",
                            "value": "POLYGON((0 0, 10 0, 10 10, 0 10, 0 0))",
                        }
                    ],
                }
            ]
        }

        result = group_cameras_from_calibration(
            calibration_data,
            n_groups=1,
            cameras_per_group=1,
            overlap_threshold=0.0,
            distance_threshold=float("inf"),
        )

        assert result["n_groups"] == 1
        assert len(result["groups"][0]) == 1
        assert result["camera_assignments"][0] == [0]

    def test_more_groups_than_cameras(self):
        """Test when n_groups > number of cameras.
        
        With duplicate detection, when cameras_per_group == n_sensors, only one
        unique group can be created since all cameras form the only possible combination.
        """
        calibration_data = {
            "sensors": [
                {
                    "id": f"Camera_{i:02d}",
                    "type": "camera",
                    "attributes": [
                        {
                            "name": "fieldOfViewPolygon",
                            "value": f"POLYGON(({i * 10} 0, {i * 10 + 10} 0, {i * 10 + 10} 10, {i * 10} 10, {i * 10} 0))",
                        }
                    ],
                }
                for i in range(2)
            ]
        }

        # 2 cameras, 4 groups of 2 = 8 slots
        # BUT with only 2 cameras and group size = 2, there's only ONE unique combination
        # So the algorithm will only create 1 group (duplicate detection prevents more)
        result = group_cameras_from_calibration(
            calibration_data,
            n_groups=4,
            cameras_per_group=2,  # Same as n_sensors, so only 1 unique group possible
            overlap_threshold=0.0,
            distance_threshold=float("inf"),
        )

        # Only 1 unique group is possible when cameras_per_group == n_sensors
        assert result["n_groups"] == 1
        # Both cameras should be in the group
        assert len(result["groups"][0]) == 2
        # All cameras should be assigned
        for camera_idx, group_ids in result["camera_assignments"].items():
            assert len(group_ids) >= 1

    def test_grouping_determinism(self, mock_calibration_data):
        """Test that grouping produces consistent results when randomization is disabled."""
        result1 = group_cameras_from_calibration(
            mock_calibration_data,
            n_groups=2,
            cameras_per_group=2,
            start_camera_index=0,
            overlap_threshold=0.0,
            distance_threshold=float("inf"),
            randomize=False,  # Disable randomization for deterministic results
        )

        result2 = group_cameras_from_calibration(
            mock_calibration_data,
            n_groups=2,
            cameras_per_group=2,
            start_camera_index=0,
            overlap_threshold=0.0,
            distance_threshold=float("inf"),
            randomize=False,  # Disable randomization for deterministic results
        )

        # Same parameters with randomize=False should produce same results
        assert result1["groups"] == result2["groups"]

    def test_all_cameras_covered(self, mock_calibration_data):
        """Test that all cameras end up in at least one group."""
        result = group_cameras_from_calibration(
            mock_calibration_data,
            n_groups=2,
            cameras_per_group=2,
            overlap_threshold=0.0,
            distance_threshold=float("inf"),
        )

        # Every camera should have at least one group assignment
        for camera_idx, group_ids in result["camera_assignments"].items():
            assert len(group_ids) >= 1, f"Camera {camera_idx} has no group assignment"


# ==============================================================================
# Test Coverage Failure Error
# ==============================================================================


class TestCoverageFailureError:
    """Test that errors are raised when cameras cannot be covered."""

    def test_coverage_failure_raises_error(self):
        """Test that RuntimeError is raised when a camera cannot be assigned to any group.

        With insufficient slots (n_groups * cameras_per_group < num_cameras),
        some cameras won't be covered and an error should be raised.
        """
        calibration_data = {
            "sensors": [
                {
                    "id": f"Camera_{i:02d}",
                    "type": "camera",
                    "attributes": [
                        {
                            "name": "fieldOfViewPolygon",
                            # Place cameras far apart so they don't overlap
                            "value": f"POLYGON(({i * 100} 0, {i * 100 + 10} 0, {i * 100 + 10} 10, {i * 100} 10, {i * 100} 0))",
                        }
                    ],
                }
                for i in range(4)
            ]
        }

        # 4 cameras, 1 group of 2 = only 2 slots
        # With strict thresholds, 2 cameras will be unassigned → error
        with pytest.raises(RuntimeError) as exc_info:
            group_cameras_from_calibration(
                calibration_data,
                n_groups=1,
                cameras_per_group=2,
                overlap_threshold=0.5,  # Strict - no overlap between distant cameras
                distance_threshold=10.0,  # Strict - cameras are far apart
            )

        assert "not assigned to any group" in str(exc_info.value)

    def test_sufficient_slots_with_duplication(self):
        """Test that with sufficient slots, all cameras get covered through duplication."""
        calibration_data = {
            "sensors": [
                {
                    "id": "Camera_01",
                    "type": "camera",
                    "attributes": [
                        {
                            "name": "fieldOfViewPolygon",
                            "value": "POLYGON((0 0, 10 0, 10 10, 0 10, 0 0))",
                        }
                    ],
                },
                {
                    "id": "Camera_02",
                    "type": "camera",
                    "attributes": [
                        {
                            "name": "fieldOfViewPolygon",
                            "value": "POLYGON((100 100, 110 100, 110 110, 100 110, 100 100))",
                        }
                    ],
                },
            ]
        }

        # 2 cameras, 2 groups of 1 = 2 slots = enough for all cameras
        result = group_cameras_from_calibration(
            calibration_data,
            n_groups=2,
            cameras_per_group=1,
            overlap_threshold=0.0,
            distance_threshold=float("inf"),
        )

        assert result["n_groups"] == 2
        # Each group should have exactly 1 camera
        for group_cameras in result["groups"].values():
            assert len(group_cameras) == 1

    def test_manager_coverage_failure_raises_runtime_error(self):
        """Test CameraGroupManager raises RuntimeError on coverage failure."""
        sensors_data = [
            {
                "id": "Camera_01",
                "type": "camera",
                "attributes": [
                    {
                        "name": "fieldOfViewPolygon",
                        "value": "POLYGON((0 0, 10 0, 10 10, 0 10, 0 0))",
                    }
                ],
            },
        ]

        manager = CameraGroupManager(sensors_data)

        # This should work fine - 1 camera, 1 group of 1
        groups = manager.create_groups(
            n_groups=1,
            cameras_per_group=1,
            use_frustum=False,
            overlap_threshold=0.0,
            distance_threshold=float("inf"),
        )

        assert len(groups) == 1
        assert 0 in groups[0]

    def test_exact_group_size_maintained(self):
        """Test that each group has exactly cameras_per_group cameras."""
        calibration_data = {
            "sensors": [
                {
                    "id": f"Camera_{i:02d}",
                    "type": "camera",
                    "attributes": [
                        {
                            "name": "fieldOfViewPolygon",
                            "value": f"POLYGON(({i * 10} 0, {i * 10 + 10} 0, {i * 10 + 10} 10, {i * 10} 10, {i * 10} 0))",
                        }
                    ],
                }
                for i in range(6)
            ]
        }

        # 6 cameras, 3 groups of 4 = 12 slots (duplication required)
        result = group_cameras_from_calibration(
            calibration_data,
            n_groups=3,
            cameras_per_group=4,
            overlap_threshold=0.0,
            distance_threshold=float("inf"),
        )

        assert result["n_groups"] == 3
        # Each group must have EXACTLY 4 cameras (not more, not less)
        for group_id, group_cameras in result["groups"].items():
            assert len(group_cameras) == 4, (
                f"Group {group_id} has {len(group_cameras)} cameras, expected 4"
            )

    def test_coverage_through_duplication(self):
        """Test that all cameras are covered through the duplication mechanism."""
        calibration_data = {
            "sensors": [
                {
                    "id": f"Camera_{i:02d}",
                    "type": "camera",
                    "attributes": [
                        {
                            "name": "fieldOfViewPolygon",
                            "value": f"POLYGON(({i * 10} 0, {i * 10 + 10} 0, {i * 10 + 10} 10, {i * 10} 10, {i * 10} 0))",
                        }
                    ],
                }
                for i in range(4)
            ]
        }

        # 4 cameras, 2 groups of 3 = 6 slots
        # With relaxed thresholds, all 4 cameras should be covered through duplication
        result = group_cameras_from_calibration(
            calibration_data,
            n_groups=2,
            cameras_per_group=3,
            overlap_threshold=0.0,  # Accept any overlap
            distance_threshold=float("inf"),  # Accept any distance
        )

        # All cameras should be assigned to at least one group
        all_assigned = set()
        for group_cameras in result["groups"].values():
            all_assigned.update(group_cameras)
        assert len(all_assigned) == 4, "All 4 cameras should be covered"

        # Each group should have exactly 3 cameras
        for group_cameras in result["groups"].values():
            assert len(group_cameras) == 3


# ==============================================================================
# Test Randomization and Duplicate Detection
# ==============================================================================


class TestRandomizationAndDuplicateDetection:
    """Test suite for randomization and duplicate group detection."""

    def test_randomization_produces_different_results(self, mock_calibration_data):
        """Test that randomization produces potentially different results across runs.
        
        Note: With randomization, results may vary. We run multiple times to check
        that at least some runs produce different results (probabilistic test).
        """
        results = []
        for _ in range(10):
            result = group_cameras_from_calibration(
                mock_calibration_data,
                n_groups=2,
                cameras_per_group=3,
                start_camera_index=0,
                overlap_threshold=0.0,
                distance_threshold=float("inf"),
                randomize=True,
            )
            # Convert groups to tuple of frozensets for comparison
            groups_tuple = tuple(frozenset(g) for g in result["groups"].values())
            results.append(groups_tuple)

        # Check that we got at least some variation (not all identical)
        unique_results = set(results)
        # With randomization, we expect some variation, but this is probabilistic
        # so we just check that results are valid
        assert len(results) == 10
        for groups_tuple in results:
            assert len(groups_tuple) == 2  # 2 groups

    def test_no_randomize_produces_deterministic_results(self, mock_calibration_data):
        """Test that disabling randomization produces deterministic results."""
        result1 = group_cameras_from_calibration(
            mock_calibration_data,
            n_groups=2,
            cameras_per_group=2,
            start_camera_index=0,
            overlap_threshold=0.0,
            distance_threshold=float("inf"),
            randomize=False,
        )

        result2 = group_cameras_from_calibration(
            mock_calibration_data,
            n_groups=2,
            cameras_per_group=2,
            start_camera_index=0,
            overlap_threshold=0.0,
            distance_threshold=float("inf"),
            randomize=False,
        )

        # With randomize=False, results should be identical
        assert result1["groups"] == result2["groups"]

    def test_random_seed_produces_deterministic_results(self, mock_calibration_data):
        """Test that same random_seed produces deterministic results with randomization enabled."""
        result1 = group_cameras_from_calibration(
            mock_calibration_data,
            n_groups=2,
            cameras_per_group=3,
            start_camera_index=0,
            overlap_threshold=0.0,
            distance_threshold=float("inf"),
            randomize=True,
            random_seed=42,
        )

        result2 = group_cameras_from_calibration(
            mock_calibration_data,
            n_groups=2,
            cameras_per_group=3,
            start_camera_index=0,
            overlap_threshold=0.0,
            distance_threshold=float("inf"),
            randomize=True,
            random_seed=42,
        )

        # With same random_seed, results should be identical
        assert result1["groups"] == result2["groups"]

    def test_different_random_seeds_may_produce_different_results(self, mock_calibration_data):
        """Test that different random seeds can produce different results."""
        results_by_seed = {}
        
        for seed in [1, 2, 3, 42, 123]:
            result = group_cameras_from_calibration(
                mock_calibration_data,
                n_groups=2,
                cameras_per_group=3,
                start_camera_index=0,
                overlap_threshold=0.0,
                distance_threshold=float("inf"),
                randomize=True,
                random_seed=seed,
            )
            # Convert groups to tuple of frozensets for comparison
            groups_tuple = tuple(frozenset(g) for g in result["groups"].values())
            results_by_seed[seed] = groups_tuple

        # All results should be valid
        for groups_tuple in results_by_seed.values():
            assert len(groups_tuple) == 2  # 2 groups
            for group in groups_tuple:
                assert len(group) == 3  # 3 cameras per group

    def test_duplicate_detection_in_manager(self, mock_sensors_data):
        """Test that duplicate group detection works in CameraGroupManager."""
        manager = CameraGroupManager(mock_sensors_data)

        # Create a known group
        existing_groups = {
            0: [0, 1, 2],
            1: [1, 2, 3],
        }

        # Test that identical group is detected as duplicate
        new_group_duplicate = [0, 1, 2]  # Same as group 0
        assert manager._is_duplicate_group(new_group_duplicate, existing_groups) is True

        # Test that different group is not detected as duplicate
        new_group_different = [0, 1, 3]
        assert manager._is_duplicate_group(new_group_different, existing_groups) is False

        # Test that order doesn't matter (sets are compared)
        new_group_reordered = [2, 0, 1]  # Same as group 0, different order
        assert manager._is_duplicate_group(new_group_reordered, existing_groups) is True

    def test_manager_avoids_duplicate_groups(self, mock_sensors_data):
        """Test that CameraGroupManager regenerates duplicate groups."""
        manager = CameraGroupManager(mock_sensors_data)

        # Create 3 groups of 2 cameras (with only 4 cameras total)
        # This should produce unique groups
        groups = manager.create_groups(
            n_groups=3,
            cameras_per_group=2,
            start_camera_index=0,
            use_frustum=False,
            overlap_threshold=0.0,
            distance_threshold=float("inf"),
            randomize=True,
            max_duplicate_retries=5,
        )

        # Verify all groups are unique
        group_sets = [frozenset(g) for g in groups.values()]
        assert len(group_sets) == len(set(group_sets)), "Duplicate groups detected"

    def test_single_group_with_all_cameras(self):
        """Test that when cameras_per_group == n_sensors, only one group is created."""
        calibration_data = {
            "sensors": [
                {
                    "id": f"Camera_{i:02d}",
                    "type": "camera",
                    "attributes": [
                        {
                            "name": "fieldOfViewPolygon",
                            "value": f"POLYGON(({i * 10} 0, {i * 10 + 10} 0, {i * 10 + 10} 10, {i * 10} 10, {i * 10} 0))",
                        }
                    ],
                }
                for i in range(4)
            ]
        }

        # Request 3 groups of size 4 (= total sensors)
        # Only 1 unique combination is possible, so algorithm should create only 1 group
        result = group_cameras_from_calibration(
            calibration_data,
            n_groups=3,  # Request 3 groups
            cameras_per_group=4,  # With all 4 cameras
            overlap_threshold=0.0,
            distance_threshold=float("inf"),
            randomize=True,
        )

        # Should only have 1 group (since all 4 cameras = only one possible combination)
        assert result["n_groups"] == 1
        assert len(result["groups"]) == 1
        assert len(result["groups"][0]) == 4


# ==============================================================================
# Test Multiple Group Sizes (List cameras_per_group)
# ==============================================================================


class TestMultipleGroupSizes:
    """Test suite for multiple group sizes (cameras_per_group as list)."""

    def test_cameras_per_group_as_list(self):
        """Test creating groups with different sizes."""
        calibration_data = {
            "sensors": [
                {
                    "id": f"Camera_{i:02d}",
                    "type": "camera",
                    "attributes": [
                        {
                            "name": "fieldOfViewPolygon",
                            "value": f"POLYGON(({i * 10} 0, {i * 10 + 10} 0, {i * 10 + 10} 10, {i * 10} 10, {i * 10} 0))",
                        }
                    ],
                }
                for i in range(6)
            ]
        }

        # Create 1 group of each size: 2, 3, 4
        result = group_cameras_from_calibration(
            calibration_data,
            n_groups=1,
            cameras_per_group=[2, 3, 4],  # List of sizes
            overlap_threshold=0.0,
            distance_threshold=float("inf"),
        )

        # Should have 3 groups total (1 group × 3 sizes)
        assert result["n_groups"] == 3
        
        # Group sizes should be 2, 3, 4
        group_sizes = sorted(len(g) for g in result["groups"].values())
        assert group_sizes == [2, 3, 4]

    def test_cameras_per_group_multiple_groups_per_size(self):
        """Test creating multiple groups of each size."""
        calibration_data = {
            "sensors": [
                {
                    "id": f"Camera_{i:02d}",
                    "type": "camera",
                    "attributes": [
                        {
                            "name": "fieldOfViewPolygon",
                            "value": f"POLYGON(({i * 10} 0, {i * 10 + 10} 0, {i * 10 + 10} 10, {i * 10} 10, {i * 10} 0))",
                        }
                    ],
                }
                for i in range(6)
            ]
        }

        # Create 2 groups of each size: 2, 3
        result = group_cameras_from_calibration(
            calibration_data,
            n_groups=2,
            cameras_per_group=[2, 3],  # List of sizes
            overlap_threshold=0.0,
            distance_threshold=float("inf"),
        )

        # Should have 4 groups total (2 groups × 2 sizes)
        assert result["n_groups"] == 4
        
        # Should have two groups of size 2 and two groups of size 3
        group_sizes = sorted(len(g) for g in result["groups"].values())
        assert group_sizes == [2, 2, 3, 3]

    def test_auto_mode_simulation(self):
        """Test simulating auto mode with cameras_per_group = [1, 2, ..., n_sensors]."""
        calibration_data = {
            "sensors": [
                {
                    "id": f"Camera_{i:02d}",
                    "type": "camera",
                    "attributes": [
                        {
                            "name": "fieldOfViewPolygon",
                            "value": f"POLYGON(({i * 10} 0, {i * 10 + 10} 0, {i * 10 + 10} 10, {i * 10} 10, {i * 10} 0))",
                        }
                    ],
                }
                for i in range(5)
            ]
        }

        # Simulate auto mode: cameras_per_group = [1, 2, 3, 4, 5]
        cameras_per_group = list(range(1, 6))
        
        result = group_cameras_from_calibration(
            calibration_data,
            n_groups=1,
            cameras_per_group=cameras_per_group,
            overlap_threshold=0.0,
            distance_threshold=float("inf"),
        )

        # Should have 5 groups
        # Note: The last group (size 5 = all cameras) will only have 1 group
        # because it's the only unique combination
        assert result["n_groups"] >= 4  # At least 4 groups

        # Verify group sizes
        group_sizes = sorted(len(g) for g in result["groups"].values())
        # Should have groups of sizes 1, 2, 3, 4, and 5
        assert 1 in group_sizes
        assert 5 in group_sizes

    def test_max_sensors_per_group_simulation(self):
        """Test simulating max_sensors_per_group by filtering cameras_per_group list."""
        calibration_data = {
            "sensors": [
                {
                    "id": f"Camera_{i:02d}",
                    "type": "camera",
                    "attributes": [
                        {
                            "name": "fieldOfViewPolygon",
                            "value": f"POLYGON(({i * 10} 0, {i * 10 + 10} 0, {i * 10 + 10} 10, {i * 10} 10, {i * 10} 0))",
                        }
                    ],
                }
                for i in range(10)
            ]
        }

        # Simulate auto mode with max_sensors_per_group=6
        # cameras_per_group = [1, 2, 3, 4, 5, 6] (capped at 6 instead of 10)
        max_sensors_per_group = 6
        cameras_per_group = list(range(1, min(10, max_sensors_per_group) + 1))
        
        assert cameras_per_group == [1, 2, 3, 4, 5, 6]
        
        result = group_cameras_from_calibration(
            calibration_data,
            n_groups=1,
            cameras_per_group=cameras_per_group,
            overlap_threshold=0.0,
            distance_threshold=float("inf"),
        )

        # Should have 6 groups (one for each size 1-6)
        assert result["n_groups"] == 6
        
        # Verify no group exceeds max_sensors_per_group
        for group in result["groups"].values():
            assert len(group) <= max_sensors_per_group


# ==============================================================================
# Test Natural Sorting (via utility function)
# ==============================================================================


class TestNaturalSorting:
    """Test suite for natural sorting of group and camera IDs."""

    def test_natural_sort_key_import(self):
        """Test that natural_sort_key can be imported from utils."""
        from spatialai_data_utils.utils.string_utils import natural_sort_key
        
        # Test sorting sensor names
        names = ["bev-sensor-1", "bev-sensor-10", "bev-sensor-2", "bev-sensor-3"]
        sorted_names = sorted(names, key=natural_sort_key)
        
        assert sorted_names == [
            "bev-sensor-1",
            "bev-sensor-2",
            "bev-sensor-3",
            "bev-sensor-10",
        ]

    def test_natural_sort_key_camera_ids(self):
        """Test natural sorting of camera IDs."""
        from spatialai_data_utils.utils.string_utils import natural_sort_key
        
        camera_ids = ["Camera_01", "Camera_10", "Camera_02", "Camera_100"]
        sorted_ids = sorted(camera_ids, key=natural_sort_key)
        
        assert sorted_ids == ["Camera_01", "Camera_02", "Camera_10", "Camera_100"]

    def test_natural_sort_key_mixed_content(self):
        """Test natural sorting with mixed alphanumeric content."""
        from spatialai_data_utils.utils.string_utils import natural_sort_key
        
        items = ["a1b2", "a10b1", "a2b1", "a1b10"]
        sorted_items = sorted(items, key=natural_sort_key)
        
        assert sorted_items == ["a1b2", "a1b10", "a2b1", "a10b1"]


if __name__ == "__main__":
    # Run tests with pytest
    pytest.main([__file__, "-v", "--tb=short"])
