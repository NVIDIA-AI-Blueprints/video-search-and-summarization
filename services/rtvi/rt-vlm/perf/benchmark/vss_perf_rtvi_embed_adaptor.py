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
RTVI Embed: Adapter from benchmark execution_results to VSS test_cases.

Maps RTVI Embed benchmark output to the standard schema: list of {test_case_id, metrics}
with metrics.latency, .throughput, .gpu. Supports modes: single_file (video embeddings),
text_embedding, text_embedding_concurrency, concurrency, and live-stream modes.
Other microservices can add their own *_adapter module and use vss_perf_common for
build_and_save + upload.

Output shape is aligned with VSS KPI paths: single-file test cases expose
metrics.latency.*, metrics.throughput.*, metrics.gpu.*. Concurrency-style modes
use metrics.concurrency_results and metrics.optimal_target_concurrency.

Standalone execution:
  When run as a script (python vss_perf_rtvi_embed_adaptor.py), reads all *_report.xlsx
  files from a performance reports folder (e.g. rtvi-embed-perf-report) and writes a
  combined JSON with test_cases. Use --reports-dir and --output (-o); use -o - for stdout.
  With --upload (-u), builds the full VSS result via vss_perf_common.build_and_save and
  uploads to MinIO; --service (default RTVI-Embed) sets the service name.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from typing import Any, Dict, List, Optional, Union

import pandas as pd
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

try:
    from vss_perf_common import (  # isort: skip
        build_and_save,
        discover_platform,
        upload_result_file,
    )
except ImportError:
    build_and_save = None  # type: ignore[assignment, misc]
    discover_platform = None  # type: ignore[assignment, misc]
    upload_result_file = None  # type: ignore[assignment, misc]

# -----------------------------------------------------------------------------
# RTVI Embed → VSS metrics schema (Pydantic)
# -----------------------------------------------------------------------------


class LatencyMetrics(BaseModel):
    e2e_seconds: float = Field(0.0, description="End-to-end latency (s)")
    inference_pipeline_seconds: float = Field(0.0, description="Inference pipeline latency (s)")
    inference_seconds: float = Field(0.0, description="Inference latency (s)")
    decode_seconds: float = Field(0.0, description="Decode latency (s)")
    chunk_latency_seconds: float = Field(0.0, description="Chunk latency (s)")


class ThroughputMetrics(BaseModel):
    chunks_processed: int = Field(0, description="Total chunks processed (video) or requests")
    chunks_per_second: float = Field(0.0, description="Chunks or requests per second")
    max_sustainable_streams: int = Field(0, description="Max sustainable streams")
    files_per_second: float = Field(0.0, description="Video files per second")
    throughput_texts_per_second: float = Field(0.0, description="Texts per second")


class GPUMetrics(BaseModel):
    inference_usage_mean: float = Field(0.0, description="Inference GPU usage mean %")
    inference_usage_p90: float = Field(0.0, description="Inference GPU usage p90 %")
    inference_memory_mean: float = Field(0.0, description="Inference GPU memory usage mean %")
    inference_nvdec_mean: float = Field(0.0, description="NVDEC utilization mean %, video only")


class EmbedTestCaseMetrics(BaseModel):
    latency: LatencyMetrics = Field(default_factory=LatencyMetrics)
    throughput: ThroughputMetrics = Field(default_factory=ThroughputMetrics)
    gpu: GPUMetrics = Field(default_factory=GPUMetrics)


class EmbedTestCase(BaseModel):
    test_case_id: str = Field(..., description="Test case id")
    metrics: EmbedTestCaseMetrics = Field(default_factory=EmbedTestCaseMetrics)


# -----------------------------------------------------------------------------
# Concurrency-style: concurrency_results + optimal_target_concurrency
# -----------------------------------------------------------------------------


class ConcurrencyResultItem(BaseModel):
    """One concurrency level result."""

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
# Helpers
# -----------------------------------------------------------------------------


def _load_json(path: str) -> Dict[str, Any]:
    try:
        with open(path, "r") as f:
            return json.load(f)
    except Exception:
        return {}


def _load_live_streams_summary_from_xlsx(xlsx_path: str) -> Dict[str, Dict[str, Any]]:
    """
    Load values from the Summary sheet of the xlsx written by live_streams_benchmark.analyze_results.
    Returns dict keyed by test_case_id with keys: max_sustainable_streams, decode_latency (and
    avg_latency when present). Used for max_live_streams mode.
    """
    if not xlsx_path or not os.path.isfile(xlsx_path):
        return {}
    try:
        summary_df = pd.read_excel(xlsx_path, sheet_name="Summary", engine="openpyxl")
    except Exception as e:
        logger.debug("Could not read Summary sheet from xlsx %s: %s", xlsx_path, e)
        return {}
    if (
        "test_case_id" not in summary_df.columns
        or "max_sustainable_streams" not in summary_df.columns
    ):
        logger.debug("Summary sheet missing test_case_id or max_sustainable_streams")
        return {}
    out: Dict[str, Dict[str, Any]] = {}
    for _, row in summary_df.iterrows():
        tc_id = row.get("test_case_id")
        if pd.isna(tc_id):
            continue
        tc_id = str(tc_id)
        out[tc_id] = {
            "max_sustainable_streams": int(row.get("max_sustainable_streams", 0) or 0),
            "decode_latency": _parse_mean_std_pct(row.get("decode_latency")),
            "avg_latency": _parse_mean_std_pct(row.get("avg_latency")),
            "inference_gpu_memory_mean": _parse_mean_std_pct(row.get("inference_gpu_memory_mean")),
            "inference_gpu_usage_mean": _parse_mean_std_pct(row.get("inference_gpu_usage_mean")),
            "inference_gpu_usage_p90": _parse_mean_std_pct(row.get("inference_gpu_usage_p90")),
            "inference_nvdec_usage_mean": _parse_mean_std_pct(
                row.get("inference_nvdec_usage_mean")
            ),
            "inference_nvdec_usage_p90": _parse_mean_std_pct(row.get("inference_nvdec_usage_p90")),
        }
    return out


