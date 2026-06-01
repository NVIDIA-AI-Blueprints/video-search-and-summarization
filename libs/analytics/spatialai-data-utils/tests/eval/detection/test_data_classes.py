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

import math

import numpy as np
import pytest

import spatialai_data_utils.eval.detection.data_classes as dc


def make_valid_md_arrays():
    n = dc.DetectionMetricData.nelem
    recall = np.linspace(0.0, 1.0, n)
    precision = np.linspace(1.0, 0.0, n)
    confidence = np.linspace(1.0, 0.0, n)
    ones = np.ones(n)
    zeros = np.zeros(n)
    return recall, precision, confidence, ones, ones, ones, ones, zeros


def test_detection_config_serialize_deserialize_and_callable():
    cfg = dc.DetectionConfig(
        class_range={"Person": 50},
        dist_fcn="center_distance",
        dist_ths=[0.5, 1.0],
        dist_th_tp=0.5,
        min_recall=0.0,
        min_precision=0.0,
        max_boxes_per_sample=500,
        mean_ap_weight=5,
    )

    cfg2 = dc.DetectionConfig.deserialize(cfg.serialize())
    assert cfg == cfg2
    assert callable(cfg.dist_fcn_callable)

    bad = dc.DetectionConfig(
        class_range={"Person": 50},
        dist_fcn="unknown",
        dist_ths=[0.5],
        dist_th_tp=0.5,
        min_recall=0.0,
        min_precision=0.0,
        max_boxes_per_sample=500,
        mean_ap_weight=5,
    )
    with pytest.raises(Exception, match="Unknown distance function"):
        _ = bad.dist_fcn_callable


def test_detection_metric_data_init_properties_and_roundtrip():
    arrays = make_valid_md_arrays()
    md = dc.DetectionMetricData(*arrays)

    assert md.max_recall_ind == np.nonzero(arrays[2])[0][-1]
    assert md.max_recall == arrays[0][md.max_recall_ind]

    n = dc.DetectionMetricData.nelem
    recall = np.linspace(0.0, 1.0, n)
    zeros = np.zeros(n)
    md2 = dc.DetectionMetricData(
        recall=recall,
        precision=zeros,
        confidence=zeros,
        trans_err=np.ones(n),
        vel_err=np.ones(n),
        scale_err=np.ones(n),
        orient_err=np.ones(n),
        attr_err=np.ones(n),
    )
    assert md2.max_recall_ind == 0
    assert md2.max_recall == recall[0]

    assert dc.DetectionMetricData.deserialize(md.serialize()) == md
    assert len(dc.DetectionMetricData.no_predictions().recall) == n
    assert len(dc.DetectionMetricData.random_md().precision) == n

    with pytest.raises(AssertionError):
        dc.DetectionMetricData(
            recall=np.array([0.0, 0.5]),
            precision=np.array([1.0, 0.0]),
            confidence=np.array([1.0, 0.0]),
            trans_err=np.array([1.0, 1.0]),
            vel_err=np.array([1.0, 1.0]),
            scale_err=np.array([1.0, 1.0]),
            orient_err=np.array([1.0, 1.0]),
            attr_err=np.array([1.0, 1.0]),
        )

    with pytest.raises(AssertionError):
        dc.DetectionMetricData(
            recall=np.linspace(1.0, 0.0, n),
            precision=np.linspace(1.0, 0.0, n),
            confidence=np.linspace(0.0, 1.0, n),
            trans_err=np.ones(n),
            vel_err=np.ones(n),
            scale_err=np.ones(n),
            orient_err=np.ones(n),
            attr_err=np.ones(n),
        )


