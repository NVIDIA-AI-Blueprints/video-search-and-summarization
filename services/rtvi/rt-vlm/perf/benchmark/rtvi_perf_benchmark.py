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

import argparse
import copy
import logging
import os
import sys
import time
from typing import Dict

from base import BenchmarkBase
from concurrency_benchmark import ConcurrencyBenchmark
from concurrent_live_streams_benchmark import ConcurrentLiveStreamsBenchmark
from dotenv import load_dotenv
from file_burst_benchmark import FileBurstBenchmark
from live_streams_benchmark import LiveStreamsBenchmark
from single_file_benchmark import SingleFileBenchmark
from single_live_stream_benchmark import SingleLiveStreamBenchmark
from text_embedding_benchmark import TextEmbeddingBenchmark
from text_embedding_concurrency_benchmark import TextEmbeddingConcurrencyBenchmark
from vss_perf_common import build_and_save, discover_platform, upload_result_file
from vss_perf_rtvi_embed_adaptor import rtvi_embed_execution_results_to_test_cases
from vss_perf_rtvi_vlm_adaptor import (
    build_dashboard_test_cases,
    normalize_config_id,
    rtvi_vlm_execution_results_to_test_cases,
)

# Setup module-level logger
logger = logging.getLogger(__name__)

# Benchmark registry mapping benchmark modes to implementation classes
BENCHMARK_REGISTRY = {
    "single_file": SingleFileBenchmark,
    "file_burst": FileBurstBenchmark,
    "max_live_streams": LiveStreamsBenchmark,
    "concurrent_live_streams": ConcurrentLiveStreamsBenchmark,
    "concurrency": ConcurrencyBenchmark,
    "single_live_stream": SingleLiveStreamBenchmark,
    "text_embedding": TextEmbeddingBenchmark,
    "text_embedding_concurrency": TextEmbeddingConcurrencyBenchmark,
}


def log_gpu_information(vlm_gpus=None):
    """Log GPU information once at the start of benchmark suite"""
    try:
        import pynvml

        pynvml.nvmlInit()
        device_count = pynvml.nvmlDeviceGetCount()

        logger.info("=" * 60)
        logger.info("GPU Monitor Initialization")
        logger.info("=" * 60)
        logger.info(f"Total GPUs detected: {device_count}")

        # Log information about each GPU
        if device_count > 0:
            logger.info("GPU Details:")
            for i in range(device_count):
                handle = pynvml.nvmlDeviceGetHandleByIndex(i)
                gpu_name = pynvml.nvmlDeviceGetName(handle)
                logger.info(f"  GPU {i}: {gpu_name}")

                try:
                    # Memory info
                    mem_info = pynvml.nvmlDeviceGetMemoryInfo(handle)
                    total_mem_gb = mem_info.total / (1024**3)
                    logger.info(f"    - Total Memory: {total_mem_gb:.2f} GB")

                    # Driver version
                    if i == 0:  # Driver version is system-wide, only print once
                        driver_version = pynvml.nvmlSystemGetDriverVersion()
                        logger.info(f"    - Driver Version: {driver_version}")

                    # CUDA compute capability
                    major, minor = pynvml.nvmlDeviceGetCudaComputeCapability(handle)
                    logger.info(f"    - Compute Capability: {major}.{minor}")

                    # Max clock speeds
                    max_gpu_clock = pynvml.nvmlDeviceGetMaxClockInfo(
                        handle, pynvml.NVML_CLOCK_GRAPHICS
                    )
                    max_mem_clock = pynvml.nvmlDeviceGetMaxClockInfo(handle, pynvml.NVML_CLOCK_MEM)
                    logger.info(f"    - Max GPU Clock: {max_gpu_clock} MHz")
                    logger.info(f"    - Max Memory Clock: {max_mem_clock} MHz")

                    # Show what this GPU is used for
                    usage = []
                    if vlm_gpus and i in vlm_gpus:
                        usage.append("VLM")
                    if usage:
                        logger.info(f"    - Used By: {', '.join(usage)}")

                except Exception as e:
                    logger.error(f"    - Error getting additional info: {e}")

        logger.info("=" * 60)

        # Log which GPUs will be monitored
        if vlm_gpus:
            all_gpu_ids = list(set(vlm_gpus))
            logger.info(f"Monitoring GPU(s): {all_gpu_ids}")
    except Exception as e:
        logger.warning(f"Failed to get GPU information: {e}")


