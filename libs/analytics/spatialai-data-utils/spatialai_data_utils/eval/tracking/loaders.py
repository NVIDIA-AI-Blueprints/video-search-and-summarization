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
# Original code by Holger Caesar, Caglayan Dicle and Oscar Beijbom, 2019.
from bisect import bisect
from collections import defaultdict
from typing import List, Dict, DefaultDict

import numpy as np
from pyquaternion import Quaternion

from nuscenes.eval.common.data_classes import EvalBoxes

from spatialai_data_utils.eval.tracking.data_classes import TrackingBox
from spatialai_data_utils.datasets.scenes import get_scene_info_from_token


def interpolate_tracking_boxes(
    left_box: TrackingBox, right_box: TrackingBox, right_ratio: float
) -> TrackingBox:
    """
    Linearly interpolate box parameters between two `TrackingBox` instances.

    Interpolates translation, size, velocity, ego_translation, and score.
    Uses Slerp (spherical linear interpolation) for rotation quaternions.
    The tracking ID and name are taken from the `right_box`.

    :param left_box: The starting `TrackingBox`.
    :type left_box: TrackingBox
    :param right_box: The ending `TrackingBox`.
    :type right_box: TrackingBox
    :param right_ratio: The interpolation factor (0=left_box, 1=right_box). Note: this seems
                        inverted in the original implementation logic (uses 1-right_ratio for left weight).
    :type right_ratio: float
    :return: The interpolated `TrackingBox`.
    :rtype: TrackingBox
    """

    def interp_list(left, right, rratio):
        return tuple(
            (1.0 - rratio) * np.array(left, dtype=float)
            + rratio * np.array(right, dtype=float)
        )

    def interp_float(left, right, rratio):
        return (1.0 - rratio) * float(left) + rratio * float(right)

    # Interpolate quaternion.
    rotation = Quaternion.slerp(
        q0=Quaternion(left_box.rotation),
        q1=Quaternion(right_box.rotation),
        amount=right_ratio,
    ).elements

    # Score will remain -1 for GT.
    tracking_score = interp_float(
        left_box.tracking_score, right_box.tracking_score, right_ratio
    )

    return TrackingBox(
        sample_token=right_box.sample_token,
        translation=interp_list(
            left_box.translation, right_box.translation, right_ratio
        ),
        size=interp_list(left_box.size, right_box.size, right_ratio),
        rotation=rotation,
        velocity=interp_list(left_box.velocity, right_box.velocity, right_ratio),
        ego_translation=interp_list(
            left_box.ego_translation, right_box.ego_translation, right_ratio
        ),  # May be inaccurate.
        tracking_id=right_box.tracking_id,
        tracking_name=right_box.tracking_name,
        tracking_score=tracking_score,
    )


def interpolate_tracks(
    tracks_by_timestamp: DefaultDict[int, List[TrackingBox]],
) -> DefaultDict[int, List[TrackingBox]]:
    """
    Interpolate tracks to fill in missing timestamps for each track ID.

    Groups boxes by track ID, finds missing timestamps within the track's duration,
    and linearly interpolates boxes for those missing timestamps using
    `interpolate_tracking_boxes`.

    Note: This interpolation does not consider visibility or occlusion.

    :param tracks_by_timestamp: Dictionary mapping timestamps to lists of `TrackingBox` objects.
    :type tracks_by_timestamp: DefaultDict[int, List[TrackingBox]]
    :return: The input dictionary updated with interpolated `TrackingBox` objects for missing timestamps.
    :rtype: DefaultDict[int, List[TrackingBox]]
    """
    # Group tracks by id.
    tracks_by_id = defaultdict(list)
    track_timestamps_by_id = defaultdict(list)
    for timestamp, tracking_boxes in tracks_by_timestamp.items():
        for tracking_box in tracking_boxes:
            tracks_by_id[tracking_box.tracking_id].append(tracking_box)
            track_timestamps_by_id[tracking_box.tracking_id].append(timestamp)

    # Interpolate missing timestamps for each track.
    timestamps = tracks_by_timestamp.keys()
    interpolate_count = 0
    for timestamp in timestamps:
        for tracking_id, track in tracks_by_id.items():
            if (
                track_timestamps_by_id[tracking_id][0]
                <= timestamp
                <= track_timestamps_by_id[tracking_id][-1]
                and timestamp not in track_timestamps_by_id[tracking_id]
            ):
                # Find the closest boxes before and after this timestamp.
                right_ind = bisect(track_timestamps_by_id[tracking_id], timestamp)
                left_ind = right_ind - 1
                right_timestamp = track_timestamps_by_id[tracking_id][right_ind]
                left_timestamp = track_timestamps_by_id[tracking_id][left_ind]
                right_tracking_box = tracks_by_id[tracking_id][right_ind]
                left_tracking_box = tracks_by_id[tracking_id][left_ind]
                right_ratio = float(right_timestamp - timestamp) / (
                    right_timestamp - left_timestamp
                )

                # Interpolate.
                tracking_box = interpolate_tracking_boxes(
                    left_tracking_box, right_tracking_box, right_ratio
                )
                interpolate_count += 1
                tracks_by_timestamp[timestamp].append(tracking_box)

    return tracks_by_timestamp


