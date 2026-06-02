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

"""
Tests for :mod:`spatialai_data_utils.eval.tracking.aicity_mtmc_eval`.

Focuses on the pure I/O-shaped layers — the text-row → MOT-row
converter, the spec-validating per-(scene, class) splitter, the
GT-count-weighted aggregator, and the JSON persistence helper — that
can be exercised without standing up TrackEval itself.  The full HOTA
orchestrator (``run_aicity_mtmc_evaluation``) is covered by the
existing end-to-end smoke run under ``tools/evaluation/`` rather than
here, to keep this test file fast.

The AICity MTMC spec constants (``CLASS_ID_TO_NAME`` / ``NUM_FIELDS``)
live in :mod:`spatialai_data_utils.datasets.aicity25.spec` (the 2025
edition's six-class table) and
:mod:`spatialai_data_utils.datasets.aicity26.spec` (the 2026 edition's
seven-class table, used as the project-wide default since the
2026-default switch).  Both tables are covered by
``tests/test_datasets.py``.
"""

import json
import os.path as osp

import pytest

from spatialai_data_utils.eval.tracking.aicity_mtmc_eval import (
    HOTA_FIELDS,
    _aicity_line_to_mot,
    _weighted_average,
    save_aicity_mtmc_results,
    split_aicity_mtmc_per_scene_per_class,
)


# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------


class TestHotaFields:
    """``HOTA_FIELDS`` is the leaderboard-reported metric quartet."""

    def test_exact_four_fields(self):
        assert HOTA_FIELDS == ["HOTA", "DetA", "AssA", "LocA"]


# ---------------------------------------------------------------------------
# _aicity_line_to_mot — pure row-by-row converter
# ---------------------------------------------------------------------------


class TestAICityLineToMot:
    """``_aicity_line_to_mot`` converts AICity MTMC rows to TrackEval MOT rows."""

    def test_translates_field_order_and_one_indexes_frame(self):
        """0-indexed frame -> 1-indexed; pitch/roll inserted as zero."""
        parts = "17 4 11 0 -2.6 -7.6 0.83 0.6 0.33 1.65 0.07".split(" ")
        out = _aicity_line_to_mot(parts)
        # frame_id 0 -> 1, object_id 11, confidence 1
        assert out.startswith("1 11 1 ")
        # pitch and roll columns must be exactly 0
        tokens = out.strip().split(" ")
        assert tokens[9] == "0.00000"   # pitch
        assert tokens[10] == "0.00000"  # roll
        # final yaw column carries the input yaw
        assert tokens[11] == "0.07000"

    def test_preserves_dimensions_in_w_l_h_order(self):
        """Width/length/height must stay in their AICity MTMC column order."""
        parts = "17 0 1 5 1.0 2.0 3.0 4.5 5.5 6.5 1.57".split(" ")
        tokens = _aicity_line_to_mot(parts).strip().split(" ")
        # tokens: frame obj 1 x y z w l h pitch roll yaw
        assert tokens[3:6] == ["1.00000", "2.00000", "3.00000"]  # x y z
        assert tokens[6:9] == ["4.50000", "5.50000", "6.50000"]  # w l h


# ---------------------------------------------------------------------------
# split_aicity_mtmc_per_scene_per_class — streaming spec-validating splitter
# ---------------------------------------------------------------------------


SCENE_MAP = {"17": "Warehouse_017", "18": "Warehouse_018"}


def _write_text(path, lines):
    with open(path, "w") as fp:
        fp.write("\n".join(lines))


