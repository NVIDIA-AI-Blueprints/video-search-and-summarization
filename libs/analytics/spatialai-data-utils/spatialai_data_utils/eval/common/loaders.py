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

# Copyright 2021 Motional
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# Adapted from nuScenes dev-kit.
# Original code by Oscar Beijbom, 2019.

from typing import List, Type

import tqdm
from nuscenes.eval.common.data_classes import EvalBoxes

from spatialai_data_utils.core.geometry.rotation import euler_to_quaternion
from spatialai_data_utils.eval.detection.data_classes import DetectionBox
from spatialai_data_utils.eval.tracking.data_classes import TrackingBox


def load_gt(
    data_infos: List[dict],
    box_cls: Type,
    verbose: bool = False,
) -> EvalBoxes:
    """
    Load ground-truth boxes from a list of sample dicts.

    Each entry in ``data_infos`` is expected to contain at least the keys
    ``token``, ``gt_boxes``, ``gt_names`` and ``gt_velocity``; the
    ``instance_inds`` key is additionally required when
    ``box_cls is TrackingBox``.  Annotations whose ``gt_names`` entry is
    ``None`` are skipped.

    :param data_infos: List of sample dicts.
    :param box_cls: Concrete box class to instantiate; must be either
        :class:`DetectionBox` or :class:`TrackingBox`.
    :param verbose: If ``True``, show a ``tqdm`` progress bar and print a
        summary line at the end.
    :returns: All loaded GT boxes, keyed by ``sample_token``.
    :raises NotImplementedError: If ``box_cls`` is not a supported subclass.
    """
    if box_cls is not DetectionBox and box_cls is not TrackingBox:
        raise NotImplementedError(f"Invalid box_cls {box_cls!r}")

    all_annotations = EvalBoxes()
    for sample in tqdm.tqdm(data_infos, leave=verbose, disable=not verbose):
        sample_token = sample["token"]
        gt_boxes = sample["gt_boxes"]
        gt_names = sample["gt_names"]
        gt_velocity = sample["gt_velocity"]
        instance_inds = (
            sample["instance_inds"] if box_cls is TrackingBox else None
        )

        sample_boxes = []
        for anno_id, name in enumerate(gt_names):
            # Skip annotations whose label was filtered to ``None`` upstream.
            if name is None:
                continue

            box = gt_boxes[anno_id]
            translation = box[:3]
            size = box[3:6]
            rotation_q = euler_to_quaternion(0, 0, -box[6])
            velocity = gt_velocity[anno_id][:2]

            if box_cls is DetectionBox:
                sample_boxes.append(
                    DetectionBox(
                        sample_token=sample_token,
                        translation=translation,
                        size=size,
                        rotation=rotation_q,
                        velocity=velocity,
                        detection_name=name,
                        detection_score=-1.0,  # GT samples do not have a score.
                    )
                )
            else:  # TrackingBox - already validated above.
                sample_boxes.append(
                    TrackingBox(
                        sample_token=sample_token,
                        translation=translation,
                        size=size,
                        rotation=rotation_q,
                        velocity=velocity,
                        # ``TrackingBox.tracking_id`` is typed as ``str``
                        # (see eval.tracking.data_classes.TrackingBox).
                        # Coerce here so equality comparisons against
                        # boxes deserialised from JSON (where IDs come
                        # back as strings) succeed.
                        tracking_id=str(instance_inds[anno_id]),
                        tracking_name=name,
                        tracking_score=-1.0,  # GT samples do not have a score.
                    )
                )

        all_annotations.add_boxes(sample_token, sample_boxes)

    if verbose:
        print(
            f"Loaded ground truth annotations for "
            f"{len(all_annotations.sample_tokens)} samples."
        )

    return all_annotations
