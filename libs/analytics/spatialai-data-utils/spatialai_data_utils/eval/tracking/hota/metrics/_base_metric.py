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

import numpy as np
from abc import ABC, abstractmethod
from spatialai_data_utils.eval.tracking.hota import _timing
from spatialai_data_utils.eval.tracking.hota.utils import TrackEvalException


class _BaseMetric(ABC):
    @abstractmethod
    def __init__(self):
        self.plottable = False
        self.integer_fields = []
        self.float_fields = []
        self.array_labels = []
        self.integer_array_fields = []
        self.float_array_fields = []
        self.fields = []
        self.summary_fields = []
        self.registered = False

    #####################################################################
    # Abstract functions for subclasses to implement

    @_timing.time
    @abstractmethod
    def eval_sequence(self, data):
        ...

    @abstractmethod
    def combine_sequences(self, all_res):
        ...

    @abstractmethod
    def combine_classes_class_averaged(self, all_res, ignore_empty_classes=False):
        ...

    @ abstractmethod
    def combine_classes_det_averaged(self, all_res):
        ...

    def plot_single_tracker_results(self, all_res, tracker, output_folder, cls):
        """
        Plot results of metrics, only valid for metrics with self.plottable
        
        :param Dict all_res: dictionary containing all results
        :param str tracker: The tracker to plot results for
        :param str output_folder: The output folder for saving the plots
        :param str cls: The class to plot results for

        :raises NotImplementedError: If the metric does not have self.plottable

        """
        if self.plottable:
            raise NotImplementedError('plot_results is not implemented for metric %s' % self.get_name())
        else:
            pass

    #####################################################################
    # Helper functions which are useful for all metrics:

    @classmethod
    def get_name(cls):
        return cls.__name__

    @staticmethod
    def _combine_sum(all_res, field):
        """
        Combine sequence results via sum
        
        :param Dict all_res: dictionary containing sequence results
        :param str field: The field to be combined
        :return: The sum of the combined results
        :rtype: float
        """
        return sum([all_res[k][field] for k in all_res.keys()])

    @staticmethod
    def _combine_weighted_av(all_res, field, comb_res, weight_field):
        """
        Combine sequence results via weighted average

        :param Dict all_res: dictionary containing sequence results
        :param str field: The field to be combined
        :param Dict comb_res: dictionary containing combined results
        :param str weight_field: The field representing the weight
        :return: The weighted average of the combined results
        :rtype: float
        """
        return sum([all_res[k][field] * all_res[k][weight_field] for k in all_res.keys()]) / np.maximum(1.0, comb_res[
            weight_field])

    def print_table(self, table_res, tracker, cls, combined_only=False):
        """
        Prints table of results for all sequences
        
        :param Dict table_res: dictionary containing the results for each sequence.
        :param str tracker: The name of the tracker.
        :param str cls: The name of the class.
        :param bool combined_only: If True, only print the COMBINED row (skip per-sequence rows).
        :return None
        """
        print('')
        metric_name = self.get_name()
        self._row_print([metric_name + ': ' + tracker + '-' + cls] + self.summary_fields)
        if not combined_only:
            for seq, results in sorted(table_res.items()):
                if seq == 'COMBINED_SEQ':
                    continue
                summary_res = self._summary_row(results)
                self._row_print([seq] + summary_res)
        summary_res = self._summary_row(table_res['COMBINED_SEQ'])
        self._row_print(['COMBINED'] + summary_res)

    def _summary_row(self, results_):
        """
        Generate a summary row of values based on the provided results.
        :param Dict results_: dictionary containing the metric results.

        :return: A list of formatted values for the summary row.
        :rtype: list
        :raises NotImplementedError: If the summary function is not implemented for a field type.
        """
        vals = []
        for h in self.summary_fields:
            if h in self.float_array_fields:
                vals.append("{0:1.5g}".format(100 * np.mean(results_[h])))
            elif h in self.float_fields:
                vals.append("{0:1.5g}".format(100 * float(results_[h])))
            elif h in self.integer_fields:
                vals.append("{0:d}".format(int(results_[h])))
            else:
                raise NotImplementedError("Summary function not implemented for this field type.")
        return vals

    @staticmethod
    def _row_print(*argv):
        """
        Prints results in an evenly spaced rows, with more space in first row
        
        :param argv: The values to be printed in each column of the row.
        :type argv: tuple or list
        """
        if len(argv) == 1:
            argv = argv[0]
        # Use dynamic width for first column based on content length (min 35)
        first_col_width = max(35, len(str(argv[0])) + 2)
        to_print = ('%-' + str(first_col_width) + 's') % argv[0]
        for v in argv[1:]:
            to_print += '%-10s' % str(v)
        print(to_print)

    def summary_results(self, table_res):
        """
        Returns a simple summary of final results for a tracker
        
        :param Dict table_res: The table of results containing per-sequence and combined sequence results.
        :return: dictionary representing the summary of final results.
        :rtype: Dict
        """
        return dict(zip(self.summary_fields, self._summary_row(table_res['COMBINED_SEQ'])))

    def detailed_results(self, table_res):
        """
        Returns detailed final results for a tracker
        
        :param Dict table_res: The table of results containing per-sequence and combined sequence results.
        :return: Detailed results for each sequence as a dictionary of dictionaries.
        :rtype: Dict
        :raises TrackEvalException: If the field names and data have different sizes.
        """
        # Get detailed field information
        detailed_fields = self.float_fields + self.integer_fields
        for h in self.float_array_fields + self.integer_array_fields:
            for alpha in [int(100*x) for x in self.array_labels]:
                detailed_fields.append(h + '___' + str(alpha))
            detailed_fields.append(h + '___AUC')

        # Get detailed results
        detailed_results = {}
        for seq, res in table_res.items():
            detailed_row = self._detailed_row(res)
            if len(detailed_row) != len(detailed_fields):
                raise TrackEvalException(
                    'Field names and data have different sizes (%i and %i)' % (len(detailed_row), len(detailed_fields)))
            detailed_results[seq] = dict(zip(detailed_fields, detailed_row))
        return detailed_results

    def _detailed_row(self, res):
        """
        Calculates a detailed row of results for a given set of metrics.

        :param Dict res: The results containing the metrics.
        :return: Detailed row of results.
        :rtype: list
        """
        detailed_row = []
        for h in self.float_fields + self.integer_fields:
            detailed_row.append(res[h])
        for h in self.float_array_fields + self.integer_array_fields:
            for i, alpha in enumerate([int(100 * x) for x in self.array_labels]):
                detailed_row.append(res[h][i])
            detailed_row.append(np.mean(res[h]))
        return detailed_row