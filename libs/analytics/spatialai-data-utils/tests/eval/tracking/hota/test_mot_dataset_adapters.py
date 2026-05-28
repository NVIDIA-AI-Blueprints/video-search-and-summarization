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

"""Tests for the MOT-Challenge dataset adapters
(:class:`MotChallenge2DBox`, :class:`MotChallenge3DLocation`).

Both adapters are TrackEval-style: at ``__init__`` time they validate
a fixture tree under ``GT_FOLDER/BENCHMARK-SPLIT/<seq>/gt/gt.txt`` and
``TRACKERS_FOLDER/BENCHMARK-SPLIT/<tracker>/data/<seq>.txt`` and
build the per-sequence ``seq_lengths`` table. Tests use
``SEQ_INFO`` to bypass the seqmap/ini lookup and
``TRACKERS_TO_EVAL`` to bypass the trackers-folder listdir, so a
tiny tmp_path tree is enough to exercise the loaders end-to-end.
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
# Fixture helpers — build the MOT-Challenge directory layout under tmp_path
# ---------------------------------------------------------------------------


def _write_text(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(content)


def _build_fixture_tree(tmp_path, *, gt_rows, pred_rows, seq="seqA",
                        benchmark="MOT17", split="train", tracker="my_tracker"):
    """Lay out a minimal MOT-Challenge-style tree and return a config
    dict suitable for instantiating either adapter."""
    gt_root = tmp_path / "gt"
    trk_root = tmp_path / "trackers"
    split_fol = f"{benchmark}-{split}"
    gt_file = gt_root / split_fol / seq / "gt" / "gt.txt"
    pred_file = trk_root / split_fol / tracker / "data" / f"{seq}.txt"
    _write_text(str(gt_file), gt_rows)
    _write_text(str(pred_file), pred_rows)
    return {
        "GT_FOLDER": str(gt_root),
        "TRACKERS_FOLDER": str(trk_root),
        "BENCHMARK": benchmark,
        "SPLIT_TO_EVAL": split,
        "TRACKERS_TO_EVAL": [tracker],
        "SEQ_INFO": {seq: 2},  # 2 timesteps; bypasses seqmap/ini lookup
        "PRINT_CONFIG": False,
    }


# Minimal valid MOT rows: frame, id, x, y, w, h, zero_marked/conf, class, vis
# Two frames, one track ('id=1'), class 1 ("class") with non-zero zero_marked.
_GT_TWO_FRAMES = (
    "1,1,10,20,30,40,1,1,1.0\n"
    "2,1,11,21,30,40,1,1,1.0\n"
)
_PRED_TWO_FRAMES = (
    "1,1,10,20,30,40,0.9,1,1.0\n"
    "2,1,11,21,30,40,0.8,1,1.0\n"
)


# ---------------------------------------------------------------------------
# MotChallenge2DBox
# ---------------------------------------------------------------------------


class TestMotChallenge2DBox:
    def test_init_with_seq_info_succeeds_and_records_lengths(self, tmp_path):
        cfg = _build_fixture_tree(
            tmp_path, gt_rows=_GT_TWO_FRAMES, pred_rows=_PRED_TWO_FRAMES,
        )
        ds = MotChallenge2DBox(cfg)
        assert ds.seq_list == ["seqA"]
        assert ds.seq_lengths == {"seqA": 2}
        assert ds.tracker_list == ["my_tracker"]
        assert ds.class_list == ["class"]
        # get_display_name defaults to the tracker name itself.
        assert ds.get_display_name("my_tracker") == "my_tracker"
        # get_eval_info returns the three lists.
        trackers, seqs, classes = ds.get_eval_info()
        assert trackers == ["my_tracker"] and seqs == ["seqA"] and classes == ["class"]

    def test_init_raises_when_no_sequences_selected(self, tmp_path):
        cfg = _build_fixture_tree(
            tmp_path, gt_rows=_GT_TWO_FRAMES, pred_rows=_PRED_TWO_FRAMES,
        )
        cfg["SEQ_INFO"] = {}  # empty -> seqmap branch -> missing seqmap file
        with pytest.raises(TrackEvalException, match="no seqmap found"):
            MotChallenge2DBox(cfg)

    def test_init_raises_on_invalid_class(self, tmp_path):
        cfg = _build_fixture_tree(
            tmp_path, gt_rows=_GT_TWO_FRAMES, pred_rows=_PRED_TWO_FRAMES,
        )
        cfg["CLASSES_TO_EVAL"] = ["bogus"]
        with pytest.raises(TrackEvalException, match="invalid class"):
            MotChallenge2DBox(cfg)

    def test_init_raises_when_gt_file_missing(self, tmp_path):
        cfg = _build_fixture_tree(
            tmp_path, gt_rows=_GT_TWO_FRAMES, pred_rows=_PRED_TWO_FRAMES,
        )
        # Remove the GT file after the tree was laid down.
        os.remove(os.path.join(
            cfg["GT_FOLDER"], f"{cfg['BENCHMARK']}-{cfg['SPLIT_TO_EVAL']}",
            "seqA", "gt", "gt.txt",
        ))
        with pytest.raises(TrackEvalException, match="GT file not found"):
            MotChallenge2DBox(cfg)

    def test_init_raises_when_tracker_file_missing(self, tmp_path):
        cfg = _build_fixture_tree(
            tmp_path, gt_rows=_GT_TWO_FRAMES, pred_rows=_PRED_TWO_FRAMES,
        )
        os.remove(os.path.join(
            cfg["TRACKERS_FOLDER"], f"{cfg['BENCHMARK']}-{cfg['SPLIT_TO_EVAL']}",
            "my_tracker", "data", "seqA.txt",
        ))
        with pytest.raises(TrackEvalException, match="Tracker file not found"):
            MotChallenge2DBox(cfg)

    def test_init_raises_on_tracker_display_name_length_mismatch(self, tmp_path):
        cfg = _build_fixture_tree(
            tmp_path, gt_rows=_GT_TWO_FRAMES, pred_rows=_PRED_TWO_FRAMES,
        )
        cfg["TRACKER_DISPLAY_NAMES"] = ["a", "b"]  # mismatch (only one tracker)
        with pytest.raises(TrackEvalException, match="tracker display names do not match"):
            MotChallenge2DBox(cfg)

    def test_load_raw_file_gt_parses_two_timesteps(self, tmp_path):
        cfg = _build_fixture_tree(
            tmp_path, gt_rows=_GT_TWO_FRAMES, pred_rows=_PRED_TWO_FRAMES,
        )
        ds = MotChallenge2DBox(cfg)
        raw = ds._load_raw_file(tracker="my_tracker", seq="seqA", is_gt=True)
        assert raw["num_timesteps"] == 2
        assert raw["seq"] == "seqA"
        # Frame 1 has one detection with id 1
        np.testing.assert_array_equal(raw["gt_ids"][0], [1])
        np.testing.assert_array_equal(raw["gt_classes"][0], [1])
        np.testing.assert_allclose(raw["gt_dets"][0], [[10, 20, 30, 40]])
        # Frame 2 same.
        np.testing.assert_array_equal(raw["gt_ids"][1], [1])

    def test_load_raw_file_tracker_parses_confidences(self, tmp_path):
        cfg = _build_fixture_tree(
            tmp_path, gt_rows=_GT_TWO_FRAMES, pred_rows=_PRED_TWO_FRAMES,
        )
        ds = MotChallenge2DBox(cfg)
        raw = ds._load_raw_file(tracker="my_tracker", seq="seqA", is_gt=False)
        np.testing.assert_array_equal(raw["tracker_ids"][0], [1])
        np.testing.assert_allclose(raw["tracker_confidences"][0], [0.9])
        np.testing.assert_allclose(raw["tracker_confidences"][1], [0.8])

    def test_load_raw_file_missing_timestep_yields_empty_detections(self, tmp_path):
        """If a frame is absent from the file, the parser must emit
        empty arrays of the correct dtype/shape — not None."""
        gt_rows = "1,1,10,20,30,40,1,1,1.0\n"  # only frame 1
        pred_rows = "1,1,10,20,30,40,0.9,1,1.0\n"
        cfg = _build_fixture_tree(tmp_path, gt_rows=gt_rows, pred_rows=pred_rows)
        ds = MotChallenge2DBox(cfg)
        raw = ds._load_raw_file(tracker="my_tracker", seq="seqA", is_gt=True)
        assert raw["gt_dets"][1].shape == (0, 4)
        assert raw["gt_ids"][1].shape == (0,)

    def test_load_raw_file_rejects_invalid_timesteps(self, tmp_path):
        """Frame ids beyond ``num_timesteps`` are rejected as
        corrupted (the file pinned ``seq_lengths`` to 2; row 5 is out
        of range)."""
        cfg = _build_fixture_tree(
            tmp_path,
            gt_rows="1,1,0,0,1,1,1,1,1\n5,1,0,0,1,1,1,1,1\n",
            pred_rows=_PRED_TWO_FRAMES,
        )
        ds = MotChallenge2DBox(cfg)
        with pytest.raises(TrackEvalException, match="invalid timesteps"):
            ds._load_raw_file(tracker="my_tracker", seq="seqA", is_gt=True)

    def test_calculate_similarities_returns_iou_in_unit_range(self, tmp_path):
        cfg = _build_fixture_tree(
            tmp_path, gt_rows=_GT_TWO_FRAMES, pred_rows=_PRED_TWO_FRAMES,
        )
        ds = MotChallenge2DBox(cfg)
        gt = np.array([[0, 0, 10, 10]], dtype=float)
        pred = np.array([[0, 0, 10, 10], [50, 50, 10, 10]], dtype=float)
        sim = ds._calculate_similarities(gt, pred)
        assert sim.shape == (1, 2)
        assert sim[0, 0] == pytest.approx(1.0)  # identical -> IoU 1
        assert sim[0, 1] == pytest.approx(0.0)  # disjoint -> IoU 0

    def test_output_fol_defaults_to_tracker_fol_when_not_set(self, tmp_path):
        cfg = _build_fixture_tree(
            tmp_path, gt_rows=_GT_TWO_FRAMES, pred_rows=_PRED_TWO_FRAMES,
        )
        ds = MotChallenge2DBox(cfg)
        assert ds.output_fol == ds.tracker_fol


# ---------------------------------------------------------------------------
# MotChallenge3DLocation
# ---------------------------------------------------------------------------


# 3D-location MOT format: same first 6 columns, plus columns 7,8 = (x, y)
# in BEV. Two-frame fixture with one track at constant world position.
_GT_3D_TWO_FRAMES = (
    "1,1,10,20,30,40,1,1.5,2.5\n"
    "2,1,11,21,30,40,1,1.5,2.5\n"
)
_PRED_3D_TWO_FRAMES = (
    "1,1,10,20,30,40,0.9,1.5,2.5\n"
    "2,1,11,21,30,40,0.8,1.5,2.5\n"
)


class TestMotChallenge3DLocation:
    def test_init_with_seq_info_succeeds(self, tmp_path):
        cfg = _build_fixture_tree(
            tmp_path, gt_rows=_GT_3D_TWO_FRAMES, pred_rows=_PRED_3D_TWO_FRAMES,
        )
        ds = MotChallenge3DLocation(cfg)
        assert ds.seq_list == ["seqA"]
        assert ds.seq_lengths == {"seqA": 2}

    def test_load_raw_file_gt_parses_3d_location(self, tmp_path):
        cfg = _build_fixture_tree(
            tmp_path, gt_rows=_GT_3D_TWO_FRAMES, pred_rows=_PRED_3D_TWO_FRAMES,
        )
        ds = MotChallenge3DLocation(cfg)
        raw = ds._load_raw_file(tracker="my_tracker", seq="seqA", is_gt=True)
        # 3D location reads columns 7:9 as the detection — x, y.
        np.testing.assert_allclose(raw["gt_dets"][0], [[1.5, 2.5]])
        np.testing.assert_array_equal(raw["gt_ids"][0], [1])

    def test_load_raw_file_tracker_parses_confidence(self, tmp_path):
        cfg = _build_fixture_tree(
            tmp_path, gt_rows=_GT_3D_TWO_FRAMES, pred_rows=_PRED_3D_TWO_FRAMES,
        )
        ds = MotChallenge3DLocation(cfg)
        raw = ds._load_raw_file(tracker="my_tracker", seq="seqA", is_gt=False)
        np.testing.assert_allclose(raw["tracker_confidences"][0], [0.9])
        np.testing.assert_allclose(raw["tracker_confidences"][1], [0.8])

    def test_load_raw_file_missing_timestep_yields_two_col_empty_dets(self, tmp_path):
        """For 3D-location the empty-frame placeholder is a (0,2)
        array (x,y), not (0,4) like the 2D-box variant."""
        cfg = _build_fixture_tree(
            tmp_path,
            gt_rows="1,1,10,20,30,40,1,1.5,2.5\n",
            pred_rows=_PRED_3D_TWO_FRAMES,
        )
        ds = MotChallenge3DLocation(cfg)
        raw = ds._load_raw_file(tracker="my_tracker", seq="seqA", is_gt=True)
        assert raw["gt_dets"][1].shape == (0, 2)

    def test_init_raises_when_gt_file_missing(self, tmp_path):
        cfg = _build_fixture_tree(
            tmp_path, gt_rows=_GT_3D_TWO_FRAMES, pred_rows=_PRED_3D_TWO_FRAMES,
        )
        os.remove(os.path.join(
            cfg["GT_FOLDER"], f"{cfg['BENCHMARK']}-{cfg['SPLIT_TO_EVAL']}",
            "seqA", "gt", "gt.txt",
        ))
        with pytest.raises(TrackEvalException, match="GT file not found"):
            MotChallenge3DLocation(cfg)

    def test_init_raises_on_invalid_class(self, tmp_path):
        cfg = _build_fixture_tree(
            tmp_path, gt_rows=_GT_3D_TWO_FRAMES, pred_rows=_PRED_3D_TWO_FRAMES,
        )
        cfg["CLASSES_TO_EVAL"] = ["bogus"]
        with pytest.raises(TrackEvalException, match="invalid class"):
            MotChallenge3DLocation(cfg)

    def test_calculate_similarities_returns_euclidean_within_unit_range(self, tmp_path):
        cfg = _build_fixture_tree(
            tmp_path, gt_rows=_GT_3D_TWO_FRAMES, pred_rows=_PRED_3D_TWO_FRAMES,
        )
        ds = MotChallenge3DLocation(cfg)
        gt = np.array([[0.0, 0.0]])
        pred = np.array([[0.0, 0.0], [100.0, 100.0]])
        sim = ds._calculate_similarities(gt, pred)
        assert sim.shape == (1, 2)
        # Identical positions => similarity 1
        assert sim[0, 0] == pytest.approx(1.0)
        # Far apart -> similarity collapses toward 0
        assert sim[0, 1] < sim[0, 0]


# ===========================================================
# Coverage supplement (merged from test_dataset_adapters_coverage.py)
# ===========================================================

"""Coverage supplement for the four TrackEval dataset adapters
(``MotChallenge2DBox``, ``MotChallenge3DLocation``, ``MTMCChallenge3DBBox``,
``MTMCChallenge3DLocation``) — pins the ``__init__`` config-fan-out branches,
``_get_seq_info`` SEQMAP_FILE / SEQMAP_FOLDER / default-seqmaps paths,
``_load_raw_file`` invalid-data branches, and ``_calculate_similarities``
empty-input branches that the existing happy-path tests don't reach."""

