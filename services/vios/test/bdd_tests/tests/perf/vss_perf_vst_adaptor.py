# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

"""
VST-SPECIFIC: Adapter from latency test csv_rows to VSS test_cases.

Maps VST benchmark output (csv_rows from test_latency.py) to the standard VSS
schema: list of {test_case_id, config, metrics} dicts.

Metrics expose metrics.latency.* and metrics.reliability.* for KPI paths
defined in vss_perf_vst_kpis.yaml.

Other microservices can use this as a pattern for their own adapter.
"""

from __future__ import annotations

from typing import Any, Dict, List

from pydantic import BaseModel, Field

from .vss_perf_common import build_and_save, discover_platform, upload_result_file

__all__ = [
    "csv_rows_to_test_cases",
    "build_and_save",
    "discover_platform",
    "upload_result_file",
]


# ---------------------------------------------------------------------------
# VST metric models
# ---------------------------------------------------------------------------


class LatencyMetrics(BaseModel):
    avg_ms: float = Field(0.0, description="Average latency (ms)")
    p50_ms: float = Field(0.0, description="Median latency (ms)")
    p90_ms: float = Field(0.0, description="P90 latency (ms)")
    p99_ms: float = Field(0.0, description="P99 latency (ms)")
    max_ms: float = Field(0.0, description="Maximum latency (ms)")


class ReliabilityMetrics(BaseModel):
    pass_count: int = Field(0, ge=0, description="Passed iterations")
    fail_count: int = Field(0, ge=0, description="Failed iterations")
    success_rate_percent: float = Field(0.0, ge=0, le=100, description="Success rate %")


class VSTTestCaseMetrics(BaseModel):
    latency: LatencyMetrics = Field(default_factory=LatencyMetrics)
    reliability: ReliabilityMetrics = Field(default_factory=ReliabilityMetrics)


class VSTTestCase(BaseModel):
    test_case_id: str = Field(..., description="Test case identifier")
    description: str = Field("", description="Human-readable description")
    config: Dict[str, Any] = Field(default_factory=dict, description="Test configuration")
    metrics: VSTTestCaseMetrics = Field(default_factory=VSTTestCaseMetrics)


# ---------------------------------------------------------------------------
# Adapter API
# ---------------------------------------------------------------------------


def _build_test_case_id(row: Dict[str, Any]) -> str:
    """Derive a stable test_case_id from a csv_row dict."""
    api = row.get("API", "unknown").replace(" ", "-")
    clip = str(row.get("Clip Duration", "")).strip()
    offset = row.get("Offset(ms)", 0)
    transcode = row.get("Transcode option", "").replace(" ", "-")

    if "concurrent" in clip.lower():
        return f"{api}_{clip.replace(' ', '-')}_{transcode}"

    return f"{api}_{clip}_{offset}ms_{transcode}"


def csv_rows_to_test_cases(
    csv_rows: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """
    Convert VST latency test csv_rows into standard VSS test_cases.

    Each csv_row (from test_latency.py's context.csv_rows) becomes one
    test_case dict suitable for ``vss_perf_common.build_and_save(test_cases=...)``.
    """
    test_cases: List[Dict[str, Any]] = []

    for row in csv_rows:
        pass_count = int(row.get("Pass Count", 0))
        fail_count = int(row.get("Fail Count", 0))
        total = pass_count + fail_count
        success_rate = round(pass_count / total * 100, 1) if total > 0 else 0.0

        tc = VSTTestCase(
            test_case_id=_build_test_case_id(row),
            description=(
                f"{row.get('API', '')} "
                f"{row.get('Clip Duration', '')} "
                f"offset={row.get('Offset(ms)', '')}ms "
                f"{row.get('Transcode option', '')}"
            ).strip(),
            config={
                "api": row.get("API", ""),
                "clip_duration": row.get("Clip Duration", ""),
                "offset_ms": int(row.get("Offset(ms)", 0)),
                "recency_pattern": row.get("Recency pattern", ""),
                "transcode_option": row.get("Transcode option", ""),
                "stream_type": row.get("Stream Type", ""),
            },
            metrics=VSTTestCaseMetrics(
                latency=LatencyMetrics(
                    avg_ms=float(row.get("Avg Latency(ms)", 0)),
                    p50_ms=float(row.get("P50(ms)", 0)),
                    p90_ms=float(row.get("P90(ms)", 0)),
                    p99_ms=float(row.get("P99(ms)", 0)),
                    max_ms=float(row.get("Max(ms)", 0)),
                ),
                reliability=ReliabilityMetrics(
                    pass_count=pass_count,
                    fail_count=fail_count,
                    success_rate_percent=success_rate,
                ),
            ),
        )
        test_cases.append(tc.model_dump(mode="json"))

    return test_cases
