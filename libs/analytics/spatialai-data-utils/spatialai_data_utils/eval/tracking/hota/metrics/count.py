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

from spatialai_data_utils.eval.tracking.hota.metrics._base_metric import _BaseMetric
from spatialai_data_utils.eval.tracking.hota import _timing


class Count(_BaseMetric):
    """
    Class which simply counts the number of tracker and gt detections and ids.
    
    :param Dict config: configuration for the app
    ::

        identity = trackeval.metrics.Count(config)
    """
    def __init__(self, config=None):
        super().__init__()
        self.integer_fields = ['Dets', 'GT_Dets', 'IDs', 'GT_IDs']
        self.fields = self.integer_fields
        self.summary_fields = self.fields

    @_timing.time
    def eval_sequence(self, data):
        """
        Returns counts for one sequence
        
        :param Dict data: dictionary containing the data for the sequence
        
        :return: dictionary containing the calculated count metrics
        :rtype: Dict[str, Dict[str]]
        """
        # Get results
        res = {'Dets': data['num_tracker_dets'],
               'GT_Dets': data['num_gt_dets'],
               'IDs': data['num_tracker_ids'],
               'GT_IDs': data['num_gt_ids'],
               'Frames': data['num_timesteps']}
        return res

    def combine_sequences(self, all_res):
        """
        Combines metrics across all sequences
        
        :param Dict[str, float] all_res: dictionary containing the metrics for each sequence        
        :return: dictionary containing the combined metrics across sequences
        :rtype: Dict[str, float]
        """
        res = {}
        for field in self.integer_fields:
            res[field] = self._combine_sum(all_res, field)
        return res

    def combine_classes_class_averaged(self, all_res, ignore_empty_classes=None):
        """
        Combines metrics across all classes by averaging over the class values
        
        :param Dict[str, float] all_res: dictionary containing the ID metrics for each class
        :param bool ignore_empty_classes: Flag to ignore empty classes, defaults to False
        :return: dictionary containing the combined metrics averaged over classes
        :rtype: Dict[str, float]
        """
        res = {}
        for field in self.integer_fields:
            res[field] = self._combine_sum(all_res, field)
        return res

    def combine_classes_det_averaged(self, all_res):
        """
        Combines metrics across all classes by averaging over the detection values
        
        :param Dict[str, float] all_res: dictionary containing the metrics for each class        
        :return: dictionary containing the combined metrics averaged over detections
        :rtype: Dict[str, float]
        """
        res = {}
        for field in self.integer_fields:
            res[field] = self._combine_sum(all_res, field)
        return res