import os

import numpy as np
import pytest

from spatialai_data_utils.eval.tracking.hota.datasets.mot_challenge_2d_box import (
    MotChallenge2DBox,
)
from spatialai_data_utils.eval.tracking.hota.datasets.mot_challenge_3d_location import (
    MotChallenge3DLocation,
)
from spatialai_data_utils.eval.tracking.hota.datasets.mtmc_challenge_3d_bbox import (
    MTMCChallenge3DBBox,
)
from spatialai_data_utils.eval.tracking.hota.datasets.mtmc_challenge_3d_location import (
    MTMCChallenge3DLocation,
)
from spatialai_data_utils.eval.tracking.hota.utils import TrackEvalException


# ---------------------------------------------------------------------------
# Common fixture helpers
# ---------------------------------------------------------------------------


# Note: ``_write_text`` is defined at the top of this module and
# reused below.


def _make_seq_info_config(*, gt_root, trk_root, tracker, seq, benchmark, split):
    return {
        "GT_FOLDER": str(gt_root),
        "TRACKERS_FOLDER": str(trk_root),
        "BENCHMARK": benchmark,
        "SPLIT_TO_EVAL": split,
        "TRACKERS_TO_EVAL": [tracker],
        "SEQ_INFO": {seq: 2},
        "PRINT_CONFIG": False,
    }


