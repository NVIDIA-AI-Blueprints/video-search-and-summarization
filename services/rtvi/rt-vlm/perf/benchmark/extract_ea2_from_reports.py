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
Extract EA2 baseline data from RTVI-VLM perf benchmark report directories.

Produces a JSON file in the same format as ea_baseline_data/ea2_engsqa.json,
but sourced from actual benchmark results rather than the EngSQA spreadsheet.

Usage:
    python3 extract_ea2_from_reports.py \\
        --reports \\
            H100=~/VSS/perf-report/rtvi-vlm-perf-report-h100-03-10-qa-machine \\
            RTX_Pro=~/VSS/perf-report/rtvi-vlm-perf-report-rtx-pro-03-11 \\
            AGX_Thor=~/VSS/perf-report/rtvi-vlm-perf-report-thor \\
            DGX_Spark=~/VSS/perf-report/rtvi-vlm-perf-report-spark-03-09 \\
        --output ea_baseline_data/ea2_from_reports.json
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Dict, List

from perf_utils import load_json


def extract_concurrency(report_dir: Path) -> Dict[str, List[Dict]]:
    """Extract concurrency latency data for 1t and 100t."""
    result = {}
    for token_label, dir_name in [
        ("1t", "concurrency_test_1_token"),
        ("100t", "concurrency_test_100_token"),
    ]:
        scenario_dir = report_dir / dir_name
        if not scenario_dir.is_dir():
            print(f"  Warning: {scenario_dir} not found, skipping {token_label} concurrency")
            continue

        entries = []
        for tc_dir in sorted(scenario_dir.iterdir()):
            summary_path = tc_dir / "test_case_summary.json"
            if not summary_path.exists():
                continue
            data = load_json(summary_path)
            if not data:
                continue

            stream_count = data.get("stream_count", 0)
            avg_latency = data.get("mean_avg_latency", 0)
            p95_latency = data.get("mean_p95_latency", 0)

            entry = {
                "streams": stream_count,
                "avg_latency": round(avg_latency, 2),
                "p95_latency": round(p95_latency, 2),
            }

            # Extract GPU usage from iteration results
            iters = data.get("iteration_results", [])
            gpu_vals = [i["vlm_gpu_usage_mean"] for i in iters if "vlm_gpu_usage_mean" in i]
            if gpu_vals:
                entry["gpu_pct"] = round(sum(gpu_vals) / len(gpu_vals), 2)

            entries.append(entry)

        # Sort by stream count
        entries.sort(key=lambda x: x["streams"])
        if entries:
            result[token_label] = entries

    return result


def extract_max_streams(report_dir: Path) -> Dict[str, Dict]:
    """Extract max live streams data for 1t and 100t."""
    result = {}
    for token_label, dir_name in [
        ("1t", "max_live_streams_test_1_token"),
        ("100t", "max_live_streams_test_100_token"),
    ]:
        scenario_dir = report_dir / dir_name
        if not scenario_dir.is_dir():
            print(f"  Warning: {scenario_dir} not found, skipping {token_label} max_streams")
            continue

        # Find the max_live_streams_results.json
        results_files = list(scenario_dir.glob("*/max_live_streams_results.json"))
        if not results_files:
            print(f"  Warning: No max_live_streams_results.json in {scenario_dir}")
            continue

        data = load_json(results_files[0])
        if not data:
            continue

        max_streams = data.get("last_stable_stream_count", data.get("max_sustainable_streams", 0))
        entry = {
            "max_streams": max_streams,
            "p95_latency": round(data.get("last_stable_p95", 0), 2),
            "avg_latency": round(data.get("last_stable_moving_average_latency", 0), 2),
            "decode_latency_avg": round(data.get("mean_decode_latency_seconds_avg", 9.5), 2),
        }

        # GPU metrics
        if "vlm_gpu_usage_mean" in data:
            entry["gpu_pct"] = round(data["vlm_gpu_usage_mean"], 2)
        if "vlm_gpu_usage_p90" in data:
            entry["gpu_p90_pct"] = round(data["vlm_gpu_usage_p90"], 2)
        if "vlm_gpu_memory_mean" in data:
            entry["gpu_mem_pct"] = round(data["vlm_gpu_memory_mean"], 2)
        if "vlm_nvdec_usage_p90" in data:
            entry["nvdec_p90_pct"] = round(data["vlm_nvdec_usage_p90"], 2)

        # SOL benchmark (if binary search was used)
        if "max_sustainable_streams" in data and data["max_sustainable_streams"] != max_streams:
            entry["sol_benchmark"] = data["max_sustainable_streams"]

        result[token_label] = entry

    return result


def main():
    parser = argparse.ArgumentParser(description="Extract EA2 baseline from report directories")
    parser.add_argument(
        "--reports",
        nargs="+",
        required=True,
        metavar="PLATFORM=PATH",
        help="Platform reports as PLATFORM=PATH pairs",
    )
    parser.add_argument(
        "--output",
        default="ea_baseline_data/ea2_from_reports.json",
        help="Output JSON path",
    )
    parser.add_argument("--label", default="EA2", help="Release label")
    parser.add_argument("--release", default="3.0", help="Release version")
    args = parser.parse_args()

    # Parse platform=path pairs
    platforms = {}
    for item in args.reports:
        if "=" not in item:
            print(f"Error: expected PLATFORM=PATH, got: {item}")
            sys.exit(1)
        name, path = item.split("=", 1)
        platforms[name] = Path(path).expanduser().resolve()

    print(f"Extracting EA2 data from {len(platforms)} platforms: {', '.join(platforms.keys())}")

    output = {
        "_metadata": {
            "label": args.label,
            "release": args.release,
            "model": "CR2-8B",
            "model_precision": "FP8",
            "engine_backend": "vLLM",
            "source": "Extracted from benchmark report directories",
            "note": "Concurrency: 448x448/80frm (~8K); Max streams: 372x372/30frm (~2K)",
        },
        "concurrency_latency": {"_header": "Concurrent streams latency at fixed stream counts"},
        "max_live_streams": {"_header": "Maximum concurrent live streams before degradation"},
    }

    for platform_name, report_dir in platforms.items():
        print(f"\nProcessing: {platform_name} ({report_dir})")
        if not report_dir.is_dir():
            print(f"  Error: directory not found: {report_dir}")
            continue

        concurrency = extract_concurrency(report_dir)
        if concurrency:
            output["concurrency_latency"][platform_name] = concurrency
            for tk, entries in concurrency.items():
                streams_str = ", ".join(f"{e['streams']}s" for e in entries)
                print(f"  concurrency {tk}: {streams_str}")

        max_streams = extract_max_streams(report_dir)
        if max_streams:
            output["max_live_streams"][platform_name] = max_streams
            for tk, entry in max_streams.items():
                print(
                    f"  max_streams {tk}: {entry['max_streams']} streams (p95={entry['p95_latency']}s)"
                )

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2)
    print(f"\nSaved: {out_path}")


if __name__ == "__main__":
    main()
