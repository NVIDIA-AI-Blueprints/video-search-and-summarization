# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

import pytest
import numpy as np
from unittest.mock import Mock
from datetime import datetime
from shapely.geometry import Polygon, MultiPolygon

from mdx.analytics.core.utils.space_utilization import SpaceAnalyzer
from mdx.analytics.core.schema.config import SpaceAnalyticsConfig


class TestSpaceAnalyzer:
    """Tests for SpaceAnalyzer class."""

    @pytest.fixture
    def mock_config(self):
        """Create mock SpaceAnalyticsConfig."""
        config = Mock(spec=SpaceAnalyticsConfig)
        config.gridSize = 0.1
        config.unsafeSize = 0.5
        config.targetObjects = ["Pallet"]
        config.useGA = False
        config.populationSizeGA = 50
        config.numGenerationsGA = 100
        return config

    @pytest.fixture
    def mock_calibration(self):
        """Create mock CalibrationE."""
        calibration = Mock()
        calibration.homography_map_global_roi = {
            "zone1": (
                (0, 0),  # origin
                (10.0, 10.0),  # dimensions
                [[1, 0, 0], [0, 1, 0], [0, 0, 1]],  # hmatrix
                [[1, 0, 0], [0, 1, 0], [0, 0, 1]]   # hmatrix_inv
            )
        }
        calibration.global_buffer_zones = []
        calibration.transform_bbox3d_to_global_rois = Mock(return_value={})
        calibration.perspective_transform = Mock(side_effect=lambda x, y, h: (x, y))
        return calibration

    @pytest.fixture
    def analyzer(self, mock_config, mock_calibration):
        """Create SpaceAnalyzer instance for testing."""
        return SpaceAnalyzer(mock_config, mock_calibration)

    def test_initialization(self, analyzer, mock_config, mock_calibration):
        """Test SpaceAnalyzer initialization."""
        assert analyzer.config == mock_config
        assert analyzer.calibration == mock_calibration
        assert analyzer.grid_size == 0.1
        assert analyzer.unsafe_size == 0.5
        assert analyzer.targets == ["Pallet"]

    def test_set_buffer_zone_dimension(self, analyzer):
        """Test that buffer zone dimensions are set correctly."""
        assert "zone1" in analyzer.buffer_zone_dimensions
        assert analyzer.buffer_zone_dimensions["zone1"]["width"] == 10.0
        assert analyzer.buffer_zone_dimensions["zone1"]["height"] == 10.0


class TestSpaceAnalyzerEventTime:
    """Tests for event time extraction methods."""

    @pytest.fixture
    def mock_config(self):
        """Create mock config."""
        config = Mock(spec=SpaceAnalyticsConfig)
        config.gridSize = 0.1
        config.unsafeSize = 0.5
        config.targetObjects = ["Pallet"]
        config.useGA = False
        config.populationSizeGA = 50
        config.numGenerationsGA = 100
        return config

    @pytest.fixture
    def mock_calibration(self):
        """Create mock calibration."""
        calibration = Mock()
        calibration.homography_map_global_roi = {}
        return calibration

    @pytest.fixture
    def analyzer(self, mock_config, mock_calibration):
        """Create SpaceAnalyzer instance."""
        return SpaceAnalyzer(mock_config, mock_calibration)

    def test_get_event_time_with_messages(self, analyzer):
        """Test get_event_time with messages."""
        msg1 = Mock()
        msg1.timestamp = datetime(2024, 3, 14, 12, 0, 0)
        msg2 = Mock()
        msg2.timestamp = datetime(2024, 3, 14, 12, 1, 0)
        
        msg_map = {
            "obj1": [msg1],
            "obj2": [msg2]
        }
        
        result = analyzer.get_event_time(msg_map)
        
        # Should return timestamp of latest message (msg2)
        assert result is not None

    def test_get_event_time_empty_messages(self, analyzer):
        """Test get_event_time with empty messages returns fallback."""
        result = analyzer.get_event_time({}, last_action_time=1234567890.0)
        assert result == 1234567890.0