def _build_minimal_tree(tmp_path, *, gt_rows, pred_rows, seq="seqA",
                         benchmark="MOT17", split="train", tracker="trkA"):
    gt_root = tmp_path / "gt"
    trk_root = tmp_path / "trackers"
    split_fol = f"{benchmark}-{split}"
    _write_text(str(gt_root / split_fol / seq / "gt" / "gt.txt"), gt_rows)
    _write_text(str(trk_root / split_fol / tracker / "data" / f"{seq}.txt"), pred_rows)
    return _make_seq_info_config(
        gt_root=gt_root, trk_root=trk_root, tracker=tracker, seq=seq,
        benchmark=benchmark, split=split,
    )


# 2D MOT format: frame, id, x, y, w, h, zero_marked, class, visibility
_GT_2D = (
    "1,1,10,20,30,40,1,1,1.0\n"
    "2,1,11,21,30,40,1,1,1.0\n"
)
_PRED_2D = (
    "1,1,10,20,30,40,0.9,1,1.0\n"
    "2,1,11,21,30,40,0.8,1,1.0\n"
)

# 3D MOT location: 9 cols (frame, id, x, y, w, h, conf, x_world, y_world)
# — adapter reads cols 7:9 as the (x,y) detection and col 6 as
# confidence; cols 2-5 are MOT-bbox-format leftovers, ignored.
_GT_3D_LOC = (
    "1,1,10,20,30,40,1,1.5,2.5\n"
    "2,1,11,21,30,40,1,1.5,2.5\n"
)
_PRED_3D_LOC = (
    "1,1,10,20,30,40,0.9,1.5,2.5\n"
    "2,1,11,21,30,40,0.8,1.5,2.5\n"
)

