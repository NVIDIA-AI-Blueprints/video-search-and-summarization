# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

from functools import cached_property

from pydantic import computed_field

from mdx.analytics.core.schema.models import Coordinate, Location
from mdx.analytics.core.schema.trajectory.trajectory_base import TrajectoryBase
from mdx.analytics.core.utils.distance_util import MPS_TO_MPH, bearing, haversine_distance_coords


class Trajectory(TrajectoryBase):
    """
    Geographic trajectory representation with configurable direction modes.

    This class extends TrajectoryBase to provide trajectory analysis in geographic space
    with configurable direction modes and clustering options. It supports both Euclidean
    and geographic coordinate systems with customizable direction representations.

    :ivar str id: Unique identifier for the trajectory
    :ivar datetime start: Start time of the trajectory
    :ivar datetime end: End time of the trajectory
    :ivar list[Coordinate] points: List of coordinate points forming the trajectory
    :ivar bool enable_geo: Enable geographic coordinate system (default: False)
    :ivar int direction_mode: Direction representation mode (default: 0)
        - 0: Basic cardinal directions (N, E, S, W)
        - 1: Intercardinal directions (N, NE, E, SE, S, SW, W, NW)
        - 2: Secondary intercardinal directions (16 directions)
    :ivar int direction_based_cluster_mode: Direction clustering mode (default: 1)
        - 0: 4-direction clustering
        - 1: 8-direction clustering
        - 2: 16-direction clustering
    :ivar int smooth_min_points: Minimum number of points required for computing smoothed trajectory and speed over time (default: 20)
    :ivar int smooth_window_size: Window size for smoothing (default: 5)
    :ivar int distance_stride: Stride for distance calculation (default: 5)
    :ivar int speed_segment_size: Size of speed segment for speed over time calculation (default: 10)

    Examples::

        # Create a geographic trajectory with different direction modes
        from datetime import datetime
        from mdx.analytics.core.schema.models import Coordinate

        # Create a trajectory moving northeast
        points = [
            Coordinate(x=-74.0060, y=40.7128, z=0),  # New York
            Coordinate(x=-73.9352, y=40.7306, z=0),  # Brooklyn
            Coordinate(x=-73.7781, y=40.6413, z=0)   # JFK Airport
        ]

        # Basic cardinal directions (N, E, S, W)
        trajectory_basic = Trajectory(
            id="traj_geo1",
            start=datetime.now(),
            end=datetime.now(),
            points=points,
            enable_geo=True,
            direction_mode=0
        )
        print(f"Basic direction: {trajectory_basic.direction}")

        # Intercardinal directions (N, NE, E, SE, S, SW, W, NW)
        trajectory_inter = Trajectory(
            id="traj_geo2",
            start=datetime.now(),
            end=datetime.now(),
            points=points,
            enable_geo=True,
            direction_mode=1
        )
        print(f"Intercardinal direction: {trajectory_inter.direction}")
    """

    enable_geo: bool = False
    direction_mode: int = 0
    direction_based_cluster_mode: int = 1

    @computed_field
    @cached_property
    def bearing(self) -> float:
        """
        Calculate the bearing of the trajectory, supporting both geographic and Euclidean systems.

        :return float: Bearing in degrees (0-360)

        Example::

            # Geographic bearing (using latitude/longitude)
            points = [
                Coordinate(x=-74.0060, y=40.7128, z=0),  # New York
                Coordinate(x=-73.9352, y=40.7306, z=0)   # Brooklyn
            ]
            geo_trajectory = Trajectory(
                id="geo1",
                start=datetime.now(),
                end=datetime.now(),
                points=points,
                enable_geo=True
            )
            geo_bearing = geo_trajectory.bearing  # Returns geographic bearing
        """
        if self.enable_geo:
            from_locaton = Location(lat=self.head.y, lon=self.head.x)
            to_locaton = Location(lat=self.last.y, lon=self.last.x)
            return bearing(from_locaton, to_locaton)
        return super().bearing

    @computed_field
    @cached_property
    def direction(self) -> str:
        """
        Get the direction of the trajectory based on the configured direction mode.

        :return str: Direction string based on the current direction_mode

        Example::

            # Basic cardinal directions (N, E, S, W)
            points = [
                Coordinate(x=0, y=0, z=0),
                Coordinate(x=1, y=1, z=0)  # Northeast
            ]
            trajectory_basic = Trajectory(
                id="basic1",
                start=datetime.now(),
                end=datetime.now(),
                points=points,
                enable_geo=True,
                direction_mode=0
            )
            basic_dir = trajectory_basic.direction  # Returns "NE"

            # Intercardinal directions
            trajectory_inter = Trajectory(
                id="inter1",
                start=datetime.now(),
                end=datetime.now(),
                points=points,
                enable_geo=True,
                direction_mode=1
            )
            inter_dir = trajectory_inter.direction  # Returns "NE"

            # Secondary intercardinal directions
            trajectory_sec = Trajectory(
                id="sec1",
                start=datetime.now(),
                end=datetime.now(),
                points=points,
                enable_geo=True,
                direction_mode=2
            )
            sec_dir = trajectory_sec.direction  # Returns "ENE"
        """
        dirs = ["Right", "Up", "Left", "Down"]
        if self.enable_geo:
            if self.direction_mode == 0:
                dirs = ["N", "E", "S", "W"]
            elif self.direction_mode == 1:
                dirs = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
            elif self.direction_mode == 2:
                dirs = [
                    "N",
                    "NNE",
                    "NE",
                    "ENE",
                    "E",
                    "ESE",
                    "SE",
                    "SSE",
                    "S",
                    "SSW",
                    "SW",
                    "WSW",
                    "W",
                    "WNW",
                    "NW",
                    "NNW",
                ]
        return dirs[self.direction_index]

    @computed_field
    @cached_property
    def direction_index(self) -> int:
        """
        Get the index of the direction in the current direction mode's direction list.

        :return int: Index of the direction in the current direction mode's list

        Example::

            # Get direction index for different modes
            points = [
                Coordinate(x=0, y=0, z=0),
                Coordinate(x=1, y=1, z=0)  # Northeast
            ]

            # Basic mode (4 directions)
            trajectory_basic = Trajectory(
                id="basic1",
                start=datetime.now(),
                end=datetime.now(),
                points=points,
                enable_geo=True,
                direction_mode=0
            )
            basic_idx = trajectory_basic.direction_index  # Returns 1 (E)

            # Intercardinal mode (8 directions)
            trajectory_inter = Trajectory(
                id="inter1",
                start=datetime.now(),
                end=datetime.now(),
                points=points,
                enable_geo=True,
                direction_mode=1
            )
            inter_idx = trajectory_inter.direction_index  # Returns 2 (NE)
        """
        if self.enable_geo:
            if self.direction_mode == 0:
                return int(((self.bearing + 45) % 360) // 90)
            if self.direction_mode == 1:
                return int(((self.bearing + 22.5) % 360) // 45)
            if self.direction_mode == 2:
                return int(((self.bearing + 11.25) % 360) // 22.5)
        return super().direction_index

    @computed_field
    @cached_property
    def direction_based_cluster_index(self) -> int:
        """
        Get the index for direction-based clustering based on the current clustering mode.

        :return int: Index for direction-based clustering

        Example::

            # Get clustering index for different modes
            points = [
                Coordinate(x=0, y=0, z=0),
                Coordinate(x=1, y=1, z=0)  # Northeast
            ]

            # 4-direction clustering
            trajectory_4 = Trajectory(
                id="cluster4",
                start=datetime.now(),
                end=datetime.now(),
                points=points,
                enable_geo=True,
                direction_based_cluster_mode=0
            )
            idx_4 = trajectory_4.direction_based_cluster_index  # Returns 1 (E)

            # 8-direction clustering
            trajectory_8 = Trajectory(
                id="cluster8",
                start=datetime.now(),
                end=datetime.now(),
                points=points,
                enable_geo=True,
                direction_based_cluster_mode=1
            )
            idx_8 = trajectory_8.direction_based_cluster_index  # Returns 2 (NE)
        """
        if self.enable_geo:
            if self.direction_based_cluster_mode == 0:
                return int(((self.bearing + 45) % 360) // 90)
            if self.direction_based_cluster_mode == 1:
                return int(((self.bearing + 22.5) % 360) // 45)
            if self.direction_based_cluster_mode == 2:
                return int(((self.bearing + 11.25) % 360) // 22.5)
        return super().direction_index

    @computed_field
    @cached_property
    def speed(self) -> float:
        """
        Calculate the average speed of the trajectory

        :return float: Average speed in mph
        """
        return super().speed * MPS_TO_MPH

    @computed_field
    @cached_property
    def speed_over_time(self) -> list[float]:
        """
        Calculate the speed of the trajectory over time in segments.

        :return list[float]: List of speeds for each segment
        """
        return [MPS_TO_MPH * x for x in super().speed_over_time]

    def _calculate_distance(self, p1: Coordinate, p2: Coordinate) -> float:
        """
        Calculate distance between two points.

        :param Coordinate p1: The first point
        :param Coordinate p2: The second point
        :return float: Distance between the two points
        """
        if self.enable_geo:
            return haversine_distance_coords(p1, p2)
        return super()._calculate_distance(p1, p2)

    def __str__(self) -> str:
        """
        Get a string representation of the trajectory.

        :return str: String representation of the trajectory
        """
        return (
            f"Moving angle {self.bearing}, dir {self.direction:>3}, speed at {self.speed:4.2f} mph, "
            f"covered {self.distance:4.2f} meters in {self.time_interval:4.2f} seconds, id = {self.id}"
        )