def _load_concurrent_live_streams_summary_from_xlsx(
    xlsx_path: str,
) -> Dict[str, Dict[str, Any]]:
    """
    Load latency and GPU values from the Summary sheet of the xlsx written by
    concurrent_live_streams_benchmark.analyze_results.
    Returns dict keyed by test_case_id with keys: avg_latency, p90_latency,
    decode_latency_seconds_avg, and GPU fields (vlm_* or inference_* normalized to vlm_*).
    """
    if not xlsx_path or not os.path.isfile(xlsx_path):
        return {}
    try:
        summary_df = pd.read_excel(xlsx_path, sheet_name="Summary", engine="openpyxl")
    except Exception as e:
        logger.debug("Could not read Summary sheet from xlsx %s: %s", xlsx_path, e)
        return {}
    if "test_case_id" not in summary_df.columns or "avg_latency" not in summary_df.columns:
        logger.debug("Summary sheet missing test_case_id or avg_latency")
        return {}
    out: Dict[str, Dict[str, Any]] = {}
    for _, row in summary_df.iterrows():
        tc_id = row.get("test_case_id")
        if pd.isna(tc_id):
            continue
        tc_id = str(tc_id)
        gpu_mean = row.get("inference_gpu_usage_mean", row.get("vlm_gpu_usage_mean", 0))
        gpu_p90 = row.get("inference_gpu_usage_p90", row.get("vlm_gpu_usage_p90", 0))
        nvdec_mean = row.get("inference_nvdec_usage_mean", row.get("vlm_nvdec_usage_mean", 0))
        out[tc_id] = {
            "avg_latency": _parse_mean_std_pct(row.get("avg_latency")),
            "p90_latency": _parse_mean_std_pct(row.get("p90_latency")),
            "decode_latency_seconds_avg": _parse_mean_std_pct(
                row.get("decode_latency_seconds_avg")
            ),
            "vlm_gpu_usage_mean": _parse_mean_std_pct(gpu_mean),
            "vlm_gpu_usage_p90": _parse_mean_std_pct(gpu_p90),
            "vlm_nvdec_usage_mean": _parse_mean_std_pct(nvdec_mean),
        }
    return out


