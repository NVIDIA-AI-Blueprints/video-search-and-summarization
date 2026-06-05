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

"""Coverage supplement for ``eval.tracking.hota.hota_eval`` — pins the
``evaluate_hota`` orchestrator end-to-end with ``_run_trackeval_for_class``
stubbed, plus its validation and verbose-log + skip branches:

* invalid ``eval_dist_fcn`` raise,
* ``verbose=True`` header / per-class status logs,
* ``valid_flag=False`` annotation skip,
* annotation ``name not in class_names`` skip,
* pred ``token not in scene_token_to_frame_id`` skip,
* pred ``track_name not in class_names`` skip,
* no-GT class -> ``skipped (no GT)`` branch + None result,
* successful per-class log,
* exception inside ``_run_trackeval_for_class`` -> warning + None,
* ``_print_summary_table`` body for both happy + no-valid-classes paths.
"""

import json
import logging
import os

import pytest

from spatialai_data_utils.eval.tracking.hota import hota_eval
from spatialai_data_utils.eval.tracking.hota.hota_eval import (
    HOTA_FIELDS,
    _print_summary_table,
    evaluate_hota,
)

# --- additional imports for migrated chunks ---
import tempfile
import numpy as np


# ---------------------------------------------------------------------------
# Validation: invalid eval_dist_fcn
# ---------------------------------------------------------------------------


def test_evaluate_hota_raises_on_invalid_eval_dist_fcn(tmp_path):
    with pytest.raises(ValueError, match="Invalid eval_dist_fcn"):
        evaluate_hota(
            data_infos=[], result_path=str(tmp_path / "r.json"),
            output_dir=str(tmp_path / "out"),
            class_names=["person"],
            eval_dist_fcn="bogus",
        )


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _fake_run_trackeval(class_name, class_dir, tracker_name, eval_dist_fcn):
    """Stub for ``_run_trackeval_for_class`` — returns a constant
    HOTA-shaped dict per class."""
    return {f: 0.5 for f in HOTA_FIELDS}


def _data_infos(scene="sceneA", n_frames=2):
    return [
        {
            "scene_name": scene,
            "token": f"{scene}__{f}",
            "frame_idx": f,
            "gt_boxes": [[0.0, 0.0, 0.0, 1.0, 1.0, 1.8, 0.0]],
            "gt_names": ["person"],
            "instance_inds": [1],
            "valid_flag": [True],
        }
        for f in range(n_frames)
    ]


def _pred_results(scene="sceneA", n_frames=2):
    out = {}
    for f in range(n_frames):
        out[f"{scene}__{f}"] = [{
            "translation": [0.0, 0.0, 0.0],
            "size": [1.0, 1.0, 1.8],
            "rotation": [1.0, 0.0, 0.0, 0.0],
            "tracking_id": "1",
            "tracking_name": "person",
        }]
    return out


def _write_pred(path, pred_results):
    path.write_text(json.dumps({"results": pred_results}))


# ---------------------------------------------------------------------------
# Happy-path end-to-end (verbose=True drives logs + summary table)
# ---------------------------------------------------------------------------


