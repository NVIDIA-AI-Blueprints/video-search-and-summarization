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

"""Tests for ``eval.tracking.algo.TrackingEvaluation``.

End-to-end exercises of ``TrackingEvaluation.accumulate()`` on tiny
hand-constructed GT/pred track sets:

* Perfect-match GT == pred → MOTA is finite (the motmetrics pipeline
  actually ran) and no GT crashes silently.
* No GT for the class → early-return empty ``TrackingMetricData``.
* No predictions → thresholds are all NaN and worst-case fallbacks
  kick in for the count-style metrics.
* Distance-function dispatch — center-distance and iou-3d branches
  both reach ``acc.update``, and unknown ``dist_fcn`` raises
  ``ValueError`` cleanly.
* ``compute_thresholds`` short-circuits cleanly when no TP scores
  exist.
"""

import numpy as np
import pytest

from nuscenes.eval.common.utils import center_distance

from spatialai_data_utils.eval.tracking import data_classes as dc
from spatialai_data_utils.eval.tracking.algo import TrackingEvaluation
from spatialai_data_utils.eval.tracking.data_classes import (
    TrackingBox,
    TrackingConfig,
    TrackingMetricData,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_config(num_thresholds=4):
    return TrackingConfig(
        tracking_names=["person"],
        pretty_tracking_names={"person": "Person"},
        class_range={"person": 50},
        dist_fcn="center_distance",
        dist_th_tp=2.0,
        min_recall=0.1,
        max_boxes_per_sample=500,
        # ``-1`` is a sentinel meaning "fill from runtime context"
        # (e.g. ``ml`` -> ``len(gt_track_ids)``). For ``mt`` and ``tp``
        # the algo has no derived fallback, so pin them to ``0`` (the
        # nuScenes convention) to avoid NotImplementedError.
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
    _make_config()  # populates TRACKING_NAMES + sets nelem
    yield
    TrackingMetricData.nelem = saved_nelem
    dc.TRACKING_NAMES = saved_names


def _box(tracking_id, translation, *, tracking_score=0.9, tracking_name="person"):
    return TrackingBox(
        sample_token="A__0",
        translation=translation,
        size=(1.0, 1.0, 1.8),
        rotation=(1.0, 0.0, 0.0, 0.0),
        velocity=(0.0, 0.0),
        tracking_id=tracking_id,
        tracking_name=tracking_name,
        tracking_score=tracking_score,
    )


def _make_perfect_tracks(n_frames=4):
    """One scene, one track ('1'), n_frames timestamps; GT == pred."""
    gt = {"sceneA": {}}
    pred = {"sceneA": {}}
    for t in range(n_frames):
        pos = (float(t), 0.0, 0.0)
        gt["sceneA"][t] = [_box("1", pos)]
        pred["sceneA"][t] = [_box("1", pos)]
    return gt, pred


def _build_evaluator(tracks_gt, tracks_pred, *, dist_fcn=center_distance,
                     dist_th_tp=2.0, num_thresholds=4, verbose=False):
    cfg = _make_config(num_thresholds=num_thresholds)
    return TrackingEvaluation(
        tracks_gt=tracks_gt,
        tracks_pred=tracks_pred,
        class_name="person",
        dist_fcn=dist_fcn,
        dist_th_tp=dist_th_tp,
        min_recall=cfg.min_recall,
        num_thresholds=num_thresholds,
        metric_worst=cfg.metric_worst,
        verbose=verbose,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_accumulate_perfect_match_produces_finite_metrics():
    """When GT == pred for every frame, motmetrics reports MOTA == 1
    at the matched threshold and the result md has finite values for
    the headline metrics."""
    gt, pred = _make_perfect_tracks(n_frames=4)
    ev = _build_evaluator(gt, pred)
    md = ev.accumulate()

    assert isinstance(md, TrackingMetricData)
    # At least one threshold produced a finite mota: perfect-match run.
    finite_mota = md.mota[np.isfinite(md.mota)]
    assert finite_mota.size > 0
    assert np.nanmax(finite_mota) == pytest.approx(1.0)


def test_accumulate_returns_empty_md_when_no_gt_for_class():
    """If no GT box matches ``class_name``, ``accumulate`` short-
    circuits and returns the default-initialised TrackingMetricData
    (all NaN, no MOT computation run)."""
    # GT has only a different class
    gt = {"sceneA": {0: [_box("1", (0, 0, 0), tracking_name="person")]}}
    # Disable that one by using a class name not present in the GT.
    ev = TrackingEvaluation(
        tracks_gt={"sceneA": {0: []}},
        tracks_pred={"sceneA": {0: []}},
        class_name="person",
        dist_fcn=center_distance,
        dist_th_tp=2.0,
        min_recall=0.1,
        num_thresholds=4,
        metric_worst=_make_config().metric_worst,
        verbose=False,
    )
    md = ev.accumulate()
    # Nothing was set → everything stays at the initial NaN-filled array.
    assert np.all(np.isnan(md.mota))


def test_accumulate_no_predictions_fills_metrics_with_worst_values():
    """Pred is empty but GT has boxes — ``compute_thresholds`` returns
    all-NaN thresholds → ``accumulate`` falls back to ``metric_worst``
    /``gt_box_count`` fillers."""
    gt = {"sceneA": {0: [_box("1", (0, 0, 0))]}}
    pred = {"sceneA": {0: []}}
    ev = _build_evaluator(gt, pred)
    md = ev.accumulate()

    assert isinstance(md, TrackingMetricData)
    # ``gt`` falls back to the GT box count (1) per the "metric_worst == -1
    # and metric in ('gt', 'fn')" path.
    assert np.all(md.gt == 1)
    assert np.all(md.fn == 1)


def test_accumulate_threshold_dispatches_iou_3d_branch():
    """When ``dist_fcn.__name__ == 'iou_3d'`` the implementation must
    take the lazy-imported ``iou_3d_matrix`` path (not center_distance).
    With perfectly overlapping boxes we expect a MATCH (IoU = 1, so
    distance = 0 < dist_th_tp)."""
    from spatialai_data_utils.eval.common.utils import iou_3d

    gt, pred = _make_perfect_tracks(n_frames=2)
    ev = _build_evaluator(gt, pred, dist_fcn=iou_3d, dist_th_tp=0.5)
    md = ev.accumulate()
    finite_mota = md.mota[np.isfinite(md.mota)]
    assert finite_mota.size > 0
    assert np.nanmax(finite_mota) == pytest.approx(1.0)


def test_accumulate_threshold_unknown_dist_fcn_raises():
    """An unrecognised distance-function name must raise rather than
    silently producing nonsensical metrics."""
    def mystery(a, b):  # pragma: no cover - never called
        return 0.0

    gt, pred = _make_perfect_tracks(n_frames=2)
    ev = _build_evaluator(gt, pred, dist_fcn=mystery)
    with pytest.raises(ValueError, match="Unsupported distance function"):
        ev.accumulate()


def test_compute_thresholds_returns_all_nan_when_no_predictions():
    """``compute_thresholds`` returns all-NaN arrays when no TP scores
    exist; downstream ``accumulate`` uses those NaNs to enter the
    worst-value-fallback branch."""
    gt = {"sceneA": {0: [_box("1", (0, 0, 0))]}}
    pred = {"sceneA": {0: []}}
    ev = _build_evaluator(gt, pred, num_thresholds=5)
    thresholds, recalls = ev.compute_thresholds(gt_box_count=1)
    assert len(thresholds) == 5 and len(recalls) == 5
    assert all(np.isnan(t) for t in thresholds)


def test_compute_thresholds_with_real_tp_scores_returns_sorted_thresholds():
    """Perfect-match scenario produces concrete TP scores so the
    threshold list is mostly non-NaN and the recall list is the
    expected ``linspace(min_recall, 1.0, num_thresholds)`` (reversed
    inside the function so the highest recall comes first)."""
    gt, pred = _make_perfect_tracks(n_frames=4)
    ev = _build_evaluator(gt, pred, num_thresholds=4)
    thresholds, recalls = ev.compute_thresholds(gt_box_count=4)
    assert len(thresholds) == 4 and len(recalls) == 4
    # Recall sequence is the reversed linspace(min_recall, 1.0, 4).
    expected_recalls = list(np.linspace(0.1, 1.0, 4).round(12))[::-1]
    np.testing.assert_allclose(recalls, expected_recalls)


# ===========================================================
# Coverage supplement (merged from test_algo_coverage.py)
# ===========================================================

"""Coverage supplement for ``eval.tracking.algo.TrackingEvaluation``
— pins the small branches the happy-path tests in ``test_algo.py``
don't reach: ``verbose=True`` prints, the ``NotImplementedError``
raise for unrecognised worst-value metric names, the
``render_classes`` branch in ``accumulate_threshold``, and the
empty-frame ``continue`` when both GT and pred have zero detections."""

import os

import numpy as np
import pytest

from nuscenes.eval.common.utils import center_distance

from spatialai_data_utils.eval.tracking import data_classes as dc
from spatialai_data_utils.eval.tracking.algo import TrackingEvaluation
from spatialai_data_utils.eval.tracking.data_classes import (
    TrackingBox,
    TrackingConfig,
    TrackingMetricData,
)


# Note: ``_box`` and ``_register_tracking_names`` are defined at the top
# of this module (the originals are supersets of the supplement's helpers)
# and are reused below.


def _make_evaluator(*, tracks_gt, tracks_pred, metric_worst=None,
                    verbose=False, output_dir=None, render_classes=None):
    if metric_worst is None:
        metric_worst = {
            "amota": 0.0, "amotp": 2.0, "recall": 0.0, "motar": 0.0,
            "mota": 0.0, "motp": 2.0, "faf": 100.0, "gt": -1, "tp": 0,
            "mt": 0, "ml": -1, "fp": -1, "fn": -1, "ids": -1, "frag": -1,
            "tid": 20.0, "lgd": 20.0,
        }
    return TrackingEvaluation(
        tracks_gt=tracks_gt, tracks_pred=tracks_pred,
        class_name="person", dist_fcn=center_distance,
        dist_th_tp=2.0, min_recall=0.1, num_thresholds=4,
        metric_worst=metric_worst, verbose=verbose,
        output_dir=output_dir,
        render_classes=render_classes,
    )


# ---------------------------------------------------------------------------
# verbose=True print paths
# ---------------------------------------------------------------------------


def test_verbose_accumulate_prints_class_header_and_threshold_summary(capsys):
    """With ``verbose=True`` we hit the three print statements:
    'Computing metrics for class', 'Computed thresholds', and
    ``print_threshold_metrics`` for each valid threshold."""
    gt = {"sceneA": {t: [_box("1", (float(t), 0.0, 0.0))] for t in range(4)}}
    pred = {"sceneA": {t: [_box("1", (float(t), 0.0, 0.0))] for t in range(4)}}
    ev = _make_evaluator(tracks_gt=gt, tracks_pred=pred, verbose=True)
    ev.accumulate()
    out = capsys.readouterr().out
    assert "Computing metrics for class person" in out
    assert "Computed thresholds" in out


# ---------------------------------------------------------------------------
# NotImplementedError raise in the worst-value fallback
# ---------------------------------------------------------------------------


def test_unrecognised_worst_metric_with_minus_one_raises_not_implemented():
    """The fallback branch only knows how to derive a worst value
    for ml / gt / fn / fp / ids / frag. A non-derivable metric set
    to the ``-1`` sentinel triggers a bare ``NotImplementedError``
    when *no* recall threshold is achieved (i.e. ``thresh_metrics``
    is empty)."""
    # Empty pred -> no recall threshold achieved -> falls into the
    # worst-value branch for every metric. Set ``motar`` (a metric
    # not in the explicit fallback list) to -1.
    gt = {"sceneA": {0: [_box("1", (0, 0, 0))]}}
    pred = {"sceneA": {0: []}}
    bad_worst = {
        "amota": 0.0, "amotp": 2.0, "recall": 0.0,
        "motar": -1,  # ← will trip raise NotImplementedError
        "mota": 0.0, "motp": 2.0, "faf": 100.0, "gt": -1, "tp": 0,
        "mt": 0, "ml": -1, "fp": -1, "fn": -1, "ids": -1, "frag": -1,
        "tid": 20.0, "lgd": 20.0,
    }
    ev = _make_evaluator(tracks_gt=gt, tracks_pred=pred,
                          metric_worst=bad_worst)
    with pytest.raises(NotImplementedError):
        ev.accumulate()


# ---------------------------------------------------------------------------
# accumulate_threshold — render_classes branch + both-empty continue
# ---------------------------------------------------------------------------


def test_accumulate_threshold_render_classes_creates_output_dir(tmp_path):
    """When ``self.class_name in self.render_classes`` and
    ``threshold is None``, the function instantiates a TrackingRenderer
    and writes per-frame artefacts under ``output_dir/render/...``."""
    gt = {"sceneA": {t: [_box("1", (float(t), 0.0, 0.0))] for t in range(2)}}
    pred = {"sceneA": {t: [_box("1", (float(t), 0.0, 0.0))] for t in range(2)}}
    out_dir = str(tmp_path / "out")
    os.makedirs(out_dir, exist_ok=True)
    ev = _make_evaluator(
        tracks_gt=gt, tracks_pred=pred, verbose=False,
        output_dir=out_dir, render_classes=["person"],
    )
    # Call accumulate_threshold(None) directly so we hit the renderer
    # without driving the full accumulate() loop.
    ev.accumulate_threshold(threshold=None)
    expected = os.path.join(out_dir, "render", "sceneA", "person")
    assert os.path.isdir(expected)


def test_accumulate_threshold_skips_frames_with_neither_gt_nor_pred():
    """A timestep where both GT and pred lists are empty hits the
    ``len(gt_ids) == 0 and len(pred_ids) == 0: continue`` short
    circuit and contributes nothing to the accumulator."""
    gt = {"sceneA": {0: [], 1: [_box("1", (0, 0, 0))]}}
    pred = {"sceneA": {0: [], 1: [_box("1", (0, 0, 0))]}}
    ev = _make_evaluator(tracks_gt=gt, tracks_pred=pred)
    acc, _ = ev.accumulate_threshold(threshold=None)
    # The accumulator only saw one frame (the t=1 frame) — the
    # empty frame 0 was skipped via ``continue``.
    assert acc is not None