class TestSplitAicity25PerScenePerClass:
    """Splitter routes rows to ``<scene>/<class>/`` and counts them."""

    def test_routes_rows_into_scene_class_files(self, tmp_path):
        gt_path = tmp_path / "gt.txt"
        out_root = tmp_path / "split"
        out_root.mkdir()
        _write_text(gt_path, [
            "17 0 1 0 0 0 0 1 1 1 0",   # Warehouse_017 / Person
            "17 0 2 1 0 0 0 1 1 1 0",   # Warehouse_017 / Person
            "17 2 5 0 0 0 0 1 1 1 0",   # Warehouse_017 / NovaCarter
            "18 1 7 0 0 0 0 1 1 1 0",   # Warehouse_018 / Forklift
        ])
        counts = split_aicity_mtmc_per_scene_per_class(
            str(gt_path), str(out_root), "gt.txt",
            SCENE_MAP, num_frames_to_eval=9000, is_pred=False,
        )
        assert counts == {
            "Warehouse_017": {"Person": 2, "NovaCarter": 1},
            "Warehouse_018": {"Forklift": 1},
        }
        assert (out_root / "Warehouse_017" / "Person" / "gt.txt").exists()
        assert (out_root / "Warehouse_017" / "NovaCarter" / "gt.txt").exists()
        assert (out_root / "Warehouse_018" / "Forklift" / "gt.txt").exists()

    def test_truncates_by_frame_id_0indexed_exclusive_upper(self, tmp_path):
        """Lines with frame_id >= num_frames_to_eval must be dropped."""
        gt_path = tmp_path / "gt.txt"
        out_root = tmp_path / "split"
        out_root.mkdir()
        _write_text(gt_path, [
            "17 0 1 0 0 0 0 1 1 1 0",    # kept (frame 0)
            "17 0 1 4 0 0 0 1 1 1 0",    # kept (frame 4)
            "17 0 1 5 0 0 0 1 1 1 0",    # dropped (frame 5 == limit)
            "17 0 1 99 0 0 0 1 1 1 0",   # dropped (well past limit)
        ])
        counts = split_aicity_mtmc_per_scene_per_class(
            str(gt_path), str(out_root), "gt.txt",
            SCENE_MAP, num_frames_to_eval=5, is_pred=False,
        )
        assert counts == {"Warehouse_017": {"Person": 2}}

    def test_drops_unknown_scene_silently_for_gt(self, tmp_path):
        """Unknown scene IDs in GT are dropped without raising."""
        gt_path = tmp_path / "gt.txt"
        out_root = tmp_path / "split"
        out_root.mkdir()
        _write_text(gt_path, [
            "17 0 1 0 0 0 0 1 1 1 0",  # mapped
            "99 0 1 0 0 0 0 1 1 1 0",  # unmapped — dropped, no error
        ])
        counts = split_aicity_mtmc_per_scene_per_class(
            str(gt_path), str(out_root), "gt.txt",
            SCENE_MAP, num_frames_to_eval=9000, is_pred=False,
        )
        assert counts == {"Warehouse_017": {"Person": 1}}

    def test_raises_on_unknown_scene_for_predictions(self, tmp_path):
        """Submissions must declare their scenes — unknown ID is a hard error."""
        pred_path = tmp_path / "pred.txt"
        out_root = tmp_path / "split"
        out_root.mkdir()
        _write_text(pred_path, ["99 0 1 0 0 0 0 1 1 1 0"])
        with pytest.raises(ValueError, match="scene id"):
            split_aicity_mtmc_per_scene_per_class(
                str(pred_path), str(out_root), "pred.txt",
                SCENE_MAP, num_frames_to_eval=9000, is_pred=True,
            )

    def test_raises_on_out_of_spec_class_id_for_predictions(self, tmp_path):
        """Submissions with ``class_id`` past the table top are rejected.

        Default class table is now the 2026 spec (project-wide default
        since the 2026-default switch), which goes up through ID 6
        (``PalletTruck``).  ID 7 is still out-of-spec under any
        published edition and must be rejected.
        """
        pred_path = tmp_path / "pred.txt"
        out_root = tmp_path / "split"
        out_root.mkdir()
        _write_text(pred_path, ["17 7 1 0 0 0 0 1 1 1 0"])
        with pytest.raises(ValueError, match="class id 7"):
            split_aicity_mtmc_per_scene_per_class(
                str(pred_path), str(out_root), "pred.txt",
                SCENE_MAP, num_frames_to_eval=9000, is_pred=True,
            )

    def test_warns_and_skips_on_out_of_spec_class_id_for_gt(
        self, tmp_path, caplog,
    ):
        """GT rows with class_id outside the active table warn + skip.

        Previously out-of-spec GT rows were silently dropped, which
        masked ``--edition`` mismatches; the warning is the only
        signal the operator gets, so this test pins it.  Under the
        project-wide default (2026 spec) the lowest still-invalid
        ``class_id`` is 7 (the 2026 table tops out at ID 6 =
        ``PalletTruck``).
        """
        import logging
        gt_path = tmp_path / "gt.txt"
        out_root = tmp_path / "split"
        out_root.mkdir()
        _write_text(gt_path, [
            "17 0 1 0 0 0 0 1 1 1 0",  # mapped (Person)
            "17 7 1 0 0 0 0 1 1 1 0",  # out-of-spec under default 2026 table
        ])
        with caplog.at_level(logging.WARNING, logger="spatialai_data_utils.eval.tracking.aicity_mtmc_eval"):
            counts = split_aicity_mtmc_per_scene_per_class(
                str(gt_path), str(out_root), "gt.txt",
                SCENE_MAP, num_frames_to_eval=9000, is_pred=False,
            )
        assert counts == {"Warehouse_017": {"Person": 1}}, (
            "Out-of-spec GT row must be dropped (only the Person row should land on disk)."
        )
        warning_messages = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
        assert any(
            "class_id 7" in m and "--edition" in m for m in warning_messages
        ), (
            "Out-of-spec GT class_id must produce a warning naming the "
            f"offending class_id and the --edition flag; got: {warning_messages}"
        )

    def test_raises_on_wrong_field_count_for_predictions(self, tmp_path):
        """Submissions with the wrong number of columns are rejected."""
        pred_path = tmp_path / "pred.txt"
        out_root = tmp_path / "split"
        out_root.mkdir()
        _write_text(pred_path, ["17 0 1 0 0 0 0 1 1 1"])  # 10 fields, not 11
        with pytest.raises(ValueError, match="11"):
            split_aicity_mtmc_per_scene_per_class(
                str(pred_path), str(out_root), "pred.txt",
                SCENE_MAP, num_frames_to_eval=9000, is_pred=True,
            )

    def test_raises_on_non_numeric_class_or_frame_id_for_predictions(self, tmp_path):
        """Submissions with non-numeric class/frame ids are rejected."""
        pred_path = tmp_path / "pred.txt"
        out_root = tmp_path / "split"
        out_root.mkdir()
        _write_text(pred_path, ["17 abc 1 0 0 0 0 1 1 1 0"])
        with pytest.raises(ValueError, match="Non-numeric"):
            split_aicity_mtmc_per_scene_per_class(
                str(pred_path), str(out_root), "pred.txt",
                SCENE_MAP, num_frames_to_eval=9000, is_pred=True,
            )

    def test_warns_and_skips_on_non_numeric_class_or_frame_id_for_gt(
        self, tmp_path, caplog,
    ):
        """Ground truth with a malformed numeric field warns + skips, doesn't crash.

        Mirrors the field-count handling above: GT may carry stray /
        malformed rows from upstream tools, so the splitter is
        forgiving for GT while strict for submissions.
        """
        import logging
        gt_path = tmp_path / "gt.txt"
        out_root = tmp_path / "split"
        out_root.mkdir()
        _write_text(gt_path, [
            "17 0 1 0 0 0 0 1 1 1 0",       # valid
            "17 abc 2 0 0 0 0 1 1 1 0",     # malformed class_id -> skip
            "17 0 3 NaN 0 0 0 1 1 1 0",     # malformed frame_id -> skip
            "17 0 4 1 0 0 0 1 1 1 0",       # valid
        ])
        with caplog.at_level(logging.WARNING):
            counts = split_aicity_mtmc_per_scene_per_class(
                str(gt_path), str(out_root), "gt.txt",
                SCENE_MAP, num_frames_to_eval=9000, is_pred=False,
            )
        assert counts == {"Warehouse_017": {"Person": 2}}
        warning_msgs = [r.message for r in caplog.records]
        assert any("non-numeric" in m.lower() for m in warning_msgs)

    def test_skips_blank_lines(self, tmp_path):
        """Blank / whitespace-only lines do not produce a warning or row."""
        gt_path = tmp_path / "gt.txt"
        out_root = tmp_path / "split"
        out_root.mkdir()
        _write_text(gt_path, [
            "17 0 1 0 0 0 0 1 1 1 0",
            "",
            "   ",
            "17 0 1 1 0 0 0 1 1 1 0",
        ])
        counts = split_aicity_mtmc_per_scene_per_class(
            str(gt_path), str(out_root), "gt.txt",
            SCENE_MAP, num_frames_to_eval=9000, is_pred=False,
        )
        assert counts == {"Warehouse_017": {"Person": 2}}


