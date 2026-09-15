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
RTVI-VLM Performance Report Chart Generator

Reads JSON results from perf benchmark report directories and generates
publication-quality comparison charts across platforms, resolutions, and EA releases.

Usage:
    # Generate all charts from multi-platform reports
    python3 plot_perf_reports.py all \\
        --reports h100=/path/to/h100-report rtx_pro=/path/to/rtx_pro-report \\
                  thor=/path/to/thor-report spark=/path/to/spark-report \\
        --configs h100=rtvi_vlm_config_h100.yaml rtx_pro=rtvi_vlm_config_rtx_pro.yaml \\
                  thor=rtvi_vlm_config_jetson.yaml spark=rtvi_vlm_config_spark.yaml \\
        --ea-baselines ea1=ea_baseline_data/ea1_engsqa.json ea2=ea_baseline_data/ea2_engsqa.json \\
        --output ./charts

    # Generate a single chart type
    python3 plot_perf_reports.py max_streams_2k \\
        --reports h100=/path/to/h100-report rtx_pro=/path/to/rtx_pro-report

    # Print text summary table only
    python3 plot_perf_reports.py summary \\
        --reports h100=/path/to/h100-report rtx_pro=/path/to/rtx_pro-report

Chart Types:
    max_streams_2k       Max live streams at 372x372, 30 frm (~2K vision tokens)
    max_streams_8k       Max live streams at 448x448, 80 frm (~8K vision tokens)
    max_streams_2k_vs_8k Max streams by available 2K/4K/8K tier
    concurrency          Live concurrent streams chunk latency (8K)
    e2e_latency          E2E file latency by video duration (2K/4K/8K when available)
    processing_speed     Processing speed as x-realtime multiplier
    gpu_util             GPU & NVDEC utilization vs concurrent streams (2K & 8K)
    throughput           Request throughput vs concurrency (warehouse_10s)
    ea_max_streams       EA1 vs EA2 max live streams comparison
    all                  Generate all charts + summary table
    summary              Print text summary table only