# 3D MTMC bbox (12 cols): scene_id, id, frame, x, y, z, w, l, h, pitch, roll, yaw
_GT_MTMC_BBOX = (
    "1,1,1,1.0,2.0,0.5,1.0,1.0,1.8,0.0,0.0,0.0\n"
    "1,1,2,1.1,2.1,0.5,1.0,1.0,1.8,0.0,0.0,0.0\n"
)
_PRED_MTMC_BBOX = _GT_MTMC_BBOX


# ---------------------------------------------------------------------------
# _get_seq_info — SEQMAP_FILE branch
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("adapter_cls,gt_rows,pred_rows", [
    (MotChallenge2DBox, _GT_2D, _PRED_2D),
    (MotChallenge3DLocation, _GT_3D_LOC, _PRED_3D_LOC),
    (MTMCChallenge3DBBox, _GT_MTMC_BBOX, _PRED_MTMC_BBOX),
    (MTMCChallenge3DLocation, _GT_MTMC_BBOX, _PRED_MTMC_BBOX),
])
class TestSeqmapBranches:
    """``_get_seq_info`` has three sub-branches when ``SEQ_INFO`` is
    empty: explicit ``SEQMAP_FILE``, ``SEQMAP_FOLDER``-derived path,
    and the default ``<GT_FOLDER>/seqmaps/<benchmark>-<split>.txt``.
    All three end in the same per-sequence ini-file loop."""

    def _build_seqmap_fixture(self, tmp_path, gt_rows, pred_rows,
                                *, in_default_seqmaps=False,
                                seqmap_folder=False):
        """Layout: GT/<benchmark>-<split>/<seq>/{gt/gt.txt, seqinfo.ini}
        plus a seqmap file at one of three possible locations."""
        benchmark = "MOT17"
        split = "train"
        seq = "seqA"
        split_fol = f"{benchmark}-{split}"
        gt_root = tmp_path / "gt"
        trk_root = tmp_path / "trackers"

        _write_text(
            str(gt_root / split_fol / seq / "gt" / "gt.txt"), gt_rows,
        )
        _write_text(
            str(gt_root / split_fol / seq / "seqinfo.ini"),
            "[Sequence]\nseqLength = 2\n",
        )
        _write_text(
            str(trk_root / split_fol / "trkA" / "data" / f"{seq}.txt"), pred_rows,
        )

        seqmap_content = "name\n" + seq + "\n"
        if in_default_seqmaps:
            _write_text(
                str(gt_root / "seqmaps" / f"{split_fol}.txt"), seqmap_content,
            )
            return gt_root, trk_root, seq, benchmark, split, {}
        elif seqmap_folder:
            seqmap_dir = tmp_path / "seqmaps_dir"
            _write_text(
                str(seqmap_dir / f"{split_fol}.txt"), seqmap_content,
            )
            return gt_root, trk_root, seq, benchmark, split, {
                "SEQMAP_FOLDER": str(seqmap_dir),
            }
        else:  # explicit SEQMAP_FILE
            seqmap = tmp_path / "my_seqmap.txt"
            _write_text(str(seqmap), seqmap_content)
            return gt_root, trk_root, seq, benchmark, split, {
                "SEQMAP_FILE": str(seqmap),
            }

    def test_explicit_seqmap_file(self, tmp_path, adapter_cls, gt_rows, pred_rows):
        gt_root, trk_root, seq, b, s, extra = self._build_seqmap_fixture(
            tmp_path, gt_rows, pred_rows,
        )
        cfg = {
            "GT_FOLDER": str(gt_root),
            "TRACKERS_FOLDER": str(trk_root),
            "BENCHMARK": b, "SPLIT_TO_EVAL": s,
            "TRACKERS_TO_EVAL": ["trkA"],
            "SEQ_INFO": None,
            "PRINT_CONFIG": False,
            **extra,
        }
        ds = adapter_cls(cfg)
        assert ds.seq_list == [seq]
        assert ds.seq_lengths[seq] == 2

    def test_seqmap_folder(self, tmp_path, adapter_cls, gt_rows, pred_rows):
        gt_root, trk_root, seq, b, s, extra = self._build_seqmap_fixture(
            tmp_path, gt_rows, pred_rows, seqmap_folder=True,
        )
        cfg = {
            "GT_FOLDER": str(gt_root),
            "TRACKERS_FOLDER": str(trk_root),
            "BENCHMARK": b, "SPLIT_TO_EVAL": s,
            "TRACKERS_TO_EVAL": ["trkA"], "SEQ_INFO": None,
            "PRINT_CONFIG": False, **extra,
        }
        ds = adapter_cls(cfg)
        assert ds.seq_list == [seq]

    def test_default_seqmaps_dir(self, tmp_path, adapter_cls, gt_rows, pred_rows):
        gt_root, trk_root, seq, b, s, extra = self._build_seqmap_fixture(
            tmp_path, gt_rows, pred_rows, in_default_seqmaps=True,
        )
        cfg = {
            "GT_FOLDER": str(gt_root),
            "TRACKERS_FOLDER": str(trk_root),
            "BENCHMARK": b, "SPLIT_TO_EVAL": s,
            "TRACKERS_TO_EVAL": ["trkA"], "SEQ_INFO": None,
            "PRINT_CONFIG": False, **extra,
        }
        ds = adapter_cls(cfg)
        assert ds.seq_list == [seq]

    def test_seqmap_file_missing_raises(self, tmp_path, adapter_cls, gt_rows, pred_rows):
        cfg = {
            "GT_FOLDER": str(tmp_path / "gt"),
            "TRACKERS_FOLDER": str(tmp_path / "trackers"),
            "BENCHMARK": "MOT17", "SPLIT_TO_EVAL": "train",
            "TRACKERS_TO_EVAL": ["trkA"], "SEQ_INFO": None,
            "PRINT_CONFIG": False,
        }
        with pytest.raises(TrackEvalException, match="no seqmap"):
            adapter_cls(cfg)