# ---------------------------------------------------------------------------
# class_id_to_name parameter — per-edition class-table override
# ---------------------------------------------------------------------------


_AICITY26_SCENE_MAP = {"23": "Warehouse_023"}


class TestSplitAicityMtmcFrameStart:
    """``frame_start`` kwarg lets callers evaluate non-prefix frame windows.

    The default (``frame_start=0``) reproduces the official validation
    server's ``[0, num_frames_to_eval)`` left-anchored window.  Passing
    a positive ``frame_start`` shifts the window's lower bound so
    callers can evaluate e.g. the second half of the test set
    (``frame_start=4500, num_frames_to_eval=9000``) without writing
    their own GT/pred subset.
    """

    def _rows_for_frames(self, frames):
        return [f"17 0 1 {f} 0 0 0 1 1 1 0" for f in frames]

    def test_default_frame_start_zero_keeps_legacy_behavior(self, tmp_path):
        """No ``frame_start`` arg -> identical to pre-flag behaviour."""
        gt_path = tmp_path / "gt.txt"
        out_root = tmp_path / "split"
        out_root.mkdir()
        _write_text(gt_path, self._rows_for_frames([0, 4, 5, 99]))
        counts = split_aicity_mtmc_per_scene_per_class(
            str(gt_path), str(out_root), "gt.txt",
            SCENE_MAP, num_frames_to_eval=5, is_pred=False,
        )
        # frames 5 and 99 dropped (>= upper), 0 and 4 kept.
        assert counts == {"Warehouse_017": {"Person": 2}}

    def test_frame_start_drops_rows_below_lower_bound(self, tmp_path):
        """``frame_start=2`` rejects frame_id 0 and 1."""
        gt_path = tmp_path / "gt.txt"
        out_root = tmp_path / "split"
        out_root.mkdir()
        _write_text(gt_path, self._rows_for_frames([0, 1, 2, 3, 4]))
        counts = split_aicity_mtmc_per_scene_per_class(
            str(gt_path), str(out_root), "gt.txt",
            SCENE_MAP, num_frames_to_eval=5, is_pred=False,
            frame_start=2,
        )
        # Only frames 2, 3, 4 survive [2, 5).
        assert counts == {"Warehouse_017": {"Person": 3}}

    def test_second_half_window(self, tmp_path):
        """Mimic 'second half of 10 frames': frame_start=5, num=10."""
        gt_path = tmp_path / "gt.txt"
        out_root = tmp_path / "split"
        out_root.mkdir()
        _write_text(gt_path, self._rows_for_frames(range(10)))
        counts = split_aicity_mtmc_per_scene_per_class(
            str(gt_path), str(out_root), "gt.txt",
            SCENE_MAP, num_frames_to_eval=10, is_pred=False,
            frame_start=5,
        )
        # frames 0..4 dropped, 5..9 kept -> exactly 5 rows.
        assert counts == {"Warehouse_017": {"Person": 5}}

    def test_first_and_second_half_partition_the_full_window(self, tmp_path):
        """First half + second half row counts must sum to full-window count."""
        gt_path = tmp_path / "gt.txt"
        out_root = tmp_path / "split"
        out_root.mkdir()
        _write_text(gt_path, self._rows_for_frames(range(10)))

        def _count(start, end):
            sub = tmp_path / f"split_{start}_{end}"
            sub.mkdir()
            return split_aicity_mtmc_per_scene_per_class(
                str(gt_path), str(sub), "gt.txt",
                SCENE_MAP, num_frames_to_eval=end, is_pred=False,
                frame_start=start,
            )["Warehouse_017"]["Person"]

        first = _count(0, 5)
        second = _count(5, 10)
        full = _count(0, 10)
        assert first + second == full == 10

    def test_mid_slice_window(self, tmp_path):
        """Non-half slice: [3, 7) keeps exactly frames 3, 4, 5, 6."""
        gt_path = tmp_path / "gt.txt"
        out_root = tmp_path / "split"
        out_root.mkdir()
        _write_text(gt_path, self._rows_for_frames(range(10)))
        counts = split_aicity_mtmc_per_scene_per_class(
            str(gt_path), str(out_root), "gt.txt",
            SCENE_MAP, num_frames_to_eval=7, is_pred=False,
            frame_start=3,
        )
        assert counts == {"Warehouse_017": {"Person": 4}}

    def test_invalid_frame_start_raises(self, tmp_path):
        """An empty (``frame_start >= num_frames_to_eval``) or negative
        window is rejected at the API boundary instead of silently
        producing empty results."""
        gt_path = tmp_path / "gt.txt"
        out_root = tmp_path / "split"
        out_root.mkdir()
        _write_text(gt_path, self._rows_for_frames(range(10)))
        with pytest.raises(ValueError):
            split_aicity_mtmc_per_scene_per_class(
                str(gt_path), str(out_root), "gt.txt",
                SCENE_MAP, num_frames_to_eval=5, is_pred=False,
                frame_start=5,
            )
        with pytest.raises(ValueError):
            split_aicity_mtmc_per_scene_per_class(
                str(gt_path), str(out_root), "gt.txt",
                SCENE_MAP, num_frames_to_eval=5, is_pred=False,
                frame_start=-1,
            )


