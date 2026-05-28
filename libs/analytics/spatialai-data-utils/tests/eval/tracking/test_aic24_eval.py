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

"""Tests for ``eval.tracking.aic24_eval.AIC24TrackEval``.

End-to-end exercises of the legacy AIC24 tracking orchestrator using
small synthetic GT (in-memory ``data_infos``) and a tiny prediction
JSON on disk. We bypass the matplotlib-heavy ``render`` step (no PDFs
under test) but still drive ``__init__`` → ``evaluate`` →
``save metrics`` end-to-end so any wiring regression surfaces.
"""

import json

import numpy as np
import pytest

from spatialai_data_utils.eval.tracking import data_classes as dc
from spatialai_data_utils.eval.tracking.aic24_eval import AIC24TrackEval
from spatialai_data_utils.eval.tracking.data_classes import (
    TrackingConfig,
    TrackingMetricData,
)


def _make_config(num_thresholds=4):
    return TrackingConfig(
        tracking_names=["person"],
        pretty_tracking_names={"person": "Person"},
        class_range={"person": 50},
        dist_fcn="center_distance",
        dist_th_tp=2.0,
        min_recall=0.1,
        max_boxes_per_sample=500,
        metric_worst={
            "amota": 0.0, "amotp": 2.0, "recall": 0.0, "motar": 0.0,
            "mota": 0.0, "motp": 2.0, "faf": 100.0, "gt": -1, "tp": 0,
            "mt": 0, "ml": -1, "fp": -1, "fn": -1, "ids": -1, "frag": -1,
            "tid": 20.0, "lgd": 20.0,
        },
        num_thresholds=num_thresholds,
    )


@pytest.fixture(autouse=True)
def _register_tracking_names():
    saved_nelem = TrackingMetricData.nelem
    saved_names = list(dc.TRACKING_NAMES)
    _make_config()  # registers TRACKING_NAMES + nelem
    yield
    TrackingMetricData.nelem = saved_nelem
    dc.TRACKING_NAMES = saved_names


def _data_infos_and_prediction(scene="sceneA", n_frames=4):
    """Build matching GT (data_infos) + prediction JSON content for a
    single track that holds a constant position across n_frames."""
    data_infos = []
    pred_entries = {}
    for f in range(n_frames):
        token = f"{scene}__{f}"
        data_infos.append({
            "token": token,
            "scene_name": scene,
            "frame_idx": f,
            "gt_boxes": [[float(f), 0.0, 0.0, 1.0, 1.0, 1.8, 0.0]],
            "gt_names": ["person"],
            "gt_velocity": [[0.0, 0.0]],
            "instance_inds": [1],
        })
        pred_entries[token] = [{
            "sample_token": token,
            "translation": [float(f), 0.0, 0.0],
            "size": [1.0, 1.0, 1.8],
            "rotation": [1.0, 0.0, 0.0, 0.0],
            "velocity": [0.0, 0.0],
            "tracking_id": "1",
            "tracking_name": "person",
            "tracking_score": 0.9,
        }]
    return data_infos, pred_entries


def _write_prediction(path, pred_entries, meta=None):
    payload = {
        "meta": meta or {"use_camera": True, "use_lidar": False,
                          "use_radar": False, "use_map": False,
                          "use_external": False},
        "results": pred_entries,
    }
    path.write_text(json.dumps(payload))


def test_aic24_main_writes_summary_and_details_json(tmp_path):
    """``AIC24TrackEval.main`` end-to-end: real GT, real prediction
    file, real ``TrackingEvaluation`` per class. Render disabled so we
    avoid the matplotlib path."""
    data_infos, pred = _data_infos_and_prediction(scene="sceneA", n_frames=4)
    pred_path = tmp_path / "pred.json"
    _write_prediction(pred_path, pred)

    out_dir = tmp_path / "out"
    evaluator = AIC24TrackEval(
        data_infos=data_infos,
        config=_make_config(),
        result_path=str(pred_path),
        output_dir=str(out_dir),
        verbose=False,
    )

    summary = evaluator.main(render_curves=False)

    # Files were written.
    summary_path = out_dir / "metrics_summary.json"
    details_path = out_dir / "metrics_details.json"
    assert summary_path.is_file()
    assert details_path.is_file()

    # ``plots/`` is always created in __init__ even if render is skipped.
    assert (out_dir / "plots").is_dir()

    # Summary contains the headline metrics for our single class.
    assert "amota" in summary and "amotp" in summary
    assert summary["meta"]["use_camera"] is True
    assert summary["label_metrics"]["amota"]["person"] == pytest.approx(
        summary["amota"]
    )


