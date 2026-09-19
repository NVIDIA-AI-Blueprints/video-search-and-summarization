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
Update RTVI-VLM KPI baselines from perf benchmark report directories.

Reads JSON results from platform report directories and updates the KPI
definition YAML with new baseline values.

Usage:
    python3 update_kpi_baselines.py \\
        --reports \\
            h100=~/VSS/perf-report/rtvi-vlm-perf-report-h100-03-10-qa-machine \\
            rtx_pro=~/VSS/perf-report/rtvi-vlm-perf-report-rtx-pro-03-11 \\
            jetson_thor=~/VSS/perf-report/rtvi-vlm-perf-report-thor \\
            dgx_spark=~/VSS/perf-report/rtvi-vlm-perf-report-spark-03-09 \\
        --kpi ~/VSS/vss_perf_analyzer/kpi_definitions/rtvi-vlm.yaml \\
        --dry-run

    # Actually write updates:
    python3 update_kpi_baselines.py \\
        --reports h100=~/path rtx_pro=~/path ... \\
        --kpi ~/VSS/vss_perf_analyzer/kpi_definitions/rtvi-vlm.yaml
"""

import argparse
import sys
from pathlib import Path
from typing import Dict, Optional

from perf_platform import get_gpu_metric
from perf_utils import load_json


def find_test_case_summary(
    report_dir: Path, scenario_prefix: str, tc_pattern: str
) -> Optional[Dict]:
    """Find and load a test_case_summary.json matching scenario prefix and test case glob."""
    for scenario_dir in sorted(report_dir.glob(f"{scenario_prefix}*")):
        if not scenario_dir.is_dir():
            continue
        for tc_dir in sorted(scenario_dir.glob(tc_pattern)):
            summary = load_json(tc_dir / "test_case_summary.json")
            if summary:
                return summary
    return None


def find_max_streams_results(report_dir: Path, scenario_name: str) -> Optional[Dict]:
    """Find and load max_live_streams_results.json for a scenario."""
    scenario_dir = report_dir / scenario_name
    if not scenario_dir.is_dir():
        return None
    for tc_dir in sorted(scenario_dir.iterdir()):
        if not tc_dir.is_dir():
            continue
        data = load_json(tc_dir / "max_live_streams_results.json")
        if data and data.get("success", False):
            return data
    return None


def find_e2e_10s_summary(report_dir: Path, token_prefix: str) -> Optional[Dict]:
    """Find the 10s video test_case_summary with concurrency_summary (the file_burst one)."""
    for scenario_dir in sorted(report_dir.glob(f"{token_prefix}*")):
        if not scenario_dir.is_dir():
            continue
        for tc_dir in sorted(scenario_dir.iterdir()):
            if not tc_dir.is_dir():
                continue
            if "10s" not in tc_dir.name.lower():
                continue
            summary = load_json(tc_dir / "test_case_summary.json")
            if summary and "concurrency_summary" in summary:
                cs = summary["concurrency_summary"]
                # Pick the one with max concurrency > 1 (the file_burst test, not single-file)
                max_c = max((int(k) for k in cs.keys() if k.isdigit()), default=0)
                if max_c > 1:
                    return summary
    return None


# ── KPI extraction functions ─────────────────────────────────────────────────


def extract_baselines(report_dir: Path, platform: str) -> Dict[str, Optional[float]]:
    """Extract all KPI baseline values from a platform's report directory."""
    baselines = {}

    # --- E2E Latency (file_burst, c=1, 10s video) ---
    for token_label, token_prefix in [
        ("1t", "e2e_latency_1_token"),
        ("100t", "e2e_latency_100_token"),
    ]:
        summary = find_e2e_10s_summary(report_dir, token_prefix)
        if summary and "concurrency_summary" in summary:
            cs = summary["concurrency_summary"]
            if "1" in cs:
                val = cs["1"].get("mean_avg_latency", 0)
                baselines[f"e2e_latency_{token_label}"] = round(val, 2) if val else None
            else:
                baselines[f"e2e_latency_{token_label}"] = None
        else:
            baselines[f"e2e_latency_{token_label}"] = None

    # --- Decode Latency (concurrency_test at 1 stream) ---
    for token_label, token_prefix in [
        ("1t", "concurrency_test_1_token"),
        ("100t", "concurrency_test_100_token"),
    ]:
        summary = find_test_case_summary(report_dir, token_prefix, "*1streams")
        if summary:
            val = summary.get("mean_decode_latency_seconds_avg", 0)
            baselines[f"decode_latency_{token_label}"] = round(val, 2) if val else None
        else:
            baselines[f"decode_latency_{token_label}"] = None

    # --- GPU Usage at max streams (448x448) ---
    for token_label, scenario_name in [
        ("1t", "max_live_streams_test_1_token_448"),
        ("100t", "max_live_streams_test_100_token_448"),
    ]:
        data = find_max_streams_results(report_dir, scenario_name)
        if data:
            val = get_gpu_metric(data, "gpu_usage_mean", platform)
            baselines[f"gpu_usage_{token_label}"] = round(val, 2) if val else None
        else:
            baselines[f"gpu_usage_{token_label}"] = None

    # --- Max Sustainable Streams (448x448) ---
    for token_label, scenario_name in [
        ("1t", "max_live_streams_test_1_token_448"),
        ("100t", "max_live_streams_test_100_token_448"),
    ]:
        data = find_max_streams_results(report_dir, scenario_name)
        if data:
            baselines[f"max_streams_{token_label}"] = data.get("max_sustainable_streams", 0)
        else:
            baselines[f"max_streams_{token_label}"] = None

    # --- File Throughput (e2e_latency, 10s video, max concurrency) ---
    for token_label, token_prefix in [
        ("1t", "e2e_latency_1_token"),
        ("100t", "e2e_latency_100_token"),
    ]:
        summary = find_e2e_10s_summary(report_dir, token_prefix)
        if summary and "concurrency_summary" in summary:
            cs = summary["concurrency_summary"]
            max_c = str(max((int(k) for k in cs.keys() if k.isdigit()), default=0))
            if max_c != "0":
                val = cs[max_c].get("mean_throughput", 0)
                baselines[f"file_throughput_{token_label}"] = round(val, 2) if val else None
            else:
                baselines[f"file_throughput_{token_label}"] = None
        else:
            baselines[f"file_throughput_{token_label}"] = None

    # --- Concurrency Latencies: 10 streams (H100/RTX Pro) ---
    for token_label, token_prefix in [
        ("1t", "concurrency_test_1_token"),
        ("100t", "concurrency_test_100_token"),
    ]:
        summary = find_test_case_summary(report_dir, token_prefix, "*10streams")
        if summary:
            avg = summary.get("mean_avg_latency", 0)
            p95 = summary.get("mean_p95_latency", 0)
            baselines[f"concurrency_avg_latency_{token_label}"] = round(avg, 2) if avg else None
            baselines[f"concurrency_p95_latency_{token_label}"] = round(p95, 2) if p95 else None
        else:
            baselines[f"concurrency_avg_latency_{token_label}"] = None
            baselines[f"concurrency_p95_latency_{token_label}"] = None

    # --- Concurrency Latencies: 2 streams (Thor/Spark edge) ---
    for token_label, token_prefix in [
        ("1t", "concurrency_test_1_token"),
        ("100t", "concurrency_test_100_token"),
    ]:
        summary = find_test_case_summary(report_dir, token_prefix, "*2streams")
        if summary:
            avg = summary.get("mean_avg_latency", 0)
            p95 = summary.get("mean_p95_latency", 0)
            baselines[f"concurrency_avg_latency_2streams_{token_label}"] = (
                round(avg, 2) if avg else None
            )
            baselines[f"concurrency_p95_latency_2streams_{token_label}"] = (
                round(p95, 2) if p95 else None
            )
        else:
            baselines[f"concurrency_avg_latency_2streams_{token_label}"] = None
            baselines[f"concurrency_p95_latency_2streams_{token_label}"] = None

    return baselines