class TestSplitAicityMtmcWithExplicitClassTable:
    """``class_id_to_name`` keyword switches the active spec table.

    Default (None) is the AICity'26 seven-class table — the
    project-wide default since the 2026-default switch — so
    ``PalletTruck`` rows (class id 6) are accepted by default.  Pass
    the AICity'25 ``CLASS_ID_TO_NAME`` explicitly to evaluate against
    the original six-class table (id 6 then becomes out-of-spec).
    """

    def test_default_accepts_class_id_6_pallet_truck(self, tmp_path):
        """No ``class_id_to_name`` -> 2026 default -> class id 6 routes to PalletTruck."""
        gt_path = tmp_path / "gt.txt"
        out_root = tmp_path / "split"
        out_root.mkdir()
        _write_text(gt_path, [
            "17 0 1 0 0 0 0 1 1 1 0",
            "17 6 2 0 0 0 0 1 1 1 0",
            "17 6 2 1 0 0 0 1 1 1 0",
        ])
        counts = split_aicity_mtmc_per_scene_per_class(
            str(gt_path), str(out_root), "gt.txt",
            SCENE_MAP, num_frames_to_eval=9000, is_pred=False,
        )
        assert counts == {"Warehouse_017": {"Person": 1, "PalletTruck": 2}}
        assert (out_root / "Warehouse_017" / "PalletTruck" / "gt.txt").exists()

    def test_explicit_aicity25_table_rejects_class_id_6_for_predictions(self, tmp_path):
        """Passing the 2025 ``CLASS_ID_TO_NAME`` brings back the six-class table; id 6 is rejected."""
        from spatialai_data_utils.datasets.aicity25.spec import (
            CLASS_ID_TO_NAME as AICITY25_CLASS_ID_TO_NAME,
        )
        pred_path = tmp_path / "pred.txt"
        out_root = tmp_path / "split"
        out_root.mkdir()
        _write_text(pred_path, ["17 6 1 0 0 0 0 1 1 1 0"])
        with pytest.raises(ValueError, match="class id 6"):
            split_aicity_mtmc_per_scene_per_class(
                str(pred_path), str(out_root), "pred.txt",
                SCENE_MAP, num_frames_to_eval=9000, is_pred=True,
                class_id_to_name=AICITY25_CLASS_ID_TO_NAME,
            )

    def test_aicity26_table_accepts_pallet_truck(self, tmp_path):
        """With the 2026 table, class id 6 routes into a PalletTruck dir."""
        from spatialai_data_utils.datasets.aicity26.spec import (
            CLASS_ID_TO_NAME as AICITY26_CLASS_ID_TO_NAME,
        )
        gt_path = tmp_path / "gt.txt"
        out_root = tmp_path / "split"
        out_root.mkdir()
        _write_text(gt_path, [
            "23 0 1 0 0 0 0 1 1 1 0",
            "23 6 2 0 0 0 0 1 1 1 0",
            "23 6 2 1 0 0 0 1 1 1 0",
        ])
        counts = split_aicity_mtmc_per_scene_per_class(
            str(gt_path), str(out_root), "gt.txt",
            _AICITY26_SCENE_MAP, num_frames_to_eval=9000, is_pred=False,
            class_id_to_name=AICITY26_CLASS_ID_TO_NAME,
        )
        assert counts == {"Warehouse_023": {"Person": 1, "PalletTruck": 2}}
        assert (out_root / "Warehouse_023" / "PalletTruck" / "gt.txt").exists()

    def test_aicity26_table_rejects_class_id_7_for_predictions(self, tmp_path):
        """The 2026 table tops out at id 6; ids 7+ are still out of spec."""
        from spatialai_data_utils.datasets.aicity26.spec import (
            CLASS_ID_TO_NAME as AICITY26_CLASS_ID_TO_NAME,
        )
        pred_path = tmp_path / "pred.txt"
        out_root = tmp_path / "split"
        out_root.mkdir()
        _write_text(pred_path, ["23 7 1 0 0 0 0 1 1 1 0"])
        with pytest.raises(ValueError, match="class id 7"):
            split_aicity_mtmc_per_scene_per_class(
                str(pred_path), str(out_root), "pred.txt",
                _AICITY26_SCENE_MAP, num_frames_to_eval=9000, is_pred=True,
                class_id_to_name=AICITY26_CLASS_ID_TO_NAME,
            )

    def test_aicity26_pred_with_pallet_truck_round_trips(self, tmp_path):
        """A 2026 prediction file with PalletTruck rows must split cleanly."""
        from spatialai_data_utils.datasets.aicity26.spec import (
            CLASS_ID_TO_NAME as AICITY26_CLASS_ID_TO_NAME,
        )
        pred_path = tmp_path / "pred.txt"
        out_root = tmp_path / "split"
        out_root.mkdir()
        _write_text(pred_path, [
            "23 6 100 0 -14.62 -51.47 0.98 0.82 1.89 1.97 2.17",
            "23 6 100 1 -14.55 -51.47 0.98 0.82 1.89 1.97 2.17",
        ])
        counts = split_aicity_mtmc_per_scene_per_class(
            str(pred_path), str(out_root), "pred.txt",
            _AICITY26_SCENE_MAP, num_frames_to_eval=9000, is_pred=True,
            class_id_to_name=AICITY26_CLASS_ID_TO_NAME,
        )
        assert counts == {"Warehouse_023": {"PalletTruck": 2}}