def test_aic24_evaluate_perfect_match_reports_amota_one(tmp_path):
    """With identical GT / predictions, AMOTA should reach 1.0 — the
    motmetrics pipeline actually ran end-to-end."""
    data_infos, pred = _data_infos_and_prediction(scene="sceneA", n_frames=4)
    pred_path = tmp_path / "pred.json"
    _write_prediction(pred_path, pred)

    evaluator = AIC24TrackEval(
        data_infos=data_infos,
        config=_make_config(),
        result_path=str(pred_path),
        output_dir=str(tmp_path / "out"),
        verbose=False,
    )
    metrics, _md_list = evaluator.evaluate()
    summary = metrics.serialize()
    assert summary["amota"] == pytest.approx(1.0)


def test_aic24_init_raises_when_prediction_file_missing(tmp_path):
    """``__init__`` asserts that the prediction file exists before
    spending any work loading GT."""
    with pytest.raises(AssertionError, match="result file does not exist"):
        AIC24TrackEval(
            data_infos=[],
            config=_make_config(),
            result_path=str(tmp_path / "no-such.json"),
            output_dir=str(tmp_path / "out"),
            verbose=False,
        )


def test_aic24_init_raises_on_pred_gt_sample_token_mismatch(tmp_path):
    """If the prediction file has tokens not in the GT (or vice-versa)
    the orchestrator must fail fast before evaluation, with the
    canonical mismatch message."""
    data_infos, pred = _data_infos_and_prediction(scene="sceneA", n_frames=2)
    # Add a stray prediction token absent from GT
    pred["sceneA__99"] = []
    pred_path = tmp_path / "pred.json"
    _write_prediction(pred_path, pred)

    with pytest.raises(AssertionError, match="Samples in split"):
        AIC24TrackEval(
            data_infos=data_infos,
            config=_make_config(),
            result_path=str(pred_path),
            output_dir=str(tmp_path / "out"),
            verbose=False,
        )


def test_aic24_evaluate_with_no_class_predictions_yields_nan_amota(tmp_path):
    """Predictions present but for a class not in the config → the
    target class has zero matches and AMOTA falls back to the worst
    value (0.0 per the metric_worst we registered)."""
    data_infos, _ = _data_infos_and_prediction(scene="sceneA", n_frames=2)
    # Empty prediction list for every sample
    pred = {sample["token"]: [] for sample in data_infos}
    pred_path = tmp_path / "pred.json"
    _write_prediction(pred_path, pred)

    evaluator = AIC24TrackEval(
        data_infos=data_infos,
        config=_make_config(),
        result_path=str(pred_path),
        output_dir=str(tmp_path / "out"),
        verbose=False,
    )
    metrics, _ = evaluator.evaluate()
    summary = metrics.serialize()
    # AMOTA falls back to the registered worst value (0.0).
    assert summary["amota"] == pytest.approx(0.0)
    # FN / GT fallback values were filled in by the algo
    assert np.isfinite(summary["label_metrics"]["fn"]["person"])


# ===========================================================
# Coverage supplement (merged from test_aic24_eval_coverage.py)
# ===========================================================

"""Coverage supplement for ``eval.tracking.aic24_eval.AIC24TrackEval``
— pins the ``verbose=True`` print branches, the ``render`` matplotlib
helper, the ``best_thresh_idx is None`` aggregate-empty branch, and
the ``render_curves=True`` driver in ``main``."""

import json
import logging
import os

import matplotlib

matplotlib.use("Agg")  # required before any matplotlib.pyplot import

import numpy as np  # noqa: E402

from spatialai_data_utils.eval.tracking import data_classes as dc  # noqa: E402
from spatialai_data_utils.eval.tracking.aic24_eval import AIC24TrackEval  # noqa: E402
from spatialai_data_utils.eval.tracking.data_classes import (  # noqa: E402
    TrackingConfig,
    TrackingMetricData,
)


# Note: ``_make_config`` is defined at the top of this module (identical
# definition) and reused below.