# ── YAML update (preserves comments and structure) ───────────────────────────


def update_yaml_baselines(
    kpi_path: Path,
    all_baselines: Dict[str, Dict[str, Optional[float]]],
    dry_run: bool = False,
) -> None:
    """Update KPI YAML baselines in-place, preserving comments and formatting.

    Uses line-by-line text manipulation to preserve YAML comments.
    """
    with open(kpi_path) as f:
        lines = f.readlines()

    changes = []
    current_kpi_name = None
    in_baselines = False
    i = 0

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        # Track which KPI we're in
        if stripped.startswith("- name:"):
            current_kpi_name = stripped.split(":", 1)[1].strip().strip('"').strip("'")
            in_baselines = False

        # Track baselines block
        if stripped.startswith("baselines:"):
            in_baselines = True
            i += 1
            continue

        # Update baseline values
        if in_baselines and current_kpi_name and ":" in stripped and not stripped.startswith("-"):
            # Parse platform key
            parts = stripped.split(":", 1)
            platform_key = parts[0].strip()

            # Check if we have new data for this platform + KPI
            if platform_key in all_baselines and current_kpi_name in all_baselines[platform_key]:
                new_val = all_baselines[platform_key][current_kpi_name]

                # Preserve inline comment if any
                comment = ""
                val_part = parts[1].strip()
                if "#" in val_part:
                    val_idx = val_part.index("#")
                    comment = "  " + val_part[val_idx:]

                # Build new line preserving indentation
                indent = line[: len(line) - len(line.lstrip())]
                if new_val is None:
                    new_line = f"{indent}{platform_key}: null{comment}\n"
                elif isinstance(new_val, int):
                    new_line = f"{indent}{platform_key}: {new_val}{comment}\n"
                else:
                    new_line = f"{indent}{platform_key}: {new_val}{comment}\n"

                if new_line != lines[i]:
                    old_val_str = val_part.split("#")[0].strip()
                    changes.append(
                        f"  {current_kpi_name}.{platform_key}: {old_val_str} -> {new_val}"
                    )
                    lines[i] = new_line

        # Exit baselines block on outdent
        if in_baselines and stripped and not stripped.startswith("#") and ":" in stripped:
            check_indent = len(line) - len(line.lstrip())
            # baselines entries are deeply indented; a less-indented line means we left
            if check_indent <= 4 and not stripped.startswith("default"):
                in_baselines = False

        i += 1

    # Report changes
    if changes:
        print(f"\n{'[DRY RUN] ' if dry_run else ''}Baseline changes ({len(changes)}):")
        for c in changes:
            print(c)

        if not dry_run:
            with open(kpi_path, "w") as f:
                f.writelines(lines)
            print(f"\nUpdated: {kpi_path}")
        else:
            print("\nNo changes written (dry-run mode).")
    else:
        print("\nNo baseline changes needed — all values match.")


