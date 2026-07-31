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

import math
from functools import cached_property

from pydantic import computed_field

from mdx.analytics.core.schema.trajectory.trajectory_base import TrajectoryBase
from mdx.analytics.core.utils.distance_util import MPS_TO_MPH
from mdx.analytics.core.transform.calibration.calibration_base import CalibrationType


class TrajectoryE(TrajectoryBase):
    """
    Euclidean trajectory representation, in cartesian or image coordinates.

    Every override is gated on ``calibration_type``, because the coordinates mean different things:
    cartesian points are metric, so bearing uses y-increasing-upward convention and speeds convert to
    mph; image points are pixels, so bearing and speeds fall through to the base class and stay in
    pixels per second. Geographic coordinates are not supported -- use ``Trajectory`` for those.

    :ivar str id: Unique identifier for the trajectory
    :ivar datetime start: Start time of the trajectory
    :ivar datetime end: End time of the trajectory
    :ivar list[Coordinate] points: List of coordinate points forming the trajectory
    :ivar int smooth_min_points: Minimum number of points required for computing smoothed trajectory and speed over time (default: 20)
    :ivar int smooth_window_size: Window size for smoothing (default: 5)
    :ivar int distance_stride: Stride for distance calculation (default: 5)
    :ivar int speed_segment_size: Size of speed segment for speed over time calculation (default: 10)

    Examples::

        # Create a trajectory with speed in mph
        from datetime import datetime
        from mdx.analytics.core.schema.models import Coordinate

        # Create a trajectory with points moving at different speeds
        points = [
            Coordinate(x=0, y=0, z=0),  # Start point
            Coordinate(x=10, y=10, z=0),  # Fast movement
            Coordinate(x=11, y=11, z=0),  # Slow movement
            Coordinate(x=20, y=20, z=0)   # Fast movement
        ]
        trajectory = TrajectoryE(
            id="traj_e1",
            start=datetime.now(),
            end=datetime.now(),
            points=points
        )

        # Access mph-specific properties
        print(f"Average speed: {trajectory.speed} mph")
        print(f"Speed over time: {trajectory.speed_over_time} mph")

        # Compare with base class (speed in m/s)
        print(f"Base speed: {base_trajectory.speed} m/s")
        print(f"Euclidean speed: {trajectory.speed} mph")
    """
    calibration_type: CalibrationType = CalibrationType.CARTESIAN

    @computed_field
    @cached_property
    def bearing(self) -> float:
        """
        Calculate the bearing of the trajectory from the first to the last point.

        Uses standard Cartesian coordinates (y increasing upward).

        :return float: Bearing in degrees (0-360)
        """
        if self.calibration_type == CalibrationType.CARTESIAN:
            brng = math.atan2(self.last.y - self.head.y, self.last.x - self.head.x) * 180 / math.pi
            return (brng + 360) % 360
        if self.calibration_type == CalibrationType.GEO:
            raise ValueError("TrajectoryE does not support geographic coordinates")
        return super().bearing

    @computed_field
    @cached_property
    def speed(self) -> float:
        """
        Calculate the average speed of the trajectory.

        Cartesian coordinates are metric, so the base speed is converted to mph. Image coordinates
        are pixels, so the rate is returned unconverted as pixels per second.

        :return float: Average speed, in mph for cartesian coordinates or pixels/second for image
        :raises ValueError: If the trajectory is in geographic coordinates

        Example::

            from datetime import datetime, timedelta

            # Create a trajectory that moves 10 meters in 2 seconds
            points = [
                Coordinate(x=0, y=0, z=0),
                Coordinate(x=6, y=8, z=0)  # 10 meters from origin
            ]
            start = datetime.now()
            end = start + timedelta(seconds=2)

            trajectory = TrajectoryE(
                id="1",
                start=start,
                end=end,
                points=points
            )

            # Speed will be converted from m/s to mph
            avg_speed = trajectory.speed  # Returns approximately 11.18 mph
        """
        if self.calibration_type == CalibrationType.CARTESIAN:
            return super().speed * MPS_TO_MPH
        if self.calibration_type == CalibrationType.GEO:
            raise ValueError("TrajectoryE does not support geographic coordinates")
        # Image coordinates are pixels, so the rate is pixels per second and there is nothing
        # metric to convert -- scaling it by a m/s factor would produce a meaningless number.
        return super().speed

    @computed_field
    @cached_property
    def speed_over_time(self) -> list[float]:
        """
        Calculate the speed of the trajectory over time in segments.

        Uses the same units as :meth:`speed`: mph for cartesian coordinates, pixels per second for
        image coordinates.

        :return list[float]: Speed for each segment
        :raises ValueError: If the trajectory is in geographic coordinates

        Example::

            from datetime import datetime, timedelta

            # Create a trajectory with varying speeds
            points = [
                Coordinate(x=0, y=0, z=0),
                Coordinate(x=3, y=4, z=0),  # 5 meters
                Coordinate(x=6, y=8, z=0)   # 5 more meters
            ]
            start = datetime.now()
            end = start + timedelta(seconds=2)

            trajectory = TrajectoryE(
                id="1",
                start=start,
                end=end,
                points=points,
                speed_segment_size=2
            )

            # Speeds will be in mph
            speeds = trajectory.speed_over_time
            # Returns list of speeds in mph for each segment
        """
        if self.calibration_type == CalibrationType.CARTESIAN:
            return [MPS_TO_MPH * x for x in super().speed_over_time]
        if self.calibration_type == CalibrationType.GEO:
            raise ValueError("TrajectoryE does not support geographic coordinates")
        # Pixels per second in image coordinates; see :meth:`speed`.
        return super().speed_over_time

    def __str__(self) -> str:
        """
        Get a string representation of the trajectory.

        :return str: String representation of the trajectory
        """
        return (
            f"Moving angle {self.bearing}, dir {self.direction:>3}, speed at {self.speed:4.2f} mph, "
            f"covered {self.distance:4.2f} meters in {self.time_interval:4.2f} seconds, id = {self.id}"
        )
