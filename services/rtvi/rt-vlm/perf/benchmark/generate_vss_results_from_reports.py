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
Generate vss_results.json from pre-generated perf report directories.

Default mode (--dashboard, or no flag): produces a single test case per platform
with _1t/_100t suffixed metrics matching the rtvi-vlm.yaml KPI definitions for
the vss_perf_analyzer dashboard.

Legacy mode (--raw): produces per-scenario test cases with standard metric names.

Usage:
    # Dashboard-compatible (default)
    python generate_vss_results_from_reports.py \\
        /path/to/rtvi-vlm-perf-report-h100-03-10-qa-machine \\
        /path/to/rtvi-vlm-perf-report-rtx-pro-03-13-h264 \\
        /path/to/rtvi-vlm-perf-report-thor-03-13 \\
        /path/to/rtvi-vlm-perf-report-spark-03-09

    # Upload to MinIO
    python generate_vss_results_from_reports.py --upload /path/to/report1

    # Legacy per-scenario mode
    python generate_vss_results_from_reports.py --raw /path/to/report1
"""

from __future__ import annotations

import argparse
import json  # noqa: E402
import logging
import sys
from pathlib import Path

# Add benchmark dir to path for imports
sys.path.insert(0, str(Path(__file__).resolve().parent))

from vss_perf_common import (  # noqa: E402
    build_and_save,
    discover_platform,
    upload_result_file,
)
from vss_perf_rtvi_vlm_adaptor import (  # noqa: E402
    build_dashboard_test_cases,
    normalize_config_id,
    rtvi_vlm_execution_results_to_test_cases,
)

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")


def _detect_platform_from_dir(report_dir: str) -> str:
    """Infer platform key from report directory name for GPU metric selection."""
    name = Path(report_dir).name.lower()
    for key in ("thor", "spark", "h100", "rtx_pro", "rtx-pro", "b200", "l40s", "jetson"):
        if key in name:
            return key
    return ""


def _fix_scenario_dir(execution_results: dict, actual_scenario_dir: Path) -> dict:
    """Replace the scenario_dir in execution_results with the actual path on disk."""
    execution_results = dict(execution_results)
    execution_results["scenario_dir"] = str(actual_scenario_dir)
    return execution_results


# ── Dashboard mode (default) ─────────────────────────────────────────────────


def generate_dashboard_report(
    report_dir: Path, output_dir: Path | None = None, upload: bool = False
):
    """Generate dashboard-compatible vss_results.json with _1t/_100t metrics.

    Produces a single test case per platform matching rtvi-vlm.yaml KPI paths.
    """
    report_dir = report_dir.resolve()
    if not report_dir.is_dir():
        logger.error("Report directory does not exist: %s", report_dir)
        return None

    raw_platform = _detect_platform_from_dir(str(report_dir))
    config_id = normalize_config_id(raw_platform)
    logger.info(
        "Processing report: %s (platform: %s -> config_id: %s)",
        report_dir.name,
        raw_platform,
        config_id,
    )

    test_cases = build_dashboard_test_cases(str(report_dir), raw_platform)
    if not test_cases:
        logger.warning("No test cases generated for %s", report_dir.name)
        return None

    try:
        platform = discover_platform(config_id=config_id)
    except Exception:
        platform = {"gpu": {"model": config_id.upper() or "Unknown", "count": 1}}

    dest = output_dir or report_dir
    output_file = dest / "vss_results.json"

    json_path = build_and_save(
        str(output_file),
        "RTVI-VLM",
        test_cases,
        config_id=config_id,
        platform=platform,
        config={
            "config_id": config_id,
            "report_dir": str(report_dir),
        },
        benchmark_name=f"rtvi_vlm_{config_id}",
        benchmark_mode="dashboard",
        passed=len(test_cases),
        failed=0,
    )
    logger.info("  Written: %s (%d test cases)", json_path, len(test_cases))

    if upload:
        if upload_result_file(str(json_path), "RTVI-VLM"):
            logger.info("  Uploaded to MinIO")
        else:
            logger.warning("  MinIO upload failed")

    return json_path


# ── Raw/legacy mode ──────────────────────────────────────────────────────────


def generate_raw_report(report_dir: Path, output_dir: Path | None = None, upload: bool = False):
    """Generate legacy vss_results.json with per-scenario test cases."""
    report_dir = report_dir.resolve()
    if not report_dir.is_dir():
        logger.error("Report directory does not exist: %s", report_dir)
        return None

    raw_platform = _detect_platform_from_dir(str(report_dir))
    config_id = normalize_config_id(raw_platform)
    logger.info("Processing report: %s (platform: %s)", report_dir.name, config_id)

    summaries = sorted(report_dir.glob("*/execution_summary.json"))
    if not summaries:
        logger.warning("No execution_summary.json files found in %s", report_dir)
        return None

    all_test_cases = []
    scenarios_processed = []
    total_passed = 0
    total_failed = 0

    for summary_path in summaries:
        scenario_dir = summary_path.parent
        scenario_name = scenario_dir.name

        try:
            with open(summary_path) as f:
                execution_results = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Skipping %s: %s", scenario_name, e)
            continue

        execution_results = _fix_scenario_dir(execution_results, scenario_dir)
        benchmark_mode = execution_results.get("benchmark_mode", "")
        logger.info("  Scenario: %-45s mode: %s", scenario_name, benchmark_mode)

        test_cases = rtvi_vlm_execution_results_to_test_cases(
            execution_results, platform=raw_platform
        )

        if test_cases:
            all_test_cases.extend(test_cases)
            scenarios_processed.append(scenario_name)
            total_passed += execution_results.get("successful_test_cases", len(test_cases))
            total_failed += execution_results.get("failed_test_cases", 0)
            logger.info("    -> %d test cases", len(test_cases))
        else:
            logger.warning("    -> No test cases produced")

    if not all_test_cases:
        logger.warning("No test cases generated for %s", report_dir.name)
        return None

    try:
        platform = discover_platform(config_id=config_id)
    except Exception:
        platform = {"gpu": {"model": config_id.upper() or "Unknown", "count": 1}}

    dest = output_dir or report_dir
    output_file = dest / "vss_results_raw.json"

    json_path = build_and_save(
        str(output_file),
        "RTVI-VLM",
        all_test_cases,
        config_id=config_id,
        platform=platform,
        config={
            "config_id": config_id,
            "report_dir": str(report_dir),
            "scenarios": scenarios_processed,
        },
        benchmark_name=f"all_scenarios_{config_id}",
        benchmark_mode="combined",
        passed=total_passed,
        failed=total_failed,
    )
    logger.info("  Written: %s (%d test cases)", json_path, len(all_test_cases))

    if upload:
        if upload_result_file(str(json_path), "RTVI-VLM"):
            logger.info("  Uploaded to MinIO")
        else:
            logger.warning("  MinIO upload failed")

    return json_path


# ── CLI ──────────────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(
        description="Generate vss_results.json from pre-generated perf report directories"
    )
    parser.add_argument(
        "report_dirs",
        nargs="+",
        help="One or more perf report directories containing scenario subdirs",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Output directory for vss_results.json files (default: same as report dir)",
    )
    parser.add_argument("--upload", action="store_true", help="Upload results to MinIO")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--dashboard",
        action="store_true",
        default=True,
        help="Dashboard-compatible output with _1t/_100t metrics (default)",
    )
    mode.add_argument(
        "--raw",
        action="store_true",
        help="Legacy per-scenario output with standard metric names",
    )
    args = parser.parse_args()

    output_dir = Path(args.output_dir) if args.output_dir else None
    if output_dir:
        output_dir.mkdir(parents=True, exist_ok=True)

    generate_fn = generate_raw_report if args.raw else generate_dashboard_report

    results = []
    for report_dir in args.report_dirs:
        path = Path(report_dir).resolve()
        dest = (output_dir / path.name) if output_dir else None
        if dest:
            dest.mkdir(parents=True, exist_ok=True)
        result = generate_fn(path, output_dir=dest, upload=args.upload)
        if result:
            results.append(result)

    logger.info("Generated %d vss_results.json files", len(results))


if __name__ == "__main__":
    main()
