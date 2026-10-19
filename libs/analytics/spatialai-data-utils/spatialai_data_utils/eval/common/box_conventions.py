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

"""
Box conventions shared by the ``data_infos``-based evaluators.

Ground truth comes from ``data_infos[i]["gt_boxes"]`` as
``[x, y, z, d0, d1, d2, yaw, ...]``: ``d0`` is the extent along the box x axis
and ``yaw`` rotates it counter-clockwise about +z.  This is the layout the
models train on, the layout NVSchema ``bbox3d.coordinates`` uses, and the one
every 3D IoU in this package draws (``size[0]`` along the box x axis).

Predictions come from a nuScenes-format result file whose ``size`` is
``wlh``.  mmdet3d-style models (Sparse4D's ``output_to_nusc_box``) write it as
``dims[[1, 0, 2]]``, so the two horizontal extents arrive swapped relative to
``gt_boxes``.  Their ``rotation`` carries the same counter-clockwise yaw as
``gt_boxes``.  The evaluators therefore swap the prediction extents back and
leave both yaws unchanged, which is also what
``converters.nusc_results_to_nvschema`` does.
"""

from typing import Sequence, Tuple


def nusc_size_to_box_dims(size: Sequence[float]) -> Tuple[float, float, float]:
    """Convert a nuScenes result ``size`` (``wlh``) to the ``gt_boxes`` dims order.

    :param size: ``[w, l, h]`` as written to the nuScenes result file.
    :return: ``(l, w, h)``, i.e. ``(d0, d1, d2)`` with ``d0`` along the box x axis.
    """
    return (size[1], size[0], size[2])


def nusc_boxes_to_box_dims(boxes):
    """Apply :func:`nusc_size_to_box_dims` in place to every box of an ``EvalBoxes``
    loaded from a nuScenes result file, so it shares the ``gt_boxes`` order of
    :func:`spatialai_data_utils.eval.common.loaders.load_gt`.

    :param boxes: ``EvalBoxes`` returned by ``nuscenes.eval.common.loaders.load_prediction``.
    :return: The same ``EvalBoxes``, modified in place.
    """
    for sample_token in boxes.sample_tokens:
        for box in boxes[sample_token]:
            box.size = nusc_size_to_box_dims(box.size)
    return boxes