# ---------------------------------------------------------------------------
# _get_seq_info — SEQ_INFO with None length reads seqinfo.ini
# ---------------------------------------------------------------------------


def test_seq_info_with_none_length_reads_seqinfo_ini(tmp_path):
    """When SEQ_INFO[seq] is None, the adapter reads
    ``<gt_fol>/<seq>/seqinfo.ini`` and parses ``[Sequence]/seqLength``."""
    seq = "seqA"
    split_fol = "MOT17-train"
    gt_root = tmp_path / "gt"
    trk_root = tmp_path / "trackers"
    _write_text(str(gt_root / split_fol / seq / "gt" / "gt.txt"), _GT_2D)
    _write_text(
        str(gt_root / split_fol / seq / "seqinfo.ini"),
        "[Sequence]\nseqLength = 2\n",
    )
    _write_text(
        str(trk_root / split_fol / "trkA" / "data" / f"{seq}.txt"), _PRED_2D,
    )
    cfg = {
        "GT_FOLDER": str(gt_root), "TRACKERS_FOLDER": str(trk_root),
        "BENCHMARK": "MOT17", "SPLIT_TO_EVAL": "train",
        "TRACKERS_TO_EVAL": ["trkA"],
        "SEQ_INFO": {seq: None},  # ← exercises the ini-file branch
        "PRINT_CONFIG": False,
    }
    ds = MotChallenge2DBox(cfg)
    assert ds.seq_lengths[seq] == 2


