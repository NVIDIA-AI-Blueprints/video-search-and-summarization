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

"""Tests for the MOT dataset adapters' ``get_preprocessed_seq_data``
path (the most expensive code in the file, previously uncovered).

The companion file ``test_mot_dataset_adapters.py`` covered ``__init__``
+ ``_load_raw_file`` + ``_calculate_similarities``; this file drives
``get_preprocessed_seq_data`` directly with hand-crafted ``raw_data``
dicts so we exercise all four MOT-Challenge preproc steps:

1. class filtering (only ``class`` GTs survive after preproc),
2. zero-marked GT dropping,
3. distractor matching (tracker dets matched to a distractor GT are
   removed via the Hungarian assignment),
4. ID relabeling to dense contiguous integers, plus the unique-IDs
   sanity check ``raise`` path.

The MOT15 / no-preproc branches and the invalid-class raise are also
covered.
"""

import os

import numpy as np
import pytest

from spatialai_data_utils.eval.tracking.hota.datasets.mot_challenge_2d_box import (
    MotChallenge2DBox,
)
from spatialai_data_utils.eval.tracking.hota.datasets.mot_challenge_3d_location import (
    MotChallenge3DLocation,
)
from spatialai_data_utils.eval.tracking.hota.utils import TrackEvalException


# ---------------------------------------------------------------------------
# Adapter fixtures — minimal disk tree so ``__init__`` succeeds.
# ---------------------------------------------------------------------------


_GT_TWO_FRAMES_2D = (
    "1,1,10,20,30,40,1,1,1.0\n"
    "2,1,11,21,30,40,1,1,1.0\n"
)
_PRED_TWO_FRAMES_2D = (
    "1,1,10,20,30,40,0.9,1,1.0\n"
    "2,1,11,21,30,40,0.8,1,1.0\n"
)