def _parse_mean_std_pct(value: Any) -> float:
    """Parse 'mean ± std%' string from Summary sheet to float mean; accept numeric as-is."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip()
    if not s:
        return 0.0
    # Format: "0.1234 ± 1.2%" or "10 ± 0.5%"
    if " ± " in s:
        s = s.split(" ± ")[0].strip()
    try:
        return float(s)
    except ValueError:
        return 0.0


def _load_text_embedding_summary_from_xlsx(xlsx_path: str) -> List[Dict[str, Any]]:
    """
    Load text embedding (single) results from the Summary sheet of the xlsx written by
    text_embedding_benchmark.analyze_results.
    Returns list of row dicts with test_case_id, e2e_latency, inference_latency,
    inference_gpu_usage_mean, inference_gpu_usage_p90 (numeric; mean ± std% parsed to mean).
    """
    if not xlsx_path or not os.path.isfile(xlsx_path):
        return []
    try:
        summary_df = pd.read_excel(xlsx_path, sheet_name="Summary", engine="openpyxl")
    except Exception as e:
        logger.debug("Could not read Summary sheet from xlsx %s: %s", xlsx_path, e)
        return []
    if "test_case_id" not in summary_df.columns:
        logger.debug("Summary sheet missing test_case_id")
        return []
    out: List[Dict[str, Any]] = []
    for _, row in summary_df.iterrows():
        tc_id = row.get("test_case_id")
        if pd.isna(tc_id):
            continue
        out.append(
            {
                "test_case_id": str(tc_id),
                "e2e_latency": _parse_mean_std_pct(row.get("e2e_latency")),
                "inference_latency": _parse_mean_std_pct(row.get("inference_latency")),
                "inference_gpu_usage_mean": _parse_mean_std_pct(
                    row.get("inference_gpu_usage_mean")
                ),
                "inference_gpu_usage_p90": _parse_mean_std_pct(row.get("inference_gpu_usage_p90")),
                "inference_gpu_memory_mean": _parse_mean_std_pct(
                    row.get("inference_gpu_memory_mean")
                ),
            }
        )
    return out


def _load_text_embedding_concurrency_summary_from_xlsx(
    xlsx_path: str,
) -> Dict[str, List[Dict[str, Any]]]:
    """
    Load text embedding concurrency results from the Summary sheet of the xlsx written by
    text_embedding_concurrency_benchmark.analyze_results.
    Returns dict keyed by test_case_id with list of row dicts per concurrency level:
    concurrency_level, e2e_latency_seconds, avg_latency, p90/p95/p99_latency,
    throughput_texts_per_second, completed_texts, and vlm_gpu_usage_* (from inference_* in xlsx).
    """
    if not xlsx_path or not os.path.isfile(xlsx_path):
        return {}
    try:
        summary_df = pd.read_excel(xlsx_path, sheet_name="Summary", engine="openpyxl")
    except Exception as e:
        logger.debug("Could not read Summary sheet from xlsx %s: %s", xlsx_path, e)
        return {}
    if "test_case_id" not in summary_df.columns or "concurrency_level" not in summary_df.columns:
        logger.debug("Summary sheet missing test_case_id or concurrency_level")
        return {}
    out: Dict[str, List[Dict[str, Any]]] = {}
    for _, row in summary_df.iterrows():
        tc_id = row.get("test_case_id")
        if pd.isna(tc_id):
            continue
        tc_id = str(tc_id)
        gpu_mean = row.get("inference_gpu_usage_mean", row.get("vlm_gpu_usage_mean", 0))
        gpu_p90 = row.get("inference_gpu_usage_p90", row.get("vlm_gpu_usage_p90", 0))
        gpu_memory_mean = row.get("inference_gpu_memory_mean", row.get("vlm_gpu_memory_mean", 0))

        row_dict: Dict[str, Any] = {
            "concurrency_level": int(row.get("concurrency_level", 0) or 0),
            "e2e_latency_seconds": _parse_mean_std_pct(row.get("e2e_latency_seconds")),
            "avg_latency": _parse_mean_std_pct(row.get("avg_latency")),
            "p90_latency": _parse_mean_std_pct(row.get("p90_latency")),
            "p95_latency": _parse_mean_std_pct(row.get("p95_latency")),
            "p99_latency": _parse_mean_std_pct(row.get("p99_latency")),
            "throughput_texts_per_second": _parse_mean_std_pct(
                row.get("throughput_texts_per_second")
            ),
            "completed_texts": int(row.get("completed_texts", 0) or 0),
            "vlm_gpu_usage_mean": _parse_mean_std_pct(gpu_mean),
            "vlm_gpu_memory_mean": _parse_mean_std_pct(gpu_memory_mean),
            "vlm_gpu_usage_p90": _parse_mean_std_pct(gpu_p90),
        }
        if tc_id not in out:
            out[tc_id] = []
        out[tc_id].append(row_dict)
    for tc_id in out:
        out[tc_id].sort(key=lambda r: r.get("concurrency_level", 0))
    return out


def _load_concurrency_summary_from_xlsx(xlsx_path: str) -> Dict[str, List[Dict[str, Any]]]:
    """
    Load concurrency results from the Summary sheet of the xlsx written by
    concurrency_benchmark.analyze_results.
    Returns dict keyed by base test_case_id (strip _c{level} suffix) with list of row dicts
    per concurrency level: concurrency_level, e2e_latency_seconds, avg_latency, p90_latency,
    throughput_files_per_second, completed_files (derived), etc.
    """
    if not xlsx_path or not os.path.isfile(xlsx_path):
        return {}
    try:
        summary_df = pd.read_excel(xlsx_path, sheet_name="Summary", engine="openpyxl")
    except Exception as e:
        logger.debug("Could not read Summary sheet from xlsx %s: %s", xlsx_path, e)
        return {}
    if "test_case_id" not in summary_df.columns or "concurrency_level" not in summary_df.columns:
        logger.debug("Summary sheet missing test_case_id or concurrency_level")
        return {}
    out: Dict[str, List[Dict[str, Any]]] = {}
    for _, row in summary_df.iterrows():
        tc_id = row.get("test_case_id")
        if pd.isna(tc_id):
            continue
        tc_id = str(tc_id)
        # Base test_case_id: strip _c{level} suffix (e.g. concurrency_video_5sec_c2 -> concurrency_video_5sec)
        base_id = tc_id.rsplit("_c", 1)[0] if "_c" in tc_id else tc_id
        if base_id not in out:
            out[base_id] = []
        throughput = _parse_mean_std_pct(row.get("throughput_files_per_second"))
        e2e = _parse_mean_std_pct(row.get("e2e_latency_seconds"))
        completed = round(throughput * e2e) if (e2e > 0 and throughput > 0) else 0

        row_dict: Dict[str, Any] = {
            "concurrency_level": int(row.get("concurrency_level", 0) or 0),
            "e2e_latency_seconds": e2e,
            "completed_files": completed,
            "failed_files": 0,
            "throughput_files_per_second": throughput,
            "avg_latency": _parse_mean_std_pct(row.get("avg_latency")),
            "p50_latency": _parse_mean_std_pct(row.get("p50_latency")),
            "p75_latency": _parse_mean_std_pct(row.get("p75_latency")),
            "p90_latency": _parse_mean_std_pct(row.get("p90_latency")),
            "p95_latency": _parse_mean_std_pct(row.get("p95_latency")),
            "p99_latency": _parse_mean_std_pct(row.get("p99_latency")),
        }
        # GPU columns from concurrency_benchmark Summary (inference_* or vlm_*)
        for key in (
            "inference_gpu_usage_mean",
            "inference_gpu_usage_p90",
            "inference_gpu_memory_mean",
            "inference_nvdec_usage_mean",
            "inference_nvdec_usage_p90",
            "vlm_gpu_usage_mean",
            "vlm_gpu_usage_p90",
            "vlm_gpu_memory_mean",
            "vlm_nvdec_usage_mean",
            "vlm_nvdec_usage_p90",
        ):
            if key in summary_df.columns:
                row_dict[key] = _parse_mean_std_pct(row.get(key))
        out[base_id].append(row_dict)
    for base_id in out:
        out[base_id].sort(key=lambda r: r.get("concurrency_level", 0))
    return out


def _load_summary_from_xlsx(xlsx_path: str) -> Dict[str, Dict[str, float]]:
    """
    Load latency values from the Summary sheet of the xlsx written by single_file_benchmark.
    Returns dict keyed by test_case_id with keys: e2e_latency_p50, inference_pipeline_latency_p50,
    inference_latency_p50, decode_latency_p50 (numeric p50 values).
    """
    if not xlsx_path or not os.path.isfile(xlsx_path):
        return {}
    try:
        summary_df = pd.read_excel(xlsx_path, sheet_name="Summary", engine="openpyxl")
    except Exception as e:
        logger.debug("Could not read Summary sheet from xlsx %s: %s", xlsx_path, e)
        return {}
    required = {
        "test_case_id",
        "e2e_latency",
        "inference_pipeline_latency",
        "inference_latency",
        "decode_latency",
    }
    if not required.issubset(summary_df.columns):
        logger.debug("Summary sheet missing columns %s", required - set(summary_df.columns))
        return {}
    out = {}
    for _, row in summary_df.iterrows():
        tc_id = row.get("test_case_id")
        if pd.isna(tc_id):
            continue
        tc_id = str(tc_id)
        out[tc_id] = {
            "e2e_latency": _parse_mean_std_pct(row.get("e2e_latency")),
            "inference_pipeline_latency": _parse_mean_std_pct(
                row.get("inference_pipeline_latency")
            ),
            "inference_latency": _parse_mean_std_pct(row.get("inference_latency")),
            "decode_latency": _parse_mean_std_pct(row.get("decode_latency")),
        }
        # Optional GPU columns (single_file/single_live_stream Summary may include them)
        for col in (
            "inference_gpu_usage_mean",
            "inference_gpu_usage_p90",
            "inference_gpu_memory_mean",
            "inference_nvdec_usage_mean",
        ):
            if col in summary_df.columns:
                out[tc_id][col] = _parse_mean_std_pct(row.get(col))
        for col in (
            "vlm_gpu_usage_mean",
            "vlm_gpu_usage_p90",
            "vlm_gpu_memory_mean",
            "vlm_nvdec_usage_mean",
        ):
            if col in summary_df.columns:
                out[tc_id][col] = _parse_mean_std_pct(row.get(col))
    return out


def _gpu_metrics_from_dict(d: Dict[str, Any]) -> GPUMetrics:
    """Build GPUMetrics from raw dict (single_file or concurrency result)."""
    return GPUMetrics(
        inference_usage_mean=d.get("inference_gpu_usage_mean", d.get("vlm_gpu_usage_mean", 0)),
        inference_usage_p90=d.get("inference_gpu_usage_p90", d.get("vlm_gpu_usage_p90", 0)),
        inference_memory_mean=d.get("inference_gpu_memory_mean", d.get("vlm_gpu_memory_mean", 0)),
        inference_nvdec_mean=d.get("inference_nvdec_usage_mean", d.get("vlm_nvdec_usage_mean", 0)),
    )


def _get_concurrency_results_from_test_case(tc: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Get concurrency_results from test case (top-level or first successful iteration)."""
    # text_embedding_concurrency has concurrency_results at top level
    if "concurrency_results" in tc:
        return tc.get("concurrency_results", [])
    for it in tc.get("iteration_results", []):
        if it.get("success") and "concurrency_results" in it:
            return it.get("concurrency_results", [])
    return []