def test_seq_info_with_none_length_missing_ini_raises(tmp_path):
    """If the seqinfo.ini doesn't exist, the SEQ_INFO=None branch
    raises a clear TrackEvalException."""
    seq = "seqA"
    split_fol = "MOT17-train"
    gt_root = tmp_path / "gt"
    trk_root = tmp_path / "trackers"
    _write_text(str(gt_root / split_fol / seq / "gt" / "gt.txt"), _GT_2D)
    # No seqinfo.ini intentionally.
    _write_text(
        str(trk_root / split_fol / "trkA" / "data" / f"{seq}.txt"), _PRED_2D,
    )
    cfg = {
        "GT_FOLDER": str(gt_root), "TRACKERS_FOLDER": str(trk_root),
        "BENCHMARK": "MOT17", "SPLIT_TO_EVAL": "train",
        "TRACKERS_TO_EVAL": ["trkA"],
        "SEQ_INFO": {seq: None},
        "PRINT_CONFIG": False,
    }
    with pytest.raises(TrackEvalException, match="ini file does not exist"):
        MotChallenge2DBox(cfg)


# ---------------------------------------------------------------------------
# __init__ — tracker display name length mismatch (TrackEvalException)
# ---------------------------------------------------------------------------


def test_tracker_display_names_unequal_length_raises(tmp_path):
    cfg = _build_minimal_tree(
        tmp_path, gt_rows=_GT_2D, pred_rows=_PRED_2D,
    )
    cfg["TRACKER_DISPLAY_NAMES"] = ["trkA", "extra"]  # 2 names, 1 tracker
    with pytest.raises(TrackEvalException,
                         match="tracker files and tracker display names"):
        MotChallenge2DBox(cfg)


def test_tracker_display_names_with_trackers_to_eval_none(tmp_path):
    """``TRACKERS_TO_EVAL=None`` + ``TRACKER_DISPLAY_NAMES=[...]`` is
    a misconfiguration the adapter must reject."""
    seq = "seqA"
    split_fol = "MOT17-train"
    gt_root = tmp_path / "gt"
    trk_root = tmp_path / "trackers"
    _write_text(str(gt_root / split_fol / seq / "gt" / "gt.txt"), _GT_2D)
    _write_text(
        str(trk_root / split_fol / "trkA" / "data" / f"{seq}.txt"), _PRED_2D,
    )
    cfg = {
        "GT_FOLDER": str(gt_root), "TRACKERS_FOLDER": str(trk_root),
        "BENCHMARK": "MOT17", "SPLIT_TO_EVAL": "train",
        "TRACKERS_TO_EVAL": None,
        "TRACKER_DISPLAY_NAMES": ["X"],
        "SEQ_INFO": {seq: 2},
        "PRINT_CONFIG": False,
    }
    with pytest.raises(TrackEvalException,
                         match="tracker files and tracker display names"):
        MotChallenge2DBox(cfg)


# ---------------------------------------------------------------------------
# Shared invalid-config tests across all four adapters
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("adapter_cls,gt_rows,pred_rows", [
    (MotChallenge2DBox, _GT_2D, _PRED_2D),
    (MotChallenge3DLocation, _GT_3D_LOC, _PRED_3D_LOC),
    (MTMCChallenge3DBBox, _GT_MTMC_BBOX, _PRED_MTMC_BBOX),
    (MTMCChallenge3DLocation, _GT_MTMC_BBOX, _PRED_MTMC_BBOX),
])
class TestAllAdapters:
    def test_invalid_class_raises(self, tmp_path, adapter_cls, gt_rows, pred_rows):
        cfg = _build_minimal_tree(tmp_path, gt_rows=gt_rows, pred_rows=pred_rows)
        cfg["CLASSES_TO_EVAL"] = ["not_a_valid_class"]
        with pytest.raises(TrackEvalException, match="Attempted to evaluate"):
            adapter_cls(cfg)

    def test_missing_gt_file_raises(self, tmp_path, adapter_cls, gt_rows, pred_rows):
        cfg = _build_minimal_tree(tmp_path, gt_rows=gt_rows, pred_rows=pred_rows)
        gt_path = os.path.join(
            cfg["GT_FOLDER"],
            f"{cfg['BENCHMARK']}-{cfg['SPLIT_TO_EVAL']}",
            "seqA", "gt", "gt.txt",
        )
        os.remove(gt_path)
        with pytest.raises(TrackEvalException, match="GT file"):
            adapter_cls(cfg)

    def test_missing_tracker_file_raises(self, tmp_path, adapter_cls, gt_rows, pred_rows):
        cfg = _build_minimal_tree(tmp_path, gt_rows=gt_rows, pred_rows=pred_rows)
        trk_path = os.path.join(
            cfg["TRACKERS_FOLDER"],
            f"{cfg['BENCHMARK']}-{cfg['SPLIT_TO_EVAL']}",
            "trkA", "data", "seqA.txt",
        )
        os.remove(trk_path)
        with pytest.raises(TrackEvalException, match="[Tt]racker file"):
            adapter_cls(cfg)

    def test_default_output_fol_inherits_tracker_fol(self, tmp_path, adapter_cls,
                                                       gt_rows, pred_rows):
        """``OUTPUT_FOLDER`` defaults to ``None`` -> falls back to
        ``self.tracker_fol`` (the ``if self.output_fol is None`` branch)."""
        cfg = _build_minimal_tree(tmp_path, gt_rows=gt_rows, pred_rows=pred_rows)
        cfg["OUTPUT_FOLDER"] = None
        ds = adapter_cls(cfg)
        assert ds.output_fol == ds.tracker_fol