def setup_signal_handlers():
    """Setup signal handlers for graceful shutdown"""
    import signal

    active_benchmark = None
    interrupt_count = 0

    def signal_handler(signum, frame):
        nonlocal interrupt_count
        interrupt_count += 1

        if interrupt_count == 1:
            logger.info("")
            logger.info("Received interrupt signal - cleaning up active resources...")
            logger.info("Press Ctrl+C again to force quit without cleanup.")
            if active_benchmark:
                active_benchmark.cleanup_resources()
            sys.exit(0)
        else:
            logger.error("")
            logger.error("Force quit - terminating immediately without cleanup!")
            os._exit(1)

    signal.signal(signal.SIGINT, signal_handler)
    return lambda benchmark: setattr(signal_handler, "active_benchmark", benchmark)


def create_benchmark_instance(benchmark_mode: str, base_url: str, output_dir: str) -> BenchmarkBase:
    """Create a benchmark instance for the specified mode"""
    benchmark_class = BENCHMARK_REGISTRY.get(benchmark_mode)
    if not benchmark_class:
        raise ValueError(
            f"Unknown benchmark mode: {benchmark_mode}. Available modes: {list(BENCHMARK_REGISTRY.keys())}"
        )

    return benchmark_class(base_url=base_url, output_base_dir=output_dir)