# -----------------------------------------------------------------------------
# Standalone: infer mode from xlsx filename and convert xlsx -> test_cases
# -----------------------------------------------------------------------------

# Suffixes that identify benchmark mode in xlsx filenames (longest first for matching).
_XLSX_SUFFIX_TO_MODE: List[tuple] = [
    ("_text_embedding_concurrency_report.xlsx", "text_embedding_concurrency"),
    ("_concurrent_live_streams_report.xlsx", "concurrent_live_streams"),
    ("_max_live_streams_report.xlsx", "max_live_streams"),
    ("_text_embedding_report.xlsx", "text_embedding"),
    ("_single_live_stream_report.xlsx", "single_live_stream"),
    ("_concurrency_report.xlsx", "concurrency"),
    ("_single_file_report.xlsx", "single_file"),
]


def infer_mode_from_xlsx_path(path: str) -> Optional[str]:
    """Infer benchmark mode from xlsx filename (e.g. .../scenario_concurrency_report.xlsx -> concurrency)."""
    base = os.path.basename(path)
    if not base.endswith(".xlsx"):
        return None
    for suffix, mode in _XLSX_SUFFIX_TO_MODE:
        if base.endswith(suffix):
            return mode
    return None


def standalone_xlsx_to_test_cases(xlsx_path: str, mode: str) -> List[Dict[str, Any]]:
    """
    Read a single xlsx report and return a list of VSS test case dicts (test_case_id + metrics).

    Used for standalone execution: no benchmark or execution_results, only xlsx files from
    a reports folder (e.g. rtvi-embed-perf-report). GPU metrics are taken from Summary when
    present; otherwise left at 0.
    """
    out: List[Dict[str, Any]] = []

    if mode == "single_file":
        summary_by_tc = _load_summary_from_xlsx(xlsx_path)
        for test_case_id, row in summary_by_tc.items():
            metrics = EmbedTestCaseMetrics(
                latency=LatencyMetrics(
                    e2e_seconds=row.get("e2e_latency", 0.0),
                    inference_pipeline_seconds=row.get("inference_pipeline_latency", 0.0),
                    inference_seconds=row.get("inference_latency", 0.0),
                    decode_seconds=row.get("decode_latency", 0.0),
                ),
                gpu=_gpu_metrics_from_dict(row),
            )
            out.append(
                EmbedTestCase(test_case_id=test_case_id, metrics=metrics).model_dump(mode="json")
            )
        return out

    if mode == "text_embedding":
        summary_rows = _load_text_embedding_summary_from_xlsx(xlsx_path)
        for row in summary_rows:
            test_case_id = row.get("test_case_id", "")
            if not test_case_id:
                continue
            e2e = row.get("e2e_latency", 0) or 0
            inference = row.get("inference_latency", 0) or 0
            metrics = EmbedTestCaseMetrics(
                latency=LatencyMetrics(
                    e2e_seconds=e2e,
                    inference_pipeline_seconds=inference,
                    inference_seconds=inference,
                    decode_seconds=0.0,
                ),
                throughput=ThroughputMetrics(
                    chunks_processed=1,
                    chunks_per_second=(1.0 / e2e) if e2e else 0.0,
                ),
                gpu=_gpu_metrics_from_dict(row),
            )
            out.append(
                EmbedTestCase(test_case_id=test_case_id, metrics=metrics).model_dump(mode="json")
            )
        return out

    if mode == "text_embedding_concurrency":
        summary_by_tc = _load_text_embedding_concurrency_summary_from_xlsx(xlsx_path)
        for tc_id, rows in summary_by_tc.items():
            for row in rows:
                concurrency_level = row.get("concurrency_level", 0)
                test_case_id = f"{tc_id}"
                avg_latency = row.get("avg_latency", 0.0)
                throughput_texts = row.get("throughput_texts_per_second", 0.0)
                completed_texts = row.get("completed_texts", 0)
                metrics = EmbedTestCaseMetrics(
                    latency=LatencyMetrics(e2e_seconds=avg_latency),
                    throughput=ThroughputMetrics(
                        chunks_processed=completed_texts,
                        throughput_texts_per_second=throughput_texts,
                    ),
                    gpu=_gpu_metrics_from_dict(row),
                )
                out.append(
                    EmbedTestCase(test_case_id=test_case_id, metrics=metrics).model_dump(
                        mode="json"
                    )
                )
        return out

    if mode == "concurrency":
        summary_by_base = _load_concurrency_summary_from_xlsx(xlsx_path)
        for base_id, rows in summary_by_base.items():
            for row in rows:
                concurrency_level = row.get("concurrency_level", 0)
                test_case_id = f"{base_id}_c{concurrency_level}"
                avg_latency = row.get("avg_latency", 0.0)
                throughput_fps = row.get("throughput_files_per_second", 0.0)
                completed_files = row.get("completed_files", 0)
                metrics = EmbedTestCaseMetrics(
                    latency=LatencyMetrics(e2e_seconds=avg_latency),
                    throughput=ThroughputMetrics(
                        chunks_processed=completed_files,
                        files_per_second=throughput_fps,
                    ),
                    gpu=_gpu_metrics_from_dict(row),
                )
                out.append(
                    EmbedTestCase(test_case_id=test_case_id, metrics=metrics).model_dump(
                        mode="json"
                    )
                )
        return out

    if mode == "max_live_streams":
        summary_by_tc = _load_live_streams_summary_from_xlsx(xlsx_path)
        for test_case_id, row in summary_by_tc.items():
            max_sustainable_streams = row.get("max_sustainable_streams", 0)
            decode_latency = row.get("decode_latency", 0.0)
            chunk_latency = row.get("avg_latency", 0.0)
            metrics = EmbedTestCaseMetrics(
                throughput=ThroughputMetrics(max_sustainable_streams=max_sustainable_streams),
                latency=LatencyMetrics(
                    chunk_latency_seconds=chunk_latency,
                    decode_seconds=decode_latency,
                ),
                gpu=_gpu_metrics_from_dict(row),
            )
            out.append(
                EmbedTestCase(test_case_id=test_case_id, metrics=metrics).model_dump(mode="json")
            )
        return out

    if mode == "concurrent_live_streams":
        summary_by_tc = _load_concurrent_live_streams_summary_from_xlsx(xlsx_path)
        for test_case_id, row in summary_by_tc.items():
            avg_latency = row.get("avg_latency", 0.0)
            decode_latency = row.get("decode_latency_seconds_avg", 0.0)
            gpu_metrics = _gpu_metrics_from_dict(row)
            metrics = EmbedTestCaseMetrics(
                latency=LatencyMetrics(
                    chunk_latency_seconds=avg_latency,
                    decode_seconds=decode_latency,
                ),
                gpu=gpu_metrics,
            )
            out.append(
                EmbedTestCase(test_case_id=test_case_id, metrics=metrics).model_dump(mode="json")
            )
        return out

    if mode == "single_live_stream":
        # Same Summary shape as single_file for single_live_stream_benchmark
        summary_by_tc = _load_summary_from_xlsx(xlsx_path)
        for test_case_id, row in summary_by_tc.items():
            metrics = EmbedTestCaseMetrics(
                latency=LatencyMetrics(
                    e2e_seconds=row.get("e2e_latency", 0.0),
                    inference_pipeline_seconds=row.get("inference_pipeline_latency", 0.0),
                    inference_seconds=row.get("inference_latency", 0.0),
                    decode_seconds=row.get("decode_latency", 0.0),
                ),
                gpu=_gpu_metrics_from_dict(row),
            )
            out.append(
                EmbedTestCase(test_case_id=test_case_id, metrics=metrics).model_dump(mode="json")
            )
        return out

    logger.warning("Unknown mode %r for xlsx %s, skipping", mode, xlsx_path)
    return out