# ---------------------------------------------------------------------------
# _weighted_average — GT-count-weighted aggregator
# ---------------------------------------------------------------------------


class TestWeightedAverage:
    """``_weighted_average`` drops missing keys instead of zeroing them."""

    def test_intersects_keys_and_normalizes(self):
        """Final = sum(w_i * v_i) / sum(w_i) over keys present in both."""
        weights = {"a": 100, "b": 200, "c": 700}
        values = {"a": 0.5, "b": 0.25, "c": 0.1}
        # 100*0.5 + 200*0.25 + 700*0.1 = 170; / 1000 = 0.17
        assert _weighted_average(weights, values) == pytest.approx(0.17)

    def test_missing_value_keys_excluded_not_zeroed(self):
        """A scene missing from *values* must not pull the mean toward zero."""
        weights = {"a": 100, "b": 200}
        values = {"a": 0.5}  # 'b' has weight but no value
        # Only key 'a' participates: 100*0.5 / 100 = 0.5.
        assert _weighted_average(weights, values) == pytest.approx(0.5)

    def test_empty_intersection_returns_zero(self):
        assert _weighted_average({"a": 1}, {"b": 0.5}) == 0.0


# ---------------------------------------------------------------------------
# save_aicity_mtmc_results - JSON persistence in 0-100 scale
# ---------------------------------------------------------------------------