def create_tracks(
    all_boxes: EvalBoxes, data_infos: dict, gt: bool
) -> Dict[str, Dict[int, List[TrackingBox]]]:
    """
    Organize `EvalBoxes` into per-scene, per-timestamp tracks and perform interpolation.

    Groups boxes by scene and timestamp. If processing predictions (`gt=False`), it
    calculates the average score for each track ID and assigns it to all boxes within that track.
    Finally, it interpolates missing timestamps within each track using `interpolate_tracks`.

    :param all_boxes: An `EvalBoxes` object containing all ground truth or predicted boxes.
    :type all_boxes: EvalBoxes
    :param data_infos: Dictionary-like structure containing sample information (scene_name, frame_idx)
                       used to initialize the track structure.
    :type data_infos: dict or list
    :param gt: Boolean flag indicating whether `all_boxes` contains ground truth (True) or predictions (False).
    :type gt: bool
    :return: A dictionary mapping scene tokens to dictionaries, which in turn map timestamps
             to lists of `TrackingBox` objects, including interpolated boxes.
    :rtype: Dict[str, Dict[int, List[TrackingBox]]]
    """
    # Tracks are stored as dict {scene_token: {timestamp: List[TrackingBox]}}.
    tracks = defaultdict(lambda: defaultdict(list))

    # Init all scenes and timestamps to guarantee completeness.
    for sample in data_infos:
        # Init all timestamps in this scene.
        scene_name = sample["scene_name"]
        frame_idx = sample["frame_idx"]
        if scene_name not in tracks:
            tracks[scene_name] = {}
        if frame_idx not in tracks[scene_name]:
            tracks[scene_name][frame_idx] = []

    # Group annotations wrt scene and timestamp.
    for sample_token in all_boxes.sample_tokens:
        scene_name, frame_idx = get_scene_info_from_token(sample_token)
        tracks[scene_name][frame_idx] = all_boxes.boxes[sample_token]

    # Replace box scores with track score (average box score). This only affects the compute_thresholds method and
    # should be done before interpolation to avoid diluting the original scores with interpolated boxes.
    if not gt:
        for scene_id, scene_tracks in tracks.items():
            # For each track_id, collect the scores.
            track_id_scores = defaultdict(list)
            for timestamp, boxes in scene_tracks.items():
                for box in boxes:
                    track_id_scores[box.tracking_id].append(box.tracking_score)

            # Compute average scores for each track.
            track_id_avg_scores = {}
            for tracking_id, scores in track_id_scores.items():
                track_id_avg_scores[tracking_id] = np.mean(scores)

            # Apply average score to each box.
            for timestamp, boxes in scene_tracks.items():
                for box in boxes:
                    box.tracking_score = track_id_avg_scores[box.tracking_id]

    # Interpolate GT and predicted tracks.
    for scene_token in tracks.keys():
        tracks[scene_token] = interpolate_tracks(tracks[scene_token])

        if not gt:
            # Make sure predictions are sorted in in time. (Always true for GT).
            tracks[scene_token] = defaultdict(
                list, sorted(tracks[scene_token].items(), key=lambda kv: kv[0])
            )

    return tracks
