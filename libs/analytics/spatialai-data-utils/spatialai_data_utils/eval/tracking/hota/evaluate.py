# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: MIT AND Apache-2.0
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
#
# Portions of this file are adapted from TrackEval:
# https://github.com/kovalp/TrackEval/tree/1.3.0
#
# MIT License
#
# Copyright (c) 2020 Jonathon Luiten
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

import time
import logging
import traceback
from multiprocessing.pool import Pool
from functools import partial
from spatialai_data_utils.eval.tracking.hota import utils
from spatialai_data_utils.eval.tracking.hota.utils import TrackEvalException
from spatialai_data_utils.eval.tracking.hota import _timing
from spatialai_data_utils.eval.tracking.hota.metrics import Count


class Evaluator:
    """
    Evaluator class for evaluating different metrics for different datasets
    
    :param dict config: configuration for the app
    ::

        evaluator = Evaluator(config)
    """

    @staticmethod
    def get_default_eval_config():
        """Returns the default config values for evaluation"""
        default_config = {
            'USE_PARALLEL': False,
            'NUM_PARALLEL_CORES': 8,
            'BREAK_ON_ERROR': True,  # Raises exception and exits with error
            'RETURN_ON_ERROR': False,  # if not BREAK_ON_ERROR, then returns from function on error
            # Off by default: opt-in by passing an absolute path in the
            # caller's output dir.  Previously defaulted to a path inside
            # the installed package (``eval/tracking/error_log.txt``),
            # which leaked an empty file into the source tree on every
            # caught exception.
            'LOG_ON_ERROR': None,

            'PRINT_RESULTS': True,
            'PRINT_ONLY_COMBINED': False,
            'PRINT_CONFIG': True,
            'TIME_PROGRESS': True,
            'DISPLAY_LESS_PROGRESS': True,

            'OUTPUT_SUMMARY': True,
            'OUTPUT_EMPTY_CLASSES': True,  # If False, summary files are not output for classes with no detections
            'OUTPUT_DETAILED': True,
            'PLOT_CURVES': True,
        }
        return default_config

    def __init__(self, config=None):
        self.config = utils.init_config(config, self.get_default_eval_config(), 'Eval')
        # Only run timing analysis if not run in parallel.
        if self.config['TIME_PROGRESS'] and not self.config['USE_PARALLEL']:
            _timing.DO_TIMING = True
            if self.config['DISPLAY_LESS_PROGRESS']:
                _timing.DISPLAY_LESS_PROGRESS = True

    @_timing.time
    def evaluate(self, dataset_list, metrics_list):
        """
        Evaluate a list of datasets with a list of metrics
        
        :param List[str] dataset_list: list of all datasets
        :param List[str] metrics_list: list of all metrics

        :return: str output_res: results of the evaluation
        :return: str output_msg: status of the evaluation

        ::

            trackeval.eval.evaluate(dataset_list, metrics_list)
        """
        config = self.config
        metrics_list = metrics_list + [Count()]  # Count metrics are always run
        metric_names = utils.validate_metrics_list(metrics_list)
        dataset_names = [dataset.get_name() for dataset in dataset_list]
        output_res = {}
        output_msg = {}

        for dataset, dataset_name in zip(dataset_list, dataset_names):
            # Get dataset info about what to evaluate
            output_res[dataset_name] = {}
            output_msg[dataset_name] = {}
            tracker_list, seq_list, class_list = dataset.get_eval_info()
            logging.info('Evaluating %i tracker(s) on %i sequence(s) for %i class(es) on %s dataset using the following '
                         'metrics: %s\n' % (len(tracker_list), len(seq_list), len(class_list), dataset_name,
                                            ', '.join(metric_names)))

            # Evaluate each tracker
            for tracker in tracker_list:
                # if not config['BREAK_ON_ERROR'] then go to next tracker without breaking
                try:
                    # Evaluate each sequence in parallel or in series.
                    # returns a nested dict (res), indexed like: res[seq][class][metric_name][sub_metric field]
                    # e.g. res[seq_0001][class][hota][DetA]
                    logging.info('Evaluating %s\n' % tracker)
                    time_start = time.time()
                    if config['USE_PARALLEL']:
                        with Pool(config['NUM_PARALLEL_CORES']) as pool:
                            _eval_sequence = partial(eval_sequence, dataset=dataset, tracker=tracker,
                                                     class_list=class_list, metrics_list=metrics_list,
                                                     metric_names=metric_names)
                            results = pool.map(_eval_sequence, seq_list)
                            res = dict(zip(seq_list, results))
                    else:
                        res = {}
                        for curr_seq in sorted(seq_list):
                            res[curr_seq] = eval_sequence(curr_seq, dataset, tracker, class_list, metrics_list,
                                                          metric_names)

                    # Combine results over all sequences and then over all classes

                    # collecting combined cls keys (cls averaged, det averaged, super classes)
                    combined_cls_keys = []
                    res['COMBINED_SEQ'] = {}
                    # combine sequences for each class
                    for c_cls in class_list:
                        res['COMBINED_SEQ'][c_cls] = {}
                        for metric, metric_name in zip(metrics_list, metric_names):
                            curr_res = {seq_key: seq_value[c_cls][metric_name] for seq_key, seq_value in res.items() if
                                        seq_key != 'COMBINED_SEQ'}
                            res['COMBINED_SEQ'][c_cls][metric_name] = metric.combine_sequences(curr_res)
                    # combine classes
                    if dataset.should_classes_combine:
                        combined_cls_keys += ['cls_comb_cls_av', 'cls_comb_det_av', 'all']
                        res['COMBINED_SEQ']['cls_comb_cls_av'] = {}
                        res['COMBINED_SEQ']['cls_comb_det_av'] = {}
                        for metric, metric_name in zip(metrics_list, metric_names):
                            cls_res = {cls_key: cls_value[metric_name] for cls_key, cls_value in
                                       res['COMBINED_SEQ'].items() if cls_key not in combined_cls_keys}
                            res['COMBINED_SEQ']['cls_comb_cls_av'][metric_name] = \
                                metric.combine_classes_class_averaged(cls_res)
                            res['COMBINED_SEQ']['cls_comb_det_av'][metric_name] = \
                                metric.combine_classes_det_averaged(cls_res)
                    # combine classes to super classes
                    if dataset.use_super_categories:
                        for cat, sub_cats in dataset.super_categories.items():
                            combined_cls_keys.append(cat)
                            res['COMBINED_SEQ'][cat] = {}
                            for metric, metric_name in zip(metrics_list, metric_names):
                                cat_res = {cls_key: cls_value[metric_name] for cls_key, cls_value in
                                           res['COMBINED_SEQ'].items() if cls_key in sub_cats}
                                res['COMBINED_SEQ'][cat][metric_name] = metric.combine_classes_det_averaged(cat_res)

                    # Print and output results in various formats
                    if config['TIME_PROGRESS']:
                        logging.info('All sequences for %s finished in %.2f seconds' % (tracker, time.time() - time_start))
                    output_fol = dataset.get_output_fol(tracker)
                    tracker_display_name = dataset.get_display_name(tracker)
                    for c_cls in res['COMBINED_SEQ'].keys():  # class_list + combined classes if calculated
                        summaries = []
                        details = []
                        num_dets = res['COMBINED_SEQ'][c_cls]['Count']['Dets']
                        if config['OUTPUT_EMPTY_CLASSES'] or num_dets > 0:
                            for metric, metric_name in zip(metrics_list, metric_names):
                                # for combined classes there is no per sequence evaluation
                                if c_cls in combined_cls_keys:
                                    table_res = {'COMBINED_SEQ': res['COMBINED_SEQ'][c_cls][metric_name]}
                                else:
                                    table_res = {seq_key: seq_value[c_cls][metric_name] for seq_key, seq_value
                                                 in res.items()}

                                if config['PRINT_RESULTS'] and config['PRINT_ONLY_COMBINED']:
                                    dont_print = dataset.should_classes_combine and c_cls not in combined_cls_keys
                                    if not dont_print:
                                        metric.print_table({'COMBINED_SEQ': table_res['COMBINED_SEQ']},
                                                           tracker_display_name, c_cls,
                                                           combined_only=True)
                                elif config['PRINT_RESULTS']:
                                    combined_seq_only = config.get('PRINT_COMBINED_SEQ_ONLY', False)
                                    metric.print_table(table_res, tracker_display_name, c_cls,
                                                       combined_only=combined_seq_only)
                                if config['OUTPUT_SUMMARY']:
                                    summaries.append(metric.summary_results(table_res))
                                if config['OUTPUT_DETAILED']:
                                    details.append(metric.detailed_results(table_res))
                                if config['PLOT_CURVES']:
                                    metric.plot_single_tracker_results(table_res, tracker_display_name, c_cls,
                                                                       output_fol)
                            if config['OUTPUT_SUMMARY']:
                                utils.write_summary_results(summaries, c_cls, output_fol)
                            if config['OUTPUT_DETAILED']:
                                utils.write_detailed_results(details, c_cls, output_fol)

                    # Output for returning from function
                    output_res[dataset_name][tracker] = res
                    output_msg[dataset_name][tracker] = 'Success'

                except Exception as err:
                    output_res[dataset_name][tracker] = None
                    if type(err) == TrackEvalException:
                        output_msg[dataset_name][tracker] = str(err)
                    else:
                        output_msg[dataset_name][tracker] = 'Unknown error occurred.'
                    logging.info('Tracker %s was unable to be evaluated.' % tracker)
                    logging.error(err)
                    traceback.print_exc()
                    if config['LOG_ON_ERROR'] is not None:
                        # ``logging.info(msg, file=f)`` does NOT redirect to a
                        # file — the ``file=`` kwarg is only meaningful for
                        # ``print``, so the previous version always wrote an
                        # empty file.  Use ``print`` to actually persist the
                        # diagnostic data to the requested log path.
                        with open(config['LOG_ON_ERROR'], 'a') as f:
                            print(dataset_name, file=f)
                            print(tracker, file=f)
                            print(traceback.format_exc(), file=f)
                            print('\n\n\n', file=f)
                    if config['BREAK_ON_ERROR']:
                        raise err
                    elif config['RETURN_ON_ERROR']:
                        return output_res, output_msg

        return output_res, output_msg


@_timing.time
def eval_sequence(seq, dataset, tracker, class_list, metrics_list, metric_names):
    """
    Function for evaluating a single sequence

    :param str seq: name of the sequence
    :param str dataset: name of the dataset
    :param str tracker: name of the tracker
    :param List[str] class_list: list of all classes to be evaluated
    :param List[str] metrics_list: list of all metrics
    :param List[str] metric_names: list of all metrics names 

    :return: Dict[str] seq_res: results of the eval sequence
    ::

        trackeval.eval.eval_sequence(seq, dataset, tracker, class_list, metrics_list, metric_names)
    """
    raw_data = dataset.get_raw_seq_data(tracker, seq)
    seq_res = {}
    for cls in class_list:
        seq_res[cls] = {}
        data = dataset.get_preprocessed_seq_data(raw_data, cls)
        for metric, met_name in zip(metrics_list, metric_names):
            seq_res[cls][met_name] = metric.eval_sequence(data)
    return seq_res