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

# Copyright 2021 Motional
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# Adapted from nuScenes dev-kit.
# Original code by Oscar Beijbom, 2019.

from collections import defaultdict
from typing import List, Dict, Tuple

import numpy as np

from nuscenes.eval.common.data_classes import MetricData, EvalBox
from nuscenes.eval.common.utils import center_distance
from nuscenes.eval.detection.constants import TP_METRICS
from spatialai_data_utils.eval.common.utils import iou_3d


class DetectionConfig:
    """
    Data class that specifies the detection evaluation settings.

    Stores configuration parameters like class ranges, distance functions, thresholds,
    recall/precision limits, and weighting factors used during evaluation.

    :param class_range: Max detection distance for each class.
    :type class_range: Dict[str, int]
    :param dist_fcn: Distance function used for matching ('center_distance', etc.).
    :type dist_fcn: str
    :param dist_ths: Distance thresholds for TP matching.
    :type dist_ths: List[float]
    :param dist_th_tp: Single distance threshold used for calculating TP metrics.
    :type dist_th_tp: float
    :param min_recall: Minimum recall value needed to be considered valid.
    :type min_recall: float
    :param min_precision: Minimum precision value needed to be considered valid.
    :type min_precision: float
    :param max_boxes_per_sample: Maximum number of prediction boxes per sample.
    :type max_boxes_per_sample: int
    :param mean_ap_weight: Weight assigned to mAP when calculating NDS.
    :type mean_ap_weight: int
    """

    def __init__(
        self,
        class_range: Dict[str, int],
        dist_fcn: str,
        dist_ths: List[float],
        dist_th_tp: float,
        min_recall: float,
        min_precision: float,
        max_boxes_per_sample: int,
        mean_ap_weight: int,
    ):
        # assert set(class_range.keys()) == set(CLASS_LIST), "Class count mismatch."
        assert dist_th_tp in dist_ths, "dist_th_tp must be in set of dist_ths."

        self.class_range = class_range
        self.dist_fcn = dist_fcn
        self.dist_ths = dist_ths
        self.dist_th_tp = dist_th_tp
        self.min_recall = min_recall
        self.min_precision = min_precision
        self.max_boxes_per_sample = max_boxes_per_sample
        self.mean_ap_weight = mean_ap_weight

        self.class_names = self.class_range.keys()

    def __eq__(self, other):
        eq = True
        for key in self.serialize().keys():
            eq = eq and np.array_equal(getattr(self, key), getattr(other, key))
        return eq

    def serialize(self) -> dict:
        """Serialize instance into json-friendly format."""
        return {
            "class_range": self.class_range,
            "dist_fcn": self.dist_fcn,
            "dist_ths": self.dist_ths,
            "dist_th_tp": self.dist_th_tp,
            "min_recall": self.min_recall,
            "min_precision": self.min_precision,
            "max_boxes_per_sample": self.max_boxes_per_sample,
            "mean_ap_weight": self.mean_ap_weight,
        }

    @classmethod
    def deserialize(cls, content: dict):
        """Initialize from serialized dictionary."""
        return cls(
            content["class_range"],
            content["dist_fcn"],
            content["dist_ths"],
            content["dist_th_tp"],
            content["min_recall"],
            content["min_precision"],
            content["max_boxes_per_sample"],
            content["mean_ap_weight"],
        )

    @property
    def dist_fcn_callable(self):
        """Return the distance function corresponding to the dist_fcn string."""
        if self.dist_fcn == "center_distance":
            return center_distance
        elif self.dist_fcn == "iou_3d":
            return iou_3d
        else:
            raise Exception("Error: Unknown distance function %s!" % self.dist_fcn)


