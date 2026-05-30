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

import argparse
import json
import os
import time
from typing import Tuple, List, Dict, Any

import numpy as np

from nuscenes.eval.common.loaders import load_prediction
from nuscenes.eval.tracking.constants import (
    AVG_METRIC_MAP,
    MOT_METRIC_MAP,
    LEGACY_METRICS,
)
from nuscenes.eval.tracking.render import (
    recall_metric_curve,
    summary_plot,
)
from nuscenes.eval.tracking.utils import print_final_metrics
from nuscenes.eval.tracking.evaluate import TrackingEval

from spatialai_data_utils.eval.common.loaders import load_gt
from spatialai_data_utils.eval.tracking.algo import TrackingEvaluation
from spatialai_data_utils.eval.tracking.data_classes import (
    TrackingMetrics,
    TrackingMetricDataList,
    TrackingConfig,
    TrackingBox,
    TrackingMetricData,
)
from spatialai_data_utils.eval.tracking.loaders import create_tracks


class AIC24TrackEval(TrackingEval):
    """
    AICity Challenge 2024 tracking evaluation class. Adapted from the nuScenes tracking
    evaluation framework.

    Handles loading of ground truth (`data_infos`) and predictions (`result_path`),
    organizes data into tracks, performs evaluation using specified `config` via
    `TrackingEvaluation`, computes summary metrics, and outputs results and optional plots
    to `output_dir`.

    Key methods:
    - `__init__`: Loads GT/predictions, creates track structures.
    - `evaluate`: Runs evaluation per class using `TrackingEvaluation` and aggregates results.
    - `render`: Renders recall-metric curves.
    - `main`: Orchestrates loading, evaluation, rendering, and saving results.
    """

    def __init__(
        self,
        data_infos: dict,
        config: TrackingConfig,
        result_path: str,
        output_dir: str,
        verbose: bool = True,
        render_classes: List[str] = None,
    ):
        """
        Initialize the AIC24TrackEval object.

        :param data_infos: Dictionary-like structure containing ground truth information per sample.
        :type data_infos: dict
        :param config: A TrackingConfig object specifying evaluation parameters.
        :type config: TrackingConfig
        :param result_path: Path to the prediction results JSON file.
        :type result_path: str
        :param output_dir: Folder path to save plots and metric results. Created if it doesn't exist.
        :type output_dir: str
        :param verbose: Whether to print status messages to stdout. Defaults to True.
        :type verbose: bool, optional
        :param render_classes: List of class names for which to save renderings. Defaults to None.
        :type render_classes: List[str], optional
        """
        self.data_infos = data_infos
        self.cfg = config
        self.result_path = result_path
        self.output_dir = output_dir
        self.verbose = verbose
        self.render_classes = render_classes

        # Check result file exists.
        assert os.path.exists(result_path), "Error: The result file does not exist!"

        # Make dirs.
        self.plot_dir = os.path.join(self.output_dir, "plots")
        if not os.path.isdir(self.output_dir):
            os.makedirs(self.output_dir)
        if not os.path.isdir(self.plot_dir):
            os.makedirs(self.plot_dir)

        # Load data.
        if verbose:
            print("Initializing tracking evaluation")
        pred_boxes, self.meta = load_prediction(
            self.result_path,
            self.cfg.max_boxes_per_sample,
            TrackingBox,
            verbose=verbose,
        )
        gt_boxes = load_gt(self.data_infos, TrackingBox, verbose=verbose)

        assert set(pred_boxes.sample_tokens) == set(gt_boxes.sample_tokens), (
            "Samples in split don't match samples in predicted tracks."
        )

        self.sample_tokens = gt_boxes.sample_tokens

        # Convert boxes to tracks format.
        self.tracks_gt = create_tracks(gt_boxes, self.data_infos, gt=True)
        self.tracks_pred = create_tracks(pred_boxes, self.data_infos, gt=False)

    def evaluate(self) -> Tuple[TrackingMetrics, TrackingMetricDataList]:
        """
        Run the tracking evaluation for all configured classes.

        Instantiates and runs `TrackingEvaluation` for each class, aggregates the results,
        computes summary metrics (AMOTA, AMOTP, best MOTA/MOTP, etc.), and returns
        the high-level and detailed metric data.

        :return: A tuple containing:
                 - `metrics` (TrackingMetrics): High-level evaluation results summary.
                 - `metric_data_list` (TrackingMetricDataList): Detailed metric data per class.
        :rtype: tuple(TrackingMetrics, TrackingMetricDataList)
        """
        start_time = time.time()
        metrics = TrackingMetrics(self.cfg)

        # -----------------------------------
        # Step 1: Accumulate metric data for all classes and distance thresholds.
        # -----------------------------------
        if self.verbose:
            print("Accumulating metric data...")
        metric_data_list = TrackingMetricDataList()

        def accumulate_class(curr_class_name):
            curr_ev = TrackingEvaluation(
                self.tracks_gt,
                self.tracks_pred,
                curr_class_name,
                self.cfg.dist_fcn_callable,
                self.cfg.dist_th_tp,
                self.cfg.min_recall,
                num_thresholds=TrackingMetricData.nelem,
                metric_worst=self.cfg.metric_worst,
                verbose=self.verbose,
                output_dir=self.output_dir,
                render_classes=self.render_classes,
            )
            curr_md = curr_ev.accumulate()
            metric_data_list.set(curr_class_name, curr_md)

        for class_name in self.cfg.class_names:
            accumulate_class(class_name)

        # -----------------------------------
        # Step 2: Aggregate metrics from the metric data.
        # -----------------------------------
        if self.verbose:
            print("Calculating metrics...")
        for class_name in self.cfg.class_names:
            # Find best MOTA to determine threshold to pick for traditional metrics.
            # If multiple thresholds have the same value, pick the one with the highest recall.
            md = metric_data_list[class_name]
            if np.all(np.isnan(md.mota)):
                best_thresh_idx = None
            else:
                best_thresh_idx = np.nanargmax(md.mota)

            # Pick best value for traditional metrics.
            if best_thresh_idx is not None:
                for metric_name in MOT_METRIC_MAP.values():
                    if metric_name == "":
                        continue
                    value = md.get_metric(metric_name)[best_thresh_idx]
                    metrics.add_label_metric(metric_name, class_name, value)

            # Compute AMOTA / AMOTP.
            for metric_name in AVG_METRIC_MAP.keys():
                values = np.array(md.get_metric(AVG_METRIC_MAP[metric_name]))
                assert len(values) == TrackingMetricData.nelem

                if np.all(np.isnan(values)):
                    # If no GT exists, set to nan.
                    value = np.nan
                else:
                    # Overwrite any nan value with the worst possible value.
                    np.all(values[np.logical_not(np.isnan(values))] >= 0)
                    values[np.isnan(values)] = self.cfg.metric_worst[metric_name]
                    value = float(np.nanmean(values))
                metrics.add_label_metric(metric_name, class_name, value)

        # Compute evaluation time.
        metrics.add_runtime(time.time() - start_time)

        return metrics, metric_data_list

    def render(self, md_list: TrackingMetricDataList) -> None:
        """
        Render recall-metric curves for the evaluation results.

        Generates a summary plot and individual plots for legacy MOT metrics
        across different recall thresholds. Saves plots to the `plot_dir`.

        :param md_list: Accumulated raw metric data per class.
        :type md_list: TrackingMetricDataList
        """
        if self.verbose:
            print("Rendering curves")

        def savepath(name):
            return os.path.join(self.plot_dir, name + ".pdf")

        # Plot a summary.
        summary_plot(self.cfg, md_list, savepath=savepath("summary"))

        # For each metric, plot all the classes in one diagram.
        for metric_name in LEGACY_METRICS:
            recall_metric_curve(
                self.cfg, md_list, metric_name, savepath=savepath("%s" % metric_name)
            )

    def main(self, render_curves: bool = True) -> Dict[str, Any]:
        """
        Execute the full tracking evaluation pipeline: evaluate, render, and save results.

        :param render_curves: Whether to render and save recall-metric curve plots. Defaults to True.
        :type render_curves: bool, optional
        :return: A dictionary containing the evaluation metrics summary and metadata.
        :rtype: Dict[str, Any]
        """
        # Run evaluation.
        metrics, metric_data_list = self.evaluate()

        # Dump the metric data, meta and metrics to disk.
        if self.verbose:
            print("Saving metrics to: %s" % self.output_dir)
        metrics_summary = metrics.serialize()
        metrics_summary["meta"] = self.meta.copy()
        with open(os.path.join(self.output_dir, "metrics_summary.json"), "w") as f:
            json.dump(metrics_summary, f, indent=2)
        with open(os.path.join(self.output_dir, "metrics_details.json"), "w") as f:
            json.dump(metric_data_list.serialize(), f, indent=2)

        # Print metrics to stdout.
        if self.verbose:
            print_final_metrics(metrics)

        # Render curves.
        if render_curves:
            self.render(metric_data_list)

        return metrics_summary
