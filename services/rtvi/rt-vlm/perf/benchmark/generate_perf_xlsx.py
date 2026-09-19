#!/usr/bin/env python3
######################################################################################################
# SPDX-FileCopyrightText: Copyright (c) 2024-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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
Generate EngSQA-format XLSX spreadsheet from RTVI-VLM perf benchmark JSON reports.

Reads JSON results from one or more platform report directories and produces an XLSX
workbook with per-platform tabs (Concurrent Streams, Max Streams, File Processing / Throughput)
plus a cross-platform Summary tab.

Usage:
    # Single platform
    python generate_perf_xlsx.py \\
        --reports H100=~/VSS/perf-report/rtvi-vlm-perf-report-h100-03-10 \\
        --output perf_report.xlsx

    # Multiple platforms
    python generate_perf_xlsx.py \\
        --reports H100=~/VSS/perf-report/rtvi-vlm-perf-report-h100-03-10 \\
                  "RTX Pro"=~/VSS/perf-report/rtvi-vlm-perf-report-rtx-pro-03-11 \\
                  Thor=~/VSS/perf-report/rtvi-vlm-perf-report-thor \\
                  Spark=~/VSS/perf-report/rtvi-vlm-perf-report-spark-03-09 \\
        --output perf_report.xlsx

    # Override defaults
    python generate_perf_xlsx.py \\
        --reports H100=~/path \\
        --output report.xlsx \\
        --release "3.2" \\
        --model "CR2-8B" \\
        --precision "FP8" \\
        --engine "vLLM"
