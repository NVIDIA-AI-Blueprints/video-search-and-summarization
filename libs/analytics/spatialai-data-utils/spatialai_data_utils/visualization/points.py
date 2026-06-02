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

import numpy as np
import cv2


def visualize_vertices_3d(image, gt_dicts):
    """
    Visualize the projected 2D vertices of 3D bounding boxes on an image.

    Extracts '2d vertices of 3d bounding box' from each dictionary in `gt_dicts`,
    flattens the list of vertices, and draws a green circle for each vertex on the image.

    :param image: The input image (NumPy array).
    :type image: numpy.ndarray
    :param gt_dicts: A list of ground truth dictionaries, each expected to contain
                     the key '2d vertices of 3d bounding box' which holds an
                     array of shape (8, 2) with the projected vertex coordinates.
    :type gt_dicts: list[dict]
    :return: The image with the 3D box vertices visualized.
    :rtype: numpy.ndarray
    """
    if len(gt_dicts) == 0:
        return image

    pts_2d = []
    for gt in gt_dicts:
        pts_2d.append(gt["2d vertices of 3d bounding box"])
    pts_2d = np.array(pts_2d)
    pts_2d = np.reshape(pts_2d, (-1, 2))

    for point in pts_2d:
        image = cv2.circle(
            image,
            (int(point[0]), int(point[1])),
            radius=6,
            color=(0, 255, 0),
            thickness=3,
        )

    return image


def visualize_keypoints(image, gt_dicts, transform):
    """
    Project and visualize 3D keypoints on an image.

    Extracts '3d keypoints' from each dictionary in `gt_dicts`, projects them
    using the provided `transform` matrix (world to image), filters points behind
    the camera, performs perspective division, and draws a red star marker for
    each valid projected keypoint on the image.

    :param image: The input image (NumPy array).
    :type image: numpy.ndarray
    :param gt_dicts: A list of ground truth dictionaries, each expected to contain
                     the key '3d keypoints' which holds an array of 3D keypoint
                     coordinates (shape [N_keypoints, 3]).
    :type gt_dicts: list[dict]
    :param transform: A 4x4 NumPy array representing the full projection matrix
                      (e.g., world-to-camera combined with camera intrinsics).
    :type transform: numpy.ndarray
    :return: The image with the projected 3D keypoints visualized.
    :rtype: numpy.ndarray
    """
    if len(gt_dicts) == 0:
        return image

    pts_3d = []
    for gt in gt_dicts:
        pts_3d.append(gt["3d keypoints"])
    pts_3d = np.array(pts_3d)
    pts_3d = np.reshape(pts_3d, (-1, 3))
    pts_3d = np.concatenate([pts_3d, np.ones((pts_3d.shape[0], 1))], axis=-1)

    pts_2d = pts_3d @ transform.T
    pts_2d = pts_2d[pts_2d[:, 2] > 0]

    pts_2d[:, 2] = np.clip(pts_2d[:, 2], a_min=1e-5, a_max=1e5)
    pts_2d[:, 0] /= pts_2d[:, 2]
    pts_2d[:, 1] /= pts_2d[:, 2]

    pts_2d = pts_2d[:, :2]

    for point in pts_2d:
        image = cv2.drawMarker(
            image,
            (int(point[0]), int(point[1])),
            (0, 0, 255),
            markerType=cv2.MARKER_STAR,
            markerSize=4,
            thickness=2,
            line_type=cv2.LINE_AA,
        )

    return image
