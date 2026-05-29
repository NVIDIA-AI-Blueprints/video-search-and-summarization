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

import logging
from datetime import datetime

from mdx.analytics.core.schema.models import Behavior, Coordinate, Message
from mdx.analytics.core.schema.trajectory.trajectory_i import TrajectoryI
from mdx.analytics.core.stream.state.behavior.state_management_base import StateMgmtBase

logger = logging.getLogger(__name__)


class StateMgmtIWithTripwire(StateMgmtBase):
    """
    Image coordinate state management with trip wire detection.
    
    Extends StateMgmtBase to add trip wire event detection and return both 
    behavior and trip messages. Uses IncrementalTrajectoryI for trajectory creation.

    Examples:
        >>> state_manager = StateMgmtIWithTripwire(config, calibration)
        >>> messages = [Message(sensor=Sensor(id="sensor1"))]
        >>> behavior, trip_behavior = state_manager.update_behavior("sensor1_obj1", messages)
        >>> print(f"Behavior: {behavior}")
    """

    def _create_trajectory(self, id: str, start: datetime, end: datetime, 
                          points: list[Coordinate]) -> TrajectoryI:
        """Returns IncrementalTrajectoryI for image coordinates."""
        return TrajectoryI(
            id=id,
            start=start,
            end=end,
            points=points,
            smooth_min_points=self.config.traj_smooth_min_points,
            smooth_window_size=self.config.traj_smooth_window_size,
            distance_stride=self.config.traj_distance_stride,
            speed_segment_size=self.config.traj_speed_segment_size,
        )


class StateMgmtI(StateMgmtIWithTripwire):
    """
    Image coordinate system state management without trip wire.
    
    Return only the behavior message, discarding the trip wire behavior for simpler API.
    """

    def update_behavior(self, message_key: str, messages: list[Message], **kwargs) -> Behavior | None:
        """Returns only the behavior message, discarding trip wire behavior."""
        return super().update_behavior(message_key, messages, **kwargs)[0]
