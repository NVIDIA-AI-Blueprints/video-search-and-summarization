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
LVS-SPECIFIC: Adapter from LVS execution_results to VSS test_cases.

Maps LVS benchmark output to the standard schema: list of {test_case_id, metrics}
with metrics.latency, .gpu. Other microservices can add their own *_adapter module
and use vss_perf_common for build_and_save + upload.

Output shape is aligned with vss_perf_lvs_kpis.yaml: single_file test cases expose
metrics.latency.*, metrics.gpu.* for KPI paths.
File_burst test cases use metrics.concurrency_results and metrics.optimal_target_concurrency.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional, Union

from pydantic import BaseModel, Field

# -----------------------------------------------------------------------------
# LVS → VSS metrics schema (Pydantic)
# -----------------------------------------------------------------------------


class LatencyMetrics(BaseModel):
    e2e_seconds: float = Field(0.0, description="End-to-end latency (s)")
    vlm_pipeline_seconds: float = Field(0.0, description="VLM pipeline latency (s)")
    ca_rag_seconds: float = Field(0.0, description="Context-aware RAG latency (s)")
    decode_seconds: Optional[float] = Field(None, description="Decode latency (s)")


class GPUMetrics(BaseModel):
    vlm_gpu_usage_mean: float = Field(0.0, description="VLM GPU usage mean %")
    vlm_gpu_memory_mean: Optional[float] = Field(0.0, description="VLM GPU memory mean %")
    llm_gpu_usage_mean: float = Field(0.0, description="LLM GPU usage mean %")
    llm_gpu_memory_mean: Optional[float] = Field(0.0, description="LLM GPU memory mean %")


class LVSTestCaseMetrics(BaseModel):
    latency: LatencyMetrics = Field(default_factory=LatencyMetrics)
    gpu: GPUMetrics = Field(default_factory=GPUMetrics)


class LVSTestCase(BaseModel):
    test_case_id: str = Field(..., description="Test case id")
    description: str = Field("", description="Human-readable description")
    status: str = Field("PASS", description="PASS | FAIL")
    config: Dict[str, Any] = Field(default_factory=dict, description="Test case config")
    metrics: LVSTestCaseMetrics = Field(default_factory=LVSTestCaseMetrics)


# -----------------------------------------------------------------------------
# File-burst: concurrency_results + optimal_target_concurrency (full structure)
# -----------------------------------------------------------------------------


class ConcurrencyResultItem(BaseModel):
    """One concurrency level result for file_burst."""

    concurrency_level: int = Field(1, ge=1)
    e2e_seconds: float = Field(0.0, ge=0)
    completed_files: int = Field(0, ge=0)
    failed_files: int = Field(0, ge=0)
    throughput_files_per_second: float = Field(0.0, ge=0)
    avg_latency: float = Field(0.0, ge=0)
    p90_latency: float = Field(0.0, ge=0)
    gpu: GPUMetrics = Field(default_factory=GPUMetrics)


class OptimalConcurrencyItem(BaseModel):
    """Optimal concurrency meeting target latency."""

    concurrency_level: int = Field(1, ge=1)
    throughput_files_per_second: float = Field(0.0, ge=0)
    avg_latency_seconds: float = Field(0.0, ge=0)
    p90_latency_seconds: float = Field(0.0, ge=0)


# -----------------------------------------------------------------------------
# Adapter API
# -----------------------------------------------------------------------------


def _load_json(path: str) -> Dict[str, Any]:
    try:
        with open(path, "r") as f:
            return json.load(f)
    except Exception:
        return {}


def _gpu_metrics_from_dict(d: Dict[str, Any]) -> GPUMetrics:
    """Build GPUMetrics from raw dict (single_file or file_burst result)."""
    return GPUMetrics(
        vlm_gpu_usage_mean=d.get("vlm_gpu_usage_mean", 0),
        vlm_gpu_memory_mean=d.get("vlm_gpu_memory_mean", 0),
        llm_gpu_usage_mean=d.get("llm_gpu_usage_mean", 0),
        llm_gpu_memory_mean=d.get("llm_gpu_memory_mean", 0),
    )


