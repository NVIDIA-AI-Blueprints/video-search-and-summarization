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

import csv
import io
import os
import traceback
import zipfile
from abc import ABC, abstractmethod
from copy import deepcopy

import numpy as np

from spatialai_data_utils.eval.tracking.hota import _timing
from spatialai_data_utils.eval.tracking.hota.utils import TrackEvalException


class _BaseDataset(ABC):
    """
    Module to create a skeleton of dataset formats
    """
    @abstractmethod
    def __init__(self):
        self.tracker_list = None
        self.seq_list = None
        self.class_list = None
        self.output_fol = None
        self.output_sub_fol = None
        self.should_classes_combine = True
        self.use_super_categories = False

    @staticmethod
    @abstractmethod
    def get_default_dataset_config():
        ...

    @abstractmethod
    def _load_raw_file(self, tracker, seq, is_gt):
        ...

    @_timing.time
    @abstractmethod
    def get_preprocessed_seq_data(self, raw_data, cls):
        ...

    @abstractmethod
    def _calculate_similarities(self, gt_dets_t, tracker_dets_t):
        ...

    @classmethod
    def get_class_name(cls):
        return cls.__name__

    def get_name(self):
        return self.get_class_name()

    def get_output_fol(self, tracker):
        return os.path.join(self.output_fol, tracker, self.output_sub_fol)

    def get_display_name(self, tracker):
        """
        Can be overwritten if the trackers name (in files) is different to how it should be displayed.
        By default this method just returns the trackers name as is.

        :param tracker: name of tracker 
        :return: None
        """
        return tracker

    def get_eval_info(self):
        """Return info about the dataset needed for the Evaluator
        
        :return: List[str] tracker_list: list of all trackers
        :return: List[str] seq_list: list of all sequences
        :return: List[str] class_list: list of all classes
        """
        return self.tracker_list, self.seq_list, self.class_list

    @_timing.time
    def get_raw_seq_data(self, tracker, seq):
        """ Loads raw data (tracker and ground-truth) for a single tracker on a single sequence.
        Raw data includes all of the information needed for both preprocessing and evaluation, for all classes.
        A later function (get_processed_seq_data) will perform such preprocessing and extract relevant information for
        the evaluation of each class.

        This returns a dict which contains the fields:
        [num_timesteps]: integer
        [gt_ids, tracker_ids, gt_classes, tracker_classes, tracker_confidences]:
                                                                list (for each timestep) of 1D NDArrays (for each det).
        [gt_dets, tracker_dets, gt_crowd_ignore_regions]: list (for each timestep) of lists of detections.
        [similarity_scores]: list (for each timestep) of 2D NDArrays.
        [gt_extras]: dict (for each extra) of lists (for each timestep) of 1D NDArrays (for each det).

        gt_extras contains dataset specific information used for preprocessing such as occlusion and truncation levels.

        Note that similarities are extracted as part of the dataset and not the metric, because almost all metrics are
        independent of the exact method of calculating the similarity. However datasets are not (e.g. segmentation
        masks vs 2D boxes vs 3D boxes).
        We calculate the similarity before preprocessing because often both preprocessing and evaluation require it and
        we don't wish to calculate this twice.
        We calculate similarity between all gt and tracker classes (not just each class individually) to allow for
        calculation of metrics such as class confusion matrices. Typically the impact of this on performance is low.

        :param: str tracker: name of tracker
        :param: str sequence: name of sequence
        :return: raw_data: similarity scores among all gt & tracker classes
        """
        # Load raw data.
        raw_gt_data = self._load_raw_file(tracker, seq, is_gt=True)
        raw_tracker_data = self._load_raw_file(tracker, seq, is_gt=False)
        raw_data = {**raw_tracker_data, **raw_gt_data}  # Merges dictionaries

        # Calculate similarities for each timestep.
        similarity_scores = []
        for t, (gt_dets_t, tracker_dets_t) in enumerate(zip(raw_data['gt_dets'], raw_data['tracker_dets'])):
            ious = self._calculate_similarities(gt_dets_t, tracker_dets_t)
            similarity_scores.append(ious)
        raw_data['similarity_scores'] = similarity_scores
        return raw_data

    @staticmethod
    def _load_simple_text_file(file, time_col=0, id_col=None, remove_negative_ids=False, valid_filter=None,
                               crowd_ignore_filter=None, convert_filter=None, is_zipped=False, zip_file=None,
                               force_delimiters=None):
        """ Function that loads data which is in a commonly used text file format.
        Assumes each det is given by one row of a text file.
        There is no limit to the number or meaning of each column,
        however one column needs to give the timestep of each det (time_col) which is default col 0.

        The file dialect (deliminator, num cols, etc) is determined automatically.
        This function automatically separates dets by timestep,
        and is much faster than alternatives such as np.loadtext or pandas.

        If remove_negative_ids is True and id_col is not None, dets with negative values in id_col are excluded.
        These are not excluded from ignore data.

        valid_filter can be used to only include certain classes.
        It is a dict with ints as keys, and lists as values,
        such that a row is included if "row[key].lower() is in value" for all key/value pairs in the dict.
        If None, all classes are included.

        crowd_ignore_filter can be used to read crowd_ignore regions separately. It has the same format as valid filter.

        convert_filter can be used to convert value read to another format.
        This is used most commonly to convert classes given as string to a class id.
        This is a dict such that the key is the column to convert, and the value is another dict giving the mapping.

        Optionally, input files could be a zip of multiple text files for storage efficiency.

        Returns read_data and ignore_data.
        Each is a dict (with keys as timesteps as strings) of lists (over dets) of lists (over column values).
        Note that all data is returned as strings, and must be converted to float/int later if needed.
        Note that timesteps will not be present in the returned dict keys if there are no dets for them

        :param str file: Path to the input text file or the name of the file within the zip file (if is_zipped is True).
        :param int time_col: Index of the column containing the timestep of each detection, defaults to 0.
        :param int id_col: Index of the column containing the ID of each detection, defaults to None.
        :param bool remove_negative_ids: Whether to exclude dets with negative IDs, defaults to False.
        :param dict valid_filter: Dictionary to include only certain classes, defaults to None.
        :param dict crowd_ignore_filter: Dictionary to read crowd_ignore regions separately, defaults to None.
        :param dict convert_filter: Dictionary to convert values read to another format, defaults to None.
        :param bool is_zipped: Whether the input file is a zip file, defaults to False.
        :param str zip_file: Path to the zip file (if is_zipped is True), defaults to None.
        :param list force_delimiters: List of potential delimiters to override the automatic delimiter detection, defaults to None.
        :raises TrackEvalException: If remove_negative_ids is True but id_col is not given, or if there's an error reading the file.
        :return: A tuple containing read_data and crowd_ignore_data dictionaries.
            read_data: dictionary with timesteps as keys (strings) and lists (over detections) of lists (over column values).
            crowd_ignore_data: dictionary with timesteps as keys (strings) and lists (over detections) of lists (over column values).
        :rtype: tuple
        """

        if remove_negative_ids and id_col is None:
            raise TrackEvalException('remove_negative_ids is True, but id_col is not given.')
        if crowd_ignore_filter is None:
            crowd_ignore_filter = {}
        if convert_filter is None:
            convert_filter = {}
        fp = None
        archive = None
        try:
            if is_zipped:  # Either open file directly or within a zip.
                if zip_file is None:
                    raise TrackEvalException('is_zipped set to True, but no zip_file is given.')
                archive = zipfile.ZipFile(os.path.join(zip_file), 'r')
                fp = io.TextIOWrapper(archive.open(file, 'r'))
            else:
                fp = open(file)
            read_data = {}
            crowd_ignore_data = {}
            fp.seek(0, os.SEEK_END)
            # check if file is empty
            if fp.tell():
                fp.seek(0)
                dialect = csv.Sniffer().sniff(next(fp), delimiters=force_delimiters)  # Auto determine structure.
                dialect.skipinitialspace = True  # Deal with extra spaces between columns
                fp.seek(0)
                reader = csv.reader(fp, dialect)
                for row in reader:
                    try:
                        # Deal with extra trailing spaces at the end of rows
                        if row[-1] in '':
                            row = row[:-1]
                        timestep = str(int(float(row[time_col])))
                        # Read ignore regions separately.
                        is_ignored = False
                        for ignore_key, ignore_value in crowd_ignore_filter.items():
                            if row[ignore_key].lower() in ignore_value:
                                # Convert values in one column (e.g. string to id)
                                for convert_key, convert_value in convert_filter.items():
                                    row[convert_key] = convert_value[row[convert_key].lower()]
                                # Save data separated by timestep.
                                if timestep in crowd_ignore_data.keys():
                                    crowd_ignore_data[timestep].append(row)
                                else:
                                    crowd_ignore_data[timestep] = [row]
                                is_ignored = True
                        if is_ignored:  # if det is an ignore region, it cannot be a normal det.
                            continue
                        # Exclude some dets if not valid.
                        if valid_filter is not None:
                            for key, value in valid_filter.items():
                                if row[key].lower() not in value:
                                    continue
                        if remove_negative_ids:
                            if int(float(row[id_col])) < 0:
                                continue
                        # Convert values in one column (e.g. string to id)
                        for convert_key, convert_value in convert_filter.items():
                            row[convert_key] = convert_value[row[convert_key].lower()]
                        # Save data separated by timestep.
                        if timestep in read_data.keys():
                            read_data[timestep].append(row)
                        else:
                            read_data[timestep] = [row]
                    except Exception:
                        exc_str_init = 'In file %s the following line cannot be read correctly: \n' % os.path.basename(
                            file)
                        exc_str = ' '.join([exc_str_init]+row)
                        raise TrackEvalException(exc_str)
        except Exception:
            print('Error loading file: %s, printing traceback.' % file)
            traceback.print_exc()
            raise TrackEvalException(
                'File %s cannot be read because it is either not present or invalidly formatted' % os.path.basename(
                    file))
        finally:
            # ``try/finally`` so a parse error mid-stream doesn't leak
            # the underlying file handle (which otherwise trips
            # ResourceWarning under pytest ``-W error`` / tracemalloc).
            if fp is not None:
                fp.close()
            if archive is not None:
                archive.close()
        return read_data, crowd_ignore_data

    @staticmethod
    def _calculate_mask_ious(masks1, masks2, is_encoded=False, do_ioa=False):
        """ Calculates the IOU (intersection over union) between two arrays of segmentation masks.
        If is_encoded a run length encoding with pycocotools is assumed as input format, otherwise an input of numpy
        arrays of the shape (num_masks, height, width) is assumed and the encoding is performed.
        If do_ioa (intersection over area) , then calculates the intersection over the area of masks1 - this is commonly
        used to determine if detections are within crowd ignore region.
        :param masks1:  first set of masks (numpy array of shape (num_masks, height, width) if not encoded,
                        else pycocotools rle encoded format)
        :param masks2:  second set of masks (numpy array of shape (num_masks, height, width) if not encoded,
                        else pycocotools rle encoded format)
        :param is_encoded: whether the input is in pycocotools rle encoded format
        :param do_ioa: whether to perform IoA computation
        :return: the IoU/IoA scores
        """

        # Only loaded when run to reduce minimum requirements
        from pycocotools import mask as mask_utils

        # use pycocotools for run length encoding of masks
        if not is_encoded:
            masks1 = mask_utils.encode(np.array(np.transpose(masks1, (1, 2, 0)), order='F'))
            masks2 = mask_utils.encode(np.array(np.transpose(masks2, (1, 2, 0)), order='F'))

        # use pycocotools for iou computation of rle encoded masks
        ious = mask_utils.iou(masks1, masks2, [do_ioa]*len(masks2))
        if len(masks1) == 0 or len(masks2) == 0:
            ious = np.asarray(ious).reshape(len(masks1), len(masks2))
        assert (ious >= 0 - np.finfo('float').eps).all()
        assert (ious <= 1 + np.finfo('float').eps).all()

        return ious

    @staticmethod
    def _calculate_box_ious(bboxes1, bboxes2, box_format='xywh', do_ioa=False):
        """ Calculates the IOU (intersection over union) between two arrays of boxes.
        Allows variable box formats ('xywh' and 'x0y0x1y1').
        If do_ioa (intersection over area) , then calculates the intersection over the area of boxes1 - this is commonly
        used to determine if detections are within crowd ignore region.

        :param bboxes1: first list of bounding boxes 
        :param bboxes2: second list of bounding boxes 
        :return: ious: the IoU/IoA scores
        """
        if box_format in 'xywh':
            # layout: (x0, y0, w, h)
            bboxes1 = deepcopy(bboxes1)
            bboxes2 = deepcopy(bboxes2)

            bboxes1[:, 2] = bboxes1[:, 0] + bboxes1[:, 2]
            bboxes1[:, 3] = bboxes1[:, 1] + bboxes1[:, 3]
            bboxes2[:, 2] = bboxes2[:, 0] + bboxes2[:, 2]
            bboxes2[:, 3] = bboxes2[:, 1] + bboxes2[:, 3]
        elif box_format not in 'x0y0x1y1':
            raise (TrackEvalException('box_format %s is not implemented' % box_format))

        # layout: (x0, y0, x1, y1)
        min_ = np.minimum(bboxes1[:, np.newaxis, :], bboxes2[np.newaxis, :, :])
        max_ = np.maximum(bboxes1[:, np.newaxis, :], bboxes2[np.newaxis, :, :])
        intersection = np.maximum(min_[..., 2] - max_[..., 0], 0) * np.maximum(min_[..., 3] - max_[..., 1], 0)
        area1 = (bboxes1[..., 2] - bboxes1[..., 0]) * (bboxes1[..., 3] - bboxes1[..., 1])

        if do_ioa:
            ioas = np.zeros_like(intersection)
            valid_mask = area1 > 0 + np.finfo('float').eps
            ioas[valid_mask, :] = intersection[valid_mask, :] / area1[valid_mask][:, np.newaxis]

            return ioas
        else:
            area2 = (bboxes2[..., 2] - bboxes2[..., 0]) * (bboxes2[..., 3] - bboxes2[..., 1])
            union = area1[:, np.newaxis] + area2[np.newaxis, :] - intersection
            intersection[area1 <= 0 + np.finfo('float').eps, :] = 0
            intersection[:, area2 <= 0 + np.finfo('float').eps] = 0
            intersection[union <= 0 + np.finfo('float').eps] = 0
            union[union <= 0 + np.finfo('float').eps] = 1
            ious = intersection / union
            return ious

    @staticmethod
    def _calculate_3DBBox_ious(bboxes1, bboxes2):
        """ Calculates the IOU (intersection over union) between two arrays of boxes.
        Box format supported: x, y, z, width, length, height, yaw

        :param bboxes1: first list of 3D bounding boxes 
        :param bboxes2: second list of 3D bounding boxes 
        :return: ious: the IoU scores
        """

        def euler_angles_to_rotation_matrix(pitch, roll, yaw):
            """
            Compute rotation matrix R for 3D rotation with:
            - pitch about X
            - roll  about Y
            - yaw   about Z

            Angles are in radians.
            The final rotation is Rz(yaw) * Ry(roll) * Rx(pitch).
            """
            # Use torch trig functions
            cx, sx = np.cos(pitch), np.sin(pitch)
            cy, sy = np.cos(roll),  np.sin(roll)
            cz, sz = np.cos(yaw),   np.sin(yaw)

            # Rotation about X (pitch)
            Rx = np.array([
                [1,     0,     0],
                [0,    cx,   -sx],
                [0,    sx,    cx],
            ], dtype=np.float64)

            # Rotation about Y (roll)
            Ry = np.array([
                [ cy,   0,   sy],
                [  0,   1,    0],
                [-sy,   0,   cy],
            ], dtype=np.float64)

            # Rotation about Z (yaw)
            Rz = np.array([
                [ cz,  -sz,   0],
                [ sz,   cz,   0],
                [  0,    0,   1],
            ], dtype=np.float64)

            # Final rotation = Rz * Ry * Rx
            return Rz @ Ry @ Rx  # (3 x 3)

        def _obb_to_corners(box_params):
            """
            Convert boxes in parametric form (B, 9):
            [x, y, z, width, length, height, pitch, roll, yaw]
            to corners of shape (B, 8, 3).

            NOTE: 
            - pitch, roll, yaw must be in radians.
            - The returned corners match the ordering required by 
                PyTorch3D's box3d_overlap if you define the local corner 
                layout carefully.

            Args:
                box_params: torch.Tensor of shape (B, 9)
                            where each row is
                            (x, y, z, w, l, h, pitch, roll, yaw)

            Returns:
                corners: torch.Tensor of shape (B, 8, 3)
            """
            B = box_params.shape[0]

            # Define the local corners of a "unit" box with the correct corner ordering:
            # Let's define (width -> X), (length -> Y), (height -> Z).
            # The corners of a unit box (0,0,0) to (1,1,1) in an order matching 
            # the figure in box3d_overlap docstring might be:
            unit_corners = np.array([
                [0, 0, 0],  # (0)
                [1, 0, 0],  # (1)
                [1, 1, 0],  # (2)
                [0, 1, 0],  # (3)
                [0, 0, 1],  # (4)
                [1, 0, 1],  # (5)
                [1, 1, 1],  # (6)
                [0, 1, 1],  # (7)
            ], dtype=np.float64)  # (8, 3)

            # Prepare an output tensor for corners
            corners_out = np.zeros((B, 8, 3), dtype=np.float64)

            for i in range(B):
                x, y, z = box_params[i, 0:3]
                w, l, h = box_params[i, 3:6]
                pitch, roll, yaw = box_params[i, 6], box_params[i, 7], box_params[i, 8]

                # Create local corners for this box with size (w, l, h).
                # Since 'unit_corners' goes from (0..1), we scale to (0..(w,l,h)).
                # Then shift them so that they are centered at the origin by subtracting half.
                # i.e. local box goes from (-w/2..+w/2) etc.
                local_corners = unit_corners.copy()
                local_corners[:, 0] *= w
                local_corners[:, 1] *= l
                local_corners[:, 2] *= h

                # Shift so the center is at (0,0,0):
                local_corners[:, 0] -= w / 2.0
                local_corners[:, 1] -= l / 2.0
                local_corners[:, 2] -= h / 2.0

                # Build rotation matrix
                R = euler_angles_to_rotation_matrix(pitch, roll, yaw)  # (3,3)

                # Rotate
                local_corners = local_corners @ R.T  # (8, 3)

                # Translate to world coords
                local_corners[:, 0] += x
                local_corners[:, 1] += y
                local_corners[:, 2] += z

                corners_out[i] = local_corners

            return corners_out

        M = bboxes1.shape[0]
        N = bboxes2.shape[0]
        if M == 0 or N == 0:
            # Return an empty IoU matrix of shape (M, N) => (0, N) or (M, 0)
            return np.zeros((M, N), dtype=np.float64)

        corners1 = _obb_to_corners(bboxes1)  # (M, 8, 3)
        corners2 = _obb_to_corners(bboxes2)  # (N, 8, 3)

        # `torch` and `pytorch3d` are optional dependencies; import locally so
        # importing this module does not require them. See the package
        # docstring in `spatialai_data_utils` for install instructions.
        from spatialai_data_utils.utils.optional_dependencies import (
            import_box3d_overlap,
            import_torch,
        )

        torch = import_torch("3D IoU computation")
        box3d_overlap = import_box3d_overlap("3D IoU computation")

        corners1 = torch.from_numpy(corners1).float()
        corners2 = torch.from_numpy(corners2).float()

        intersection_vol, iou_3d = box3d_overlap(corners1, corners2)


        return iou_3d.cpu().detach().numpy()


    @staticmethod
    def _calculate_euclidean_similarity(dets1, dets2, zero_distance):
        """ Calculates the euclidean distance between two sets of detections, and then converts this into a similarity
        measure with values between 0 and 1 using the following formula: sim = max(0, 1 - dist/zero_distance).
        The default zero_distance of 2.0, corresponds to the default used in MOT15_3D, such that a 0.5 similarity
        threshold corresponds to a 1m distance threshold for TPs.

        :param dets1: first list of detections 
        :param dets2: second list of detections 
        :return: sim: the similarity score
        """
        dist = np.linalg.norm(dets1[:, np.newaxis]-dets2[np.newaxis, :], axis=2)
        sim = np.maximum(0, 1 - dist/zero_distance)
        return sim

    @staticmethod
    def _check_unique_ids(data, after_preproc=False):
        """Check the requirement that the tracker_ids and gt_ids are unique per timestep"""
        gt_ids = data['gt_ids']
        tracker_ids = data['tracker_ids']
        for t, (gt_ids_t, tracker_ids_t) in enumerate(zip(gt_ids, tracker_ids)):
            if len(tracker_ids_t) > 0:
                unique_ids, counts = np.unique(tracker_ids_t, return_counts=True)
                if np.max(counts) != 1:
                    duplicate_ids = unique_ids[counts > 1]
                    exc_str_init = 'Tracker predicts the same ID more than once in a single timestep ' \
                                   '(seq: %s, frame: %i, ids:' % (data['seq'], t+1)
                    exc_str = ' '.join([exc_str_init] + [str(d) for d in duplicate_ids]) + ')'
                    if after_preproc:
                        exc_str_init += '\n Note that this error occurred after preprocessing (but not before), ' \
                                        'so ids may not be as in file, and something seems wrong with preproc.'
                    raise TrackEvalException(exc_str)
            if len(gt_ids_t) > 0:
                unique_ids, counts = np.unique(gt_ids_t, return_counts=True)
                if np.max(counts) != 1:
                    duplicate_ids = unique_ids[counts > 1]
                    exc_str_init = 'Ground-truth has the same ID more than once in a single timestep ' \
                                   '(seq: %s, frame: %i, ids:' % (data['seq'], t+1)
                    exc_str = ' '.join([exc_str_init] + [str(d) for d in duplicate_ids]) + ')'
                    if after_preproc:
                        exc_str_init += '\n Note that this error occurred after preprocessing (but not before), ' \
                                        'so ids may not be as in file, and something seems wrong with preproc.'
                    raise TrackEvalException(exc_str)