class DetectionMetricData(MetricData):
    """
    Holds accumulated and interpolated data required to calculate detection metrics.

    Stores arrays for recall, precision, confidence, and various true positive errors
    (translation, velocity, scale, orientation, attribute) across recall/confidence levels.

    :param recall: Array of recall values (ascending).
    :type recall: numpy.ndarray
    :param precision: Array of precision values corresponding to recall.
    :type precision: numpy.ndarray
    :param confidence: Array of confidence thresholds (descending).
    :type confidence: numpy.ndarray
    :param trans_err: Array of mean translation errors.
    :type trans_err: numpy.ndarray
    :param vel_err: Array of mean velocity errors.
    :type vel_err: numpy.ndarray
    :param scale_err: Array of mean scale errors.
    :type scale_err: numpy.ndarray
    :param orient_err: Array of mean orientation errors.
    :type orient_err: numpy.ndarray
    :param attr_err: Array of mean attribute errors.
    :type attr_err: numpy.ndarray
    """

    nelem = 101

    def __init__(
        self,
        recall: np.array,
        precision: np.array,
        confidence: np.array,
        trans_err: np.array,
        vel_err: np.array,
        scale_err: np.array,
        orient_err: np.array,
        attr_err: np.array,
    ):
        # Assert lengths.
        assert len(recall) == self.nelem
        assert len(precision) == self.nelem
        assert len(confidence) == self.nelem
        assert len(trans_err) == self.nelem
        assert len(vel_err) == self.nelem
        assert len(scale_err) == self.nelem
        assert len(orient_err) == self.nelem
        assert len(attr_err) == self.nelem

        # Assert ordering.
        assert all(
            confidence == sorted(confidence, reverse=True)
        )  # Confidences should be descending.
        assert all(recall == sorted(recall))  # Recalls should be ascending.

        # Set attributes explicitly to help IDEs figure out what is going on.
        self.recall = recall
        self.precision = precision
        self.confidence = confidence
        self.trans_err = trans_err
        self.vel_err = vel_err
        self.scale_err = scale_err
        self.orient_err = orient_err
        self.attr_err = attr_err

    def __eq__(self, other):
        eq = True
        for key in self.serialize().keys():
            eq = eq and np.array_equal(getattr(self, key), getattr(other, key))
        return eq

    @property
    def max_recall_ind(self):
        """Return the index of the highest recall value achieved before confidence drops to zero."""

        # Last instance of confidence > 0 is index of max achieved recall.
        non_zero = np.nonzero(self.confidence)[0]
        if (
            len(non_zero) == 0
        ):  # If there are no matches, all the confidence values will be zero.
            max_recall_ind = 0
        else:
            max_recall_ind = non_zero[-1]

        return max_recall_ind

    @property
    def max_recall(self):
        """Return the maximum recall value achieved."""

        return self.recall[self.max_recall_ind]

    def serialize(self):
        """Serialize instance into json-friendly format."""
        return {
            "recall": self.recall.tolist(),
            "precision": self.precision.tolist(),
            "confidence": self.confidence.tolist(),
            "trans_err": self.trans_err.tolist(),
            "vel_err": self.vel_err.tolist(),
            "scale_err": self.scale_err.tolist(),
            "orient_err": self.orient_err.tolist(),
            "attr_err": self.attr_err.tolist(),
        }

    @classmethod
    def deserialize(cls, content: dict):
        """Initialize from serialized content."""
        return cls(
            recall=np.array(content["recall"]),
            precision=np.array(content["precision"]),
            confidence=np.array(content["confidence"]),
            trans_err=np.array(content["trans_err"]),
            vel_err=np.array(content["vel_err"]),
            scale_err=np.array(content["scale_err"]),
            orient_err=np.array(content["orient_err"]),
            attr_err=np.array(content["attr_err"]),
        )

    @classmethod
    def no_predictions(cls):
        """Return a DetectionMetricData instance corresponding to having no predictions."""
        return cls(
            recall=np.linspace(0, 1, cls.nelem),
            precision=np.zeros(cls.nelem),
            confidence=np.zeros(cls.nelem),
            trans_err=np.ones(cls.nelem),
            vel_err=np.ones(cls.nelem),
            scale_err=np.ones(cls.nelem),
            orient_err=np.ones(cls.nelem),
            attr_err=np.ones(cls.nelem),
        )

    @classmethod
    def random_md(cls):
        """Return a DetectionMetricData instance corresponding to random results (for testing)."""
        return cls(
            recall=np.linspace(0, 1, cls.nelem),
            precision=np.random.random(cls.nelem),
            confidence=np.linspace(0, 1, cls.nelem)[::-1],
            trans_err=np.random.random(cls.nelem),
            vel_err=np.random.random(cls.nelem),
            scale_err=np.random.random(cls.nelem),
            orient_err=np.random.random(cls.nelem),
            attr_err=np.random.random(cls.nelem),
        )


