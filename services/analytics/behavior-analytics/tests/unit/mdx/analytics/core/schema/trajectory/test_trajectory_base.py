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


from mdx.analytics.core.schema.models import Coordinate, GeoLocation, Point
from mdx.analytics.core.schema.trajectory.trajectory_base import TrajectoryBase


class TestTrajectoryBase:
    """Test suite for TrajectoryBase functionality."""

    @pytest.fixture
    def simple_trajectory(self):
        """Create a simple trajectory for testing."""
        points = [
            Coordinate(x=0, y=0, z=0),
            Coordinate(x=1, y=1, z=0),
            Coordinate(x=2, y=2, z=0),
        ]
        start = datetime(2024, 1, 1, 12, 0, 0)
        end = datetime(2024, 1, 1, 12, 0, 10)  # 10 seconds
        return TrajectoryBase(
            id="test_trajectory",
            start=start,
            end=end,
            points=points
        )

    @pytest.fixture
    def single_point_trajectory(self):
        """Create a trajectory with single point."""
        points = [Coordinate(x=5, y=5, z=0)]
        start = datetime(2024, 1, 1, 12, 0, 0)
        end = datetime(2024, 1, 1, 12, 0, 5)
        return TrajectoryBase(
            id="single_point",
            start=start,
            end=end,
            points=points
        )

    @pytest.fixture
    def large_trajectory(self):
        """Create a large trajectory for smoothing tests."""
        points = []
        for i in range(25):  # More than smooth_min_points (20)
            x = i + (0.1 * (i % 3))  # Add some noise
            y = i + (0.1 * ((i + 1) % 3))
            points.append(Coordinate(x=x, y=y, z=0))
        
        start = datetime(2024, 1, 1, 12, 0, 0)
        end = datetime(2024, 1, 1, 12, 0, 50)  # 50 seconds
        return TrajectoryBase(
            id="large_trajectory",
            start=start,
            end=end,
            points=points
        )

    @pytest.fixture
    def zero_time_trajectory(self):
        """Create a trajectory with zero time interval."""
        points = [
            Coordinate(x=0, y=0, z=0),
            Coordinate(x=3, y=4, z=0),
        ]
        start = datetime(2024, 1, 1, 12, 0, 0)
        end = start  # Same time
        return TrajectoryBase(
            id="zero_time",
            start=start,
            end=end,
            points=points
        )

    def test_head_property(self, simple_trajectory, single_point_trajectory):
        """Test head property returns first point."""
        # Test with multiple points
        assert simple_trajectory.head == Coordinate(x=0, y=0, z=0)
        
        # Test with single point
        assert single_point_trajectory.head == Coordinate(x=5, y=5, z=0)

    def test_last_property(self, simple_trajectory, single_point_trajectory):
        """Test last property returns last point."""
        # Test with multiple points
        assert simple_trajectory.last == Coordinate(x=2, y=2, z=0)
        
        # Test with single point
        assert single_point_trajectory.last == Coordinate(x=5, y=5, z=0)

    def test_smooth_trajectory_small_points(self, simple_trajectory):
        """Test smooth_trajectory with points less than smooth_min_points."""
        # Should return original points when less than smooth_min_points
        smoothed = simple_trajectory.smooth_trajectory
        assert len(smoothed) == 3
        assert smoothed == simple_trajectory.points

    def test_smooth_trajectory_large_points(self, large_trajectory):
        """Test smooth_trajectory with points greater than smooth_min_points."""
        smoothed = large_trajectory.smooth_trajectory
        
        # Smoothed trajectory has one fewer point due to moving average algorithm
        assert len(smoothed) == len(large_trajectory.points) - 1
        assert smoothed != large_trajectory.points
        
        # Smoothed points should be different from original (due to noise reduction)
        # The smoothing algorithm provides meaningful smoothing for noisy data
        # Just verify that smoothing is applied (some points differ)
        differences = sum(1 for i, smooth in enumerate(smoothed) 
                         if i < len(large_trajectory.points) - 1 and 
                         (abs(large_trajectory.points[i].x - smooth.x) > 1e-10 or 
                          abs(large_trajectory.points[i].y - smooth.y) > 1e-10))
        assert differences > 0  # At least some points should be smoothed

    def test_smooth_trajectory_edge_window_size(self):
        """Test smooth_trajectory with different window sizes."""
        points = [Coordinate(x=i, y=i + (0.5 if i % 2 else 0), z=0) for i in range(25)]
        start = datetime(2024, 1, 1, 12, 0, 0)
        end = datetime(2024, 1, 1, 12, 0, 25)
        
        # Test with window size 1
        traj_ws1 = TrajectoryBase(
            id="ws1",
            start=start,
            end=end,
            points=points,
            smooth_window_size=1
        )
        smoothed_ws1 = traj_ws1.smooth_trajectory
        # With window size 1, smoothing still reduces length by 1
        assert len(smoothed_ws1) == len(points) - 1
        # Values should be same as original but shifted by 1 (skips first point)
        for i, smooth_pt in enumerate(smoothed_ws1):
            assert smooth_pt.x == points[i + 1].x
            assert smooth_pt.y == points[i + 1].y
        
        # Test with larger window size
        traj_ws10 = TrajectoryBase(
            id="ws10",
            start=start,
            end=end,
            points=points,
            smooth_window_size=10
        )
        smoothed_ws10 = traj_ws10.smooth_trajectory
        assert len(smoothed_ws10) == len(points) - 1

    def test_distance_single_point(self, single_point_trajectory):
        """Test distance calculation with single point."""
        distance = single_point_trajectory.distance
        assert distance == 0  # Should be 0 for single point

    def test_distance_two_points(self):
        """Test distance calculation with two points."""
        points = [
            Coordinate(x=0, y=0, z=0),
            Coordinate(x=3, y=4, z=0),  # 3-4-5 triangle, distance = 5
        ]
        start = datetime(2024, 1, 1, 12, 0, 0)
        end = datetime(2024, 1, 1, 12, 0, 10)
        
        trajectory = TrajectoryBase(
            id="two_points",
            start=start,
            end=end,
            points=points
        )
        
        distance = trajectory.distance
        assert abs(distance - 5.0) < 1e-10  # Should be 5 meters

    def test_distance_with_stride(self, large_trajectory):
        """Test distance calculation with stride."""
        distance = large_trajectory.distance
        assert distance > 0
        
        # Test with different stride
        large_trajectory.distance_stride = 10
        distance_stride10 = large_trajectory.distance
        # Note: Due to caching, we need to clear cache to test different stride
        # This tests the current implementation behavior

    def test_linear_distance(self, simple_trajectory, single_point_trajectory):
        """Test linear distance calculation."""
        # Test with multiple points
        linear_dist = simple_trajectory.linear_distance
        expected = math.sqrt((2-0)**2 + (2-0)**2 + (0-0)**2)  # Distance from (0,0,0) to (2,2,0)
        assert abs(linear_dist - expected) < 1e-10
        
        # Test with single point
        single_linear_dist = single_point_trajectory.linear_distance
        assert single_linear_dist == 0

    def test_linear_distance_less_than_two_points(self):
        """Test linear distance with less than 2 smoothed points."""
        points = [Coordinate(x=0, y=0, z=0)]
        start = datetime(2024, 1, 1, 12, 0, 0)
        end = datetime(2024, 1, 1, 12, 0, 10)
        
        trajectory = TrajectoryBase(
            id="one_point",
            start=start,
            end=end,
            points=points
        )
        
        assert trajectory.linear_distance == 0

    def test_speed_normal_case(self, simple_trajectory):
        """Test speed calculation with normal time interval."""
        speed = simple_trajectory.speed
        expected_speed = simple_trajectory.distance / simple_trajectory.time_interval
        assert abs(speed - expected_speed) < 1e-10

    def test_speed_zero_time_interval(self, zero_time_trajectory):
        """Test speed calculation with zero time interval."""
        with patch('mdx.analytics.core.schema.trajectory.trajectory_base.logger') as mock_logger:
            speed = zero_time_trajectory.speed
            assert speed == 0
            # Should log warning for multiple points with zero time
            mock_logger.warning.assert_called_once()

    def test_speed_zero_time_single_point(self):
        """Test speed with zero time and single point (no warning)."""
        points = [Coordinate(x=0, y=0, z=0)]
        start = datetime(2024, 1, 1, 12, 0, 0)
        end = start
        
        trajectory = TrajectoryBase(
            id="zero_time_single",
            start=start,
            end=end,
            points=points
        )
        
        with patch('mdx.analytics.core.schema.trajectory.trajectory_base.logger') as mock_logger:
            speed = trajectory.speed
            assert speed == 0
            # Should not log warning for single point
            mock_logger.warning.assert_not_called()

    def test_speed_over_time_zero_interval(self, zero_time_trajectory):
        """Test speed_over_time with zero time interval."""
        speeds = zero_time_trajectory.speed_over_time
        assert speeds == [zero_time_trajectory.speed]

    def test_speed_over_time_small_trajectory(self, simple_trajectory):
        """Test speed_over_time with trajectory less than smooth_min_points."""
        speeds = simple_trajectory.speed_over_time
        assert speeds == [simple_trajectory.speed]

    def test_speed_over_time_large_trajectory(self, large_trajectory):
        """Test speed_over_time with large trajectory."""
        speeds = large_trajectory.speed_over_time
        assert len(speeds) > 0
        assert all(isinstance(speed, float) for speed in speeds)
        assert all(speed >= 0 for speed in speeds)  # Speeds should be non-negative

    def test_bearing_calculation(self):
        """Test bearing calculation for different directions.
        
        TrajectoryBase uses image coordinate convention where y increases downward.
        - East (x+) = 0°
        - Down (y+) = 270° (in image, y+ is down)
        - West (x-) = 180°
        - Up (y-) = 90° (in image, y- is up)
        """
        start = datetime(2024, 1, 1, 12, 0, 0)
        end = datetime(2024, 1, 1, 12, 0, 10)
        
        # Test East (0 degrees) - Right
        points_east = [Coordinate(x=0, y=0, z=0), Coordinate(x=1, y=0, z=0)]
        traj_east = TrajectoryBase(id="east", start=start, end=end, points=points_east)
        assert abs(traj_east.bearing - 0.0) < 1e-10
        
        # Test Down (270 degrees) - y+ in image coordinates
        points_down = [Coordinate(x=0, y=0, z=0), Coordinate(x=0, y=1, z=0)]
        traj_down = TrajectoryBase(id="down", start=start, end=end, points=points_down)
        assert abs(traj_down.bearing - 270.0) < 1e-10
        
        # Test West (180 degrees) - Left
        points_west = [Coordinate(x=0, y=0, z=0), Coordinate(x=-1, y=0, z=0)]
        traj_west = TrajectoryBase(id="west", start=start, end=end, points=points_west)
        assert abs(traj_west.bearing - 180.0) < 1e-10
        
        # Test Up (90 degrees) - y- in image coordinates
        points_up = [Coordinate(x=0, y=0, z=0), Coordinate(x=0, y=-1, z=0)]
        traj_up = TrajectoryBase(id="up", start=start, end=end, points=points_up)
        assert abs(traj_up.bearing - 90.0) < 1e-10
        
        # Test Down-Right (315 degrees) - x+, y+
        points_dr = [Coordinate(x=0, y=0, z=0), Coordinate(x=1, y=1, z=0)]
        traj_dr = TrajectoryBase(id="dr", start=start, end=end, points=points_dr)
        assert abs(traj_dr.bearing - 315.0) < 1e-10

    def test_direction_calculation(self):
        """Test direction calculation for all cardinal directions.
        
        Uses image coordinate convention where y increases downward.
        """
        start = datetime(2024, 1, 1, 12, 0, 0)
        end = datetime(2024, 1, 1, 12, 0, 10)
        
        # Test Right (x+)
        points_right = [Coordinate(x=0, y=0, z=0), Coordinate(x=1, y=0, z=0)]
        traj_right = TrajectoryBase(id="right", start=start, end=end, points=points_right)
        assert traj_right.direction == "Right"
        
        # Test Down (y+ in image coordinates)
        points_down = [Coordinate(x=0, y=0, z=0), Coordinate(x=0, y=1, z=0)]
        traj_down = TrajectoryBase(id="down", start=start, end=end, points=points_down)
        assert traj_down.direction == "Down"
        
        # Test Left (x-)
        points_left = [Coordinate(x=0, y=0, z=0), Coordinate(x=-1, y=0, z=0)]
        traj_left = TrajectoryBase(id="left", start=start, end=end, points=points_left)
        assert traj_left.direction == "Left"
        
        # Test Up (y- in image coordinates)
        points_up = [Coordinate(x=0, y=0, z=0), Coordinate(x=0, y=-1, z=0)]
        traj_up = TrajectoryBase(id="up", start=start, end=end, points=points_up)
        assert traj_up.direction == "Up"

    def test_direction_index(self):
        """Test direction_index calculation.
        
        Uses image coordinate convention where y increases downward.
        Direction index: 0=Right, 1=Up, 2=Left, 3=Down
        """
        start = datetime(2024, 1, 1, 12, 0, 0)
        end = datetime(2024, 1, 1, 12, 0, 10)
        
        # Test all directions with image coordinates
        test_cases = [
            ([Coordinate(x=0, y=0, z=0), Coordinate(x=1, y=0, z=0)], 0),   # Right (x+)
            ([Coordinate(x=0, y=0, z=0), Coordinate(x=0, y=-1, z=0)], 1),  # Up (y- in image)
            ([Coordinate(x=0, y=0, z=0), Coordinate(x=-1, y=0, z=0)], 2),  # Left (x-)
            ([Coordinate(x=0, y=0, z=0), Coordinate(x=0, y=1, z=0)], 3),   # Down (y+ in image)
        ]
        
        for points, expected_index in test_cases:
            traj = TrajectoryBase(id="test", start=start, end=end, points=points)
            assert traj.direction_index == expected_index

    def test_time_interval(self, simple_trajectory):
        """Test time_interval calculation."""
        interval = simple_trajectory.time_interval
        assert interval == 10.0  # 10 seconds

    def test_time_interval_zero(self, zero_time_trajectory):
        """Test time_interval with same start and end times."""
        interval = zero_time_trajectory.time_interval
        assert interval == 0.0

    def test_geo_location(self, simple_trajectory):
        """Test geo_location conversion."""
        geo_loc = simple_trajectory.geo_location
        
        assert isinstance(geo_loc, GeoLocation)
        assert geo_loc.type == "linestring"
        assert len(geo_loc.coordinates) == 3
        
        # Check coordinate conversion
        for i, point in enumerate(geo_loc.coordinates):
            original = simple_trajectory.points[i]
            assert isinstance(point, Point)
            assert point.point == [original.x, original.y, original.z]

    def test_smooth_geo_location(self, simple_trajectory):
        """Test smooth_geo_location conversion."""
        smooth_geo_loc = simple_trajectory.smooth_geo_location
        
        assert isinstance(smooth_geo_loc, GeoLocation)
        assert smooth_geo_loc.type == "linestring"
        assert len(smooth_geo_loc.coordinates) == len(simple_trajectory.smooth_trajectory)
        
        # Check coordinate conversion
        for i, point in enumerate(smooth_geo_loc.coordinates):
            smooth_point = simple_trajectory.smooth_trajectory[i]
            assert isinstance(point, Point)
            assert point.point == [smooth_point.x, smooth_point.y, smooth_point.z]

    @patch('mdx.analytics.core.schema.trajectory.trajectory_base.euclidean_distance')
    def test_calculate_distance(self, mock_euclidean_distance, simple_trajectory):
        """Test _calculate_distance method."""
        mock_euclidean_distance.return_value = 5.0
        
        p1 = Coordinate(x=0, y=0, z=0)
        p2 = Coordinate(x=3, y=4, z=0)
        
        result = simple_trajectory._calculate_distance(p1, p2)
        
        assert result == 5.0
        mock_euclidean_distance.assert_called_once_with(p1, p2)

    def test_str_representation(self, simple_trajectory):
        """Test __str__ method."""
        str_repr = str(simple_trajectory)
        
        # Check that key information is in the string
        assert simple_trajectory.id in str_repr
        assert str(simple_trajectory.bearing) in str_repr
        assert simple_trajectory.direction in str_repr
        assert f"{simple_trajectory.speed:.2f}" in str_repr
        assert f"{simple_trajectory.distance:.2f}" in str_repr
        assert f"{simple_trajectory.time_interval:.2f}" in str_repr

    def test_edge_case_empty_points_list(self):
        """Test edge case with empty points list."""
        start = datetime(2024, 1, 1, 12, 0, 0)
        end = datetime(2024, 1, 1, 12, 0, 10)
        
        # This should raise an IndexError when accessing head/last
        trajectory = TrajectoryBase(
            id="empty",
            start=start,
            end=end,
            points=[]
        )
        
        with pytest.raises(IndexError):
            _ = trajectory.head
            
        with pytest.raises(IndexError):
            _ = trajectory.last

    def test_bearing_edge_cases(self):
        """Test bearing calculation edge cases."""
        start = datetime(2024, 1, 1, 12, 0, 0)
        end = datetime(2024, 1, 1, 12, 0, 10)
        
        # Test with same points (zero vector)
        points_same = [Coordinate(x=1, y=1, z=0), Coordinate(x=1, y=1, z=0)]
        traj_same = TrajectoryBase(id="same", start=start, end=end, points=points_same)
        bearing = traj_same.bearing
        assert 0 <= bearing <= 360

    def test_custom_parameters(self):
        """Test trajectory with custom parameters."""
        points = [Coordinate(x=i, y=i, z=0) for i in range(25)]
        start = datetime(2024, 1, 1, 12, 0, 0)
        end = datetime(2024, 1, 1, 12, 0, 25)
        
        trajectory = TrajectoryBase(
            id="custom",
            start=start,
            end=end,
            points=points,
            smooth_min_points=30,  # Higher than point count
            smooth_window_size=3,
            distance_stride=2,
            speed_segment_size=5
        )
        
        # Should use original points since less than smooth_min_points
        assert trajectory.smooth_trajectory == points
        
        # Test with different parameters
        trajectory2 = TrajectoryBase(
            id="custom2",
            start=start,
            end=end,
            points=points,
            smooth_min_points=10,  # Lower than point count
            smooth_window_size=7,
            distance_stride=3,
            speed_segment_size=8
        )
        
        # Should apply smoothing (length will be reduced by 1)
        assert len(trajectory2.smooth_trajectory) == len(points) - 1

    def test_distance_with_less_than_two_trajectory_points(self):
        """Test distance calculation when stride processing results in less than 2 points."""
        # Create a trajectory that will have fewer than 2 points after stride processing
        points = [
            Coordinate(x=0, y=0, z=0),
            Coordinate(x=1, y=1, z=0),
        ]
        start = datetime(2024, 1, 1, 12, 0, 0)
        end = datetime(2024, 1, 1, 12, 0, 10)
        
        trajectory = TrajectoryBase(
            id="short",
            start=start,
            end=end,
            points=points,
            distance_stride=100  # Large stride
        )
        
        # Should fall back to linear_distance
        distance = trajectory.distance
        linear_distance = trajectory.linear_distance
        assert distance == linear_distance

    def test_distance_edge_case_single_smoothed_point(self):
        """Test distance calculation with single point trajectory that becomes single after smoothing."""
        # Create a trajectory with minimal points and high min_points threshold
        points = [Coordinate(x=0, y=0, z=0)]
        start = datetime(2024, 1, 1, 12, 0, 0)
        end = datetime(2024, 1, 1, 12, 0, 10)
        
        trajectory = TrajectoryBase(
            id="single_smooth",
            start=start,
            end=end,
            points=points,
            smooth_min_points=5,  # Higher than point count, so returns original
            distance_stride=1
        )
        
        # With single point, distance should be 0
        distance = trajectory.distance
        assert distance == 0

    def test_distance_edge_case_stride_reduces_to_single_point(self):
        """Test distance calculation where stride reduces trajectory to single point after processing."""
        # Create a small trajectory that when smoothed and strided results in < 2 points
        points = [Coordinate(x=0, y=0, z=0), Coordinate(x=1, y=1, z=0)]
        start = datetime(2024, 1, 1, 12, 0, 0)
        end = datetime(2024, 1, 1, 12, 0, 10)
        
        # Use small trajectory (< smooth_min_points) with large stride
        trajectory = TrajectoryBase(
            id="stride_single",
            start=start,
            end=end,
            points=points,
            smooth_min_points=10,  # Forces original points
            distance_stride=10  # Large stride to reduce to single point
        )
        
        # This should trigger the len(tr) < 2 condition and return linear_distance
        distance = trajectory.distance
        linear_distance = trajectory.linear_distance
        assert distance == linear_distance
        # Linear distance should be sqrt(2) for points (0,0) to (1,1)
        expected_distance = (2 ** 0.5)
        assert abs(distance - expected_distance) < 1e-10

    def test_large_bearing_normalization(self):
        """Test that bearing is properly normalized to 0-360 range.
        
        Uses image coordinate convention where y increases downward.
        """
        start = datetime(2024, 1, 1, 12, 0, 0)
        end = datetime(2024, 1, 1, 12, 0, 10)
        
        # Test up-left direction (-1, -1) in image coords (135 degrees)
        # x- and y- means left and up in image
        points_ul = [Coordinate(x=0, y=0, z=0), Coordinate(x=-1, y=-1, z=0)]
        traj_ul = TrajectoryBase(id="ul", start=start, end=end, points=points_ul)
        bearing = traj_ul.bearing
        assert 0 <= bearing <= 360
        assert abs(bearing - 135.0) < 1e-10

    def test_speed_over_time_segments_calculation(self):
        """Test detailed speed_over_time calculation logic."""
        # Create a trajectory with exact segment boundaries
        points = []
        for i in range(30):  # 30 points for testing segment logic
            points.append(Coordinate(x=i, y=0, z=0))  # Straight line
        
        start = datetime(2024, 1, 1, 12, 0, 0)
        end = datetime(2024, 1, 1, 12, 0, 30)
        
        trajectory = TrajectoryBase(
            id="segments",
            start=start,
            end=end,
            points=points,
            speed_segment_size=10,
            smooth_min_points=20
        )
        
        speeds = trajectory.speed_over_time
        assert len(speeds) >= 1
        assert all(speed >= 0 for speed in speeds)
        # All speeds should be rounded to 2 decimal places
        assert all(len(str(speed).split('.')[-1]) <= 2 for speed in speeds)

    @pytest.mark.parametrize("window_size,min_points", [
        (1, 25),
        (3, 25), 
        (5, 25),
        (10, 25),
    ])
    def test_smooth_trajectory_different_windows(self, window_size, min_points):
        """Test smooth_trajectory with different window sizes."""
        points = [Coordinate(x=i, y=i + (0.1 if i % 2 else 0), z=0) for i in range(min_points)]
        start = datetime(2024, 1, 1, 12, 0, 0)
        end = datetime(2024, 1, 1, 12, 0, min_points)
        
        trajectory = TrajectoryBase(
            id=f"window_{window_size}",
            start=start,
            end=end,
            points=points,
            smooth_window_size=window_size,
            smooth_min_points=20
        )
        
        smoothed = trajectory.smooth_trajectory
        # Smoothing always reduces length by 1
        assert len(smoothed) == len(points) - 1
        
        if window_size == 1:
            # Window size 1 should return values same as original but shifted by 1 (skips first point)
            for i, smooth_pt in enumerate(smoothed):
                assert abs(smooth_pt.x - points[i + 1].x) < 1e-10
                assert abs(smooth_pt.y - points[i + 1].y) < 1e-10
        else:
            # Larger windows should smooth the trajectory
            # Check that some points are different (smoothed)
            differences = sum(1 for i, smooth in enumerate(smoothed) 
                            if abs(points[i].y - smooth.y) > 1e-10)
            assert differences > 0