"""

import argparse
import math
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import matplotlib.pyplot as plt
import numpy as np
from perf_utils import load_json

try:
    import yaml
except ImportError:
    yaml = None  # graceful fallback; config reading will warn


# ── Constants ────────────────────────────────────────────────────────────────

PLATFORM_COLORS = {
    "h100": "#2E86C1",
    "rtx_pro": "#E67E22",
    "thor": "#27AE60",
    "spark": "#8E44AD",
    "l40s": "#E74C3C",
    "b200": "#34495E",
    "igx_thor": "#27AE60",
    "agx_thor": "#1ABC9C",
    "dgx_spark": "#8E44AD",
}

TOKEN_COLORS = {"1t": "#5B7DB1", "100t": "#8CB150"}

VISION_TIERS = ["2K", "4K", "8K"]

MAX_STREAMS_SCENARIO_SPECS = [
    (
        "2K",
        ["max_live_streams_test_1_token_2k", "max_live_streams_test_1_token"],
        ["max_live_streams_test_100_token_2k", "max_live_streams_test_100_token"],
    ),
    ("4K", ["max_live_streams_test_1_token_4k"], ["max_live_streams_test_100_token_4k"]),
    (
        "8K",
        ["max_live_streams_test_1_token_8k", "max_live_streams_test_1_token_448"],
        ["max_live_streams_test_100_token_8k", "max_live_streams_test_100_token_448"],
    ),
]

CONCURRENCY_SCENARIO_SPECS = [
    ("2K", ["concurrency_test_1_token_2k"], ["concurrency_test_100_token_2k"]),
    ("4K", ["concurrency_test_1_token_4k"], ["concurrency_test_100_token_4k"]),
    (
        "8K",
        ["concurrency_test_1_token_8k", "concurrency_test_1_token"],
        ["concurrency_test_100_token_8k", "concurrency_test_100_token"],
    ),
]

FILE_BURST_SCENARIO_SPECS = [
    ("2K", ["file_burst_1_token_2k"], ["file_burst_100_token_2k"]),
    ("4K", ["file_burst_1_token_4k"], ["file_burst_100_token_4k"]),
    (
        "8K",
        ["file_burst_1_token_8k", "file_burst_1_token"],
        ["file_burst_100_token_8k", "file_burst_100_token"],
    ),
]

E2E_SCENARIO_SPECS = [
    ("2K", "e2e_latency_1_token_2k", "e2e_latency_100_token_2k"),
    ("4K", "e2e_latency_1_token_4k", "e2e_latency_100_token_4k"),
    ("8K", "e2e_latency_1_token_8k", "e2e_latency_100_token_8k"),
]

VISION_TIER_HATCHES = {"2K": "", "4K": "\\\\", "8K": ".."}

EA_COLORS = {
    "ea1": "#E74C3C",
    "ea2": "#2E86C1",
    "ea3": "#27AE60",
    "ea4": "#8E44AD",
}

# Display names for platforms in chart labels
PLATFORM_DISPLAY = {
    "h100": "H100",
    "rtx_pro": "RTX Pro",
    "thor": "Thor",
    "spark": "Spark",
    "l40s": "L40s",
    "b200": "B200",
    "igx_thor": "IGX Thor",
    "agx_thor": "AGX Thor",
    "dgx_spark": "DGX Spark",
}

# Video duration mapping (directory name substring -> seconds)
VIDEO_DURATIONS = {
    "warehouse_10s": 10,
    "warehouse_10min": 600,
    "warehouse_60min": 3600,
}

# Video duration display labels
VIDEO_DURATION_LABELS = {
    "warehouse_10s": "10s",
    "warehouse_10min": "10min",
    "warehouse_60min": "60min",
}

# Scenario-to-tier mapping for determining 2K vs 8K
SCENARIO_2K_PREFIXES = [
    "max_live_streams_test_1_token",
    "max_live_streams_test_100_token",
]
SCENARIO_8K_PREFIXES = [
    "max_live_streams_test_1_token_448",
    "max_live_streams_test_100_token_448",
    "concurrency_test_1_token",
    "concurrency_test_100_token",
    "e2e_latency_1_token",
    "e2e_latency_100_token",
]

plt.rcParams.update(
    {
        "figure.facecolor": "white",
        "axes.facecolor": "white",
        "axes.grid": True,
        "grid.alpha": 0.3,
        "font.size": 10,
    }
)


# ── Vision Token Calculation ─────────────────────────────────────────────────


def calc_vision_tokens(w: int, h: int, num_frames: int) -> int:
    """Calculate vision token count from resolution and frame count.

    Formula: ceil(w/32) * ceil(h/32) * (num_frames // 2)
    """
    return math.ceil(w / 32) * math.ceil(h / 32) * (num_frames // 2)


def tokens_label(tokens: int) -> str:
    """Return a human-readable token label like '~2K' or '~8K'."""
    k = round(tokens / 1000)
    return f"~{k}K"


# ── YAML Config Reading ─────────────────────────────────────────────────────


def load_yaml(path: Path) -> Optional[Dict]:
    """Load a YAML file, return None on error."""
    if yaml is None:
        print(f"  Warning: PyYAML not installed, cannot read config: {path}")
        return None
    try:
        with open(path) as f:
            return yaml.safe_load(f)
    except Exception as e:
        print(f"  Warning: Failed to load YAML {path}: {e}")
        return None


def _deep_merge(base: Dict, override: Dict) -> Dict:
    """Recursively merge override into base, returning a new dict."""
    result = dict(base)
    for k, v in override.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = v
    return result


def resolve_scenario_params(config: Dict, scenario_name: str) -> Dict[str, Any]:
    """Resolve generate_captions_params for a scenario using config merge order.

    Merge order: global.generate_captions_params -> scenario.generate_captions_params
    -> video[0].generate_captions_params
    """
    global_params = {}
    if "global" in config and "generate_captions_params" in config["global"]:
        global_params = dict(config["global"]["generate_captions_params"])

    scenario = config.get("test_scenarios", {}).get(scenario_name, {})
    if not scenario:
        return global_params

    scenario_params = scenario.get("generate_captions_params", {})
    merged = _deep_merge(global_params, scenario_params)

    # Apply video[0] overrides
    videos = scenario.get("videos", [])
    if videos and isinstance(videos[0], dict):
        video_params = videos[0].get("generate_captions_params", {})
        merged = _deep_merge(merged, video_params)

    return merged


def get_scenario_vision_info(config: Dict, scenario_name: str) -> Dict[str, Any]:
    """Extract resolution, frame count, max_tokens, and computed vision tokens for a scenario."""
    params = resolve_scenario_params(config, scenario_name)
    w = params.get("vlm_input_width", 448)
    h = params.get("vlm_input_height", 448)
    nf = params.get("num_frames_per_second_or_fixed_frames_chunk", 80)
    mt = params.get("max_tokens", 100)
    tokens = calc_vision_tokens(w, h, nf)
    return {
        "width": w,
        "height": h,
        "num_frames": nf,
        "max_tokens": mt,
        "vision_tokens": tokens,
        "vision_tokens_label": tokens_label(tokens),
    }


def find_scenario_dirs(report_dir: Path, prefix: str) -> List[Path]:
    """Find scenario directories matching a prefix exactly (no partial suffix match)."""
    if not report_dir.exists():
        return []
    dirs = []
    for d in sorted(report_dir.iterdir()):
        if d.is_dir() and d.name == prefix:
            dirs.append(d)
    # Fallback: also find dirs that start with prefix followed by underscore-separated suffixes
    # but only if exact match yielded nothing
    if not dirs:
        dirs = sorted([d for d in report_dir.iterdir() if d.is_dir() and d.name.startswith(prefix)])
    return dirs


def find_existing_scenario_dir(report_dir: Path, scenario_names: List[str]) -> Optional[Path]:
    """Return the first existing exact scenario directory from an ordered candidate list."""
    if not report_dir.exists():
        return None
    for scenario_name in scenario_names:
        candidate = report_dir / scenario_name
        if candidate.is_dir():
            return candidate
    return None


def find_test_case_results(scenario_dir: Path, result_filename: str) -> List[Tuple[str, Dict]]:
    """Find all test case result files within a scenario directory."""
    results = []
    for tc_dir in sorted(scenario_dir.iterdir()):
        if not tc_dir.is_dir():
            continue
        result_file = tc_dir / result_filename
        if result_file.exists():
            data = load_json(result_file)
            if data:
                results.append((tc_dir.name, data))
    return results


# ── Max Live Streams Data ────────────────────────────────────────────────────


def get_max_streams_for_scenario(report_dir: Path, scenario_name: str) -> Optional[Dict[str, Any]]:
    """Extract max_live_streams result for a specific scenario.

    Returns: {max_streams, p95, avg_latency, gpu_usage, gpu_memory} or None.
    """
    for scenario_dir in find_scenario_dirs(report_dir, scenario_name):
        for tc_name, data in find_test_case_results(scenario_dir, "max_live_streams_results.json"):
            return {
                "max_streams": data.get("max_sustainable_streams", 0),
                "p95": data.get("last_stable_p95", 0),
                "avg_latency": data.get("last_stable_moving_average_latency", 0),
                "gpu_usage": data.get("vlm_gpu_usage_mean", 0),
                "gpu_memory": data.get("vlm_gpu_memory_mean", 0),
                "test_case_id": tc_name,
            }
    return None


def get_max_streams_for_scenarios(
    report_dir: Path, scenario_names: List[str]
) -> Optional[Dict[str, Any]]:
    """Extract max-live-stream data from the first exact scenario directory present."""
    scenario_dir = find_existing_scenario_dir(report_dir, scenario_names)
    if scenario_dir is None:
        return None
    for tc_name, data in find_test_case_results(scenario_dir, "max_live_streams_results.json"):
        return {
            "max_streams": data.get("max_sustainable_streams", 0),
            "p95": data.get("last_stable_p95", 0),
            "avg_latency": data.get("last_stable_moving_average_latency", 0),
            "gpu_usage": data.get("vlm_gpu_usage_mean", 0),
            "gpu_memory": data.get("vlm_gpu_memory_mean", 0),
            "test_case_id": tc_name,
            "scenario": scenario_dir.name,
        }
    return None


def get_max_streams_by_tier(report_dir: Path) -> Dict[str, Dict[str, Dict[str, Any]]]:
    """Return max-stream data grouped by vision tier and output-token setting."""
    tier_data: Dict[str, Dict[str, Dict[str, Any]]] = {}
    for vision_tier, one_token_scenarios, hundred_token_scenarios in MAX_STREAMS_SCENARIO_SPECS:
        data_1t = get_max_streams_for_scenarios(report_dir, one_token_scenarios)
        data_100t = get_max_streams_for_scenarios(report_dir, hundred_token_scenarios)
        if data_1t or data_100t:
            tier_data[vision_tier] = {}
            if data_1t:
                tier_data[vision_tier]["1t"] = data_1t
            if data_100t:
                tier_data[vision_tier]["100t"] = data_100t
    return tier_data


def get_all_max_streams_results(report_dir: Path) -> Dict[str, Dict]:
    """Extract all max_live_streams results. Returns {scenario_name: {...}}."""
    results = {}
    if not report_dir.exists():
        return results
    for d in sorted(report_dir.iterdir()):
        if d.is_dir() and d.name.startswith("max_live_streams"):
            for tc_name, data in find_test_case_results(d, "max_live_streams_results.json"):
                results[d.name] = {
                    "max_streams": data.get("max_sustainable_streams", 0),
                    "p95": data.get("last_stable_p95", 0),
                    "avg_latency": data.get("last_stable_moving_average_latency", 0),
                    "gpu_usage": data.get("vlm_gpu_usage_mean", 0),
                    "gpu_memory": data.get("vlm_gpu_memory_mean", 0),
                    "test_case_id": tc_name,
                }
    return results


# ── Concurrency Data ─────────────────────────────────────────────────────────


def get_concurrency_data(report_dir: Path, scenario_prefix: str) -> Dict[int, Dict[str, float]]:
    """Extract concurrency latency data from test_case_summary.json files.

    Reads concurrent_live_streams mode results where each test case directory
    corresponds to a specific stream_count.

    Returns {stream_count: {avg_latency, p95_latency, p99_latency}}.
    """
    summary = {}
    for scenario_dir in find_scenario_dirs(report_dir, scenario_prefix):
        for tc_name, tc_data in find_test_case_results(scenario_dir, "test_case_summary.json"):
            if "stream_count" in tc_data:
                sc = tc_data["stream_count"]
                summary[sc] = {
                    "avg_latency": non_negative_latency(tc_data.get("mean_avg_latency", 0)),
                    "p95_latency": non_negative_latency(tc_data.get("mean_p95_latency", 0)),
                    "p99_latency": non_negative_latency(tc_data.get("mean_p99_latency", 0)),
                }
    return dict(sorted(summary.items()))


def get_concurrency_data_for_scenarios(
    report_dir: Path, scenario_names: List[str]
) -> Dict[int, Dict[str, float]]:
    """Extract concurrency data from the first exact scenario directory present."""
    scenario_dir = find_existing_scenario_dir(report_dir, scenario_names)
    if scenario_dir is None:
        return {}

    summary = {}
    for tc_name, tc_data in find_test_case_results(scenario_dir, "test_case_summary.json"):
        if "stream_count" in tc_data:
            sc = tc_data["stream_count"]
            summary[sc] = {
                "avg_latency": non_negative_latency(tc_data.get("mean_avg_latency", 0)),
                "p95_latency": non_negative_latency(tc_data.get("mean_p95_latency", 0)),
                "p99_latency": non_negative_latency(tc_data.get("mean_p99_latency", 0)),
            }
    return dict(sorted(summary.items()))


def get_concurrency_data_by_tier(report_dir: Path) -> Dict[str, Dict[str, Dict[int, Dict]]]:
    """Return live concurrency data grouped by vision tier and output-token setting."""
    tier_data: Dict[str, Dict[str, Dict[int, Dict]]] = {}
    for vision_tier, one_token_scenarios, hundred_token_scenarios in CONCURRENCY_SCENARIO_SPECS:
        data_1t = get_concurrency_data_for_scenarios(report_dir, one_token_scenarios)
        data_100t = get_concurrency_data_for_scenarios(report_dir, hundred_token_scenarios)
        if data_1t or data_100t:
            tier_data[vision_tier] = {"1t": data_1t, "100t": data_100t}
    return tier_data


# ── E2E Latency / File Burst Data ────────────────────────────────────────────


def get_e2e_latency_by_duration(
    report_dir: Path, scenario_prefix: str
) -> Dict[str, Dict[str, float]]:
    """Extract E2E latency at concurrency=1 for different video durations.

    Looks in e2e_latency_* scenarios for test_case_summary.json files.
    Identifies video duration by directory name substrings (warehouse_10s, warehouse_10min,
    warehouse_60min).

    Returns {duration_key: {avg_latency, p95_latency, throughput}}.
    """
    results = {}
    for scenario_dir in find_scenario_dirs(report_dir, scenario_prefix):
        for tc_name, tc_data in find_test_case_results(scenario_dir, "test_case_summary.json"):
            # Identify video duration from test case directory name
            dur_key = None
            for vd_key in VIDEO_DURATIONS:
                if vd_key in tc_name:
                    dur_key = vd_key
                    break
            if dur_key is None:
                continue

            # Try concurrency_summary first for averaged data at c=1
            if "concurrency_summary" in tc_data and "1" in tc_data["concurrency_summary"]:
                cs = tc_data["concurrency_summary"]["1"]
                results[dur_key] = {
                    "avg_latency": cs.get("mean_avg_latency", 0),
                    "p95_latency": cs.get("mean_p95_latency", 0),
                    "throughput": cs.get("mean_throughput", 0),
                }
            else:
                # Fallback: extract from first iteration's concurrency_results at c=1
                iters = tc_data.get("iteration_results", [])
                if iters:
                    lats = []
                    for it in iters:
                        cr = it.get("concurrency_results", [])
                        c1 = [r for r in cr if r.get("concurrency_level") == 1]
                        if c1:
                            lats.append(
                                c1[0].get("avg_latency", c1[0].get("e2e_latency_seconds", 0))
                            )
                    if lats:
                        results[dur_key] = {
                            "avg_latency": round(float(np.mean(lats)), 2),
                            "p95_latency": 0,
                            "throughput": 0,
                        }
    return results


def get_e2e_latency_by_tier(
    report_dir: Path,
) -> Dict[str, Dict[str, Dict[str, Dict[str, float]]]]:
    """Return E2E duration data grouped by vision tier and output token setting."""
    tier_data: Dict[str, Dict[str, Dict[str, Dict[str, float]]]] = {}
    for vision_tier, one_token_scenario, hundred_token_scenario in E2E_SCENARIO_SPECS:
        data_1t = get_e2e_latency_by_duration(report_dir, one_token_scenario)
        data_100t = get_e2e_latency_by_duration(report_dir, hundred_token_scenario)
        if data_1t or data_100t:
            tier_data[vision_tier] = {"1t": data_1t, "100t": data_100t}

    if tier_data:
        return tier_data

    # Legacy configs used unsuffixed E2E scenario names. Keep them readable.
    data_1t = get_e2e_latency_by_duration(report_dir, "e2e_latency_1_token")
    data_100t = get_e2e_latency_by_duration(report_dir, "e2e_latency_100_token")
    if data_1t or data_100t:
        return {"8K": {"1t": data_1t, "100t": data_100t}}

    return {}


# ── EA Baseline Data ─────────────────────────────────────────────────────────


def load_ea_baseline(path: Path) -> Optional[Dict]:
    """Load an EA baseline JSON file."""
    return load_json(path)


# ── Display Helpers ──────────────────────────────────────────────────────────


def platform_display(name: str) -> str:
    """Get display name for a platform."""
    return PLATFORM_DISPLAY.get(name.lower(), name.upper().replace("_", " "))


def platform_color(name: str) -> str:
    """Get color for a platform."""
    return PLATFORM_COLORS.get(name.lower(), "#999999")


def non_negative_latency(value: Any) -> float:
    """Clamp impossible negative latency values caused by timestamp clock skew."""
    try:
        return max(float(value or 0), 0.0)
    except (TypeError, ValueError):
        return 0.0


def _bar_label(ax, bar, text: str, fontsize: int = 9, offset: float = 0.5, bold: bool = True):
    """Add a text label above a bar."""
    ax.text(
        bar.get_x() + bar.get_width() / 2,
        bar.get_height() + offset,
        text,
        ha="center",
        va="bottom",
        fontsize=fontsize,
        fontweight="bold" if bold else "normal",
    )


# ── Annotation helper ────────────────────────────────────────────────────────

HIGHER_BETTER = "▲ Higher is better"
LOWER_BETTER = "▼ Lower is better"


def annotate_better(ax, text: str):
    """Add a 'Higher/Lower is better' annotation near the y-axis label."""
    color = "#2E7D32" if "Higher" in text else "#C62828"
    ax.annotate(
        text,
        xy=(0, 1),
        xycoords="axes fraction",
        xytext=(4, 4),
        textcoords="offset points",
        fontsize=8,
        color=color,
        fontstyle="italic",
        va="bottom",
        ha="left",
    )


# ── Chart: Max Streams 2K ───────────────────────────────────────────────────


def plot_max_streams_2k(
    reports: Dict[str, Path],
    configs: Dict[str, Dict],
    output_dir: Path,
):
    """Bar chart of max sustainable streams at 2K vision tokens.

    Two subplots: 1-Token and 100-Token output.
    """
    # Compute token info from first available config
    token_info = "~2K Vision Tokens"
    for name, cfg in configs.items():
        info = get_scenario_vision_info(cfg, "max_live_streams_test_1_token_2k")
        token_info = (
            f"{info['width']}x{info['height']}, {info['num_frames']} frm, "
            f"{info['vision_tokens_label']} Vision Tokens"
        )
        break

    data_1t = {}
    data_100t = {}

    for name, report_dir in reports.items():
        result = get_max_streams_for_scenarios(
            report_dir, ["max_live_streams_test_1_token_2k", "max_live_streams_test_1_token"]
        )
        if result:
            data_1t[name] = result
        result = get_max_streams_for_scenarios(
            report_dir,
            ["max_live_streams_test_100_token_2k", "max_live_streams_test_100_token"],
        )
        if result:
            data_100t[name] = result

    if not data_1t and not data_100t:
        print("  No max_live_streams 2K data found.")
        return

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 6))
    fig.suptitle(
        f"Max Live Streams ({token_info})",
        fontsize=14,
        fontweight="bold",
    )

    def _plot(ax, data: Dict[str, Dict], title: str):
        if not data:
            ax.set_visible(False)
            return
        labels = [platform_display(n) for n in data]
        values = [d["max_streams"] for d in data.values()]
        p95s = [d["p95"] for d in data.values()]
        colors = [platform_color(n) for n in data]
        bars = ax.bar(labels, values, color=colors, width=0.6, edgecolor="white", linewidth=0.5)
        for bar, val, p95 in zip(bars, values, p95s):
            _bar_label(ax, bar, f"{val}\np95={p95:.1f}s", offset=max(values) * 0.01)
        ax.set_title(title, fontsize=11)
        ax.set_ylabel("Max Concurrent Streams")
        annotate_better(ax, HIGHER_BETTER)
        ax.set_ylim(0, max(values) * 1.3 if values else 10)

    _plot(ax1, data_1t, "1-Token Output")
    _plot(ax2, data_100t, "100-Token Output")

    plt.tight_layout(rect=[0, 0, 1, 0.93])
    out = output_dir / "max_streams_2k.png"
    plt.savefig(out, dpi=150, bbox_inches="tight")
    print(f"  Saved: {out}")
    plt.close()


# ── Chart: Max Streams 8K ───────────────────────────────────────────────────


def plot_max_streams_8k(
    reports: Dict[str, Path],
    configs: Dict[str, Dict],
    output_dir: Path,
):
    """Bar chart of max sustainable streams at 8K vision tokens."""
    token_info = "~8K Vision Tokens"
    for name, cfg in configs.items():
        info = get_scenario_vision_info(cfg, "max_live_streams_test_1_token_8k")
        token_info = (
            f"{info['width']}x{info['height']}, {info['num_frames']} frm, "
            f"{info['vision_tokens_label']} Vision Tokens"
        )
        break

    data_1t = {}
    data_100t = {}

    for name, report_dir in reports.items():
        result = get_max_streams_for_scenarios(
            report_dir,
            ["max_live_streams_test_1_token_8k", "max_live_streams_test_1_token_448"],
        )
        if result:
            data_1t[name] = result
        result = get_max_streams_for_scenarios(
            report_dir,
            ["max_live_streams_test_100_token_8k", "max_live_streams_test_100_token_448"],
        )
        if result:
            data_100t[name] = result

    if not data_1t and not data_100t:
        print("  No max_live_streams 8K data found.")
        return

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 6))
    fig.suptitle(
        f"Max Live Streams ({token_info})",
        fontsize=14,
        fontweight="bold",
    )

    def _plot(ax, data: Dict[str, Dict], title: str):
        if not data:
            ax.set_visible(False)
            return
        labels = [platform_display(n) for n in data]
        values = [d["max_streams"] for d in data.values()]
        p95s = [d["p95"] for d in data.values()]
        colors = [platform_color(n) for n in data]
        bars = ax.bar(labels, values, color=colors, width=0.6, edgecolor="white", linewidth=0.5)
        for bar, val, p95 in zip(bars, values, p95s):
            _bar_label(ax, bar, f"{val}\np95={p95:.1f}s", offset=max(values) * 0.01)
        ax.set_title(title, fontsize=11)
        ax.set_ylabel("Max Concurrent Streams")
        annotate_better(ax, HIGHER_BETTER)
        ax.set_ylim(0, max(values) * 1.3 if values else 10)

    _plot(ax1, data_1t, "1-Token Output")
    _plot(ax2, data_100t, "100-Token Output")

    plt.tight_layout(rect=[0, 0, 1, 0.93])
    out = output_dir / "max_streams_8k.png"
    plt.savefig(out, dpi=150, bbox_inches="tight")
    print(f"  Saved: {out}")
    plt.close()


# ── Chart: Max Streams 2K vs 8K ─────────────────────────────────────────────


def plot_max_streams_2k_vs_8k(
    reports: Dict[str, Path],
    configs: Dict[str, Dict],
    output_dir: Path,
):
    """Side-by-side grouped bars showing max streams by vision tier per platform.

    Includes yellow multiplier badges showing the 2K/8K ratio when both tiers exist.
    """
    all_data: Dict[str, Dict[str, Dict[str, Dict[str, Any]]]] = {}
    for name, report_dir in reports.items():
        tier_data = get_max_streams_by_tier(report_dir)
        if tier_data:
            all_data[name] = tier_data

    if not all_data:
        print("  No max-stream tier comparison data found.")
        return

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 6))
    fig.suptitle(
        "Max Live Streams by Vision Token Tier",
        fontsize=14,
        fontweight="bold",
    )

    def _plot_comparison(ax, token_tier: str, title: str):
        plats = [
            platform
            for platform, tier_data in all_data.items()
            if any(token_tier in tier_data.get(vision_tier, {}) for vision_tier in VISION_TIERS)
        ]
        if not plats:
            ax.set_visible(False)
            return

        x = np.arange(len(plats))
        active_tiers = [
            vision_tier
            for vision_tier in VISION_TIERS
            if any(token_tier in all_data[p].get(vision_tier, {}) for p in plats)
        ]
        width = 0.8 / max(len(active_tiers), 1)
        tier_colors = {"2K": "#5B7DB1", "4K": "#8CB150", "8K": "#E67E22"}
        all_vals = []

        for i, vision_tier in enumerate(active_tiers):
            vals = [
                all_data[p].get(vision_tier, {}).get(token_tier, {}).get("max_streams", 0)
                for p in plats
            ]
            all_vals.extend(vals)
            offset = (i - len(active_tiers) / 2 + 0.5) * width
            bars = ax.bar(
                x + offset,
                vals,
                width,
                label=f"~{vision_tier} Tokens",
                color=tier_colors.get(vision_tier, "#999999"),
                edgecolor="white",
            )
            max_for_offset = max(vals, default=0)
            for bar, val in zip(bars, vals):
                if val > 0:
                    _bar_label(ax, bar, str(val), fontsize=9, offset=max_for_offset * 0.01)

        max_val = max(all_vals, default=0)

        # Multiplier badges
        for i, p in enumerate(plats):
            v2k = all_data[p].get("2K", {}).get(token_tier, {}).get("max_streams", 0)
            v8k = all_data[p].get("8K", {}).get(token_tier, {}).get("max_streams", 0)
            if v8k > 0 and v2k > 0:
                ratio = v2k / v8k
                badge_y = max(v2k, v8k) + max_val * 0.12
                ax.annotate(
                    f"{ratio:.1f}x",
                    xy=(x[i], badge_y),
                    ha="center",
                    va="bottom",
                    fontsize=9,
                    fontweight="bold",
                    color="#856404",
                    bbox=dict(
                        boxstyle="round,pad=0.3",
                        facecolor="#FFF3CD",
                        edgecolor="#FFC107",
                        linewidth=1.5,
                    ),
                )

        ax.set_title(title, fontsize=11)
        ax.set_ylabel("Max Concurrent Streams")
        annotate_better(ax, HIGHER_BETTER)
        ax.set_xticks(x)
        ax.set_xticklabels([platform_display(p) for p in plats])
        ax.set_ylim(0, max_val * 1.45 if max_val > 0 else 10)
        ax.legend(fontsize=9)

    _plot_comparison(ax1, "1t", "1-Token Output")
    _plot_comparison(ax2, "100t", "100-Token Output")

    plt.tight_layout(rect=[0, 0, 1, 0.93])
    out = output_dir / "max_streams_2k_vs_8k.png"
    plt.savefig(out, dpi=150, bbox_inches="tight")
    print(f"  Saved: {out}")
    plt.close()


# ── Chart: Concurrency ──────────────────────────────────────────────────────


def plot_concurrency(
    reports: Dict[str, Path],
    configs: Dict[str, Dict],
    output_dir: Path,
):
    """Live concurrent streams chunk latency by BCD vision-token tier.

    Generates one chart per available tier: concurrency_2k.png, concurrency_4k.png,
    and concurrency_8k.png.
    """
    high_perf = ["h100", "rtx_pro", "b200", "l40s"]
    edge = ["thor", "spark", "igx_thor", "agx_thor", "dgx_spark"]

    all_data: Dict[str, Dict[str, Dict[str, Dict[int, Dict]]]] = {}
    for name, report_dir in reports.items():
        tier_data = get_concurrency_data_by_tier(report_dir)
        if tier_data:
            all_data[name] = tier_data

    if not all_data:
        print("  No concurrency data found.")
        return

    combo_colors = {
        "1t": {"alpha": 1.0, "hatch": ""},
        "100t": {"alpha": 0.75, "hatch": "//"},
    }

    def _plot_concurrency_panel(
        ax,
        tier_data: Dict[str, Dict[str, Dict[int, Dict]]],
        platforms: List[str],
        stream_counts: List[int],
        title: str,
    ):
        if not platforms or not stream_counts:
            ax.set_visible(False)
            return

        # Build groups: each stream_count is a group, within which we have
        # platform*token bars
        combos = []
        for p in platforms:
            if p in tier_data:
                for tk in ["1t", "100t"]:
                    if tier_data[p].get(tk):
                        combos.append((p, tk))

        if not combos:
            ax.set_visible(False)
            return

        n_groups = len(stream_counts)
        n_bars = len(combos)
        width = 0.8 / max(n_bars, 1)
        x = np.arange(n_groups)

        for i, (p, tk) in enumerate(combos):
            data = tier_data[p][tk]
            avg_vals = []
            p95_vals = []
            for sc in stream_counts:
                if sc in data:
                    avg_vals.append(data[sc]["avg_latency"])
                    p95_vals.append(data[sc]["p95_latency"])
                else:
                    avg_vals.append(0)
                    p95_vals.append(0)

            offset = (i - n_bars / 2 + 0.5) * width
            color = platform_color(p)
            alpha = combo_colors[tk]["alpha"]
            hatch = combo_colors[tk]["hatch"]
            label = f"{platform_display(p)} {tk.upper()}"

            # Error bars: show distance from avg to p95
            yerr = [max(0, p95 - avg) for avg, p95 in zip(avg_vals, p95_vals)]

            bars = ax.bar(
                x + offset,
                avg_vals,
                width,
                label=label,
                color=color,
                alpha=alpha,
                hatch=hatch,
                edgecolor="white",
                linewidth=0.5,
                yerr=yerr,
                error_kw=dict(elinewidth=1.5, capsize=3, capthick=1.2, ecolor="#333333"),
            )

            # Label bars with avg value
            for bar, avg, p95 in zip(bars, avg_vals, p95_vals):
                if avg > 0:
                    ax.text(
                        bar.get_x() + bar.get_width() / 2,
                        avg + (p95 - avg) + 0.05,
                        f"{avg:.2f}",
                        ha="center",
                        va="bottom",
                        fontsize=7,
                        fontweight="bold",
                    )

        ax.set_title(title, fontsize=11)
        ax.set_xlabel("Stream Count")
        ax.set_ylabel("Chunk Latency (s)")
        annotate_better(ax, LOWER_BETTER)
        ax.set_xticks(x)
        ax.set_xticklabels([str(s) for s in stream_counts])
        ax.legend(fontsize=7, loc="upper left")

    generated = False
    for vision_tier in VISION_TIERS:
        tier_data = {
            platform: platform_tiers[vision_tier]
            for platform, platform_tiers in all_data.items()
            if vision_tier in platform_tiers
        }
        if not tier_data:
            continue

        left_platforms = [n for n in reports if n.lower() in high_perf and n in tier_data]
        right_platforms = [n for n in reports if n.lower() in edge and n in tier_data]

        if not left_platforms and not right_platforms:
            names = list(tier_data.keys())
            mid = max(1, len(names) // 2)
            left_platforms = names[:mid]
            right_platforms = names[mid:]

        left_streams = set()
        right_streams = set()
        for name in left_platforms:
            for tk in ["1t", "100t"]:
                left_streams.update(tier_data[name].get(tk, {}).keys())
        for name in right_platforms:
            for tk in ["1t", "100t"]:
                right_streams.update(tier_data[name].get(tk, {}).keys())

        left_streams = sorted(left_streams)
        right_streams = sorted(right_streams)

        fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(16, 7))
        fig.suptitle(
            f"Live Concurrent Streams \u2014 Chunk Latency ({vision_tier})",
            fontsize=14,
            fontweight="bold",
        )

        left_title = (
            f"{' & '.join(platform_display(p) for p in left_platforms)} "
            f"(streams: {', '.join(str(s) for s in left_streams)})"
            if left_platforms
            else ""
        )
        right_title = (
            f"{' & '.join(platform_display(p) for p in right_platforms)} "
            f"(streams: {', '.join(str(s) for s in right_streams)})"
            if right_platforms
            else ""
        )

        _plot_concurrency_panel(ax1, tier_data, left_platforms, left_streams, left_title)
        _plot_concurrency_panel(ax2, tier_data, right_platforms, right_streams, right_title)

        plt.tight_layout(rect=[0, 0, 1, 0.93])
        out = output_dir / f"concurrency_{vision_tier.lower()}.png"
        plt.savefig(out, dpi=150, bbox_inches="tight")
        print(f"  Saved: {out}")
        plt.close()
        generated = True

    if not generated:
        print("  No concurrency tier data found.")


# ── Chart: E2E Latency by Video Duration ────────────────────────────────────


def plot_e2e_latency(
    reports: Dict[str, Path],
    configs: Dict[str, Dict],
    output_dir: Path,
):
    """Grouped bar chart of E2E file latency by video duration (10s, 10min, 60min).

    All platforms, 2K/4K/8K tiers, 1T + 100T combined. Concurrency=1.
    """
    # Collect data: {platform: {vision_tier: {token_tier: {dur_key: {...}}}}}
    all_data: Dict[str, Dict[str, Dict[str, Dict[str, Dict[str, float]]]]] = {}
    for name, report_dir in reports.items():
        platform_data = get_e2e_latency_by_tier(report_dir)
        if platform_data:
            all_data[name] = platform_data

    if not all_data:
        print("  No E2E latency data found.")
        return

    # Determine which durations are available
    dur_keys_all = set()
    for platform_data in all_data.values():
        for token_data_by_tier in platform_data.values():
            for dur_data in token_data_by_tier.values():
                dur_keys_all.update(dur_data.keys())
    dur_keys = sorted(dur_keys_all, key=lambda k: VIDEO_DURATIONS.get(k, 0))

    if not dur_keys:
        print("  No E2E latency duration data found.")
        return

    combos = []
    for platform in sorted(all_data.keys()):
        for vision_tier in ["2K", "4K", "8K"]:
            tier_data = all_data[platform].get(vision_tier, {})
            for token_tier in ["1t", "100t"]:
                if tier_data.get(token_tier):
                    combos.append((platform, vision_tier, token_tier))

    n_groups = len(dur_keys)
    n_bars = len(combos)
    width = 0.8 / max(n_bars, 1)
    x = np.arange(n_groups)

    fig, ax = plt.subplots(figsize=(16, 7))

    for i, (platform, vision_tier, token_tier) in enumerate(combos):
        vals = []
        for dk in dur_keys:
            d = all_data[platform][vision_tier][token_tier].get(dk, {})
            vals.append(d.get("avg_latency", 0))

        offset = (i - n_bars / 2 + 0.5) * width
        color = platform_color(platform)
        alpha = 1.0 if token_tier == "1t" else 0.75
        hatch = VISION_TIER_HATCHES.get(vision_tier, "")
        if token_tier == "100t":
            hatch += "//"
        label = f"{platform_display(platform)} {vision_tier} {token_tier.upper()}"

        bars = ax.bar(
            x + offset,
            vals,
            width,
            label=label,
            color=color,
            alpha=alpha,
            hatch=hatch,
            edgecolor="white",
            linewidth=0.5,
        )

        for bar, val in zip(bars, vals):
            if val > 0:
                ax.text(
                    bar.get_x() + bar.get_width() / 2,
                    bar.get_height() + 0.3,
                    f"{val:.1f}s",
                    ha="center",
                    va="bottom",
                    fontsize=7,
                    fontweight="bold",
                    rotation=45 if val > 100 else 0,
                )

    ax.set_title(
        "E2E File Latency by Video Duration (2K/4K/8K when available)",
        fontsize=13,
        fontweight="bold",
    )
    ax.set_xlabel("Video Duration")
    ax.set_ylabel("E2E Latency (s)")
    annotate_better(ax, LOWER_BETTER)
    ax.set_xticks(x)
    ax.set_xticklabels([VIDEO_DURATION_LABELS.get(dk, dk) for dk in dur_keys])
    ax.legend(fontsize=8, bbox_to_anchor=(1.02, 1), loc="upper left")
    ax.set_yscale("log")
    ax.set_ylim(bottom=0.1)

    plt.tight_layout()
    out = output_dir / "e2e_latency_by_duration.png"
    plt.savefig(out, dpi=150, bbox_inches="tight")
    print(f"  Saved: {out}")
    plt.close()


# ── Chart: Processing Speed ─────────────────────────────────────────────────


def plot_processing_speed(
    reports: Dict[str, Path],
    configs: Dict[str, Dict],
    output_dir: Path,
):
    """Processing speed as x-realtime multiplier: video_duration / e2e_latency.

    Same data source as e2e_latency, recomputed as speed multiplier.
    """
    all_data: Dict[str, Dict[str, Dict[str, Dict[str, Dict[str, float]]]]] = {}
    for name, report_dir in reports.items():
        platform_data = get_e2e_latency_by_tier(report_dir)
        if platform_data:
            all_data[name] = platform_data

    if not all_data:
        print("  No processing speed data found.")
        return

    dur_keys_all = set()
    for platform_data in all_data.values():
        for token_data_by_tier in platform_data.values():
            for dur_data in token_data_by_tier.values():
                dur_keys_all.update(dur_data.keys())
    dur_keys = sorted(dur_keys_all, key=lambda k: VIDEO_DURATIONS.get(k, 0))

    if not dur_keys:
        print("  No processing speed duration data found.")
        return

    combos = []
    for platform in sorted(all_data.keys()):
        for vision_tier in ["2K", "4K", "8K"]:
            tier_data = all_data[platform].get(vision_tier, {})
            for token_tier in ["1t", "100t"]:
                if tier_data.get(token_tier):
                    combos.append((platform, vision_tier, token_tier))

    n_groups = len(dur_keys)
    n_bars = len(combos)
    width = 0.8 / max(n_bars, 1)
    x = np.arange(n_groups)

    fig, ax = plt.subplots(figsize=(16, 7))

    for i, (platform, vision_tier, token_tier) in enumerate(combos):
        speeds = []
        for dk in dur_keys:
            d = all_data[platform][vision_tier][token_tier].get(dk, {})
            lat = d.get("avg_latency", 0)
            dur_sec = VIDEO_DURATIONS.get(dk, 0)
            if lat > 0 and dur_sec > 0:
                speeds.append(round(dur_sec / lat, 2))
            else:
                speeds.append(0)

        offset = (i - n_bars / 2 + 0.5) * width
        color = platform_color(platform)
        alpha = 1.0 if token_tier == "1t" else 0.75
        hatch = VISION_TIER_HATCHES.get(vision_tier, "")
        if token_tier == "100t":
            hatch += "//"
        label = f"{platform_display(platform)} {vision_tier} {token_tier.upper()}"

        bars = ax.bar(
            x + offset,
            speeds,
            width,
            label=label,
            color=color,
            alpha=alpha,
            hatch=hatch,
            edgecolor="white",
            linewidth=0.5,
        )

        for bar, spd in zip(bars, speeds):
            if spd > 0:
                ax.text(
                    bar.get_x() + bar.get_width() / 2,
                    bar.get_height() + 0.1,
                    f"{spd:.1f}x",
                    ha="center",
                    va="bottom",
                    fontsize=7,
                    fontweight="bold",
                )

    # Add 1x realtime reference line
    ax.axhline(
        y=1.0, color="#E74C3C", linestyle="--", linewidth=1.5, alpha=0.7, label="1x Realtime"
    )

    ax.set_title(
        "Processing Speed (x Realtime, 2K/4K/8K when available)",
        fontsize=13,
        fontweight="bold",
    )
    ax.set_xlabel("Video Duration")
    ax.set_ylabel("Speed (x Realtime)")
    annotate_better(ax, HIGHER_BETTER)
    ax.set_xticks(x)
    ax.set_xticklabels([VIDEO_DURATION_LABELS.get(dk, dk) for dk in dur_keys])
    ax.legend(fontsize=8, bbox_to_anchor=(1.02, 1), loc="upper left")

    plt.tight_layout()
    out = output_dir / "processing_speed.png"
    plt.savefig(out, dpi=150, bbox_inches="tight")
    print(f"  Saved: {out}")
    plt.close()


# ── Chart: EA Comparison (Concurrency Latency) ──────────────────────────────


def plot_ea_comparison(
    ea_baselines: Dict[str, Dict],
    output_dir: Path,
):
    """EA1 vs EA2 (or more): Concurrent streams latency comparison (~2K Vision Tokens).

    Two subplots: 1T and 100T. Grouped bars per stream count with platform+EA pairs.
    Only platforms present in ALL EA releases are shown. Bar colors use PLATFORM_COLORS;
    EA1 = solid fill, EA2 = hatched ('//').
    """
    if not ea_baselines:
        print("  No EA baseline data provided.")
        return

    # Get vision token info from first baseline metadata
    token_label = "~2K Vision Tokens"
    for label, data in ea_baselines.items():
        meta = data.get("_metadata", {})
        tl = meta.get("vision_tokens_label", "")
        if tl:
            token_label = f"{tl} Vision Tokens"
        break

    ea_labels_sorted = list(ea_baselines.keys())

    # Find platforms present in ALL EA releases (concurrency_latency section)
    platform_sets = []
    for ea_label in ea_labels_sorted:
        cl = ea_baselines[ea_label].get("concurrency_latency", {})
        platform_sets.append({p for p in cl if p != "_header"})
    common_platforms = sorted(set.intersection(*platform_sets)) if platform_sets else []

    if not common_platforms:
        print("  No EA concurrency latency data found for platforms common to all EA releases.")
        return

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(16, 7))

    fig.suptitle(
        f"{' vs '.join(lbl.upper() for lbl in ea_labels_sorted)}: "
        f"Concurrent Streams Latency ({token_label})",
        fontsize=14,
        fontweight="bold",
    )

    def _plot_ea_concurrency(ax, token_key: str, title: str):
        # Collect all stream counts across EA versions for common platforms
        all_stream_counts = set()
        for ea_label in ea_labels_sorted:
            cl = ea_baselines[ea_label].get("concurrency_latency", {})
            for plat in common_platforms:
                tier_data = cl.get(plat, {}).get(token_key, [])
                for entry in tier_data:
                    all_stream_counts.add(entry.get("streams", 0))

        stream_counts = sorted(all_stream_counts)
        if not stream_counts:
            ax.set_visible(False)
            return

        # Build combos: platform * EA — order by platform then EA so EA1/EA2 are adjacent
        combos = []
        for plat in common_platforms:
            for ea_label in ea_labels_sorted:
                cl = ea_baselines[ea_label].get("concurrency_latency", {})
                if plat in cl and token_key in cl[plat]:
                    combos.append((ea_label, plat))

        if not combos:
            ax.set_visible(False)
            return

        n_groups = len(stream_counts)
        n_bars = len(combos)
        width = 0.8 / max(n_bars, 1)
        x = np.arange(n_groups)

        for i, (ea_label, plat) in enumerate(combos):
            cl = ea_baselines[ea_label].get("concurrency_latency", {})
            tier_data = cl.get(plat, {}).get(token_key, [])
            data_by_streams = {e["streams"]: e for e in tier_data}

            avg_vals = []
            p95_vals = []
            for sc in stream_counts:
                entry = data_by_streams.get(sc, {})
                avg_vals.append(entry.get("avg_latency", 0))
                p95_vals.append(entry.get("p95_latency", 0))

            offset = (i - n_bars / 2 + 0.5) * width
            color = platform_color(plat)
            ea_idx = ea_labels_sorted.index(ea_label)
            hatch = "//" if ea_idx >= 1 else ""
            alpha = 1.0 if ea_idx == 0 else 0.75
            label_text = f"{platform_display(plat)} {ea_label.upper()}"

            yerr = [max(0, p95 - avg) for avg, p95 in zip(avg_vals, p95_vals)]

            bars = ax.bar(
                x + offset,
                avg_vals,
                width,
                label=label_text,
                color=color,
                alpha=alpha,
                hatch=hatch,
                edgecolor="white",
                linewidth=0.5,
                yerr=yerr,
                error_kw=dict(elinewidth=1.2, capsize=2, capthick=1, ecolor="#333333"),
            )

            for bar, avg in zip(bars, avg_vals):
                if avg > 0:
                    ax.text(
                        bar.get_x() + bar.get_width() / 2,
                        bar.get_height() + 0.05,
                        f"{avg:.2f}",
                        ha="center",
                        va="bottom",
                        fontsize=7,
                        fontweight="bold",
                    )

        # Add speedup % annotations between first and last EA for each platform
        if len(ea_labels_sorted) >= 2:
            first_ea = ea_labels_sorted[0]
            last_ea = ea_labels_sorted[-1]
            for plat in common_platforms:
                first_cl = (
                    ea_baselines[first_ea]
                    .get("concurrency_latency", {})
                    .get(plat, {})
                    .get(token_key, [])
                )
                last_cl = (
                    ea_baselines[last_ea]
                    .get("concurrency_latency", {})
                    .get(plat, {})
                    .get(token_key, [])
                )
                first_by_sc = {e["streams"]: e for e in first_cl}
                last_by_sc = {e["streams"]: e for e in last_cl}

                for j, sc in enumerate(stream_counts):
                    old_avg = first_by_sc.get(sc, {}).get("avg_latency", 0)
                    new_avg = last_by_sc.get(sc, {}).get("avg_latency", 0)
                    if old_avg > 0 and new_avg > 0 and old_avg != new_avg:
                        pct = ((old_avg - new_avg) / old_avg) * 100
                        if abs(pct) > 1:
                            color = "#27AE60" if pct > 0 else "#E74C3C"
                            ax.text(
                                x[j],
                                ax.get_ylim()[1] * 0.95,
                                f"{pct:+.0f}%",
                                ha="center",
                                va="top",
                                fontsize=7,
                                color=color,
                                fontweight="bold",
                            )

        ax.set_title(title, fontsize=11)
        ax.set_xlabel("Stream Count")
        ax.set_ylabel("Chunk Latency (s)")
        annotate_better(ax, LOWER_BETTER)
        ax.set_xticks(x)
        ax.set_xticklabels([str(s) for s in stream_counts])
        ax.legend(fontsize=7, loc="upper left")

    _plot_ea_concurrency(ax1, "1t", "1-Token Output")
    _plot_ea_concurrency(ax2, "100t", "100-Token Output")

    plt.tight_layout(rect=[0, 0, 1, 0.93])
    out = output_dir / "ea_comparison_latency.png"
    plt.savefig(out, dpi=150, bbox_inches="tight")
    print(f"  Saved: {out}")
    plt.close()


# ── Chart: EA Max Streams ───────────────────────────────────────────────────


def plot_ea_max_streams(
    ea_baselines: Dict[str, Dict],
    output_dir: Path,
):
    """EA1 vs EA2 (or more): Max live streams comparison (~2K Vision Tokens).

    Two subplots: 1T and 100T. Grouped bars per platform. Only platforms present
    in ALL EA releases are shown. Bar colors use PLATFORM_COLORS; EA1 = solid fill,
    EA2 = hatched ('//').
    """
    if not ea_baselines:
        print("  No EA baseline data provided.")
        return

    token_label = "~2K Vision Tokens"
    for label, data in ea_baselines.items():
        meta = data.get("_metadata", {})
        tl = meta.get("vision_tokens_label", "")
        if tl:
            token_label = f"{tl} Vision Tokens"
        break

    ea_labels_sorted = list(ea_baselines.keys())

    # Find platforms present in ALL EA releases (max_live_streams section)
    platform_sets = []
    for ea_label in ea_labels_sorted:
        ms = ea_baselines[ea_label].get("max_live_streams", {})
        platform_sets.append({p for p in ms if p != "_header"})
    common_platforms = sorted(set.intersection(*platform_sets)) if platform_sets else []

    if not common_platforms:
        print("  No EA max_live_streams data found for platforms common to all EA releases.")
        return

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(16, 7))
    fig.suptitle(
        f"{' vs '.join(lbl.upper() for lbl in ea_labels_sorted)}: Max Live Streams ({token_label})",
        fontsize=14,
        fontweight="bold",
    )

    def _plot_ea_max(ax, token_key: str, title: str):
        # Filter to common platforms that have data for this token tier in ALL EA releases
        plats = []
        for p in common_platforms:
            has_all = True
            for ea_label in ea_labels_sorted:
                ms = ea_baselines[ea_label].get("max_live_streams", {})
                if p not in ms or token_key not in ms[p]:
                    has_all = False
                    break
            if has_all:
                plats.append(p)

        if not plats:
            ax.set_visible(False)
            return

        n_groups = len(plats)
        n_bars = len(ea_labels_sorted)
        width = 0.8 / max(n_bars, 1)
        x = np.arange(n_groups)

        all_vals = []
        for i, ea_label in enumerate(ea_labels_sorted):
            vals = []
            p95s = []
            for p in plats:
                ms = (
                    ea_baselines[ea_label].get("max_live_streams", {}).get(p, {}).get(token_key, {})
                )
                vals.append(ms.get("max_streams", 0))
                p95s.append(ms.get("p95_latency", 0))

            offset = (i - n_bars / 2 + 0.5) * width
            # Use platform colors — each bar pair shares the platform color,
            # but EA1 is solid and EA2+ is hatched
            hatch = "//" if i >= 1 else ""
            alpha = 1.0 if i == 0 else 0.75

            # Draw one bar per platform with that platform's color
            for j, p in enumerate(plats):
                bar_label = f"{platform_display(p)} {ea_label.upper()}" if j == 0 else None
                bar = ax.bar(
                    x[j] + offset,
                    vals[j],
                    width,
                    label=bar_label,
                    color=platform_color(p),
                    alpha=alpha,
                    hatch=hatch,
                    edgecolor="white",
                    linewidth=0.5,
                )
                if vals[j] > 0:
                    _bar_label(
                        ax,
                        bar[0],
                        f"{vals[j]}\np95={p95s[j]:.1f}s",
                        fontsize=8,
                        offset=max(vals) * 0.01 if vals else 0,
                    )

            all_vals.extend(vals)

        # Build a proper legend with platform+EA entries
        legend_handles = []
        import matplotlib.patches as mpatches

        for p in plats:
            for i, ea_label in enumerate(ea_labels_sorted):
                hatch = "//" if i >= 1 else ""
                alpha = 1.0 if i == 0 else 0.75
                patch = mpatches.Patch(
                    facecolor=platform_color(p),
                    alpha=alpha,
                    hatch=hatch,
                    edgecolor="white",
                    label=f"{platform_display(p)} {ea_label.upper()}",
                )
                legend_handles.append(patch)
        ax.legend(handles=legend_handles, fontsize=8)

        # Improvement percentage annotations
        if len(ea_labels_sorted) >= 2:
            first_ea = ea_labels_sorted[0]
            last_ea = ea_labels_sorted[-1]
            max_val = max(all_vals) if all_vals else 0
            for j, p in enumerate(plats):
                old_ms = (
                    ea_baselines[first_ea].get("max_live_streams", {}).get(p, {}).get(token_key, {})
                )
                new_ms = (
                    ea_baselines[last_ea].get("max_live_streams", {}).get(p, {}).get(token_key, {})
                )
                old_val = old_ms.get("max_streams", 0)
                new_val = new_ms.get("max_streams", 0)
                if old_val > 0 and new_val > 0 and old_val != new_val:
                    pct = ((new_val - old_val) / old_val) * 100
                    color = "#27AE60" if pct > 0 else "#E74C3C"
                    ax.annotate(
                        f"{pct:+.0f}%",
                        xy=(x[j], max_val * 1.2),
                        ha="center",
                        va="bottom",
                        fontsize=9,
                        fontweight="bold",
                        color=color,
                    )

        ax.set_title(title, fontsize=11)
        ax.set_ylabel("Max Concurrent Streams")
        annotate_better(ax, HIGHER_BETTER)
        ax.set_xticks(x)
        ax.set_xticklabels([platform_display(p) for p in plats])
        max_val = max(all_vals) if all_vals else 10
        ax.set_ylim(0, max_val * 1.4)

    _plot_ea_max(ax1, "1t", "1-Token Output")
    _plot_ea_max(ax2, "100t", "100-Token Output")

    plt.tight_layout(rect=[0, 0, 1, 0.93])
    out = output_dir / "ea_comparison_max_streams.png"
    plt.savefig(out, dpi=150, bbox_inches="tight")
    print(f"  Saved: {out}")
    plt.close()


# ── Chart: GPU & NVDEC Utilization vs Streams ────────────────────────────────


def _get_latency_snapshots(report_dir: Path, scenario_name: str) -> Tuple[List[Dict], int]:
    """Extract latency_snapshots and max_sustainable_streams from a max_live_streams scenario.

    Returns (snapshots_list, max_sustainable_streams).
    """
    for scenario_dir in find_scenario_dirs(report_dir, scenario_name):
        for tc_name, data in find_test_case_results(scenario_dir, "max_live_streams_results.json"):
            snapshots = data.get("latency_snapshots", [])
            max_streams = data.get("max_sustainable_streams", 0)
            return snapshots, max_streams
    return [], 0


def _get_latency_snapshots_for_scenarios(
    report_dir: Path, scenario_names: List[str]
) -> Tuple[List[Dict], int]:
    """Extract latency snapshots from the first exact scenario directory present."""
    scenario_dir = find_existing_scenario_dir(report_dir, scenario_names)
    if scenario_dir is None:
        return [], 0
    for tc_name, data in find_test_case_results(scenario_dir, "max_live_streams_results.json"):
        snapshots = data.get("latency_snapshots", [])
        max_streams = data.get("max_sustainable_streams", 0)
        return snapshots, max_streams
    return [], 0


def plot_gpu_util_vs_streams(
    reports: Dict[str, Path],
    configs: Dict[str, Dict],
    output_dir: Path,
):
    """GPU and NVDEC utilization vs concurrent streams by vision tier.

    Two subplots stacked vertically: top = GPU utilization, bottom = NVDEC utilization.
    Lines per platform and tier. Vertical dashed lines mark
    max_sustainable_streams per platform.
    """
    scenario_map = {
        "2K": ["max_live_streams_test_1_token_2k", "max_live_streams_test_1_token"],
        "4K": ["max_live_streams_test_1_token_4k"],
        "8K": ["max_live_streams_test_1_token_8k", "max_live_streams_test_1_token_448"],
    }
    line_styles = {"2K": "-", "4K": "-.", "8K": "--"}

    # Collect data: {platform: {tier: {snapshots, max_streams}}}
    all_data: Dict[str, Dict[str, Dict]] = {}
    for name, report_dir in reports.items():
        for tier, scenario_names in scenario_map.items():
            snapshots, max_streams = _get_latency_snapshots_for_scenarios(
                report_dir, scenario_names
            )
            if snapshots:
                all_data.setdefault(name, {})[tier] = {
                    "snapshots": snapshots,
                    "max_streams": max_streams,
                }

    if not all_data:
        print("  No GPU utilization snapshot data found.")
        return

    fig, (ax_gpu, ax_nvdec) = plt.subplots(2, 1, figsize=(14, 10), sharex=True)
    fig.suptitle(
        "GPU Utilization vs Concurrent Streams by Vision Token Tier",
        fontsize=14,
        fontweight="bold",
    )

    for name in sorted(all_data.keys()):
        color = platform_color(name)
        for tier in VISION_TIERS:
            tier_data = all_data[name].get(tier)
            if not tier_data:
                continue
            snapshots = tier_data["snapshots"]
            max_streams = tier_data["max_streams"]

            # Extract x (stream_count) and y values
            stream_counts = [s.get("stream_count", 0) for s in snapshots]
            gpu_vals = [s.get("gpu_usage_pct", 0) for s in snapshots]
            nvdec_vals = [s.get("nvdec_usage_pct", 0) for s in snapshots]

            if not stream_counts:
                continue

            ls = line_styles[tier]
            label = f"{platform_display(name)} {tier}"

            ax_gpu.plot(
                stream_counts,
                gpu_vals,
                color=color,
                linestyle=ls,
                marker="o",
                markersize=4,
                linewidth=2,
                label=label,
            )
            ax_nvdec.plot(
                stream_counts,
                nvdec_vals,
                color=color,
                linestyle=ls,
                marker="o",
                markersize=4,
                linewidth=2,
                label=label,
            )

            # Mark max_sustainable_streams with vertical dashed line
            if max_streams > 0:
                for ax in (ax_gpu, ax_nvdec):
                    ax.axvline(
                        x=max_streams,
                        color=color,
                        linestyle=":",
                        alpha=0.5,
                        linewidth=1.5,
                    )
                # Annotate on GPU subplot only
                ax_gpu.annotate(
                    f"{platform_display(name)} {tier}\nmax={max_streams}",
                    xy=(max_streams, 0),
                    xycoords=("data", "axes fraction"),
                    xytext=(5, 0.02),
                    textcoords=("offset points", "axes fraction"),
                    fontsize=7,
                    color=color,
                    alpha=0.7,
                    rotation=90,
                    va="bottom",
                )

    ax_gpu.set_ylabel("GPU Utilization (%)")
    ax_gpu.set_ylim(0, 105)
    annotate_better(ax_gpu, HIGHER_BETTER)
    ax_gpu.legend(fontsize=8, bbox_to_anchor=(1.02, 1), loc="upper left")

    ax_nvdec.set_xlabel("Concurrent Streams")
    ax_nvdec.set_ylabel("NVDEC Utilization (%)")
    ax_nvdec.set_ylim(0, 105)
    annotate_better(ax_nvdec, HIGHER_BETTER)
    ax_nvdec.legend(fontsize=8, bbox_to_anchor=(1.02, 1), loc="upper left")

    plt.tight_layout(rect=[0, 0, 0.88, 0.95])
    out = output_dir / "gpu_util_vs_streams.png"
    plt.savefig(out, dpi=150, bbox_inches="tight")
    print(f"  Saved: {out}")
    plt.close()


# ── Chart: Throughput (RPS) ──────────────────────────────────────────────────


def _get_throughput_by_concurrency(
    report_dir: Path, scenario_prefix: str, video_key: str = "warehouse_10s"
) -> Dict[int, float]:
    """Extract mean_throughput at each concurrency level for a specific video.

    Looks in e2e_latency scenarios for test_case_summary.json files containing
    concurrency_summary with mean_throughput.

    Returns {concurrency_level: mean_throughput}.
    """
    results = {}
    for scenario_dir in find_scenario_dirs(report_dir, scenario_prefix):
        for tc_name, tc_data in find_test_case_results(scenario_dir, "test_case_summary.json"):
            if video_key not in tc_name:
                continue
            cs = tc_data.get("concurrency_summary", {})
            for level_str, level_data in cs.items():
                try:
                    level = int(level_str)
                except ValueError:
                    continue
                throughput = level_data.get("mean_throughput", 0)
                if throughput > 0:
                    results[level] = throughput
    return dict(sorted(results.items()))


def _get_throughput_by_concurrency_for_scenarios(
    report_dir: Path, scenario_names: List[str], video_key: str = "warehouse_10s"
) -> Dict[int, float]:
    """Extract mean_throughput from the first exact file-burst scenario present."""
    scenario_dir = find_existing_scenario_dir(report_dir, scenario_names)
    if scenario_dir is None:
        return {}

    results = {}
    for tc_name, tc_data in find_test_case_results(scenario_dir, "test_case_summary.json"):
        if video_key not in tc_name:
            continue
        cs = tc_data.get("concurrency_summary", {})
        for level_str, level_data in cs.items():
            try:
                level = int(level_str)
            except ValueError:
                continue
            throughput = level_data.get("mean_throughput", 0)
            if throughput > 0:
                results[level] = throughput
    return dict(sorted(results.items()))


def plot_throughput(
    reports: Dict[str, Path],
    configs: Dict[str, Dict],
    output_dir: Path,
):
    """Request throughput (files/sec) vs concurrency for BCD file-burst scenarios.

    Lines per platform and vision tier. 1T = solid, 100T = dashed.
    """
    all_data: Dict[str, Dict[str, Dict[str, Dict[int, float]]]] = {}
    for name, report_dir in reports.items():
        platform_data: Dict[str, Dict[str, Dict[int, float]]] = {}
        for vision_tier, one_token_scenarios, hundred_token_scenarios in FILE_BURST_SCENARIO_SPECS:
            data_1t = _get_throughput_by_concurrency_for_scenarios(report_dir, one_token_scenarios)
            data_100t = _get_throughput_by_concurrency_for_scenarios(
                report_dir, hundred_token_scenarios
            )
            if data_1t or data_100t:
                platform_data[vision_tier] = {"1t": data_1t, "100t": data_100t}
        if platform_data:
            all_data[name] = platform_data

    if not all_data:
        print("  No throughput data found.")
        return

    fig, ax = plt.subplots(figsize=(12, 6))

    token_styles = {"1t": "-", "100t": "--"}
    tier_markers = {"2K": "o", "4K": "s", "8K": "^"}

    for name in sorted(all_data.keys()):
        color = platform_color(name)
        for vision_tier in VISION_TIERS:
            tier_data = all_data[name].get(vision_tier, {})
            for tk in ["1t", "100t"]:
                data = tier_data.get(tk, {})
                if not data:
                    continue
                concurrency_levels = sorted(data.keys())
                throughputs = [data[c] for c in concurrency_levels]

                ls = token_styles[tk]
                label = f"{platform_display(name)} {vision_tier} {tk.upper()}"

                ax.plot(
                    concurrency_levels,
                    throughputs,
                    color=color,
                    linestyle=ls,
                    marker=tier_markers.get(vision_tier, "o"),
                    markersize=5,
                    linewidth=2,
                    label=label,
                )

                # Annotate each point with its value
                for c, t in zip(concurrency_levels, throughputs):
                    ax.annotate(
                        f"{t:.2f}",
                        xy=(c, t),
                        xytext=(0, 8),
                        textcoords="offset points",
                        fontsize=7,
                        ha="center",
                        fontweight="bold",
                    )

    ax.set_title(
        "Request Throughput vs Concurrency (file_burst, warehouse 10s)",
        fontsize=13,
        fontweight="bold",
    )
    ax.set_xlabel("Concurrency Level")
    ax.set_ylabel("Throughput (files/sec)")
    annotate_better(ax, HIGHER_BETTER)
    ax.legend(fontsize=8, bbox_to_anchor=(1.02, 1), loc="upper left")

    # Force integer x-ticks
    all_levels = set()
    for platform_data in all_data.values():
        for tier_data in platform_data.values():
            for token_data in tier_data.values():
                all_levels.update(token_data.keys())
    if all_levels:
        ax.set_xticks(sorted(all_levels))

    plt.tight_layout()
    out = output_dir / "throughput_vs_concurrency.png"
    plt.savefig(out, dpi=150, bbox_inches="tight")
    print(f"  Saved: {out}")
    plt.close()


# ── Summary Table ────────────────────────────────────────────────────────────


def print_summary_table(
    reports: Dict[str, Path],
    configs: Dict[str, Dict],
    ea_baselines: Dict[str, Dict],
):
    """Print a comprehensive text summary table of key metrics."""
    print("\n" + "=" * 100)
    print("PERFORMANCE SUMMARY")
    print("=" * 100)

    # Max streams
    has_max_streams = False
    for name, report_dir in reports.items():
        platform_data = get_max_streams_by_tier(report_dir)
        for vision_tier in VISION_TIERS:
            token_data = platform_data.get(vision_tier, {})
            for token_tier in ["1t", "100t"]:
                r = token_data.get(token_tier)
                if not r:
                    continue
                if not has_max_streams:
                    print("\n-- Max Live Streams --")
                    print(
                        f"{'Platform':<12} {'Tier':<6} {'OSL':<6} "
                        f"{'Streams':>8} {'P95':>8} {'GPU%':>8}"
                    )
                    print("-" * 70)
                    has_max_streams = True
                print(
                    f"{platform_display(name):<12} {vision_tier:<6} {token_tier:<6} "
                    f"{r['max_streams']:>8} "
                    f"{r['p95']:>7.1f}s "
                    f"{r['gpu_usage']:>7.1f}%"
                )

    # Concurrency latency
    has_conc = False
    for name, report_dir in reports.items():
        platform_data = get_concurrency_data_by_tier(report_dir)
        for vision_tier in VISION_TIERS:
            token_data = platform_data.get(vision_tier, {})
            for token_tier in ["1t", "100t"]:
                data = token_data.get(token_tier, {})
                if not data:
                    continue
                if not has_conc:
                    print("\n-- Concurrency Latency (avg / p95) --")
                    has_conc = True
                print(f"\n  {platform_display(name)} -- {vision_tier} {token_tier}:")
                for sc, stats in sorted(data.items()):
                    avg = stats.get("avg_latency", 0)
                    p95 = stats.get("p95_latency", 0)
                    print(f"    {sc:>3} streams: avg={avg:.2f}s  p95={p95:.2f}s")

    # E2E latency
    has_e2e = False
    for name, report_dir in reports.items():
        platform_data = get_e2e_latency_by_tier(report_dir)
        for vision_tier in ["2K", "4K", "8K"]:
            token_data = platform_data.get(vision_tier, {})
            for token_tier in ["1t", "100t"]:
                data = token_data.get(token_tier, {})
                if not data:
                    continue
                if not has_e2e:
                    print("\n-- E2E File Latency at c=1 --")
                    has_e2e = True
                print(f"\n  {platform_display(name)} -- {vision_tier} {token_tier}:")
                for dk in sorted(data.keys(), key=lambda k: VIDEO_DURATIONS.get(k, 0)):
                    lat = data[dk]["avg_latency"]
                    dur = VIDEO_DURATIONS.get(dk, 0)
                    speed = round(dur / lat, 1) if lat > 0 else 0
                    print(
                        f"    {VIDEO_DURATION_LABELS.get(dk, dk):>6}: "
                        f"avg={lat:.2f}s  speed={speed:.1f}x realtime"
                    )

    # EA baselines
    if ea_baselines:
        print("\n-- EA Baselines --")
        for ea_label, data in ea_baselines.items():
            meta = data.get("_metadata", {})
            print(
                f"\n  {ea_label.upper()}: {meta.get('model', '?')} "
                f"({meta.get('model_precision', '?')}), "
                f"{meta.get('vision_tokens_label', '?')} tokens, "
                f"date={meta.get('date', '?')}"
            )
            ms = data.get("max_live_streams", {})
            for plat in sorted(ms.keys()):
                if plat == "_header":
                    continue
                for tk in ["1t", "100t"]:
                    tier = ms[plat].get(tk, {})
                    if tier:
                        print(
                            f"    {plat:<12} {tk}: "
                            f"max={tier.get('max_streams', '?')} "
                            f"p95={tier.get('p95_latency', '?')}s"
                        )

    print("\n" + "=" * 100)


# ── CLI Parsing ──────────────────────────────────────────────────────────────


def parse_kv_args(args: Optional[List[str]], label: str) -> Dict[str, str]:
    """Parse NAME=PATH key-value arguments from a list."""
    if not args:
        return {}
    result = {}
    for arg in args:
        if "=" not in arg:
            print(f"Error: {label} argument must be NAME=PATH, got: {arg}")
            sys.exit(1)
        name, path_str = arg.split("=", 1)
        result[name] = path_str
    return result


def parse_reports(report_args: List[str]) -> Dict[str, Path]:
    """Parse name=path report arguments into validated Paths."""
    kv = parse_kv_args(report_args, "--reports")
    reports = {}
    for name, path_str in kv.items():
        p = Path(path_str).expanduser().resolve()
        if not p.exists():
            print(f"Warning: report directory does not exist: {p}")
        reports[name] = p
    return reports


def parse_configs(config_args: Optional[List[str]]) -> Dict[str, Dict]:
    """Parse name=path config arguments, load YAML, return {name: config_dict}."""
    kv = parse_kv_args(config_args, "--configs")
    configs = {}
    for name, path_str in kv.items():
        p = Path(path_str).expanduser().resolve()
        cfg = load_yaml(p)
        if cfg:
            configs[name] = cfg
        else:
            print(f"Warning: Could not load config for {name}: {p}")
    return configs


def parse_ea_baselines(ea_args: Optional[List[str]]) -> Dict[str, Dict]:
    """Parse label=path EA baseline arguments, load JSON."""
    kv = parse_kv_args(ea_args, "--ea-baselines")
    baselines = {}
    for label, path_str in kv.items():
        p = Path(path_str).expanduser().resolve()
        data = load_json(p)
        if data:
            baselines[label] = data
        else:
            print(f"Warning: Could not load EA baseline for {label}: {p}")
    return baselines


# ── Chart Registry ───────────────────────────────────────────────────────────

# Charts that require report data (and optionally configs)
REPORT_CHARTS = {
    "max_streams_2k": plot_max_streams_2k,
    "max_streams_8k": plot_max_streams_8k,
    "max_streams_2k_vs_8k": plot_max_streams_2k_vs_8k,
    "concurrency": plot_concurrency,
    "e2e_latency": plot_e2e_latency,
    "processing_speed": plot_processing_speed,
    "gpu_util": plot_gpu_util_vs_streams,
    "throughput": plot_throughput,
}

# Charts that require EA baseline data
# NOTE: ea_comparison (latency) removed — EA1 used ~2K tokens, EA2 uses ~8K, not comparable.
EA_CHARTS = {
    "ea_max_streams": plot_ea_max_streams,
}

ALL_CHART_TYPES = list(REPORT_CHARTS.keys()) + list(EA_CHARTS.keys()) + ["all", "summary"]


def main():
    parser = argparse.ArgumentParser(
        description="RTVI-VLM Performance Report Chart Generator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "chart_type",
        choices=ALL_CHART_TYPES,
        help="Type of chart to generate (or 'all' for everything, 'summary' for text table)",
    )
    parser.add_argument(
        "--reports",
        nargs="+",
        metavar="NAME=PATH",
        help="Platform reports as NAME=PATH pairs (e.g. h100=/path/to/h100-report)",
    )
    parser.add_argument(
        "--configs",
        nargs="+",
        metavar="NAME=PATH",
        help="Platform YAML configs as NAME=PATH pairs (e.g. h100=rtvi_vlm_config_h100.yaml)",
    )
    parser.add_argument(
        "--ea-baselines",
        nargs="+",
        metavar="LABEL=PATH",
        help="EA baseline JSON files as LABEL=PATH pairs (e.g. ea1=ea_baseline_data/ea1_engsqa.json)",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=".",
        help="Output directory for charts (default: current directory)",
    )

    args = parser.parse_args()

    # Validate required arguments
    chart = args.chart_type
    needs_reports = chart in REPORT_CHARTS or chart in ("all", "summary")
    needs_ea = chart in EA_CHARTS or chart == "all"

    if needs_reports and not args.reports:
        if not needs_ea or not args.ea_baselines:
            parser.error(f"--reports is required for chart type '{chart}'")

    reports = parse_reports(args.reports) if args.reports else {}
    configs = parse_configs(args.configs) if args.configs else {}
    ea_baselines = parse_ea_baselines(getattr(args, "ea_baselines", None))
    output_dir = Path(args.output).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    # If no configs provided, create empty dict stubs so charts can still run
    # with default token info
    if not configs:
        configs = {name: {} for name in reports}

    print(f"Reports: {', '.join(reports.keys()) if reports else '(none)'}")
    print(f"Configs: {', '.join(configs.keys()) if configs else '(none)'}")
    print(f"EA baselines: {', '.join(ea_baselines.keys()) if ea_baselines else '(none)'}")
    print(f"Output: {output_dir}")

    if chart == "summary":
        print_summary_table(reports, configs, ea_baselines)
    elif chart == "all":
        print_summary_table(reports, configs, ea_baselines)
        if reports:
            for chart_name, chart_fn in REPORT_CHARTS.items():
                print(f"\nGenerating: {chart_name}")
                chart_fn(reports, configs, output_dir)
        if ea_baselines:
            for chart_name, chart_fn in EA_CHARTS.items():
                print(f"\nGenerating: {chart_name}")
                chart_fn(ea_baselines, output_dir)
        print(f"\nDone. Charts saved to: {output_dir}")
    elif chart in REPORT_CHARTS:
        print(f"\nGenerating: {chart}")
        REPORT_CHARTS[chart](reports, configs, output_dir)
    elif chart in EA_CHARTS:
        print(f"\nGenerating: {chart}")
        EA_CHARTS[chart](ea_baselines, output_dir)


if __name__ == "__main__":
    main()