# ---------------------------------------------------------------------------
# _load_raw_file — invalid time keys + missing columns
# ---------------------------------------------------------------------------


def test_load_raw_file_invalid_timesteps_raises(tmp_path):
    """A timestep number larger than ``seq_lengths[seq]`` triggers
    the 'invalid timesteps' exception."""
    gt = "1,1,10,20,30,40,1,1,1.0\n9999,1,11,21,30,40,1,1,1.0\n"
    pred = "1,1,10,20,30,40,0.9,1,1.0\n"
    cfg = _build_minimal_tree(tmp_path, gt_rows=gt, pred_rows=pred)
    ds = MotChallenge2DBox(cfg)
    with pytest.raises(TrackEvalException, match="invalid timesteps"):
        ds._load_raw_file("trkA", "seqA", is_gt=True)


def test_load_raw_file_tracker_invalid_timesteps_raises(tmp_path):
    """Same as above but for the tracker side (different prefix)."""
    gt = _GT_2D
    pred = "1,1,10,20,30,40,0.9,1,1.0\n9999,1,11,21,30,40,0.9,1,1.0\n"
    cfg = _build_minimal_tree(tmp_path, gt_rows=gt, pred_rows=pred)
    ds = MotChallenge2DBox(cfg)
    with pytest.raises(TrackEvalException, match="invalid timesteps"):
        ds._load_raw_file("trkA", "seqA", is_gt=False)


def test_load_raw_file_gt_too_few_columns_raises(tmp_path):
    """GT rows shorter than required columns trigger 'not enough columns'."""
    gt = "1,1,10\n2,1,11\n"  # only 3 cols
    pred = _PRED_2D
    cfg = _build_minimal_tree(tmp_path, gt_rows=gt, pred_rows=pred)
    ds = MotChallenge2DBox(cfg)
    with pytest.raises(TrackEvalException, match="not enough"):
        ds._load_raw_file("trkA", "seqA", is_gt=True)


# ---------------------------------------------------------------------------
# _calculate_similarities — empty-input branch
# ---------------------------------------------------------------------------


def test_calculate_similarities_empty_inputs(tmp_path):
    """Empty dets on either side returns a zero matrix with the right
    shape — drives the ``_calculate_box_ious`` / ``_calculate_euclidean_similarity``
    empty-array fast path."""
    cfg = _build_minimal_tree(tmp_path, gt_rows=_GT_2D, pred_rows=_PRED_2D)
    ds = MotChallenge2DBox(cfg)
    sim = ds._calculate_similarities(
        np.zeros((0, 4)), np.zeros((0, 4)),
    )
    assert sim.shape == (0, 0)


# ---------------------------------------------------------------------------
# MTMCChallenge3DBBox / MTMCChallenge3DLocation — get_display_name + extra columns
# ---------------------------------------------------------------------------


def test_mtmc_3d_bbox_get_display_name_round_trips(tmp_path):
    cfg = _build_minimal_tree(
        tmp_path, gt_rows=_GT_MTMC_BBOX, pred_rows=_PRED_MTMC_BBOX,
    )
    cfg["TRACKERS_TO_EVAL"] = ["trkA"]
    cfg["TRACKER_DISPLAY_NAMES"] = ["pretty"]
    ds = MTMCChallenge3DBBox(cfg)
    assert ds.get_display_name("trkA") == "pretty"


def test_mtmc_3d_location_get_display_name_round_trips(tmp_path):
    cfg = _build_minimal_tree(
        tmp_path, gt_rows=_GT_MTMC_BBOX, pred_rows=_PRED_MTMC_BBOX,
    )
    cfg["TRACKERS_TO_EVAL"] = ["trkA"]
    cfg["TRACKER_DISPLAY_NAMES"] = ["pretty"]
    ds = MTMCChallenge3DLocation(cfg)
    assert ds.get_display_name("trkA") == "pretty"


