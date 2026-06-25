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

"""
Image coordinate system trajectory representation.

TrajectoryI is an alias for TrajectoryBase, which uses image coordinate convention
where the y-axis is inverted (increasing downward).

Examples::

    from datetime import datetime
    from mdx.analytics.core.schema.models import Coordinate
    from mdx.analytics.core.schema.trajectory.trajectory_i import TrajectoryI

    # Create a trajectory moving upward in image space
    # (y decreases as we move up in image coordinates)
    points = [
        Coordinate(x=100, y=200, z=0),  # Bottom of image
        Coordinate(x=100, y=150, z=0),  # Moving up
        Coordinate(x=100, y=100, z=0)   # Top of image
    ]
    trajectory = TrajectoryI(
        id="traj_i1",
        start=datetime.now(),
        end=datetime.now(),
        points=points
    )

    print(f"Image bearing: {trajectory.bearing} degrees")  # Returns 90.0 (upward)
"""

from mdx.analytics.core.schema.trajectory.trajectory_base import TrajectoryBase

# Type alias for image coordinate trajectories
# TrajectoryBase already uses image coordinate convention (inverted y-axis)
TrajectoryI = TrajectoryBase