def run_single_scenario(
    args: argparse.Namespace,
    scenario_name: str,
    scenario_config: Dict,
    global_config: Dict,
    base_url: str,
    output_base_dir: str,
    initial_stream_count: int = None,
    add_stream_count: int = None,
    binary_search_refinement: bool = None,
    concurrency_levels: list = None,
) -> Dict:
    """Run a single test scenario"""
    logger.info("")
    logger.info(f"=== Running scenario: {scenario_name} ===")
    logger.debug(f"Description: {scenario_config.get('description', 'No description')}")

    effective_config_id = args.config_id or os.path.basename(output_base_dir) or "default"

    # Determine benchmark mode
    benchmark_mode = scenario_config.get("benchmark_mode", "single_file")
    logger.debug(f"Benchmark mode: {benchmark_mode}")

    # Apply CLI overrides for max_live_streams scenarios
    _overrides = {
        k: v
        for k, v in {
            "initial_stream_count": initial_stream_count,
            "add_stream_count": add_stream_count,
            "binary_search_refinement": binary_search_refinement,
        }.items()
        if v is not None
    }
    if _overrides:
        if benchmark_mode == "max_live_streams":
            scenario_config = copy.deepcopy(scenario_config)
            for video in scenario_config.get("videos", []):
                for key, value in _overrides.items():
                    video[key] = value
                    logger.info(f"CLI override: {key}={value}")
        else:
            logger.warning(
                "--initial-stream-count / --add-stream-count / --binary-search-refinement ignored "
                f"(only applies to max_live_streams; scenario '{scenario_name}' is '{benchmark_mode}')"
            )

    # Apply --concurrency-levels override
    if concurrency_levels is not None:
        _CONCURRENCY_MODES = {
            "concurrent_live_streams": "stream_count",
            "file_burst": "concurrency_levels",
            "concurrency": "concurrency_levels",
        }
        video_key = _CONCURRENCY_MODES.get(benchmark_mode)
        if video_key:
            scenario_config = copy.deepcopy(scenario_config)
            for video in scenario_config.get("videos", []):
                video[video_key] = concurrency_levels
            logger.info(f"CLI override: {video_key}={concurrency_levels}")
        else:
            logger.warning(
                f"--concurrency-levels ignored (not applicable to '{benchmark_mode}' scenarios)"
            )

    # Create benchmark instance
    try:
        benchmark = create_benchmark_instance(benchmark_mode, base_url, output_base_dir)
    except ValueError as e:
        logger.error(f"Error: {e}")
        return {"scenario_name": scenario_name, "success": False, "error": str(e)}

    # Setup signal handling for this benchmark
    set_active_benchmark = setup_signal_handlers()
    set_active_benchmark(benchmark)

    scenario_result = {
        "scenario_name": scenario_name,
        "benchmark_mode": benchmark_mode,
        "success": False,
        "error": None,
        "execution_time_seconds": 0,
    }

    start_time = time.time()

    try:
        if benchmark_mode in {"max_live_streams", "concurrent_live_streams", "single_live_stream"}:
            benchmark.assert_no_active_streams(f"before scenario '{scenario_name}'")

        # Load and validate configuration
        full_config = {"global": global_config, "test_scenarios": {scenario_name: scenario_config}}

        # Execute benchmark
        execution_results = benchmark.execute(full_config, scenario_name)

        # Analyze results and generate report
        results_dir = execution_results["scenario_dir"]
        report_file = os.path.join(output_base_dir, f"{scenario_name}_{benchmark_mode}_report.xlsx")
        benchmark.analyze_results(results_dir, report_file)

        # Determine scenario success based on test results
        successful = execution_results.get("successful_test_cases", 0)
        total = execution_results.get("total_test_cases", 0)
        scenario_success = successful > 0 if total > 0 else False

        # Update scenario result
        result_update = {
            "success": scenario_success,
            "results_dir": results_dir,
            "report_file": report_file,
            "total_test_cases": total,
            "successful_test_cases": successful,
            "failed_test_cases": execution_results.get("failed_test_cases", 0),
        }
        resource_bound_test_cases = sum(
            1 for result in execution_results.get("test_cases", []) if result.get("resource_bound")
        )
        if resource_bound_test_cases:
            result_update["resource_bound"] = True
            result_update["resource_bound_test_cases"] = resource_bound_test_cases

        # Add error message if scenario failed due to no passing tests
        if not scenario_success and total > 0:
            if resource_bound_test_cases:
                result_update["error"] = (
                    f"{resource_bound_test_cases}/{total} test case(s) were resource-bound"
                )
            else:
                result_update["error"] = f"All {total} test case(s) failed"

        scenario_result.update(result_update)

        if scenario_success:
            logger.info(f"Scenario '{scenario_name}' completed successfully!")
        else:
            logger.error(f"Scenario '{scenario_name}' failed - no test cases passed!")
        logger.info(f"Results: {successful}/{total} test cases passed")
        logger.info(f"Report generated: {report_file}")

        # Optional: output VSS result JSON and/or upload (vss_perf_common + vss_perf_rtvi_adapter)
        if getattr(args, "output_json", None) and scenario_success:
            try:
                backend = getattr(benchmark, "backend_type", None) or global_config.get(
                    "backend_type", "rtvi_vlm"
                )
                if backend == "rtvi_embed":
                    test_cases = rtvi_embed_execution_results_to_test_cases(
                        benchmark, execution_results
                    )
                else:
                    test_cases = rtvi_vlm_execution_results_to_test_cases(
                        execution_results,
                        platform=effective_config_id,
                    )
                if test_cases:
                    try:
                        platform = discover_platform(
                            config_id=effective_config_id,
                            topology=global_config.get("topology"),
                        )
                    except Exception:
                        vlm_gpus = global_config.get("vlm_gpus") or []
                        llm_gpus = global_config.get("llm_gpus") or []
                        gpu_count = len(vlm_gpus) + len(llm_gpus) or 1
                        platform = {"gpu": {"model": "Unknown", "count": gpu_count}}
                    result_config = {
                        "config_id": effective_config_id,
                        "output_dir": global_config.get("output_dir"),
                        "benchmark_mode": benchmark_mode,
                    }
                    duration_seconds = time.time() - start_time
                    passed = execution_results.get("successful_test_cases", len(test_cases))
                    failed = execution_results.get("failed_test_cases", 0)
                    out_path = (
                        args.output_json
                        if os.path.isabs(args.output_json)
                        else os.path.join(output_base_dir, args.output_json)
                    )
                    service_name = "RTVI-Embed" if backend == "rtvi_embed" else "RTVI-VLM"
                    json_path = build_and_save(
                        out_path,
                        service_name,
                        test_cases,
                        config_id=effective_config_id,
                        platform=platform,
                        config=result_config,
                        benchmark_name=scenario_name,
                        benchmark_mode=benchmark_mode,
                        triggered_by=getattr(args, "triggered_by", "manual"),
                        pipeline_url=(getattr(args, "pipeline_url", None) or "").strip() or "",
                        duration_seconds=duration_seconds,
                        release=(global_config.get("release") or "").strip(),
                        passed=passed,
                        failed=failed,
                    )
                    logger.info("VSS result JSON: %s", json_path)
                    if getattr(args, "upload", False):
                        if upload_result_file(str(json_path), service_name):
                            logger.info("Uploaded to MinIO: %s/%s", service_name, json_path.name)
                        else:
                            logger.warning(
                                "Upload failed. Optional: set MINIO_ACCESS_KEY, MINIO_SECRET_KEY if your "
                                "server does not use default (minioadmin). Check MINIO_ENDPOINT is reachable."
                            )
            except Exception as e:
                logger.warning("VSS JSON/upload failed: %s", e)

    except Exception as e:
        scenario_result.update({"success": False, "error": str(e)})
        logger.error(f"Scenario '{scenario_name}' failed: {e}")

    finally:
        scenario_result["execution_time_seconds"] = time.time() - start_time

        # Cleanup benchmark resources
        try:
            benchmark.cleanup_resources()
        except Exception as e:
            logger.error(f"Error during cleanup: {e}")
            scenario_result.update(
                {
                    "success": False,
                    "error": f"cleanup failed after scenario '{scenario_name}': {e}",
                    "cleanup_failed": True,
                }
            )

        if benchmark_mode in {"max_live_streams", "concurrent_live_streams", "single_live_stream"}:
            try:
                benchmark.assert_no_active_streams(f"after scenario '{scenario_name}' cleanup")
            except Exception as e:
                logger.error(f"Post-scenario live-stream cleanup verification failed: {e}")
                previous_error = scenario_result.get("error")
                merged_error = (
                    f"{previous_error}; post-scenario verification failed: {e}"
                    if previous_error
                    else f"post-scenario verification failed: {e}"
                )
                scenario_result.update(
                    {
                        "success": False,
                        "error": merged_error,
                        "cleanup_failed": True,
                    }
                )

    return scenario_result