def test_evaluate_hota_end_to_end_verbose(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(hota_eval, "_run_trackeval_for_class", _fake_run_trackeval)
    pred_path = tmp_path / "results.json"
    _write_pred(pred_path, _pred_results())
    out_dir = tmp_path / "out"

    with caplog.at_level(logging.INFO):
        results = evaluate_hota(
            data_infos=_data_infos(),
            result_path=str(pred_path),
            output_dir=str(out_dir),
            class_names=["person"],
            verbose=True,  # ← exercises log branches + summary table
        )

    assert set(results.keys()) == {"per_class", "average"}
    assert results["per_class"]["person"]["HOTA"] == pytest.approx(0.5)
    # average across one valid class
    assert results["average"]["HOTA"] == pytest.approx(0.5)
    # JSON summary file landed.
    assert (out_dir / "hota_metrics_summary.json").is_file()
    # Verbose header + per-class status + saved-path logs.
    assert "HOTA Tracking Evaluation" in caplog.text
    assert "person" in caplog.text
    assert "HOTA metrics saved to" in caplog.text


# ---------------------------------------------------------------------------
# Branches: token / class skip, valid_flag, no-GT class
# ---------------------------------------------------------------------------


def test_evaluate_hota_skips_invalid_annotations_and_unknown_classes(
    tmp_path, monkeypatch,
):
    """Drive a fixture where:
    - one annotation has valid_flag=False (skip line 187),
    - one annotation has gt_name='unknown' (not in class_names; line 190),
    - one pred token is missing from scene_token_to_frame_id (line 202),
    - one pred track_name is 'unknown' (not in class_names; line 207)."""
    monkeypatch.setattr(hota_eval, "_run_trackeval_for_class", _fake_run_trackeval)
    data_infos = [
        {
            "scene_name": "sceneA", "token": "sceneA__0", "frame_idx": 0,
            "gt_boxes": [
                [0.0, 0.0, 0.0, 1.0, 1.0, 1.8, 0.0],  # valid
                [1.0, 0.0, 0.0, 1.0, 1.0, 1.8, 0.0],  # valid_flag=False -> skip
                [2.0, 0.0, 0.0, 1.0, 1.0, 1.8, 0.0],  # name=None -> skip
                [3.0, 0.0, 0.0, 1.0, 1.0, 1.8, 0.0],  # unknown class -> skip
            ],
            "gt_names": ["person", "person", None, "unknown_class"],
            "instance_inds": [1, 2, 3, 4],
            "valid_flag": [True, False, True, True],
        },
    ]
    pred = {
        "sceneA__0": [
            {"translation": [0, 0, 0], "size": [1, 1, 1.8],
              "rotation": [1, 0, 0, 0], "tracking_id": "1",
              "tracking_name": "person"},
            {"translation": [0, 0, 0], "size": [1, 1, 1.8],
              "rotation": [1, 0, 0, 0], "tracking_id": "9",
              "tracking_name": "unknown_class"},  # skipped
        ],
        "non_existent_token": [  # skipped (line 202)
            {"translation": [0, 0, 0], "size": [1, 1, 1.8],
              "rotation": [1, 0, 0, 0], "tracking_id": "1",
              "tracking_name": "person"},
        ],
    }
    pred_path = tmp_path / "results.json"
    _write_pred(pred_path, pred)
    results = evaluate_hota(
        data_infos=data_infos, result_path=str(pred_path),
        output_dir=str(tmp_path / "out"),
        class_names=["person"], verbose=False,
    )
    assert results["per_class"]["person"]["HOTA"] == pytest.approx(0.5)


def test_evaluate_hota_logs_skipped_class_with_no_gt(
    tmp_path, monkeypatch, caplog,
):
    """Drive a fixture where the ``class_names`` list contains a class
    that doesn't appear in any GT annotation — the orchestrator marks
    it as ``None`` and logs ``skipped (no GT)``."""
    monkeypatch.setattr(hota_eval, "_run_trackeval_for_class", _fake_run_trackeval)
    pred_path = tmp_path / "results.json"
    _write_pred(pred_path, _pred_results())
    with caplog.at_level(logging.INFO):
        results = evaluate_hota(
            data_infos=_data_infos(),
            result_path=str(pred_path),
            output_dir=str(tmp_path / "out"),
            class_names=["person", "forklift"],  # forklift not in GT
            verbose=True,
        )
    assert results["per_class"]["forklift"] is None
    assert "skipped (no GT)" in caplog.text


# ---------------------------------------------------------------------------
# Failure path: _run_trackeval_for_class raises
# ---------------------------------------------------------------------------


def test_evaluate_hota_records_none_when_trackeval_raises(
    tmp_path, monkeypatch, caplog,
):
    """Errors inside ``_run_trackeval_for_class`` are caught and the
    class result is set to ``None`` with a warning log."""
    def _boom(*args, **kwargs):
        raise RuntimeError("trackeval blew up")
    monkeypatch.setattr(hota_eval, "_run_trackeval_for_class", _boom)
    pred_path = tmp_path / "results.json"
    _write_pred(pred_path, _pred_results())
    with caplog.at_level(logging.WARNING):
        results = evaluate_hota(
            data_infos=_data_infos(), result_path=str(pred_path),
            output_dir=str(tmp_path / "out"),
            class_names=["person"], verbose=True,
        )
    assert results["per_class"]["person"] is None
    assert "HOTA evaluation failed" in caplog.text


# ---------------------------------------------------------------------------
# _print_summary_table — no-valid-classes branch
# ---------------------------------------------------------------------------


def test_print_summary_table_handles_no_valid_classes(caplog):
    """If every class is None, the summary table prints
    ``N/A (no valid classes)`` for the average row."""
    results = {"per_class": {"person": None, "forklift": None}, "average": {}}
    with caplog.at_level(logging.INFO):
        _print_summary_table(results, ["person", "forklift"],
                              no_gt_classes={"person"})
    assert "N/A (no valid classes)" in caplog.text
    # 'no GT' reason rendered for the person row
    assert "(no GT)" in caplog.text
    # 'FAILED' reason rendered for the forklift row
    assert "(FAILED)" in caplog.text


class TestCenterDistanceHOTA:
    """Tests for evaluate_hota with eval_dist_fcn='center_distance' (Euclidean center-distance matching)."""

    @staticmethod
    def _make_data_infos(scenes):
        """Build data_infos from a dict of {scene_name: [frame_dicts]}.

        Each frame_dict has keys: token, gt_boxes, gt_names, instance_inds,
        and optionally valid_flag.
        """
        data_infos = []
        for scene_name, frames in scenes.items():
            for frame in frames:
                frame["scene_name"] = scene_name
                data_infos.append(frame)
        return data_infos

    @staticmethod
    def _make_pred_json(predictions, tmp_dir):
        """Build results_nusc.json from {token: [{translation, size, rotation, tracking_name, tracking_id}]}."""
        results = {}
        for token, annos in predictions.items():
            serialized = []
            for a in annos:
                serialized.append({
                    "translation": a["translation"],
                    "size": a["size"],
                    "rotation": a.get("rotation", [1, 0, 0, 0]),
                    "tracking_name": a["tracking_name"],
                    "tracking_id": a["tracking_id"],
                    "tracking_score": a.get("tracking_score", 0.9),
                })
            results[token] = serialized
        data = {
            "results": results,
            "meta": {"use_camera": True, "use_lidar": False, "use_radar": False,
                     "use_map": False, "use_external": False},
        }
        path = os.path.join(tmp_dir, "results_nusc.json")
        with open(path, "w") as f:
            json.dump(data, f)
        return path

    def test_perfect_tracking_location(self):
        """Perfect tracking (identical GT and predictions) → HOTA ≈ 1."""
        num_frames = 5
        tokens = [f"tok_{i}" for i in range(num_frames)]

        scenes = {"scene_0": []}
        predictions = {}
        for tok in tokens:
            scenes["scene_0"].append({
                "token": tok,
                "gt_boxes": np.array([[0, 0, 0, 1, 1, 1, 0],
                                      [5, 5, 0, 1, 1, 1, 0]], dtype=np.float64),
                "gt_names": ["person", "person"],
                "instance_inds": [0, 1],
                "valid_flag": [True, True],
            })
            predictions[tok] = [
                {"translation": [0, 0, 0], "size": [1, 1, 1],
                 "tracking_name": "person", "tracking_id": "0"},
                {"translation": [5, 5, 0], "size": [1, 1, 1],
                 "tracking_name": "person", "tracking_id": "1"},
            ]

        data_infos = self._make_data_infos(scenes)

        with tempfile.TemporaryDirectory() as tmp_dir:
            result_path = self._make_pred_json(predictions, tmp_dir)
            results = evaluate_hota(
                data_infos=data_infos,
                result_path=result_path,
                output_dir=tmp_dir,
                class_names=["person"],
                eval_dist_fcn="center_distance",
                verbose=False,
            )

        assert "per_class" in results
        assert "average" in results
        assert results["per_class"]["person"] is not None
        assert results["average"]["HOTA"] > 0.9

    def test_no_predictions_location(self):
        """No predictions → HOTA = 0."""
        tokens = ["tok_0", "tok_1"]
        scenes = {"scene_0": []}
        predictions = {}
        for tok in tokens:
            scenes["scene_0"].append({
                "token": tok,
                "gt_boxes": np.array([[0, 0, 0, 1, 1, 1, 0]], dtype=np.float64),
                "gt_names": ["person"],
                "instance_inds": [0],
                "valid_flag": [True],
            })
            predictions[tok] = []

        data_infos = self._make_data_infos(scenes)

        with tempfile.TemporaryDirectory() as tmp_dir:
            result_path = self._make_pred_json(predictions, tmp_dir)
            results = evaluate_hota(
                data_infos=data_infos,
                result_path=result_path,
                output_dir=tmp_dir,
                class_names=["person"],
                eval_dist_fcn="center_distance",
                verbose=False,
            )

        assert results["average"]["HOTA"] < 1e-6

    def test_far_predictions_location(self):
        """Predictions very far from GT → low HOTA (similarity ≈ 0)."""
        tokens = ["tok_0", "tok_1"]
        scenes = {"scene_0": []}
        predictions = {}
        for tok in tokens:
            scenes["scene_0"].append({
                "token": tok,
                "gt_boxes": np.array([[0, 0, 0, 1, 1, 1, 0]], dtype=np.float64),
                "gt_names": ["person"],
                "instance_inds": [0],
            })
            predictions[tok] = [
                {"translation": [100, 100, 0], "size": [1, 1, 1],
                 "tracking_name": "person", "tracking_id": "0"},
            ]

        data_infos = self._make_data_infos(scenes)

        with tempfile.TemporaryDirectory() as tmp_dir:
            result_path = self._make_pred_json(predictions, tmp_dir)
            results = evaluate_hota(
                data_infos=data_infos,
                result_path=result_path,
                output_dir=tmp_dir,
                class_names=["person"],
                eval_dist_fcn="center_distance",
                verbose=False,
            )

        assert results["average"]["HOTA"] < 0.1

    def test_id_switch_location(self):
        """Tracker swaps IDs midway → HOTA < 1 even with perfect detection."""
        num_frames = 4
        tokens = [f"tok_{i}" for i in range(num_frames)]
        scenes = {"scene_0": []}
        predictions = {}

        for i, tok in enumerate(tokens):
            scenes["scene_0"].append({
                "token": tok,
                "gt_boxes": np.array([[0, 0, 0, 1, 1, 1, 0],
                                      [5, 0, 0, 1, 1, 1, 0]], dtype=np.float64),
                "gt_names": ["person", "person"],
                "instance_inds": [0, 1],
            })
            # Swap IDs at midpoint
            if i < 2:
                predictions[tok] = [
                    {"translation": [0, 0, 0], "size": [1, 1, 1],
                     "tracking_name": "person", "tracking_id": "0"},
                    {"translation": [5, 0, 0], "size": [1, 1, 1],
                     "tracking_name": "person", "tracking_id": "1"},
                ]
            else:
                predictions[tok] = [
                    {"translation": [0, 0, 0], "size": [1, 1, 1],
                     "tracking_name": "person", "tracking_id": "1"},
                    {"translation": [5, 0, 0], "size": [1, 1, 1],
                     "tracking_name": "person", "tracking_id": "0"},
                ]

        data_infos = self._make_data_infos(scenes)

        with tempfile.TemporaryDirectory() as tmp_dir:
            result_path = self._make_pred_json(predictions, tmp_dir)
            results = evaluate_hota(
                data_infos=data_infos,
                result_path=result_path,
                output_dir=tmp_dir,
                class_names=["person"],
                eval_dist_fcn="center_distance",
                verbose=False,
            )

        # DetA should be ~1 (perfect detection), AssA < 1 (ID switch), HOTA < 1
        person_metrics = results["per_class"]["person"]
        assert person_metrics["DetA"] > 0.9
        assert person_metrics["AssA"] < 1.0
        assert person_metrics["HOTA"] < 1.0

    def test_multi_class_location(self):
        """Multiple classes evaluated independently via location matching."""
        tokens = ["tok_0", "tok_1"]
        scenes = {"scene_0": []}
        predictions = {}

        for tok in tokens:
            scenes["scene_0"].append({
                "token": tok,
                "gt_boxes": np.array([[0, 0, 0, 1, 1, 1, 0],
                                      [10, 0, 0, 2, 3, 2, 0]], dtype=np.float64),
                "gt_names": ["person", "forklift"],
                "instance_inds": [0, 1],
            })
            predictions[tok] = [
                {"translation": [0, 0, 0], "size": [1, 1, 1],
                 "tracking_name": "person", "tracking_id": "0"},
                {"translation": [10, 0, 0], "size": [2, 3, 2],
                 "tracking_name": "forklift", "tracking_id": "1"},
            ]

        data_infos = self._make_data_infos(scenes)

        with tempfile.TemporaryDirectory() as tmp_dir:
            result_path = self._make_pred_json(predictions, tmp_dir)
            results = evaluate_hota(
                data_infos=data_infos,
                result_path=result_path,
                output_dir=tmp_dir,
                class_names=["person", "forklift"],
                eval_dist_fcn="center_distance",
                verbose=False,
            )

        assert results["per_class"]["person"] is not None
        assert results["per_class"]["forklift"] is not None
        assert results["per_class"]["person"]["HOTA"] > 0.9
        assert results["per_class"]["forklift"]["HOTA"] > 0.9
        assert results["average"]["HOTA"] > 0.9

    def test_missing_class_in_gt_skipped(self):
        """A class with no GT should be skipped (not in per_class results)."""
        scenes = {"scene_0": [{
            "token": "tok_0",
            "gt_boxes": np.array([[0, 0, 0, 1, 1, 1, 0]], dtype=np.float64),
            "gt_names": ["person"],
            "instance_inds": [0],
        }]}
        predictions = {"tok_0": [
            {"translation": [0, 0, 0], "size": [1, 1, 1],
             "tracking_name": "person", "tracking_id": "0"},
        ]}
        data_infos = self._make_data_infos(scenes)

        with tempfile.TemporaryDirectory() as tmp_dir:
            result_path = self._make_pred_json(predictions, tmp_dir)
            results = evaluate_hota(
                data_infos=data_infos,
                result_path=result_path,
                output_dir=tmp_dir,
                class_names=["person", "forklift"],
                eval_dist_fcn="center_distance",
                verbose=False,
            )

        # forklift had no GT → should be None in per_class results
        assert results["per_class"]["forklift"] is None
        # Average should still be computed from the valid class only
        assert results["average"]["HOTA"] > 0.9

    def test_location_vs_bbox_uses_different_dataset(self):
        """eval_dist_fcn='center_distance' should use center-distance similarity,
        producing different results than 'iou_3d' for offset predictions."""
        tokens = ["tok_0", "tok_1"]
        scenes = {"scene_0": []}
        predictions = {}

        for tok in tokens:
            scenes["scene_0"].append({
                "token": tok,
                "gt_boxes": np.array([[0, 0, 0, 2, 2, 2, 0]], dtype=np.float64),
                "gt_names": ["person"],
                "instance_inds": [0],
            })
            # Prediction is offset by a small amount: same center-distance,
            # but 3D IoU differs from distance-based similarity
            predictions[tok] = [
                {"translation": [0.3, 0, 0], "size": [2, 2, 2],
                 "tracking_name": "person", "tracking_id": "0"},
            ]

        data_infos = self._make_data_infos(scenes)

        with tempfile.TemporaryDirectory() as tmp_dir:
            result_path = self._make_pred_json(predictions, tmp_dir)
            results_loc = evaluate_hota(
                data_infos=data_infos,
                result_path=result_path,
                output_dir=os.path.join(tmp_dir, "loc"),
                class_names=["person"],
                eval_dist_fcn="center_distance",
                verbose=False,
            )
            results_bbox = evaluate_hota(
                data_infos=data_infos,
                result_path=result_path,
                output_dir=os.path.join(tmp_dir, "bbox"),
                class_names=["person"],
                eval_dist_fcn="iou_3d",
                verbose=False,
            )

        # Both should produce valid results
        assert results_loc["per_class"]["person"] is not None
        assert results_bbox["per_class"]["person"] is not None
        # The LocA values should differ because they use different similarity functions
        loc_loca = results_loc["per_class"]["person"]["LocA"]
        bbox_loca = results_bbox["per_class"]["person"]["LocA"]
        # With a 0.3m offset on a 2m box, center distance similarity and IoU similarity differ
        assert loc_loca != pytest.approx(bbox_loca, abs=0.01)

    def test_hota_output_fields(self):
        """evaluate_hota should return all expected HOTA fields."""
        scenes = {"scene_0": [{
            "token": "tok_0",
            "gt_boxes": np.array([[0, 0, 0, 1, 1, 1, 0]], dtype=np.float64),
            "gt_names": ["person"],
            "instance_inds": [0],
        }]}
        predictions = {"tok_0": [
            {"translation": [0, 0, 0], "size": [1, 1, 1],
             "tracking_name": "person", "tracking_id": "0"},
        ]}
        data_infos = self._make_data_infos(scenes)
        expected_fields = ["HOTA", "DetA", "AssA", "LocA", "DetRe", "DetPr",
                           "AssRe", "AssPr", "OWTA"]

        with tempfile.TemporaryDirectory() as tmp_dir:
            result_path = self._make_pred_json(predictions, tmp_dir)
            results = evaluate_hota(
                data_infos=data_infos,
                result_path=result_path,
                output_dir=tmp_dir,
                class_names=["person"],
                eval_dist_fcn="center_distance",
                verbose=False,
            )

        for field in expected_fields:
            assert field in results["per_class"]["person"], f"Missing field: {field}"
            assert field in results["average"], f"Missing average field: {field}"


# ===================================================================
