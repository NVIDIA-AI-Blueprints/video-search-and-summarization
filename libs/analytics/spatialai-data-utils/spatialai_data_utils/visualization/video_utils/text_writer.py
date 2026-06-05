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
Text Label Overlay Module

This module provides utilities for adding text labels to video frames and images.
It creates professional-looking labels with background rectangles for improved
readability over varying image content.

Key Features:
- Add text labels to image frames
- Automatic background rectangle for readability
- Support for multi-line labels (newline separated)
- Configurable text size, font, and thickness
- Consistent styling for video overlays

Main Functions:
- plot_frame_label: Add labeled text overlay to an image

Label Style:
- Font: cv2.FONT_HERSHEY_DUPLEX
- Background: Semi-transparent gray rectangle
- Text color: White for maximum contrast
- Position: Top-left corner
- Multi-line support: Automatic spacing

Use Cases:
- Label camera views in multi-camera systems
- Add metadata to video frames
- Create informative visualizations
- Identify video sources
- Add timestamps or frame numbers

Typical Usage:
1. Load image frame with OpenCV
2. Call plot_frame_label with label text
3. Text rendered with background for visibility
4. Use in video generation or image saving
"""

import cv2
import numpy as np


def plot_frame_label(image_frame: np.array, frame_label: str) -> np.array:
    """
    Plots frame label on a frame image

    :param np.array image_frame: image frame
    :param str frame_label: frame label
    :return: plotted image frame
    :rtype: np.array
    ::

        image_frame = plot_frame_label(image_frame, frame_label)
    """
    text_face = cv2.FONT_HERSHEY_DUPLEX
    text_scale = 3.0
    text_thickness = 3

    frame_labels = frame_label.split("\n")
    num_frame_labels = len(frame_labels)
    frame_label_width_max = 0
    frame_label_height_max = 0

    for frame_label in frame_labels:
        frame_label_size = cv2.getTextSize(
            frame_label, text_face, text_scale, text_thickness
        )[0]
        if frame_label_size[0] > frame_label_width_max:
            frame_label_width_max = frame_label_size[0]
        if frame_label_size[1] > frame_label_height_max:
            frame_label_height_max = frame_label_size[1]

    cv2.rectangle(
        image_frame,
        (0, 0),
        (frame_label_width_max + 24, (frame_label_height_max + 44) * num_frame_labels),
        (66, 66, 66),
        -1,
    )

    for i in range(num_frame_labels):
        cv2.putText(
            image_frame,
            frame_labels[i],
            (14, (frame_label_height_max * (i + 1)) + (16 * i) + 20),
            text_face,
            text_scale,
            (255, 255, 255),
            text_thickness,
            cv2.LINE_AA,
        )

    return image_frame
