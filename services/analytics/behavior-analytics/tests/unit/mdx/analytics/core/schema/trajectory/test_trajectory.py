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
from mdx.analytics.core.schema.trajectory.trajectory import Trajectory


@pytest.fixture
def sample_geo_trajectory():
    """Create a sample geo trajectory for testing"""
    points = [
        Coordinate(x=-122.4194, y=37.7749, z=0.0),  # San Francisco
        Coordinate(x=-122.3321, y=37.8085, z=0.0),  # Oakland
        Coordinate(x=-122.2711, y=37.8044, z=0.0),  # Berkeley
    ]
    start_time = datetime(2024, 1, 1, 12, 0, 0)
    end_time = start_time + timedelta(seconds=1800)  # 30 minutes
    return Trajectory(
        id="test_geo_trajectory",
        start=start_time,
        end=end_time,
        points=points,
        enable_geo=True
    )


def test_geo_distance_calculation(sample_geo_trajectory):
    """Test geo distance calculations using haversine formula"""
    # Distance should be calculated using haversine formula when geo is enabled
    assert sample_geo_trajectory.distance > 0
    assert sample_geo_trajectory.distance < 100000  # Should be less than 100km for SF to Berkeley


def test_geo_bearing_calculation(sample_geo_trajectory):
    """Test geo bearing calculations"""
    # Bearing should be calculated using geo coordinates
    bearing = sample_geo_trajectory.bearing
    assert 0 <= bearing <= 360
    # For SF to Berkeley, bearing should be roughly northeast
    assert 0 < bearing < 90


def test_direction_modes():
    """Test different direction modes for geo trajectories"""
    points = [
        Coordinate(x=-122.4194, y=37.7749, z=0.0),  # San Francisco
        Coordinate(x=-122.2711, y=37.8044, z=0.0),  # Berkeley
    ]
    start_time = datetime(2024, 1, 1, 12, 0, 0)
    end_time = start_time + timedelta(seconds=1800)

    # Test mode 0 (4 directions)
    traj_4dir = Trajectory(
        id="test_4dir",
        start=start_time,
        end=end_time,
        points=points,
        enable_geo=True,
        direction_mode=0,
        timestamps=[start_time, end_time]
    )
    assert traj_4dir.direction in ["N", "E", "S", "W"]

    # Test mode 1 (8 directions)
    traj_8dir = Trajectory(
        id="test_8dir",
        start=start_time,
        end=end_time,
        points=points,
        enable_geo=True,
        direction_mode=1,
        timestamps=[start_time, end_time]
    )
    assert traj_8dir.direction in ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]

    # Test mode 2 (16 directions)
    traj_16dir = Trajectory(
        id="test_16dir",
        start=start_time,
        end=end_time,
        points=points,
        enable_geo=True,
        direction_mode=2,
        timestamps=[start_time, end_time]
    )
    assert traj_16dir.direction in [
        "N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
        "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"
    ]


def test_direction_based_clustering():
    """Test direction-based clustering modes"""
    points = [
        Coordinate(x=-122.4194, y=37.7749, z=0.0),  # San Francisco
        Coordinate(x=-122.2711, y=37.8044, z=0.0),  # Berkeley
    ]
    start_time = datetime(2024, 1, 1, 12, 0, 0)
    end_time = start_time + timedelta(seconds=1800)

    # Test mode 0 (4 clusters)
    traj_4cluster = Trajectory(
        id="test_4cluster",
        start=start_time,
        end=end_time,
        points=points,
        enable_geo=True,
        direction_based_cluster_mode=0,
        timestamps=[start_time, end_time]
    )
    assert 0 <= traj_4cluster.direction_based_cluster_index < 4

    # Test mode 1 (8 clusters)
    traj_8cluster = Trajectory(
        id="test_8cluster",
        start=start_time,
        end=end_time,
        points=points,
        enable_geo=True,
        direction_based_cluster_mode=1,
        timestamps=[start_time, end_time]
    )
    assert 0 <= traj_8cluster.direction_based_cluster_index < 8


def test_geo_str_representation(sample_geo_trajectory):
    """Test string representation of geo trajectory"""
    str_repr = str(sample_geo_trajectory)
    assert sample_geo_trajectory.id in str_repr
    assert "mph" in str_repr
    assert "meters" in str_repr
    assert sample_geo_trajectory.direction in str_repr


def test_geo_vs_euclidean():
    """Test differences between geo and euclidean calculations"""
    points = [
        Coordinate(x=-122.4194, y=37.7749, z=0.0),  # San Francisco
        Coordinate(x=-122.2711, y=37.8044, z=0.0),  # Berkeley
    ]
    start_time = datetime(2024, 1, 1, 12, 0, 0)
    end_time = start_time + timedelta(seconds=1800)

    # Geo trajectory
    geo_traj = Trajectory(
        id="geo",
        start=start_time,
        end=end_time,
        points=points,
        enable_geo=True,
        timestamps=[start_time, end_time]
    )

    # Euclidean trajectory
    euclid_traj = Trajectory(
        id="euclid",
        start=start_time,
        end=end_time,
        points=points,
        enable_geo=False,
        timestamps=[start_time, end_time]
    )

    # Distances should be different
    assert geo_traj.distance != euclid_traj.distance
    # Geo distance should be larger (actual ground distance)
    assert geo_traj.distance > euclid_traj.distance
