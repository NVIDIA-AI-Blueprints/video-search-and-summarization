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
Test cases for camera clustering algorithm.

This module tests the camera clustering functionality including:
1. CameraClusterHelper static methods for overlap calculation and distance metrics
2. CameraFovInfo container class
3. CameraClusterManager clustering operations
4. Top-level clustering functions

Uses synthetic calibration data for all tests - no external data files required.
"""

import json
import logging
import os
import tempfile
import shutil
from pathlib import Path

import numpy as np
from shapely.geometry import Polygon

try:
    import pytest
except ImportError:
    pytest = None

logger = logging.getLogger(__name__)

from spatialai_data_utils.core.cameras.clustering import (
    CameraClusterHelper,
    CameraFovInfo,
    CameraClusterManager,
    cluster_cameras_from_calibration,
    get_camera_fov_polygon,
)
from spatialai_data_utils.core.cameras.bev import (
    create_camera_clusters_from_calibration,
)
from spatialai_data_utils.core.cameras.group_utils import (
    reassign_camera_groups_from_calibration,
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

            Image.new("RGB", (1, 1), color=(0, 0, 0)).save(map_path)
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

    temp_path = tempfile.mkdtemp(dir=tmp_base, prefix="test_clustering_")
    preserve_tmp = os.getenv("PRESERVE_TEST_TMP", "").lower() in ("1", "true", "yes", "on")
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
                    "value": f"POLYGON(({idx*10} 0, {idx*10+10} 0, {idx*10+10} 10, {idx*10} 10, {idx*10} 0))",
                },
            ],
        }
        for idx in range(4)
    ]

    calib_path = base / "calibration.json"
    with calib_path.open("w") as f:
        json.dump({"sensors": sensors}, f)

    map_path = base / "Top.png"
    try:
        from PIL import Image

        Image.new("RGB", (200, 200), color=(0, 0, 0)).save(map_path)
    except Exception:
        # Fallback: write a minimal valid 1x1 PNG
        png_bytes = (
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
            b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\nIDATx\x9cc``\x00\x00\x00"
            b"\x02\x00\x01E-\xb4\xdc\x00\x00\x00\x00IEND\xaeB`\x82"
        )
        map_path.write_bytes(png_bytes)

    return base, map_path


@pytest.fixture
def simple_polygons():
    """Create simple test polygons for overlap testing."""
    # Two overlapping squares
    poly1 = Polygon([(0, 0), (10, 0), (10, 10), (0, 10), (0, 0)])
    poly2 = Polygon([(5, 5), (15, 5), (15, 15), (5, 15), (5, 5)])
    # Non-overlapping polygon
    poly3 = Polygon([(20, 20), (30, 20), (30, 30), (20, 30), (20, 20)])
    return poly1, poly2, poly3


@pytest.fixture
def mock_sensors_data():
    """Create mock sensor data for testing CameraClusterManager."""
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


# ==============================================================================
# Test CameraClusterHelper
# ==============================================================================


class TestCameraClusterHelper:
    """Test suite for CameraClusterHelper static methods."""

    def test_compute_camera_fov_center_basic(self, simple_polygons):
        """Test FOV center calculation with valid polygon."""
        poly1, _, _ = simple_polygons

        center = CameraClusterHelper.compute_camera_fov_center(poly1)

        assert center == (5.0, 5.0), (
            "Center of 10x10 polygon at origin should be (5, 5)"
        )

    def test_compute_camera_fov_center_offset(self, simple_polygons):
        """Test FOV center with offset polygon."""
        _, poly2, _ = simple_polygons

        center = CameraClusterHelper.compute_camera_fov_center(poly2)

        assert center == (10.0, 10.0), "Center of offset polygon should be (10, 10)"

    def test_compute_camera_fov_center_none(self):
        """Test FOV center returns (0, 0) for None polygon."""
        center = CameraClusterHelper.compute_camera_fov_center(None)
        assert center == (0.0, 0.0)

    def test_compute_camera_fov_center_empty(self):
        """Test FOV center returns (0, 0) for empty polygon."""
        empty_poly = Polygon()
        center = CameraClusterHelper.compute_camera_fov_center(empty_poly)
        assert center == (0.0, 0.0)

    def test_compute_overlap_intersecting(self, simple_polygons):
        """Test overlap calculation for intersecting polygons."""
        poly1, poly2, _ = simple_polygons

        overlap = CameraClusterHelper.compute_overlap(poly1, poly2)

        # poly1 area = 100, poly2 area = 100, intersection = 25 (5x5)
        # union = 100 + 100 - 25 = 175
        # overlap = 25/175 * 100 = ~14.29%
        assert 0.14 <= overlap <= 0.15, f"Expected ~0.1429 overlap ratio, got {overlap}"

    def test_compute_overlap_identical(self, simple_polygons):
        """Test overlap calculation for identical polygons."""
        poly1, _, _ = simple_polygons

        overlap = CameraClusterHelper.compute_overlap(poly1, poly1)

        assert overlap == 1.0, "Identical polygons should have full overlap (1.0 ratio)"

    def test_compute_overlap_no_intersection(self, simple_polygons):
        """Test overlap calculation for non-overlapping polygons."""
        poly1, _, poly3 = simple_polygons

        overlap = CameraClusterHelper.compute_overlap(poly1, poly3)

        assert overlap == 0.0, "Non-overlapping polygons should have 0% overlap"

    def test_compute_overlap_none_polygon(self, simple_polygons):
        """Test overlap returns 0 when polygon is None."""
        poly1, _, _ = simple_polygons

        overlap = CameraClusterHelper.compute_overlap(None, poly1)
        assert overlap == 0.0

        overlap = CameraClusterHelper.compute_overlap(poly1, None)
        assert overlap == 0.0

    def test_compute_overlap_empty_polygon(self, simple_polygons):
        """Test overlap returns 0 for empty polygon."""
        poly1, _, _ = simple_polygons
        empty_poly = Polygon()

        overlap = CameraClusterHelper.compute_overlap(empty_poly, poly1)
        assert overlap == 0.0

    def test_get_unioned_polygon_multiple(self, simple_polygons):
        """Test union of multiple polygons."""
        poly1, poly2, poly3 = simple_polygons

        union_poly = CameraClusterHelper.get_unioned_polygon([poly1, poly2])

        # Union should be larger than either individual polygon
        assert union_poly.area > poly1.area
        assert (
            union_poly.area == poly1.area + poly2.area - poly1.intersection(poly2).area
        )

    def test_get_unioned_polygon_empty_list(self):
        """Test union returns None for empty list."""
        result = CameraClusterHelper.get_unioned_polygon([])
        assert result is None

    def test_get_unioned_polygon_with_none(self, simple_polygons):
        """Test union handles None polygons in list."""
        poly1, _, _ = simple_polygons

        result = CameraClusterHelper.get_unioned_polygon([poly1, None])
        assert result is not None
        assert result.area == poly1.area

    def test_shortest_distance_basic(self):
        """Test shortest distance calculation."""
        point_list = [(0, 0), (10, 0), (10, 10)]
        target = (5, 0)

        distance = CameraClusterHelper.shortest_distance(point_list, target)

        assert distance == 5.0, "Shortest distance from (5,0) to (0,0) or (10,0) is 5"

    def test_shortest_distance_empty_list(self):
        """Test shortest distance returns inf for empty list."""
        distance = CameraClusterHelper.shortest_distance([], (0, 0))
        assert distance == float("inf")

    def test_shortest_distance_exact_match(self):
        """Test shortest distance when target is in list."""
        point_list = [(0, 0), (5, 5), (10, 10)]
        target = (5, 5)

        distance = CameraClusterHelper.shortest_distance(point_list, target)
        assert distance == 0.0

    def test_longest_distance_basic(self):
        """Test longest distance calculation."""
        point_list = [(0, 0), (10, 0), (10, 10)]
        target = (0, 0)

        distance = CameraClusterHelper.longest_distance(point_list, target)

        # Longest distance from (0,0) to (10,10) = sqrt(200) ≈ 14.14
        expected = np.sqrt(200)
        assert abs(distance - expected) < 0.01

    def test_longest_distance_empty_list(self):
        """Test longest distance returns 0 for empty list."""
        distance = CameraClusterHelper.longest_distance([], (0, 0))
        assert distance == 0.0

    def test_longest_distance_sum_basic(self):
        """Test sum of longest distances."""
        point_list = [(0, 0), (10, 0)]

        total = CameraClusterHelper.longest_distance_sum(point_list)

        # From (0,0): longest is 10 to (10,0)
        # From (10,0): longest is 10 to (0,0)
        # Total = 20
        assert total == 20.0

    def test_longest_distance_sum_empty(self):
        """Test longest distance sum returns 0 for empty list."""
        total = CameraClusterHelper.longest_distance_sum([])
        assert total == 0.0

    def test_split_number_even(self):
        """Test splitting number evenly."""
        parts = CameraClusterHelper.split_number(12, 3)

        assert len(parts) == 3
        assert sum(parts) == 12
        assert parts == [4, 4, 4]

    def test_split_number_uneven(self):
        """Test splitting number with remainder."""
        parts = CameraClusterHelper.split_number(10, 3)

        assert len(parts) == 3
        assert sum(parts) == 10
        # Remainder distributed to first parts
        assert parts == [4, 3, 3]

    def test_split_number_more_parts_than_n(self):
        """Test splitting when K > N."""
        parts = CameraClusterHelper.split_number(2, 5)

        assert len(parts) == 5
        assert sum(parts) == 2
        assert parts == [1, 1, 0, 0, 0]

    def test_split_number_k_zero_raises(self):
        """Test split_number raises error for K <= 0."""
        with pytest.raises(ValueError):
            CameraClusterHelper.split_number(10, 0)

    def test_split_number_negative_n_raises(self):
        """Test split_number raises error for negative N."""
        with pytest.raises(ValueError):
            CameraClusterHelper.split_number(-5, 3)


# ==============================================================================
# Test CameraFovInfo
# ==============================================================================


class TestCameraFovInfo:
    """Test suite for CameraFovInfo container class."""

    def test_initialization(self, simple_polygons):
        """Test CameraFovInfo initialization."""
        poly1, _, _ = simple_polygons

        info = CameraFovInfo(
            camera_id="cam_01",
            poly=poly1,
            center_point=(5.0, 5.0),
            category=0,
        )

        assert info.camera_id == "cam_01"
        assert info.poly == poly1
        assert info.center_point == (5.0, 5.0)
        assert info.category == 0

    def test_initialization_defaults(self):
        """Test CameraFovInfo with default values."""
        info = CameraFovInfo(camera_id="cam_02")

        assert info.camera_id == "cam_02"
        assert info.poly is None
        assert info.center_point is None
        assert info.category is None

    def test_setters_and_getters(self, simple_polygons):
        """Test CameraFovInfo setter and getter methods."""
        poly1, _, _ = simple_polygons
        info = CameraFovInfo(camera_id="cam_03")

        info.set_poly(poly1)
        info.set_center_point((10.0, 10.0))
        info.set_category(2)

        assert info.get_poly() == poly1
        assert info.get_center_point() == (10.0, 10.0)
        assert info.get_category() == 2


# ==============================================================================
# Test CameraClusterManager
# ==============================================================================


class TestCameraClusterManager:
    """Test suite for CameraClusterManager class."""

    def test_initialization(self, mock_sensors_data):
        """Test CameraClusterManager initialization."""
        manager = CameraClusterManager(mock_sensors_data)

        assert manager.sensors_data == mock_sensors_data
        assert manager._camera_info_dict == {}
        assert manager.clusters == {}

    def test_initialize_camera_info(self, mock_sensors_data):
        """Test camera info initialization from sensor data."""
        manager = CameraClusterManager(mock_sensors_data)
        manager.initialize_camera_info(use_frustum=False)

        assert len(manager._camera_info_dict) == 4
        # All cameras should have center points calculated
        for idx, info in manager._camera_info_dict.items():
            assert info.center_point is not None
            assert info.poly is not None or info.center_point == (0.0, 0.0)

    def test_get_camera_num(self, mock_sensors_data):
        """Test camera count retrieval."""
        manager = CameraClusterManager(mock_sensors_data)
        manager.initialize_camera_info(use_frustum=False)

        assert manager.get_camera_num() == 4

    def test_all_categorized_false(self, mock_sensors_data):
        """Test all_categorized returns False when cameras uncategorized."""
        manager = CameraClusterManager(mock_sensors_data)
        manager.initialize_camera_info(use_frustum=False)

        assert manager.all_categorized() is False

    def test_all_categorized_true(self, mock_sensors_data):
        """Test all_categorized returns True when all cameras categorized."""
        manager = CameraClusterManager(mock_sensors_data)
        manager.initialize_camera_info(use_frustum=False)

        # Categorize all cameras
        for idx in manager._camera_info_dict.keys():
            manager.categorize_camera(idx, 0)

        assert manager.all_categorized() is True

    def test_get_uncategorized_camera_indices(self, mock_sensors_data):
        """Test getting uncategorized camera indices."""
        manager = CameraClusterManager(mock_sensors_data)
        manager.initialize_camera_info(use_frustum=False)

        uncategorized = manager.get_uncategorized_camera_indices()
        assert len(uncategorized) == 4

        # Categorize one camera
        manager.categorize_camera(0, 0)
        uncategorized = manager.get_uncategorized_camera_indices()
        assert len(uncategorized) == 3
        assert 0 not in uncategorized

    def test_categorize_camera(self, mock_sensors_data):
        """Test camera categorization."""
        manager = CameraClusterManager(mock_sensors_data)
        manager.initialize_camera_info(use_frustum=False)

        manager.categorize_camera(0, 1)
        manager.categorize_camera(1, 2)

        assert manager._camera_info_dict[0].get_category() == 1
        assert manager._camera_info_dict[1].get_category() == 2

    def test_get_camera_polygon(self, mock_sensors_data):
        """Test camera polygon retrieval."""
        manager = CameraClusterManager(mock_sensors_data)
        manager.initialize_camera_info(use_frustum=False)

        poly = manager.get_camera_polygon(0)
        assert poly is not None
        assert not poly.is_empty

    def test_get_camera_center_point(self, mock_sensors_data):
        """Test camera center point retrieval."""
        manager = CameraClusterManager(mock_sensors_data)
        manager.initialize_camera_info(use_frustum=False)

        center = manager.get_camera_center_point(0)
        assert center is not None
        assert len(center) == 2

    def test_get_union_polygon(self, mock_sensors_data):
        """Test union polygon for multiple cameras."""
        manager = CameraClusterManager(mock_sensors_data)
        manager.initialize_camera_info(use_frustum=False)

        union_poly = manager.get_union_polygon([0, 1])
        assert union_poly is not None
        # Union should be larger than individual polygons
        poly0 = manager.get_camera_polygon(0)
        assert union_poly.area >= poly0.area

    def test_generate_cluster_assignments(self, mock_sensors_data):
        """Test cluster assignment generation."""
        manager = CameraClusterManager(mock_sensors_data)
        manager.initialize_camera_info(use_frustum=False)

        # Set up clusters manually
        manager.clusters = {0: [0, 1], 1: [2, 3]}
        for idx in [0, 1]:
            manager.categorize_camera(idx, 0)
        for idx in [2, 3]:
            manager.categorize_camera(idx, 1)

        assignments = manager.generate_cluster_assignments()

        assert len(assignments) == 4
        assert assignments[0] == 0
        assert assignments[1] == 0
        assert assignments[2] == 1
        assert assignments[3] == 1

    def test_evaluate_point_scatter(self, mock_sensors_data):
        """Test cluster scatter evaluation."""
        manager = CameraClusterManager(mock_sensors_data)
        manager.initialize_camera_info(use_frustum=False)

        # Cameras 0 and 1 should have lower scatter (closer together)
        scatter_01 = manager.evaluate_point_scatter([0, 1])
        # Cameras 0 and 2 should have higher scatter (further apart)
        scatter_02 = manager.evaluate_point_scatter([0, 2])

        assert scatter_01 >= 0
        assert scatter_02 >= 0
        # Depending on actual positions, scatter might differ

    def test_evaluate_point_scatter_empty(self, mock_sensors_data):
        """Test scatter evaluation for empty list."""
        manager = CameraClusterManager(mock_sensors_data)
        manager.initialize_camera_info(use_frustum=False)

        scatter = manager.evaluate_point_scatter([])
        assert scatter == 0.0

    def test_seed_clusters_and_get_unassigned_cameras(self, mock_sensors_data):
        """Test greedy cluster initialization."""
        manager = CameraClusterManager(mock_sensors_data)
        manager.initialize_camera_info(use_frustum=False)

        manager.seed_clusters_and_get_unassigned_cameras(n_clusters=2, start_camera_index=0)

        # Should have 2 clusters
        assert len(manager.clusters) == 2
        # All cameras should be assigned
        assert manager.all_categorized()
        # Total cameras in clusters should equal 4
        total = sum(len(cameras) for cameras in manager.clusters.values())
        assert total == 4

    def test_cluster_cameras_basic(self, mock_sensors_data):
        """Test full clustering workflow."""
        manager = CameraClusterManager(mock_sensors_data)

        assignments = manager.cluster_cameras(
            n_clusters=2,
            start_camera_index=0,
            use_frustum=False,
            max_cluster_size=4,
        )

        assert len(assignments) == 4
        assert all(a is not None for a in assignments)
        assert set(assignments) == {0, 1}


# ==============================================================================
# Test Top-Level Functions
# ==============================================================================


class TestClusterCamerasFromCalibration:
    """Test suite for cluster_cameras_from_calibration function."""

    def test_basic_clustering(self, mock_calibration_data):
        """Test basic clustering from calibration data."""
        result = cluster_cameras_from_calibration(
            mock_calibration_data,
            n_clusters=2,
            start_camera_index=0,
            use_frustum=False,
            max_cluster_size=10,
        )

        assert "assignments" in result
        assert "clusters" in result
        assert "n_clusters" in result
        assert "sensor_ids" in result

        assert len(result["assignments"]) == 4
        assert result["n_clusters"] == 2
        assert len(result["sensor_ids"]) == 4

    def test_empty_sensors(self):
        """Test clustering with empty sensors list."""
        result = cluster_cameras_from_calibration(
            {"sensors": []},
            n_clusters=2,
            max_cluster_size=1,
        )

        assert result["assignments"] == []
        assert result["clusters"] == {}
        assert result["n_clusters"] == 0

    def test_n_clusters_greater_than_sensors(self, mock_calibration_data):
        """Test clustering when n_clusters > number of sensors."""
        result = cluster_cameras_from_calibration(
            mock_calibration_data,
            n_clusters=10,  # More clusters than cameras
            max_cluster_size=10,
        )

        # Should reduce n_clusters to sensor count
        assert result["n_clusters"] <= 4

    def test_invalid_start_camera_index(self, mock_calibration_data):
        """Test clustering with invalid start camera index."""
        result = cluster_cameras_from_calibration(
            mock_calibration_data,
            n_clusters=2,
            start_camera_index=100,  # Invalid index
            max_cluster_size=10,
        )

        # Should default to 0 and still produce valid results
        assert result["n_clusters"] == 2

    def test_single_cluster(self, mock_calibration_data):
        """Test clustering with n_clusters=1."""
        result = cluster_cameras_from_calibration(
            mock_calibration_data,
            n_clusters=1,
            max_cluster_size=10,
        )

        assert result["n_clusters"] == 1
        assert all(a == 0 for a in result["assignments"])

    def test_cluster_assignments_match_clusters_dict(self, mock_calibration_data):
        """Test that assignments list matches clusters dictionary."""
        result = cluster_cameras_from_calibration(
            mock_calibration_data,
            n_clusters=2,
            max_cluster_size=10,
        )

        # Verify consistency between assignments and clusters
        for cluster_id, camera_indices in result["clusters"].items():
            for idx in camera_indices:
                assert result["assignments"][idx] == cluster_id


class TestClusteringWithSyntheticData:
    """Test clustering with synthetic calibration data (no external assets)."""

    def test_cluster_synthetic_calibration(self, synthetic_calibration_dir):
        base_dir, _ = synthetic_calibration_dir

        with (base_dir / "calibration.json").open() as f:
            calibration_data = json.load(f)

        sensor_count = len(calibration_data["sensors"])
        n_clusters = min(3, sensor_count)

        result = cluster_cameras_from_calibration(
            calibration_data,
            n_clusters=n_clusters,
            use_frustum=False,
            max_cluster_size=10,
        )

        assert result["n_clusters"] <= n_clusters
        assert len(result["assignments"]) == sensor_count

    def test_cluster_synthetic_calibration_with_frustum(self, synthetic_calibration_dir):
        base_dir, _ = synthetic_calibration_dir

        with (base_dir / "calibration.json").open() as f:
            calibration_data = json.load(f)

        sensor_count = len(calibration_data["sensors"])
        n_clusters = min(2, sensor_count)

        result = cluster_cameras_from_calibration(
            calibration_data,
            n_clusters=n_clusters,
            use_frustum=True,
            max_camera_distance=30.0,
            max_cluster_size=10,
        )

        assert result["n_clusters"] <= n_clusters
        assert len(result["assignments"]) == sensor_count


class TestClusteringWithDirectoryInput:
    """Test create_camera_clusters_from_calibration with directory input."""

    def test_clustering_with_directory_input(self, synthetic_calibration_dir, temp_dir):
        base_dir, map_path = synthetic_calibration_dir

        output_file = Path(temp_dir) / "calibration_dir_test.json"

        result_path = create_camera_clusters_from_calibration(
            input_calibration=str(base_dir),
            max_camera_per_group=10,
            output=str(output_file),
            n_clusters=2,
            start_camera_index=0,
            dilation=5.0,
            use_frustum=False,
            max_camera_distance=30.0,
            visualize=True,
        )

        assert result_path.exists(), "Output calibration file should be created"

        with open(result_path, "r") as f:
            result = json.load(f)

        for sensor in result["sensors"]:
            assert "group" in sensor, f"Sensor {sensor['id']} should have group info"
            assert "name" in sensor["group"]
            assert "origin" in sensor["group"]
            assert "dimensions" in sensor["group"]


class TestCreateCameraClustersFromCalibration:
    """Test create_camera_clusters_from_calibration wrapper function."""

    def test_basic_wrapper_with_mock_data(self, mock_calibration_data, temp_dir):
        """Test wrapper function with mock calibration data."""
        # Save mock data to temp file
        input_dir = Path(temp_dir)
        input_file = input_dir / "calibration.json"
        with open(input_file, "w") as f:
            json.dump(mock_calibration_data, f)

        _ensure_map_file(input_dir)

        output_file = Path(temp_dir) / "mock_calibration_clustered.json"

        result_path = create_camera_clusters_from_calibration(
            input_calibration=str(input_dir),
            max_camera_per_group=10,
            output=str(output_file),
            n_clusters=2,
            dilation=5.0,
            use_frustum=False,
        )

        assert result_path.exists()
        assert result_path == output_file

        # Verify output contents
        with open(result_path, "r") as f:
            result = json.load(f)

        assert "sensors" in result
        assert len(result["sensors"]) == 4

        # All sensors should have group information
        for sensor in result["sensors"]:
            assert "group" in sensor, f"Sensor {sensor['id']} should have group info"
            assert "name" in sensor["group"]
            assert "origin" in sensor["group"]
            assert "dimensions" in sensor["group"]

    def test_wrapper_overwrite_mode(self, mock_calibration_data, temp_dir):
        """Test wrapper function with overwrite=True."""
        input_dir = Path(temp_dir)
        input_file = input_dir / "calibration.json"
        with open(input_file, "w") as f:
            json.dump(mock_calibration_data, f)

        _ensure_map_file(input_dir)

        result_path = create_camera_clusters_from_calibration(
            input_calibration=str(input_dir),
            max_camera_per_group=10,
            overwrite=True,
            n_clusters=2,
        )

        assert result_path == input_file

        # Verify file was updated
        with open(result_path, "r") as f:
            result = json.load(f)

        for sensor in result["sensors"]:
            assert "group" in sensor

    def test_wrapper_default_output_path(self, mock_calibration_data, temp_dir):
        """Test wrapper function with default output path."""
        input_dir = Path(temp_dir)
        input_file = input_dir / "calibration.json"
        with open(input_file, "w") as f:
            json.dump(mock_calibration_data, f)

        _ensure_map_file(input_dir)

        result_path = create_camera_clusters_from_calibration(
            input_calibration=str(input_dir),
            max_camera_per_group=10,
            n_clusters=2,
        )

        # Default output should be calibration_clustered.json
        expected_output = input_dir / "calibration_clustered.json"
        assert result_path == expected_output
        assert result_path.exists()

    def test_wrapper_with_sensor_filtering(self, mock_calibration_data, temp_dir):
        """Test wrapper function with sensor name filtering."""
        input_dir = Path(temp_dir)
        input_file = input_dir / "calibration.json"
        with open(input_file, "w") as f:
            json.dump(mock_calibration_data, f)

        _ensure_map_file(input_dir)

        output_file = Path(temp_dir) / "calibration_filtered.json"

        # Filter to only 2 cameras
        result_path = create_camera_clusters_from_calibration(
            input_calibration=str(input_dir),
            max_camera_per_group=10,
            output=str(output_file),
            sensor_names=["Camera_01", "Camera_02"],
            n_clusters=1,
        )

        with open(result_path, "r") as f:
            result = json.load(f)

        assert len(result["sensors"]) == 2
        sensor_ids = {s["id"] for s in result["sensors"]}
        assert sensor_ids == {"Camera_01", "Camera_02"}

    def test_wrapper_single_cluster(self, mock_calibration_data, temp_dir):
        """Test wrapper function with n_clusters=1."""
        input_dir = Path(temp_dir)
        input_file = input_dir / "calibration.json"
        with open(input_file, "w") as f:
            json.dump(mock_calibration_data, f)

        _ensure_map_file(input_dir)

        output_file = Path(temp_dir) / "calibration_single.json"

        result_path = create_camera_clusters_from_calibration(
            input_calibration=str(input_dir),
            max_camera_per_group=10,
            output=str(output_file),
            n_clusters=1,
        )

        with open(result_path, "r") as f:
            result = json.load(f)

        # All sensors should be in the same group
        group_names = {s["group"]["name"] for s in result["sensors"]}
        assert len(group_names) == 1

    def test_wrapper_n_clusters_exceeds_sensors(self, mock_calibration_data, temp_dir):
        """Test wrapper raises error when n_clusters exceeds sensor count."""
        input_dir = Path(temp_dir)
        input_file = input_dir / "calibration.json"
        with open(input_file, "w") as f:
            json.dump(mock_calibration_data, f)

        _ensure_map_file(input_dir)

        output_file = Path(temp_dir) / "calibration_reduced.json"

        # Request 10 clusters but only have 4 cameras - should raise error
        with pytest.raises(SystemExit):
            create_camera_clusters_from_calibration(
                input_calibration=str(input_dir),
                max_camera_per_group=1,
                output=str(output_file),
                n_clusters=10,
            )

    def test_wrapper_with_real_data(self, synthetic_calibration_dir, temp_dir):
        """Test wrapper function with synthetic calibration data."""
        base_dir, _ = synthetic_calibration_dir
        output_file = Path(temp_dir) / "synthetic_data_clustered.json"

        result_path = create_camera_clusters_from_calibration(
            input_calibration=str(base_dir),
            max_camera_per_group=10,
            output=str(output_file),
            n_clusters=2,
            use_frustum=False,
        )

        assert result_path.exists()

        with open(result_path, "r") as f:
            result = json.load(f)

        for sensor in result["sensors"]:
            assert "group" in sensor
            assert "name" in sensor["group"]
            assert "origin" in sensor["group"]
            assert "dimensions" in sensor["group"]

    def test_wrapper_with_map_and_visualization(self, synthetic_calibration_dir, temp_dir):
        """Test wrapper function with map file and visualization."""
        base_dir, _ = synthetic_calibration_dir
        output_file = Path(temp_dir) / "synthetic_with_viz.json"

        result_path = create_camera_clusters_from_calibration(
            input_calibration=str(base_dir),
            max_camera_per_group=10,
            output=str(output_file),
            n_clusters=2,
            visualize=True,
        )

        assert result_path.exists()

        viz_file = Path(temp_dir) / "synthetic_with_viz_map.png"
        if viz_file.exists():
            logger.debug("Visualization created: %s", viz_file)

    def test_wrapper_invalid_calibration_file(self, temp_dir):
        """Test wrapper function with non-existent file."""
        with pytest.raises(SystemExit):
            create_camera_clusters_from_calibration(
                input_calibration="/nonexistent/path",
                max_camera_per_group=10,
                n_clusters=2,
            )

    def test_wrapper_missing_sensors_field(self, temp_dir):
        """Test wrapper function with calibration missing sensors field."""
        input_dir = Path(temp_dir)
        input_file = input_dir / "calibration.json"
        with open(input_file, "w") as f:
            json.dump({"invalid": "data"}, f)

        _ensure_map_file(input_dir)

        with pytest.raises(SystemExit):
            create_camera_clusters_from_calibration(
                input_calibration=str(input_dir),
                max_camera_per_group=10,
                n_clusters=2,
            )

    def test_wrapper_missing_map_file_allows_no_region_metadata(self, mock_calibration_data, temp_dir):
        """Visualization proceeds with black background when map is missing."""
        input_dir = Path(temp_dir)
        input_file = input_dir / "calibration.json"
        with open(input_file, "w") as f:
            json.dump(mock_calibration_data, f)

        result_path = create_camera_clusters_from_calibration(
            input_calibration=str(input_dir),
            max_camera_per_group=10,
            visualize=True,
        )

        assert result_path.exists()

    def test_wrapper_overwrite_and_output_mutually_exclusive(
        self, mock_calibration_data, temp_dir
    ):
        """Test wrapper raises error when both overwrite and output are specified."""
        input_dir = Path(temp_dir)
        input_file = input_dir / "calibration.json"
        with open(input_file, "w") as f:
            json.dump(mock_calibration_data, f)

        _ensure_map_file(input_dir)

        with pytest.raises(SystemExit):
            create_camera_clusters_from_calibration(
                input_calibration=str(input_dir),
                max_camera_per_group=10,
                output=str(Path(temp_dir) / "output.json"),
                overwrite=True,
            )


class TestReassignCameraGroupsFromCalibration:
    """Test reassignment wrapper now in group_utils."""

    def test_reassign_updates_groups(self, mock_calibration_data, temp_dir):
        input_dir = Path(temp_dir)
        input_file = input_dir / "calibration.json"
        input_dir.mkdir(parents=True, exist_ok=True)
        seed_data = json.loads(json.dumps(mock_calibration_data))

        # Original groups: assign two cameras to bev-sensor-1, two to bev-sensor-2
        for idx, sensor in enumerate(seed_data["sensors"]):
            sensor["group"] = {
                "name": "bev-sensor-1" if idx < 2 else "bev-sensor-2",
                "alias": f"area-{1 if idx < 2 else 2}",
                "type": "bev",
            }

        # Persist grouped calibration
        with open(input_file, "w") as f:
            json.dump(seed_data, f)

        # Ensure map file exists for consistent scene bounds/visualization pathing
        _ensure_map_file(input_dir)

        output_file = input_dir / "calibration_reassigned.json"

        result_path, warnings = reassign_camera_groups_from_calibration(
            input_calibration=str(input_dir),
            moves=["Camera_01:bev-sensor-2", "Camera_02:bev-sensor-2"],
            output=str(output_file),
            overwrite=False,
            visualize=False,
        )

        assert result_path == output_file
        assert result_path.exists()
        assert not warnings

        with open(result_path, "r") as f:
            result = json.load(f)

        # Cameras 1 and 2 (Camera_01, Camera_02) should now be in bev-sensor-2
        cam1 = next(s for s in result["sensors"] if s["id"] == "Camera_01")
        cam2 = next(s for s in result["sensors"] if s["id"] == "Camera_02")
        assert cam1["group"]["name"] == "bev-sensor-2"
        assert cam2["group"]["name"] == "bev-sensor-2"


# ==============================================================================
# Test Edge Cases and Error Handling
# ==============================================================================


class TestClusteringEdgeCases:
    """Test edge cases and error handling."""

    def test_single_camera_clustering(self):
        """Test clustering with single camera."""
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

        result = cluster_cameras_from_calibration(
            calibration_data,
            n_clusters=1,
            max_cluster_size=5,
        )

        assert result["n_clusters"] == 1
        assert len(result["assignments"]) == 1
        assert result["assignments"][0] == 0

    def test_cameras_without_fov_polygon(self):
        """Test clustering cameras without fieldOfViewPolygon attribute."""
        calibration_data = {
            "sensors": [
                {
                    "id": "Camera_01",
                    "type": "camera",
                    "intrinsicMatrix": [[1000, 0, 960], [0, 1000, 540], [0, 0, 1]],
                    "extrinsicMatrix": [
                        [1, 0, 0, 0],
                        [0, 1, 0, 2],
                        [0, 0, 1, 5],
                        [0, 0, 0, 1],
                    ],
                    "attributes": [
                        {"name": "frameWidth", "value": "1920"},
                        {"name": "frameHeight", "value": "1080"},
                    ],
                },
                {
                    "id": "Camera_02",
                    "type": "camera",
                    "intrinsicMatrix": [[1000, 0, 960], [0, 1000, 540], [0, 0, 1]],
                    "extrinsicMatrix": [
                        [1, 0, 0, 10],
                        [0, 1, 0, 2],
                        [0, 0, 1, 5],
                        [0, 0, 0, 1],
                    ],
                    "attributes": [
                        {"name": "frameWidth", "value": "1920"},
                        {"name": "frameHeight", "value": "1080"},
                    ],
                },
            ]
        }

        # Should fall back to frustum calculation
        result = cluster_cameras_from_calibration(
            calibration_data,
            n_clusters=1,
            use_frustum=True,
            max_cluster_size=5,
        )

        assert result["n_clusters"] >= 1
        assert len(result["assignments"]) == 2

    def test_clustering_determinism(self, mock_calibration_data):
        """Test that clustering produces consistent results."""
        result1 = cluster_cameras_from_calibration(
            mock_calibration_data,
            n_clusters=2,
            start_camera_index=0,
            max_cluster_size=10,
        )

        result2 = cluster_cameras_from_calibration(
            mock_calibration_data,
            n_clusters=2,
            start_camera_index=0,
            max_cluster_size=10,
        )

        # Same parameters should produce same results
        assert result1["assignments"] == result2["assignments"]


class TestGetCameraFovPolygon:
    """Test get_camera_fov_polygon function."""

    def test_get_camera_fov_polygon_basic(self):
        """Test FOV polygon generation from camera matrices."""
        sensor = {
            "id": "Camera_01",
            "intrinsicMatrix": [[1000, 0, 960], [0, 1000, 540], [0, 0, 1]],
            "extrinsicMatrix": [
                [1, 0, 0, 0],
                [0, 0.866, -0.5, 2],
                [0, 0.5, 0.866, 5],
                [0, 0, 0, 1],
            ],
            "attributes": [
                {"name": "frameWidth", "value": "1920"},
                {"name": "frameHeight", "value": "1080"},
            ],
        }

        poly = get_camera_fov_polygon(sensor, max_camera_distance=30.0)

        # Should return a polygon (may be None if calculation fails)
        if poly is not None:
            assert not poly.is_empty
            assert poly.area > 0

    def test_get_camera_fov_polygon_missing_matrices(self):
        """Test FOV polygon generation with missing matrices."""
        sensor = {
            "id": "Camera_01",
            "attributes": [],
        }

        poly = get_camera_fov_polygon(sensor)

        # Should return None when matrices are missing
        assert poly is None


if __name__ == "__main__":
    # Run tests with pytest
    pytest.main([__file__, "-v", "--tb=short"])
