######################################################################################################
# SPDX-FileCopyrightText: Copyright (c) 2025-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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
######################################################################################################
"""
Shared platform detection and GPU metric helpers for perf benchmarks.

Centralises:
- DCGM/Prometheus vs PyNVML GPU metric source selection
- Platform / config_id normalisation
- GPU metric extraction from benchmark result dicts

All perf scripts should import from here instead of defining their own copies.
"""

from __future__ import annotations

from typing import Any, Dict

# Platforms that prefer Prometheus/DCGM metric keys when both sources exist.
# Substring matching: any of these appearing in a normalised platform string
# triggers the prometheus path (e.g. "rtvi-vlm-perf-report-thor" matches "thor").
PROMETHEUS_GPU_PLATFORMS = frozenset(
    {"jetson_thor", "agx_thor", "igx_thor", "dgx_spark", "thor", "spark", "jetson"}
)

# Map raw platform keys (from report dir names / config) to canonical KPI
# baseline config_ids used in rtvi-vlm.yaml.
CONFIG_ID_MAP: Dict[str, str] = {
    "thor": "jetson_thor",
    "spark": "dgx_spark",
    "rtx-pro": "rtx_pro",
    "rtx_pro": "rtx_pro",
    "h100": "h100",
    "b200": "b200",
    "l40s": "l40s",
}


def is_prometheus_platform(platform: str) -> bool:
    """Return True if *platform* should use prometheus (DCGM) GPU metrics.

    Normalises the input (lowercase, replace spaces/hyphens with underscores)
    and checks whether any known prometheus platform name appears as a
    substring.  This lets it match both exact keys (``"thor"``) and longer
    config_ids (``"rtvi-vlm-perf-report-thor"``).
    """
    normalised = platform.lower().replace(" ", "_").replace("-", "_")
    return any(p in normalised for p in PROMETHEUS_GPU_PLATFORMS)


def normalize_config_id(raw: str) -> str:
    """Map a raw platform key to the canonical KPI-baseline config_id."""
    return CONFIG_ID_MAP.get(raw, raw)


def get_gpu_metric(data: Dict[str, Any], metric_base: str, platform: str) -> float:
    """Extract a single GPU metric with DCGM-preferred source selection.

    Prefer ``prometheus_vlm_<metric>`` whenever a benchmark captured it,
    including dGPU platforms such as H100. Fall back to PyNVML-backed
    ``vlm_<metric>`` for older reports or runs without Prometheus/DCGM.

    Always returns a float (0.0 when absent).
    """
    val = data.get(f"prometheus_vlm_{metric_base}")
    if val is not None:
        return float(val)
    return float(data.get(f"vlm_{metric_base}", 0) or 0)
