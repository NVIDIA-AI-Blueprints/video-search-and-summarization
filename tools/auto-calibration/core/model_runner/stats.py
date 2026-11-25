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

import numpy as np
from .schemas import AdapterOutput


def remove_outliers_iqr(values: list[float], multiplier: float = 1.5) -> tuple[list[float], dict]:
    """IQR-based outlier removal (matches logic from run_geocalib.py)."""
    if len(values) < 4:
        return values, {"outliers_removed": 0, "method": "insufficient_data"}
    arr = np.array(values)
    q25, q75 = np.percentile(arr, [25, 75])
    iqr = q75 - q25
    lo, hi = q25 - multiplier * iqr, q75 + multiplier * iqr
    mask = (arr >= lo) & (arr <= hi)
    cleaned = arr[mask].tolist()
    return cleaned, {
        "original_count": len(values),
        "cleaned_count": len(cleaned),
        "outliers_removed": len(values) - len(cleaned),
        "method": f"IQR (multiplier={multiplier})",
        "bounds": [float(lo), float(hi)],
    }


def compute_focal_stats(outputs: list[AdapterOutput]) -> dict:
    """Compute per-model focal length statistics from repeated runs."""
    focals = [o.focal_length_px for o in outputs if o.focal_length_px is not None]
    if len(focals) < 2:
        return {"n": len(focals)}
    cleaned, outlier_info = remove_outliers_iqr(focals)
    arr = np.array(cleaned)
    mean_val = float(np.mean(arr))
    return {
        "n": len(focals),
        "mean": mean_val,
        "median": float(np.median(arr)),
        "std": float(np.std(arr)),
        "min": float(np.min(arr)),
        "max": float(np.max(arr)),
        "cv": float((np.std(arr) / mean_val) * 100) if mean_val != 0 else float("inf"),
        "outlier_info": outlier_info,
    }
