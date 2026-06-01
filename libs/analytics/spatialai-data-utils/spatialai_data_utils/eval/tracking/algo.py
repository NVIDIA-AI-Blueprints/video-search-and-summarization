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

"""
Adapted from nuScenes dev-kit.
Original code by Holger Caesar, Caglayan Dicle and Oscar Beijbom, 2019.

This module contains the core logic for computing tracking metrics (MOTA, MOTP, MT, ML, etc.)
based on comparing ground truth and predicted tracks. It uses the py-motmetrics library
for accumulation and calculation.
"""

import os
from typing import List, Dict, Callable, Tuple

import numpy as np
import sklearn
import tqdm
import pandas

from nuscenes.eval.tracking.constants import MOT_METRIC_MAP, TRACKING_METRICS
from nuscenes.eval.tracking.mot import MOTAccumulatorCustom
from nuscenes.eval.tracking.render import TrackingRenderer
from nuscenes.eval.tracking.utils import print_threshold_metrics, create_motmetrics

from spatialai_data_utils.eval.tracking.data_classes import (
    TrackingBox,
    TrackingMetricData,
)


class TrackingEvaluation(object):
    """
    Computes tracking metrics for a single class.

    This class encapsulates the process of evaluating tracking performance for a specific
    object class by comparing ground truth tracks to predicted tracks. It uses the
    MOTAccumulator from py-motmetrics to compute various metrics across multiple
    score thresholds.

    :param tracks_gt: Ground truth tracks structured as {scene_token: {timestamp: List[TrackingBox]}}.
    :type tracks_gt: Dict[str, Dict[int, List[TrackingBox]]]
    :param tracks_pred: Predicted tracks in the same format as `tracks_gt`.
    :type tracks_pred: Dict[str, Dict[int, List[TrackingBox]]]
    :param class_name: The name of the class being evaluated (e.g., 'car').
    :type class_name: str
    :param dist_fcn: Callable function to compute distance between boxes (e.g., `center_distance`).
    :type dist_fcn: Callable
    :param dist_th_tp: Distance threshold for considering a match as a True Positive.
    :type dist_th_tp: float
    :param min_recall: Minimum recall threshold. Thresholds below this are penalized.
    :type min_recall: float
    :param num_thresholds: Number of score thresholds to evaluate between min_recall and 1.0.
    :type num_thresholds: int
    :param metric_worst: Dictionary mapping metric names to their worst possible value, used for
                         thresholds where recall is not met.
    :type metric_worst: Dict[str, float]
    :param verbose: If True, print progress and metrics to stdout. Defaults to True.
    :type verbose: bool, optional
    :param output_dir: Directory to save optional renderings. Defaults to None.
    :type output_dir: str, optional
    :param render_classes: List of class names for which to save renderings. Defaults to None.
    :type render_classes: List[str], optional
    """

    def __init__(
        self,
        tracks_gt: Dict[str, Dict[int, List[TrackingBox]]],
        tracks_pred: Dict[str, Dict[int, List[TrackingBox]]],
        class_name: str,
        dist_fcn: Callable,
        dist_th_tp: float,
        min_recall: float,
        num_thresholds: int,
        metric_worst: Dict[str, float],
        verbose: bool = True,
        output_dir: str = None,
        render_classes: List[str] = None,
    ):
        """
        Create a TrackingEvaluation object which computes all metrics for a given class.
        :param tracks_gt: The ground-truth tracks.
        :param tracks_pred: The predicted tracks.
        :param class_name: The current class we are evaluating on.
        :param dist_fcn: The distance function used for evaluation.
        :param dist_th_tp: The distance threshold used to determine matches.
        :param min_recall: The minimum recall value below which we drop thresholds due to too much noise.
        :param num_thresholds: The number of recall thresholds from 0 to 1. Note that some of these may be dropped.
        :param metric_worst: Mapping from metric name to the fallback value assigned if a recall threshold
            is not achieved.
        :param verbose: Whether to print to stdout.
        :param output_dir: Output directory to save renders.
        :param render_classes: Classes to render to disk or None.

        Computes the metrics defined in:
        - Stiefelhagen 2008: Evaluating Multiple Object Tracking Performance: The CLEAR MOT Metrics.
          MOTA, MOTP
        - Nevatia 2008: Global Data Association for Multi-Object Tracking Using Network Flows.
          MT/PT/ML
        - Weng 2019: "A Baseline for 3D Multi-Object Tracking".
          AMOTA/AMOTP
        """
        self.tracks_gt = tracks_gt
        self.tracks_pred = tracks_pred
        self.class_name = class_name
        self.dist_fcn = dist_fcn
        self.dist_th_tp = dist_th_tp
        self.min_recall = min_recall
        self.num_thresholds = num_thresholds
        self.metric_worst = metric_worst
        self.verbose = verbose
        self.output_dir = output_dir
        self.render_classes = [] if render_classes is None else render_classes

        self.n_scenes = len(self.tracks_gt)

        # Specify threshold naming pattern. Note that no two thresholds may have the same name.
        def name_gen(_threshold):
            return "thr_%.4f" % _threshold

        self.name_gen = name_gen

        # Check that metric definitions are consistent.
        for metric_name in MOT_METRIC_MAP.values():
            assert metric_name == "" or metric_name in TRACKING_METRICS

    def accumulate(self) -> TrackingMetricData:
        """
        Compute tracking metrics across all specified recall thresholds.

        Iterates through score thresholds determined by `compute_thresholds`,
        accumulates matching statistics using `accumulate_threshold` for each,
        computes MOT metrics using py-motmetrics, and aggregates them into a
        `TrackingMetricData` object. Handles missing GT and unachieved/duplicate thresholds.

        :return: An object containing aggregated metric values across all thresholds.
        :rtype: TrackingMetricData
        """
        # Init.
        if self.verbose:
            print("Computing metrics for class %s...\n" % self.class_name)
        accumulators = []
        thresh_metrics = []
        md = TrackingMetricData()

        # Skip missing classes.
        gt_box_count = 0
        gt_track_ids = set()
        for scene_tracks_gt in self.tracks_gt.values():
            for frame_gt in scene_tracks_gt.values():
                for box in frame_gt:
                    if box.tracking_name == self.class_name:
                        gt_box_count += 1
                        gt_track_ids.add(box.tracking_id)
        if gt_box_count == 0:
            # Do not add any metric. The average metrics will then be nan.
            return md

        # Register mot metrics.
        mh = create_motmetrics()

        # Get thresholds.
        # Note: The recall values are the hypothetical recall (10%, 20%, ..).
        # The actual recall may vary as there is no way to compute it without trying all thresholds.
        thresholds, recalls = self.compute_thresholds(gt_box_count)
        md.confidence = thresholds
        md.recall_hypo = recalls
        if self.verbose:
            print("Computed thresholds\n")

        for t, threshold in enumerate(thresholds):
            # If recall threshold is not achieved, we assign the worst possible value in AMOTA and AMOTP.
            if np.isnan(threshold):
                continue

            # Do not compute the same threshold twice.
            # This becomes relevant when a user submits many boxes with the exact same score.
            if threshold in thresholds[:t]:
                continue

            # Accumulate track data.
            acc, _ = self.accumulate_threshold(threshold)
            accumulators.append(acc)

            # Compute metrics for current threshold.
            thresh_name = self.name_gen(threshold)
            thresh_summary = mh.compute(
                acc, metrics=MOT_METRIC_MAP.keys(), name=thresh_name
            )
            thresh_metrics.append(thresh_summary)

            # Print metrics to stdout.
            if self.verbose:
                print_threshold_metrics(thresh_summary.to_dict())

        # Concatenate all metrics. We only do this for more convenient access.
        if len(thresh_metrics) == 0:
            summary = []
        else:
            summary = pandas.concat(thresh_metrics)

        # Get the number of thresholds which were not achieved (i.e. nan).
        unachieved_thresholds = np.array([t for t in thresholds if np.isnan(t)])
        num_unachieved_thresholds = len(unachieved_thresholds)

        # Get the number of thresholds which were achieved (i.e. not nan).
        valid_thresholds = [t for t in thresholds if not np.isnan(t)]
        assert valid_thresholds == sorted(valid_thresholds)
        num_duplicate_thresholds = len(valid_thresholds) - len(
            np.unique(valid_thresholds)
        )

        # Sanity check.
        assert (
            num_unachieved_thresholds + num_duplicate_thresholds + len(thresh_metrics)
            == self.num_thresholds
        )

        # Figure out how many times each threshold should be repeated.
        rep_counts = [np.sum(thresholds == t) for t in np.unique(valid_thresholds)]

        # Store all traditional metrics.
        for mot_name, metric_name in MOT_METRIC_MAP.items():
            # Skip metrics which we don't output.
            if metric_name == "":
                continue

            # Retrieve and store values for current metric.
            if len(thresh_metrics) == 0:
                # Set all the worst possible value if no recall threshold is achieved.
                worst = self.metric_worst[metric_name]
                if worst == -1:
                    if metric_name == "ml":
                        worst = len(gt_track_ids)
                    elif metric_name in ["gt", "fn"]:
                        worst = gt_box_count
                    elif metric_name in ["fp", "ids", "frag"]:
                        worst = (
                            np.nan
                        )  # We can't know how these error types are distributed.
                    else:
                        raise NotImplementedError

                all_values = [worst] * TrackingMetricData.nelem
            else:
                values = summary.get(mot_name).values
                assert np.all(values[np.logical_not(np.isnan(values))] >= 0)

                # If a threshold occurred more than once, duplicate the metric values.
                assert len(rep_counts) == len(values)
                values = np.concatenate(
                    [([v] * r) for (v, r) in zip(values, rep_counts)]
                )

                # Pad values with nans for unachieved recall thresholds.
                all_values = [np.nan] * num_unachieved_thresholds
                all_values.extend(values)

            assert len(all_values) == TrackingMetricData.nelem
            md.set_metric(metric_name, all_values)

        return md

    def accumulate_threshold(
        self, threshold: float = None
    ) -> Tuple[pandas.DataFrame, List[float]]:
        """
        Accumulate MOT metrics for a single score threshold.

        If `threshold` is None, this runs in a mode to collect TP scores for determining
        recall thresholds. Otherwise, it filters predictions by the given `threshold`,
        matches predictions to ground truth frame-by-frame using the specified distance
        function and threshold (`dist_th_tp`), and updates a MOTAccumulator.

        :param threshold: The score threshold to filter predictions. If None, used to compute
                          TP scores for threshold determination. Defaults to None.
        :type threshold: float, optional
        :return: A tuple containing:
                 - acc_merged (pandas.DataFrame): The merged MOT accumulator events DataFrame.
                 - scores (List[float]): List of tracking scores for True Positive matches
                                         (only populated if `threshold` is None).
        :rtype: tuple(pandas.DataFrame, List[float])
        """
        accs = []
        scores = []  # The scores of the TPs. These are used to determine the recall thresholds initially.

        # Go through all frames and associate ground truth and tracker results.
        # Groundtruth and tracker contain lists for every single frame containing lists detections.
        for scene_id in tqdm.tqdm(
            self.tracks_gt.keys(), disable=not self.verbose, leave=False
        ):
            # Initialize accumulator and frame_id for this scene
            acc = MOTAccumulatorCustom()
            frame_id = 0  # Frame ids must be unique across all scenes

            # Retrieve GT and preds.
            scene_tracks_gt = self.tracks_gt[scene_id]
            scene_tracks_pred = self.tracks_pred[scene_id]

            # Visualize the boxes in this frame.
            if self.class_name in self.render_classes and threshold is None:
                save_path = os.path.join(
                    self.output_dir, "render", str(scene_id), self.class_name
                )
                os.makedirs(save_path, exist_ok=True)
                renderer = TrackingRenderer(save_path)
            else:
                renderer = None

            for timestamp in scene_tracks_gt.keys():
                # Select only the current class.
                frame_gt = scene_tracks_gt[timestamp]
                frame_pred = scene_tracks_pred[timestamp]
                frame_gt = [f for f in frame_gt if f.tracking_name == self.class_name]
                frame_pred = [
                    f for f in frame_pred if f.tracking_name == self.class_name
                ]

                # Threshold boxes by score. Note that the scores were previously averaged over the whole track.
                if threshold is not None:
                    frame_pred = [
                        f for f in frame_pred if f.tracking_score >= threshold
                    ]

                # Abort if there are neither GT nor pred boxes.
                gt_ids = [gg.tracking_id for gg in frame_gt]
                pred_ids = [tt.tracking_id for tt in frame_pred]
                if len(gt_ids) == 0 and len(pred_ids) == 0:
                    continue

                # Calculate distances.
                if len(frame_gt) == 0 or len(frame_pred) == 0:
                    distances = np.ones((0, 0))
                elif self.dist_fcn.__name__ == "center_distance":
                    # Vectorized center distance for speed.
                    gt_boxes = np.array([b.translation[:2] for b in frame_gt])
                    pred_boxes = np.array([b.translation[:2] for b in frame_pred])
                    distances = sklearn.metrics.pairwise.euclidean_distances(
                        gt_boxes, pred_boxes
                    )
                elif self.dist_fcn.__name__ == "iou_3d":
                    # 3D IoU-based distance (1 - IoU).
                    from spatialai_data_utils.eval.common.utils import iou_3d_matrix
                    distances = iou_3d_matrix(frame_gt, frame_pred)
                else:
                    raise ValueError(
                        "Unsupported distance function: %s" % self.dist_fcn.__name__
                    )

                # Distances that are larger than the threshold won't be associated.
                assert len(distances) == 0 or not np.all(np.isnan(distances))
                distances[distances >= self.dist_th_tp] = np.nan

                # Accumulate results.
                # Note that we cannot use timestamp as frameid as motmetrics assumes it's an integer.
                acc.update(gt_ids, pred_ids, distances, frameid=frame_id)

                # Store scores of matches, which are used to determine recall thresholds.
                if threshold is None:
                    events = acc.events.loc[frame_id]
                    matches = events[events.Type == "MATCH"]
                    match_ids = matches.HId.values
                    match_scores = [
                        tt.tracking_score
                        for tt in frame_pred
                        if tt.tracking_id in match_ids
                    ]
                    scores.extend(match_scores)
                else:
                    events = None

                # Render the boxes in this frame.
                if self.class_name in self.render_classes and threshold is None:
                    renderer.render(events, timestamp, frame_gt, frame_pred)

                # Increment the frame_id, unless there are no boxes (equivalent to what motmetrics does).
                frame_id += 1

            accs.append(acc)

        # Merge accumulators
        acc_merged = MOTAccumulatorCustom.merge_event_dataframes(accs)

        return acc_merged, scores

    def compute_thresholds(self, gt_box_count: int) -> Tuple[List[float], List[float]]:
        """
        Compute score thresholds corresponding to predefined recall values.

        Uses the True Positive scores obtained from `accumulate_threshold(threshold=None)`
        to find the score thresholds that achieve specific recall levels (linearly
        interpolated between `min_recall` and 1.0).

        :param gt_box_count: The total number of ground truth boxes for this class.
        :type gt_box_count: int
        :return: A tuple containing:
                 - thresholds (List[float]): The computed score thresholds. Unachieved recalls have NaN.
                 - recalls (List[float]): The target recall values corresponding to the thresholds.
        :rtype: tuple(List[float], List[float])
        """
        # Run accumulate to get the scores of TPs.
        _, scores = self.accumulate_threshold(threshold=None)

        # Abort if no predictions exist.
        if len(scores) == 0:
            return [np.nan] * self.num_thresholds, [np.nan] * self.num_thresholds

        # Sort scores.
        scores = np.array(scores)
        scores.sort()
        scores = scores[::-1]

        # Compute recall levels.
        tps = np.array(range(1, len(scores) + 1))
        rec = tps / gt_box_count
        assert len(scores) / gt_box_count <= 1

        # Determine thresholds.
        max_recall_achieved = np.max(rec)
        rec_interp = np.linspace(self.min_recall, 1, self.num_thresholds).round(12)
        thresholds = np.interp(rec_interp, rec, scores, right=0)

        # Set thresholds for unachieved recall values to nan to penalize AMOTA/AMOTP later.
        thresholds[rec_interp > max_recall_achieved] = np.nan

        # Cast to list.
        thresholds = list(thresholds.tolist())
        rec_interp = list(rec_interp.tolist())

        # Reverse order for more convenient presentation.
        thresholds.reverse()
        rec_interp.reverse()

        # Check that we return the correct number of thresholds.
        assert len(thresholds) == len(rec_interp) == self.num_thresholds

        return thresholds, rec_interp
