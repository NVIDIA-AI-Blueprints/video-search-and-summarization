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

"""End-to-end + summary-print tests for
``eval.tracking.aicity_mtmc_eval``.

The companion file ``test_aicity_mtmc_eval.py`` covers the pure
helpers (line→MOT conversion, splitter, weighted-average aggregator,
on-disk JSON persistence).  This file fills in the remaining gaps:

* the orchestrator ``run_aicity_mtmc_evaluation`` which drives the
  real TrackEval-based ``_run_hota_for_scene_class`` once per
  (scene, class) pair,
* the warn-and-skip GT branches in
  ``split_aicity_mtmc_per_scene_per_class`` (bad field count,
  non-numeric class id, unknown class id),
* the formatted-log writer ``print_aicity_mtmc_summary``.

The orchestrator test runs real TrackEval, so it's slower than the
helper tests — keep that in mind when iterating.
"""

import json
import logging

import pytest

from spatialai_data_utils.eval.tracking.aicity_mtmc_eval import (
    HOTA_FIELDS,
    print_aicity_mtmc_summary,
    run_aicity_mtmc_evaluation,
    split_aicity_mtmc_per_scene_per_class,
)


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


SCENE_ID = "17"
SCENE_NAME = "Warehouse_017"
SCENE_MAP = {SCENE_ID: SCENE_NAME}
# Use class id 0 = "Person" (consistent across AICity'25 and AICity'26 tables)
CLASS_ID = 0
CLASS_NAME = "Person"


def _aicity_row(*, scene=SCENE_ID, class_id=CLASS_ID, object_id, frame_id,
                x=0.0, y=0.0, z=0.0, w=0.6, length=0.6, h=1.8, yaw=0.0):
    """One AICity MTMC row: ``scene class obj frame x y z w l h yaw``."""
    return (
        f"{scene} {class_id} {object_id} {frame_id} "
        f"{x} {y} {z} {w} {length} {h} {yaw}\n"
    )


def _write_lines(path, lines):
    path.write_text("".join(lines))


# ---------------------------------------------------------------------------
# print_aicity_mtmc_summary  — synthetic results dict, capture log output
# ---------------------------------------------------------------------------


def _synthetic_results():
    """Hand-crafted results dict in the shape ``run_aicity_mtmc_evaluation``
    returns.  Two scenes, two classes each, one class with metrics and
    one with a ``None`` (skipped) sentinel so we hit both row-format
    branches in the printer."""
    per_class_metrics = {f: 0.75 for f in HOTA_FIELDS}
    per_scene_metrics = {f: 0.7 for f in HOTA_FIELDS}
    return {
        "eval_type": "bbox",
        "num_frames_to_eval": 100,
        "scene_id_to_name": SCENE_MAP,
        "per_scene_object_counts": {"sceneA": 12, "sceneB": 5},
        "per_scene_per_class": {
            "sceneA": {"Person": per_class_metrics, "Forklift": None},
            "sceneB": {"Person": per_class_metrics},
        },
        "per_scene": {
            "sceneA": per_scene_metrics,
            "sceneB": per_scene_metrics,
        },
        "final": {f: 0.65 for f in HOTA_FIELDS},
    }


def test_print_summary_logs_both_skipped_and_evaluated_classes(caplog):
    """The skipped (None) class must render as ``--`` columns and the
    evaluated class as numeric percentages; the per-scene table must
    show one row per scene plus a WEIGHTED FINAL footer."""
    caplog.set_level(logging.INFO,
                     logger="spatialai_data_utils.eval.tracking.aicity_mtmc_eval")
    print_aicity_mtmc_summary(_synthetic_results())
    out = caplog.text
    assert "Per-(scene, class) HOTA results" in out
    # Skipped class renders as '--' columns.
    assert "Forklift" in out and "--" in out
    # Numeric (75.00) shows up for the evaluated class (HOTA=0.75 * 100).
    assert "75.00" in out
    # Per-scene aggregate table + weighted final row.
    assert "Per-scene HOTA (mean across classes) and weighted aggregate" in out
    assert "WEIGHTED FINAL" in out


# ---------------------------------------------------------------------------
# Additional warn-and-skip GT branches in the splitter
# ---------------------------------------------------------------------------


