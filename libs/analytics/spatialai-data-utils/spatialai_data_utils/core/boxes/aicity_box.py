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
:class:`AICityBox`: 3D bounding box extended with MTMC tracking metadata.

Subclasses :class:`nuscenes.utils.data_classes.Box` to keep compatibility
with NuScenes-style detection / tracking pipelines, and adds three
optional appearance / visibility fields used by multi-target multi-camera
(MTMC) tracking:

* ``embed`` — generic embedding vector for the object.
* ``reid_embed`` — ReID embedding for appearance-based data association.
* ``visibility_scores`` — per-camera visibility scores.

The class is named after the AICity Challenge that drove its initial
development; the implementation itself contains no AICity-specific
logic and is reusable for any 3D-box detection + tracking pipeline that
wants to attach appearance / visibility metadata. Lower-level box math
(corner derivation, IoU, etc.) lives alongside this file in
:mod:`spatialai_data_utils.core.boxes.box_3d` and
:mod:`spatialai_data_utils.core.boxes.bbox_overlaps`.
"""

import numpy as np
from typing import List, Tuple
from pyquaternion import Quaternion
from nuscenes.utils.data_classes import Box as NuScenesBox


class AICityBox(NuScenesBox):
    """
    Represents a 3D bounding box with AICity-specific extensions.

    Inherits from `nuscenes.utils.data_classes.Box` and adds fields for
    generic embeddings (`embed`), ReID embeddings (`reid_embed`), and
    visibility scores (`visibility_scores`).

    :param center: Center of the box [x, y, z].
    :type center: List[float]
    :param size: Size of the box [width, length, height].
    :type size: List[float]
    :param orientation: Orientation of the box as a Quaternion.
    :type orientation: pyquaternion.Quaternion
    :param label: Integer label, optional. Defaults to np.nan.
    :type label: int, optional
    :param score: Float confidence score, optional. Defaults to np.nan.
    :type score: float, optional
    :param velocity: Box velocity [vx, vy, vz], optional. Defaults to (nan, nan, nan).
    :type velocity: Tuple, optional
    :param name: Box name, optional. Defaults to None.
    :type name: str, optional
    :param token: Box token, optional. Defaults to None.
    :type token: str, optional
    :param embed: Generic embedding vector, optional. Defaults to None.
    :type embed: numpy.ndarray, optional
    :param reid_embed: Re-identification embedding vector, optional. Defaults to None.
    :type reid_embed: numpy.ndarray, optional
    :param visibility_scores: Array of visibility scores (e.g., per camera), optional. Defaults to None.
    :type visibility_scores: numpy.ndarray, optional
    """

    def __init__(
        self,
        center: List[float],
        size: List[float],
        orientation: Quaternion,
        label: int = np.nan,
        score: float = np.nan,
        velocity: Tuple = (np.nan, np.nan, np.nan),
        name: str = None,
        token: str = None,
        embed: np.ndarray = None,
        reid_embed: np.ndarray = None,
        visibility_scores: np.ndarray = None,
    ):
        super(AICityBox, self).__init__(
            center, size, orientation, label, score, velocity, name, token
        )
        self.embed = embed
        self.reid_embed = reid_embed
        self.visibility_scores = visibility_scores
