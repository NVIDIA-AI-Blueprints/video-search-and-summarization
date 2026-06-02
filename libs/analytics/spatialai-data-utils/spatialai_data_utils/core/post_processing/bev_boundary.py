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

"""
BEV (Bird's Eye View) Boundary Checking Module

This module provides utilities for checking whether 3D object locations fall within
defined BEV (Bird's Eye View) boundaries. It is used for filtering out detections
that are outside the valid spatial region of interest, such as outside a building
floorplan or scene boundaries.

Key Features:
- Check if 3D positions are within rectangular BEV boundaries
- Handle cases with or without defined boundaries
- Used for spatial filtering in multi-camera tracking systems

Main Functions:
- is_inside_bev_boundary: Check if a translation is within BEV boundaries

Boundary Format:
- bev_boundaries: [x_min, x_max, y_min, y_max] in world coordinates
- Boundaries define a rectangular region in the BEV plane (top-down view)

Use Cases:
- Filter detections outside building floorplan
- Remove objects beyond scene extent
- Validate tracking results within operational area
- Post-process 3D detection outputs

Typical Usage:
1. Define BEV boundaries based on scene extent or floorplan
2. Check each detection's 3D location against boundaries
3. Filter out detections outside valid region
4. Use remaining detections for downstream processing
"""


def is_inside_bev_boundary(translation, bev_boundaries=None):
    """
    Check if a 3D translation (position) is within the defined BEV boundaries.

    This function validates whether an object's 3D world position falls within
    a rectangular region defined by BEV boundaries. If no boundaries are specified,
    all positions are considered valid.

    :param translation: 3D position [x, y, z] or [x, y] in world coordinates (meters).
                        Only x and y coordinates are checked against boundaries.
    :type translation: list or tuple or numpy.ndarray
    :param bev_boundaries: BEV boundary limits [x_min, x_max, y_min, y_max] in world
                           coordinates. If None, no boundary check is performed and
                           the function returns True. Defaults to None.
    :type bev_boundaries: list or tuple or None
    :return: True if the translation is within boundaries (or no boundaries defined),
             False if outside the boundaries.
    :rtype: bool

    Example:
        >>> boundaries = [-50.0, 50.0, -30.0, 30.0]  # x: [-50, 50], y: [-30, 30]
        >>> is_inside_bev_boundary([10.0, 20.0, 1.5], boundaries)
        True
        >>> is_inside_bev_boundary([60.0, 20.0, 1.5], boundaries)
        False
        >>> is_inside_bev_boundary([10.0, 20.0, 1.5], None)
        True
    """
    if bev_boundaries:
        if not (
            bev_boundaries[0] < translation[0] < bev_boundaries[1]
            and bev_boundaries[2] < translation[1] < bev_boundaries[3]
        ):
            # remove detections outside the floorplan
            return False
        else:
            return True
    else:
        # no boundary defined
        return True