class TestSplitGTWarnSkipBranches:
    def test_gt_warns_and_skips_row_with_wrong_field_count(self, tmp_path, caplog):
        """GT rows with the wrong field count must log a warning and
        skip the row — never raise (only predictions are strict)."""
        gt = tmp_path / "gt.txt"
        # First line is well-formed; second is short (only 5 fields).
        _write_lines(gt, [
            _aicity_row(object_id=1, frame_id=0),
            f"{SCENE_ID} {CLASS_ID} 1 1 0.0\n",
        ])
        out_dir = tmp_path / "split"
        out_dir.mkdir()
        with caplog.at_level(logging.WARNING):
            counts = split_aicity_mtmc_per_scene_per_class(
                str(gt), str(out_dir), "gt.txt",
                scene_id_to_name=SCENE_MAP, num_frames_to_eval=100,
                is_pred=False,
            )
        assert counts[SCENE_NAME][CLASS_NAME] == 1  # only the well-formed row
        assert "5 fields" in caplog.text or "expected 11" in caplog.text

    def test_gt_warns_and_skips_row_with_non_numeric_class_id(self, tmp_path, caplog):
        gt = tmp_path / "gt.txt"
        _write_lines(gt, [
            _aicity_row(object_id=1, frame_id=0),
            f"{SCENE_ID} not_a_number 1 1 0 0 0 0.6 0.6 1.8 0\n",
        ])
        with caplog.at_level(logging.WARNING):
            counts = split_aicity_mtmc_per_scene_per_class(
                str(gt), str(tmp_path / "split2"), "gt.txt",
                scene_id_to_name=SCENE_MAP, num_frames_to_eval=100,
                is_pred=False,
            )
        assert counts[SCENE_NAME][CLASS_NAME] == 1
        assert "non-numeric" in caplog.text

    def test_gt_warns_and_skips_unknown_class_id(self, tmp_path, caplog):
        gt = tmp_path / "gt.txt"
        _write_lines(gt, [
            _aicity_row(object_id=1, frame_id=0),
            _aicity_row(object_id=2, frame_id=0, class_id=999),  # unknown
        ])
        with caplog.at_level(logging.WARNING):
            counts = split_aicity_mtmc_per_scene_per_class(
                str(gt), str(tmp_path / "split3"), "gt.txt",
                scene_id_to_name=SCENE_MAP, num_frames_to_eval=100,
                is_pred=False,
            )
        assert counts[SCENE_NAME][CLASS_NAME] == 1
        assert "class_id 999" in caplog.text


# ---------------------------------------------------------------------------
# run_aicity_mtmc_evaluation  — real TrackEval end-to-end
# ---------------------------------------------------------------------------


def _write_minimal_gt_and_pred(tmp_path, n_frames=4):
    """Build a perfect-match GT + pred pair: one Person object that
    holds a constant world position across ``n_frames``."""
    gt_lines = [
        _aicity_row(object_id=1, frame_id=i, x=float(i), y=0.0)
        for i in range(n_frames)
    ]
    pred_lines = [
        _aicity_row(object_id=1, frame_id=i, x=float(i), y=0.0)
        for i in range(n_frames)
    ]
    gt = tmp_path / "gt.txt"
    pred = tmp_path / "pred.txt"
    _write_lines(gt, gt_lines)
    _write_lines(pred, pred_lines)
    return str(gt), str(pred)


@pytest.fixture
def _force_sequential_trackeval(monkeypatch):
    """TrackEval's Evaluator uses ``multiprocessing.Pool(NUM_PARALLEL_CORES)``
    when ``USE_PARALLEL=True``. On Python 3.13 the parent pytest
    process is multi-threaded (numpy / torch / pytorch3d each spawn
    workers), so ``fork()`` from a long-running parent can deadlock
    or silently fail. Force the sequential path for these end-to-end
    tests so they don't depend on test-order luck.

    Patches the consumer module ``aicity_mtmc_eval``, not the source
    ``trackeval_utils`` — because aicity_mtmc_eval does
    ``from trackeval_utils import setup_evaluation_configs`` (an
    eager binding into its own namespace) so a source-side patch
    wouldn't reach the consumer's already-bound copy."""
    from spatialai_data_utils.eval.tracking import aicity_mtmc_eval

    original = aicity_mtmc_eval.setup_evaluation_configs

    def _seq_configs(*args, **kwargs):
        ds, ev = original(*args, **kwargs)
        ev["USE_PARALLEL"] = False
        return ds, ev

    monkeypatch.setattr(
        aicity_mtmc_eval, "setup_evaluation_configs", _seq_configs,
    )


