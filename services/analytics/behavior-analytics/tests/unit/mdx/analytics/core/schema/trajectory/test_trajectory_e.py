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

import pytest
from datetime import datetime, timedelta
from mdx.analytics.core.schema.models import Coordinate
from mdx.analytics.core.schema.trajectory.trajectory_e import TrajectoryE


@pytest.fixture
def sample_trajectory():
    """Create a sample trajectory for testing"""
    points = [
        Coordinate(x=0.0, y=0.0, z=0.0),
        Coordinate(x=1.0, y=1.0, z=0.0),
        Coordinate(x=2.0, y=2.0, z=0.0),
        Coordinate(x=3.0, y=3.0, z=0.0),
    ]
    start_time = datetime(2024, 1, 1, 12, 0, 0)
    end_time = start_time + timedelta(seconds=30)
    return TrajectoryE(
        id="test_trajectory",
        start=start_time,
        end=end_time,
        points=points,
    )


def test_head_last():
    """Test head and last point getters"""
    points = [
        Coordinate(x=1.0, y=1.0, z=0.0),
        Coordinate(x=2.0, y=2.0, z=0.0),
    ]
    start = datetime.now()
    end = datetime.now()
    traj = TrajectoryE(
        id="test",
        start=start,
        end=end,
        points=points,
    )

    assert traj.head == points[0]
    assert traj.last == points[-1]


def test_smooth_trajectory(sample_trajectory):
    """Test trajectory smoothing"""
    smoothed = sample_trajectory.smooth_trajectory
    assert len(smoothed) == len(sample_trajectory.points)
    # Since points are linear, smoothed points should be similar
    assert abs(smoothed[1].x - sample_trajectory.points[1].x) < 0.1


def test_distance_calculations(sample_trajectory):
    """Test distance calculations"""
    # Linear distance should be less than or equal to total distance
    assert sample_trajectory.linear_distance <= sample_trajectory.distance
    assert sample_trajectory.distance > 0


def test_speed_calculations(sample_trajectory):
    """Test speed calculations"""
    assert sample_trajectory.speed > 0
    assert len(sample_trajectory.speed_over_time) > 0


def test_bearing_and_direction():
    """Test bearing and direction calculations"""
    # Test right direction (0 degrees)
    points = [
        Coordinate(x=0.0, y=0.0, z=0.0),
        Coordinate(x=1.0, y=0.0, z=0.0),
    ]
    start = datetime.now()
    end = datetime.now()
    traj = TrajectoryE(
        id="test",
        start=start,
        end=end,
        points=points,
    )
    assert traj.direction == "Right"
    assert abs(traj.bearing - 0) < 0.1


def test_time_interval(sample_trajectory):
    """Test time interval calculation"""
    assert sample_trajectory.time_interval == 30.0  # 30 seconds from fixture


def test_geo_location(sample_trajectory):
    """Test geo location conversions"""
    geo_loc = sample_trajectory.geo_location
    assert geo_loc.type == "linestring"
    assert len(geo_loc.coordinates) == len(sample_trajectory.points)


def test_empty_trajectory():
    """Test handling of minimal trajectory"""
    points = [Coordinate(x=0.0, y=0.0, z=0.0)]
    start = datetime.now()
    end = datetime.now()
    traj = TrajectoryE(
        id="test",
        start=start,
        end=end,
        points=points,
    )
    assert traj.linear_distance == 0
    assert traj.speed == 0


def test_str_representation(sample_trajectory):
    """Test string representation"""
    str_repr = str(sample_trajectory)
    assert sample_trajectory.id in str_repr
    assert "mph" in str_repr
    assert "meters" in str_repr


def test_complex_trajectory():
    """Test trajectory with complex movement patterns"""
    # Create a trajectory with varying speeds and directions
    points = [
        Coordinate(x=0.0, y=0.0, z=0.0),
        Coordinate(x=1.0, y=0.0, z=0.0),  # Right
        Coordinate(x=1.0, y=1.0, z=0.0),  # Up
        Coordinate(x=0.0, y=1.0, z=0.0),  # Left
        Coordinate(x=0.0, y=0.0, z=0.0),  # Down
    ]
    start_time = datetime(2024, 1, 1, 12, 0, 0)
    end_time = start_time + timedelta(seconds=40)
    traj = TrajectoryE(
        id="complex_trajectory",
        start=start_time,
        end=end_time,
        points=points,
    )
    # Test distance calculations
    assert traj.linear_distance == 0  # Linear distance should be less than actual path

    # Test direction changes
    assert traj.direction in ["Right", "Up", "Left", "Down"]


def test_high_speed_trajectory():
    """Test trajectory with high speed movement"""
    points = [
        Coordinate(x=0.0, y=0.0, z=0.0),
        Coordinate(x=1000.0, y=1000.0, z=0.0),  # Large movement
    ]
    start_time = datetime(2024, 1, 1, 12, 0, 0)
    end_time = start_time + timedelta(seconds=1)  # Very short time interval
    traj = TrajectoryE(
        id="high_speed_trajectory",
        start=start_time,
        end=end_time,
        points=points,
    )

    assert traj.speed > 0
    assert traj.distance > 0
    assert traj.time_interval == 1.0


def test_vertical_movement():
    """Test trajectory with vertical movement"""
    points = [
        Coordinate(x=0.0, y=1, z=0.0),
        Coordinate(x=0.0, y=2, z=10.0),
        Coordinate(x=0.0, y=3, z=20.0),
    ]
    start_time = datetime(2024, 1, 1, 12, 0, 0)
    end_time = start_time + timedelta(seconds=20)
    traj = TrajectoryE(
        id="vertical_trajectory",
        start=start_time,
        end=end_time,
        points=points,
    )

    assert traj.distance > 0
    assert traj.speed > 0
    assert traj.direction == "Up"  # Should detect vertical movement


def test_equality_comparison():
    """Test trajectory equality comparison"""
    points1 = [Coordinate(x=0.0, y=0.0, z=0.0), Coordinate(x=1.0, y=1.0, z=0.0)]
    points2 = [Coordinate(x=0.0, y=0.0, z=0.0), Coordinate(x=1.0, y=1.0, z=0.0)]
    start_time = datetime(2024, 1, 1, 12, 0, 0)
    end_time = start_time + timedelta(seconds=10)

    traj1 = TrajectoryE(id="test1", start=start_time, end=end_time, points=points1)
    traj2 = TrajectoryE(id="test1", start=start_time, end=end_time, points=points2)
    traj3 = TrajectoryE(id="test2", start=start_time, end=end_time, points=points1)

    assert traj1 == traj2  # Same points and timing
    assert traj1 != traj3  # Different IDs