def test_detection_metrics_add_get_mean_and_scores(monkeypatch):
    # DetectionMetrics reads TP_METRICS from data_classes module globals.
    monkeypatch.setattr(dc, "TP_METRICS", ["trans_err", "scale_err"])

    cfg = dc.DetectionConfig(
        class_range={"Person": 50, "Car": 50},
        dist_fcn="center_distance",
        dist_ths=[0.5, 1.0],
        dist_th_tp=0.5,
        min_recall=0.0,
        min_precision=0.0,
        max_boxes_per_sample=500,
        mean_ap_weight=5,
    )
    metrics = dc.DetectionMetrics(cfg)

    metrics.add_label_ap("Person", 0.5, 0.4)
    metrics.add_label_ap("Person", 1.0, 0.6)
    metrics.add_label_ap("Car", 0.5, 0.8)
    metrics.add_label_ap("Car", 1.0, 0.2)
    assert metrics.get_label_ap("Person", 0.5) == 0.4

    assert math.isclose(metrics.mean_dist_aps["Person"], 0.5)
    assert math.isclose(metrics.mean_dist_aps["Car"], 0.5)
    assert math.isclose(metrics.mean_ap, 0.5)

    metrics.add_label_tp("Person", "trans_err", 0.2)
    metrics.add_label_tp("Person", "scale_err", 0.3)
    metrics.add_label_tp("Car", "trans_err", 0.4)
    metrics.add_label_tp("Car", "scale_err", 0.6)
    assert metrics.get_label_tp("Car", "scale_err") == 0.6

    assert math.isclose(metrics.tp_errors["trans_err"], 0.3)
    assert math.isclose(metrics.tp_errors["scale_err"], 0.45)
    assert all(0.0 <= value <= 1.0 for value in metrics.tp_scores.values())
    assert isinstance(metrics.nd_score, float)

    metrics_rt = dc.DetectionMetrics.deserialize(metrics.serialize())
    assert metrics.cfg.serialize() == metrics_rt.cfg.serialize()


def test_detection_box_serialize_deserialize_and_eq():
    box = dc.DetectionBox(
        sample_token="1",
        translation=(1.0, 2.0, 3.0),
        size=(4.0, 5.0, 6.0),
        rotation=(1.0, 0.0, 0.0, 0.0),
        velocity=(0.1, 0.2),
        detection_name="Person",
        detection_score=0.9,
        attribute_name="",
    )
    assert dc.DetectionBox.deserialize(box.serialize()) == box

    with pytest.raises(AssertionError, match="may not be NaN"):
        dc.DetectionBox(detection_score=float("nan"))


def test_detection_metric_data_list_set_get_serialize_roundtrip():
    mdl = dc.DetectionMetricDataList()
    md = dc.DetectionMetricData(*make_valid_md_arrays())

    mdl.set("Person", 0.5, md)
    mdl.set("Car", 1.0, md)

    class_data = mdl.get_class_data("Person")
    assert len(class_data) == 1
    assert math.isclose(class_data[0][1], 0.5)

    dist_data = mdl.get_dist_data(1.0)
    assert len(dist_data) == 1
    assert dist_data[0][1] == "Car"

    mdl_rt = dc.DetectionMetricDataList.deserialize(mdl.serialize())
    assert mdl.md.keys() == mdl_rt.md.keys()
    for key in mdl.md:
        assert mdl[key] == mdl_rt[key]
    assert mdl == mdl_rt


# ===========================================================
# Coverage supplement (merged from test_data_classes_eq.py)
# ===========================================================

"""Coverage supplement for ``DetectionMetrics.__eq__`` — the existing
test file exercises serialize/deserialize round-trips but not the
equality operator, which is needed for downstream test comparisons."""

import pytest

from spatialai_data_utils.eval.detection.data_classes import (
    DetectionConfig,
    DetectionMetrics,
)


def _config():
    return DetectionConfig(
        class_range={"Person": 50},
        dist_fcn="center_distance",
        dist_ths=[0.5],
        dist_th_tp=0.5,
        min_recall=0.0,
        min_precision=0.0,
        max_boxes_per_sample=500,
        mean_ap_weight=5,
    )


def _populated_metrics():
    """Build a DetectionMetrics with both label_aps and label_tp_errors
    set, runtime recorded, plus the cfg already supplied."""
    m = DetectionMetrics(_config())
    m.add_label_ap("Person", 0.5, 0.9)
    m.add_label_tp("Person", "trans_err", 0.1)
    m.add_runtime(1.23)
    return m


class TestDetectionMetricsEq:
    def test_equal_instances_compare_equal(self):
        assert _populated_metrics() == _populated_metrics()

    def test_unequal_label_ap_breaks_equality(self):
        a = _populated_metrics()
        b = _populated_metrics()
        b.add_label_ap("Person", 0.5, 0.4)  # different AP
        assert a != b

    def test_unequal_label_tp_error_breaks_equality(self):
        a = _populated_metrics()
        b = _populated_metrics()
        b.add_label_tp("Person", "trans_err", 0.9)
        assert a != b

    def test_unequal_eval_time_breaks_equality(self):
        a = _populated_metrics()
        b = _populated_metrics()
        b.add_runtime(99.99)
        assert a != b
