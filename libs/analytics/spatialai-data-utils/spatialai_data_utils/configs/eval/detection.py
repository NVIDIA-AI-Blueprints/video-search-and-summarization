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

"""Detection-evaluation config presets.

Two presets are exported, both consumed by
:class:`spatialai_data_utils.eval.detection.data_classes.DetectionConfig`
via its ``deserialize`` classmethod. They share the same class list,
thresholds, and ``max_boxes_per_sample`` cap; the only difference is the
matching function used to pair predictions with ground truth:

- :data:`DET_CONFIG_IOU3D` matches by 3D IoU. Used by the standalone
  detection evaluator (``evaluate_detection_per_BEV_sensor`` and friends),
  and by external consumers that want the strictest geometric match.
- :data:`DET_CONFIG_CENTER_DISTANCE` matches by centre distance in
  metres. Used by the MTMC validation+evaluation tool
  (``tools/validation_and_evaluation/run_validation_and_evaluation.py``)
  on Sparse4D BEV outputs, where centre-distance matching is the
  established protocol on the cloud side.
"""

from copy import deepcopy
from typing import Any, Dict


# Per-class max evaluation range in **metres** (see
# ``spatialai_data_utils.eval.detection.data_classes.DetectionConfig``
# docstring: "Max detection distance for each class"). Predictions whose
# centre is farther than this radius from the BEV origin are excluded
# from the per-class AP / TP-error calculation.
#
# Values mirror
# ``spatialai_data_utils.configs.object_classes.warehouse.CLASS_RANGE_DICT``
# (40 m for every warehouse class — Person, the humanoid robots, and the
# vehicles); the container/marker/rack classes that don't appear in that
# dict (Box, Pallet, Crate, Basket, KLTBin, Cone, Rack, and the Fii AMR
# racks) are kept at the same 40 m default for consistency. This
# replaces a pre-existing 4 m typo (10x smaller than the warehouse
# convention) that was carried through earlier detection-config literals.
#
# The literal is duplicated here (instead of ``{c: 40 for c in CLASS_LIST}``)
# to break the
# ``configs.eval.detection`` ↔ ``eval.common.classes`` import cycle.
# ``tests/configs/eval/test_detection.py::test_class_range_matches_class_list``
# pins the keys to ``CLASS_LIST`` so drift is caught at CI time.
_DET_CONFIG_CLASS_RANGE: Dict[str, float] = {
    "Person": 40,
    "NovaCarter": 40,
    "Transporter": 40,
    "Forklift": 40,
    "Box": 40,
    "Pallet": 40,
    "Crate": 40,
    "Basket": 40,
    "KLTBin": 40,
    "Cone": 40,
    "Rack": 40,
    "Fourier_GR1_T2_Humanoid": 40,
    "Agility_Digit_Humanoid": 40,
    "Fii_AMR_Bianca_Rack": 40,
    "Fii_AMR_HGX_Rack": 40,
}


# Shared knobs both presets agree on. Pulled out as a private template so
# the only thing the two public configs differ on (``dist_fcn``) stays
# obvious at a glance.
_DET_CONFIG_BASE: Dict[str, Any] = {
    "class_range": _DET_CONFIG_CLASS_RANGE,
    "dist_ths": [0.5],
    "dist_th_tp": 0.5,
    "min_recall": 0.1,
    "min_precision": 0.1,
    "max_boxes_per_sample": 300,
    "mean_ap_weight": 5,
}


DET_CONFIG_IOU3D: Dict[str, Any] = {
    **deepcopy(_DET_CONFIG_BASE),
    "dist_fcn": "iou_3d",
}


DET_CONFIG_CENTER_DISTANCE: Dict[str, Any] = {
    **deepcopy(_DET_CONFIG_BASE),
    "dist_fcn": "center_distance",
}


__all__ = [
    "DET_CONFIG_CENTER_DISTANCE",
    "DET_CONFIG_IOU3D",
]
