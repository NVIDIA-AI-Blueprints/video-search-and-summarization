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

"""Tests for ``eval.tracking.data_classes``.

Covers the four public types — ``TrackingConfig``,
``TrackingMetricData``, ``TrackingMetrics``, ``TrackingBox`` and
``TrackingMetricDataList`` — and the cross-coupling state they
manage: ``TrackingConfig.__init__`` sets the module-level
``TRACKING_NAMES`` (which ``TrackingBox.__init__`` validates against)
and the class-level ``TrackingMetricData.nelem`` (required before any
``TrackingMetricData()``).
"""

import numpy as np
import pytest

from spatialai_data_utils.eval.tracking import data_classes as dc
from spatialai_data_utils.eval.tracking.data_classes import (
    TrackingBox,
    TrackingConfig,
    TrackingMetricData,
    TrackingMetricDataList,
    TrackingMetrics,
)


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


def _make_config(*, dist_fcn="center_distance", num_thresholds=4):
    """Build a tiny TrackingConfig. This also registers TRACKING_NAMES
    and pins TrackingMetricData.nelem as a side effect — every test in
    this file ultimately needs that side effect."""
    return TrackingConfig(
        tracking_names=["person"],
        pretty_tracking_names={"person": "Person"},
        class_range={"person": 50},
        dist_fcn=dist_fcn,
        dist_th_tp=2.0,
        min_recall=0.1,
        max_boxes_per_sample=500,
        metric_worst={"amota": 0.0, "amotp": 2.0, "mota": 0.0, "motp": 2.0,
                       "recall": 0.0, "motar": 0.0, "gt": -1, "mt": -1, "ml": -1,
                       "faf": 100.0, "tp": -1, "fp": -1, "fn": -1, "ids": -1,
                       "frag": -1, "tid": 20.0, "lgd": 20.0},
        num_thresholds=num_thresholds,
    )


@pytest.fixture(autouse=True)
def _config_setup_and_teardown():
    """Every test ultimately needs TrackingMetricData.nelem set; rather
    than scatter setup, do it once via autouse. Reset after each test
    so the global / class-level state doesn't bleed across tests."""
    saved_nelem = TrackingMetricData.nelem
    saved_names = list(dc.TRACKING_NAMES)
    cfg = _make_config()
    yield cfg
    TrackingMetricData.nelem = saved_nelem
    dc.TRACKING_NAMES = saved_names


# ---------------------------------------------------------------------------
# TrackingConfig
# ---------------------------------------------------------------------------


def test_config_init_sets_global_tracking_names_and_nelem():
    """Constructing a TrackingConfig populates the module-level
    TRACKING_NAMES (used by TrackingBox validation) and the class-level
    TrackingMetricData.nelem (required for TrackingMetricData())."""
    assert dc.TRACKING_NAMES == ["person"]
    assert TrackingMetricData.nelem == 4


def test_config_class_count_mismatch_raises():
    with pytest.raises(AssertionError, match="Class count mismatch"):
        TrackingConfig(
            tracking_names=["person", "car"],
            pretty_tracking_names={"person": "Person", "car": "Car"},
            class_range={"person": 50},  # missing "car"
            dist_fcn="center_distance",
            dist_th_tp=2.0,
            min_recall=0.1,
            max_boxes_per_sample=500,
            metric_worst={},
            num_thresholds=4,
        )


def test_config_serialize_deserialize_round_trip():
    cfg = _make_config()
    payload = cfg.serialize()
    rebuilt = TrackingConfig.deserialize(payload)
    assert rebuilt == cfg


def test_config_dist_fcn_callable_center_distance_returns_callable():
    from nuscenes.eval.common.utils import center_distance
    cfg = _make_config(dist_fcn="center_distance")
    assert cfg.dist_fcn_callable is center_distance


def test_config_dist_fcn_callable_iou_3d_returns_callable():
    from spatialai_data_utils.eval.common.utils import iou_3d
    cfg = _make_config(dist_fcn="iou_3d")
    assert cfg.dist_fcn_callable is iou_3d


def test_config_dist_fcn_callable_unknown_raises():
    cfg = _make_config()
    cfg.dist_fcn = "unknown_metric"
    with pytest.raises(Exception, match="Unknown distance function"):
        _ = cfg.dist_fcn_callable


def test_config_class_names_is_sorted_keys_of_class_range():
    cfg = TrackingConfig(
        tracking_names=["zebra", "antelope"],
        pretty_tracking_names={"zebra": "Z", "antelope": "A"},
        class_range={"zebra": 10, "antelope": 20},
        dist_fcn="center_distance",
        dist_th_tp=1.0,
        min_recall=0.1,
        max_boxes_per_sample=10,
        metric_worst={},
        num_thresholds=4,
    )
    assert cfg.class_names == ["antelope", "zebra"]


# ---------------------------------------------------------------------------
# TrackingMetricData
# ---------------------------------------------------------------------------


def test_metric_data_set_and_get_metric_round_trip():
    md = TrackingMetricData()
    values = np.array([0.1, 0.2, 0.3, 0.4])
    md.set_metric("mota", values)
    np.testing.assert_allclose(md.get_metric("mota"), values)