def _flatten_optimal_target_concurrency(optimal_dict: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Normalize file_burst optimal_target_concurrency to flat KPI schema.

    file_burst_benchmark stores binary-search output:
      { estimated_concurrency, actual_result: { avg_latency, p90_latency, ... } }
    MinIO KPI paths expect:
      { concurrency_level, avg_latency_seconds, p90_latency_seconds, throughput_files_per_second }
    """
    if not optimal_dict or not isinstance(optimal_dict, dict) or optimal_dict.get("error"):
        return None

    if "concurrency_level" in optimal_dict and "estimated_concurrency" not in optimal_dict:
        return OptimalConcurrencyItem(
            concurrency_level=optimal_dict.get("concurrency_level", 1),
            throughput_files_per_second=optimal_dict.get("throughput_files_per_second", 0),
            avg_latency_seconds=optimal_dict.get("avg_latency_seconds", 0),
            p90_latency_seconds=optimal_dict.get("p90_latency_seconds", 0),
        ).model_dump(mode="json")

    actual = optimal_dict.get("actual_result") or {}
    concurrency_level = optimal_dict.get("estimated_concurrency") or actual.get("concurrency_level", 1)
    return OptimalConcurrencyItem(
        concurrency_level=concurrency_level,
        throughput_files_per_second=actual.get("throughput_files_per_second", 0),
        avg_latency_seconds=actual.get("avg_latency_seconds", actual.get("avg_latency", 0)),
        p90_latency_seconds=actual.get("p90_latency_seconds", actual.get("p90_latency", 0)),
    ).model_dump(mode="json")


def execution_results_to_test_cases(
    benchmark: Any, execution_results: Dict[str, Any], *, validate: bool = True
) -> List[Union[Dict[str, Any], LVSTestCase]]:
    """
    Convert LVS execution_results to standard VSS test_cases.
    Returns list of dicts (when validate=True) for vss_perf_common.build_and_save(..., test_cases=...).
    Single-file: one dict per test case with test_case_id, description, status, config,
    and metrics (latency, gpu).
    File-burst: one dict with test_case_id, config, metrics (concurrency_results,
    optimal_target_concurrency).
    """
    single_file_out: List[LVSTestCase] = []
    file_burst_out: List[Dict[str, Any]] = []
    results_dir = execution_results.get("scenario_dir", "")
    mode = execution_results.get("benchmark_mode", "single_file")

    for tc in execution_results.get("test_cases", []):
        test_case_id = tc.get("test_case_id", "")
        video_url = tc.get("video_url", "")
        video_basename = os.path.basename(video_url)
        chunk_s = tc.get("chunk_size", 0)

        if mode == "file_burst":
            concurrency_results = []
            for res in tc.get("concurrency_results", []):
                concurrency_results.append(
                    ConcurrencyResultItem(
                        concurrency_level=res.get("concurrency_level", 1),
                        e2e_seconds=res.get("e2e_latency_seconds", 0),
                        completed_files=res.get("completed_files", 0),
                        failed_files=res.get("failed_files", 0),
                        throughput_files_per_second=res.get("throughput_files_per_second", 0),
                        avg_latency=res.get("avg_latency", 0),
                        p90_latency=res.get("p90_latency", 0),
                        gpu=_gpu_metrics_from_dict(res),
                    ).model_dump(mode="json")
                )
            optimal_dict = tc.get("optimal_target_concurrency") or {}
            optimal = _flatten_optimal_target_concurrency(optimal_dict)
            file_burst_out.append(
                {
                    "test_case_id": test_case_id,
                    "description": f"{video_basename} @ {chunk_s}s",
                    "status": "PASS" if tc.get("success", False) else "FAIL",
                    "error": tc.get("error", ""),
                    "config": {
                        "video_url": video_url,
                        "chunk_size_seconds": chunk_s,
                        "concurrency_levels_tested": tc.get("concurrency_levels_tested", []),
                        "target_latency_seconds": tc.get("target_latency_seconds", 0),
                    },
                    "metrics": {
                        "concurrency_results": concurrency_results,
                        "optimal_target_concurrency": optimal,
                    },
                }
            )
        else:
            successful_iterations = [i for i in tc.get("iteration_results", []) if i.get("success")]

            # Collect per-iteration values to average across all successful iterations
            walls, vlm_pipeline, decode, ca_rag = [], [], [], []
            gpu_dicts = []

            for iteration in successful_iterations:
                idx = iteration.get("iteration", 1)
                metrics_dict = iteration.get("api_metrics", {})
                iter_dir = os.path.join(results_dir, test_case_id, f"iteration_{idx}")

                walls.append(iteration.get("wall_clock_seconds", 0))
                vlm_pipeline.append(metrics_dict.get("vlm_pipeline_latency_seconds_latest", 0))
                decode_value = metrics_dict.get("decode_latency_seconds_latest")
                if isinstance(decode_value, (int, float)) and not isinstance(
                    decode_value, bool
                ):
                    decode.append(float(decode_value))
                ca_rag.append(metrics_dict.get("ca_rag_latency_seconds_latest", 0))

                gpu_file = os.path.join(iter_dir, f"gpu_metrics_iter_{idx}_stats.json")
                gpu_dicts.append(
                    benchmark.process_gpu_stats(gpu_file)
                    if hasattr(benchmark, "process_gpu_stats")
                    else {}
                )

            def _mean(values):
                return sum(values) / len(values) if values else 0.0

            def _mean_gpu(dicts, key, default=0.0):
                values = [d.get(key, 0) for d in dicts if d.get(key, 0) is not None]
                return _mean(values) if values else default

            avg_gpu = GPUMetrics(
                vlm_gpu_usage_mean=_mean_gpu(gpu_dicts, "vlm_gpu_usage_mean"),
                vlm_gpu_memory_mean=_mean_gpu(gpu_dicts, "vlm_gpu_memory_mean", None),
                llm_gpu_usage_mean=_mean_gpu(gpu_dicts, "llm_gpu_usage_mean"),
                llm_gpu_memory_mean=_mean_gpu(gpu_dicts, "llm_gpu_memory_mean", None),
            )

            video_duration = os.path.splitext(video_basename)[0] if video_basename else ""

            metrics = LVSTestCaseMetrics(
                latency=LatencyMetrics(
                    e2e_seconds=_mean(walls),
                    vlm_pipeline_seconds=_mean(vlm_pipeline),
                    ca_rag_seconds=_mean(ca_rag),
                    decode_seconds=_mean(decode) if decode else None,
                ),
                gpu=avg_gpu,
            )
            single_file_out.append(
                LVSTestCase(
                    test_case_id=test_case_id,
                    description=f"{video_duration} video summarization",
                    status="PASS" if tc.get("success", False) else "FAIL",
                    config={
                        "video_duration": video_duration,
                        "error": tc.get("error", ""),
                        "successful_iterations": tc.get("successful_iterations", 0),
                        "requested_iterations": tc.get("iterations", 0),
                    },
                    metrics=metrics,
                )
            )

    if validate:
        return [m.model_dump(mode="json") for m in single_file_out] + file_burst_out
    return single_file_out + file_burst_out
