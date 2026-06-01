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


from mdx.analytics.core.constants import TripwireDirection
from mdx.analytics.core.schema.config import AppConfig
from mdx.analytics.core.schema.models import Line, Point2D, Tripwire
from mdx.analytics.core.transform.calibration.calibration_base import CalibrationBase
from mdx.analytics.core.transform.event.base_event import BaseEvent
from mdx.analytics.core.utils.distance_util import intersect


class TripwireEvent(BaseEvent[Tripwire]):
    """
    Event detection class for Tripwire interactions.

    This class extends BaseEvent to detect when objects cross tripwires.
    It uses line-based detection to determine if trajectories intersect with
    tripwire lines and tracks the direction of crossing.

    :ivar AppConfig config: Configuration object for the application.
    :ivar CalibrationBase calibration: Calibration object containing tripwire definitions.

    Examples::
        >>> config = AppConfig()
        >>> calibration = CalibrationBase(config, "calibration.json")
        >>> tripwire_detector = TripwireEvent(config, calibration)
        >>> behavior = Behavior(
        ...     id="obj1",
        ...     locations=GeoLocation(coordinates=[
        ...         Coordinate(point=[1.0, 1.0]),
        ...         Coordinate(point=[2.0, 2.0])
        ...     ]),
        ...     sensor=SensorInfo(id="sensor1")
        ... )
        >>> events = tripwire_detector.get_events(behavior)
        >>> for event in events:
        ...     print(f"Tripwire event: {event.event.type} at {event.timestamp}")
    """

    def __init__(self, config: AppConfig, calibration: CalibrationBase) -> None:
        """
        Initialize the TripwireEvent detector.

        :param AppConfig config: Configuration object for the application
        :param CalibrationBase calibration: Calibration object containing tripwire definitions

        Examples::
            >>> config = AppConfig()
            >>> calibration = CalibrationBase(config, "calibration.json")
            >>> tripwire_detector = TripwireEvent(config, calibration)
        """
        super().__init__(config, calibration, TripwireDirection, "tripwire", "TripEvent")

    def _check_point(self, point: Point2D, sensor_id: str, obj_id: str) -> bool:
        """
        Check if a point is on the in-direction side of a tripwire.

        :param Point2D point: The point to check
        :param str sensor_id: ID of the sensor associated with the tripwire
        :param str obj_id: ID of the tripwire to check against
        :return bool: True if the point is on the tripwire, False otherwise

        Examples::
            >>> point = Point2D(x=1.0, y=1.0)
            >>> is_on_tripwire = tripwire_detector._check_point(point, "sensor1", "tripwire1")
            >>> print(f"Point is {'on' if is_on_tripwire else 'not on'} tripwire")
        """
        return self.calibration.point_in_tripwire(point, sensor_id, obj_id)

    def _get_objects(self, sensor_id: str) -> list[Tripwire]:
        """
        Get all tripwires associated with a sensor.

        :param str sensor_id: ID of the sensor to get tripwires for
        :return list[Tripwire]: List of tripwires associated with the sensor

        Examples::
            >>> tripwires = tripwire_detector._get_objects("sensor1")
            >>> print(f"Found {len(tripwires)} tripwires for sensor1")
            >>> for tripwire in tripwires:
            ...     print(f"Tripwire ID: {tripwire.id}")
        """
        return list(self.calibration.sensor_map[sensor_id].tripwires.values())

    def _intersect(self, trip: list[Point2D], sensor_id: str, obj_id: str) -> bool:
        """
        Check if a trajectory intersects with a tripwire by checking if the line
        formed by the start and end points of the trajectory intersects with the tripwire.

        :param list[Point2D] trip: The trajectory to check
        :param str sensor_id: ID of the sensor associated with the tripwire
        :param str obj_id: ID of the tripwire to check against
        :return bool: True if the trajectory intersects with the tripwire, False otherwise

        Examples::
            >>> trip = [Point2D(x=1.0, y=1.0), Point2D(x=2.0, y=2.0)]
            >>> intersects = tripwire_detector._intersect(trip, "sensor1", "tripwire1")
            >>> print(f"Trajectory {'intersects' if intersects else 'does not intersect'} tripwire")
        """
        wires = self.calibration.sensor_map[sensor_id].tripwires[obj_id].wires
        trip_line = Line(p1=trip[0], p2=trip[-1])
        return intersect(wires, trip_line)