class TestSaveAicity25Track1Results:
    """``save_aicity_mtmc_results`` writes the 0-100-scaled JSON."""

    def _make_results(self):
        return {
            "eval_type": "bbox",
            "num_frames_to_eval": 9000,
            "scene_id_to_name": {"17": "Warehouse_017"},
            "per_scene_object_counts": {"Warehouse_017": 200},
            "per_scene_per_class": {
                "Warehouse_017": {
                    "Person": {
                        "HOTA": 0.7423, "DetA": 0.7781,
                        "AssA": 0.7082, "LocA": 0.8377,
                    },
                    "Forklift": None,
                },
            },
            "per_scene": {
                "Warehouse_017": {
                    "HOTA": 0.5686, "DetA": 0.6179,
                    "AssA": 0.5333, "LocA": 0.6169,
                },
            },
            "final": {
                "HOTA": 0.6119, "DetA": 0.6311,
                "AssA": 0.5966, "LocA": 0.6965,
            },
        }

    def test_writes_json_in_0_to_100_scale(self, tmp_path):
        path = save_aicity_mtmc_results(self._make_results(), str(tmp_path))
        assert osp.exists(path)
        with open(path, "r") as fp:
            on_disk = json.load(fp)
        # Headline aggregate is multiplied by 100.
        assert on_disk["final"]["HOTA"] == pytest.approx(61.19)
        # Per-(scene, class) too.
        assert on_disk["per_scene_per_class"]["Warehouse_017"]["Person"]["HOTA"] \
            == pytest.approx(74.23)
        # Failed (None) classes round-trip as JSON null.
        assert on_disk["per_scene_per_class"]["Warehouse_017"]["Forklift"] is None

    def test_does_not_mutate_input_results(self, tmp_path):
        """Saving must not change the in-memory results dict."""
        results = self._make_results()
        snapshot = json.dumps(results, sort_keys=True)
        save_aicity_mtmc_results(results, str(tmp_path))
        assert json.dumps(results, sort_keys=True) == snapshot

    def test_creates_missing_output_dir(self, tmp_path):
        target = tmp_path / "nested" / "deeper"
        path = save_aicity_mtmc_results(self._make_results(), str(target))
        assert osp.exists(path)
        assert path.startswith(str(target))


# ===========================================================
# Coverage supplement (merged from test_aicity_mtmc_eval_coverage.py)
# ===========================================================

"""Coverage supplement for ``eval.tracking.aicity_mtmc_eval`` — pins
the missing-input early-return branches of ``_run_hota_for_scene_class``,
the TrackEval exception-catch path, the orchestrator skip-paths
(``scene_dir`` absent, GT/pred files missing for a class), and the
no-valid-class scene-skip warning."""

import logging
import os

import pytest

from spatialai_data_utils.eval.tracking.aicity_mtmc_eval import (
    _run_hota_for_scene_class,
    run_aicity_mtmc_evaluation,
)