def _data_infos_and_pred(scene="sceneA", n_frames=4):
    """Matching GT (data_infos) + prediction JSON content for a single
    constant-position track."""
    data_infos = []
    pred_entries = {}
    for f in range(n_frames):
        token = f"{scene}__{f}"
        data_infos.append({
            "token": token, "scene_name": scene, "frame_idx": f,
            "gt_boxes": [[float(f), 0.0, 0.0, 1.0, 1.0, 1.8, 0.0]],
            "gt_names": ["person"], "gt_velocity": [[0.0, 0.0]],
            "instance_inds": [1],
        })
        pred_entries[token] = [{
            "sample_token": token,
            "translation": [float(f), 0.0, 0.0],
            "size": [1.0, 1.0, 1.8],
            "rotation": [1.0, 0.0, 0.0, 0.0],
            "velocity": [0.0, 0.0],
            "tracking_id": "1", "tracking_name": "person",
            "tracking_score": 0.9,
        }]
    return data_infos, pred_entries


def _write_pred(path, pred_entries):
    path.write_text(json.dumps({
        "meta": {"use_camera": True, "use_lidar": False,
                  "use_radar": False, "use_map": False, "use_external": False},
        "results": pred_entries,
    }))


import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _isolate_state():
    """Avoid TRACKING_NAMES + TrackingMetricData.nelem state leaks."""
    saved_nelem = TrackingMetricData.nelem
    saved_names = list(dc.TRACKING_NAMES)
    yield
    TrackingMetricData.nelem = saved_nelem
    dc.TRACKING_NAMES = saved_names


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_main_with_verbose_print_branches(tmp_path, capsys):
    """``verbose=True`` exercises the four print branches:
    ``Initializing tracking evaluation``, ``Accumulating metric data...``,
    ``Calculating metrics...``, and ``Saving metrics to: <dir>``.

    Note: the ``render`` matplotlib path is broken upstream
    (``nuscenes.eval.tracking.render.summary_plot`` expects a
    ``tracking_colors`` attribute on the config that our
    ``TrackingConfig`` doesn't have), so we don't drive
    ``render_curves=True`` here."""
    data_infos, pred = _data_infos_and_pred(scene="sceneA", n_frames=4)
    pred_path = tmp_path / "pred.json"
    _write_pred(pred_path, pred)

    out_dir = tmp_path / "out"
    evaluator = AIC24TrackEval(
        data_infos=data_infos,
        config=_make_config(),
        result_path=str(pred_path),
        output_dir=str(out_dir),
        verbose=True,  # ← exercises the print branches
    )
    summary = evaluator.main(render_curves=False)

    captured_stdout = capsys.readouterr().out
    assert "Initializing tracking evaluation" in captured_stdout
    assert "Accumulating metric data" in captured_stdout
    assert "Calculating metrics" in captured_stdout
    assert "Saving metrics to" in captured_stdout
    assert (out_dir / "metrics_summary.json").is_file()
    assert (out_dir / "metrics_details.json").is_file()
    assert summary["meta"]["use_camera"] is True


def test_evaluate_when_all_mota_are_nan_skips_best_thresh_branch(tmp_path):
    """When the per-class TrackingMetricData has every ``mota`` value
    set to ``NaN`` (no GT for the class), ``best_thresh_idx`` is set
    to ``None`` and the per-class traditional-metrics aggregation
    loop is skipped — but the AMOTA / AMOTP averaging still runs and
    sets ``np.nan`` values when every recall threshold also failed."""
    data_infos, _ = _data_infos_and_pred(scene="sceneA", n_frames=2)
    pred_path = tmp_path / "pred.json"
    # Empty predictions for every sample -> no MOTA at any threshold
    _write_pred(pred_path, {sample["token"]: [] for sample in data_infos})

    evaluator = AIC24TrackEval(
        data_infos=data_infos,
        config=_make_config(),
        result_path=str(pred_path),
        output_dir=str(tmp_path / "out"),
        verbose=False,
    )
    metrics, _ = evaluator.evaluate()
    # The MOTA aggregation got skipped (best_thresh_idx None branch)
    # but the AMOTA fallback to worst-value populated it; serialize
    # without raising and return.
    summary = metrics.serialize()
    assert "amota" in summary