class TestRunAicityMtmcEvaluation:
    """End-to-end orchestrator tests.

    These exercise the real bundled TrackEval engine; each test runs
    HOTA for one (scene, class) pair so they're not free — but still
    fast enough (sub-second) on small fixtures."""

    def test_perfect_match_yields_high_hota(self, tmp_path, _force_sequential_trackeval):
        gt, pred = _write_minimal_gt_and_pred(tmp_path, n_frames=4)
        results = run_aicity_mtmc_evaluation(
            ground_truth_file=gt,
            prediction_file=pred,
            scene_id_to_name=SCENE_MAP,
            output_dir=str(tmp_path / "out"),
            num_cores=1,
            num_frames_to_eval=100,
            eval_type="bbox",
            fps=10.0,
            quiet=True,
        )
        # Shape: top-level keys
        assert set(results.keys()) >= {
            "eval_type", "num_frames_to_eval", "scene_id_to_name",
            "per_scene_object_counts", "per_scene_per_class",
            "per_scene", "final",
        }
        assert results["eval_type"] == "bbox"
        # Perfect-match -> final HOTA approaches 1.0
        assert results["final"]["HOTA"] == pytest.approx(1.0, abs=0.01)
        # Per-scene record for the only scene we drove.
        assert SCENE_NAME in results["per_scene"]

    def test_out_of_gt_frame_predictions_are_ignored(self, tmp_path, _force_sequential_trackeval):
        """Predictions on frames absent from the GT are dropped, not scored
        as false positives.

        Evaluation is scoped to the frames the GT annotates, so a
        full-length submission judged against a truncated GT is not
        penalised for its out-of-GT-range predictions.  The GT covers
        frames 0-3 while the prediction covers 0-7 within a 100-frame
        window; the extra frames 4-7 must be ignored, leaving a perfect
        self-eval on frames 0-3 -> HOTA approaches 1.0.
        """
        gt_lines = [
            _aicity_row(object_id=1, frame_id=i, x=float(i), y=0.0)
            for i in range(4)
        ]
        pred_lines = [
            _aicity_row(object_id=1, frame_id=i, x=float(i), y=0.0)
            for i in range(8)
        ]
        gt = tmp_path / "gt.txt"
        pred = tmp_path / "pred.txt"
        _write_lines(gt, gt_lines)
        _write_lines(pred, pred_lines)

        results = run_aicity_mtmc_evaluation(
            ground_truth_file=str(gt),
            prediction_file=str(pred),
            scene_id_to_name=SCENE_MAP,
            output_dir=str(tmp_path / "out"),
            num_cores=1,
            num_frames_to_eval=100,  # window spans frames 4-7, but the GT lacks them
            eval_type="bbox",
            fps=10.0,
            quiet=True,
        )
        # The 4 extra predictions (frames 4-7, no GT) are ignored, so the
        # self-consistent frames 0-3 still yield a perfect score.
        assert results["final"]["HOTA"] == pytest.approx(1.0, abs=0.01)
        assert results["per_scene_object_counts"] == {SCENE_NAME: 4}

    def test_partial_submission_does_not_outscore_full_submission(
        self, tmp_path, _force_sequential_trackeval,
    ):
        """Regression: omitting whole warehouses must not raise the score.

        Reproduces the participant report (Team Xiilab_Calix): a submission
        covering only a subset of warehouses previously out-scored a
        complete submission because missing warehouses were dropped from
        the weighted mean. With the fix they are scored 0 at their full GT
        weight, so a partial submission can no longer beat the full one and
        can no longer reach a perfect score.

        GT: three warehouses (all Person, one track each). FULL is perfect
        on W023 but wrong on W024/W025; PARTIAL contains only W023.
        """
        scene_map = {
            "23": "Warehouse_023", "24": "Warehouse_024", "25": "Warehouse_025",
        }
        n_frames = 6

        def perfect(scene):
            return [
                _aicity_row(scene=scene, object_id=1, frame_id=f, x=float(f), y=0.0)
                for f in range(n_frames)
            ]

        def wrong(scene):
            # Same cardinality but 1000 m away -> never overlaps GT -> HOTA 0.
            return [
                _aicity_row(scene=scene, object_id=1, frame_id=f, x=1000.0 + f, y=0.0)
                for f in range(n_frames)
            ]

        gt = perfect("23") + perfect("24") + perfect("25")
        full = perfect("23") + wrong("24") + wrong("25")
        partial = perfect("23")  # W024 + W025 omitted entirely

        gt_p = tmp_path / "gt.txt"
        full_p = tmp_path / "full.txt"
        part_p = tmp_path / "partial.txt"
        _write_lines(gt_p, gt)
        _write_lines(full_p, full)
        _write_lines(part_p, partial)

        def _final_hota(pred_path, out_name):
            res = run_aicity_mtmc_evaluation(
                ground_truth_file=str(gt_p),
                prediction_file=str(pred_path),
                scene_id_to_name=scene_map,
                output_dir=str(tmp_path / out_name),
                num_cores=1,
                num_frames_to_eval=n_frames,
                eval_type="bbox",
                fps=10.0,
                quiet=True,
            )
            return res["final"]["HOTA"]

        full_hota = _final_hota(full_p, "out_full")
        partial_hota = _final_hota(part_p, "out_partial")

        # The two omitted warehouses are scored 0 with full weight, so the
        # partial submission cannot beat the full one...
        assert partial_hota <= full_hota + 1e-9
        # ...and cannot reach a perfect score just by dropping hard scenes.
        assert partial_hota < 1.0 - 1e-6

    def test_run_with_default_output_dir_uses_tempdir(self, tmp_path, _force_sequential_trackeval):
        """Passing ``output_dir=None`` triggers the tempfile branch.
        The orchestrator should still return a valid results dict."""
        gt, pred = _write_minimal_gt_and_pred(tmp_path, n_frames=4)
        results = run_aicity_mtmc_evaluation(
            ground_truth_file=gt,
            prediction_file=pred,
            scene_id_to_name=SCENE_MAP,
            output_dir=None,  # -> tempdir branch
            num_cores=1,
            num_frames_to_eval=100,
            eval_type="bbox",
            fps=10.0,
            quiet=True,
        )
        assert results["final"]["HOTA"] >= 0.0  # ran to completion

    def test_run_with_location_eval_type_uses_3d_location_dataset(self, tmp_path, _force_sequential_trackeval):
        """``eval_type='location'`` selects the centre-distance variant
        of the bundled dataset adapter."""
        gt, pred = _write_minimal_gt_and_pred(tmp_path, n_frames=3)
        results = run_aicity_mtmc_evaluation(
            ground_truth_file=gt,
            prediction_file=pred,
            scene_id_to_name=SCENE_MAP,
            output_dir=str(tmp_path / "out"),
            num_cores=1,
            num_frames_to_eval=100,
            eval_type="location",
            fps=10.0,
            quiet=True,
        )
        assert results["eval_type"] == "location"

    def test_raises_when_scene_map_is_empty(self, tmp_path):
        gt, pred = _write_minimal_gt_and_pred(tmp_path, n_frames=2)
        with pytest.raises(ValueError, match="scene_id_to_name is empty"):
            run_aicity_mtmc_evaluation(
                ground_truth_file=gt,
                prediction_file=pred,
                scene_id_to_name={},
                num_frames_to_eval=100,
            )

    def test_run_writes_summary_json_when_save_called(self, tmp_path, _force_sequential_trackeval):
        """End-to-end + ``save_aicity_mtmc_results`` writes the canonical
        ``aicity_mtmc_hota_summary.json`` with metrics in 0-100 scale."""
        from spatialai_data_utils.eval.tracking.aicity_mtmc_eval import (
            save_aicity_mtmc_results,
        )

        gt, pred = _write_minimal_gt_and_pred(tmp_path, n_frames=3)
        out_dir = tmp_path / "out"
        results = run_aicity_mtmc_evaluation(
            ground_truth_file=gt,
            prediction_file=pred,
            scene_id_to_name=SCENE_MAP,
            output_dir=str(out_dir),
            num_cores=1,
            num_frames_to_eval=100,
            eval_type="bbox",
            fps=10.0,
            quiet=True,
        )
        path = save_aicity_mtmc_results(results, str(out_dir))
        with open(path) as f:
            payload = json.load(f)
        # In-disk metrics scaled to 0-100.
        assert 0.0 <= payload["final"]["HOTA"] <= 100.0