def test_metric_data_setattr_rejects_wrong_length():
    """The ``__setattr__`` override enforces array length == nelem to
    prevent silent shape drift across thresholds."""
    md = TrackingMetricData()
    with pytest.raises(AssertionError):
        md.set_metric("mota", np.array([0.1, 0.2]))  # too short


def test_metric_data_serialize_deserialize_round_trip():
    md = TrackingMetricData()
    md.confidence = np.array([0.9, 0.7, 0.5, 0.3])
    md.recall_hypo = np.array([0.25, 0.5, 0.75, 1.0])
    for metric in TrackingMetricData.metrics:
        md.set_metric(metric, np.full(TrackingMetricData.nelem, 0.5))

    payload = md.serialize()
    assert "confidence" in payload and "recall_hypo" in payload
    rebuilt = TrackingMetricData.deserialize(payload)
    assert rebuilt == md


def test_metric_data_no_predictions_factory():
    md = TrackingMetricData.no_predictions()
    np.testing.assert_array_equal(md.confidence, np.zeros(4))
    np.testing.assert_array_equal(md.mota, np.zeros(4))
    np.testing.assert_allclose(md.recall, np.linspace(0, 1, 4))


def test_metric_data_random_md_factory_is_in_unit_range():
    md = TrackingMetricData.random_md()
    assert (md.mota >= 0).all() and (md.mota <= 1).all()
    np.testing.assert_allclose(md.recall, np.linspace(0, 1, 4))


def test_metric_data_max_recall_when_all_zero_confidence():
    """Empty / all-zero confidence => max_recall_ind defaults to 0."""
    md = TrackingMetricData.no_predictions()  # confidence is all zeros
    assert md.max_recall_ind == 0
    assert md.max_recall == 0.0


def test_metric_data_max_recall_picks_last_non_zero_confidence_index():
    md = TrackingMetricData.no_predictions()
    md.confidence = np.array([0.0, 0.9, 0.0, 0.7])  # last non-zero at idx 3
    md.recall = np.array([0.1, 0.4, 0.7, 0.95])
    assert md.max_recall_ind == 3
    assert md.max_recall == pytest.approx(0.95)


# ---------------------------------------------------------------------------
# TrackingMetrics
# ---------------------------------------------------------------------------


def test_metrics_add_label_metric_and_named_class_lookup():
    cfg = _make_config()
    m = TrackingMetrics(cfg)
    m.add_label_metric("mota", "person", 0.42)
    assert m.compute_metric("mota", "person") == pytest.approx(0.42)


def test_metrics_compute_metric_all_averages_for_mota():
    cfg = _make_config()
    m = TrackingMetrics(cfg)
    m.add_label_metric("mota", "person", 0.6)
    assert m.compute_metric("mota", "all") == pytest.approx(0.6)


def test_metrics_compute_metric_all_sums_for_count_metrics():
    """``mt / ml / tp / fp / fn / ids / frag`` are summed across
    classes, the rest are averaged. Build a 2-class config to make the
    sum-vs-average distinction observable."""
    cfg = TrackingConfig(
        tracking_names=["a", "b"],
        pretty_tracking_names={"a": "A", "b": "B"},
        class_range={"a": 10, "b": 20},
        dist_fcn="center_distance",
        dist_th_tp=2.0,
        min_recall=0.1,
        max_boxes_per_sample=100,
        metric_worst={},
        num_thresholds=4,
    )
    m = TrackingMetrics(cfg)
    m.add_label_metric("tp", "a", 3)
    m.add_label_metric("tp", "b", 5)
    assert m.compute_metric("tp", "all") == pytest.approx(8.0)


def test_metrics_serialize_includes_aggregated_metrics_and_cfg():
    cfg = _make_config()
    m = TrackingMetrics(cfg)
    m.add_label_metric("amota", "person", 0.5)
    m.add_runtime(1.23)
    payload = m.serialize()
    assert payload["eval_time"] == 1.23
    assert payload["amota"] == pytest.approx(0.5)
    assert payload["cfg"] == cfg.serialize()
    assert payload["label_metrics"]["amota"]["person"] == 0.5


def test_metrics_deserialize_round_trip():
    cfg = _make_config()
    m = TrackingMetrics(cfg)
    m.add_label_metric("amota", "person", 0.5)
    m.add_runtime(1.0)
    rebuilt = TrackingMetrics.deserialize(m.serialize())
    assert rebuilt == m


# ---------------------------------------------------------------------------
# TrackingBox
# ---------------------------------------------------------------------------


def _tracking_box(**overrides):
    kwargs = dict(
        sample_token="scene_A__0",
        translation=(1.0, 2.0, 0.5),
        size=(0.5, 1.0, 1.8),
        rotation=(1.0, 0.0, 0.0, 0.0),
        velocity=(0.0, 0.0),
        tracking_id="42",
        tracking_name="person",
        tracking_score=0.9,
    )
    kwargs.update(overrides)
    return TrackingBox(**kwargs)