class DetectionMetrics:
    """
    Stores average precision (AP) and true positive (TP) metric results.

    Provides properties and methods to calculate and summarize detection metrics
    like mAP, NDS, and TP errors/scores across classes and distance thresholds.

    :param cfg: The detection configuration settings.
    :type cfg: DetectionConfig
    """

    def __init__(self, cfg: DetectionConfig):
        self.cfg = cfg
        self._label_aps = defaultdict(lambda: defaultdict(float))
        self._label_tp_errors = defaultdict(lambda: defaultdict(float))
        self.eval_time = None

    def add_label_ap(self, detection_name: str, dist_th: float, ap: float) -> None:
        """Add the AP for a specific class and distance threshold."""
        self._label_aps[detection_name][dist_th] = ap

    def get_label_ap(self, detection_name: str, dist_th: float) -> float:
        """Retrieve the AP for a specific class and distance threshold."""
        return self._label_aps[detection_name][dist_th]

    def add_label_tp(self, detection_name: str, metric_name: str, tp: float):
        """Add the True Positive metric error for a specific class and metric type."""
        self._label_tp_errors[detection_name][metric_name] = tp

    def get_label_tp(self, detection_name: str, metric_name: str) -> float:
        """Retrieve the True Positive metric error for a specific class and metric type."""
        return self._label_tp_errors[detection_name][metric_name]

    def add_runtime(self, eval_time: float) -> None:
        """Store the evaluation runtime."""
        self.eval_time = eval_time

    @property
    def mean_dist_aps(self) -> Dict[str, float]:
        """Calculate the mean AP over distance thresholds for each class."""
        return {
            class_name: np.mean(list(d.values()))
            for class_name, d in self._label_aps.items()
        }

    @property
    def mean_ap(self) -> float:
        """Calculate the overall mean AP (mAP) across classes and distance thresholds."""
        return float(np.mean(list(self.mean_dist_aps.values())))

    @property
    def tp_errors(self) -> Dict[str, float]:
        """Calculate the mean true positive error across all classes for each TP metric.

        If a class has no TPs for a given metric, ``get_label_tp`` returns
        ``nan``; those values are filtered out so that the mean is taken over
        the classes that *do* have a measurement. When every class is ``nan``
        (e.g., tiny eval inputs with no TPs), the metric is reported as
        ``nan`` without emitting a ``RuntimeWarning: Mean of empty slice``.
        """
        errors = {}
        for metric_name in TP_METRICS:
            class_errors = [
                self.get_label_tp(detection_name, metric_name)
                for detection_name in self.cfg.class_names
            ]
            valid = [e for e in class_errors if not np.isnan(e)]
            errors[metric_name] = float(np.mean(valid)) if valid else float("nan")

        return errors

    @property
    def tp_scores(self) -> Dict[str, float]:
        """Calculate the true positive scores (1 - normalized error) for each TP metric."""
        scores = {}
        tp_errors = self.tp_errors
        for metric_name in TP_METRICS:
            # We convert the true positive errors to "scores" by 1-error.
            score = 1.0 - tp_errors[metric_name]

            # Some of the true positive errors are unbounded, so we bound the scores to min 0.
            score = max(0.0, score)

            scores[metric_name] = score

        return scores

    @property
    def nd_score(self) -> float:
        """
        Compute the Detection Score (NDS): weighted sum of mAP and TP scores.
        """
        # Summarize.
        total = float(
            self.cfg.mean_ap_weight * self.mean_ap
            + np.sum(list(self.tp_scores.values()))
        )

        # Normalize.
        total = total / float(self.cfg.mean_ap_weight + len(self.tp_scores.keys()))

        return total

    def serialize(self):
        """Serialize instance into json-friendly format, including aggregated metrics."""
        return {
            "label_aps": self._label_aps,
            "mean_dist_aps": self.mean_dist_aps,
            "mean_ap": self.mean_ap,
            "label_tp_errors": self._label_tp_errors,
            "tp_errors": self.tp_errors,
            "tp_scores": self.tp_scores,
            "nd_score": self.nd_score,
            "eval_time": self.eval_time,
            "cfg": self.cfg.serialize(),
        }

    @classmethod
    def deserialize(cls, content: dict):
        """Initialize from serialized dictionary."""

        cfg = DetectionConfig.deserialize(content["cfg"])

        metrics = cls(cfg=cfg)
        metrics.add_runtime(content["eval_time"])

        for detection_name, label_aps in content["label_aps"].items():
            for dist_th, ap in label_aps.items():
                metrics.add_label_ap(
                    detection_name=detection_name, dist_th=float(dist_th), ap=float(ap)
                )

        for detection_name, label_tps in content["label_tp_errors"].items():
            for metric_name, tp in label_tps.items():
                metrics.add_label_tp(
                    detection_name=detection_name, metric_name=metric_name, tp=float(tp)
                )

        return metrics

    def __eq__(self, other):
        eq = True
        eq = eq and self._label_aps == other._label_aps
        eq = eq and self._label_tp_errors == other._label_tp_errors
        eq = eq and self.eval_time == other.eval_time
        eq = eq and self.cfg == other.cfg

        return eq