def main():
    """Main entry point"""
    load_dotenv()

    parser = argparse.ArgumentParser(
        description="RTVI Performance Test Harness",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Examples:
  # Run with default config file
  python rtvi_perf_benchmark.py

  # Run with custom config file
  python rtvi_perf_benchmark.py --config my_config.yaml

  # Run specific test scenario
  python rtvi_perf_benchmark.py --scenario single_file_test

  # Run with custom config and scenario
  python rtvi_perf_benchmark.py --config my_config.yaml --scenario file_burst_test
        """,
    )
    parser.add_argument(
        "--config",
        type=str,
        default="config.yaml",
        help="Path to YAML configuration file (default: config.yaml)",
    )
    parser.add_argument(
        "--scenario",
        type=str,
        help="Test scenario to run (if not specified, runs all scenarios in config)",
    )
    parser.add_argument(
        "--list-scenarios", action="store_true", help="List available test scenarios and exit"
    )
    parser.add_argument(
        "--debug", action="store_true", help="Enable debug logging including API request payloads"
    )
    parser.add_argument(
        "--list-modes", action="store_true", help="List available benchmark modes and exit"
    )
    parser.add_argument(
        "--initial-stream-count",
        type=int,
        default=None,
        metavar="N",
        help="Override initial_stream_count for max_live_streams scenarios (must be > 0)",
    )
    parser.add_argument(
        "--add-stream-count",
        type=int,
        default=None,
        metavar="N",
        help="Override add_stream_count for max_live_streams scenarios (must be >= 1)",
    )
    parser.add_argument(
        "--binary-search-refinement",
        action=argparse.BooleanOptionalAction,
        default=None,
        help=(
            "Override binary_search_refinement for max_live_streams scenarios "
            "(--binary-search-refinement to enable, --no-binary-search-refinement to disable)"
        ),
    )
    parser.add_argument(
        "--concurrency-levels",
        type=int,
        nargs="+",
        metavar="N",
        default=None,
        help=(
            "Override concurrency levels for concurrent_live_streams (stream_count), "
            "file_burst, and concurrency scenarios. "
            "Example: --concurrency-levels 1 5 10 20"
        ),
    )
    parser.add_argument(
        "--output-json",
        type=str,
        default=None,
        help="Path to output JSON file (default: None)",
    )
    parser.add_argument("--upload", action="store_true", help="Upload result JSON to MinIO")
    parser.add_argument(
        "--dashboard-json",
        type=str,
        default=None,
        help=(
            "Path to output dashboard-compatible JSON after all scenarios complete. "
            "Produces XLSX-mirroring test cases with all metrics from all scenarios."
        ),
    )
    parser.add_argument(
        "--config-id",
        type=str,
        default=None,
        help="Override config_id for VSS result JSON (default: None)",
    )

    args = parser.parse_args()

    # Configure logging
    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
        handlers=[logging.StreamHandler()],
    )

    # List benchmark modes if requested
    if args.list_modes:
        logger.info("Available benchmark modes:")
        for mode in BENCHMARK_REGISTRY.keys():
            logger.info(f"  {mode}")
        sys.exit(0)

    # Load configuration using base class functionality
    try:
        # Use any benchmark class to load config (they all have the same method)
        temp_benchmark = SingleFileBenchmark("", "")
        config = temp_benchmark.load_config_file(args.config)
    except (FileNotFoundError, ValueError) as e:
        logger.error(f"Error loading configuration: {e}")
        sys.exit(1)

    # List scenarios if requested
    if args.list_scenarios:
        logger.info("Available test scenarios:")
        for scenario_name, scenario_config in config["test_scenarios"].items():
            description = scenario_config.get("description", "No description")
            benchmark_mode = scenario_config.get("benchmark_mode", "single_file")
            video_count = len(scenario_config.get("videos", []))
            logger.info(
                f"  {scenario_name}: {description} (mode: {benchmark_mode}, {video_count} video files)"
            )
        sys.exit(0)

    # Get global configuration
    global_config = config["global"]

    # Get RTVI backend URL (environment variable takes precedence)
    base_url = os.environ.get(
        "RTVI_BACKEND",
        global_config.get("rtvi_backend", "http://localhost:8000"),
    )
    output_base_dir = global_config.get("output_dir", "rtvi-perf-report")

    logger.info(f"Using RTVI backend: {base_url}")
    logger.info(f"Output directory: {output_base_dir}")

    # Determine scenarios to run
    scenarios_to_run = {}
    if args.scenario:
        if args.scenario not in config["test_scenarios"]:
            logger.error(f"Error: Scenario '{args.scenario}' not found in configuration")
            logger.error("Available scenarios: %s", list(config["test_scenarios"].keys()))
            sys.exit(1)
        scenarios_to_run[args.scenario] = config["test_scenarios"][args.scenario]
    else:
        scenarios_to_run = config["test_scenarios"]

    # Log GPU information
    if global_config.get("gpu_monitoring", {}).get("enabled", False):
        vlm_gpus = global_config.get("vlm_gpus", [])
        log_gpu_information(vlm_gpus)

    # Run each scenario
    scenario_results = []
    total_start_time = time.time()

    logger.info("")
    logger.info("=== Starting Benchmark Suite ===")
    logger.info(f"Total scenarios to run: {len(scenarios_to_run)}")

    for scenario_name, scenario_config in scenarios_to_run.items():
        scenario_result = run_single_scenario(
            args,
            scenario_name,
            scenario_config,
            global_config,
            base_url,
            output_base_dir,
            initial_stream_count=args.initial_stream_count,
            add_stream_count=args.add_stream_count,
            binary_search_refinement=args.binary_search_refinement,
            concurrency_levels=args.concurrency_levels,
        )
        scenario_results.append(scenario_result)

        if scenario_result.get("cleanup_failed"):
            logger.error(
                "Aborting benchmark suite because scenario cleanup failed. "
                "Later scenarios may be contaminated by stale server state."
            )
            break

        # Brief pause between scenarios
        time.sleep(2)

    total_execution_time = time.time() - total_start_time

    # Print overall summary
    successful_scenarios = sum(1 for r in scenario_results if r["success"])
    failed_scenarios = len(scenario_results) - successful_scenarios

    logger.info("")
    logger.info("=" * 60)
    logger.info("BENCHMARK SUITE COMPLETED")
    logger.info("=" * 60)
    logger.info(f"Total execution time: {total_execution_time:.1f} seconds")
    logger.info(f"Scenarios run: {len(scenario_results)}")
    logger.info(f"Successful: {successful_scenarios}")
    logger.info(f"Failed: {failed_scenarios}")

    if failed_scenarios > 0:
        logger.error("")
        logger.error("Failed scenarios:")
        for result in scenario_results:
            if not result["success"]:
                logger.error(
                    f"  - {result['scenario_name']}: {result.get('error', 'Unknown error')}"
                )

    logger.info("")
    logger.debug(f"Individual scenario results available in: {output_base_dir}")
    logger.info("")
    logger.info("Generated reports:")
    for result in scenario_results:
        if result["success"] and "report_file" in result:
            logger.info(f"  - {result['scenario_name']}: {result['report_file']}")

    # Dashboard JSON: build from output_base_dir after all scenarios complete
    if getattr(args, "dashboard_json", None) and successful_scenarios > 0:
        try:
            effective_config_id = getattr(args, "config_id", None) or global_config.get(
                "config_id", ""
            )
            backend = global_config.get("backend_type", "rtvi_vlm")
            if backend != "rtvi_embed":
                dashboard_tc = build_dashboard_test_cases(output_base_dir, effective_config_id)
                if dashboard_tc:
                    config_id = normalize_config_id(effective_config_id)
                    try:
                        plat = discover_platform(
                            config_id=config_id,
                            topology=global_config.get("topology"),
                        )
                    except Exception:
                        vlm_gpus = global_config.get("vlm_gpus") or []
                        llm_gpus = global_config.get("llm_gpus") or []
                        gpu_count = len(vlm_gpus) + len(llm_gpus) or 1
                        plat = {"gpu": {"model": "Unknown", "count": gpu_count}}
                    out_path = (
                        args.dashboard_json
                        if os.path.isabs(args.dashboard_json)
                        else os.path.join(output_base_dir, args.dashboard_json)
                    )
                    json_path = build_and_save(
                        out_path,
                        "RTVI-VLM",
                        dashboard_tc,
                        config_id=config_id,
                        platform=plat,
                        config={
                            "config_id": config_id,
                            "output_dir": output_base_dir,
                        },
                        benchmark_name=f"rtvi_vlm_{config_id}",
                        benchmark_mode="dashboard",
                        triggered_by=getattr(args, "triggered_by", "manual"),
                        pipeline_url=((getattr(args, "pipeline_url", None) or "").strip() or ""),
                        duration_seconds=total_execution_time,
                        release=(global_config.get("release") or "").strip(),
                        passed=len(dashboard_tc),
                        failed=0,
                    )
                    logger.info("Dashboard JSON: %s (%d test cases)", json_path, len(dashboard_tc))
                    if getattr(args, "upload", False):
                        if upload_result_file(str(json_path), "RTVI-VLM"):
                            logger.info(
                                "Uploaded dashboard JSON to MinIO: RTVI-VLM/%s",
                                json_path.name,
                            )
                        else:
                            logger.warning("Dashboard JSON upload to MinIO failed")
        except Exception as e:
            logger.warning("Dashboard JSON generation failed: %s", e)

    # Exit with appropriate code
    sys.exit(0 if failed_scenarios == 0 else 1)


if __name__ == "__main__":
    main()