def _write_text(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(content)


def _make_2d_adapter(tmp_path, benchmark="MOT17"):
    gt_path = (
        tmp_path / "gt" / f"{benchmark}-train" / "seqA" / "gt" / "gt.txt"
    )
    pred_path = (
        tmp_path / "trackers" / f"{benchmark}-train"
        / "my_tracker" / "data" / "seqA.txt"
    )
    _write_text(str(gt_path), _GT_TWO_FRAMES_2D)
    _write_text(str(pred_path), _PRED_TWO_FRAMES_2D)
    return MotChallenge2DBox({
        "GT_FOLDER": str(tmp_path / "gt"),
        "TRACKERS_FOLDER": str(tmp_path / "trackers"),
        "BENCHMARK": benchmark,
        "SPLIT_TO_EVAL": "train",
        "TRACKERS_TO_EVAL": ["my_tracker"],
        "SEQ_INFO": {"seqA": 2},
        "PRINT_CONFIG": False,
    })


def _make_3d_adapter(tmp_path, benchmark="MOT17"):
    gt_path = (
        tmp_path / "gt" / f"{benchmark}-train" / "seqA" / "gt" / "gt.txt"
    )
    pred_path = (
        tmp_path / "trackers" / f"{benchmark}-train"
        / "my_tracker" / "data" / "seqA.txt"
    )
    _write_text(str(gt_path),
                 "1,1,10,20,30,40,1,1.5,2.5\n2,1,11,21,30,40,1,1.5,2.5\n")
    _write_text(str(pred_path),
                 "1,1,10,20,30,40,0.9,1.5,2.5\n2,1,11,21,30,40,0.8,1.5,2.5\n")
    return MotChallenge3DLocation({
        "GT_FOLDER": str(tmp_path / "gt"),
        "TRACKERS_FOLDER": str(tmp_path / "trackers"),
        "BENCHMARK": benchmark,
        "SPLIT_TO_EVAL": "train",
        "TRACKERS_TO_EVAL": ["my_tracker"],
        "SEQ_INFO": {"seqA": 2},
        "PRINT_CONFIG": False,
    })


# ---------------------------------------------------------------------------
# Hand-built raw_data shapes
# ---------------------------------------------------------------------------


def _raw_data_perfect_match(class_id=1, num_timesteps=2):
    """One GT + one tracker per timestep, IoU=1 (similarity=1)."""
    return {
        "seq": "seqA",
        "num_timesteps": num_timesteps,
        "gt_ids": [np.array([10]) for _ in range(num_timesteps)],
        "gt_dets": [np.array([[10, 20, 30, 40]]) for _ in range(num_timesteps)],
        "gt_classes": [np.array([class_id]) for _ in range(num_timesteps)],
        "gt_extras": [{"zero_marked": np.array([1])} for _ in range(num_timesteps)],
        "gt_crowd_ignore_regions": [np.empty((0, 4)) for _ in range(num_timesteps)],
        "tracker_ids": [np.array([100]) for _ in range(num_timesteps)],
        "tracker_dets": [np.array([[10, 20, 30, 40]]) for _ in range(num_timesteps)],
        "tracker_classes": [np.array([1]) for _ in range(num_timesteps)],
        "tracker_confidences": [np.array([0.9]) for _ in range(num_timesteps)],
        "similarity_scores": [np.array([[1.0]]) for _ in range(num_timesteps)],
    }


# ---------------------------------------------------------------------------
# get_preprocessed_seq_data — happy path + relabeling
# ---------------------------------------------------------------------------


class TestPerfectMatch:
    def test_2d_perfect_match_relabels_ids_to_dense_integers(self, tmp_path):
        """A raw GT/tracker id of 10/100 should be remapped to 0
        after preprocessing's ID-densification step."""
        adapter = _make_2d_adapter(tmp_path)
        data = adapter.get_preprocessed_seq_data(_raw_data_perfect_match(), cls="class")
        assert data["num_gt_dets"] == 2
        assert data["num_tracker_dets"] == 2
        assert data["num_gt_ids"] == 1
        assert data["num_tracker_ids"] == 1
        # IDs are densified to start from 0.
        np.testing.assert_array_equal(data["gt_ids"][0], [0])
        np.testing.assert_array_equal(data["tracker_ids"][0], [0])

    def test_3d_perfect_match_runs_through_preprocessing(self, tmp_path):
        adapter = _make_3d_adapter(tmp_path)
        # 3D location has 2-col dets; override the GT/tracker dets shape.
        raw = _raw_data_perfect_match()
        raw["gt_dets"] = [np.array([[1.5, 2.5]]) for _ in range(2)]
        raw["tracker_dets"] = [np.array([[1.5, 2.5]]) for _ in range(2)]
        raw["gt_crowd_ignore_regions"] = [np.empty((0, 2)) for _ in range(2)]
        data = adapter.get_preprocessed_seq_data(raw, cls="class")
        assert data["num_gt_dets"] == 2
        assert data["num_tracker_dets"] == 2


# ---------------------------------------------------------------------------
# Zero-marked GT dropping  +  class-id filtering
# ---------------------------------------------------------------------------


class TestZeroMarkedAndClassFiltering:
    def test_zero_marked_gt_dets_are_dropped(self, tmp_path):
        """``zero_marked=0`` flags a GT row to be removed before
        evaluation (MOT Challenge "do not evaluate" tag)."""
        adapter = _make_2d_adapter(tmp_path)
        raw = _raw_data_perfect_match()
        # Mark every GT row as zero -> they should all be dropped.
        raw["gt_extras"] = [{"zero_marked": np.array([0])} for _ in range(2)]
        data = adapter.get_preprocessed_seq_data(raw, cls="class")
        assert data["num_gt_dets"] == 0
        assert data["num_gt_ids"] == 0

    def test_gt_with_non_class_id_is_dropped_after_preproc(self, tmp_path):
        """GT rows whose class != 'class' (id 1) are kept during the
        distractor-matching step but pruned in the final
        ``gt_to_keep_mask = (zero_marked != 0) & (gt_classes == cls_id)``
        step. The visible result: zero surviving GT rows."""
        adapter = _make_2d_adapter(tmp_path)
        raw = _raw_data_perfect_match()
        # Mark every GT as 'box' (class id 2), a valid distractor class
        # in the MOT name table so the invalid-class raise is not hit.
        raw["gt_classes"] = [np.array([2]) for _ in range(2)]
        data = adapter.get_preprocessed_seq_data(raw, cls="class")
        assert data["num_gt_dets"] == 0


# ---------------------------------------------------------------------------
# Distractor-matching tracker pruning
# ---------------------------------------------------------------------------


class TestDistractorRemoval:
    def test_tracker_matched_to_distractor_gt_is_removed(self, tmp_path):
        """A tracker det matched to a distractor-class GT (e.g. ``box``)
        via the Hungarian-on-similarity-scores step should be dropped
        from the per-timestep tracker_ids/dets list."""
        adapter = _make_2d_adapter(tmp_path)
        raw = _raw_data_perfect_match()
        # One non-distractor GT (class) and one distractor GT (box=2)
        # for timestep 0; tracker has 2 dets, the second matches the
        # distractor GT with similarity 1.0 -> should be removed.
        raw["gt_ids"] = [np.array([10, 11]), np.array([10])]
        raw["gt_dets"] = [
            np.array([[10, 20, 30, 40], [100, 200, 30, 40]]),
            np.array([[10, 20, 30, 40]]),
        ]
        raw["gt_classes"] = [np.array([1, 2]), np.array([1])]
        raw["gt_extras"] = [
            {"zero_marked": np.array([1, 1])},
            {"zero_marked": np.array([1])},
        ]
        raw["tracker_ids"] = [np.array([100, 200]), np.array([100])]
        raw["tracker_dets"] = [
            np.array([[10, 20, 30, 40], [100, 200, 30, 40]]),
            np.array([[10, 20, 30, 40]]),
        ]
        raw["tracker_classes"] = [np.array([1, 1]), np.array([1])]
        raw["tracker_confidences"] = [np.array([0.9, 0.85]), np.array([0.9])]
        raw["similarity_scores"] = [
            np.array([[1.0, 0.0], [0.0, 1.0]]),  # 1↔100 and 2↔200
            np.array([[1.0]]),
        ]
        data = adapter.get_preprocessed_seq_data(raw, cls="class")
        # Tracker 200 (matched to distractor) is gone -> only one
        # tracker id survives across the whole sequence.
        assert data["num_tracker_ids"] == 1


# ---------------------------------------------------------------------------
# Validation raises
# ---------------------------------------------------------------------------


class TestValidationRaises:
    def test_non_class_tracker_class_raises(self, tmp_path):
        """The MOT adapter only evaluates class id 1 ('class'). A
        tracker entry with class > 1 must trip the
        'non class class found' raise."""
        adapter = _make_2d_adapter(tmp_path)
        raw = _raw_data_perfect_match()
        raw["tracker_classes"] = [np.array([2]) for _ in range(2)]
        with pytest.raises(TrackEvalException, match="Non class class"):
            adapter.get_preprocessed_seq_data(raw, cls="class")

    def test_invalid_gt_class_id_raises_with_full_message(self, tmp_path):
        """A gt_classes entry outside the canonical
        ``class_name_to_class_id`` numbers must raise (catches data
        files that pre-date a class-table refresh)."""
        adapter = _make_2d_adapter(tmp_path)
        raw = _raw_data_perfect_match()
        # Class id 999 is not in the valid numbers list.
        raw["gt_classes"] = [np.array([999]) for _ in range(2)]
        with pytest.raises(TrackEvalException, match="invalid gt classes"):
            adapter.get_preprocessed_seq_data(raw, cls="class")

    def test_duplicate_gt_ids_in_one_timestep_raise(self, tmp_path):
        """``_check_unique_ids`` runs at the start of preprocessing;
        duplicate ids within a single timestep must raise."""
        adapter = _make_2d_adapter(tmp_path)
        raw = _raw_data_perfect_match()
        raw["gt_ids"] = [np.array([10, 10]), np.array([10])]
        raw["gt_dets"] = [
            np.array([[10, 20, 30, 40], [10, 20, 30, 40]]),
            np.array([[10, 20, 30, 40]]),
        ]
        raw["gt_classes"] = [np.array([1, 1]), np.array([1])]
        raw["gt_extras"] = [
            {"zero_marked": np.array([1, 1])},
            {"zero_marked": np.array([1])},
        ]
        raw["similarity_scores"] = [
            np.array([[1.0], [0.0]]),
            np.array([[1.0]]),
        ]
        with pytest.raises(TrackEvalException):
            adapter.get_preprocessed_seq_data(raw, cls="class")


# ---------------------------------------------------------------------------
# MOT15 / DO_PREPROC=False
# ---------------------------------------------------------------------------


class TestNoPreprocessing:
    def test_preprocessing_skipped_when_do_preproc_false(self, tmp_path):
        """Disabling preprocessing should keep every GT (no zero-marked
        drop, no class filter) and every tracker det (no distractor
        removal)."""
        adapter = _make_2d_adapter(tmp_path)
        adapter.do_preproc = False
        raw = _raw_data_perfect_match()
        # Even with zero_marked=1 + class!=1, all GTs survive under
        # the no-preproc branch (it only drops zero_marked=0).
        raw["gt_classes"] = [np.array([3]) for _ in range(2)]  # 'car', invalid normally
        data = adapter.get_preprocessed_seq_data(raw, cls="class")
        assert data["num_gt_dets"] == 2

    def test_mot15_benchmark_skips_distractor_preprocessing(self, tmp_path):
        """``benchmark='MOT15'`` short-circuits the distractor-matching
        step but keeps the zero-marked drop."""
        adapter = _make_2d_adapter(tmp_path, benchmark="MOT15")
        raw = _raw_data_perfect_match()
        # Even with a class!=1 GT, MOT15 keeps the row because
        # gt_to_keep_mask = (zero_marked != 0) only.
        raw["gt_classes"] = [np.array([3]) for _ in range(2)]
        data = adapter.get_preprocessed_seq_data(raw, cls="class")
        assert data["num_gt_dets"] == 2
