######################################################################################################
# SPDX-FileCopyrightText: Copyright (c) 2025-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary
#
# NVIDIA CORPORATION, its affiliates and licensors retain all intellectual
# property and proprietary rights in and to this material, related
# documentation and any modifications thereto. Any use, reproduction,
# disclosure or distribution of this material and related documentation
# without an express license agreement from NVIDIA CORPORATION or
# its affiliates is strictly prohibited.
######################################################################################################

"""
RTVI VLM: Adapter from benchmark execution_results to VSS test_cases.

Maps RTVI VLM benchmark output to the standard schema: list of {test_case_id, metrics}
with metrics.latency, .throughput, .gpu. Supports modes: max_live_streams,
concurrent_live_streams, and file_burst (e2e_latency).

Output shape is aligned with VSS KPI paths:
- max_live_streams: metrics.throughput.max_sustainable_streams, metrics.latency.*,
  metrics.gpu.*
- concurrent_live_streams: metrics.latency.chunk_latency_seconds,
  metrics.latency.decode_seconds, metrics.gpu.*
- file_burst: metrics.latency.e2e_seconds, metrics.throughput.files_per_second,
  metrics.gpu.* per concurrency level

Dashboard-compatible output (build_dashboard_test_cases):
  Merges all scenarios into a single test case with _1t/_100t suffixed metrics
  matching rtvi-vlm.yaml KPI definitions.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from perf_platform import CONFIG_ID_MAP, get_gpu_metric, normalize_config_id
from perf_utils import load_json
from pydantic import BaseModel, Field

# Backward-compat alias for external consumers
DASHBOARD_CONFIG_ID_MAP = CONFIG_ID_MAP

logger = logging.getLogger(__name__)

# -----------------------------------------------------------------------------
# RTVI VLM → VSS metrics schema (Pydantic)
# -----------------------------------------------------------------------------


class LatencyMetrics(BaseModel):
    e2e_seconds: float = Field(0.0, description="End-to-end latency (s)")
    vlm_seconds: float = Field(0.0, description="VLM inference latency (s)")
    decode_seconds: float = Field(0.0, description="Decode latency (s)")
    chunk_latency_seconds: float = Field(0.0, description="Chunk E2E latency avg (s)")
    chunk_latency_p95_seconds: float = Field(0.0, description="Chunk E2E latency p95 (s)")


class ThroughputMetrics(BaseModel):
    max_sustainable_streams: int = Field(0, description="Max sustainable live streams")
    files_per_second: float = Field(0.0, description="File throughput (files/sec)")


class GPUMetrics(BaseModel):
    inference_usage_mean: float = Field(0.0, description="Inference GPU usage mean %")
    inference_usage_p90: float = Field(0.0, description="Inference GPU usage p90 %")
    inference_memory_mean: float = Field(0.0, description="GPU memory usage mean %")
    inference_nvdec_mean: float = Field(0.0, description="NVDEC utilization mean %")
    inference_nvdec_p90: float = Field(0.0, description="NVDEC utilization p90 %")
    gpu_temp_mean_c: float = Field(0.0, description="GPU temperature mean (C)")
    gpu_temp_p90_c: float = Field(0.0, description="GPU temperature p90 (C)")
    power_mean_watts: float = Field(0.0, description="GPU power mean (W)")
    power_p90_watts: float = Field(0.0, description="GPU power p90 (W)")


class VLMTestCaseMetrics(BaseModel):
    latency: LatencyMetrics = Field(default_factory=LatencyMetrics)
    throughput: ThroughputMetrics = Field(default_factory=ThroughputMetrics)
    gpu: GPUMetrics = Field(default_factory=GPUMetrics)


class VLMTestCase(BaseModel):
    test_case_id: str = Field(..., description="Test case id")
    metrics: VLMTestCaseMetrics = Field(default_factory=VLMTestCaseMetrics)


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------


def _gpu_metrics_from_dict(d: Dict[str, Any], platform: str = "") -> GPUMetrics:
    """Build GPUMetrics from raw result dict.

    Prefer prometheus_vlm_* (DCGM) for GPU/nvdec. Fall back to PyNVML-backed
    vlm_* values for older reports or runs without Prometheus/DCGM samples.
    """
    return GPUMetrics(
        inference_usage_mean=get_gpu_metric(d, "gpu_usage_mean", platform),
        inference_usage_p90=get_gpu_metric(d, "gpu_usage_p90", platform),
        inference_memory_mean=get_gpu_metric(d, "gpu_memory_mean", platform),
        inference_nvdec_mean=get_gpu_metric(d, "nvdec_usage_mean", platform),
        inference_nvdec_p90=get_gpu_metric(d, "nvdec_usage_p90", platform),
        gpu_temp_mean_c=float(d.get("prometheus_vlm_gpu_temp_mean_c", 0) or 0),
        gpu_temp_p90_c=float(d.get("prometheus_vlm_gpu_temp_p90_c", 0) or 0),
        power_mean_watts=float(d.get("prometheus_vlm_power_mean_watts", 0) or 0),
        power_p90_watts=float(d.get("prometheus_vlm_power_p90_watts", 0) or 0),
    )


def _mean_iter_gpu(iters: List[Dict[str, Any]], platform: str = "") -> GPUMetrics:
    """Average GPUMetrics across successful iterations."""
    if not iters:
        return GPUMetrics()
    gpu_list = [_gpu_metrics_from_dict(it, platform) for it in iters]
    n = len(gpu_list)
    return GPUMetrics(
        inference_usage_mean=sum(g.inference_usage_mean for g in gpu_list) / n,
        inference_usage_p90=sum(g.inference_usage_p90 for g in gpu_list) / n,
        inference_memory_mean=sum(g.inference_memory_mean for g in gpu_list) / n,
        inference_nvdec_mean=sum(g.inference_nvdec_mean for g in gpu_list) / n,
        inference_nvdec_p90=sum(g.inference_nvdec_p90 for g in gpu_list) / n,
        gpu_temp_mean_c=sum(g.gpu_temp_mean_c for g in gpu_list) / n,
        gpu_temp_p90_c=sum(g.gpu_temp_p90_c for g in gpu_list) / n,
        power_mean_watts=sum(g.power_mean_watts for g in gpu_list) / n,
        power_p90_watts=sum(g.power_p90_watts for g in gpu_list) / n,
    )


def _safe_mean(values: List[float]) -> float:
    return sum(values) / len(values) if values else 0.0


# -----------------------------------------------------------------------------
# Adapter: max_live_streams
# -----------------------------------------------------------------------------


def _adapt_max_live_streams(
    execution_results: Dict[str, Any],
    report_dir: str,
    platform: str,
) -> List[VLMTestCase]:
    """Adapt max_live_streams execution_results to VLMTestCase list."""
    out: List[VLMTestCase] = []
    for tc in execution_results.get("test_cases", []):
        if not tc.get("success"):
            continue
        test_case_id = tc.get("test_case_id", "")
        metrics = VLMTestCaseMetrics(
            throughput=ThroughputMetrics(
                max_sustainable_streams=tc.get("max_sustainable_streams", 0),
            ),
            latency=LatencyMetrics(
                chunk_latency_seconds=tc.get("last_stable_moving_average_latency", 0),
                chunk_latency_p95_seconds=tc.get("last_stable_p95", 0),
                decode_seconds=tc.get("decode_latency_seconds_avg", 0),
            ),
            gpu=_gpu_metrics_from_dict(tc, platform),
        )
        out.append(VLMTestCase(test_case_id=test_case_id, metrics=metrics))
    return out


# -----------------------------------------------------------------------------
# Adapter: concurrent_live_streams
# -----------------------------------------------------------------------------


def _adapt_concurrent_live_streams(
    execution_results: Dict[str, Any],
    report_dir: str,
    platform: str,
) -> List[VLMTestCase]:
    """Adapt concurrent_live_streams execution_results to VLMTestCase list.

    Reads test_case_summary.json for cross-iteration means. Falls back to
    iteration_results in execution_summary.
    """
    out: List[VLMTestCase] = []
    results_path = Path(report_dir)

    for tc in execution_results.get("test_cases", []):
        if not tc.get("success"):
            continue
        test_case_id = tc.get("test_case_id", "")

        # Prefer test_case_summary.json (has cross-iteration means)
        summary_path = results_path / test_case_id / "test_case_summary.json"
        summary = load_json(summary_path, verbose=False)

        if summary:
            avg_lat = summary.get("mean_avg_latency", 0)
            p95_lat = summary.get("mean_p95_latency", 0)
            decode_lat = summary.get("mean_decode_latency_seconds_avg", 0)
            # Average GPU metrics from iteration_results
            iters = [r for r in summary.get("iteration_results", []) if r.get("success", False)]
            gpu = _mean_iter_gpu(iters, platform)
        else:
            # Fallback: iteration_results from execution_summary
            iters = [r for r in tc.get("iteration_results", []) if r.get("success", False)]
            avg_lat = _safe_mean([r.get("avg_latency", 0) for r in iters])
            p95_lat = _safe_mean([r.get("p95_latency", 0) for r in iters])
            decode_lat = _safe_mean([r.get("decode_latency_seconds_avg", 0) for r in iters])
            gpu = _mean_iter_gpu(iters, platform)

        metrics = VLMTestCaseMetrics(
            latency=LatencyMetrics(
                chunk_latency_seconds=avg_lat,
                chunk_latency_p95_seconds=p95_lat,
                decode_seconds=decode_lat,
            ),
            gpu=gpu,
        )
        out.append(VLMTestCase(test_case_id=test_case_id, metrics=metrics))
    return out


# -----------------------------------------------------------------------------
# Adapter: file_burst (e2e_latency)
# -----------------------------------------------------------------------------


def _adapt_file_burst(
    execution_results: Dict[str, Any],
    report_dir: str,
    platform: str,
) -> List[VLMTestCase]:
    """Adapt file_burst execution_results to VLMTestCase list.

    Produces one test case per (video, concurrency_level) pair.
    Reads test_case_summary.json → concurrency_summary for cross-iteration
    means. Falls back to iteration_results → concurrency_results.
    """
    out: List[VLMTestCase] = []
    results_path = Path(report_dir)

    for tc in execution_results.get("test_cases", []):
        if not tc.get("success"):
            continue
        test_case_id = tc.get("test_case_id", "")

        # Prefer test_case_summary with concurrency_summary
        summary_path = results_path / test_case_id / "test_case_summary.json"
        summary = load_json(summary_path, verbose=False)

        if summary and "concurrency_summary" in summary:
            cs = summary["concurrency_summary"]
            for c_str, stats in sorted(cs.items(), key=lambda x: int(x[0])):
                concurrency = int(c_str)
                tc_id = f"{test_case_id}_c{concurrency}"
                metrics = VLMTestCaseMetrics(
                    latency=LatencyMetrics(
                        e2e_seconds=stats.get("mean_avg_latency", 0),
                    ),
                    throughput=ThroughputMetrics(
                        files_per_second=stats.get("mean_throughput", 0),
                    ),
                )
                out.append(VLMTestCase(test_case_id=tc_id, metrics=metrics))
        else:
            # Fallback: first successful iteration's concurrency_results
            for it in tc.get("iteration_results", []):
                if not it.get("success"):
                    continue
                for cr in it.get("concurrency_results", []):
                    concurrency = cr.get("concurrency_level", 0)
                    tc_id = f"{test_case_id}_c{concurrency}"
                    metrics = VLMTestCaseMetrics(
                        latency=LatencyMetrics(
                            e2e_seconds=cr.get("avg_latency", 0),
                        ),
                        throughput=ThroughputMetrics(
                            files_per_second=cr.get("throughput_files_per_second", 0),
                        ),
                        gpu=_gpu_metrics_from_dict(cr, platform),
                    )
                    out.append(VLMTestCase(test_case_id=tc_id, metrics=metrics))
                break  # Only first successful iteration
    return out


# -----------------------------------------------------------------------------
# Public API
# -----------------------------------------------------------------------------


def rtvi_vlm_execution_results_to_test_cases(
    execution_results: Dict[str, Any],
    *,
    platform: str = "",
    validate: bool = True,
) -> List[Union[Dict[str, Any], VLMTestCase]]:
    """
    Convert RTVI VLM execution_results to standard VSS test_cases.

    Args:
        execution_results: The execution_summary dict from a benchmark run
            (loaded from execution_summary.json).
        platform: Platform key for GPU metric source selection
            (e.g. "h100", "thor"). Empty string still prefers DCGM fields
            and falls back to PyNVML-backed vlm_* values when needed.
        validate: If True, return list of dicts (model_dump); else return
            VLMTestCase objects.

    Returns:
        List of test case dicts (validate=True) or VLMTestCase objects.
    """
    mode = execution_results.get("benchmark_mode", "")
    results_dir = execution_results.get("scenario_dir", "")

    if mode == "max_live_streams":
        cases = _adapt_max_live_streams(execution_results, results_dir, platform)
    elif mode == "concurrent_live_streams":
        cases = _adapt_concurrent_live_streams(execution_results, results_dir, platform)
    elif mode == "file_burst":
        cases = _adapt_file_burst(execution_results, results_dir, platform)
    else:
        logger.warning("Unknown benchmark mode: %s", mode)
        return []

    if validate:
        return [c.model_dump(mode="json") for c in cases]
    return cases


# =============================================================================
# Dashboard-compatible output: merged _1t/_100t metrics per rtvi-vlm.yaml KPIs
# =============================================================================


def _detect_token_suffix(scenario_name: str) -> Optional[str]:
    """Return '_1t' or '_100t' based on scenario directory name."""
    if re.search(r"[_-]1[_-]?token", scenario_name) or re.search(r"[_-]1t[_-]", scenario_name):
        return "_1t"
    if re.search(r"[_-]100[_-]?token", scenario_name) or re.search(r"[_-]100t[_-]", scenario_name):
        return "_100t"
    return None


def _find_warehouse_10s_tc(execution_results: Dict[str, Any]) -> Optional[Dict]:
    """Find the warehouse_10s test case from execution_results."""
    for tc in execution_results.get("test_cases", []):
        if not tc.get("success"):
            continue
        tid = tc.get("test_case_id", "")
        if "warehouse_10s" in tid or "warehouse_gopro_10s" in tid:
            return tc
    return None


def _extract_stream_count(test_case_id: str) -> Optional[int]:
    """Extract stream count from concurrent_live_streams test_case_id."""
    m = re.search(r"(\d+)streams$", test_case_id)
    return int(m.group(1)) if m else None


def _pick_gpu_usage_mean(tc: Dict[str, Any], platform: str) -> float:
    """Platform-aware GPU usage mean from max_live_streams test case."""
    return get_gpu_metric(tc, "gpu_usage_mean", platform)


def _find_scenario(report_dir: Path, *patterns: str) -> Optional[str]:
    """Find first existing scenario directory matching any of the given patterns."""
    for pattern in patterns:
        for d in sorted(report_dir.iterdir()):
            if d.is_dir() and re.search(pattern, d.name):
                if (d / "execution_summary.json").exists():
                    return d.name
    return None


def _avg_gpu_for_concurrency(
    iteration_results: List[Dict[str, Any]], concurrency: int, platform: str
) -> Dict[str, float]:
    """Average GPU/telemetry metrics across iterations for a given concurrency level."""
    samples: List[Dict[str, Any]] = []
    for ir in iteration_results:
        if not ir.get("success", False):
            continue
        for cr in ir.get("concurrency_results", []):
            if cr.get("concurrency_level") == concurrency:
                samples.append(cr)
                break
    if not samples:
        return {}
    return {
        "usage_mean_pct": _safe_mean(
            [get_gpu_metric(s, "gpu_usage_mean", platform) for s in samples]
        ),
        "usage_p90_pct": _safe_mean(
            [get_gpu_metric(s, "gpu_usage_p90", platform) for s in samples]
        ),
        "memory_mean_pct": _safe_mean(
            [float(s.get("vlm_gpu_memory_mean", 0) or 0) for s in samples]
        ),
        "nvdec_mean_pct": _safe_mean(
            [get_gpu_metric(s, "nvdec_usage_mean", platform) for s in samples]
        ),
        "nvdec_p90_pct": _safe_mean(
            [get_gpu_metric(s, "nvdec_usage_p90", platform) for s in samples]
        ),
        "cpu_util_mean_pct": _safe_mean(
            [float(s.get("nodeexporter_cpu_util_mean", 0) or 0) for s in samples]
        ),
        "cpu_util_p90_pct": _safe_mean(
            [float(s.get("nodeexporter_cpu_util_p90", 0) or 0) for s in samples]
        ),
    }


def _file_burst_test_cases(
    report_dir: Path, scenario_name: str, token_suffix: str, platform: str
) -> List[Dict[str, Any]]:
    """Build one test case per (video, concurrency) from file_burst scenario.

    Mirrors the XLSX Summary sheet: each row is a concurrency level with
    avg/p50/p90/p95/p99 latency, throughput, and GPU telemetry.
    GPU metrics are averaged from iteration_results.concurrency_results
    (not in concurrency_summary which only has latency/throughput).
    """
    scenario_path = report_dir / scenario_name
    exec_summary = load_json(scenario_path / "execution_summary.json", verbose=False)
    if not exec_summary:
        return []

    out: List[Dict[str, Any]] = []
    for tc in exec_summary.get("test_cases", []):
        if not tc.get("success"):
            continue
        tc_id = tc.get("test_case_id", "")
        summary = load_json(scenario_path / tc_id / "test_case_summary.json", verbose=False)
        if not summary or "concurrency_summary" not in summary:
            continue

        # Derive short video label (e.g. "10s", "10min", "60min")
        video_label = "unknown"
        for tag in ("120min", "60min", "30min", "10min", "10s"):
            if tag in tc_id:
                video_label = tag
                break

        iters = summary.get("iteration_results", [])
        cs = summary["concurrency_summary"]
        for c_str, stats in sorted(cs.items(), key=lambda x: int(x[0])):
            concurrency = int(c_str)
            gpu = _avg_gpu_for_concurrency(iters, concurrency, platform)
            test_case = {
                "test_case_id": f"file_burst_{video_label}_c{concurrency}{token_suffix}",
                "metrics": {
                    "benchmark_mode": "file_burst",
                    "video_label": video_label,
                    "concurrency_level": concurrency,
                    "latency": {
                        "avg_seconds": stats.get("mean_avg_latency", 0),
                        "p50_seconds": stats.get("mean_p50_latency", 0),
                        "p90_seconds": stats.get("mean_p90_latency", 0),
                        "p95_seconds": stats.get("mean_p95_latency", 0),
                        "p99_seconds": stats.get("mean_p99_latency", 0),
                    },
                    "throughput": {
                        "files_per_second": stats.get("mean_throughput", 0),
                    },
                    "gpu": gpu,
                },
            }
            out.append(test_case)
    return out


def _max_live_streams_test_cases(
    report_dir: Path, scenario_name: str, token_suffix: str, platform: str
) -> List[Dict[str, Any]]:
    """Build test cases from max_live_streams scenario.

    Mirrors the XLSX Summary sheet: max_sustainable_streams, all latency
    percentiles, GPU/NVDEC/memory utilisation, decode latency.
    """
    exec_summary = load_json(report_dir / scenario_name / "execution_summary.json", verbose=False)
    if not exec_summary:
        return []

    out: List[Dict[str, Any]] = []
    for tc in exec_summary.get("test_cases", []):
        if not tc.get("success"):
            continue

        # Derive resolution label from suffix: _8k_* -> 8k, _2k_* -> 2k
        res_label = "8k" if "448" in scenario_name else "2k"

        test_case = {
            "test_case_id": f"max_live_streams{token_suffix}",
            "metrics": {
                "benchmark_mode": "max_live_streams",
                "resolution": res_label,
                "throughput": {
                    "max_sustainable_streams": tc.get("max_sustainable_streams", 0),
                    "total_streams_tested": tc.get("total_streams_tested", 0),
                },
                "latency": {
                    "avg_seconds": tc.get("last_stable_moving_average_latency", 0),
                    "p95_seconds": tc.get("last_stable_p95", 0),
                    "decode_seconds": tc.get("decode_latency_seconds_avg", 0),
                },
                "gpu": {
                    "usage_mean_pct": get_gpu_metric(tc, "gpu_usage_mean", platform),
                    "usage_p90_pct": get_gpu_metric(tc, "gpu_usage_p90", platform),
                    "nvdec_mean_pct": get_gpu_metric(tc, "nvdec_usage_mean", platform),
                    "nvdec_p90_pct": get_gpu_metric(tc, "nvdec_usage_p90", platform),
                    "memory_mean_pct": float(tc.get("vlm_gpu_memory_mean", 0) or 0),
                    "power_mean_watts": float(tc.get("prometheus_vlm_power_mean_watts", 0) or 0),
                    "power_p90_watts": float(tc.get("prometheus_vlm_power_p90_watts", 0) or 0),
                    "temp_mean_c": float(tc.get("prometheus_vlm_gpu_temp_mean_c", 0) or 0),
                    "temp_p90_c": float(tc.get("prometheus_vlm_gpu_temp_p90_c", 0) or 0),
                },
                "system": {
                    "cpu_util_mean_pct": float(tc.get("nodeexporter_cpu_util_mean", 0) or 0),
                    "cpu_util_p90_pct": float(tc.get("nodeexporter_cpu_util_p90", 0) or 0),
                    "memory_used_mean_pct": float(
                        tc.get("nodeexporter_memory_used_pct_mean", 0) or 0
                    ),
                    "load1_mean": float(tc.get("nodeexporter_load1_mean", 0) or 0),
                },
            },
        }
        out.append(test_case)
    return out


def _concurrency_test_cases(
    report_dir: Path, scenario_name: str, token_suffix: str, platform: str
) -> List[Dict[str, Any]]:
    """Build one test case per stream_count from concurrent_live_streams scenario.

    Mirrors the XLSX Summary sheet: stream_count, avg/p90/p95/p99/max latency,
    decode latency, GPU utilisation.
    """
    scenario_path = report_dir / scenario_name
    exec_summary = load_json(scenario_path / "execution_summary.json", verbose=False)
    if not exec_summary:
        return []

    out: List[Dict[str, Any]] = []
    for tc in exec_summary.get("test_cases", []):
        if not tc.get("success"):
            continue
        tc_id = tc.get("test_case_id", "")
        n_streams = _extract_stream_count(tc_id)
        if n_streams is None:
            continue

        summary = load_json(scenario_path / tc_id / "test_case_summary.json", verbose=False)
        if summary:
            avg_lat = summary.get("mean_avg_latency", 0)
            p90_lat = summary.get("mean_p90_latency", 0)
            p95_lat = summary.get("mean_p95_latency", 0)
            p99_lat = summary.get("mean_p99_latency", 0)
            max_lat = summary.get("mean_max_latency", 0)
            decode_lat = summary.get("mean_decode_latency_seconds_avg", 0)
            # Average GPU from iteration_results
            iters = [r for r in summary.get("iteration_results", []) if r.get("success", False)]
        else:
            iters = [r for r in tc.get("iteration_results", []) if r.get("success", False)]
            avg_lat = _safe_mean([r.get("avg_latency", 0) for r in iters])
            p90_lat = _safe_mean([r.get("p90_latency", 0) for r in iters])
            p95_lat = _safe_mean([r.get("p95_latency", 0) for r in iters])
            p99_lat = _safe_mean([r.get("p99_latency", 0) for r in iters])
            max_lat = _safe_mean([r.get("max_latency", 0) for r in iters])
            decode_lat = _safe_mean([r.get("decode_latency_seconds_avg", 0) for r in iters])

        # GPU metrics averaged across iterations
        def _mean_iter_metric(base: str, iters=iters, platform=platform) -> float:
            if not iters:
                return 0.0
            return _safe_mean([get_gpu_metric(it, base, platform) for it in iters])

        gpu_mem = (
            _safe_mean([get_gpu_metric(it, "gpu_memory_mean", platform) for it in iters])
            if iters
            else 0.0
        )

        test_case = {
            "test_case_id": f"concurrency_{n_streams}streams{token_suffix}",
            "metrics": {
                "benchmark_mode": "concurrent_live_streams",
                "stream_count": n_streams,
                "latency": {
                    "avg_seconds": avg_lat,
                    "p90_seconds": p90_lat,
                    "p95_seconds": p95_lat,
                    "p99_seconds": p99_lat,
                    "max_seconds": max_lat,
                    "decode_seconds": decode_lat,
                },
                "gpu": {
                    "usage_mean_pct": _mean_iter_metric("gpu_usage_mean"),
                    "usage_p90_pct": _mean_iter_metric("gpu_usage_p90"),
                    "memory_mean_pct": gpu_mem,
                    "nvdec_mean_pct": _mean_iter_metric("nvdec_usage_mean"),
                    "nvdec_p90_pct": _mean_iter_metric("nvdec_usage_p90"),
                },
            },
        }
        out.append(test_case)
    return out


def build_dashboard_test_cases(
    report_dir: str,
    platform: str,
) -> List[Dict[str, Any]]:
    """Build test cases mirroring the XLSX report structure.

    Produces one test case per row in the XLSX reports:
    - file_burst: one per (video, concurrency_level, token_config)
    - max_live_streams: one per (resolution, token_config)
    - concurrent_live_streams: one per (stream_count, token_config)

    Each test case contains all columns from the XLSX as nested metrics.

    Args:
        report_dir: Path to the perf report directory.
        platform: Platform key for GPU metric source selection.

    Returns:
        List of test case dicts mirroring XLSX rows.
    """
    rdir = Path(report_dir)
    config_id = normalize_config_id(platform)
    test_cases: List[Dict[str, Any]] = []

    # --- File burst (e2e_latency) scenarios ---
    for suffix, patterns in [
        ("_1t", [r"e2e_latency_1_token$", r"e2e_latency_1token$", r"file_burst_1_token$"]),
        ("_100t", [r"e2e_latency_100_token$", r"e2e_latency_100token$", r"file_burst_100_token$"]),
    ]:
        scenario = _find_scenario(rdir, *patterns)
        if scenario:
            test_cases.extend(_file_burst_test_cases(rdir, scenario, suffix, platform))

    # --- Max live streams: 2K (372x372x30) and 8K (448x448x80) ---
    for suffix, patterns in [
        ("_8k_1t", [r"max_live_streams_test_1_token_448$"]),
        ("_8k_100t", [r"max_live_streams_test_100_token_448$"]),
        ("_2k_1t", [r"max_live_streams_test_1_token$"]),
        ("_2k_100t", [r"max_live_streams_test_100_token$"]),
    ]:
        scenario = _find_scenario(rdir, *patterns)
        if scenario:
            test_cases.extend(_max_live_streams_test_cases(rdir, scenario, suffix, platform))

    # --- Concurrency tests ---
    for suffix, patterns in [
        ("_1t", [r"concurrency_test_1_token$"]),
        ("_100t", [r"concurrency_test_100_token$"]),
    ]:
        scenario = _find_scenario(rdir, *patterns)
        if scenario:
            test_cases.extend(_concurrency_test_cases(rdir, scenario, suffix, platform))

    logger.info(
        "Dashboard test cases for %s: %d test cases",
        config_id,
        len(test_cases),
    )

    return test_cases