def collect_standalone_test_cases(reports_dir: str) -> List[Dict[str, Any]]:
    """
    Scan a performance reports folder for *_report.xlsx files and return combined test_cases.

    Each xlsx is parsed according to its inferred mode (from filename); all test cases are
    merged into a single list. Order is by discovery of xlsx files, then by test_case_id
    within each file.
    """
    combined: List[Dict[str, Any]] = []
    if not os.path.isdir(reports_dir):
        logger.warning("Reports dir does not exist: %s", reports_dir)
        return combined
    xlsx_files: List[str] = []
    for name in os.listdir(reports_dir):
        if name.endswith("_report.xlsx"):
            xlsx_files.append(os.path.join(reports_dir, name))
    xlsx_files.sort()
    for xlsx_path in xlsx_files:
        mode = infer_mode_from_xlsx_path(xlsx_path)
        if not mode:
            logger.debug("Could not infer mode for %s, skipping", xlsx_path)
            continue
        cases = standalone_xlsx_to_test_cases(xlsx_path, mode)
        if cases:
            combined.extend(cases)
            logger.info("Loaded %d test cases from %s (mode=%s)", len(cases), xlsx_path, mode)
    return combined


# -----------------------------------------------------------------------------
# Adapter API
# -----------------------------------------------------------------------------