def test_mtmc_3d_bbox_too_few_columns_raises_for_gt(tmp_path):
    gt = "1,1,1,1.0,2.0,0.5\n"  # only 6 cols (needs 12)
    pred = _PRED_MTMC_BBOX
    cfg = _build_minimal_tree(tmp_path, gt_rows=gt, pred_rows=pred)
    ds = MTMCChallenge3DBBox(cfg)
    with pytest.raises(TrackEvalException, match="not enough"):
        ds._load_raw_file("trkA", "seqA", is_gt=True)


# ---------------------------------------------------------------------------
# _load_raw_file: happy paths for the four adapters
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("adapter_cls,gt_rows,pred_rows", [
    (MotChallenge2DBox, _GT_2D, _PRED_2D),
    (MotChallenge3DLocation, _GT_3D_LOC, _PRED_3D_LOC),
    (MTMCChallenge3DBBox, _GT_MTMC_BBOX, _PRED_MTMC_BBOX),
    (MTMCChallenge3DLocation, _GT_MTMC_BBOX, _PRED_MTMC_BBOX),
])
class TestLoadRawFileHappyPaths:
    """End-to-end happy-path `_load_raw_file` for both GT and tracker
    sides across all four adapters — drives the
    ``time_data.shape[1] >= ...`` class-column branches and the
    confidence / zero_marked extraction."""

    def test_load_gt_succeeds(self, tmp_path, adapter_cls, gt_rows, pred_rows):
        cfg = _build_minimal_tree(tmp_path, gt_rows=gt_rows, pred_rows=pred_rows)
        ds = adapter_cls(cfg)
        raw = ds._load_raw_file("trkA", "seqA", is_gt=True)
        # Raw dict uses ``gt_ids`` keys when is_gt=True (per the
        # _BaseDataset get_raw_seq_data contract).
        assert "gt_ids" in raw
        assert raw["gt_ids"][0] is not None

    def test_load_tracker_succeeds(self, tmp_path, adapter_cls, gt_rows, pred_rows):
        cfg = _build_minimal_tree(tmp_path, gt_rows=gt_rows, pred_rows=pred_rows)
        ds = adapter_cls(cfg)
        raw = ds._load_raw_file("trkA", "seqA", is_gt=False)
        assert "tracker_ids" in raw
        assert raw["tracker_ids"][0] is not None


# ---------------------------------------------------------------------------
# Extra non-2D adapters: invalid-timestep and corrupted-data branches
# ---------------------------------------------------------------------------


def test_mot_3d_location_invalid_timesteps_raises(tmp_path):
    """``MotChallenge3DLocation`` keys by col 0 (frame) — a 9999 frame
    triggers the invalid-timesteps guard."""
    gt = "1,1,10,20,30,40,1,1.5,2.5\n9999,1,10,20,30,40,1,1.5,2.5\n"
    pred = _PRED_3D_LOC
    cfg = _build_minimal_tree(tmp_path, gt_rows=gt, pred_rows=pred)
    ds = MotChallenge3DLocation(cfg)
    with pytest.raises(TrackEvalException, match="invalid timesteps"):
        ds._load_raw_file("trkA", "seqA", is_gt=True)


@pytest.mark.parametrize("adapter_cls,short_rows,full_rows", [
    (MotChallenge3DLocation, "1,1,1.0\n", _PRED_3D_LOC),
    (MTMCChallenge3DBBox, "1,1,1,1\n", _PRED_MTMC_BBOX),
    (MTMCChallenge3DLocation, "1,1,1,1\n", _PRED_MTMC_BBOX),
])
def test_load_raw_file_too_few_columns_other_adapters(
    tmp_path, adapter_cls, short_rows, full_rows,
):
    """Each adapter raises 'not enough columns' when a row is too short."""
    cfg = _build_minimal_tree(tmp_path, gt_rows=short_rows, pred_rows=full_rows)
    ds = adapter_cls(cfg)
    with pytest.raises(TrackEvalException, match="not enough"):
        ds._load_raw_file("trkA", "seqA", is_gt=True)


@pytest.mark.parametrize("adapter_cls,full_rows,corrupt_rows", [
    (MotChallenge2DBox, _GT_2D,
      "not-a-number,1,10,20,30,40,1,1,1.0\n"),
    (MotChallenge3DLocation, _GT_3D_LOC,
      "not-a-number,1,10,20,30,40,1,1.5,2.5\n"),
])
def test_load_raw_file_corrupted_data_raises(
    tmp_path, adapter_cls, full_rows, corrupt_rows,
):
    """A non-float-coercible value in any column raises
    'Cannot convert ... to float' from the per-row ValueError handler."""
    cfg = _build_minimal_tree(tmp_path, gt_rows=full_rows, pred_rows=full_rows)
    ds = adapter_cls(cfg)
    # Overwrite the GT file with the corrupted variant.
    gt_path = os.path.join(
        cfg["GT_FOLDER"],
        f"{cfg['BENCHMARK']}-{cfg['SPLIT_TO_EVAL']}",
        "seqA", "gt", "gt.txt",
    )
    # Note: _load_simple_text_file's outer except wraps the inner raise.
    # Just need to exercise the inner branch.
    with open(gt_path, "w") as f:
        f.write(corrupt_rows)
    with pytest.raises(TrackEvalException):
        ds._load_raw_file("trkA", "seqA", is_gt=True)