class DetectionBox(EvalBox):
    """
    Data class for a detection result, used for both predictions and ground truth.

    Extends `EvalBox` with detection-specific attributes like detection name and score.

    :param sample_token: The token of the sample associated with this box.
    :type sample_token: str, optional
    :param translation: Box center location (x, y, z).
    :type translation: Tuple[float, float, float], optional
    :param size: Box dimensions (width, length, height).
    :type size: Tuple[float, float, float], optional
    :param rotation: Box rotation as a quaternion (w, x, y, z).
    :type rotation: Tuple[float, float, float, float], optional
    :param velocity: Box velocity in the XY plane (vx, vy).
    :type velocity: Tuple[float, float], optional
    :param detection_name: The predicted or ground truth class name.
    :type detection_name: str, optional
    :param detection_score: The confidence score of the detection. Defaults to -1.0 (for GT).
    :type detection_score: float, optional
    :param attribute_name: The predicted or ground truth attribute name (optional).
    :type attribute_name: str, optional
    """

    def __init__(
        self,
        sample_token: str = "",
        translation: Tuple[float, float, float] = (0, 0, 0),
        size: Tuple[float, float, float] = (0, 0, 0),
        rotation: Tuple[float, float, float, float] = (0, 0, 0, 0),
        velocity: Tuple[float, float] = (0, 0),
        detection_name: str = "person",  # The class name used in the detection challenge.
        detection_score: float = -1.0,  # GT samples do not have a score.
        attribute_name: str = "",
    ):  # Box attribute. Each box can have at most 1 attribute.
        super().__init__(sample_token, translation, size, rotation, velocity)

        assert detection_name is not None, "Error: detection_name cannot be empty!"
        # assert detection_name in CLASS_LIST, 'Error: Unknown detection_name %s' % detection_name

        # assert attribute_name in ATTRIBUTE_NAMES or attribute_name == '', \
        #     'Error: Unknown attribute_name %s' % attribute_name

        assert isinstance(detection_score, float), "Error: detection_score must be a float!"
        assert not np.any(np.isnan(detection_score)), (
            "Error: detection_score may not be NaN!"
        )

        # Assign.
        self.detection_name = detection_name
        self.detection_score = detection_score
        self.attribute_name = attribute_name

    def __eq__(self, other):
        return (
            self.sample_token == other.sample_token
            and self.translation == other.translation
            and self.size == other.size
            and self.rotation == other.rotation
            and self.velocity == other.velocity
            and self.detection_name == other.detection_name
            and self.detection_score == other.detection_score
            and self.attribute_name == other.attribute_name
        )

    def serialize(self) -> dict:
        """Serialize instance into json-friendly format."""
        return {
            "sample_token": self.sample_token,
            "translation": self.translation,
            "size": self.size,
            "rotation": self.rotation,
            "velocity": self.velocity,
            "detection_name": self.detection_name,
            "detection_score": self.detection_score,
            "attribute_name": self.attribute_name,
        }

    @classmethod
    def deserialize(cls, content: dict):
        """Initialize from serialized content."""
        return cls(
            sample_token=content["sample_token"],
            translation=tuple(content["translation"]),
            size=tuple(content["size"]),
            rotation=tuple(content["rotation"]),
            velocity=tuple(content["velocity"]),
            detection_name=content["detection_name"],
            detection_score=-1.0
            if "detection_score" not in content
            else float(content["detection_score"]),
            attribute_name=content["attribute_name"],
        )


class DetectionMetricDataList:
    """
    Stores a collection of DetectionMetricData objects.

    Uses a dictionary where keys are tuples of (detection_name, match_distance)
    and values are DetectionMetricData instances. Provides methods to access
    data grouped by class name or distance threshold.
    """

    def __init__(self):
        self.md = {}

    def __getitem__(self, key: Tuple[str, float]) -> DetectionMetricData:
        """Access the DetectionMetricData for a given (class_name, dist_threshold) key."""
        return self.md[key]

    def __eq__(self, other):
        eq = True
        for key in self.md.keys():
            eq = eq and self[key] == other[key]
        return eq

    def get_class_data(
        self, detection_name: str
    ) -> List[Tuple[DetectionMetricData, float]]:
        """Get all (MetricData, dist_th) pairs for a specific detection class name."""
        return [
            (md, dist_th)
            for (name, dist_th), md in self.md.items()
            if name == detection_name
        ]

    def get_dist_data(self, dist_th: float) -> List[Tuple[DetectionMetricData, str]]:
        """Get all (MetricData, detection_name) pairs for a specific distance threshold."""
        return [
            (md, detection_name)
            for (detection_name, dist), md in self.md.items()
            if dist == dist_th
        ]

    def set(
        self, detection_name: str, match_distance: float, data: DetectionMetricData
    ):
        """Set the MetricData for a given class name and distance threshold."""
        self.md[(detection_name, match_distance)] = data

    def serialize(self) -> dict:
        """Serialize the collection into a json-friendly dictionary."""
        return {
            key[0] + ":" + str(key[1]): value.serialize()
            for key, value in self.md.items()
        }

    @classmethod
    def deserialize(cls, content: dict):
        """Initialize the collection from a serialized dictionary."""
        mdl = cls()
        for key, md in content.items():
            name, distance = key.split(":")
            mdl.set(name, float(distance), DetectionMetricData.deserialize(md))
        return mdl
