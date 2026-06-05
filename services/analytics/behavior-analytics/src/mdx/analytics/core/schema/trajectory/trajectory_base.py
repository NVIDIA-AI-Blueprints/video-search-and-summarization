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

import logging
import math
from datetime import datetime
from functools import cached_property

import numpy as np
from pydantic import BaseModel, computed_field

from mdx.analytics.core.schema.models import Coordinate, GeoLocation, Point
from mdx.analytics.core.utils.distance_util import euclidean_distance

logger = logging.getLogger(__name__)


class TrajectoryBase(BaseModel):
    """
    Base class for trajectory representation and analysis.

    This class provides fundamental functionality for working with trajectories, including:
    - Point storage and access
    - Trajectory smoothing
    - Distance calculations
    - Speed calculations
    - Direction and bearing calculations
    - Geographic location representation

    :ivar str id: Unique identifier for the trajectory
    :ivar datetime start: Start time of the trajectory
    :ivar datetime end: End time of the trajectory
    :ivar list[Coordinate] points: List of coordinate points forming the trajectory
    :ivar int smooth_min_points: Minimum number of points required for computing smoothed trajectory and speed over time (default: 20)
    :ivar int smooth_window_size: Window size for smoothing  (default: 5)
    :ivar int distance_stride: Stride for distance calculation (default: 5)
    :ivar int speed_segment_size: Size of speed segment for speed over time calculation (default: 10)

    Examples::

        # Basic trajectory creation
        from datetime import datetime
        from mdx.analytics.core.schema.models import Coordinate

        # Create a simple trajectory with 3 points
        points = [Coordinate(x=0, y=0, z=0), Coordinate(x=1, y=1, z=0)]
        trajectory = TrajectoryBase(
            id="traj1",
            start=datetime.now(),
            end=datetime.now(),
            points=points
        )

        # Access trajectory properties
        print(f"Total distance: {trajectory.distance} meters")
        print(f"Average speed: {trajectory.speed} m/s")
        print(f"Direction: {trajectory.direction}")
        print(f"Bearing: {trajectory.bearing} degrees")

        # Get smoothed trajectory
        smoothed_points = trajectory.smooth_trajectory

        # Get speed over time segments
        speeds = trajectory.speed_over_time
    """

    id: str
    start: datetime
    end: datetime
    points: list[Coordinate]
    smooth_min_points: int = 20
    smooth_window_size: int = 5
    distance_stride: int = 5
    speed_segment_size: int = 10

    @computed_field
    @cached_property
    def head(self) -> Coordinate:
        """
        Get the first point of the trajectory.

        :return Coordinate: First coordinate point
        """
        return self.points[0]

    @computed_field
    @cached_property
    def last(self) -> Coordinate:
        """
        Get the last point of the trajectory.

        :return Coordinate: Last coordinate point
        """
        return self.points[-1]

    @computed_field
    @cached_property
    def smooth_trajectory(self) -> list[Coordinate]:
        """
        Get a smoothed version of the trajectory using a moving average window.

        :return list[Coordinate]: List of smoothed coordinates

        Example::

            # Create a trajectory with some noise
            points = [
                Coordinate(x=0, y=0, z=0),
                Coordinate(x=1, y=1.1, z=0),  # Slight noise
                Coordinate(x=2, y=2, z=0),
                Coordinate(x=3, y=2.9, z=0),  # Slight noise
                Coordinate(x=4, y=4, z=0)
            ]
            trajectory = TrajectoryBase(
                id="1",
                start=datetime.now(),
                end=datetime.now(),
                points=points,
                moving_avg_window=3
            )

            # Get smoothed points
            smoothed = trajectory.smooth_trajectory
            # The y-coordinates will be averaged over the window size
            # reducing the effect of noise
        """
        if len(self.points) < self.smooth_min_points:
            return self.points

        # Convert points to numpy arrays for vectorized operations
        points_array = np.array([[p.x, p.y, p.z] for p in self.points])

        # Create a padded array to handle the window at the start
        padded = np.pad(points_array, ((self.smooth_window_size - 1, 0), (0, 0)), mode='edge')

        # Calculate cumulative sum for efficient moving average
        cumsum = np.cumsum(padded, axis=0)

        # Calculate moving average using cumulative sum
        # Ensure both arrays have the same shape by adjusting the slicing
        window_size = self.smooth_window_size
        smoothed = (cumsum[window_size:] - cumsum[:-window_size]) / window_size

        # Convert back to Coordinate objects
        return [Coordinate(x=float(x), y=float(y), z=float(z)) for x, y, z in smoothed]

    @computed_field
    @cached_property
    def distance(self) -> float:
        """
        Calculate the total distance of the smoothed trajectory.

        :return float: Distance in meters

        Example::

            # Create a trajectory with known distance
            points = [
                Coordinate(x=0, y=0, z=0),
                Coordinate(x=3, y=4, z=0)  # 5 meters from origin (3-4-5 triangle)
            ]
            trajectory = TrajectoryBase(
                id="1",
                start=datetime.now(),
                end=datetime.now(),
                points=points
            )
            total_distance = trajectory.distance  # Returns approximately 5.0
        """
        tr = [self.smooth_trajectory[i] for i in range(0, len(self.smooth_trajectory), self.distance_stride)] + [
            self.last
        ]

        if len(tr) < 2:
            return self.linear_distance
        return sum(self._calculate_distance(tr[i], tr[i + 1]) for i in range(len(tr) - 1))

    @computed_field
    @cached_property
    def linear_distance(self) -> float:
        """
        Calculate the linear distance between the first and last points of the smoothed trajectory.

        :return float: Linear distance in meter
        """
        p1 = self.smooth_trajectory[0]
        p2 = self.smooth_trajectory[-1]
        if len(self.smooth_trajectory) < 2:
            return 0
        return self._calculate_distance(p1, p2)

    @computed_field
    @cached_property
    def speed(self) -> float:
        """
        Calculate the average speed of the trajectory.

        :return float: Average speed in meters per second

        Example::

            from datetime import datetime, timedelta

            # Create a trajectory that moves 10 meters in 2 seconds
            points = [
                Coordinate(x=0, y=0, z=0),
                Coordinate(x=6, y=8, z=0)  # 10 meters from origin
            ]
            start = datetime.now()
            end = start + timedelta(seconds=2)

            trajectory = TrajectoryBase(
                id="1",
                start=start,
                end=end,
                points=points
            )

            avg_speed = trajectory.speed  # Returns 5.0 m/s (10 meters / 2 seconds)
        """
        if self.time_interval == 0:
            if len(self.points) >= 2:
                logger.warning(f"Time interval is 0 for trajectory {self.id} with {len(self.points)} points")
            return 0
        return self.distance / self.time_interval

    @computed_field
    @cached_property
    def speed_over_time(self) -> list[float]:
        """
        Calculate the speed of the trajectory over time in segments.

        :return list[float]: List of speeds for each segment
        """
        if self.time_interval == 0:
            return [self.speed]

        if len(self.smooth_trajectory) < self.smooth_min_points:
            return [self.speed]

        segment_size = self.speed_segment_size
        slices = [
            self.smooth_trajectory[i : i + segment_size] for i in range(0, len(self.smooth_trajectory), segment_size)
        ]
        last_segment = self.smooth_trajectory[-segment_size:]
        interval = self.time_interval * (segment_size - 1) / (len(self.smooth_trajectory) - 1)

        trimmed_slices = slices[:-1]
        trimmed_slices_speeds = [
            sum(self._calculate_distance(x[i], x[i + 1]) for i in range(len(x) - 1)) / interval for x in trimmed_slices
        ]

        last_segment_speed = (
            sum(self._calculate_distance(last_segment[i], last_segment[i + 1]) for i in range(len(last_segment) - 1))
            / interval
        )

        speeds_over_time = trimmed_slices_speeds + [last_segment_speed]
        speeds_over_time = [
            (speeds_over_time[i] + speeds_over_time[i + 1]) / 2 for i in range(len(speeds_over_time) - 1)
        ]

        return [round(x, 2) for x in speeds_over_time]

    @computed_field
    @cached_property
    def bearing(self) -> float:
        """
        Calculate the bearing of the trajectory from the first to the last point, with y-axis inverted.

        This uses image coordinate convention where y increases downward.

        :return float: Bearing in degrees (0-360)

        Example::

            # Create a trajectory moving right in image coordinates
            points = [
                Coordinate(x=0, y=0, z=0),
                Coordinate(x=1, y=0, z=0)
            ]
            trajectory = TrajectoryBase(
                id="1",
                start=datetime.now(),
                end=datetime.now(),
                points=points
            )

            angle = trajectory.bearing  # Returns 0.0 degrees (right)
        """
        brng = math.atan2(-self.last.y + self.head.y, self.last.x - self.head.x) * 180 / math.pi
        return (brng + 360) % 360

    @computed_field
    @cached_property
    def direction(self) -> str:
        """
        Get the cardinal direction of the trajectory.

        :return str: Cardinal direction ("Right", "Up", "Left", "Down")

        Example::

            # Create a trajectory moving upward
            points = [
                Coordinate(x=0, y=0, z=0),
                Coordinate(x=0, y=1, z=0)
            ]
            trajectory = TrajectoryBase(
                id="1",
                start=datetime.now(),
                end=datetime.now(),
                points=points
            )

            direction = trajectory.direction  # Returns "Up"
        """
        directions = ["Right", "Up", "Left", "Down"]
        return directions[int((self.bearing / 90) + 0.5) % 4]

    @computed_field
    @cached_property
    def direction_index(self) -> int:
        """
        Get the cardinal direction index of the trajectory.

        :return int: Cardinal direction index of list ("Right", "Up", "Left", "Down")
        """
        return int((self.bearing / 90) + 0.5) % 4

    @computed_field
    @cached_property
    def time_interval(self) -> float:
        """
        Calculate the time interval of the trajectory.

        :return float: Time interval in seconds
        """
        return (self.end - self.start).total_seconds()

    @computed_field
    @cached_property
    def geo_location(self) -> GeoLocation:
        """
        Get the geographical location of the trajectory as a GeoLocation object.

        :return GeoLocation: GeoLocation object representing the trajectory
        """
        geo_location = GeoLocation(type="linestring")
        for pt in self.points:
            coordinate = Point()
            coordinate.point.extend([pt.x, pt.y, pt.z])
            geo_location.coordinates.append(coordinate)
        return geo_location

    @computed_field
    @cached_property
    def smooth_geo_location(self) -> GeoLocation:
        """
        Get the smoothed geographical location of the trajectory as a GeoLocation object.

        :return GeoLocation: GeoLocation object representing the smoothed trajectory
        """
        # Create GeoLocation with pre-allocated coordinates list
        geo_location = GeoLocation(type="linestring")
        # Use list comprehension to create all points at once
        geo_location.coordinates = [
            Point(point=[pt.x, pt.y, pt.z])
            for pt in self.smooth_trajectory
        ]
        return geo_location

    def _calculate_distance(self, p1: Coordinate, p2: Coordinate) -> float:
        """
        Calculate distance between two points.

        :param Coordinate p1: The first point
        :param Coordinate p2: The second point
        :return float: Distance between the two points
        """
        return euclidean_distance(p1, p2)

    def __str__(self) -> str:
        """
        Get a string representation of the trajectory.

        :return str: String representation of the trajectory
        """
        return (
            f"Moving angle {self.bearing}, dir {self.direction:>3}, speed at {self.speed:4.2f}, "
            f"covered {self.distance:4.2f} in {self.time_interval:4.2f} seconds, id = {self.id}"
        )
