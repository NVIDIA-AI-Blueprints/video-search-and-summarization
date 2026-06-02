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

from datetime import datetime

from mdx.analytics.core.schema.models import Behavior, Place, Location


class CollisionState:
    """
    A class for tracking collision events between objects.

    This class maintains state information about potential collisions between objects, including:
    1. Object IDs and sensor information
    2. Trajectory and behavior data
    3. Timing and location metadata
    4. Analytics module triggers

    :ivar set[str] object_ids: List of object IDs involved in the collision
    :ivar str sensor_id: ID of the sensor that detected the collision
    :ivar Place place: The place where the collision occurred
    :ivar Location location: Location object representing the location of the collision
    :ivar datetime start_time: Start time of the collision event
    :ivar list[str] trigger_modules: List of modules that triggered the collision detection
    :ivar int timeout: Timeout duration in seconds (default: 10)
    :ivar dict[str, int] proximity_moments: Dictionary mapping object IDs to their collision moments
    :ivar Set frame_ids: Set of (frame_id, timestamp) tuples for frames in the collision
    :ivar dict[str, List] trajectories: Dictionary mapping object IDs to their trajectory lists
    :ivar dict[str, Behavior] behaviors: Dictionary mapping object IDs to their behaviors
    :ivar str primary_object_id: ID of the primary object in the collision

    Examples::

        >>> collision_state = CollisionState(
        ...     object_ids=["51", "52"],
        ...     sensor_id="sensor1",
        ...     place=place_obj,
        ...     location=location_obj,
        ...     start_time=datetime.now(),
        ...     trigger_modules=["Abnormal Movement"],
        ...     timeout=10
        ... )
        >>> # Creates collision state tracking objects 51 and 52
    """
    __slots__ = ['sensor_id', 'object_ids', 'start_time', 'frame_ids', 'trajectories', 'behaviors', 
                'place', 'location', 'trigger_modules', 'primary_object_id', 'timeout', 'proximity_moments']

    def __init__(
        self,
        object_ids: list[str],
        sensor_id: str,
        place: Place,
        location: Location,
        start_time: datetime,
        trigger_modules: list[str],
        timeout: int = 10,
    ) -> None:
        """
        Initialize the CollisionState object.

        :param list[str] object_ids: List of object IDs involved in the collision
        :param str sensor_id: ID of the sensor that detected the collision
        :param Place place: The place where the collision occurred
        :param Location location: Location object representing the location of the collision
        :param datetime start_time: Start time of the collision event
        :param list[str] trigger_modules: List of modules that triggered the collision detection
        :param int timeout: Timeout duration in seconds (default: 10)
        """
        self.object_ids = set(object_ids)
        self.start_time = start_time
        self.frame_ids = set()
        self.trajectories = {}
        self.behaviors = {}
        self.proximity_moments = {}
        self.place = place
        self.location = location
        self.trigger_modules = trigger_modules
        for object_id in object_ids:
            self.trajectories[object_id] = []

        self.primary_object_id = object_ids[0]
        self.sensor_id = sensor_id
        self.timeout = timeout

    def update_behavior(self, object_id: str, behavior: Behavior) -> None:
        """
        Update behavior for a specific object in the collision state.

        :param str object_id: ID of the object to update behavior for
        :param Behavior behavior: New behavior to assign to the object
        :return: None

        Examples::

            collision_state.update_behavior("51", behavior)
        """

        self.behaviors[object_id] = behavior

    def get_behavior(self, object_id: str) -> Behavior | None:
        """
        Get behavior for a specific object in the collision state.

        :param str object_id: ID of the object to get behavior for
        :return Behavior | None: Behavior object if found, None otherwise

        Examples::

            behavior = collision_state.get_behavior("51")
        """

        if object_id in self.behaviors.keys():
            return self.behaviors[object_id]
        else:
            return None

    def update_trajectory_by_frame(self, object_id: str, frame: dict) -> None:
        """
        Update trajectory for a specific object based on frame data.

        :param str object_id: ID of the object to update trajectory for
        :param dict frame: Frame data containing ObjectData instances and metadata
        :return: None

        Examples::
            from mdx.analytics.core.transform.detection.collision_detection import ObjectData
            frame_data = {
                'num_references': 20,
                'timestamp': 1746593571096,
                'id': '1864',
                '64': ObjectData(
                    x=42.49199228614487,
                    y=-90.6647537620458,
                    bbox=None,
                    object_type='Vehicle',
                    is_geo_coordinate=True,
                    bearing=0.0
                )
            }
            collision_state.update_trajectory_by_frame("51", frame_data)
        """
        trajectory = {"x": frame[object_id].x, "y": frame[object_id].y, "timestamp": frame["timestamp"]}
        if object_id not in self.trajectories.keys():
            self.trajectories[object_id] = []
        self.trajectories[object_id].append(trajectory)

    def update_trajectory_by_trajectory(self, object_id: str, trajectory: list[dict]) -> None:
        """
        Update trajectory for a specific object with a new trajectory list.

        :param str object_id: ID of the object to update trajectory for
        :param list[Dict] trajectory: List of trajectory points, where each point is a dict
            containing x, y coordinates and timestamp
        :return: None

        Examples::

            trajectory_points = [
                {"x": 40.71288, "y": -74.00609, "timestamp": 1746600209900},
                {"x": 40.71289, "y": -74.00610, "timestamp": 1746600210000}
            ]
            collision_state.update_trajectory_by_trajectory("obj1", trajectory_points)
        """
        self.trajectories[object_id] = trajectory

    def get_trajectory(self, object_id: str) -> list[dict]:
        """
        Get trajectory for a specific object.

        :param str object_id: ID of the object to get trajectory for
        :return list[Dict]: List of trajectory points, where each point is a dict containing x, y coordinates and timestamp

        Examples::

            # Get trajectory for object with ID "51"
            trajectory = collision_state.get_trajectory("51")
            # Returns: [
            #   {"x": 40.71288, "y": -74.00609, "timestamp": 1746600209900},
            #   {"x": 40.71289, "y": -74.00610, "timestamp": 1746600210000}
            # ]
        """
        trajectory = self.trajectories[object_id]
        return trajectory

    def get_place(self) -> Place:
        """
        Get the place information 

        :return Place: The place where the collision occurred
        """
        return self.place
    
    def get_location(self) -> Location:
        """
        Get the location of the collision.
        Location will be more accurate than Place.

        :return Location: Location object representing the location of the collision
        """
        return self.location
    
    def update_location(self, location: Location) -> None:
        """
        Update the location of the collision.
    
        :param Location location: Location object representing the location of the collision
        :return: None
        """
        self.location = location

    def get_trajectories(self) -> dict[str, list[dict]]:
        """
        Get all trajectories for objects in this collision state.

        :return dict[str, list[Dict]]: Dictionary mapping object IDs to their trajectory lists
        """
        return self.trajectories

    def add_object_id(self, object_id: str, collision_timestamp: int | None = None) -> None:
        """
        Add a new object ID to the collision state.

        :param str object_id: ID of the object to add
        :param int collision_timestamp: Optional timestamp when the collision condition was met
        :return: None

        Examples::

            # Add new object with ID "51" to collision state
            collision_state.add_object_id("51", 1746600209900)
            # Object ID "51" is added to object_ids set and empty trajectory list is created
            # Collision moment is set to 1746600209900
        """
        self.object_ids.add(object_id)
        if object_id not in self.trajectories:
            self.trajectories[object_id] = []
        if collision_timestamp is not None:
            if object_id not in self.proximity_moments:
                self.proximity_moments[object_id] = collision_timestamp
            if self.primary_object_id not in self.proximity_moments:
                self.proximity_moments[self.primary_object_id] = collision_timestamp

    def get_proximity_moment(self, object_id: str) -> int | None:
        """
        Get the collision moment for a specific object.

        :param str object_id: ID of the object
        :return int | None: Timestamp of the collision moment or None if not set
        """
        return self.proximity_moments.get(object_id)

    def add_frame_id(self, frame_id: str, timestamp: int) -> None:
        """
        Add a frame ID and its timestamp to the collision state.

        :param str frame_id: ID of the frame to add
        :param int timestamp: Timestamp of the frame
        :return: None

        Examples::

            # Add frame with ID "1429" and timestamp 1746600209900 to collision state
            collision_state.add_frame_id("1429", 1746600209900)
            # Frame ID "1429" and timestamp 1746600209900 are added to frame_ids set
        """
        self.frame_ids.add((frame_id, timestamp))

    def get_frame_ids(self) -> list[str]:
        """
        Get all frame IDs that have been added to this collision state, sorted by timestamp.

        :return list[str]: List of frame IDs sorted by timestamp

        Examples::

            # Get sorted frame IDs from collision state
            frame_ids = collision_state.get_frame_ids()
            # Returns: ["1429", "1430", "1431"]
        """
        sorted_frame_ids = sorted(list(self.frame_ids), key=lambda x: x[1])
        return [frame_id for frame_id, _ in sorted_frame_ids]

    def reset_time(self) -> None:
        """
        Reset the start time of this collision state to the current time.

        :return: None
        """
        self.start_time = datetime.now()

    def is_timeout(self) -> bool:
        """
        Check if the collision state has timed out based on the configured timeout duration.

        :return bool: True if the time elapsed since start_time exceeds the timeout duration, False otherwise

        Examples::

            # Check if collision state has timed out after 5 seconds
            collision_state = CollisionState(["51"], "sensor1", place, datetime.now(), [], timeout=5)
            time.sleep(6)
            is_timeout = collision_state.is_timeout()
            # Returns: True since more than 5 seconds have elapsed
        """
        duration = datetime.now() - self.start_time
        return duration.total_seconds() > self.timeout

    def get_object_ids(self) -> set[str]:
        """
        Get the set of object IDs involved in this collision state.

        :return set[str]: Set of object IDs
        """
        return self.object_ids
