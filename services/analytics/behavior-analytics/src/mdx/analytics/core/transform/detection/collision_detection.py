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

import copy
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

import numpy as np
import math

from mdx.analytics.core.schema.config import CollisionDetectionConfig
from mdx.analytics.core.transform.calibration.calibration_dynamic import CalibrationType
from mdx.analytics.core.schema.models import AnalyticsModule, Bbox, Behavior, Incident, Object, Message, Location
from mdx.analytics.core.schema.collision.collision_state import CollisionState
from mdx.analytics.core.utils.distance_util import calculate_haversine_distance_vectorized
from mdx.analytics.core.utils.util import iso_to_epoch

from collections import namedtuple

TIMEOUT_THRESHOLD = 5 * 60  # 5 minutes
FRAME_TIMEOUT_THRESHOLD = 30 * 60  # 30 minutes
GEO_COORDINATE_INITIAL_LOCATION = Location(lat=0, lon=0, alt=0)
METADATA_KEYS = {"timestamp", "num_references", "id"}

ObjectData = namedtuple('ObjectData', ['x', 'y', 'bbox', 'object_type', 'is_geo_coordinate', 'bearing'])

class CollisionDetection:
    """
    A class for detecting potential collisions between objects in video streams.

    This class implements collision detection logic by tracking object positions and trajectories
    across video frames. It can work with both pixel coordinates and geographic coordinates to
    detect when objects get too close to each other.

    :ivar dict[str, dict[str, CollisionState]] potential_collision_map: Map tracking potential collisions between objects
    :ivar defaultdict sensor_to_frames_map: Map of sensor IDs to frame data
    :ivar defaultdict object_id_to_frames_map: Map of object IDs to frame references
    :ivar dict timeout_counter_map: Map tracking timeout counters for objects
    :ivar dict frame_ids: Map of sensor IDs to frame counters
    :ivar dict alert_list: Map tracking generated collision alerts
    :ivar CollisionDetectionConfig config: Configuration parameters for collision detection
    :ivar CalibrationType calibration_type: Calibration type for collision detection

    Examples::
        >>> config = CollisionDetectionConfig(
        ...     enable=True,
        ...     distancePixelsThreshold=100,
        ...     distanceMetersThreshold=2.0,
        ...     alertTimeWindow=30
        ... )
        >>> calibration_type = CalibrationType.GEO
        >>> detector = CollisionDetection(config, coordinate_system)
        >>> incidents = []
        >>> potential_collisions, anomalies = detector.detect_batch(behaviors, crs)
        >>> frames = group_messages_by_frame_id(updated_messages)
        >>> detector.update_frames(frames)
        >>> for object_id, sensor_id, behavior, trigger_modules in potential_collisions:
        ...     detector.update_potential_collision(object_id, sensor_id, behavior, trigger_modules)
        >>> collision_incidents = self.collision_detection.get_collision_alerts()
        >>> if len(collision_incidents) > 0:
        ...     for incident, collision_state in collision_incidents:
        ...         incidents.append(incident)
    """

    def __init__(self, config: CollisionDetectionConfig, calibration_type: CalibrationType) -> None:
        self.potential_collision_map = {}
        self.sensor_to_frames_map = defaultdict(dict)
        self.object_id_to_frames_map = defaultdict(dict)
        self.timeout_counter_map = {}
        self.frame_ids = {}
        self.alert_list = {}
        self.object_id_to_behavior_map = {}
        self.config = config
        self.calibration_type = calibration_type


    def check_is_in_potential_collision(self, object_id: str, sensor_id: str) -> tuple[bool, str | None]:
        """
        Check if an object is currently in a potential collision state for a given sensor.

        This method determines whether the specified object is being tracked as part of a potential collision
        for the provided sensor. It checks the internal potential_collision_map to see if the object is either
        a primary tracked object or part of another object's collision state.

        :param str object_id: The ID of the object to check for potential collision
        :param str sensor_id: The ID of the sensor to check within
        :return tuple[bool, str | None]: Tuple containing a boolean indicating if the object is in a potential collision, and the primary object ID if found

        Examples::
            >>> collision_detection = CollisionDetection(config)
            >>> is_in_collision, primary_id = collision_detection.check_is_in_potential_collision("obj_1", "sensor_1")
            >>> if is_in_collision:
            ...     print(f"Object is in potential collision, tracked under: {primary_id}")
        """

        if sensor_id not in self.potential_collision_map.keys():
            return False, None
        if object_id not in self.potential_collision_map[sensor_id].keys():
            for object_id_i in self.potential_collision_map[sensor_id].keys():
                collision_state = self.potential_collision_map[sensor_id][object_id_i]
                if object_id in collision_state.object_ids:
                    return True, object_id_i
            return False, None
        else:
            return True, object_id


    def initialize_potential_collision(
        self, potential_object_id: str, sensor_id: str, behavior: Behavior, trigger_modules: list[str]
    ) -> bool:
        """
        Initialize a new collision state for a potential collision object.

        This method creates a new collision state entry for an object that has been
        identified as having potential for collision. It:
        1. Checks if sensor already exists in collision map
        2. Verifies object is not already being tracked for collisions
        3. Creates new CollisionState with start time and trigger modules

        :param str potential_object_id: ID of the object to initialize collision tracking for
        :param str sensor_id: ID of the sensor that detected the object
        :param Behavior behavior: Behavior object containing place information
        :param list[str] trigger_modules: List of modules that triggered this collision detection
        :return bool: True if new collision state was initialized, False if object already being tracked

        Examples::
            >>> collision_detection = CollisionDetection(config)
            >>> behavior = Behavior()
            >>> initialized = collision_detection.initialize_potential_collision(
            ...     "obj_1", "sensor_1", behavior, ["Abnormal Movement"]
            ... )
            >>> if initialized:
            ...     print("New collision state initialized")
        """
        if sensor_id not in self.potential_collision_map.keys():
            self.potential_collision_map[sensor_id] = {}
        is_in_potential_collision, _ = self.check_is_in_potential_collision(potential_object_id, sensor_id)
        if is_in_potential_collision == False:
            data = datetime.now()
            start_time = data
            location = GEO_COORDINATE_INITIAL_LOCATION
            self.potential_collision_map[sensor_id][potential_object_id] = CollisionState(
                object_ids=[potential_object_id],
                sensor_id=sensor_id,
                start_time=start_time,
                place=behavior.place,
                location=location,
                trigger_modules=trigger_modules,
                timeout=self.config.alertTimeWindow,
            )
            return True
        else:
            return False

    def update_from_past_frames(self, sensor_id: str, potential_object_id: str, object_id: str) -> None:
        """
        Update collision state with historical frame data for a given object.

        This method examines past frames stored in object_id_to_frames_map and
        sensor_to_frames_map to update the collision state trajectory for a specific
        object. For each historical frame containing the object, it:
        1. Retrieves the frame data from sensor_to_frames_map
        2. Updates the collision state trajectory with the object's position
        3. Adds the frame ID and timestamp to track the collision state history

        :param str sensor_id: ID of the sensor that detected the objects
        :param str potential_object_id: ID of the object being tracked for collisions
        :param str object_id: ID of the object whose historical data is being added
        :return: None

        Examples::
            >>> collision_detection = CollisionDetection(config)
            >>> collision_detection.update_from_past_frames(
            ...     "sensor_1", "obj_1", "obj_2"
            ... )
            >>> # Historical frame data is added to collision state for obj_2, obj_1 is the primary object
        """
        if sensor_id in self.object_id_to_frames_map.keys():
            if object_id in self.object_id_to_frames_map[sensor_id].keys():
                for frame_id in self.object_id_to_frames_map[sensor_id][object_id]:
                    if frame_id not in self.sensor_to_frames_map[sensor_id]:
                        continue
                    frame = self.sensor_to_frames_map[sensor_id][frame_id]
                    self.potential_collision_map[sensor_id][potential_object_id].update_trajectory_by_frame(
                        object_id, frame
                    )
                    self.potential_collision_map[sensor_id][potential_object_id].add_frame_id(
                        frame["id"], frame["timestamp"]
                    )

    def get_pairs(
        self, frame: dict[str, Any], potential_object_id: str
    ) -> tuple[list[tuple[ObjectData, ObjectData]], list[tuple[str, str]], bool]:
        """
        Extract object pairs from a frame for collision detection analysis.

        This method takes a frame and a potential object ID, and returns pairs of objects
        to check for potential collisions. It:
        1. Filters out metadata fields from frame keys to get object IDs
        2. Creates pairs between the potential object and all other objects
        3. Determines if frame uses geographic coordinates

        :param dict[str, Any] frame: Frame data containing object positions and metadata
        :param str potential_object_id: ID of the primary object to check for collisions
        :return tuple[list[tuple[ObjectData, ObjectData]], list[tuple[str, str]], bool]: Tuple containing:
                - List of object data pairs to check distances between
                - List of object ID pairs corresponding to the data pairs
                - Boolean indicating if coordinates are geographic

        Examples::
            >>> collision_detection = CollisionDetection(config)
            >>> frame_data = {...}  # Frame containing multiple objects
            >>> pairs, pair_ids, is_geo = collision_detection.get_pairs(
            ...     frame_data, "obj_1"
            ... )
            >>> # Returns pairs of objects to check for collisions with obj_1
        """
        object_ids = [k for k in frame.keys() if not k.startswith("_") and k not in METADATA_KEYS]
        pairs = []
        pair_ids = []
        is_geo_coordinate_frame = frame[potential_object_id].is_geo_coordinate
        for obj_id in object_ids:
            if obj_id == potential_object_id:
                continue

            pairs.append((frame[potential_object_id], frame[obj_id]))
            pair_ids.append((potential_object_id, obj_id))
        return pairs, pair_ids, is_geo_coordinate_frame

    def update_collision_location(self, 
        sensor_id: str, 
        frame: dict[str, Any], 
        is_geo_coordinate_frame: bool, 
        potential_object_id: str
    ) -> None:
        """
        Update the location of the collision.
        Location of the collision is the first location that potential object is collided with other object.

        :param str sensor_id: ID of the sensor that detected the object
        :param dict[str, Any] frame: Frame data containing object positions and metadata
        :param bool is_geo_coordinate_frame: Whether the frame uses geographic coordinates
        :param str potential_object_id: ID of the object to update the location for
        :return: None
        """
        if is_geo_coordinate_frame == True:
            lat = frame[potential_object_id].x
            lon = frame[potential_object_id].y
            location = Location(lat=lat, lon=lon, alt=0.0)
        else:
            location = GEO_COORDINATE_INITIAL_LOCATION

        if sensor_id in self.potential_collision_map.keys():
            if potential_object_id in self.potential_collision_map[sensor_id].keys():
                old_location = self.potential_collision_map[sensor_id][potential_object_id].get_location()
                # First time update the location of the collision
                INIT_LOC = GEO_COORDINATE_INITIAL_LOCATION
                if old_location.lat == INIT_LOC.lat and old_location.lon == INIT_LOC.lon and old_location.alt == INIT_LOC.alt:
                    self.potential_collision_map[sensor_id][potential_object_id].update_location(location)


    def process_potential_collision_on_past_frames(self, potential_object_id: str, sensor_id: str) -> None:
        """
        Process potential collisions by analyzing past frames for a given object and sensor.

        This method examines historical frame data in ascending order of timestamp to detect potential collisions between
        the specified object and other objects detected by the sensor. For each frame
        containing the potential_object_id, it:
        1. Gets pairs of objects to check for collisions
        2. Checks distances between object pairs
        3. Updates collision tracking state based on proximity
        4. Merges collision states if objects are already being tracked separately

        :param str potential_object_id: ID of the object to check for collisions
        :param str sensor_id: ID of the sensor that detected the object
        :return: None

        Examples::
            >>> collision_detection = CollisionDetection(config)
            >>> collision_detection.process_potential_collision_on_past_frames(
            ...     "obj_1", "sensor_1"
            ... )
            >>> # Historical frames are analyzed for potential collisions with obj_1
        """
        if potential_object_id in self.object_id_to_frames_map[sensor_id].keys():
            # Sort frame IDs by timestamp in descending order
            sorted_frame_ids = sorted(
                self.object_id_to_frames_map[sensor_id][potential_object_id],
                key=lambda frame_id: self.sensor_to_frames_map[sensor_id][frame_id]["timestamp"],
                reverse=True,
            )
            # Get up to maxNumberPastFrames frames
            sorted_frame_ids = sorted_frame_ids[: self.config.maxNumberPastFrames]

            # Reverse the list to process frames in ascending order of timestamp
            sorted_frame_ids.reverse()

            for frame_id in sorted_frame_ids:
                removed_object_id_list = set()
                frame = self.sensor_to_frames_map[sensor_id][frame_id]
                pairs, pair_ids, is_geo_coordinate_frame = self.get_pairs(frame, potential_object_id)
                closest_objects = self.check_distances(pairs, pair_ids, is_geo_coordinate_frame)
                for object_id_a, _ in closest_objects:
                    self.update_collision_location(sensor_id, frame, is_geo_coordinate_frame, potential_object_id)
                    is_in_potential_collision, object_id_b = self.check_is_in_potential_collision(
                        object_id_a, sensor_id
                    )
                    if is_in_potential_collision == False:
                        self.potential_collision_map[sensor_id][potential_object_id].add_object_id(object_id_a, frame["timestamp"])
                        self.update_from_past_frames(sensor_id, potential_object_id, object_id_a)
                    else:
                        if object_id_b is not None and object_id_b != potential_object_id:
                            removed_object_id = self.merge_potential_collisions(
                                potential_object_id, object_id_b, sensor_id
                            )
                            removed_object_id_list.add(removed_object_id)
                for removed_object_id in removed_object_id_list:
                    self.potential_collision_map[sensor_id].pop(removed_object_id)

    def update_potential_collision(
        self, potential_object_id: str, sensor_id: str, behavior: Behavior, trigger_modules: list[str]
    ) -> None:
        """
        Updates the potential collision tracking state for a given object and sensor.

        This method updates or initializes collision tracking for an object detected by a sensor.
        It checks if the object is already being tracked for collisions and either:
        1. Returns early if the object is already in the alert list
        2. Initializes new collision tracking if the object isn't being tracked
        3. Updates past frame data for collision analysis

        :param str potential_object_id: ID of the object to track for collisions
        :param str sensor_id: ID of the sensor that detected the object
        :param Behavior behavior: Behavior object containing object metadata and tracking info
        :param list[str] trigger_modules: List of analytics modules that triggered collision detection
        :return: None

        Examples::
            >>> collision_detection = CollisionDetection(config)
            >>> collision_detection.update_potential_collision(
            ...     "51",
            ...     "sensor_1",
            ...     behavior,
            ...     ["Abnormal Movement"]
            ... )
            >>> # Collision tracking is initialized/updated for 51
        """
        if self.config.enable == False:
            return
        if sensor_id in self.alert_list.keys():
            if potential_object_id in self.alert_list[sensor_id].keys():
                return
        if sensor_id not in self.potential_collision_map.keys():
            self.potential_collision_map[sensor_id] = {}
        if potential_object_id not in self.potential_collision_map[sensor_id].keys():
            is_init = self.initialize_potential_collision(potential_object_id, sensor_id, behavior, trigger_modules)
            if is_init == True:
                self.update_from_past_frames(sensor_id, potential_object_id, potential_object_id)
                if sensor_id in self.object_id_to_frames_map.keys():
                    self.process_potential_collision_on_past_frames(potential_object_id, sensor_id)

    def have_potential_collision(self) -> bool:
        """
        Checks if there are any potential collisions being tracked.

        This method checks if there are any objects currently being tracked for potential collisions
        by examining the potential_collision_map. Returns True if there are any objects being tracked,
        False otherwise.

        :return bool: True if there are potential collisions being tracked, False otherwise

        Examples::
            >>> collision_detection = CollisionDetection(config)
            >>> collision_detection.update_potential_collision(
            ...     "51",
            ...     "sensor_1",
            ...     behavior,
            ...     ["Abnormal Movement"]
            ... )
            >>> collision_detection.have_potential_collision()
            True  # Returns True since object 51 is being tracked
        """
        return len(self.potential_collision_map) > 0

    def update_frame(self, sensor_id: str, frame: list[Message], frame_id: str) -> tuple[set, dict]:
        """
        Updates the collision detection system with a new frame of data.

        This method processes a new frame of sensor data to track objects and their positions.
        It extracts object IDs and positions from the frame, updates internal tracking maps,
        and prepares the data for collision detection processing.

        :param str sensor_id: ID of the sensor that generated this frame
        :param list[Message] frame: List of Message objects containing sensor data and object positions/metadata
        :return tuple[Set, Dict]: Tuple containing set of current object IDs and processed frame data

        Examples::
            >>> collision_detection = CollisionDetection(config)
            >>> frames = group_messages_by_frame_id(updated_messages)
            >>> for sensor_id in frames.keys():
            ...     for frame_id, frame in frames[sensor_id]:
            ...         object_in_current_frame_ids, new_frame = collision_detection.update_frame(sensor_id, frame, frame_id)
            >>> # Returns set of object IDs and new frame data
        """
        id = frame_id
        # Update frame to self.object_id_to_frames_map and self.sensor_to_frames_map
        timestamp = iso_to_epoch(frame[0].timestamp)
        if sensor_id not in self.timeout_counter_map.keys():
            self.timeout_counter_map[sensor_id] = {}

        if sensor_id not in self.frame_ids.keys():
            self.frame_ids[sensor_id] = 0
        frame_id = self.frame_ids[sensor_id]
        new_frame = {"num_references": 0, "timestamp": timestamp, "id": id}
        object_in_current_frame_ids = set()
        for message in frame:
            obj = message.object
            if obj is None:
                continue
            if obj.type in self.config.targetClasses:
                obj_id, geo_coord, bbox, object_type, is_geo_coordinate = self.parse_object(obj)
                if sensor_id in self.object_id_to_behavior_map.keys() and \
                    obj_id in self.object_id_to_behavior_map[sensor_id].keys():
                    bearing = self.object_id_to_behavior_map[sensor_id][obj_id].bearing
                else:
                    bearing = -1
                
                obj_data = ObjectData(
                    x=geo_coord[0],
                    y=geo_coord[1],
                    bbox=bbox,
                    object_type=object_type,
                    is_geo_coordinate=is_geo_coordinate,
                    bearing=bearing
                )
                new_frame[obj_id] = obj_data
                self.timeout_counter_map[sensor_id][obj_id] = datetime.now()
                if sensor_id not in self.object_id_to_frames_map.keys():
                    self.object_id_to_frames_map[sensor_id] = {}
                if obj_id not in self.object_id_to_frames_map[sensor_id].keys():
                    self.object_id_to_frames_map[sensor_id][obj_id] = []
                self.object_id_to_frames_map[sensor_id][obj_id].append(frame_id)
                new_frame["num_references"] += 1
                object_in_current_frame_ids.add(obj_id)

        self.sensor_to_frames_map[sensor_id][frame_id] = new_frame
        self.frame_ids[sensor_id] = (self.frame_ids[sensor_id] + 1) % 1000000

        return object_in_current_frame_ids, new_frame

    def process_potential_collisions_on_new_frame(
        self, object_in_current_frame_ids: set, new_frame: dict, sensor_id: str
    ) -> None:
        """
        Process potential collisions for objects in a new frame.

        This method processes a new frame to detect and track potential collisions between objects.
        It merges collision states when objects get close to each other and updates trajectory
        information for objects in potential collision.

        :param Set object_in_current_frame_ids: Set of object IDs present in current frame
        :param Dict new_frame: Dictionary containing frame data including object positions and metadata
        :param str sensor_id: ID of the sensor that generated this frame
        :return: None

        Examples::
            >>> collision_detection = CollisionDetection(config)
            >>> object_ids, frame = collision_detection.update_frame(frame)
            >>> collision_detection.process_potential_collisions_on_new_frame(object_ids, frame, "sensor1")
            >>> # Updates internal collision tracking state
        """
        # Check distance between potential objects and other objects in the frame to merge potential collisions
        if sensor_id in self.potential_collision_map.keys():
            # Create a copy of the keys to iterate over
            potential_object_ids = list(self.potential_collision_map[sensor_id].keys())
            while len(potential_object_ids) > 0:
                potential_object_id = potential_object_ids.pop()
                removed_object_id_list = set()
                if potential_object_id in object_in_current_frame_ids:
                    self.potential_collision_map[sensor_id][potential_object_id].add_frame_id(
                        new_frame["id"], new_frame["timestamp"]
                    )

                    pairs, pair_ids, is_geo_coordinate_frame = self.get_pairs(new_frame, potential_object_id)
                    closest_objects = self.check_distances(pairs, pair_ids, is_geo_coordinate_frame)
                    for object_id_a, _ in closest_objects:
                        self.update_collision_location(sensor_id, new_frame, is_geo_coordinate_frame, potential_object_id)
                        is_in_potential_collision, object_id_b = self.check_is_in_potential_collision(
                            object_id_a, sensor_id
                        )
                        if is_in_potential_collision == False:
                            self.potential_collision_map[sensor_id][potential_object_id].add_object_id(object_id_a, new_frame["timestamp"])
                            self.update_from_past_frames(sensor_id, potential_object_id, object_id_a)
                        else:
                            if object_id_b is not None and object_id_b != potential_object_id:
                                removed_object_id = self.merge_potential_collisions(
                                    potential_object_id, object_id_b, sensor_id
                                )
                                removed_object_id_list.add(removed_object_id)

                for removed_object_id in removed_object_id_list:
                    self.potential_collision_map[sensor_id].pop(removed_object_id)
                    if removed_object_id in potential_object_ids:
                        potential_object_ids.remove(removed_object_id)

                for object_id in self.potential_collision_map[sensor_id][potential_object_id].get_object_ids():
                    if object_id in object_in_current_frame_ids:
                        self.potential_collision_map[sensor_id][potential_object_id].update_trajectory_by_frame(
                            object_id, new_frame
                        )

    def cleanup_data(self) -> None:
        """
        Cleans up stale data from the collision detection system.

        This method removes timed out objects and expired collision states to prevent memory leaks.
        It also cleans up frame history beyond the configured window and alert list that is older than the alert list timeout threshold.

        :return: None

        Examples::
            >>> collision_detection = CollisionDetection(config)
            >>> collision_detection.cleanup_data()
            >>> # Removes stale objects and collision states 
            >>> # Removes frame history and alert list that is older than the timeout threshold
        """
        # Remove objects that are not in the current frame and potential collisions that have timed out
        for sensor_id in self.timeout_counter_map.keys():
            removed_object_ids = []
            for obj_id in self.timeout_counter_map[sensor_id].keys():
                duration = datetime.now() - self.timeout_counter_map[sensor_id][obj_id]
                if duration.total_seconds() >= TIMEOUT_THRESHOLD:
                    is_in_potential_collision, _ = self.check_is_in_potential_collision(obj_id, sensor_id)
                    if is_in_potential_collision == False:
                        removed_object_ids.append(obj_id)
            for removed_object_id in removed_object_ids:
                self.remove_object_id(sensor_id, removed_object_id)
        
        # Remove frames that are older than the frame timeout threshold
        for sensor_id in self.sensor_to_frames_map.keys():
            frame_removed_ids = []
            for frame_id in self.sensor_to_frames_map[sensor_id].keys():
                current_time_ms = int(datetime.now().timestamp() * 1000)
                duration_ms = current_time_ms - self.sensor_to_frames_map[sensor_id][frame_id]["timestamp"]
                duration = duration_ms / 1000  # Convert to seconds
                if duration >= FRAME_TIMEOUT_THRESHOLD:
                    frame_removed_ids.append(frame_id)
            for frame_id in frame_removed_ids:
                if frame_id in self.sensor_to_frames_map[sensor_id]:
                    frame = self.sensor_to_frames_map[sensor_id][frame_id]
                    object_ids = [k for k in frame.keys() if not k.startswith("_") and k not in METADATA_KEYS]
                    for obj_id in object_ids:
                        if sensor_id in self.object_id_to_frames_map and obj_id in self.object_id_to_frames_map[sensor_id]:
                            if frame_id in self.object_id_to_frames_map[sensor_id][obj_id]:
                                self.object_id_to_frames_map[sensor_id][obj_id].remove(frame_id)
                    self.sensor_to_frames_map[sensor_id].pop(frame_id)

        # Remove alert list that is older than the alert list timeout threshold
        for sensor_id in self.alert_list.keys():
            alert_list_removed_ids = []
            for object_id in self.alert_list[sensor_id].keys():
                duration = datetime.now() - self.alert_list[sensor_id][object_id]
                if duration.total_seconds() >= self.config.alertListTimeoutThreshold:
                    alert_list_removed_ids.append(object_id)
            for object_id in alert_list_removed_ids:
                self.alert_list[sensor_id].pop(object_id)

    def update_frames(self, frames: dict[str, list[tuple[str, list[Message]]]]) -> None:
        """
        Updates the collision detection system with new frames of data.

        This method processes a batch of frames to update the collision detection state. For each frame, it:
        1. Updates internal frame tracking data structures
        2. Processes potential collisions between objects in the frame
        3. Cleans up stale tracking data

        :param dict[str, list[tuple[str, list[Message]]]] frames: Dictionary of frames containing sensor and object data
        :return: None

        Examples::
            >>> collision_detection = CollisionDetection(config)
            >>> frames = group_messages_by_frame_id(updated_messages)
            >>> collision_detection.update_frames(frames)
            >>> # Frames are processed and collision states are updated
        """
        if self.config.enable == False:
            return
        for sensor_id in frames.keys():
            for frame_id, frame in frames[sensor_id]:
                if len(frame) > 0:
                    object_in_current_frame_ids, new_frame = self.update_frame(sensor_id, frame, frame_id)
                    self.process_potential_collisions_on_new_frame(object_in_current_frame_ids, new_frame, sensor_id)

        self.cleanup_data()

    def update_behaviors(
        self, 
        behaviors: list[Behavior]
    ) -> None:
        """
        Updates the behavior mapping used for collision detection.

        This method updates the internal mapping of behaviors for each sensor and object,
        which is used to track object states and behaviors during collision detection.
        The behaviors are stored in a nested dictionary structure indexed by sensor_id 
        and object_id.
        Finally, it updates the collision states with the matched behavior data.

        :param list[Behavior] behaviors: List of behavior objects to update the mapping with
        :return: None

        Examples::
            >>> collision_detection = CollisionDetection(config)
            >>> behaviors = [
            ...     Behavior(id="sensor1 #-# obj1", ...),
            ...     Behavior(id="sensor1 #-# obj2", ...)
            ... ]
            >>> collision_detection.update_behaviors(behaviors)
            >>> # Behavior mapping is updated for collision tracking
        """
        self.object_id_to_behavior_map = {}
        for behavior in behaviors:
            sensor_id, object_id = behavior.id.split(" #-# ")
            if sensor_id not in self.object_id_to_behavior_map.keys():
                self.object_id_to_behavior_map[sensor_id] = {}
            self.object_id_to_behavior_map[sensor_id][object_id] = behavior

        for sensor_id in self.potential_collision_map.keys():
            for potential_object_id in self.potential_collision_map[sensor_id].keys():
                collision_state = self.potential_collision_map[sensor_id][potential_object_id]
                for object_id in collision_state.get_object_ids():
                    if sensor_id in self.object_id_to_behavior_map.keys():
                        if object_id in self.object_id_to_behavior_map[sensor_id].keys():
                            self.potential_collision_map[sensor_id][potential_object_id].update_behavior(
                                object_id, self.object_id_to_behavior_map[sensor_id][object_id]
                            )

    def merge_potential_collisions(self, object_id_i: str, object_id_j: str, sensor_id: str) -> str:
        """
        Merges two potential collision states when objects are found to be interacting.

        This method combines the collision states of two objects that have been detected as potentially colliding.
        It takes the collision state from object_id_j and merges it into the collision state of object_id_i,
        copying over all tracked objects, trajectories and behaviors. The object_id_j is then returned so it can
        be removed from tracking since its state has been merged.

        :param str object_id_i: ID of the first object (collision state to merge into)
        :param str object_id_j: ID of the second object (collision state to merge from)
        :param str sensor_id: ID of the sensor that detected these objects
        :return str: ID of the object that was merged and can be removed from tracking

        Examples::
            >>> collision_detection = CollisionDetection(config)
            >>> removed_id = collision_detection.merge_potential_collisions(
            ...     "obj_1",
            ...     "obj_2",
            ...     "sensor_1"
            ... )
            >>> # obj_2's collision state is merged into obj_1's state
            >>> # removed_id will be "obj_2"
        """
        collision_state_j = self.potential_collision_map[sensor_id][object_id_j]

        for object_id in collision_state_j.get_object_ids():
            coll_moment = collision_state_j.get_proximity_moment(object_id)
            self.potential_collision_map[sensor_id][object_id_i].add_object_id(object_id, coll_moment)
            self.potential_collision_map[sensor_id][object_id_i].update_trajectory_by_trajectory(
                object_id, collision_state_j.get_trajectory(object_id)
            )
            self.potential_collision_map[sensor_id][object_id_i].update_behavior(
                object_id, collision_state_j.get_behavior(object_id)
            )
        return object_id_j

    def check_bbox_intersection(
        self, 
        bbox1: Bbox, 
        bbox2: Bbox
    ) -> bool:
        """
        Check if two bounding boxes intersect.

        This method determines if two bounding boxes overlap by comparing their coordinates.
        Each bounding box is defined by leftX, topY, rightX, bottomY values from the Bbox model.

        :param Bbox bbox1: First bounding box
        :param Bbox bbox2: Second bounding box
        :return bool: True if bounding boxes intersect, False otherwise

        Examples::
            >>> collision_detection = CollisionDetection(config)
            >>> bbox1 = Bbox(leftX=0, topY=0, rightX=10, bottomY=10)
            >>> bbox2 = Bbox(leftX=5, topY=5, rightX=15, bottomY=15)
            >>> intersects = collision_detection.check_bbox_intersection(bbox1, bbox2)
            >>> print(intersects)  # True - boxes overlap
        """
        # Check if one box is to the left of the other
        if bbox1.rightX < bbox2.leftX or bbox2.rightX < bbox1.leftX:
            return False

        # Check if one box is above the other
        if bbox1.bottomY < bbox2.topY or bbox2.bottomY < bbox1.topY:
            return False

        # If neither of above conditions are true, boxes must intersect
        return True

    def calculate_iou(self, bbox1: Bbox, bbox2: Bbox) -> float:
        """
        Calculate Intersection-over-Union (IoU) for two axis-aligned bounding boxes.

        :param Bbox bbox1: First bounding box
        :param Bbox bbox2: Second bounding box
        :return float: IoU value between the two bounding boxes

        Examples::
            >>> collision_detection = CollisionDetection(config)
            >>> iou = collision_detection.calculate_iou(bbox1, bbox2)
            >>> print(iou)
            >>> # Returns IoU value between the two bounding boxes
        """
        inter_left = max(bbox1.leftX, bbox2.leftX)
        inter_top = max(bbox1.topY, bbox2.topY)
        inter_right = min(bbox1.rightX, bbox2.rightX)
        inter_bottom = min(bbox1.bottomY, bbox2.bottomY)

        inter_w = max(0.0, inter_right - inter_left)
        inter_h = max(0.0, inter_bottom - inter_top)
        inter_area = inter_w * inter_h

        area1 = max(0.0, bbox1.rightX - bbox1.leftX) * max(0.0, bbox1.bottomY - bbox1.topY)
        area2 = max(0.0, bbox2.rightX - bbox2.leftX) * max(0.0, bbox2.bottomY - bbox2.topY)

        union = area1 + area2 - inter_area
        if union <= 0:
            return 0.0
        return inter_area / union

    def distance_condition(
        self,
        distance: float,
        dist_threshold: float,
        iou_threshold: float,
        bbox1: Bbox,
        bbox2: Bbox,
        bearing_other: float
    ) -> bool:
        """
        Check if two objects meet distance-based collision conditions.

        This method evaluates whether two objects are in a potential collision state based on their:
        - Absolute distance
        - Lateral distance (perpendicular to direction of travel)
        - Bounding box intersection
        - Relative bearings

        :param float distance: Distance between the two objects
        :param float dist_threshold: Distance threshold for collision detection. Geo and Euclidian coordinate only
        :param float iou_threshold: IoU threshold for collision detection. Image coordinate only
        :param Bbox bbox1: Bounding box of potential object
        :param Bbox bbox2: Bounding box of other object 
        :param float bearing_other: Bearing (heading) of other object in degrees
        :return bool: True if objects meet collision conditions, False otherwise
        """
        if self.calibration_type == CalibrationType.IMAGE:
            # IoU-based collision using bearing-adjusted threshold (image coordinates)
            iou = self.calculate_iou(bbox1, bbox2)
            iou_condition = iou > iou_threshold
            return iou_condition
        elif self.calibration_type == CalibrationType.GEO:
            # Calculate centers of bounding boxes
            center1 = {
                'x': (bbox1.leftX + bbox1.rightX) / 2,
                'y': (bbox1.topY + bbox1.bottomY) / 2
            }
            center2 = {
                'x': (bbox2.leftX + bbox2.rightX) / 2,
                'y': (bbox2.topY + bbox2.bottomY) / 2
            }

            # Calculate angle between centers
            angle = math.degrees(math.atan2(
                center2['y'] - center1['y'],
                center2['x'] - center1['x']
            ))

            # Calculate angle between line segment and bearing_2 
            angle_diff_other = abs(angle - bearing_other)
            if angle_diff_other > 180:
                angle_diff_other = 360 - angle_diff_other

            # Calculate lateral (perpendicular) distance component
            lateral_distance = abs(distance * math.sin(math.radians(angle_diff_other)))

            # Distance + intersection condition (kept for robustness)
            proximity_condition = (distance < dist_threshold and 
                                lateral_distance < 3 * dist_threshold / 5 and
                                self.check_bbox_intersection(bbox1, bbox2))
            return proximity_condition
        else:
            return False

    def check_distances(
        self,
        pairs: list[tuple[ObjectData, ObjectData]],
        pair_ids: list[tuple[str, str]],
        is_geo_coordinate_frame: bool,
    ) -> list[tuple[str, float]]:
        """
        Calculate distances between pairs of objects and return those within threshold.

        This method calculates distances between pairs of objects and returns those within a specified threshold.
        It supports both geographic (lat/lon) and pixel-based coordinate systems.

        :param List pairs: List of object pairs to check distances between
        :param List pair_ids: List of object ID pairs corresponding to the position pairs
        :param bool is_geo_coordinate_frame: Whether coordinates are geographic (lat/lon) or pixels
        :return list[tuple[str, float]]: List of tuples containing (object_id, distance) for objects within threshold distance

        Examples::
            >>> collision_detection = CollisionDetection(config)
            >>> pairs = [
            ...     (ObjectData(x=10, y=10, ...), ObjectData(x=20, y=20, ...)),
            ...     (ObjectData(x=10, y=10, ...), ObjectData(x=30, y=30, ...))
            ... ]
            >>> pair_ids = [("obj_1", "obj_2"), ("obj_1", "obj_3")]
            >>> is_geo_coordinate_frame = True
            >>> distances = collision_detection.check_distances(pairs, pair_ids, is_geo_coordinate_frame)
            >>> # Returns list of tuples with object IDs and distances
        """
        points1 = np.empty((0, 2))
        points2 = np.empty((0, 2))
        for num in range(len(pairs)):
            seg1, seg2 = pairs[num]
            # print(pairs[num])
            points1 = np.concatenate((points1, np.column_stack((seg1.x, seg1.y))), axis=0)
            points2 = np.concatenate((points2, np.column_stack((seg2.x, seg2.y))), axis=0)
        objects = []
        if len(pairs) > 0:
            if self.calibration_type == CalibrationType.GEO:
                distances = calculate_haversine_distance_vectorized(
                    points1[:, 0], points1[:, 1], points2[:, 0], points2[:, 1]
                )
                dist_threshold = self.config.distanceMetersThreshold
                iou_threshold = 0.0
            elif self.calibration_type == CalibrationType.IMAGE:
                distances = [0.0] * len(pairs)
                dist_threshold = 0.0
                iou_threshold = self.config.iouThreshold

            for i in range(len(distances)):
                bbox1 = pairs[i][0].bbox
                bbox2 = pairs[i][1].bbox
                _, object_id_a = pair_ids[i]
                
                bearing_1 = pairs[i][0].bearing
                bearing_2 = pairs[i][1].bearing
                if bearing_1 == -1 or bearing_2 == -1:
                    bearing_1 = 0
                    bearing_2 = 180
                if self.distance_condition(distances[i], dist_threshold, iou_threshold, bbox1, bbox2, bearing_2):
                    objects.append((object_id_a, distances[i]))

        return objects

    def remove_object_id(self, sensor_id: str, object_id: str) -> None:
        """
        Removes an object ID and its associated data from all tracking maps for a given sensor.

        This method cleans up all references to an object ID across the collision detection system's
        internal tracking maps. It removes the object from:
        1. Timeout counter tracking
        2. Potential collision state tracking
        3. Frame reference mappings and updates reference counts
        4. Behavior mapping

        :param str sensor_id: ID of the sensor the object belongs to
        :param str object_id: ID of the object to remove
        :return: None

        Examples::
            >>> collision_detection = CollisionDetection(config)
            >>> collision_detection.remove_object_id(
            ...     "sensor_1",
            ...     "obj_1"
            ... )
            >>> # Object obj_1 is removed from all tracking for sensor_1
        """
        if sensor_id in self.timeout_counter_map.keys():
            if object_id in self.timeout_counter_map[sensor_id].keys():
                self.timeout_counter_map[sensor_id].pop(object_id)
        if sensor_id in self.potential_collision_map.keys():
            if object_id in self.potential_collision_map[sensor_id].keys():
                self.potential_collision_map[sensor_id].pop(object_id)
        if sensor_id in self.object_id_to_frames_map.keys():
            if object_id in self.object_id_to_frames_map[sensor_id].keys():
                for frame_id in self.object_id_to_frames_map[sensor_id][object_id]:
                    if sensor_id in self.sensor_to_frames_map.keys():
                        if frame_id in self.sensor_to_frames_map[sensor_id].keys():
                            self.sensor_to_frames_map[sensor_id][frame_id]["num_references"] -= 1
                            if object_id in self.sensor_to_frames_map[sensor_id][frame_id].keys():
                                self.sensor_to_frames_map[sensor_id][frame_id].pop(object_id)
                            if self.sensor_to_frames_map[sensor_id][frame_id]["num_references"] <= 0:
                                self.sensor_to_frames_map[sensor_id].pop(frame_id)
                self.object_id_to_frames_map[sensor_id].pop(object_id)
        if sensor_id in self.object_id_to_behavior_map.keys():
            if object_id in self.object_id_to_behavior_map[sensor_id].keys():
                self.object_id_to_behavior_map[sensor_id].pop(object_id)

    def is_ghost_object(self, collision_state: CollisionState, object_id: str) -> bool:
        """
        Check if an object in a collision state is likely a ghost object caused by tracker error.
        A ghost object is identified if its creation time is too close to the collision moment
        AND the total distance traveled since its initial appearance is too short.
        :param CollisionState collision_state: Collision state object
        :param str object_id: ID of the object to check
        :return bool: True if the object is a ghost object, False otherwise

        Examples::
            >>> is_ghost = collision_detection.is_ghost_object(collision_state, "obj1")
            >>> # Returns True if obj1 is a ghost object, False otherwise
        """
        trajectory = collision_state.get_trajectory(object_id)
        if not trajectory:
            return False
            
        sorted_traj = sorted(trajectory, key=lambda p: p["timestamp"])
        creation_time = sorted_traj[0]["timestamp"]
        
        proximity_moment = collision_state.get_proximity_moment(object_id)
        if proximity_moment is None:
            return False
            
        time_diff = abs(proximity_moment - creation_time)
        if time_diff > self.config.ghostTimeThresholdMs:
            return False
            
        if len(sorted_traj) >= 2:
            xs = np.array([p["x"] for p in sorted_traj])
            ys = np.array([p["y"] for p in sorted_traj])
            if self.calibration_type == CalibrationType.GEO:
                distances = calculate_haversine_distance_vectorized(
                    xs[:-1], ys[:-1], xs[1:], ys[1:]
                )
                threshold = self.config.ghostDistanceMetersThreshold
            else:
                distances = np.sqrt(np.diff(xs)**2 + np.diff(ys)**2)
                threshold = self.config.ghostDistancePixelsThreshold
            if float(np.sum(distances)) > threshold:
                return False
                    
        return True

    def get_collision_alerts(self) -> list[tuple[Incident, CollisionState]]:
        """
        Retrieves collision alerts from the collision detection system.

        This method checks the potential_collision_map for collision states that have timed out.
        For each timed out collision state with multiple objects involved, it:
        1. Creates an incident from the collision state
        2. Adds the involved object IDs to the alert list.
        3. Checks if the objects (except the primary object) are ghost objects and removes them if they are.
        4. Removes the processed objects from tracking.

        :return list[tuple[Incident, CollisionState]]: List of tuples containing the generated incident and corresponding collision state

        Examples::
            >>> collision_detection = CollisionDetection(config)
            >>> incidents = collision_detection.get_collision_alerts()
            >>> for incident, state in incidents:
            ...     print(f"Collision alert for objects: {state.get_object_ids()}")
            >>> # Returns list of collision incidents and states
        """
        collision_incidents = []
        if self.config.enable == False:
            return collision_incidents

        for sensor_id in self.potential_collision_map.keys():
            removed_object_ids = []
            for potential_object_id in self.potential_collision_map[sensor_id].keys():
                if self.potential_collision_map[sensor_id][potential_object_id].is_timeout():
                    collision_state = copy.deepcopy(self.potential_collision_map[sensor_id][potential_object_id])

                    if len(collision_state.get_object_ids()) > 1 and potential_object_id not in removed_object_ids:
                        ghost_object_ids = []
                        for obj_id in collision_state.get_object_ids():
                            if obj_id == collision_state.primary_object_id:
                                continue
                            if self.is_ghost_object(collision_state, obj_id):
                                ghost_object_ids.append(obj_id)
                        
                        if len(collision_state.get_object_ids()) - len(ghost_object_ids) > 1:
                            updated_collision_state = copy.deepcopy(collision_state)
                            updated_collision_state.object_ids = set([obj_id for obj_id in collision_state.get_object_ids() if obj_id not in ghost_object_ids])
                            incident = self.collision_state_to_incident(updated_collision_state)
                            collision_incidents.append((incident, updated_collision_state))
                            if sensor_id not in self.alert_list.keys():
                                self.alert_list[sensor_id] = {}
                            for object_id in updated_collision_state.get_object_ids():
                                self.alert_list[sensor_id][object_id] = datetime.now()
                        
                    removed_object_ids.append(potential_object_id)
                    for join_object_id in collision_state.get_object_ids():
                        if join_object_id != potential_object_id:
                            removed_object_ids.append(join_object_id)
            for removed_object_id in removed_object_ids:
                self.remove_object_id(sensor_id, removed_object_id)
        return collision_incidents


    def collision_state_to_incident(self, collision_state: CollisionState) -> Incident:
        """
        Converts a CollisionState object to an Incident object.

        This method creates an Incident object from a CollisionState by extracting relevant metadata:
        1. Gets start/end timestamps from trajectory data
        2. Formats object IDs and sensor information
        3. Adds analytics module metadata and place details
        4. Includes frame IDs and additional collision info

        :param CollisionState collision_state: Object containing collision tracking state and metadata
        :return Incident: Incident object with formatted collision information

        Examples::
            >>> collision_detection = CollisionDetection(config)
            >>> collision_state = collision_detection.potential_collision_map["sensor1"]["obj1"]
            >>> incident = collision_detection.collision_state_to_incident(collision_state)
            >>> # Returns Incident with collision_state metadata
        """

        timestamps = []
        trajectory = collision_state.get_trajectory(collision_state.primary_object_id)

        for point in trajectory:
            if "timestamp" in point:
                timestamps.append(point["timestamp"])

        current_time = datetime.now(timezone.utc)
        try:
            min_timestamp = min(timestamps)
            max_timestamp = max(timestamps)
            limit_milliseconds = 1000000000000
            # Check if timestamps are in milliseconds
            if min_timestamp > limit_milliseconds:  # If timestamp is in milliseconds
                # convert to seconds
                min_timestamp = min_timestamp / 1000.0
                max_timestamp = max_timestamp / 1000.0

            start_timestamp = datetime.fromtimestamp(min_timestamp, tz=timezone.utc)
            end_timestamp = datetime.fromtimestamp(max_timestamp, tz=timezone.utc)
        except (ValueError, OSError, OverflowError) as e:
            print(f"Error converting timestamps: {e}")
            start_timestamp = current_time
            end_timestamp = current_time
        location = collision_state.get_location()

        collision_moment = None
        proximity_moment = collision_state.get_proximity_moment(collision_state.primary_object_id)
        if proximity_moment is not None:
            collision_moment = datetime.fromtimestamp(proximity_moment / 1000.0, tz=timezone.utc)

        info = {
            "primaryObjectId": str(collision_state.primary_object_id),
            "location": f"{location.lat},{location.lon},{location.alt}",
        }
        if collision_moment is not None:
            info["collisionMoment"] = collision_moment.isoformat()

        # Create Incident
        incident = Incident(
            timestamp=start_timestamp,
            end=end_timestamp,
            sensorId=collision_state.sensor_id,
            objectIds=list(collision_state.get_object_ids()),
            place=collision_state.get_place(),
            analyticsModule=AnalyticsModule(
                id="Collision Detection Module",
                description=f"Potential collision detected between {len(collision_state.get_object_ids())} vehicles",
                info={"triggerModules": ", ".join(collision_state.trigger_modules)},
                source="",
                version="",
            ),
            category="collision",
            isAnomaly=True,
            info=info,
            frameIds=[],
        )

        return incident

    def parse_object(self, obj: Object) -> tuple[str, tuple[float, float], Bbox, str, bool]:
        """
        Parses object data to extract coordinates and metadata for collision detection.

        This method extracts relevant coordinate information and metadata from an object.
        It determines whether to use geographic or pixel coordinates and returns:
        1. Object ID for tracking
        2. Tuple of coordinates (x,y) where:
        - For pixel coordinates: x is bbox center, y is bbox top
        - For geographic coordinates: x is latitude, y is longitude
        3. Full bounding box object
        4. Object type classification
        5. Flag indicating if geographic coordinates are used

        :param Object obj: Object containing bbox coordinates, geo coordinates, and metadata
        :return tuple[str, tuple[float, float], Bbox, str, bool]: Tuple containing (object_id, (x,y), bbox, object_type, is_geo_coordinate)

        Examples::
            >>> collision_detection = CollisionDetection(config)
            >>> for obj in frame.objects:
            ...     obj_id, coords, bbox, obj_type, is_geo = collision_detection.parse_object(obj)
        """

        obj_id = obj.id
        # Safe access for bbox
        bbox = getattr(obj, "bbox", None)
        if bbox is None:
            bbox = Bbox(leftX=0, topY=0, rightX=0, bottomY=0)
        x1 = bbox.leftX
        y1 = bbox.topY
        x2 = bbox.rightX
        y2 = bbox.bottomY
        # Safe access for coordinate
        coordinate = getattr(obj, "coordinate", None)
        coord_x = getattr(coordinate, "x", 0)
        coord_y = getattr(coordinate, "y", 0)
        # Safe access for location
        location = getattr(obj, "location", None)
        lat = getattr(location, "lat", 0)
        lon = getattr(location, "lon", 0)
        object_type = obj.type

        if coord_x != 0 and coord_y != 0 and lat != 0 and lon != 0:
            is_geo_coord = True
        else:
            is_geo_coord = False
            self.calibration_type = CalibrationType.IMAGE

        if not is_geo_coord:
            geo_x = (x1 + x2) / 2
            geo_y = (y1 + y2) / 2
        else:
            geo_x = lat
            geo_y = lon

        return obj_id, (geo_x, geo_y), bbox, object_type, is_geo_coord
