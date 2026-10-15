# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

"""Geometric helpers used by the clustering pipeline."""

from __future__ import annotations

from typing import Sequence

BBox = Sequence[float]  # (leftX, topY, rightX, bottomY)


def compute_iou(box_a: BBox, box_b: BBox) -> float:
    """Compute the intersection-over-union of two axis-aligned boxes.

    Args:
        box_a: ``(leftX, topY, rightX, bottomY)``.
        box_b: ``(leftX, topY, rightX, bottomY)``.

    Returns:
        IoU in ``[0.0, 1.0]``. Returns ``0.0`` when either box has
        non-positive area or the union is zero.
    """
    left = max(box_a[0], box_b[0])
    top = max(box_a[1], box_b[1])
    right = min(box_a[2], box_b[2])
    bottom = min(box_a[3], box_b[3])

    intersection = max(0.0, right - left) * max(0.0, bottom - top)
    area_a = max(0.0, box_a[2] - box_a[0]) * max(0.0, box_a[3] - box_a[1])
    area_b = max(0.0, box_b[2] - box_b[0]) * max(0.0, box_b[3] - box_b[1])
    union = area_a + area_b - intersection

    return intersection / union if union > 0.0 else 0.0
