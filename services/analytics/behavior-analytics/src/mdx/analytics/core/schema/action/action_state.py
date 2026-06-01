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

from datetime import datetime

from mdx.analytics.core.schema.models import Action


class ActionState:
    """
    Class to track action state for a specific sensor/object.

    :ivar str sensor_id: ID of the sensor
    :ivar str object_id: ID of the object
    :ivar str last_action: Last action detected
    :ivar dict[str, list[list[datetime]]] action_intervals: Dictionary mapping action types to lists of timestamp intervals
    :ivar dict[str, float] durations: Dictionary mapping action types to durations
    :ivar float ACTION_CONFIDENCE_THRESHOLD: Threshold for action detection

    Examples::
        >>> action_state = ActionState("sensor1", "object1", 0.5)
        >>> action_state.update_action(Action(type="Lying Down", confidence=0.95), datetime.now())
        >>> action_state.get_last_action()
        "Lying Down"
        >>> action_state.get_last_interval("Lying Down")
        [datetime.now(), datetime.now()]
    """
    __slots__ = ['sensor_id', 'object_id', 'last_action', 'action_intervals', 'durations', 'ACTION_CONFIDENCE_THRESHOLD']
    def __init__(self, sensor_id: str, object_id: str, action_threshold: float):
        """
        Initialize the ActionState object.

        :param str sensor_id: ID of the sensor
        :param str object_id: ID of the object
        """
        self.sensor_id = sensor_id
        self.object_id = object_id
        self.last_action = "Unknown"
        self.action_intervals = {}
        self.durations = {}
        self.ACTION_CONFIDENCE_THRESHOLD = action_threshold

    def update_action(self, action: Action, timestamp: datetime) -> None:
        """
        Update the action state for a specific sensor/object.

        :param Action action: Action to update the state for
        :param datetime timestamp: Timestamp of the action
        :return: None
        """
        if action.confidence > self.ACTION_CONFIDENCE_THRESHOLD:
            action_type = action.type
            if action_type not in self.action_intervals:
                self.action_intervals[action_type] = []
                self.durations[action_type] = 0.0
            if self.last_action == action_type:
                # Update the last action interval end time
                self.durations[action_type] += float((timestamp - self.action_intervals[action_type][-1][1]).total_seconds())
                self.action_intervals[action_type][-1][1] = timestamp
            else:
                # Start a new action interval
                self.last_action = action_type
                self.action_intervals[action_type].append([timestamp, timestamp])

    def get_last_action(self) -> str:
        """
        Get the last action detected.

        :return str: Last action detected
        """
        return self.last_action

    def get_last_interval(self, action_type: str) -> list[datetime] | None:
        """
        Get the last interval for a specific action type.

        :param str action_type: Type of action to get the last interval for
        :return list[datetime] | None: Last interval for the action type
        """
        if action_type not in self.action_intervals:
            return None
        return self.action_intervals[action_type][-1]
    
    def get_action_intervals(self) -> dict[str, list[list[datetime]]]:
        """
        Get the action intervals.

        :return dict[str, list[list[datetime]]]: Action intervals
        """
        return self.action_intervals

    def get_durations(self) -> dict[str, float]:
        """
        Get the durations of the actions.

        :return dict[str, float]: Durations of the actions
        """
        return self.durations

class FallRiskState:
    """
    Class to track fall risk state for a specific sensor/object.

    :ivar str sensor_id: ID of the sensor
    :ivar str object_id: ID of the object
    :ivar bool fall_risk: Whether the object is in fall risk
    :ivar datetime start_time: Start time of the fall risk
    :ivar datetime end_time: End time of the fall risk
    :ivar int n_confirm_start: Number of times confirm at start time
    :ivar int n_confirm_end: Number of times confirm at end time
    :ivar int N_CONFIRM_START: Number of times confirm to start fall risk
    :ivar int N_CONFIRM_END: Number of times confirm to end fall risk
    """
    __slots__ = ['sensor_id', 'object_id', 'fall_risk', 'start_time', 'end_time', 
                'n_confirm_start', 'n_confirm_end', 'N_CONFIRM_START', 'N_CONFIRM_END']
    def __init__(
        self,
        sensor_id: str,
        object_id: str,
        timestamp: datetime,
        n_confirm_start: int,
        n_confirm_end: int,
    ):
        """
        Initialize the FallRiskState object.

        :param str sensor_id: ID of the sensor
        :param str object_id: ID of the object
        """
        self.sensor_id = sensor_id
        self.object_id = object_id
        self.fall_risk = False
        self.start_time = timestamp
        self.end_time = timestamp
        self.n_confirm_start = 0
        self.n_confirm_end = 0
        self.N_CONFIRM_START = n_confirm_start
        self.N_CONFIRM_END = n_confirm_end

    def is_fall_risk(self) -> bool:
        """
        Check if the object is in fall risk.

        :return bool: Whether the object is in fall risk
        """
        return self.fall_risk
    
    def update_start_time(self, timestamp: datetime) -> None:
        """
        Update the start time of the fall risk.

        :param datetime timestamp: Timestamp of the start time
        :return: None
        """
        self.start_time = timestamp

    def update_end_time(self, timestamp: datetime) -> None:
        """
        Update the end time of the fall risk.

        :param datetime timestamp: Timestamp of the end time
        :return: None
        """
        self.end_time = timestamp
    
    def update_fall_risk(self, fall_risk: bool, timestamp: datetime) -> None:
        """
        Update the fall risk state for a specific sensor/object.

        :param bool fall_risk: Whether the object is in fall risk
        :param datetime timestamp: Timestamp of the fall risk
        :return: None
        """
        self.fall_risk = fall_risk
        self.end_time = timestamp

    def get_start_time(self) -> datetime:
        """
        Get the start time of the fall risk.

        :return datetime: Start time of the fall risk
        """
        return self.start_time
    
    def get_end_time(self) -> datetime:
        """
        Get the end time of the fall risk.

        :return datetime: End time of the fall risk
        """
        return self.end_time

    def increase_n_confirm_start(self) -> None:
        """
        Increase the number of times confirm to start fall risk.

        :return: None
        """
        self.n_confirm_start += 1
    
    def is_confirmed_start(self) -> bool:
        """
        Check if the start time has been confirmed.

        :return bool: Whether the start time has been confirmed
        """
        return self.n_confirm_start >= self.N_CONFIRM_START

    def increase_n_confirm_end(self) -> None:
        """
        Increase the number of times confirm to end fall risk.

        :return: None
        """
        self.n_confirm_end += 1
    
    def is_confirmed_end(self) -> bool:
        """
        Check if the end time has been confirmed.

        :return bool: Whether the end time has been confirmed
        """
        return self.n_confirm_end >= self.N_CONFIRM_END
    
    def reset_n_confirm_start(self) -> None:
        """
        Reset the number of times confirm to start fall risk.

        :return: None
        """
        self.n_confirm_start = 0
    
    def reset_n_confirm_end(self) -> None:
        """
        Reset the number of times confirm to end fall risk.

        :return: None
        """
        self.n_confirm_end = 0