# ---------------------------------------------------------------------------
# _run_hota_for_scene_class — early-return branches
# ---------------------------------------------------------------------------


class TestRunHotaForSceneClassEarlyReturns:
    def test_returns_none_for_missing_gt_file(self, tmp_path):
        # GT path doesn't exist.
        pred = tmp_path / "pred.txt"
        pred.write_text("1 1 1 1 0 0 0 0 0 0 0 0\n")
        out = _run_hota_for_scene_class(
            gt_mot_file=str(tmp_path / "no-such.txt"),
            pred_mot_file=str(pred),
            work_dir=str(tmp_path / "work"),
            eval_type="bbox", num_cores=1,
            seq_name="seq", seq_length=10, fps=10.0, quiet=True,
        )
        assert out is None

    def test_returns_none_for_empty_gt_file(self, tmp_path):
        gt = tmp_path / "gt.txt"
        gt.write_text("")  # exists but empty
        pred = tmp_path / "pred.txt"
        pred.write_text("1 1 1 1 0 0 0 0 0 0 0 0\n")
        out = _run_hota_for_scene_class(
            gt_mot_file=str(gt), pred_mot_file=str(pred),
            work_dir=str(tmp_path / "work"),
            eval_type="bbox", num_cores=1,
            seq_name="seq", seq_length=10, fps=10.0, quiet=True,
        )
        assert out is None

    def test_returns_none_for_missing_pred_file(self, tmp_path):
        gt = tmp_path / "gt.txt"
        gt.write_text("1 1 1 1 0 0 0 0 0 0 0 0\n")
        out = _run_hota_for_scene_class(
            gt_mot_file=str(gt),
            pred_mot_file=str(tmp_path / "no-such.txt"),
            work_dir=str(tmp_path / "work"),
            eval_type="bbox", num_cores=1,
            seq_name="seq", seq_length=10, fps=10.0, quiet=True,
        )
        assert out is None

    def test_returns_none_when_trackeval_raises(self, tmp_path, monkeypatch):
        """If TrackEval throws inside ``run_evaluation`` the wrapper
        catches it, logs, and returns ``None`` — never raises through
        the orchestrator boundary."""
        gt = tmp_path / "gt.txt"
        pred = tmp_path / "pred.txt"
        gt.write_text("1 1 1 1 0 0 0 0 0 0 0 0\n")
        pred.write_text("1 1 1 1 0 0 0 0 0 0 0 0\n")

        from spatialai_data_utils.eval.tracking import aicity_mtmc_eval as mod

        def _boom(*args, **kwargs):
            raise RuntimeError("synthetic trackeval failure")

        monkeypatch.setattr(mod, "run_evaluation", _boom)
        out = _run_hota_for_scene_class(
            gt_mot_file=str(gt), pred_mot_file=str(pred),
            work_dir=str(tmp_path / "work"),
            eval_type="bbox", num_cores=1,
            seq_name="seq", seq_length=10, fps=10.0, quiet=True,
        )
        assert out is None

    def test_returns_none_on_unexpected_hota_result_shape(self, tmp_path, monkeypatch, caplog):
        """A mismatch between the expected ``output_res[...][...]['class']
        ['HOTA']`` shape and the actual structure triggers
        ``KeyError`` -> warn -> return None (catches any TrackEval
        backward-compat drift)."""
        gt = tmp_path / "gt.txt"
        pred = tmp_path / "pred.txt"
        gt.write_text("1 1 1 1 0 0 0 0 0 0 0 0\n")
        pred.write_text("1 1 1 1 0 0 0 0 0 0 0 0\n")

        from spatialai_data_utils.eval.tracking import aicity_mtmc_eval as mod
        monkeypatch.setattr(mod, "run_evaluation",
                            lambda *a, **kw: ({"some_other_shape": {}}, {}))
        with caplog.at_level(logging.WARNING):
            out = _run_hota_for_scene_class(
                gt_mot_file=str(gt), pred_mot_file=str(pred),
                work_dir=str(tmp_path / "work"),
                eval_type="bbox", num_cores=1,
                seq_name="seq", seq_length=10, fps=10.0, quiet=True,
            )
        assert out is None
        assert "Unexpected HOTA result shape" in caplog.text


# ---------------------------------------------------------------------------
# run_aicity_mtmc_evaluation — orchestrator skip + warn branches
# ---------------------------------------------------------------------------


# Orchestrator-test-only constants — distinct names so they don't
# shadow the top-of-module ``_ORCH_SCENE_MAP`` used by the splitter tests.
_ORCH_SCENE_ID = "17"
_ORCH_SCENE_NAME = "Warehouse_017"
_ORCH_SCENE_MAP = {_ORCH_SCENE_ID: _ORCH_SCENE_NAME}