class TestSpaceAnalyzerGeometryConversions:
    """Tests for geometry conversion methods."""

    @pytest.fixture
    def mock_config(self):
        """Create mock config."""
        config = Mock(spec=SpaceAnalyticsConfig)
        config.gridSize = 0.1
        config.unsafeSize = 0.5
        config.targetObjects = ["Pallet"]
        config.useGA = False
        config.populationSizeGA = 50
        config.numGenerationsGA = 100
        return config

    @pytest.fixture
    def mock_calibration(self):
        """Create mock calibration."""
        calibration = Mock()
        calibration.homography_map_global_roi = {}
        calibration.perspective_transform = Mock(side_effect=lambda x, y, h: (x, y))
        return calibration

    @pytest.fixture
    def analyzer(self, mock_config, mock_calibration):
        """Create SpaceAnalyzer instance."""
        return SpaceAnalyzer(mock_config, mock_calibration)

    def test_convert_geo_to_coord_list_polygon(self, analyzer):
        """Test converting Polygon to coord list."""
        polygon = Polygon([(0, 0), (1, 0), (1, 1), (0, 1)])
        result = analyzer.convert_geo_to_coord_list(polygon)
        
        assert len(result) == 1
        # First polygon's exterior
        assert len(result[0]) >= 1

    def test_convert_geo_to_coord_list_multipolygon(self, analyzer):
        """Test converting MultiPolygon to coord list."""
        poly1 = Polygon([(0, 0), (1, 0), (1, 1), (0, 1)])
        poly2 = Polygon([(2, 2), (3, 2), (3, 3), (2, 3)])
        multipolygon = MultiPolygon([poly1, poly2])
        
        result = analyzer.convert_geo_to_coord_list(multipolygon)
        
        assert len(result) == 2

    def test_convert_polygon_to_list(self, analyzer):
        """Test converting single polygon to list."""
        polygon = Polygon([(0, 0), (1, 0), (1, 1), (0, 1)])
        result = analyzer.convert_polygon_to_list(polygon)
        
        assert len(result) >= 1
        # Exterior coords
        assert len(result[0]) == 5  # 4 corners + closing point

    def test_convert_polygon_to_list_with_hole(self, analyzer):
        """Test converting polygon with hole to list."""
        exterior = [(0, 0), (4, 0), (4, 4), (0, 4)]
        hole = [(1, 1), (3, 1), (3, 3), (1, 3)]
        polygon = Polygon(exterior, [hole])
        
        result = analyzer.convert_polygon_to_list(polygon)
        
        assert len(result) == 2  # Exterior + 1 hole

    def test_convert_poly_list_to_coord_list(self, analyzer):
        """Test converting list of polygons to coord list."""
        poly1 = Polygon([(0, 0), (1, 0), (1, 1), (0, 1)])
        poly2 = Polygon([(2, 2), (3, 2), (3, 3), (2, 3)])
        
        result = analyzer.convert_poly_list_to_coord_list([poly1, poly2])
        
        assert len(result) == 2


