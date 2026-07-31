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

from mdx.analytics.core.schema.models import Coordinate
from mdx.analytics.core.schema.trajectory.trajectory_i import TrajectoryI
from mdx.analytics.core.stream.state.behavior.state_management_base import StateMgmtBase

logger = logging.getLogger(__name__)


class StateMgmtI(StateMgmtBase):
    """
    Behavior state management in image coordinates.

    Differs from the base only in the trajectory type; batching, emission and trip handling are
    inherited unchanged. Trip behaviors are always produced -- callers that do not run tripwire or
    ROI detection simply ignore ``BehaviorBatch.trip_behaviors``.

    Examples:
        >>> state_manager = StateMgmtI(config, calibration)
        >>> batch = state_manager.process_batch(messages_map)
        >>> print(f"Writing {len(batch.behaviors_to_write)} behavior(s)")
    """

    def _create_trajectory(self, id: str, start: datetime, end: datetime,
                          points: list[Coordinate]) -> TrajectoryI:
        """Returns TrajectoryI for image coordinates."""
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