def _aicity_row(*, scene=_ORCH_SCENE_ID, class_id=0, object_id, frame_id,
                x=0.0, y=0.0, z=0.0, w=0.6, length=0.6, h=1.8, yaw=0.0):
    return (
        f"{scene} {class_id} {object_id} {frame_id} "
        f"{x} {y} {z} {w} {length} {h} {yaw}\n"
    )


def test_orchestrator_logs_no_valid_class_warning_when_every_class_fails(
    tmp_path, monkeypatch, caplog,
):
    """When every (scene, class) pair returns ``None`` from
    ``_run_hota_for_scene_class`` (e.g. because TrackEval keeps
    failing), the orchestrator emits the "no class evaluated; excluding
    from final mean" warning for that scene."""
    gt = tmp_path / "gt.txt"
    pred = tmp_path / "pred.txt"
    gt.write_text(_aicity_row(object_id=1, frame_id=0)
                  + _aicity_row(object_id=1, frame_id=1))
    pred.write_text(_aicity_row(object_id=1, frame_id=0)
                    + _aicity_row(object_id=1, frame_id=1))

    # Force every (scene, class) evaluation to fail by stubbing the
    # per-(scene, class) HOTA runner to always return None.
    from spatialai_data_utils.eval.tracking import aicity_mtmc_eval as mod
    monkeypatch.setattr(mod, "_run_hota_for_scene_class",
                        lambda *a, **kw: None)

    with caplog.at_level(logging.WARNING):
        results = run_aicity_mtmc_evaluation(
            ground_truth_file=str(gt),
            prediction_file=str(pred),
            scene_id_to_name=_ORCH_SCENE_MAP,
            output_dir=str(tmp_path / "out"),
            num_cores=1, num_frames_to_eval=100,
            eval_type="bbox", fps=10.0, quiet=True,
        )
    assert _ORCH_SCENE_NAME not in results["per_scene"]
    assert "no class evaluated" in caplog.text


def test_orchestrator_logs_failed_class_when_metrics_are_none(
    tmp_path, monkeypatch, caplog,
):
    """When ``_run_hota_for_scene_class`` returns ``None`` for a
    specific (scene, class) pair, the orchestrator logs a "FAILED"
    line and records the class entry as ``None``."""
    gt = tmp_path / "gt.txt"
    pred = tmp_path / "pred.txt"
    gt.write_text(_aicity_row(object_id=1, frame_id=0))
    pred.write_text(_aicity_row(object_id=1, frame_id=0))

    from spatialai_data_utils.eval.tracking import aicity_mtmc_eval as mod
    monkeypatch.setattr(mod, "_run_hota_for_scene_class",
                        lambda *a, **kw: None)
    with caplog.at_level(logging.INFO,
                          logger="spatialai_data_utils.eval.tracking.aicity_mtmc_eval"):
        results = run_aicity_mtmc_evaluation(
            ground_truth_file=str(gt),
            prediction_file=str(pred),
            scene_id_to_name=_ORCH_SCENE_MAP,
            output_dir=str(tmp_path / "out"),
            num_cores=1, num_frames_to_eval=100,
            eval_type="bbox", fps=10.0, quiet=True,
        )
    # The (scene, class) entry is recorded as None.
    assert results["per_scene_per_class"][_ORCH_SCENE_NAME]["Person"] is None
    assert "FAILED" in caplog.text


def test_orchestrator_skips_class_with_missing_pred_file(
    tmp_path, monkeypatch, caplog,
):
    """If the per-(scene, class) split produced a GT file but the
    matching pred file is absent (or vice versa), the orchestrator
    logs a 'skipping' line and records ``None``."""
    gt = tmp_path / "gt.txt"
    pred = tmp_path / "pred.txt"
    gt.write_text(_aicity_row(object_id=1, frame_id=0))
    # No pred rows at all -> split produces a GT dir but no pred file.
    pred.write_text("")

    with caplog.at_level(logging.INFO,
                          logger="spatialai_data_utils.eval.tracking.aicity_mtmc_eval"):
        results = run_aicity_mtmc_evaluation(
            ground_truth_file=str(gt),
            prediction_file=str(pred),
            scene_id_to_name=_ORCH_SCENE_MAP,
            output_dir=str(tmp_path / "out"),
            num_cores=1, num_frames_to_eval=100,
            eval_type="bbox", fps=10.0, quiet=True,
        )
    # Scene entry exists but every class was skipped -> per_scene
    # doesn't include this scene.
    assert _ORCH_SCENE_NAME not in results["per_scene"]
    assert "skipping" in caplog.text
