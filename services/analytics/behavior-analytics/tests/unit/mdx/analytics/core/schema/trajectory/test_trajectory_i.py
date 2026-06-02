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

import math
import pytest
from datetime import datetime
from unittest.mock import patch

from mdx.analytics.core.schema.models import Coordinate
from mdx.analytics.core.schema.trajectory.trajectory_i import TrajectoryI


class TestTrajectoryI:
    """Test suite for TrajectoryI functionality, focusing on bearing calculation with inverted y-axis."""

    @pytest.fixture
    def sample_trajectory(self):
        """Create a sample trajectory for testing."""
        points = [
            Coordinate(x=100, y=200, z=0),  # Bottom of image
            Coordinate(x=100, y=100, z=0),  # Top of image (upward movement in image coordinates)
        ]
        start = datetime(2024, 1, 1, 12, 0, 0)
        end = datetime(2024, 1, 1, 12, 0, 10)
        return TrajectoryI(
            id="sample_trajectory",
            start=start,
            end=end,
            points=points
        )

    def test_bearing_cardinal_directions(self):
        """Test bearing calculation for all 4 cardinal directions with inverted y-axis."""
        start = datetime(2024, 1, 1, 12, 0, 0)
        end = datetime(2024, 1, 1, 12, 0, 10)
        
        # Test East (0 degrees) - moving right
        points_east = [Coordinate(x=100, y=100, z=0), Coordinate(x=200, y=100, z=0)]
        traj_east = TrajectoryI(id="east", start=start, end=end, points=points_east)
        assert abs(traj_east.bearing - 0.0) < 1e-10
        
        # Test North (90 degrees) - moving up in image coords means decreasing y
        points_north = [Coordinate(x=100, y=200, z=0), Coordinate(x=100, y=100, z=0)]
        traj_north = TrajectoryI(id="north", start=start, end=end, points=points_north)
        assert abs(traj_north.bearing - 90.0) < 1e-10
        
        # Test West (180 degrees) - moving left
        points_west = [Coordinate(x=200, y=100, z=0), Coordinate(x=100, y=100, z=0)]
        traj_west = TrajectoryI(id="west", start=start, end=end, points=points_west)
        assert abs(traj_west.bearing - 180.0) < 1e-10
        
        # Test South (270 degrees) - moving down in image coords means increasing y
        points_south = [Coordinate(x=100, y=100, z=0), Coordinate(x=100, y=200, z=0)]
        traj_south = TrajectoryI(id="south", start=start, end=end, points=points_south)
        assert abs(traj_south.bearing - 270.0) < 1e-10

    def test_bearing_diagonal_directions(self):
        """Test bearing calculation for diagonal directions with inverted y-axis."""
        start = datetime(2024, 1, 1, 12, 0, 0)
        end = datetime(2024, 1, 1, 12, 0, 10)
        
        # Test Northeast (45 degrees) - right and up (decreasing y)
        points_ne = [Coordinate(x=100, y=200, z=0), Coordinate(x=200, y=100, z=0)]
        traj_ne = TrajectoryI(id="ne", start=start, end=end, points=points_ne)
        assert abs(traj_ne.bearing - 45.0) < 1e-10
        
        # Test Northwest (135 degrees) - left and up (decreasing y)
        points_nw = [Coordinate(x=200, y=200, z=0), Coordinate(x=100, y=100, z=0)]
        traj_nw = TrajectoryI(id="nw", start=start, end=end, points=points_nw)
        assert abs(traj_nw.bearing - 135.0) < 1e-10
        
        # Test Southwest (225 degrees) - left and down (increasing y)
        points_sw = [Coordinate(x=200, y=100, z=0), Coordinate(x=100, y=200, z=0)]
        traj_sw = TrajectoryI(id="sw", start=start, end=end, points=points_sw)
        assert abs(traj_sw.bearing - 225.0) < 1e-10
        
        # Test Southeast (315 degrees) - right and down (increasing y)
        points_se = [Coordinate(x=100, y=100, z=0), Coordinate(x=200, y=200, z=0)]
        traj_se = TrajectoryI(id="se", start=start, end=end, points=points_se)
        assert abs(traj_se.bearing - 315.0) < 1e-10

    def test_bearing_same_points_zero_vector(self):
        """Test bearing calculation with same points (zero vector)."""
        start = datetime(2024, 1, 1, 12, 0, 0)
        end = datetime(2024, 1, 1, 12, 0, 10)
        
        # Same points should result in atan2(0, 0) which returns 0
        points_same = [Coordinate(x=100, y=100, z=0), Coordinate(x=100, y=100, z=0)]
        traj_same = TrajectoryI(id="same", start=start, end=end, points=points_same)
        bearing = traj_same.bearing
        
        # atan2(0, 0) returns 0, so bearing should be 0
        assert abs(bearing - 0.0) < 1e-10

    def test_bearing_normalization_negative_angles(self):
        """Test bearing normalization for angles that would be negative."""
        start = datetime(2024, 1, 1, 12, 0, 0)
        end = datetime(2024, 1, 1, 12, 0, 10)
        
        # Test cases that produce negative atan2 results
        # Southwest movement: atan2 would give negative result, should normalize to positive
        points_sw = [Coordinate(x=100, y=100, z=0), Coordinate(x=50, y=150, z=0)]
        traj_sw = TrajectoryI(id="sw_norm", start=start, end=end, points=points_sw)
        bearing_sw = traj_sw.bearing
        assert 0 <= bearing_sw <= 360
        assert 180 < bearing_sw < 270  # Should be in third quadrant

    def test_bearing_small_coordinate_differences(self):
        """Test bearing calculation with very small coordinate differences."""
        start = datetime(2024, 1, 1, 12, 0, 0)
        end = datetime(2024, 1, 1, 12, 0, 10)
        
        # Very small movements
        points_small = [
            Coordinate(x=100.0, y=100.0, z=0), 
            Coordinate(x=100.001, y=99.999, z=0)  # Tiny northeast movement
        ]
        traj_small = TrajectoryI(id="small", start=start, end=end, points=points_small)
        bearing = traj_small.bearing
        assert 0 <= bearing <= 360
        assert 0 < bearing < 90  # Should be in first quadrant

    def test_bearing_large_coordinate_differences(self):
        """Test bearing calculation with very large coordinate differences."""
        start = datetime(2024, 1, 1, 12, 0, 0)
        end = datetime(2024, 1, 1, 12, 0, 10)
        
        # Very large movements
        points_large = [
            Coordinate(x=0, y=0, z=0), 
            Coordinate(x=1000000, y=-1000000, z=0)  # Large southeast movement
        ]
        traj_large = TrajectoryI(id="large", start=start, end=end, points=points_large)
        bearing = traj_large.bearing
        assert 0 <= bearing <= 360
        assert 0 < bearing < 90  # Should be in first quadrant (due to inverted y)

    def test_bearing_precise_angle_calculations(self):
        """Test bearing calculation for precise angles."""
        start = datetime(2024, 1, 1, 12, 0, 0)
        end = datetime(2024, 1, 1, 12, 0, 10)
        
        # Test 30 degrees - using trigonometry to create exact angle
        # For 30 degrees: dx = cos(30°) = sqrt(3)/2, dy_inverted = sin(30°) = 1/2
        # Since y is inverted, to move "up" by sin(30°), we subtract from y
        dx = math.sqrt(3)  # This creates a 30° angle
        dy_inverted = 1    # Upward movement in image coordinates (subtract from y)
        points_30 = [Coordinate(x=0, y=100, z=0), Coordinate(x=dx, y=100 - dy_inverted, z=0)]
        traj_30 = TrajectoryI(id="30deg", start=start, end=end, points=points_30)
        assert abs(traj_30.bearing - 30.0) < 1e-10
        
        # Test 60 degrees - dx = cos(60°) = 1/2, dy_inverted = sin(60°) = sqrt(3)/2
        dx = 1
        dy_inverted = math.sqrt(3)  # Upward movement in image coordinates
        points_60 = [Coordinate(x=0, y=100, z=0), Coordinate(x=dx, y=100 - dy_inverted, z=0)]
        traj_60 = TrajectoryI(id="60deg", start=start, end=end, points=points_60)
        assert abs(traj_60.bearing - 60.0) < 1e-10

    def test_bearing_edge_case_near_360(self):
        """Test bearing calculation for angles near 360 degrees."""
        start = datetime(2024, 1, 1, 12, 0, 0)
        end = datetime(2024, 1, 1, 12, 0, 10)
        
        # Slightly northwest of east (should be around 359 degrees)
        points_near_360 = [
            Coordinate(x=0, y=100, z=0), 
            Coordinate(x=100, y=99.9, z=0)  # Slightly upward movement
        ]
        traj_near_360 = TrajectoryI(id="near_360", start=start, end=end, points=points_near_360)
        bearing = traj_near_360.bearing
        assert 0 <= bearing <= 360
        assert bearing > 350 or bearing < 10  # Should be near 360/0 boundary

    def test_bearing_inheritance_from_trajectory_base(self):
        """Test that TrajectoryI properly overrides the base bearing method."""
        start = datetime(2024, 1, 1, 12, 0, 0)
        end = datetime(2024, 1, 1, 12, 0, 10)
        
        # Create trajectory moving upward in image coordinates (y decreasing)
        points = [Coordinate(x=100, y=200, z=0), Coordinate(x=100, y=100, z=0)]
        traj_i = TrajectoryI(id="image_traj", start=start, end=end, points=points)
        
        # In image coordinates, this should be 90 degrees (North)
        # because we're moving upward (decreasing y)
        assert abs(traj_i.bearing - 90.0) < 1e-10

    def test_bearing_with_z_coordinate_ignored(self):
        """Test that z-coordinate is properly ignored in bearing calculation."""
        start = datetime(2024, 1, 1, 12, 0, 0)
        end = datetime(2024, 1, 1, 12, 0, 10)
        
        # Points with different z values but same x,y movement
        points_z1 = [Coordinate(x=0, y=100, z=0), Coordinate(x=100, y=100, z=0)]
        points_z2 = [Coordinate(x=0, y=100, z=50), Coordinate(x=100, y=100, z=100)]
        
        traj_z1 = TrajectoryI(id="z1", start=start, end=end, points=points_z1)
        traj_z2 = TrajectoryI(id="z2", start=start, end=end, points=points_z2)
        
        # Both should have same bearing (East = 0 degrees)
        assert abs(traj_z1.bearing - traj_z2.bearing) < 1e-10
        assert abs(traj_z1.bearing - 0.0) < 1e-10

    def test_bearing_error_conditions_empty_points(self):
        """Test bearing calculation with empty points list."""
        start = datetime(2024, 1, 1, 12, 0, 0)
        end = datetime(2024, 1, 1, 12, 0, 10)
        
        trajectory = TrajectoryI(
            id="empty",
            start=start,
            end=end,
            points=[]
        )
        
        # Should raise IndexError when accessing head or last
        with pytest.raises(IndexError):
            _ = trajectory.bearing

    def test_bearing_error_conditions_single_point(self):
        """Test bearing calculation with single point."""
        start = datetime(2024, 1, 1, 12, 0, 0)
        end = datetime(2024, 1, 1, 12, 0, 10)
        
        trajectory = TrajectoryI(
            id="single",
            start=start,
            end=end,
            points=[Coordinate(x=100, y=100, z=0)]
        )
        
        # Should raise IndexError when accessing last (points[-1] on single item list)
        # Actually, single point should work - head and last will be the same point
        bearing = trajectory.bearing
        # Same point means zero vector, atan2(0,0) = 0
        assert abs(bearing - 0.0) < 1e-10

    @pytest.mark.parametrize("dx,dy,expected_bearing", [
        (1, 0, 0),      # East
        (1, -1, 45),    # Northeast (y inverted)
        (0, -1, 90),    # North (y inverted)
        (-1, -1, 135),  # Northwest (y inverted)
        (-1, 0, 180),   # West
        (-1, 1, 225),   # Southwest (y inverted)
        (0, 1, 270),    # South (y inverted)
        (1, 1, 315),    # Southeast (y inverted)
    ])
    def test_bearing_parametrized_directions(self, dx, dy, expected_bearing):
        """Test bearing calculation for various directions using parametrized tests."""
        start = datetime(2024, 1, 1, 12, 0, 0)
        end = datetime(2024, 1, 1, 12, 0, 10)
        
        points = [
            Coordinate(x=100, y=100, z=0),
            Coordinate(x=100 + dx, y=100 + dy, z=0)
        ]
        trajectory = TrajectoryI(
            id=f"param_{dx}_{dy}",
            start=start,
            end=end,
            points=points
        )
        
        bearing = trajectory.bearing
        assert abs(bearing - expected_bearing) < 1e-10, \
            f"Expected {expected_bearing}°, got {bearing}° for movement dx={dx}, dy={dy}"

    def test_bearing_cached_property(self):
        """Test that bearing is cached and computed only once."""
        start = datetime(2024, 1, 1, 12, 0, 0)
        end = datetime(2024, 1, 1, 12, 0, 10)
        points = [Coordinate(x=0, y=100, z=0), Coordinate(x=100, y=0, z=0)]
        
        trajectory = TrajectoryI(id="cache_test", start=start, end=end, points=points)
        
        # Mock math.atan2 to ensure it's called only once
        with patch('math.atan2', wraps=math.atan2) as mock_atan2:
            # First access
            bearing1 = trajectory.bearing
            # Second access should use cached value
            bearing2 = trajectory.bearing
            
            # Values should be identical
            assert bearing1 == bearing2
            # atan2 should be called only once due to caching
            assert mock_atan2.call_count == 1

    def test_bearing_formula_correctness(self):
        """Test the specific formula used in TrajectoryI bearing calculation."""
        start = datetime(2024, 1, 1, 12, 0, 0)
        end = datetime(2024, 1, 1, 12, 0, 10)
        
        # Create trajectory with known coordinates
        head_point = Coordinate(x=10, y=20, z=0)
        last_point = Coordinate(x=30, y=15, z=0)
        points = [head_point, last_point]
        
        trajectory = TrajectoryI(id="formula_test", start=start, end=end, points=points)
        
        # Calculate expected bearing using the formula:
        # brng = math.atan2(-self.last.y + self.head.y, self.last.x - self.head.x) * 180 / math.pi
        expected_y_component = -last_point.y + head_point.y  # -15 + 20 = 5
        expected_x_component = last_point.x - head_point.x   # 30 - 10 = 20
        expected_atan2_result = math.atan2(expected_y_component, expected_x_component)
        expected_bearing = (expected_atan2_result * 180 / math.pi + 360) % 360
        
        actual_bearing = trajectory.bearing
        assert abs(actual_bearing - expected_bearing) < 1e-10