def test_tracking_box_rejects_unknown_tracking_name():
    with pytest.raises(AssertionError, match="Unknown tracking_name"):
        _tracking_box(tracking_name="unicorn")


def test_tracking_box_rejects_non_float_score():
    with pytest.raises(AssertionError, match="must be a float"):
        _tracking_box(tracking_score=1)


def test_tracking_box_rejects_nan_score():
    with pytest.raises(AssertionError, match="may not be NaN"):
        _tracking_box(tracking_score=float("nan"))


def test_tracking_box_serialize_deserialize_round_trip():
    b = _tracking_box()
    rebuilt = TrackingBox.deserialize(b.serialize())
    assert rebuilt == b


def test_tracking_box_deserialize_defaults_missing_optional_fields():
    """``ego_translation`` / ``num_pts`` / ``tracking_score`` are
    optional on disk — older results files won't have them."""
    payload = {
        "sample_token": "scene_A__0",
        "translation": [1.0, 2.0, 0.5],
        "size": [0.5, 1.0, 1.8],
        "rotation": [1.0, 0.0, 0.0, 0.0],
        "velocity": [0.0, 0.0],
        "tracking_id": "42",
        "tracking_name": "person",
    }
    b = TrackingBox.deserialize(payload)
    assert b.ego_translation == (0.0, 0.0, 0.0)
    assert b.num_pts == -1
    assert b.tracking_score == -1.0


def test_tracking_box_equality_pivots_on_all_fields():
    a = _tracking_box()
    b = _tracking_box()
    assert a == b
    c = _tracking_box(tracking_id="999")
    assert a != c


# ---------------------------------------------------------------------------
# TrackingMetricDataList
# ---------------------------------------------------------------------------


def _fully_populated_md():
    """``TrackingMetricData.__eq__`` uses ``np.array_equal`` which
    returns False for NaN==NaN (no ``equal_nan`` flag). Both
    ``no_predictions()`` and ``random_md()`` leave ``recall_hypo``
    NaN-defaulted, so they can't round-trip-compare via ``==``. Build
    a fully-populated fixture for equality tests."""
    md = TrackingMetricData()
    md.confidence = np.zeros(TrackingMetricData.nelem)
    md.recall_hypo = np.linspace(0, 1, TrackingMetricData.nelem)
    md.recall = np.linspace(0, 1, TrackingMetricData.nelem)
    for metric in TrackingMetricData.metrics:
        md.set_metric(metric, np.zeros(TrackingMetricData.nelem))
    return md


def test_metric_data_list_set_and_get_per_class():
    mdl = TrackingMetricDataList()
    md = TrackingMetricData.no_predictions()
    mdl.set("person", md)
    assert mdl["person"] is md


def test_metric_data_list_serialize_deserialize_round_trip():
    mdl = TrackingMetricDataList()
    mdl.set("person", _fully_populated_md())
    payload = mdl.serialize()
    rebuilt = TrackingMetricDataList.deserialize(payload, TrackingMetricData)
    assert rebuilt == mdl


def test_metric_data_list_equality_via_per_class_data():
    mdl_a = TrackingMetricDataList()
    mdl_a.set("person", _fully_populated_md())
    mdl_b = TrackingMetricDataList()
    mdl_b.set("person", _fully_populated_md())
    assert mdl_a == mdl_b


# ===========================================================
# Coverage supplement (merged from test_tracking_data_classes_coverage.py)
# ===========================================================

"""Coverage supplement for ``eval.tracking.data_classes.TrackingMetrics.compute_metric``
— pins the ``empty data list -> NaN`` branch that the existing tests
don't reach (every metric has at least one class registered, so
``len(data) > 0`` always)."""

import math

from spatialai_data_utils.eval.tracking import data_classes as dc
from spatialai_data_utils.eval.tracking.data_classes import (
    TrackingConfig,
    TrackingMetricData,
    TrackingMetrics,
)


def test_compute_metric_all_with_no_registered_classes_returns_nan():
    """When the per-metric ``label_metrics`` dict is empty (zero
    classes), ``compute_metric(..., 'all')`` short-circuits to
    ``np.nan`` instead of attempting an empty-list nanmean/nansum."""
    saved_nelem = TrackingMetricData.nelem
    saved_names = list(dc.TRACKING_NAMES)
    try:
        cfg = TrackingConfig(
            tracking_names=["person"],
            pretty_tracking_names={"person": "Person"},
            class_range={"person": 50},
            dist_fcn="center_distance",
            dist_th_tp=2.0,
            min_recall=0.1,
            max_boxes_per_sample=100,
            metric_worst={},
            num_thresholds=4,
        )
        m = TrackingMetrics(cfg)
        # Forcibly empty the per-class dict for a metric so the
        # ``len(data) > 0`` guard fails and we hit the ``return
        # np.nan`` fallthrough.
        m.label_metrics["amota"] = {}
        out = m.compute_metric("amota", "all")
        assert math.isnan(out)
    finally:
        TrackingMetricData.nelem = saved_nelem
        dc.TRACKING_NAMES = saved_names