class TestSpaceAnalyzerMetrics:
    """Tests for space metrics computation."""

    @pytest.fixture
    def mock_config(self):
        """Create mock config."""
        config = Mock(spec=SpaceAnalyticsConfig)
        config.gridSize = 0.1
        config.unsafeSize = 0.5
        config.targetObjects = ["Pallet"]
        config.useGA = False
        config.populationSizeGA = 50
        config.numGenerationsGA = 100
        return config

    @pytest.fixture
    def mock_calibration(self):
        """Create mock calibration."""
        calibration = Mock()
        calibration.homography_map_global_roi = {}
        return calibration

    @pytest.fixture
    def analyzer(self, mock_config, mock_calibration):
        """Create SpaceAnalyzer instance."""
        return SpaceAnalyzer(mock_config, mock_calibration)

    def test_compute_space_metrics_empty_area(self, analyzer):
        """Test computing metrics for empty area."""
        area_width = 10.0
        area_height = 10.0
        existing_pallets = []
        solution = []
        existing_bbox3ds = []
        
        result = analyzer.compute_space_metrics(
            area_width, area_height, 
            existing_pallets, solution, existing_bbox3ds
        )
        
        buffer_area, occupied_space, free_space, utilizable, unsafe_flag, unsafe_pallets, unsafe_cnt = result
        
        # Buffer area should be the full area
        assert buffer_area.area == pytest.approx(100.0)
        # No pallets, so free space equals total
        assert free_space.area == pytest.approx(100.0)
        # No unsafe conditions
        assert unsafe_flag is False
        assert unsafe_cnt == 0

    def test_compute_space_metrics_with_pallets(self, analyzer):
        """Test computing metrics with existing pallets."""
        area_width = 10.0
        area_height = 10.0
        # One pallet at (0,0) with size 2x2
        existing_pallets = [Polygon([(0, 0), (2, 0), (2, 2), (0, 2)])]
        solution = []
        existing_bbox3ds = [Mock()]
        
        result = analyzer.compute_space_metrics(
            area_width, area_height,
            existing_pallets, solution, existing_bbox3ds
        )
        
        buffer_area, occupied_space, free_space, utilizable, unsafe_flag, unsafe_pallets, unsafe_cnt = result
        
        # Occupied space should be 4 sq units
        assert occupied_space.area == pytest.approx(4.0)
        # Free space should be 96 sq units
        assert free_space.area == pytest.approx(96.0)


class TestSpaceAnalyzerHelpers:
    """Tests for helper methods."""

    @pytest.fixture
    def mock_config(self):
        """Create mock config."""
        config = Mock(spec=SpaceAnalyticsConfig)
        config.gridSize = 0.1
        config.unsafeSize = 0.5
        config.targetObjects = ["Pallet"]
        config.useGA = False
        config.populationSizeGA = 50
        config.numGenerationsGA = 100
        return config

    @pytest.fixture
    def mock_calibration(self):
        """Create mock calibration."""
        calibration = Mock()
        calibration.homography_map_global_roi = {}
        return calibration

    @pytest.fixture
    def analyzer(self, mock_config, mock_calibration):
        """Create SpaceAnalyzer instance."""
        return SpaceAnalyzer(mock_config, mock_calibration)

    def test_datetime_to_str(self, analyzer):
        """Test datetime to string conversion."""
        dt = datetime(2024, 3, 14, 12, 30, 45, 123456)
        result = analyzer.datetime_to_str(dt)
        
        assert result.endswith("Z")
        assert "2024-03-14T12:30:45.123" in result

    def test_draw_circle_pts(self, analyzer):
        """Test generating circle points."""
        center = (5.0, 5.0)
        radius = 2.0
        num_pts = 8
        
        result = analyzer.draw_circle_pts(center, radius, num_pts)
        
        assert len(result) == num_pts
        # All points should be at distance radius from center
        for x, y in result:
            dist = np.sqrt((x - 5.0)**2 + (y - 5.0)**2)
            assert pytest.approx(dist, rel=1e-6) == radius

    def test_find_convex_hull(self, analyzer):
        """Test finding convex hull."""
        points = np.array([
            [0, 0],
            [1, 0],
            [0.5, 0.5],  # Interior point
            [1, 1],
            [0, 1]
        ])
        
        result = analyzer.find_convex_hull(points)
        
        # Convex hull should have 4 vertices (the corners)
        assert result is not None
        assert len(result) == 4

    def test_find_convex_hull_too_few_points(self, analyzer):
        """Test convex hull with too few points."""
        points = np.array([[0, 0], [1, 1]])
        
        result = analyzer.find_convex_hull(points)
        
        assert result is None