def rtvi_embed_execution_results_to_test_cases(
    benchmark: Any, execution_results: Dict[str, Any], *, validate: bool = True
) -> List[Union[Dict[str, Any], EmbedTestCase]]:
    """
    Convert RTVI Embed execution_results to standard VSS test_cases.

    Returns list of dicts (when validate=True) for vss_perf_common.build_and_save(..., test_cases=...).
    - single_file / single_live_stream: one dict per test case with test_case_id + metrics
      (latency, throughput, gpu).
    - text_embedding / text_embedding_concurrency: one dict per test case; text_embedding uses
      iteration e2e_latency/inference_latency.
    - concurrency (and file_burst if present): one dict per test case with concurrency_results
      and optimal_target_concurrency.
    """
    out: List[Union[Dict[str, Any], EmbedTestCase]] = []
    results_dir = execution_results.get("scenario_dir", "")
    mode = execution_results.get("benchmark_mode", "single_file")

    # text_embedding: prefer Summary sheet from xlsx (text_embedding_benchmark.analyze_results)
    if mode == "text_embedding":
        scenario_name = execution_results.get("scenario_name", "")
        output_base_dir = os.path.dirname(results_dir)
        xlsx_path = os.path.join(
            output_base_dir,
            f"{scenario_name}_text_embedding_report.xlsx",
        )
        summary_rows = _load_text_embedding_summary_from_xlsx(xlsx_path)

        if summary_rows:
            for row in summary_rows:
                test_case_id = row.get("test_case_id", "")
                if not test_case_id:
                    continue
                e2e = row.get("e2e_latency", 0) or 0
                inference = row.get("inference_latency", 0) or 0
                metrics = EmbedTestCaseMetrics(
                    latency=LatencyMetrics(
                        e2e_seconds=e2e,
                        inference_pipeline_seconds=inference,
                        inference_seconds=inference,
                        decode_seconds=0.0,
                    ),
                    throughput=ThroughputMetrics(
                        chunks_processed=1,
                        chunks_per_second=(1.0 / e2e) if e2e else 0.0,
                    ),
                    gpu=_gpu_metrics_from_dict(row),
                )
                out.append(EmbedTestCase(test_case_id=test_case_id, metrics=metrics))
        else:
            # Fallback: build from execution_results + gpu stats JSON
            for tc in execution_results.get("test_cases", []):
                if not tc.get("success"):
                    continue
                test_case_id = tc.get("test_case_id", "")
                iteration = next(
                    (i for i in tc.get("iteration_results", []) if i.get("success")),
                    None,
                )
                if not iteration:
                    continue
                e2e = iteration.get("e2e_latency", 0) or 0
                inference = iteration.get("inference_latency", 0) or 0
                metrics = EmbedTestCaseMetrics(
                    latency=LatencyMetrics(
                        e2e_seconds=e2e,
                        inference_pipeline_seconds=inference,
                        inference_seconds=inference,
                        decode_seconds=0.0,
                    ),
                    throughput=ThroughputMetrics(
                        chunks_processed=1,
                        chunks_per_second=(1.0 / e2e) if e2e else 0.0,
                    ),
                    gpu=GPUMetrics(),
                )
                idx = iteration.get("iteration", 1)
                iter_dir = os.path.join(results_dir, test_case_id, f"iteration_{idx}")
                gpu_file = os.path.join(iter_dir, f"gpu_metrics_iter_{idx}_stats.json")
                if hasattr(benchmark, "process_gpu_stats") and os.path.isfile(gpu_file):
                    gpu_dict = benchmark.process_gpu_stats(gpu_file)
                    metrics.gpu = _gpu_metrics_from_dict(gpu_dict)
                out.append(EmbedTestCase(test_case_id=test_case_id, metrics=metrics))
        if validate:
            return [m.model_dump(mode="json") for m in out]
        return out

    if mode == "text_embedding_concurrency":
        scenario_name = execution_results.get("scenario_name", "")
        benchmark_mode = execution_results.get("benchmark_mode", "text_embedding_concurrency")
        output_base_dir = os.path.dirname(results_dir)
        xlsx_path = os.path.join(
            output_base_dir,
            f"{scenario_name}_{benchmark_mode}_report.xlsx",
        )
        summary_by_tc = _load_text_embedding_concurrency_summary_from_xlsx(xlsx_path)

        for tc_id, rows in summary_by_tc.items():
            for row in rows:
                concurrency_level = row.get("concurrency_level", 0)
                test_case_id = f"{tc_id}"
                avg_latency = row.get("avg_latency", 0.0)
                throughput_texts = row.get("throughput_texts_per_second", 0.0)
                completed_texts = row.get("completed_texts", 0)
                metrics = EmbedTestCaseMetrics(
                    latency=LatencyMetrics(
                        e2e_seconds=avg_latency,
                    ),
                    throughput=ThroughputMetrics(
                        chunks_processed=completed_texts,
                        throughput_texts_per_second=throughput_texts,
                    ),
                    gpu=_gpu_metrics_from_dict(row),
                )
                out.append(EmbedTestCase(test_case_id=test_case_id, metrics=metrics))
        if validate:
            return [m.model_dump(mode="json") for m in out]
        return out

    if mode == "max_live_streams":
        scenario_name = execution_results.get("scenario_name", "")
        benchmark_mode = execution_results.get("benchmark_mode", "max_live_streams")
        xlsx_path = os.path.join(
            os.path.dirname(results_dir),
            f"{scenario_name}_{benchmark_mode}_report.xlsx",
        )
        summary_by_tc = _load_live_streams_summary_from_xlsx(xlsx_path)

        for tc in execution_results.get("test_cases", []):
            if not tc.get("success"):
                continue
            test_case_id = tc.get("test_case_id", "")
            row = summary_by_tc.get(test_case_id)
            if row is not None:
                max_sustainable_streams = row.get("max_sustainable_streams", 0)
                decode_latency = row.get("decode_latency", 0.0)
                chunk_latency = row.get("avg_latency", 0.0)
                gpu_metrics = _gpu_metrics_from_dict(row)
            else:
                max_sustainable_streams = tc.get("max_sustainable_streams", 0)
                decode_latency = tc.get("decode_latency_seconds_avg", 0)
                chunk_latency = tc.get("avg_latency", 0.0)
                gpu_metrics = _gpu_metrics_from_dict(tc)
            metrics = EmbedTestCaseMetrics(
                throughput=ThroughputMetrics(
                    max_sustainable_streams=max_sustainable_streams,
                ),
                latency=LatencyMetrics(
                    chunk_latency_seconds=chunk_latency,
                    decode_seconds=decode_latency,
                ),
                gpu=gpu_metrics,
            )
            out.append(EmbedTestCase(test_case_id=test_case_id, metrics=metrics))
        if validate:
            return [m.model_dump(mode="json") for m in out]
        return out

    if mode == "concurrent_live_streams":
        scenario_name = execution_results.get("scenario_name", "")
        benchmark_mode = execution_results.get("benchmark_mode", "concurrent_live_streams")
        xlsx_path = os.path.join(
            os.path.dirname(results_dir),
            f"{scenario_name}_{benchmark_mode}_report.xlsx",
        )
        summary_by_tc = _load_concurrent_live_streams_summary_from_xlsx(xlsx_path)

        for tc in execution_results.get("test_cases", []):
            if not tc.get("success"):
                continue
            test_case_id = tc.get("test_case_id", "")
            row = summary_by_tc.get(test_case_id)
            if row is not None:
                avg_latency = row.get("avg_latency", 0.0)
                decode_latency = row.get("decode_latency_seconds_avg", 0.0)
                gpu_metrics = _gpu_metrics_from_dict(row)
            else:
                avg_latency = tc.get("avg_latency", 0.0)
                decode_latency = tc.get("decode_latency_seconds_avg", 0.0)
                gpu_metrics = _gpu_metrics_from_dict(tc)
            metrics = EmbedTestCaseMetrics(
                latency=LatencyMetrics(
                    chunk_latency_seconds=avg_latency,
                    decode_seconds=decode_latency,
                ),
                gpu=gpu_metrics,
            )
            out.append(EmbedTestCase(test_case_id=test_case_id, metrics=metrics))
        if validate:
            return [m.model_dump(mode="json") for m in out]
        return out

    if mode == "concurrency":
        scenario_name = execution_results.get("scenario_name", "")
        benchmark_mode = execution_results.get("benchmark_mode", "concurrency")
        output_base_dir = os.path.dirname(results_dir)
        xlsx_path = os.path.join(
            output_base_dir,
            f"{scenario_name}_{benchmark_mode}_report.xlsx",
        )
        summary_by_base = _load_concurrency_summary_from_xlsx(xlsx_path)

        for base_id, rows in summary_by_base.items():
            for row in rows:
                concurrency_level = row.get("concurrency_level", 0)
                test_case_id = f"{base_id}_c{concurrency_level}"
                avg_latency = row.get("avg_latency", 0.0)
                throughput_fps = row.get("throughput_files_per_second", 0.0)
                completed_files = row.get("completed_files", 0)
                test_case_dir = os.path.join(results_dir, base_id)
                gpu_file = os.path.join(
                    test_case_dir, f"gpu_metrics_concurrency_{concurrency_level}_stats.json"
                )
                gpu_dict = (
                    benchmark.process_gpu_stats(gpu_file)
                    if hasattr(benchmark, "process_gpu_stats") and os.path.isfile(gpu_file)
                    else {}
                )
                metrics = EmbedTestCaseMetrics(
                    latency=LatencyMetrics(
                        e2e_seconds=avg_latency,
                    ),
                    throughput=ThroughputMetrics(
                        chunks_processed=completed_files,
                        files_per_second=throughput_fps,
                    ),
                    gpu=_gpu_metrics_from_dict(gpu_dict),
                )
                out.append(EmbedTestCase(test_case_id=test_case_id, metrics=metrics))
        if validate:
            return [m.model_dump(mode="json") for m in out]
        return out

    # single_file: load latency from xlsx Summary sheet (p50), fallback to iteration_results
    if mode == "single_file":
        xlsx_path = os.path.join(
            os.path.dirname(results_dir),
            f"{os.path.basename(results_dir)}_single_file_report.xlsx",
        )
        summary_by_tc = _load_summary_from_xlsx(xlsx_path)

        for tc in execution_results.get("test_cases", []):
            if not tc.get("success"):
                continue
            test_case_id = tc.get("test_case_id", "")
            successful_iterations = [i for i in tc.get("iteration_results", []) if i.get("success")]
            if not successful_iterations:
                continue

            row = summary_by_tc.get(test_case_id)
            wall = row.get("e2e_latency", 0.0)
            pipeline_latency = row.get("inference_pipeline_latency", 0.0)
            inference_latency = row.get("inference_latency", 0.0)
            decode_latency = row.get("decode_latency", 0.0)

            first_iteration = successful_iterations[0]
            idx = first_iteration.get("iteration", 1)
            iter_dir = os.path.join(results_dir, test_case_id, f"iteration_{idx}")
            gpu_file = os.path.join(iter_dir, f"gpu_metrics_iter_{idx}_stats.json")
            gpu_dict = (
                benchmark.process_gpu_stats(gpu_file)
                if hasattr(benchmark, "process_gpu_stats") and os.path.isfile(gpu_file)
                else {}
            )
            metrics = EmbedTestCaseMetrics(
                latency=LatencyMetrics(
                    e2e_seconds=wall,
                    inference_pipeline_seconds=pipeline_latency,
                    inference_seconds=inference_latency,
                    decode_seconds=decode_latency or 0.0,
                ),
                gpu=_gpu_metrics_from_dict(gpu_dict),
            )
            out.append(EmbedTestCase(test_case_id=test_case_id, metrics=metrics))

        if validate:
            return [m.model_dump(mode="json") for m in out]
        return out


