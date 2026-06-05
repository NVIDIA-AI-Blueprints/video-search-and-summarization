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
2D Bounding Box Visualization Module

This module provides simple utilities for drawing 2D bounding boxes on images
using OpenCV. It supports drawing single or multiple boxes with customizable
colors and line thickness.

Key Features:
- Draw single 2D bounding box on image
- Draw multiple 2D bounding boxes in batch
- Customizable box color and line thickness
- Uses OpenCV for fast rendering

Main Functions:
- draw_box_2d: Draw a single 2D bounding box
- draw_boxes_2d: Draw multiple 2D bounding boxes

Bounding Box Format:
- Input format: [xmin, ymin, xmax, ymax]
- Coordinates in pixels (integer or float, will be converted to int)
- Top-left and bottom-right corners

Drawing Parameters:
- color: RGB tuple (B, G, R) for OpenCV compatibility
- thickness: Line thickness in pixels (default: 2)

Use Cases:
- Visualize detection results on images
- Debug bounding box outputs
- Create annotated images for presentations
- Validate tracking results
- Compare predictions with ground truth

Typical Usage:
1. Load image using cv2.imread or similar
2. Prepare bounding boxes in [xmin, ymin, xmax, ymax] format
3. Call draw_box_2d or draw_boxes_2d to render boxes
4. Display or save annotated image
"""

import cv2


def draw_box_2d(image, box_2d, color=(255, 255, 255), thinkness=2):
    """
    Draw a single 2D bounding box on an image.

    :param image: The input image (NumPy array).
    :type image: numpy.ndarray
    :param box_2d: A list or tuple representing the box [xmin, ymin, xmax, ymax].
    :type box_2d: list or tuple
    :param color: The color of the bounding box (B, G, R). Defaults to (255, 255, 255) (white).
    :type color: tuple, optional
    :param thinkness: The thickness of the box lines. Defaults to 2.
    :type thinkness: int, optional
    :return: The image with the bounding box drawn.
    :rtype: numpy.ndarray
    """
    left, top, right, bottom = box_2d
    image = cv2.rectangle(
        image, (int(left), int(top)), (int(right), int(bottom)), color, thinkness
    )
    return image


def draw_boxes_2d(image, boxes_2d, color=(255, 255, 255), thinkness=2):
    """
    Draw multiple 2D bounding boxes on an image.

    Iterates through a list of boxes and calls `draw_box_2d` for each one.

    :param image: The input image (NumPy array).
    :type image: numpy.ndarray
    :param boxes_2d: A list or array of bounding boxes, where each box is
                     [xmin, ymin, xmax, ymax].
    :type boxes_2d: list or numpy.ndarray
    :param color: The color for all bounding boxes (B, G, R). Defaults to (255, 255, 255).
    :type color: tuple, optional
    :param thinkness: The thickness for all box lines. Defaults to 2.
    :type thinkness: int, optional
    :return: The image with all bounding boxes drawn.
    :rtype: numpy.ndarray
    """
    for box_2d in boxes_2d:
        image = draw_box_2d(image, box_2d, color=color, thinkness=thinkness)
    return image
