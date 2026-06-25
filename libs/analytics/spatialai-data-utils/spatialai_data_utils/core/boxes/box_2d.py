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
2D Bounding Box Utilities Module

This module provides utility functions for working with 2D bounding boxes in image space.
It includes functions for validation, visibility checking, and box filtering operations.

Key Features:
- Check if points fall within image boundaries
- Validate bounding box visibility and validity
- Compute bounding box areas
- Filter boxes based on visibility criteria
- Handle both individual points and box corners

Main Functions:
- is_inside_image_any: Check if any point is within image boundaries
- is_inside_image_all: Check if all points are within image boundaries
- is_valid_box2d: Check if a 2D box is valid and visible
- get_area_2d: Compute area of a 2D bounding box
- visibility_filter_boxes2d: Filter boxes by visibility threshold

Bounding Box Format:
- 2D boxes: [xmin, ymin, xmax, ymax] in pixel coordinates
- Image coordinates: Origin at top-left, x increases right, y increases down
- Valid boxes must have xmax > xmin and ymax > ymin

Visibility Criteria:
- Boxes can be partially outside image boundaries
- Visibility is measured as ratio of visible area to total box area
- Configurable visibility threshold for filtering

Use Cases:
- Validate detection boxes before evaluation
- Filter occluded or partially visible objects
- Pre-process bounding boxes for tracking
- Quality control for annotation data

Typical Workflow:
1. Check if box corners are within image
2. Compute visible area
3. Filter boxes by visibility threshold
4. Use validated boxes for downstream processing
"""

import numpy as np

from spatialai_data_utils.constants import IMAGE_SIZE


def is_inside_image_any(pts_2d, image_size=IMAGE_SIZE):
    """
    Check if *any* point in a set of 2D points falls within image boundaries.

    :param pts_2d: A NumPy array of 2D points with shape (N, 2), where N is the number of points.
                   Each row represents [x, y].
    :type pts_2d: numpy.ndarray
    :param image_size: A tuple representing the image dimensions (width, height).
                       Defaults to `IMAGE_SIZE` from constants.
    :type image_size: tuple(int, int), optional
    :return: True if at least one point is within the image boundaries [0, width) and [0, height),
             False otherwise.
    :rtype: bool
    """
    x_flags = np.logical_and(pts_2d[:, 0] >= 0, pts_2d[:, 0] < image_size[0])
    y_flags = np.logical_and(pts_2d[:, 1] >= 0, pts_2d[:, 1] < image_size[1])
    insides = np.logical_and(x_flags, y_flags)
    return np.any(insides)


def is_inside_image_all(pts_2d, image_size=IMAGE_SIZE):
    """
    Check if *all* points in a set of 2D points fall within image boundaries.

    :param pts_2d: A NumPy array of 2D points with shape (N, 2), where N is the number of points.
                   Each row represents [x, y].
    :type pts_2d: numpy.ndarray
    :param image_size: A tuple representing the image dimensions (width, height).
                       Defaults to `IMAGE_SIZE` from constants.
    :type image_size: tuple(int, int), optional
    :return: True if all points are within the image boundaries [0, width) and [0, height),
             False otherwise.
    :rtype: bool
    """
    x_flags = np.logical_and(pts_2d[:, 0] >= 0, pts_2d[:, 0] < image_size[0])
    y_flags = np.logical_and(pts_2d[:, 1] >= 0, pts_2d[:, 1] < image_size[1])
    insides = np.logical_and(x_flags, y_flags)
    return np.all(insides)


def get_box_2d_from_projected_vertices(bboxes_2d, image_size=IMAGE_SIZE):
    """
    Calculate the axis-aligned 2D bounding box enclosing a set of projected 3D box vertices.

    Takes an array where each row represents the projected 2D coordinates of the
    vertices of a 3D box, finds the min/max x and y values, and clamps them
    to the image boundaries.

    :param bboxes_2d: A NumPy array of shape (N, 8, 2) or similar structure where the
                      second dimension represents the vertices and the third dimension
                      contains the [x, y] coordinates for N boxes.
    :type bboxes_2d: numpy.ndarray
    :param image_size: A tuple representing the image dimensions (width, height)
                       for clamping. Defaults to `IMAGE_SIZE` from constants.
    :type image_size: tuple(int, int), optional
    :return: A NumPy array of shape (N, 4) containing the [x_min, y_min, x_max, y_max]
             coordinates of the enclosing 2D boxes, clamped to image boundaries.
             Returns an empty array if the input is empty.
    :rtype: numpy.ndarray
    """
    if len(bboxes_2d) == 0:
        # empty boxes_2d
        return bboxes_2d

    left = np.min(bboxes_2d[..., 0], axis=1)
    right = np.max(bboxes_2d[..., 0], axis=1)
    top = np.min(bboxes_2d[..., 1], axis=1)
    bottom = np.max(bboxes_2d[..., 1], axis=1)

    left[left < 0] = 0
    right[right > image_size[0]] = image_size[0]
    top[top < 0] = 0
    bottom[bottom > image_size[1]] = image_size[1]

    bboxes_2d_proj = np.concatenate(
        (left[:, None], top[:, None], right[:, None], bottom[:, None]), axis=1
    )
    return bboxes_2d_proj
