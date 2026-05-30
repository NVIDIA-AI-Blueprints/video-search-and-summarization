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
Detection and Tracking Filtering Module

This module provides filtering utilities for detection and tracking results.
It includes functions for filtering based on confidence scores and 2D bounding
box dimensions, helping to remove low-quality or invalid predictions.

Key Features:
- Filter detections/tracks by confidence threshold
- Filter 2D bounding boxes by minimum size
- Support for multiple confidence score field names
- Handle different data formats (detection, tracking, generic)

Main Functions:
- filter_dets_by_conf: Filter by confidence score threshold
- filter_by_box_2d_size: Filter by minimum 2D box dimensions

Confidence Score Fields:
The filter supports multiple field names for confidence scores:
- detection_score: For detection results
- tracking_score: For tracking results
- confidence: Generic confidence field

Use Cases:
- Remove low-confidence detections before evaluation
- Filter tiny or invalid bounding boxes
- Pre-process detections for tracking algorithms
- Quality control for detection outputs

Typical Usage:
1. Load detection or tracking results
2. Apply confidence threshold filtering
3. Apply size-based filtering
4. Use filtered results for downstream tasks
"""


def filter_dets_by_conf(results, thresh):
    """
    Filter detection or tracking results by confidence score threshold.

    This function identifies detections with confidence scores above a specified
    threshold. It supports multiple field names for confidence scores to handle
    different data formats (detection_score, tracking_score, or confidence).

    :param results: List of detection/tracking result dictionaries. Each dictionary
                    should contain one of: 'detection_score', 'tracking_score', or
                    'confidence' field. If none are present, assumes score of 1.0.
    :type results: list of dict
    :param thresh: Minimum confidence score threshold (inclusive). Results with
                   scores >= thresh are kept.
    :type thresh: float
    :return: List of indices for results that pass the confidence threshold.
    :rtype: list of int

    Example:
        >>> results = [
        ...     {'detection_score': 0.9, 'box': [10, 20, 30, 40]},
        ...     {'detection_score': 0.3, 'box': [50, 60, 70, 80]},
        ...     {'tracking_score': 0.8, 'box': [90, 100, 110, 120]}
        ... ]
        >>> keep_indices = filter_dets_by_conf(results, thresh=0.5)
        >>> keep_indices
        [0, 2]
        >>> filtered_results = [results[i] for i in keep_indices]
    """
    keep = []
    for i, box in enumerate(results):
        if "detection_score" in box:
            box_score = box["detection_score"]
        elif "tracking_score" in box:
            box_score = box["tracking_score"]
        elif "confidence" in box:
            box_score = box["confidence"]
        else:
            # raise NotImplementedError
            box_score = 1.0

        if box_score >= thresh:
            keep.append(i)

    return keep


def filter_by_box_2d_size(dets_2d, size_thresh):
    """
    Filter 2D detections by minimum bounding box dimensions.

    This function removes detections with bounding boxes smaller than the specified
    width and height thresholds. Useful for filtering out tiny or invalid boxes that
    may be false positives or artifacts.

    :param dets_2d: List of 2D detections. Each detection should be in format
                    [class_name, [xmin, ymin, xmax, ymax], confidence, ...] where
                    the bounding box is at index 1.
    :type dets_2d: list
    :param size_thresh: Tuple of (width_threshold, height_threshold) in pixels.
                        Boxes must have both width > width_threshold AND
                        height > height_threshold to be kept.
    :type size_thresh: tuple of (float, float)
    :return: List of detections that pass the size threshold.
    :rtype: list

    Example:
        >>> detections = [
        ...     ['person', [10, 20, 50, 80], 0.9],  # width=40, height=60
        ...     ['person', [100, 110, 105, 115], 0.8],  # width=5, height=5 (tiny)
        ...     ['person', [200, 210, 250, 270], 0.95]  # width=50, height=60
        ... ]
        >>> filtered = filter_by_box_2d_size(detections, size_thresh=(10, 10))
        >>> len(filtered)
        2
        >>> filtered[0][1]
        [10, 20, 50, 80]
    """
    width_thres, height_thres = size_thresh
    dets_2d_filtered = []
    for det_2d in dets_2d:
        width = det_2d[1][2] - det_2d[1][0]
        height = det_2d[1][3] - det_2d[1][1]
        if width > width_thres and height > height_thres:
            dets_2d_filtered.append(det_2d)
    return dets_2d_filtered
