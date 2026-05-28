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

from datetime import timedelta
from enum import Enum
from typing import Generic, Protocol, TypeVar

from mdx.analytics.core.schema.config import AppConfig
from mdx.analytics.core.schema.models import AnalyticsModule, Behavior, Event, Point2D
from mdx.analytics.core.transform.calibration.calibration_base import CalibrationBase
from mdx.analytics.core.utils.schema_util import point_list_to_geo_location


class HasId(Protocol):
    id: str


T = TypeVar("T", bound=HasId)


class BaseEvent(Generic[T]):
    """
    Base class for event detection that handles common functionality between different types of events.

    :ivar AppConfig config: Configuration object for the application.
    :ivar CalibrationBase calibration: Calibration for different coordinate systems.
    :ivar Type[Enum] direction_enum: Enum class for direction types.
    :ivar str event_name: Name identifier for the event.
    :ivar str event_type: Type identifier for the event.
    """

    def __init__(
        self,
        config: AppConfig,
        calibration: CalibrationBase,
        direction_enum: type[Enum],
        event_name: str,
        event_type: str,
    ) -> None:
        self.config = config
        self.calibration = calibration
        self.direction_enum = direction_enum
        self.event_name = event_name
        self.event_type = event_type

    def _check_point(self, point: Point2D, sensor_id: str, obj_id: str) -> bool:
        """
        Abstract method to check if a point is within the given obj (tripwire or roi).
        Must be implemented by subclasses.
        """
        raise NotImplementedError("_check_point is not implemented")

    def _get_objects(self, sensor_id: str) -> list[T]:
        """
        Abstract method to get the list of objects for a sensor.
        Must be implemented by subclasses.
        """
        raise NotImplementedError("_get_objects is not implemented")

    def _intersect(self, trip: list[Point2D], sensor_id: str, obj_id: str) -> bool:
        """
        Abstract method to check if a trip intersects with an object.
        Must be implemented by subclasses.
        """
        raise NotImplementedError("_intersect is not implemented")

    def get_events(self, behavior: Behavior | None) -> list[Behavior]:
        """
        Process a behavior to detect and generate events based on object interactions.

        This method analyzes the behavior's trajectory to detect interactions with objects
        (tripwires or ROIs) and generates corresponding events. It checks for intersections
        and direction of movement to determine event types.

        :param Behavior | None behavior: The behavior object containing trajectory and sensor information
        :return list[Behavior]: List of detected events as Behavior objects

        Examples::
            >>> base_event = BaseEvent(config, calibration, direction_enum, "crossing", "tripwire")
            >>> behavior = Behavior(...)
            >>> events = base_event.get_events(behavior)
            >>> for event in events:
            ...     print(f"Event type: {event.event.type}, Timestamp: {event.timestamp}")
        """
        if not behavior:
            return []

        sensor_id = behavior.sensor.id

        # Check if there is ROIs or tripwires for the sensor
        if sensor_id not in self.calibration.sensor_map:
            return []

        # Get ROI or tripwire objects for the sensor
        objects = self._get_objects(sensor_id)

        # If there are no objects or the behavior is too short, return an empty list
        if not objects or behavior.length < 2:
            return []

        events = []

        # Get the minimum trip length to consider for the event
        min_trip_length = self.config.sensor_tripwire_min_points(sensor_id) * 2
        # Calculate time interval per point from behavior data
        interval = behavior.timeInterval / (behavior.length - 1)

        for obj in objects:
            locations = [Point2D(x=coord.point[0], y=coord.point[1]) for coord in behavior.locations.coordinates]
            # Check if each point is inside the object (ROI or tripwire)
            positions = [self._check_point(loc, sensor_id, obj.id) for loc in locations]
            # Split the trajectory, and position checks into smaller segments of minimum required length
            tracklets = [locations[i : i + min_trip_length] for i in range(len(locations) - min_trip_length + 1)]
            tracklets_positions = [
                positions[i : i + min_trip_length] for i in range(len(positions) - min_trip_length + 1)
            ]
            for idx, tracklet_positions in enumerate(tracklets_positions):
                trip = tracklets[idx]
                side1, side2 = sum(1 for p in tracklet_positions if p), sum(1 for p in tracklet_positions if not p)

                # Check if the trip intersects with the object and evenly distributed on each side
                if self._intersect(trip, sensor_id, obj.id) and side1 == side2:
                    # Determine the direction of the trip
                    dir = self.direction_enum.OUT if tracklet_positions[0] else self.direction_enum.IN
                    timestamp = behavior.timestamp + timedelta(seconds=idx * interval)
                    end_time = behavior.timestamp + timedelta(seconds=(idx + min_trip_length - 1) * interval)
                    e = Event(id=obj.id, type=dir.value, info={"class": self.event_name})
                    am = AnalyticsModule(id=self.event_type, description=f"index = {idx}")

                    # Generate the new event as a behavior object
                    events.append(
                        Behavior(
                            id=behavior.id,
                            speed=behavior.speed,
                            speedOverTime=behavior.speedOverTime,
                            timeInterval=(end_time - timestamp).total_seconds(),
                            bearing=behavior.bearing,
                            direction=behavior.direction,
                            object=behavior.object,
                            timestamp=timestamp,
                            end=end_time,
                            length=min_trip_length,
                            distance=behavior.distance * min_trip_length / behavior.length,
                            event=e,
                            locations=point_list_to_geo_location(trip),
                            analyticsModule=am,
                            sensor=behavior.sensor,
                            place=behavior.place,
                        )
                    )
        return events
