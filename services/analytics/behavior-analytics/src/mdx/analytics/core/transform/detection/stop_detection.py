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

from mdx.analytics.core.transform.calibration.calibration_dynamic import CalibrationType
from mdx.analytics.core.schema.models import Coordinate, Message, Behavior
from datetime import datetime, timedelta
from collections import deque
from mdx.analytics.core.utils.distance_util import haversine_distance_coords, euclidean_distance


class StopDetection:
    """
    Class to detect vehicle stops.
    There are two ways to detect vehicle stops:
    - by distance covered in the latest T seconds.
    - by average speed below the threshold and total time since the start of the tracking is greater than T.

    Stop detection algorithm by distance:
    - Maintains a sliding window of position data for the last T seconds
    - Calculates total distance traveled within this time window
    - Vehicle is considered stopped if distance < stop_distance_threshold

    Stop detection algorithm by speed:
    - Vehicle is considered stopped if speed < speed_threshold and timeInterval > time_interval_threshold
    """
    def __init__(self, calibration_type: CalibrationType) -> None:
        """
        Initialize stop detection.

        :param CalibrationType calibration_type: The calibration type (IMAGE or GEO) used for distance calculations.
        :return: None
        """
        self.time_window_seconds = None
        self.stop_distance_threshold = None
        self.calibration_type = calibration_type
        # Dictionary to store position history for each sensor-object pair
        self.position_history: dict[str, dict[str, deque]] = {}
        self.allowed_objects = ["Vehicle"]

    def _distance(self, pos1: Coordinate, pos2: Coordinate) -> float:
        """
        Calculate distance between two coordinates.

        :param Coordinate pos1: First coordinate
        :param Coordinate pos2: Second coordinate
        :return float: Distance between two coordinates
        """
        if self.calibration_type == CalibrationType.IMAGE:
            return euclidean_distance(pos1, pos2)
        elif self.calibration_type == CalibrationType.GEO:
            return haversine_distance_coords(pos1, pos2)
        else:
            return float('inf')

    def set_time_window_seconds(self, time_window_seconds: float) -> None:
        """
        Set the time window seconds. Duration of the stop detection.
        
        :param float time_window_seconds: Time window seconds
        :return: None
        """
        self.time_window_seconds = time_window_seconds

    def set_stop_distance_threshold(self, stop_distance_threshold: float) -> None:
        """
        Set the stop distance threshold. Distance traveled in the time window.
        
        :param float stop_distance_threshold: Stop distance threshold
        :return: None
        """
        self.stop_distance_threshold = stop_distance_threshold


    def _update_position(
        self, 
        sensor_id: str, 
        object_id: str, 
        position: Coordinate, 
        timestamp: datetime
    ) -> None:
        """
        Update position history for a sensor-object pair.
        
        :param str sensor_id: ID of the sensor
        :param str object_id: ID of the object/vehicle
        :param Coordinate position: Current position coordinates
        :param datetime timestamp: Current timestamp
        :return: None
        """
        # Initialize storage for this sensor-object pair if needed
        if sensor_id not in self.position_history:
            self.position_history[sensor_id] = {}
        if object_id not in self.position_history[sensor_id]:
            self.position_history[sensor_id][object_id] = deque()
        
        # Add new position data
        position_data = {
            'position': position,
            'timestamp': timestamp
        }
        self.position_history[sensor_id][object_id].append(position_data)
        
        # Remove old data outside the time window
        self._cleanup_old_positions(sensor_id, object_id, timestamp)


    def update_frame(self, sensor_id: str, frame: list[Message]) -> None:
        """
        Updates the stop detection system with a new frame of data.

        This method processes a new frame of sensor data to track objects and their positions.
        It extracts object IDs and positions from the frame, updates internal tracking maps,
        and prepares the data for stop detection processing.

        :param str sensor_id: ID of the sensor that generated this frame
        :param list[Message] frame: List of Message objects containing sensor data and object positions/metadata
        :return: None

        Examples::
            >>> stop_detection = StopDetection(config)
            >>> frames = group_messages_by_frame_id(updated_messages)
            >>> for sensor_id in frames.keys():
            ...     for frame_id, frame in frames[sensor_id]:
            ...         stop_detection.update_frame(sensor_id, frame)
        """

        for message in frame:
            obj = message.object
            if obj is None:
                continue
            if obj.type in self.allowed_objects:
                if self.calibration_type == CalibrationType.GEO:
                    # Guard against missing location data
                    location = getattr(obj, 'location', None)
                    if location is None:
                        continue
                    lat = getattr(location, 'lat', None)
                    lon = getattr(location, 'lon', None)
                    if lat is None or lon is None:
                        continue
                    position = Coordinate(x=lon, y=lat)
                elif self.calibration_type == CalibrationType.IMAGE:
                    # Guard against missing bbox data
                    bbox = getattr(obj, 'bbox', None)
                    if bbox is None:
                        continue
                    x1 = bbox.leftX
                    y1 = bbox.topY
                    x2 = bbox.rightX
                    y2 = bbox.bottomY
                    position = Coordinate(x=(x1+x2)/2, y=(y1+y2)/2)
                else:
                    continue
                
                self._update_position(sensor_id, obj.id, position, message.timestamp)

    
    def update_frames(self, frames: dict[str, list[tuple[str, list[Message]]]]) -> None:
        """
        Update the stop detection system with a new set of frames.

        :param dict[str, list[tuple[str, list[Message]]]] frames: Dictionary of frames grouped by sensor ID
        :return: None
        """
        for sensor_id in frames.keys():
            for _, frame in frames[sensor_id]:
                if len(frame) > 0:
                    self.update_frame(sensor_id, frame)
    
    def _cleanup_old_positions(
        self, 
        sensor_id: str, 
        object_id: str, 
        current_timestamp: datetime
    ) -> None:
        """
        Remove position data older than the time window.
        
        :param str sensor_id: ID of the sensor
        :param str object_id: ID of the object/vehicle
        :param datetime current_timestamp: Current timestamp
        :return: None
        """
        if self.time_window_seconds is None:
            return
        history = self.position_history[sensor_id][object_id]
        cutoff_time = current_timestamp - timedelta(seconds=self.time_window_seconds + 1)
        
        while history and history[0]['timestamp'] < cutoff_time:
            history.popleft()
    

    def _calculate_distance_traveled(self, sensor_id: str, object_id: str) -> tuple[float, float]:
        """
        Calculate average distance traveled in the time window and the distance traveled between the last and first position.

        :param str sensor_id: ID of the sensor
        :param str object_id: ID of the object/vehicle
        :return tuple[float, float]: Average distance traveled over the time window and the distance traveled between the last and first position

        Examples::
            >>> stop_detection = StopDetection(config)
            >>> distance_traveled, distance_last_first = stop_detection._calculate_distance_traveled(sensor_id, object_id)
            >>> print(distance_traveled)
            >>> print(distance_last_first)
        """
        if (sensor_id not in self.position_history or 
            object_id not in self.position_history[sensor_id]):
            return float('inf'), float('inf')
 
        history = self.position_history[sensor_id][object_id]
        if len(history) < 2:
            return float('inf'), float('inf')

        total_distance = 0.0
        avg_distance = 0.0
        distance_last_first = 0.0
        for i in range(1, len(history)):
            prev_pos = history[i-1]['position']
            curr_pos = history[i]['position']
            
            # Calculate Euclidean distance between consecutive points
            distance = self._distance(prev_pos, curr_pos)
            total_distance += distance
        avg_distance = total_distance / (len(history) - 1)
        distance_last_first = self._distance(history[-1]['position'], history[0]['position'])
        return avg_distance, distance_last_first
        

    def is_vehicle_stopped_by_distance(self, sensor_id: str, object_id: str) -> bool:
        """
        Determine if vehicle is stopped based on distance traveled in time window.
        
        :param str sensor_id: ID of the sensor
        :param str object_id: ID of the object/vehicle
        :return bool: True if vehicle is stopped (distance < threshold), False otherwise
        """
        if self.stop_distance_threshold is None:
            return False
        if (sensor_id not in self.position_history or
            object_id not in self.position_history[sensor_id] or
            len(self.position_history[sensor_id][object_id]) < 2):
            return False

        distance_traveled, distance_last_first = self._calculate_distance_traveled(sensor_id, object_id)
        duration = self.position_history[sensor_id][object_id][-1]['timestamp'] - self.position_history[sensor_id][object_id][0]['timestamp']
        duration = duration.total_seconds()
        
        return distance_traveled < self.stop_distance_threshold and distance_last_first < 3*self.stop_distance_threshold and duration >= self.time_window_seconds
    
    
    def is_vehicle_stopped_by_speed(
        self, 
        behavior: Behavior, 
        speed_threshold: float, 
        time_interval_threshold: float
    ) -> bool:
        """
        Determine if vehicle is stopped based on speed.

        :param Behavior behavior: Behavior object
        :param float speed_threshold: Speed threshold
        :param float time_interval_threshold: Time interval threshold
        :return bool: True if vehicle is stopped (speed < threshold), False otherwise
        """
        if behavior.speed < speed_threshold and behavior.timeInterval > time_interval_threshold:
            return True
        return False


    def _remove_object(self, sensor_id: str, object_id: str):
        """
        Remove all stored data for a specific vehicle.
        
        :param str sensor_id: ID of the sensor
        :param str object_id: ID of the object to remove
        :return: None
        """
        if (sensor_id in self.position_history and 
            object_id in self.position_history[sensor_id]):
            del self.position_history[sensor_id][object_id]
            
            # Clean up empty sensor dictionary
            if not self.position_history[sensor_id]:
                del self.position_history[sensor_id]
    

    def _remove_sensor(self, sensor_id: str) -> None:
        """
        Remove a sensor from the position history.

        :param str sensor_id: ID of the sensor to remove
        :return: None
        """
        if sensor_id in self.position_history:
            del self.position_history[sensor_id]


    def update_live_object(self, live_objects: list[str]) -> None:
        """
        Update the live object state.

        :param list[str] live_objects: List of live objects
        :return: None
        """
        live_object_dict = {}
        for live_object in live_objects:
            parts = live_object.split(" #-# ")
            if len(parts) != 2:
                continue
            sensor_id, object_id = parts

            if sensor_id not in live_object_dict:
                live_object_dict[sensor_id] = set()
            live_object_dict[sensor_id].add(object_id)
        
        sensor_ids = list(self.position_history.keys())
        for sensor_id in sensor_ids:
            if sensor_id not in live_object_dict:
                self._remove_sensor(sensor_id)
            else:
                object_ids = list(self.position_history[sensor_id].keys())
                for object_id in object_ids:
                    if object_id not in live_object_dict[sensor_id]:
                        self._remove_object(sensor_id, object_id)
