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

from abc import ABC, abstractmethod
import numpy as np

from .schemas import ModelRunResult
from .stats import remove_outliers_iqr


class ConsolidationPolicy(ABC):
    @abstractmethod
    def select_focal(self, per_model: dict[str, ModelRunResult]) -> dict:
        ...


class SingleSourcePolicy(ConsolidationPolicy):
    def __init__(self, model_id: str = "geocalib"):
        self._model_id = model_id

    def select_focal(self, per_model):
        mr = per_model.get(self._model_id)
        if mr is None or not mr.outputs:
            return {"selected_focal_length_px": None, "policy": "single_source", "error": "no data"}
        focals = [o.focal_length_px for o in mr.outputs if o.focal_length_px is not None]
        selected = float(np.median(focals)) if focals else None
        return {"selected_focal_length_px": selected, "policy": "single_source", "source": self._model_id}


class MedianOfModelsPolicy(ConsolidationPolicy):
    def select_focal(self, per_model):
        medians = []
        for mid, mr in per_model.items():
            focals = [o.focal_length_px for o in mr.outputs if o.focal_length_px is not None]
            if focals:
                medians.append(float(np.median(focals)))
        selected = float(np.median(medians)) if medians else None
        return {"selected_focal_length_px": selected, "policy": "median_of_models", "inputs": medians}


class WeightedByConfidencePolicy(ConsolidationPolicy):
    def select_focal(self, per_model):
        focals, weights = [], []
        for mid, mr in per_model.items():
            valid = [o for o in mr.outputs if o.focal_length_px is not None]
            if not valid:
                continue
            median_focal = float(np.median([o.focal_length_px for o in valid]))
            focal_uncertainties = [
                o.uncertainty.get("focal_px") for o in valid
                if o.uncertainty.get("focal_px") is not None
            ]
            w = 1.0 / max(float(np.mean(focal_uncertainties)), 1e-6) if focal_uncertainties else 1.0
            focals.append(median_focal)
            weights.append(w)
        if not focals:
            return {"selected_focal_length_px": None, "policy": "weighted_by_confidence", "error": "no data"}
        w_arr = np.array(weights)
        w_arr = w_arr / w_arr.sum()
        selected = float(np.dot(focals, w_arr))
        return {"selected_focal_length_px": selected, "policy": "weighted_by_confidence", "weights": w_arr.tolist()}


class TrimmedMeanPolicy(ConsolidationPolicy):
    def select_focal(self, per_model):
        all_focals = []
        for mid, mr in per_model.items():
            focals = [o.focal_length_px for o in mr.outputs if o.focal_length_px is not None]
            if focals:
                all_focals.append(float(np.median(focals)))
        cleaned, info = remove_outliers_iqr(all_focals)
        selected = float(np.mean(cleaned)) if cleaned else None
        return {"selected_focal_length_px": selected, "policy": "trimmed_mean", "outlier_info": info}


class CustomPolicy(ConsolidationPolicy):
    """Placeholder for a user-defined consolidation policy.

    Implement your logic in select_focal(). The per_model dict maps
    model_id -> ModelRunResult, where each ModelRunResult has:
      .outputs: list[AdapterOutput]   (each has .focal_length_px, .extra, etc.)
      .stats:   dict or None          (mean, median, std, cv, etc.)
    Return a dict with at least {"selected_focal_length_px": float}.
    """
    def select_focal(self, per_model):
        # TODO: implement your custom consolidation logic here
        raise NotImplementedError("CustomPolicy.select_focal() not yet implemented")


_POLICIES = {
    "single_source": lambda cfg: SingleSourcePolicy(cfg.get("policy_params", {}).get("model_id", "geocalib")),
    "median_of_models": lambda cfg: MedianOfModelsPolicy(),
    "weighted_by_confidence": lambda cfg: WeightedByConfidencePolicy(),
    "trimmed_mean": lambda cfg: TrimmedMeanPolicy(),
    "custom": lambda cfg: CustomPolicy(),
}


def get_policy(consolidation_config: dict) -> ConsolidationPolicy:
    name = consolidation_config.get("policy", "single_source")
    factory = _POLICIES.get(name)
    if factory is None:
        raise ValueError(f"Unknown consolidation policy: {name}")
    return factory(consolidation_config)