# ── Main ─────────────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(
        description="Update RTVI-VLM KPI baselines from perf benchmark reports",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--reports",
        nargs="+",
        required=True,
        metavar="PLATFORM=PATH",
        help=(
            "Platform reports as PLATFORM=PATH pairs. Platform keys must match "
            "the KPI YAML baseline keys: h100, rtx_pro, jetson_thor, dgx_spark"
        ),
    )
    parser.add_argument(
        "--kpi",
        type=str,
        required=True,
        help="Path to KPI definition YAML file",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show changes without writing to file",
    )
    args = parser.parse_args()

    # Parse platform=path pairs
    platforms = {}
    for item in args.reports:
        if "=" not in item:
            print(f"Error: expected PLATFORM=PATH, got: {item}")
            sys.exit(1)
        name, path = item.split("=", 1)
        platforms[name] = Path(path).expanduser().resolve()

    kpi_path = Path(args.kpi).expanduser().resolve()
    if not kpi_path.exists():
        print(f"Error: KPI file not found: {kpi_path}")
        sys.exit(1)

    print(f"KPI file: {kpi_path}")
    print(f"Platforms: {', '.join(platforms.keys())}")
    if args.dry_run:
        print("Mode: DRY RUN")

    # Extract baselines from each platform
    all_baselines: Dict[str, Dict[str, Optional[float]]] = {}
    for platform_key, report_dir in platforms.items():
        print(f"\nExtracting: {platform_key} ({report_dir})")
        if not report_dir.is_dir():
            print("  Error: directory not found")
            continue

        baselines = extract_baselines(report_dir, platform_key)
        all_baselines[platform_key] = baselines

        # Print extracted values
        for kpi_name, val in sorted(baselines.items()):
            status = f"{val}" if val is not None else "null (no data)"
            print(f"  {kpi_name}: {status}")

    # Update YAML
    update_yaml_baselines(kpi_path, all_baselines, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