# -----------------------------------------------------------------------------
# Standalone execution
# -----------------------------------------------------------------------------


def _main_standalone() -> int:
    """Entry point when run as script: read xlsx from reports dir, write combined JSON."""
    parser = argparse.ArgumentParser(
        description="Combine RTVI Embed performance report xlsx files into a single JSON (standalone mode)."
    )
    parser.add_argument(
        "--reports-dir",
        default="rtvi-embed-perf-report",
        help="Directory containing *_report.xlsx files (default: rtvi-embed-perf-report)",
    )
    parser.add_argument(
        "--output",
        "-o",
        default="combined_perf_report.json",
        help="Output JSON path; use '-' for stdout (default: combined_perf_report.json)",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable info logging",
    )
    parser.add_argument(
        "--upload",
        "-u",
        action="store_true",
        help="Upload result JSON to MinIO (uses vss_perf_common; requires MINIO_* env or config)",
    )
    parser.add_argument(
        "--service",
        default="RTVI-Embed",
        help="Service name for upload and VSS result metadata (default: RTVI-Embed)",
    )
    parser.add_argument(
        "--config-id",
        default=None,
        help="Override config_id for VSS result JSON (e.g. 'h100', 'rtx_pro', 'h100-ce1-448p', "
        "'rtx6000pro-ce1-448p'); passed to discover_platform() for optional gpu.topology (NxM token) "
        "and stored in result metadata (default: None)",
    )
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s: %(message)s",
    )
    reports_dir = os.path.abspath(args.reports_dir)
    test_cases = collect_standalone_test_cases(reports_dir)

    # Resolve GPU/platform info for JSON output (same approach as rtvi_perf_benchmark)
    platform: Dict[str, Any]
    if discover_platform is not None:
        try:
            platform = discover_platform(config_id=args.config_id)
        except Exception:
            platform = {"gpu": {"model": "Unknown", "count": 0}}
    else:
        platform = {"gpu": {"model": "Unknown", "count": 0}}

    if args.upload:
        if build_and_save is None or upload_result_file is None:
            print(
                "Upload requires vss_perf_common (run from perf/benchmark or set PYTHONPATH).",
                file=sys.stderr,
            )
            return 1
        if args.output == "-":
            print(
                "Error: --upload cannot be used with -o - (stdout). Specify a file path.",
                file=sys.stderr,
            )
            return 1
        output_file = args.output
        result_config = {"config_id": args.config_id} if args.config_id else None
        json_path = build_and_save(
            output_file,
            args.service,
            test_cases,
            config_id=args.config_id or "",
            platform=platform,
            config=result_config,
        )
        if upload_result_file(str(json_path), args.service):
            print(f"Uploaded to MinIO: {args.service}/{json_path.name}", file=sys.stderr)
        else:
            print("Upload to MinIO failed.", file=sys.stderr)
            return 1
        print(f"Wrote {len(test_cases)} test cases to {json_path}", file=sys.stderr)
        return 0

    payload = {"test_cases": test_cases, "source_reports_dir": reports_dir, "platform": platform}
    out_json = json.dumps(payload, indent=2)
    if args.output == "-":
        print(out_json)
    else:
        with open(args.output, "w") as f:
            f.write(out_json)
        print(f"Wrote {len(test_cases)} test cases to {args.output}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(_main_standalone())