"""

import argparse
import math
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml
from openpyxl import Workbook
from openpyxl.drawing.image import Image as XlImage
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from perf_platform import get_gpu_metric
from perf_utils import load_json

# ── Styling constants ────────────────────────────────────────────────────────
HEADER_FONT = Font(bold=True, size=10)
SECTION_FONT = Font(bold=True, size=11, color="FFFFFF")
SECTION_FILL_1 = PatternFill(start_color="2E4057", end_color="2E4057", fill_type="solid")
SECTION_FILL_2 = PatternFill(start_color="3D5A80", end_color="3D5A80", fill_type="solid")
SECTION_FILL_3 = PatternFill(start_color="4A7C59", end_color="4A7C59", fill_type="solid")
SUMMARY_HEADER_FILL = PatternFill(start_color="1B4332", end_color="1B4332", fill_type="solid")
SUMMARY_HEADER_FONT = Font(bold=True, size=11, color="FFFFFF")
THIN_BORDER = Border(
    left=Side(style="thin"),
    right=Side(style="thin"),
    top=Side(style="thin"),
    bottom=Side(style="thin"),
)
NUMBER_FORMAT_2DP = "0.00"
NUMBER_FORMAT_1DP = "0.0"
NUMBER_FORMAT_INT = "0"


# ── Resolution/token parsing helpers ─────────────────────────────────────────
def parse_resolution_from_name(name: str) -> Tuple[int, int]:
    """Parse image resolution from scenario/test case name. Returns (width, height)."""
    name_lower = name.lower()
    if "448" in name_lower:
        return (448, 448)
    if "224" in name_lower:
        return (224, 224)
    # Default resolution
    return (384, 384)


def parse_osl_from_name(name: str) -> int:
    """Parse output sequence length (OSL) from scenario name."""
    name_lower = name.lower()
    if "100_token" in name_lower or "100t" in name_lower:
        return 100
    if "1_token" in name_lower or "single_token" in name_lower or "1t" in name_lower:
        return 1
    return 1


def parse_video_duration_from_name(name: str) -> str:
    """Parse video duration string from test case name."""
    name_lower = name.lower()
    if "60min" in name_lower or "60m" in name_lower:
        return "60 min"
    if "10min" in name_lower or "10m" in name_lower:
        return "10 min"
    if "10s" in name_lower:
        return "10 sec"
    if "30s" in name_lower:
        return "30 sec"
    if "5min" in name_lower or "5m" in name_lower:
        return "5 min"
    return "unknown"


def parse_stream_count_from_name(name: str) -> Optional[int]:
    """Parse stream count from test case name (e.g. '10streams' -> 10)."""
    match = re.search(r"(\d+)streams", name.lower())
    if match:
        return int(match.group(1))
    return None


def calc_vision_tokens(w: int, h: int, num_frames: int) -> int:
    """Calculate vision tokens using the VLM tiling formula.

    Formula: ceil(w/32) * ceil(h/32) * (num_frames // 2)
    """
    return math.ceil(w / 32) * math.ceil(h / 32) * (num_frames // 2)


def vision_tokens_label(tokens: int) -> str:
    """Return a human-friendly label like '~2K' or '~8K'."""
    return f"~{round(tokens / 1000)}K"


def get_frames_per_chunk(res_w: int) -> int:
    """Get default frames per chunk based on resolution (fallback when no config)."""
    if res_w >= 448:
        return 80
    return 30


def get_vision_tokens(res_w: int, res_h: int = 0, num_frames: int = 0) -> int:
    """Get vision tokens — uses formula if all params given, else fallback."""
    if res_h > 0 and num_frames > 0:
        return calc_vision_tokens(res_w, res_h, num_frames)
    # Fallback for backward compat
    if res_w >= 448:
        return calc_vision_tokens(res_w, res_w, 80)
    return calc_vision_tokens(res_w if res_w > 0 else 372, res_w if res_w > 0 else 372, 30)


# ── Config parsing ───────────────────────────────────────────────────────────
def load_yaml_config(config_path: Path) -> Optional[Dict]:
    """Load a YAML config file."""
    try:
        with open(config_path) as f:
            return yaml.safe_load(f)
    except Exception as e:
        print(f"  Warning: Failed to load config {config_path}: {e}")
        return None


def get_scenario_params(config: Dict, scenario_name: str) -> Dict[str, Any]:
    """Extract merged generate_captions_params for a scenario.

    Merge order: global.generate_captions_params -> scenario.generate_captions_params
                 -> video[0].generate_captions_params
    """
    params = {}

    # Global level
    global_cfg = config.get("global", {})
    global_gcp = global_cfg.get("generate_captions_params", {})
    params.update(global_gcp)

    # Scenario level
    scenarios = config.get("test_scenarios", {})
    scenario = scenarios.get(scenario_name, {})
    scenario_gcp = scenario.get("generate_captions_params", {})
    params.update(scenario_gcp)

    # Video level (first video)
    videos = scenario.get("videos", [])
    if videos:
        video_gcp = videos[0].get("generate_captions_params", {})
        params.update(video_gcp)

    return {
        "vlm_input_width": params.get("vlm_input_width", 448),
        "vlm_input_height": params.get("vlm_input_height", 448),
        "num_frames": params.get("num_frames_per_second_or_fixed_frames_chunk", 80),
        "max_tokens": params.get("max_tokens", 100),
    }


def resolve_scenario_vision_info(
    config: Optional[Dict], scenario_name: str
) -> Tuple[int, int, int, int]:
    """Return (width, height, num_frames, vision_tokens) for a scenario.

    If config is available, uses merged params. Otherwise falls back to name parsing.
    """
    if config:
        p = get_scenario_params(config, scenario_name)
        w, h, nf = p["vlm_input_width"], p["vlm_input_height"], p["num_frames"]
        return w, h, nf, calc_vision_tokens(w, h, nf)

    # Fallback: parse from name
    w, h = parse_resolution_from_name(scenario_name)
    nf = get_frames_per_chunk(w)
    return w, h, nf, get_vision_tokens(w, h, nf)


def find_scenario_dirs(report_dir: Path, prefix: str) -> List[Path]:
    """Find scenario directories matching a prefix."""
    if not report_dir.exists():
        return []
    return sorted([d for d in report_dir.iterdir() if d.is_dir() and d.name.startswith(prefix)])


def find_test_case_dirs(scenario_dir: Path) -> List[Path]:
    """Find all test case subdirectories within a scenario directory."""
    if not scenario_dir.exists():
        return []
    return sorted([d for d in scenario_dir.iterdir() if d.is_dir()])


# ── Section 1: Concurrent Streams & Latency ──────────────────────────────────
PIPELINE_STAGE_COLUMN_SPECS = [
    ("chunk_decode_latency_seconds", "Decode"),
    ("chunk_queue_latency_seconds", "Queue"),
    ("chunk_vlm_latency_seconds", "VLM"),
    ("chunk_server_processing_latency_seconds", "Server Processing"),
    ("chunk_server_e2e_latency_seconds", "Server E2E"),
]

PIPELINE_STAGE_STAT_SPECS = [
    ("min", "Min"),
    ("avg", "Avg"),
    ("max", "Max"),
    ("p50", "p50"),
    ("p75", "p75"),
    ("p90", "p90"),
    ("p95", "p95"),
    ("p99", "p99"),
]

PIPELINE_STAGE_HEADERS = [
    f"{stage_label} Stage Latency {stat_label} (s)"
    for _, stage_label in PIPELINE_STAGE_COLUMN_SPECS
    for _, stat_label in PIPELINE_STAGE_STAT_SPECS
]

CONCURRENCY_HEADERS = [
    "Release",
    "Platform",
    "Model",
    "Model Precision",
    "Engine Backend",
    "Chunk Duration (sec)",
    "# Frames / Chunk",
    "Image Resolution_w",
    "Image Resolution_h",
    "Vision Tokens",
    "ISL - Text",
    "OSL",
    "Concurrent Streams",
    "Chunk E2E Latency Min (s)",
    "Chunk E2E Latency Avg (s)",
    "Chunk E2E Latency Max (s)",
    "Chunk E2E Latency p50 (s)",
    "Chunk E2E Latency p75 (s)",
    "Chunk E2E Latency p90 (s)",
    "Chunk E2E Latency p95 (s)",
    "Chunk E2E Latency p99 (s)",
    *PIPELINE_STAGE_HEADERS,
    "Decode latency Avg (s)",
    "Streams With Errors",
    "Error Rate (%)",
    "GPU Core Utilization Avg (%)",
    "GPU Memory Utilization Avg (%)",
    "GPU NVdec Utilization - Avg (%)",
    "GPU NVdec Utilization - P90 (%)",
    "GPU Temperature Avg (C)",
    "GPU Temperature P90 (C)",
    "GPU Power Avg (W)",
    "GPU Power P90 (W)",
    "CPU Core Utilization Avg (%)",
    "System Memory Utilization Avg (%)",
]


def extract_concurrency_data(
    report_dir: Path, platform: str, defaults: Dict[str, Any], config: Optional[Dict] = None
) -> List[Dict[str, Any]]:
    """Extract concurrency test data from report directory."""
    rows = []

    for token_prefix in ["concurrency_test_1_token", "concurrency_test_100_token"]:
        osl = 1 if "1_token" in token_prefix else 100

        for scenario_dir in find_scenario_dirs(report_dir, token_prefix):
            res_w, res_h, num_frames, vtokens = resolve_scenario_vision_info(
                config, scenario_dir.name
            )

            for tc_dir in find_test_case_dirs(scenario_dir):
                # Try test_case_summary.json first
                summary = load_json(tc_dir / "test_case_summary.json")
                if summary and "iteration_results" in summary:
                    stream_count = summary.get(
                        "stream_count", parse_stream_count_from_name(tc_dir.name)
                    )
                    if stream_count is None:
                        continue

                    iters = [r for r in summary["iteration_results"] if r.get("success", False)]
                    if not iters:
                        continue

                    avg_lat = _mean([non_negative_latency(r.get("avg_latency", 0)) for r in iters])
                    p90_lat = _mean([non_negative_latency(r.get("p90_latency", 0)) for r in iters])
                    p95_lat = _mean([non_negative_latency(r.get("p95_latency", 0)) for r in iters])
                    p99_lat = _mean([non_negative_latency(r.get("p99_latency", 0)) for r in iters])
                    p50_lat = _mean([non_negative_latency(r.get("p50_latency", 0)) for r in iters])
                    p75_lat = _mean([non_negative_latency(r.get("p75_latency", 0)) for r in iters])
                    min_lat = _mean([non_negative_latency(r.get("min_latency", 0)) for r in iters])
                    max_lat = _mean([non_negative_latency(r.get("max_latency", 0)) for r in iters])
                    decode_lat = _mean(
                        [
                            non_negative_latency(r.get("decode_latency_seconds_avg", 0))
                            for r in iters
                        ]
                    )
                    streams_with_errors = _mean([r.get("streams_with_errors", 0) for r in iters])
                    error_rate = _failure_rate(
                        stream_count - streams_with_errors, streams_with_errors
                    )
                    gpu_usage = _get_iter_gpu_metric(iters, "gpu_usage_mean", platform)
                    gpu_mem = _get_iter_gpu_metric(iters, "gpu_memory_mean", platform)
                    nvdec_mean = _get_iter_gpu_metric(iters, "nvdec_usage_mean", platform)
                    nvdec_p90 = _get_iter_gpu_metric(iters, "nvdec_usage_p90", platform)
                    gpu_temp_mean = _mean(
                        [r.get("prometheus_vlm_gpu_temp_mean_c", 0) for r in iters]
                    )
                    gpu_temp_p90 = _mean([r.get("prometheus_vlm_gpu_temp_p90_c", 0) for r in iters])
                    gpu_power_mean = _mean(
                        [r.get("prometheus_vlm_power_mean_watts", 0) for r in iters]
                    )
                    gpu_power_p90 = _mean(
                        [r.get("prometheus_vlm_power_p90_watts", 0) for r in iters]
                    )
                    cpu_util = _mean([r.get("nodeexporter_cpu_util_mean", 0) for r in iters])
                    mem_util = _mean([r.get("nodeexporter_memory_used_pct_mean", 0) for r in iters])

                    rows.append(
                        {
                            "Release": defaults["release"],
                            "Platform": platform,
                            "Model": defaults["model"],
                            "Model Precision": defaults["precision"],
                            "Engine Backend": defaults["engine"],
                            "Chunk Duration (sec)": summary.get("chunk_size", 10) or "Full video",
                            "# Frames / Chunk": num_frames,
                            "Image Resolution_w": res_w,
                            "Image Resolution_h": res_h,
                            "Vision Tokens": vtokens,
                            "ISL - Text": defaults["isl_text"],
                            "OSL": osl,
                            "Concurrent Streams": stream_count,
                            "Chunk E2E Latency Min (s)": round(min_lat, 2),
                            "Chunk E2E Latency Avg (s)": round(avg_lat, 2),
                            "Chunk E2E Latency Max (s)": round(max_lat, 2),
                            "Chunk E2E Latency p50 (s)": round(p50_lat, 2),
                            "Chunk E2E Latency p75 (s)": round(p75_lat, 2),
                            "Chunk E2E Latency p90 (s)": round(p90_lat, 2),
                            "Chunk E2E Latency p95 (s)": round(p95_lat, 2),
                            "Chunk E2E Latency p99 (s)": round(p99_lat, 2),
                            **_stage_latency_columns(iters, summary),
                            "Decode latency Avg (s)": round(decode_lat, 2),
                            "Streams With Errors": round(streams_with_errors, 2),
                            "Error Rate (%)": round(error_rate, 2),
                            "GPU Core Utilization Avg (%)": round(gpu_usage, 1),
                            "GPU Memory Utilization Avg (%)": round(gpu_mem, 1),
                            "GPU NVdec Utilization - Avg (%)": round(nvdec_mean, 1),
                            "GPU NVdec Utilization - P90 (%)": round(nvdec_p90, 1),
                            "GPU Temperature Avg (C)": round(gpu_temp_mean, 1),
                            "GPU Temperature P90 (C)": round(gpu_temp_p90, 1),
                            "GPU Power Avg (W)": round(gpu_power_mean, 1),
                            "GPU Power P90 (W)": round(gpu_power_p90, 1),
                            "CPU Core Utilization Avg (%)": round(cpu_util, 1),
                            "System Memory Utilization Avg (%)": round(mem_util, 1),
                        }
                    )
                    continue

                # Fallback: read concurrent_live_streams_results.json directly
                clr = load_json(tc_dir / "concurrent_live_streams_results.json")
                if clr and clr.get("success", False):
                    stream_count = clr.get(
                        "stream_count", parse_stream_count_from_name(tc_dir.name)
                    )
                    if stream_count is None:
                        continue
                    rows.append(
                        {
                            "Release": defaults["release"],
                            "Platform": platform,
                            "Model": defaults["model"],
                            "Model Precision": defaults["precision"],
                            "Engine Backend": defaults["engine"],
                            "Chunk Duration (sec)": clr.get("chunk_size", 10) or "Full video",
                            "# Frames / Chunk": num_frames,
                            "Image Resolution_w": res_w,
                            "Image Resolution_h": res_h,
                            "Vision Tokens": vtokens,
                            "ISL - Text": defaults["isl_text"],
                            "OSL": osl,
                            "Concurrent Streams": stream_count,
                            "Chunk E2E Latency Min (s)": round(
                                non_negative_latency(clr.get("min_latency", 0)), 2
                            ),
                            "Chunk E2E Latency Avg (s)": round(
                                non_negative_latency(clr.get("avg_latency", 0)), 2
                            ),
                            "Chunk E2E Latency Max (s)": round(
                                non_negative_latency(clr.get("max_latency", 0)), 2
                            ),
                            "Chunk E2E Latency p50 (s)": round(
                                non_negative_latency(clr.get("p50_latency", 0)), 2
                            ),
                            "Chunk E2E Latency p75 (s)": round(
                                non_negative_latency(clr.get("p75_latency", 0)), 2
                            ),
                            "Chunk E2E Latency p90 (s)": round(
                                non_negative_latency(clr.get("p90_latency", 0)), 2
                            ),
                            "Chunk E2E Latency p95 (s)": round(
                                non_negative_latency(clr.get("p95_latency", 0)), 2
                            ),
                            "Chunk E2E Latency p99 (s)": round(
                                non_negative_latency(clr.get("p99_latency", 0)), 2
                            ),
                            **_stage_latency_columns([clr], clr),
                            "Decode latency Avg (s)": round(
                                non_negative_latency(clr.get("decode_latency_seconds_avg", 0)), 2
                            ),
                            "Streams With Errors": clr.get("streams_with_errors", 0),
                            "Error Rate (%)": round(
                                _failure_rate(
                                    stream_count - clr.get("streams_with_errors", 0),
                                    clr.get("streams_with_errors", 0),
                                ),
                                2,
                            ),
                            "GPU Core Utilization Avg (%)": round(
                                get_gpu_metric(clr, "gpu_usage_mean", platform), 1
                            ),
                            "GPU Memory Utilization Avg (%)": round(
                                get_gpu_metric(clr, "gpu_memory_mean", platform), 1
                            ),
                            "GPU NVdec Utilization - Avg (%)": round(
                                get_gpu_metric(clr, "nvdec_usage_mean", platform), 1
                            ),
                            "GPU NVdec Utilization - P90 (%)": round(
                                get_gpu_metric(clr, "nvdec_usage_p90", platform), 1
                            ),
                            "GPU Temperature Avg (C)": round(
                                clr.get("prometheus_vlm_gpu_temp_mean_c", 0), 1
                            ),
                            "GPU Temperature P90 (C)": round(
                                clr.get("prometheus_vlm_gpu_temp_p90_c", 0), 1
                            ),
                            "GPU Power Avg (W)": round(
                                clr.get("prometheus_vlm_power_mean_watts", 0), 1
                            ),
                            "GPU Power P90 (W)": round(
                                clr.get("prometheus_vlm_power_p90_watts", 0), 1
                            ),
                            "CPU Core Utilization Avg (%)": round(
                                clr.get("nodeexporter_cpu_util_mean", 0), 1
                            ),
                            "System Memory Utilization Avg (%)": round(
                                clr.get("nodeexporter_memory_used_pct_mean", 0), 1
                            ),
                        }
                    )

    # Sort by OSL, then stream count
    rows.sort(key=lambda r: (r["OSL"], r["Concurrent Streams"]))
    return rows


# ── Section 2: Max Concurrent Streams & Latency ─────────────────────────────
MAX_STREAMS_HEADERS = [
    "Release",
    "Platform",
    "Model",
    "Model Precision",
    "Engine Backend",
    "Chunk Duration (sec)",
    "# Frames / Chunk",
    "Image Resolution_w",
    "Image Resolution_h",
    "Vision Tokens",
    "ISL - Text",
    "OSL",
    "Max Concurrent Streams",
    "Chunk E2E Latency Min (s)",
    "Chunk E2E Latency Avg (s)",
    "Chunk E2E Latency Max (s)",
    "Chunk E2E Latency p50 (s)",
    "Chunk E2E Latency p75 (s)",
    "Chunk E2E Latency p90 (s)",
    "Chunk E2E Latency p95 (s)",
    "Chunk E2E Latency p99 (s)",
    *PIPELINE_STAGE_HEADERS,
    "Decode latency Avg (s)",
    "Dropped Chunks",
    "GPU Core Utilization - Avg (%)",
    "GPU Core Utilization - P90 (%)",
    "GPU Memory Utilization - Avg (%)",
    "GPU NVdec Utilization - Avg (%)",
    "GPU NVdec Utilization - P90 (%)",
    "GPU Temperature Avg (C)",
    "GPU Temperature P90 (C)",
    "GPU Power Avg (W)",
    "GPU Power P90 (W)",
    "CPU Core Utilization - Avg (%)",
    "System Memory Utilization - Avg (%)",
]


def extract_max_streams_data(
    report_dir: Path, platform: str, defaults: Dict[str, Any], config: Optional[Dict] = None
) -> List[Dict[str, Any]]:
    """Extract max_live_streams test data from report directory."""
    rows = []

    for scenario_dir in find_scenario_dirs(report_dir, "max_live_streams_test"):
        scenario_name = scenario_dir.name
        res_w, res_h, num_frames, vtokens = resolve_scenario_vision_info(config, scenario_name)
        osl = parse_osl_from_name(scenario_name)

        for tc_dir in find_test_case_dirs(scenario_dir):
            data = load_json(tc_dir / "max_live_streams_results.json")
            if not data or not data.get("success", False):
                continue

            rows.append(
                {
                    "Release": defaults["release"],
                    "Platform": platform,
                    "Model": defaults["model"],
                    "Model Precision": defaults["precision"],
                    "Engine Backend": defaults["engine"],
                    "Chunk Duration (sec)": data.get("chunk_size", 10),
                    "# Frames / Chunk": num_frames,
                    "Image Resolution_w": res_w,
                    "Image Resolution_h": res_h,
                    "Vision Tokens": vtokens,
                    "ISL - Text": defaults["isl_text"],
                    "OSL": osl,
                    "Max Concurrent Streams": data.get("max_sustainable_streams", 0),
                    "Chunk E2E Latency Min (s)": round(
                        non_negative_latency(data.get("min_latency", 0)), 2
                    ),
                    "Chunk E2E Latency Avg (s)": round(
                        non_negative_latency(data.get("last_stable_moving_average_latency", 0)), 2
                    ),
                    "Chunk E2E Latency Max (s)": round(
                        non_negative_latency(data.get("last_stable_max_latency", 0)), 2
                    ),
                    "Chunk E2E Latency p50 (s)": round(
                        non_negative_latency(data.get("last_stable_p50", 0)), 2
                    ),
                    "Chunk E2E Latency p75 (s)": round(
                        non_negative_latency(data.get("last_stable_p75", 0)), 2
                    ),
                    "Chunk E2E Latency p90 (s)": round(
                        non_negative_latency(data.get("last_stable_p90", 0)), 2
                    ),
                    "Chunk E2E Latency p95 (s)": round(
                        non_negative_latency(data.get("last_stable_p95", 0)), 2
                    ),
                    "Chunk E2E Latency p99 (s)": round(
                        non_negative_latency(data.get("last_stable_p99", 0)), 2
                    ),
                    **_stage_latency_columns([data], data),
                    "Decode latency Avg (s)": round(data.get("decode_latency_seconds_avg", 0), 2),
                    "Dropped Chunks": data.get("total_dropped_chunks", 0),
                    "GPU Core Utilization - Avg (%)": round(
                        get_gpu_metric(data, "gpu_usage_mean", platform), 1
                    ),
                    "GPU Core Utilization - P90 (%)": round(
                        get_gpu_metric(data, "gpu_usage_p90", platform), 1
                    ),
                    "GPU Memory Utilization - Avg (%)": round(
                        get_gpu_metric(data, "gpu_memory_mean", platform), 1
                    ),
                    "GPU NVdec Utilization - Avg (%)": round(
                        get_gpu_metric(data, "nvdec_usage_mean", platform), 1
                    ),
                    "GPU NVdec Utilization - P90 (%)": round(
                        get_gpu_metric(data, "nvdec_usage_p90", platform), 1
                    ),
                    "GPU Temperature Avg (C)": round(
                        data.get("prometheus_vlm_gpu_temp_mean_c", 0), 1
                    ),
                    "GPU Temperature P90 (C)": round(
                        data.get("prometheus_vlm_gpu_temp_p90_c", 0), 1
                    ),
                    "GPU Power Avg (W)": round(data.get("prometheus_vlm_power_mean_watts", 0), 1),
                    "GPU Power P90 (W)": round(data.get("prometheus_vlm_power_p90_watts", 0), 1),
                    "CPU Core Utilization - Avg (%)": round(
                        data.get("nodeexporter_cpu_util_mean", 0), 1
                    ),
                    "System Memory Utilization - Avg (%)": round(
                        data.get("nodeexporter_memory_used_pct_mean", 0), 1
                    ),
                }
            )

    # Sort by resolution then OSL
    rows.sort(key=lambda r: (r["Image Resolution_w"], r["OSL"]))
    return rows


# ── Section 3: E2E File Processing ──────────────────────────────────────────
E2E_HEADERS = [
    "Release",
    "Platform",
    "Model",
    "Model Precision",
    "Engine Backend",
    "Chunk Duration (sec)",
    "# Frames / Chunk",
    "Image Resolution_w",
    "Image Resolution_h",
    "Vision Tokens",
    "ISL - Text",
    "OSL",
    "Benchmark Mode",
    "Scenario",
    "Video Duration",
    "Concurrency",
    "E2E Latency Min (s)",
    "E2E Latency Avg (s)",
    "E2E Latency Max (s)",
    "E2E Latency p50 (s)",
    "E2E Latency p75 (s)",
    "E2E Latency p90 (s)",
    "E2E Latency p95 (s)",
    "E2E Latency p99 (s)",
    *PIPELINE_STAGE_HEADERS,
    "Throughput (files/sec)",
    "Failed Requests",
    "Error Rate (%)",
    "GPU Core Utilization Avg (%)",
    "GPU Memory Utilization Avg (%)",
    "GPU NVdec Utilization - Avg (%)",
    "GPU Temperature Avg (C)",
    "GPU Power Avg (W)",
    "CPU Core Utilization Avg (%)",
    "System Memory Utilization Avg (%)",
]


def extract_e2e_data(
    report_dir: Path, platform: str, defaults: Dict[str, Any], config: Optional[Dict] = None
) -> List[Dict[str, Any]]:
    """Extract file processing data from file_burst and e2e_latency scenarios."""
    rows = []

    token_prefixes = [
        "file_burst_1_token",
        "file_burst_100_token",
        "e2e_latency_1_token",
        "e2e_latency_100_token",
    ]

    for token_prefix in token_prefixes:
        osl = parse_osl_from_name(token_prefix)

        for scenario_dir in find_scenario_dirs(report_dir, token_prefix):
            benchmark_mode = (
                "file_burst" if scenario_dir.name.startswith("file_burst") else "e2e_latency"
            )
            res_w, res_h, num_frames, vtokens = resolve_scenario_vision_info(
                config, scenario_dir.name
            )

            for tc_dir in find_test_case_dirs(scenario_dir):
                video_duration = parse_video_duration_from_name(tc_dir.name)

                # Try test_case_summary with concurrency_summary
                summary = load_json(tc_dir / "test_case_summary.json")
                concurrency_summary = summary.get("concurrency_summary") if summary else None
                if concurrency_summary:
                    # GPU data is in iteration_results.concurrency_results,
                    # not in concurrency_summary — average across iterations
                    iters = [
                        r for r in summary.get("iteration_results", []) if r.get("success", False)
                    ]
                    for level_str, stats in sorted(
                        concurrency_summary.items(), key=lambda x: int(x[0])
                    ):
                        concurrency = int(level_str)
                        # Collect GPU samples for this concurrency across iterations
                        gpu_samples = []
                        for ir in iters:
                            for cr in ir.get("concurrency_results", []):
                                if cr.get("concurrency_level") == concurrency:
                                    gpu_samples.append(cr)
                                    break
                        gpu_usage = (
                            _mean(
                                [get_gpu_metric(s, "gpu_usage_mean", platform) for s in gpu_samples]
                            )
                            if gpu_samples
                            else ""
                        )
                        gpu_mem = (
                            _mean(
                                [
                                    get_gpu_metric(s, "gpu_memory_mean", platform)
                                    for s in gpu_samples
                                ]
                            )
                            if gpu_samples
                            else ""
                        )
                        nvdec = (
                            _mean(
                                [
                                    get_gpu_metric(s, "nvdec_usage_mean", platform)
                                    for s in gpu_samples
                                ]
                            )
                            if gpu_samples
                            else ""
                        )
                        gpu_temp = (
                            _mean(
                                [
                                    float(s.get("prometheus_vlm_gpu_temp_mean_c", 0) or 0)
                                    for s in gpu_samples
                                ]
                            )
                            if gpu_samples
                            else ""
                        )
                        gpu_power = (
                            _mean(
                                [
                                    float(s.get("prometheus_vlm_power_mean_watts", 0) or 0)
                                    for s in gpu_samples
                                ]
                            )
                            if gpu_samples
                            else ""
                        )
                        cpu_util = (
                            _mean(
                                [
                                    float(s.get("nodeexporter_cpu_util_mean", 0) or 0)
                                    for s in gpu_samples
                                ]
                            )
                            if gpu_samples
                            else ""
                        )
                        sys_mem = (
                            _mean(
                                [
                                    float(s.get("nodeexporter_memory_used_pct_mean", 0) or 0)
                                    for s in gpu_samples
                                ]
                            )
                            if gpu_samples
                            else ""
                        )
                        failed_requests = stats.get("mean_failed_files")
                        if failed_requests is None:
                            failed_requests = _mean(
                                [float(s.get("failed_files", 0) or 0) for s in gpu_samples]
                            )
                        completed_requests = _mean(
                            [float(s.get("completed_files", 0) or 0) for s in gpu_samples]
                        )
                        min_lat = _latency_min_from_samples(gpu_samples, stats)
                        max_lat = _latency_max_from_samples(gpu_samples, stats)
                        rows.append(
                            {
                                "Release": defaults["release"],
                                "Platform": platform,
                                "Model": defaults["model"],
                                "Model Precision": defaults["precision"],
                                "Engine Backend": defaults["engine"],
                                "Chunk Duration (sec)": summary.get("chunk_size", 10)
                                or "Full video",
                                "# Frames / Chunk": num_frames,
                                "Image Resolution_w": res_w,
                                "Image Resolution_h": res_h,
                                "Vision Tokens": vtokens,
                                "ISL - Text": defaults["isl_text"],
                                "OSL": osl,
                                "Benchmark Mode": benchmark_mode,
                                "Scenario": scenario_dir.name,
                                "Video Duration": video_duration,
                                "Concurrency": concurrency,
                                "E2E Latency Min (s)": round(min_lat, 2),
                                "E2E Latency Avg (s)": round(
                                    non_negative_latency(stats.get("mean_avg_latency", 0)), 2
                                ),
                                "E2E Latency Max (s)": round(max_lat, 2),
                                "E2E Latency p50 (s)": round(
                                    non_negative_latency(stats.get("mean_p50_latency", 0)), 2
                                ),
                                "E2E Latency p75 (s)": round(
                                    non_negative_latency(stats.get("mean_p75_latency", 0)), 2
                                ),
                                "E2E Latency p90 (s)": round(
                                    non_negative_latency(stats.get("mean_p90_latency", 0)), 2
                                ),
                                "E2E Latency p95 (s)": round(
                                    non_negative_latency(stats.get("mean_p95_latency", 0)), 2
                                ),
                                "E2E Latency p99 (s)": round(
                                    non_negative_latency(stats.get("mean_p99_latency", 0)), 2
                                ),
                                **_stage_latency_columns(gpu_samples, stats),
                                "Throughput (files/sec)": round(stats.get("mean_throughput", 0), 2),
                                "Failed Requests": round(float(failed_requests or 0), 2),
                                "Error Rate (%)": round(
                                    _failure_rate(completed_requests, failed_requests), 2
                                ),
                                "GPU Core Utilization Avg (%)": (
                                    round(gpu_usage, 1) if gpu_usage != "" else ""
                                ),
                                "GPU Memory Utilization Avg (%)": (
                                    round(gpu_mem, 1) if gpu_mem != "" else ""
                                ),
                                "GPU NVdec Utilization - Avg (%)": (
                                    round(nvdec, 1) if nvdec != "" else ""
                                ),
                                "GPU Temperature Avg (C)": (
                                    round(gpu_temp, 1) if gpu_temp != "" else ""
                                ),
                                "GPU Power Avg (W)": round(gpu_power, 1) if gpu_power != "" else "",
                                "CPU Core Utilization Avg (%)": (
                                    round(cpu_util, 1) if cpu_util != "" else ""
                                ),
                                "System Memory Utilization Avg (%)": (
                                    round(sys_mem, 1) if sys_mem != "" else ""
                                ),
                            }
                        )
                    continue

                # Fallback: read iteration file_burst_results.json and average
                if summary and "iteration_results" in summary:
                    # A steady-state throughput run can be marked unsuccessful when one request
                    # fails even though it still contains useful per-concurrency measurements.
                    iters = [
                        r for r in summary["iteration_results"] if r.get("concurrency_results")
                    ]
                    for iter_data in iters:
                        for cr in iter_data.get("concurrency_results", []):
                            concurrency = cr.get("concurrency_level", 0)
                            failed_requests = cr.get("failed_files", 0)
                            completed_requests = cr.get("completed_files", 0)
                            rows.append(
                                {
                                    "Release": defaults["release"],
                                    "Platform": platform,
                                    "Model": defaults["model"],
                                    "Model Precision": defaults["precision"],
                                    "Engine Backend": defaults["engine"],
                                    "Chunk Duration (sec)": summary.get("chunk_size", 10)
                                    or "Full video",
                                    "# Frames / Chunk": num_frames,
                                    "Image Resolution_w": res_w,
                                    "Image Resolution_h": res_h,
                                    "Vision Tokens": vtokens,
                                    "ISL - Text": defaults["isl_text"],
                                    "OSL": osl,
                                    "Benchmark Mode": benchmark_mode,
                                    "Scenario": scenario_dir.name,
                                    "Video Duration": video_duration,
                                    "Concurrency": concurrency,
                                    "E2E Latency Min (s)": round(_latency_min(cr), 2),
                                    "E2E Latency Avg (s)": round(
                                        non_negative_latency(cr.get("avg_latency", 0)), 2
                                    ),
                                    "E2E Latency Max (s)": round(_latency_max(cr), 2),
                                    "E2E Latency p50 (s)": round(
                                        non_negative_latency(cr.get("p50_latency", 0)), 2
                                    ),
                                    "E2E Latency p75 (s)": round(
                                        non_negative_latency(cr.get("p75_latency", 0)), 2
                                    ),
                                    "E2E Latency p90 (s)": round(
                                        non_negative_latency(cr.get("p90_latency", 0)), 2
                                    ),
                                    "E2E Latency p95 (s)": round(
                                        non_negative_latency(cr.get("p95_latency", 0)), 2
                                    ),
                                    "E2E Latency p99 (s)": round(
                                        non_negative_latency(cr.get("p99_latency", 0)), 2
                                    ),
                                    **_stage_latency_columns([cr], cr),
                                    "Throughput (files/sec)": round(
                                        cr.get("throughput_files_per_second", 0), 2
                                    ),
                                    "Failed Requests": failed_requests,
                                    "Error Rate (%)": round(
                                        _failure_rate(completed_requests, failed_requests), 2
                                    ),
                                    "GPU Core Utilization Avg (%)": round(
                                        get_gpu_metric(cr, "gpu_usage_mean", platform), 1
                                    ),
                                    "GPU Memory Utilization Avg (%)": round(
                                        get_gpu_metric(cr, "gpu_memory_mean", platform), 1
                                    ),
                                    "GPU NVdec Utilization - Avg (%)": round(
                                        get_gpu_metric(cr, "nvdec_usage_mean", platform), 1
                                    ),
                                    "GPU Temperature Avg (C)": round(
                                        cr.get("prometheus_vlm_gpu_temp_mean_c", 0), 1
                                    ),
                                    "GPU Power Avg (W)": round(
                                        cr.get("prometheus_vlm_power_mean_watts", 0), 1
                                    ),
                                    "CPU Core Utilization Avg (%)": round(
                                        cr.get("nodeexporter_cpu_util_mean", 0), 1
                                    ),
                                    "System Memory Utilization Avg (%)": round(
                                        cr.get("nodeexporter_memory_used_pct_mean", 0), 1
                                    ),
                                }
                            )
                        # Only use first successful iteration for fallback
                        break
                    continue

                # Last resort: read iteration dirs for file_burst_results.json
                for iter_dir in sorted(tc_dir.iterdir()):
                    if not iter_dir.is_dir() or not iter_dir.name.startswith("iteration_"):
                        continue
                    fb_data = load_json(iter_dir / "file_burst_results.json")
                    if not fb_data:
                        continue
                    for cr in fb_data.get("concurrency_results", []):
                        concurrency = cr.get("concurrency_level", 0)
                        failed_requests = cr.get("failed_files", 0)
                        completed_requests = cr.get("completed_files", 0)
                        rows.append(
                            {
                                "Release": defaults["release"],
                                "Platform": platform,
                                "Model": defaults["model"],
                                "Model Precision": defaults["precision"],
                                "Engine Backend": defaults["engine"],
                                "Chunk Duration (sec)": 10,
                                "# Frames / Chunk": num_frames,
                                "Image Resolution_w": res_w,
                                "Image Resolution_h": res_h,
                                "Vision Tokens": vtokens,
                                "ISL - Text": defaults["isl_text"],
                                "OSL": osl,
                                "Benchmark Mode": benchmark_mode,
                                "Scenario": scenario_dir.name,
                                "Video Duration": video_duration,
                                "Concurrency": concurrency,
                                "E2E Latency Min (s)": round(_latency_min(cr), 2),
                                "E2E Latency Avg (s)": round(
                                    non_negative_latency(cr.get("avg_latency", 0)), 2
                                ),
                                "E2E Latency Max (s)": round(_latency_max(cr), 2),
                                "E2E Latency p50 (s)": round(
                                    non_negative_latency(cr.get("p50_latency", 0)), 2
                                ),
                                "E2E Latency p75 (s)": round(
                                    non_negative_latency(cr.get("p75_latency", 0)), 2
                                ),
                                "E2E Latency p90 (s)": round(
                                    non_negative_latency(cr.get("p90_latency", 0)), 2
                                ),
                                "E2E Latency p95 (s)": round(
                                    non_negative_latency(cr.get("p95_latency", 0)), 2
                                ),
                                "E2E Latency p99 (s)": round(
                                    non_negative_latency(cr.get("p99_latency", 0)), 2
                                ),
                                **_stage_latency_columns([cr], cr),
                                "Throughput (files/sec)": round(
                                    cr.get("throughput_files_per_second", 0), 2
                                ),
                                "Failed Requests": failed_requests,
                                "Error Rate (%)": round(
                                    _failure_rate(completed_requests, failed_requests), 2
                                ),
                                "GPU Core Utilization Avg (%)": round(
                                    get_gpu_metric(cr, "gpu_usage_mean", platform), 1
                                ),
                                "GPU Memory Utilization Avg (%)": round(
                                    get_gpu_metric(cr, "gpu_memory_mean", platform), 1
                                ),
                                "GPU NVdec Utilization - Avg (%)": round(
                                    get_gpu_metric(cr, "nvdec_usage_mean", platform), 1
                                ),
                                "GPU Temperature Avg (C)": round(
                                    cr.get("prometheus_vlm_gpu_temp_mean_c", 0), 1
                                ),
                                "GPU Power Avg (W)": round(
                                    cr.get("prometheus_vlm_power_mean_watts", 0), 1
                                ),
                                "CPU Core Utilization Avg (%)": round(
                                    cr.get("nodeexporter_cpu_util_mean", 0), 1
                                ),
                                "System Memory Utilization Avg (%)": round(
                                    cr.get("nodeexporter_memory_used_pct_mean", 0), 1
                                ),
                            }
                        )
                    # Only first iteration
                    break

    # Sort by scenario family, OSL, vision tier, video duration order, concurrency
    duration_order = {"10 sec": 0, "30 sec": 1, "5 min": 2, "10 min": 3, "60 min": 4}
    rows.sort(
        key=lambda r: (
            r["Benchmark Mode"],
            r["OSL"],
            r["Vision Tokens"],
            duration_order.get(r["Video Duration"], 99),
            r["Concurrency"],
        )
    )
    return rows


# ── Utility ──────────────────────────────────────────────────────────────────
def _mean(values: List[float]) -> float:
    """Safe mean that returns 0 for empty lists."""
    if not values:
        return 0.0
    return sum(values) / len(values)


def non_negative_latency(value: Any) -> float:
    """Clamp impossible negative latency values caused by timestamp clock skew."""
    try:
        return max(float(value or 0), 0.0)
    except (TypeError, ValueError):
        return 0.0


def _stage_latency_columns(
    samples: List[Dict[str, Any]], summary: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """Return XLSX columns for per-stage chunk latency metrics."""
    row: Dict[str, Any] = {}
    summary = summary or {}
    for metric_base, stage_label in PIPELINE_STAGE_COLUMN_SPECS:
        for stat_key, stat_label in PIPELINE_STAGE_STAT_SPECS:
            header = f"{stage_label} Stage Latency {stat_label} (s)"
            field = f"{metric_base}_{stat_key}"
            values = [
                non_negative_latency(sample.get(field))
                for sample in samples
                if isinstance(sample.get(field), (int, float))
            ]
            if values:
                value: Any = _mean(values)
            else:
                value = ""
                for candidate in (f"mean_{field}", field):
                    if isinstance(summary.get(candidate), (int, float)):
                        value = non_negative_latency(summary[candidate])
                        break
            row[header] = round(value, 2) if isinstance(value, (int, float)) else value
    return row


def _failure_rate(completed: Any, failed: Any) -> float:
    """Return failed / total as a percentage when request counts are available."""
    try:
        completed_f = max(float(completed or 0), 0.0)
        failed_f = max(float(failed or 0), 0.0)
    except (TypeError, ValueError):
        return 0.0
    total = completed_f + failed_f
    return (failed_f / total) * 100.0 if total > 0 else 0.0


def _flatten_numeric_latencies(value: Any) -> List[float]:
    """Extract latency values from result histories that may be list or dict shaped."""
    if isinstance(value, (int, float)):
        return [non_negative_latency(value)]
    if isinstance(value, list):
        latencies = []
        for item in value:
            latencies.extend(_flatten_numeric_latencies(item))
        return latencies
    if isinstance(value, dict):
        for key in ("latency", "latency_seconds", "processing_time", "e2e_latency_seconds"):
            if key in value:
                return _flatten_numeric_latencies(value[key])
        latencies = []
        for item in value.values():
            latencies.extend(_flatten_numeric_latencies(item))
        return latencies
    return []


def _latency_min(result: Dict[str, Any]) -> float:
    """Return min latency from explicit fields or latency history."""
    if "min_latency" in result:
        return non_negative_latency(result.get("min_latency", 0))
    latencies = _flatten_numeric_latencies(result.get("latency_history", []))
    if latencies:
        return min(latencies)
    return non_negative_latency(result.get("avg_latency", 0))


def _latency_max(result: Dict[str, Any]) -> float:
    """Return max latency from explicit fields or latency history."""
    if "max_latency" in result:
        return non_negative_latency(result.get("max_latency", 0))
    latencies = _flatten_numeric_latencies(result.get("latency_history", []))
    if latencies:
        return max(latencies)
    return non_negative_latency(result.get("avg_latency", 0))


def _latency_min_from_samples(samples: List[Dict[str, Any]], stats: Dict[str, Any]) -> float:
    """Return min latency from per-iteration samples, falling back to aggregate stats."""
    values = [_latency_min(sample) for sample in samples if sample]
    return min(values) if values else non_negative_latency(stats.get("mean_avg_latency", 0))


def _latency_max_from_samples(samples: List[Dict[str, Any]], stats: Dict[str, Any]) -> float:
    """Return max latency from per-iteration samples, falling back to aggregate stats."""
    values = [_latency_max(sample) for sample in samples if sample]
    return max(values) if values else non_negative_latency(stats.get("mean_avg_latency", 0))


def _get_iter_gpu_metric(iters: List[Dict], metric_base: str, platform: str) -> float:
    """Get mean GPU metric across iterations with platform-aware fallback."""
    return _mean([get_gpu_metric(r, metric_base, platform) for r in iters])


def auto_fit_columns(ws) -> None:
    """Auto-fit column widths based on content."""
    for col in ws.columns:
        max_length = 0
        col_letter = get_column_letter(col[0].column)
        for cell in col:
            if cell.value is not None:
                cell_len = len(str(cell.value))
                if cell_len > max_length:
                    max_length = cell_len
        # Add padding, cap at 35
        adjusted_width = min(max_length + 3, 35)
        ws.column_dimensions[col_letter].width = max(adjusted_width, 10)


def write_section(
    ws,
    start_row: int,
    section_title: str,
    headers: List[str],
    rows: List[Dict[str, Any]],
    section_fill: PatternFill,
) -> int:
    """Write a section (title + headers + data rows) to the worksheet.

    Returns the next available row.
    """
    num_cols = len(headers)

    # Section title row (merged)
    ws.merge_cells(start_row=start_row, start_column=1, end_row=start_row, end_column=num_cols)
    title_cell = ws.cell(row=start_row, column=1, value=section_title)
    title_cell.font = SECTION_FONT
    title_cell.fill = section_fill
    title_cell.alignment = Alignment(horizontal="center")
    start_row += 1

    # Header row
    for col_idx, header in enumerate(headers, 1):
        cell = ws.cell(row=start_row, column=col_idx, value=header)
        cell.font = HEADER_FONT
        cell.border = THIN_BORDER
        cell.alignment = Alignment(horizontal="center", wrap_text=True)
    start_row += 1

    # Data rows
    for row_data in rows:
        for col_idx, header in enumerate(headers, 1):
            value = row_data.get(header, "")
            cell = ws.cell(row=start_row, column=col_idx, value=value)
            cell.border = THIN_BORDER
            cell.alignment = Alignment(horizontal="center")
            # Apply number formatting
            if isinstance(value, float):
                if abs(value) < 100:
                    cell.number_format = NUMBER_FORMAT_2DP
                else:
                    cell.number_format = NUMBER_FORMAT_1DP
            elif isinstance(value, int):
                cell.number_format = NUMBER_FORMAT_INT
        start_row += 1

    # Blank separator row
    start_row += 1
    return start_row


def write_platform_tab(
    wb: Workbook,
    platform: str,
    report_dir: Path,
    defaults: Dict[str, Any],
    config: Optional[Dict] = None,
) -> None:
    """Create a worksheet tab for a single platform with all 3 sections."""
    # Truncate sheet name to 31 chars (Excel limit)
    sheet_name = platform[:31]
    ws = wb.create_sheet(title=sheet_name)

    row = 1

    # Section 1: Concurrent Streams & Latency
    concurrency_rows = extract_concurrency_data(report_dir, platform, defaults, config)
    if concurrency_rows:
        row = write_section(
            ws,
            row,
            "Concurrent Streams & Latency",
            CONCURRENCY_HEADERS,
            concurrency_rows,
            SECTION_FILL_1,
        )
    else:
        print(f"  [{platform}] No concurrency test data found.")

    # Section 2: Max Concurrent Streams & Latency
    max_streams_rows = extract_max_streams_data(report_dir, platform, defaults, config)
    if max_streams_rows:
        row = write_section(
            ws,
            row,
            "Max Concurrent Streams & Latency",
            MAX_STREAMS_HEADERS,
            max_streams_rows,
            SECTION_FILL_2,
        )
    else:
        print(f"  [{platform}] No max_live_streams data found.")

    # Section 3: File Processing / Throughput
    e2e_rows = extract_e2e_data(report_dir, platform, defaults, config)
    if e2e_rows:
        row = write_section(
            ws,
            row,
            "File Processing / Throughput",
            E2E_HEADERS,
            e2e_rows,
            SECTION_FILL_3,
        )
    else:
        print(f"  [{platform}] No E2E latency data found.")

    # Freeze top row
    ws.freeze_panes = "A2"

    # Auto-fit columns
    auto_fit_columns(ws)


def write_summary_tab(
    wb: Workbook,
    reports: Dict[str, Path],
    defaults: Dict[str, Any],
    configs: Optional[Dict[str, Optional[Dict]]] = None,
) -> None:
    """Create a Summary tab with key metrics from all platforms side by side."""
    if configs is None:
        configs = {}
    ws = wb.create_sheet(title="Summary", index=0)

    platforms = list(reports.keys())
    row = 1

    # ── Max Streams Summary ──────────────────────────────────────────────
    ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=1 + len(platforms))
    cell = ws.cell(row=row, column=1, value="Max Sustainable Streams")
    cell.font = SUMMARY_HEADER_FONT
    cell.fill = SUMMARY_HEADER_FILL
    row += 1

    # Headers
    ws.cell(row=row, column=1, value="Scenario").font = HEADER_FONT
    for i, p in enumerate(platforms, 2):
        ws.cell(row=row, column=i, value=p).font = HEADER_FONT
    row += 1

    # Collect max streams data per platform
    max_streams_by_platform = {}
    for platform, report_dir in reports.items():
        config = configs.get(platform)
        ms_rows = extract_max_streams_data(report_dir, platform, defaults, config)
        max_streams_by_platform[platform] = ms_rows

    # Build unique scenario keys
    scenario_keys = set()
    for platform, ms_rows in max_streams_by_platform.items():
        for r in ms_rows:
            res = f"{r['Image Resolution_w']}x{r['Image Resolution_h']}"
            key = f"OSL={r['OSL']} / {res}"
            scenario_keys.add(key)

    for key in sorted(scenario_keys):
        ws.cell(row=row, column=1, value=key)
        for i, platform in enumerate(platforms, 2):
            ms_rows = max_streams_by_platform.get(platform, [])
            for r in ms_rows:
                res = f"{r['Image Resolution_w']}x{r['Image Resolution_h']}"
                rkey = f"OSL={r['OSL']} / {res}"
                if rkey == key:
                    val = r["Max Concurrent Streams"]
                    lat = r["Chunk E2E Latency Avg (s)"]
                    p99 = r["Chunk E2E Latency p99 (s)"]
                    drops = r["Dropped Chunks"]
                    ws.cell(
                        row=row, column=i, value=f"{val} (avg={lat}s / p99={p99}s / drops={drops})"
                    )
                    break
        row += 1

    row += 1

    # ── Concurrency Latency Summary ──────────────────────────────────────
    ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=1 + len(platforms))
    cell = ws.cell(row=row, column=1, value="Concurrency Latency (Avg / p95 / p99 / errors)")
    cell.font = SUMMARY_HEADER_FONT
    cell.fill = SUMMARY_HEADER_FILL
    row += 1

    ws.cell(row=row, column=1, value="Streams / OSL").font = HEADER_FONT
    for i, p in enumerate(platforms, 2):
        ws.cell(row=row, column=i, value=p).font = HEADER_FONT
    row += 1

    conc_by_platform = {}
    for platform, report_dir in reports.items():
        config = configs.get(platform)
        conc_rows = extract_concurrency_data(report_dir, platform, defaults, config)
        conc_by_platform[platform] = conc_rows

    conc_keys = set()
    for platform, c_rows in conc_by_platform.items():
        for r in c_rows:
            key = f"{r['Concurrent Streams']} streams / OSL={r['OSL']}"
            conc_keys.add(key)

    for key in sorted(conc_keys):
        ws.cell(row=row, column=1, value=key)
        for i, platform in enumerate(platforms, 2):
            c_rows = conc_by_platform.get(platform, [])
            for r in c_rows:
                rkey = f"{r['Concurrent Streams']} streams / OSL={r['OSL']}"
                if rkey == key:
                    avg = r["Chunk E2E Latency Avg (s)"]
                    p95 = r["Chunk E2E Latency p95 (s)"]
                    p99 = r["Chunk E2E Latency p99 (s)"]
                    err = r["Error Rate (%)"]
                    ws.cell(
                        row=row, column=i, value=f"{avg}s / p95={p95}s / p99={p99}s / err={err}%"
                    )
                    break
        row += 1

    row += 1

    # ── File Processing / Throughput Summary ────────────────────────────
    ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=1 + len(platforms))
    cell = ws.cell(row=row, column=1, value="File Processing / Throughput")
    cell.font = SUMMARY_HEADER_FONT
    cell.fill = SUMMARY_HEADER_FILL
    row += 1

    ws.cell(row=row, column=1, value="Mode / Vision Tokens / Duration / OSL / c").font = HEADER_FONT
    for i, p in enumerate(platforms, 2):
        ws.cell(row=row, column=i, value=p).font = HEADER_FONT
    row += 1

    e2e_by_platform = {}
    for platform, report_dir in reports.items():
        e2e_config = configs.get(platform)
        e2e_rows = extract_e2e_data(report_dir, platform, defaults, e2e_config)
        e2e_by_platform[platform] = e2e_rows

    e2e_keys = set()
    for platform, e_rows in e2e_by_platform.items():
        for r in e_rows:
            key = (
                f"{r['Benchmark Mode']} / VT={r['Vision Tokens']} / "
                f"{r['Video Duration']} / OSL={r['OSL']} / c={r['Concurrency']}"
            )
            e2e_keys.add(key)

    duration_order = {"10 sec": 0, "30 sec": 1, "5 min": 2, "10 min": 3, "60 min": 4}

    def _file_processing_sort_key(key: str) -> Tuple[str, int, int, int, int]:
        parts = [p.strip() for p in key.split("/")]
        mode = parts[0] if parts else ""
        vt = int(parts[1].split("=", 1)[1]) if len(parts) > 1 and "=" in parts[1] else 0
        duration = parts[2] if len(parts) > 2 else ""
        osl = int(parts[3].split("=", 1)[1]) if len(parts) > 3 and "=" in parts[3] else 0
        concurrency = int(parts[4].split("=", 1)[1]) if len(parts) > 4 and "=" in parts[4] else 0
        return (mode, osl, vt, duration_order.get(duration, 99), concurrency)

    for key in sorted(
        e2e_keys,
        key=_file_processing_sort_key,
    ):
        ws.cell(row=row, column=1, value=key)
        for i, platform in enumerate(platforms, 2):
            e_rows = e2e_by_platform.get(platform, [])
            for r in e_rows:
                rkey = (
                    f"{r['Benchmark Mode']} / VT={r['Vision Tokens']} / "
                    f"{r['Video Duration']} / OSL={r['OSL']} / c={r['Concurrency']}"
                )
                if rkey == key:
                    avg = r["E2E Latency Avg (s)"]
                    p95 = r["E2E Latency p95 (s)"]
                    p99 = r["E2E Latency p99 (s)"]
                    throughput = r["Throughput (files/sec)"]
                    failed = r["Failed Requests"]
                    ws.cell(
                        row=row,
                        column=i,
                        value=(
                            f"{avg}s / p95={p95}s / p99={p99}s / "
                            f"rps={throughput} / failed={failed}"
                        ),
                    )
                    break
        row += 1

    # Freeze top row
    ws.freeze_panes = "B2"

    # Auto-fit columns
    auto_fit_columns(ws)


# ── CLI ──────────────────────────────────────────────────────────────────────
def parse_reports(report_args: List[str]) -> Dict[str, Path]:
    """Parse PLATFORM=PATH report arguments."""
    reports = {}
    for arg in report_args:
        if "=" not in arg:
            print(f"Error: report argument must be PLATFORM=PATH, got: {arg}")
            sys.exit(1)
        name, path_str = arg.split("=", 1)
        p = Path(path_str).expanduser().resolve()
        if not p.exists():
            print(f"Warning: report directory does not exist: {p}")
        reports[name] = p
    return reports


def extract_gpu_info_from_xlsx(report_dir: Path) -> List[Dict[str, Any]]:
    """Extract GPU Info from per-scenario XLSX reports in a report directory.

    The benchmark framework writes GPU_Info sheets into each scenario's XLSX.
    We read the first valid one we find.
    """
    try:
        from openpyxl import load_workbook as _load_wb
    except ImportError:
        return []

    for xlsx_path in sorted(report_dir.glob("*.xlsx")):
        if xlsx_path.name.startswith("~$"):
            continue  # skip lock files
        try:
            wb_src = _load_wb(str(xlsx_path), read_only=True, data_only=True)
        except Exception:
            continue
        if "GPU_Info" not in wb_src.sheetnames:
            wb_src.close()
            continue
        ws = wb_src["GPU_Info"]
        rows_data = list(ws.iter_rows(values_only=True))
        wb_src.close()
        if len(rows_data) < 2:
            continue
        headers = rows_data[0]
        gpu_rows = []
        for row_vals in rows_data[1:]:
            gpu_rows.append(dict(zip(headers, row_vals)))
        return gpu_rows
    return []


def write_gpu_info_tab(
    wb: Workbook,
    reports: Dict[str, Path],
) -> None:
    """Create a GPU Info tab showing GPU hardware specs per platform."""
    ws = wb.create_sheet(title="GPU Info")

    row = 1
    ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=10)
    cell = ws.cell(row=row, column=1, value="GPU Hardware Information")
    cell.font = SUMMARY_HEADER_FONT
    cell.fill = SUMMARY_HEADER_FILL
    row += 1

    gpu_info_headers = [
        "Platform",
        "GPU Index",
        "Name",
        "Total Memory (GB)",
        "Compute Capability",
        "Max GPU Clock (MHz)",
        "Max Memory Clock (MHz)",
        "Driver Version",
        "Used By",
    ]

    for col_idx, header in enumerate(gpu_info_headers, 1):
        cell = ws.cell(row=row, column=col_idx, value=header)
        cell.font = HEADER_FONT
        cell.border = THIN_BORDER
        cell.alignment = Alignment(horizontal="center", wrap_text=True)
    row += 1

    found_any = False
    for platform, report_dir in reports.items():
        gpu_rows = extract_gpu_info_from_xlsx(report_dir)
        if not gpu_rows:
            # Write a placeholder row
            ws.cell(row=row, column=1, value=platform).border = THIN_BORDER
            ws.cell(row=row, column=3, value="GPU info not available").border = THIN_BORDER
            row += 1
            continue
        found_any = True
        for gpu_data in gpu_rows:
            name_val = gpu_data.get("Name", "")
            if isinstance(name_val, str) and "Failed" in name_val:
                ws.cell(row=row, column=1, value=platform).border = THIN_BORDER
                ws.cell(row=row, column=3, value="GPU info not supported (Jetson)").border = (
                    THIN_BORDER
                )
                row += 1
                break
            vals = [
                platform,
                gpu_data.get("GPU Index", ""),
                name_val,
                gpu_data.get("Total Memory (GB)", ""),
                gpu_data.get("Compute Capability", ""),
                gpu_data.get("Max GPU Clock (MHz)", ""),
                gpu_data.get("Max Memory Clock (MHz)", ""),
                gpu_data.get("Driver Version", ""),
                gpu_data.get("Used By", ""),
            ]
            for col_idx, val in enumerate(vals, 1):
                cell = ws.cell(row=row, column=col_idx, value=val)
                cell.border = THIN_BORDER
                cell.alignment = Alignment(horizontal="center")
            row += 1

    auto_fit_columns(ws)
    if found_any:
        print("  GPU Info tab created.")
    else:
        print("  GPU Info tab created (no GPU info found in report xlsx files).")


def write_charts_tab(wb: Workbook, charts_dir: Path):
    """Embed PNG charts from a directory into a 'Charts' tab."""
    png_files = sorted(charts_dir.glob("*.png"))
    if not png_files:
        print("  No PNG files found in charts directory.")
        return

    ws = wb.create_sheet("Charts", 0)
    row = 1

    for png_path in png_files:
        # Add chart title
        ws.cell(row=row, column=1, value=png_path.stem.replace("_", " ").title())
        ws.cell(row=row, column=1).font = Font(bold=True, size=12)
        row += 1

        # Embed the image
        try:
            img = XlImage(str(png_path))
            # Scale to reasonable size (width ~900px in Excel)
            scale = min(900 / img.width, 500 / img.height) if img.width > 0 else 0.5
            img.width = int(img.width * scale)
            img.height = int(img.height * scale)
            ws.add_image(img, f"A{row}")
            # Advance rows based on image height (~15px per row)
            row += max(int(img.height / 15) + 2, 5)
            print(f"  Embedded: {png_path.name}")
        except Exception as e:
            print(f"  Warning: Failed to embed {png_path.name}: {e}")
            row += 2


def write_machine_config_tab(
    wb: Workbook,
    configs: Dict[str, Optional[Dict]],
    reports: Dict[str, Path],
) -> None:
    """Create a Machine Config tab showing test configuration per platform.

    Shows global settings (backend URL, output dir, GPU, monitoring) and
    per-scenario parameters (resolution, frames, tokens, stream counts, thresholds).
    """
    ws = wb.create_sheet(title="Machine Config")

    row = 1
    platforms = list(configs.keys())

    # ── Global Settings ──────────────────────────────────────────────────
    ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=3 + len(platforms))
    cell = ws.cell(row=row, column=1, value="Platform Configuration")
    cell.font = SUMMARY_HEADER_FONT
    cell.fill = SUMMARY_HEADER_FILL
    row += 1

    ws.cell(row=row, column=1, value="Setting").font = HEADER_FONT
    for i, p in enumerate(platforms, 2):
        ws.cell(row=row, column=i, value=p).font = HEADER_FONT
    row += 1

    global_keys = [
        ("Report Directory", lambda c, p: str(reports.get(p, ""))),
        ("Backend URL", lambda c, p: c.get("global", {}).get("rtvi_backend", "") if c else ""),
        ("Output Dir", lambda c, p: c.get("global", {}).get("output_dir", "") if c else ""),
        ("VLM GPUs", lambda c, p: str(c.get("global", {}).get("vlm_gpus", [])) if c else ""),
        (
            "GPU Monitoring",
            lambda c, p: (
                str(c.get("global", {}).get("gpu_monitoring", {}).get("enabled", "")) if c else ""
            ),
        ),
        (
            "Prometheus Enabled",
            lambda c, p: (
                str(
                    c.get("global", {})
                    .get("gpu_monitoring", {})
                    .get("prometheus", {})
                    .get("enabled", "")
                )
                if c
                else ""
            ),
        ),
        (
            "DCGM Exporter URL",
            lambda c, p: (
                c.get("global", {})
                .get("gpu_monitoring", {})
                .get("prometheus", {})
                .get("dcgm_exporter_url", "")
                if c
                else ""
            ),
        ),
        (
            "Default Resolution",
            lambda c, p: (
                (
                    f"{c.get('global', {}).get('generate_captions_params', {}).get('vlm_input_width', '')}x"
                    f"{c.get('global', {}).get('generate_captions_params', {}).get('vlm_input_height', '')}"
                )
                if c
                else ""
            ),
        ),
        (
            "Default Frames/Chunk",
            lambda c, p: (
                str(
                    c.get("global", {})
                    .get("generate_captions_params", {})
                    .get("num_frames_per_second_or_fixed_frames_chunk", "")
                )
                if c
                else ""
            ),
        ),
    ]

    for label, fn in global_keys:
        ws.cell(row=row, column=1, value=label)
        for i, p in enumerate(platforms, 2):
            config = configs.get(p)
            ws.cell(row=row, column=i, value=fn(config, p))
        row += 1

    row += 1

    # ── Per-Scenario Parameters ──────────────────────────────────────────
    ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=3 + len(platforms))
    cell = ws.cell(row=row, column=1, value="Per-Scenario Test Parameters")
    cell.font = SUMMARY_HEADER_FONT
    cell.fill = SECTION_FILL_2
    row += 1

    scenario_headers = [
        "Scenario",
        "Resolution",
        "Frames/Chunk",
        "Vision Tokens",
        "Max Tokens (OSL)",
        "Benchmark Mode",
    ]
    for col_idx, h in enumerate(scenario_headers, 1):
        ws.cell(row=row, column=col_idx, value=h).font = HEADER_FONT
    row += 1

    # Use first available config to enumerate scenarios
    sample_config = next((c for c in configs.values() if c), None)
    if sample_config and "test_scenarios" in sample_config:
        for scenario_name in sample_config["test_scenarios"]:
            scenario = sample_config["test_scenarios"][scenario_name]
            p = get_scenario_params(sample_config, scenario_name)
            w, h, nf = p["vlm_input_width"], p["vlm_input_height"], p["num_frames"]
            vt = calc_vision_tokens(w, h, nf)

            ws.cell(row=row, column=1, value=scenario_name)
            ws.cell(row=row, column=2, value=f"{w}x{h}")
            ws.cell(row=row, column=3, value=nf)
            ws.cell(row=row, column=4, value=f"{vt} ({vision_tokens_label(vt)})")
            ws.cell(row=row, column=5, value=p["max_tokens"])
            ws.cell(row=row, column=6, value=scenario.get("benchmark_mode", ""))
            row += 1

    ws.freeze_panes = "A2"
    auto_fit_columns(ws)


def main():
    parser = argparse.ArgumentParser(
        description="Generate EngSQA-format XLSX from RTVI-VLM perf benchmark JSON reports",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--reports",
        nargs="+",
        required=True,
        metavar="PLATFORM=PATH",
        help="Platform reports as PLATFORM=PATH pairs (e.g. H100=~/path/to/report)",
    )
    parser.add_argument(
        "--output",
        type=str,
        default="perf_report.xlsx",
        help="Output XLSX file path (default: perf_report.xlsx)",
    )
    parser.add_argument(
        "--release",
        type=str,
        default="3.1",
        help="Release version string (default: 3.1)",
    )
    parser.add_argument(
        "--model",
        type=str,
        default="CR2-8B",
        help="Model name (default: CR2-8B)",
    )
    parser.add_argument(
        "--precision",
        type=str,
        default="FP8",
        help="Model precision (default: FP8)",
    )
    parser.add_argument(
        "--engine",
        type=str,
        default="vLLM",
        help="Engine backend (default: vLLM)",
    )
    parser.add_argument(
        "--isl-text",
        type=int,
        default=549,
        help="Input sequence length for text (default: 549)",
    )
    parser.add_argument(
        "--configs",
        nargs="+",
        default=[],
        metavar="PLATFORM=PATH",
        help="Platform config YAMLs as PLATFORM=PATH pairs (e.g. H100=rtvi_vlm_config_h100.yaml)",
    )
    parser.add_argument(
        "--charts",
        type=str,
        default=None,
        help="Directory containing PNG charts to embed in a 'Charts' tab (e.g. ./perf_charts)",
    )

    args = parser.parse_args()
    reports = parse_reports(args.reports)

    # Parse configs
    configs: Dict[str, Optional[Dict]] = {}
    for arg in args.configs:
        if "=" not in arg:
            print(f"Error: config argument must be PLATFORM=PATH, got: {arg}")
            sys.exit(1)
        name, path_str = arg.split("=", 1)
        configs[name] = load_yaml_config(Path(path_str).expanduser().resolve())

    defaults = {
        "release": args.release,
        "model": args.model,
        "precision": args.precision,
        "engine": args.engine,
        "isl_text": args.isl_text,
    }

    print(f"Platforms: {', '.join(reports.keys())}")
    print(f"Output: {args.output}")
    print(
        f"Defaults: release={defaults['release']}, model={defaults['model']}, "
        f"precision={defaults['precision']}, engine={defaults['engine']}"
    )

    wb = Workbook()
    # Remove default sheet
    wb.remove(wb.active)

    # Create Summary tab first (will be index 0)
    # But we need to create platform tabs first to collect data
    for platform, report_dir in reports.items():
        print(f"\nProcessing: {platform} ({report_dir})")
        config = configs.get(platform)
        write_platform_tab(wb, platform, report_dir, defaults, config)

    # Create Summary tab (inserted at index 0)
    print("\nGenerating Summary tab...")
    write_summary_tab(wb, reports, defaults, configs)

    # Create GPU Info tab
    print("\nGenerating GPU Info tab...")
    write_gpu_info_tab(wb, reports)

    # Create Machine Config tab
    if configs:
        print("\nGenerating Machine Config tab...")
        write_machine_config_tab(wb, configs, reports)

    # Embed charts if provided
    if args.charts:
        charts_dir = Path(args.charts).expanduser()
        if charts_dir.exists():
            print(f"\nEmbedding charts from: {charts_dir}")
            write_charts_tab(wb, charts_dir)
        else:
            print(f"\nWarning: charts directory not found: {charts_dir}")

    # Save
    output_path = Path(args.output).resolve()
    wb.save(str(output_path))
    print(f"\nSaved: {output_path}")


if __name__ == "__main__":
    main()
