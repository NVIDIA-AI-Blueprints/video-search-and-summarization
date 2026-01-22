######################################################################################################
# SPDX-FileCopyrightText: Copyright (c) 2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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
import logging
import os
import sys
import time
from typing import Dict

from alert_review_benchmark import AlertReviewBenchmark
from base import BenchmarkBase
from chat_completions_benchmark import ChatCompletionsBenchmark
from dotenv import load_dotenv
from file_burst_benchmark import FileBurstBenchmark
from live_streams_benchmark import LiveStreamsBenchmark
from single_file_benchmark import SingleFileBenchmark
from vlm_captions_benchmark import VlmCaptionsBenchmark

# Setup module-level logger
logger = logging.getLogger(__name__)


class ColoredFormatter(logging.Formatter):
    """Custom formatter that adds color coding to log levels"""

    # ANSI color codes
    COLORS = {
        "DEBUG": "\033[36m",  # Cyan
        "INFO": "\033[32m",  # Green
        "WARNING": "\033[33m",  # Yellow
        "ERROR": "\033[31m",  # Red
        "CRITICAL": "\033[35m",  # Magenta
    }
    RESET = "\033[0m"
    BOLD = "\033[1m"

    def format(self, record):
        # Save original levelname
        original_levelname = record.levelname

        # Add color to levelname
        if record.levelname in self.COLORS:
            record.levelname = (
                f"{self.BOLD}{self.COLORS[record.levelname]}{record.levelname}{self.RESET}"
            )

        # Format the message
        result = super().format(record)

        # Restore original levelname
        record.levelname = original_levelname

        return result


# Benchmark registry mapping benchmark modes to implementation classes
BENCHMARK_REGISTRY = {
    "single_file": SingleFileBenchmark,
    "file_burst": FileBurstBenchmark,
    "max_live_streams": LiveStreamsBenchmark,
    "alert_review_burst": AlertReviewBenchmark,
    "vlm_captions_burst": VlmCaptionsBenchmark,
    "chat_completions_burst": ChatCompletionsBenchmark,
}


def log_gpu_information(vlm_gpus=None, llm_gpus=None):
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
                    if llm_gpus and i in llm_gpus:
                        usage.append("LLM")
                    if usage:
                        logger.info(f"    - Used By: {', '.join(usage)}")

                except Exception as e:
                    logger.error(f"    - Error getting additional info: {e}")

        logger.info("=" * 60)

        # Log which GPUs will be monitored
        if vlm_gpus or llm_gpus:
            all_gpu_ids = list(set((vlm_gpus or []) + (llm_gpus or [])))
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
    scenario_name: str,
    scenario_config: Dict,
    global_config: Dict,
    base_url: str,
    output_base_dir: str,
) -> Dict:
    """Run a single test scenario"""
    logger.info("")
    logger.info(f"=== Running scenario: {scenario_name} ===")
    logger.debug(f"Description: {scenario_config.get('description', 'No description')}")

    # Determine benchmark mode
    benchmark_mode = scenario_config.get("benchmark_mode", "single_file")
    logger.debug(f"Benchmark mode: {benchmark_mode}")

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

        # Add error message if scenario failed due to no passing tests
        if not scenario_success and total > 0:
            result_update["error"] = f"All {total} test case(s) failed"

        scenario_result.update(result_update)

        if scenario_success:
            logger.info(f"Scenario '{scenario_name}' completed successfully!")
        else:
            logger.error(f"Scenario '{scenario_name}' failed - no test cases passed!")
        logger.info(f"Results: {successful}/{total} test cases passed")
        logger.info(f"Report generated: {report_file}")

    except Exception as e:
        scenario_result.update({"success": False, "error": str(e)})
        logger.error(f"Scenario '{scenario_name}' failed: {e}")

    finally:
        scenario_result["execution_time_seconds"] = time.time() - start_time

        # Cleanup benchmark resources
        try:
            benchmark.cleanup_resources()
        except Exception as e:
            logger.warning(f"Error during cleanup: {e}")

    return scenario_result


def main():
    """Main entry point"""
    load_dotenv()

    parser = argparse.ArgumentParser(
        description="VSS Performance Test Harness",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Examples:
  # Run with default config file
  python vss_perf_benchmark.py

  # Run with custom config file
  python vss_perf_benchmark.py --config my_config.yaml

  # Run specific test scenario
  python vss_perf_benchmark.py --scenario single_file_test

  # Run multiple test scenarios
  python vss_perf_benchmark.py --scenario single_file_test file_burst_test

  # Run with custom config and scenarios
  python vss_perf_benchmark.py --config my_config.yaml --scenario scenario1 scenario2
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
        nargs="+",
        help="Test scenario(s) to run. Can specify multiple: --scenario scenario1 scenario2 "
        "(if not specified, runs all scenarios in config)",
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

    args = parser.parse_args()

    # Configure logging with color coding
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(ColoredFormatter("%(asctime)s - %(levelname)s - %(message)s"))
    console_handler.setLevel(logging.DEBUG if args.debug else logging.INFO)

    logging.basicConfig(
        level=logging.DEBUG,  # Root logger at DEBUG so file gets everything
        format="%(asctime)s - %(levelname)s - %(message)s",
        handlers=[console_handler],
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

    # Get VSS backend URL (environment variable takes precedence)
    base_url = os.environ.get(
        "VIA_BACKEND", global_config.get("vss_backend", "http://localhost:8000")
    )
    output_base_dir = global_config.get("output_dir", "vss-perf-report")

    logger.info(f"Using VSS backend: {base_url}")
    logger.info(f"Output directory: {output_base_dir}")

    # Setup file logging
    os.makedirs(output_base_dir, exist_ok=True)
    log_file_path = os.path.join(output_base_dir, "vss_perf_benchmark_log.txt")
    file_handler = logging.FileHandler(log_file_path, mode="w")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))

    # Add to root logger so all loggers inherit it
    root_logger = logging.getLogger()
    root_logger.addHandler(file_handler)

    logger.info(f"Benchmark logs will be saved to: {log_file_path}")

    # Determine scenarios to run
    scenarios_to_run = {}
    if args.scenario:
        # Validate all specified scenarios exist
        invalid_scenarios = [s for s in args.scenario if s not in config["test_scenarios"]]
        if invalid_scenarios:
            logger.error(f"Error: Scenario(s) not found in configuration: {invalid_scenarios}")
            logger.error("Available scenarios: %s", list(config["test_scenarios"].keys()))
            sys.exit(1)
        # Add all specified scenarios
        for scenario_name in args.scenario:
            scenarios_to_run[scenario_name] = config["test_scenarios"][scenario_name]
    else:
        scenarios_to_run = config["test_scenarios"]

    # Log GPU information
    if global_config.get("gpu_monitoring", {}).get("enabled", False):
        vlm_gpus = global_config.get("vlm_gpus", [])
        llm_gpus = global_config.get("llm_gpus", [])
        log_gpu_information(vlm_gpus, llm_gpus)

    # Run each scenario
    scenario_results = []
    total_start_time = time.time()

    logger.info("")
    logger.info("=== Starting Benchmark Suite ===")
    logger.info(f"Total scenarios to run: {len(scenarios_to_run)}")

    for scenario_name, scenario_config in scenarios_to_run.items():
        scenario_result = run_single_scenario(
            scenario_name, scenario_config, global_config, base_url, output_base_dir
        )
        scenario_results.append(scenario_result)

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
    logger.info(f"Detailed benchmark logs saved to: {log_file_path}")
    logger.info("")
    logger.info("Generated reports:")
    for result in scenario_results:
        if result["success"] and "report_file" in result:
            logger.info(f"  - {result['scenario_name']}: {result['report_file']}")

    # Exit with appropriate code
    sys.exit(0 if failed_scenarios == 0 else 1)


if __name__ == "__main__":
    main()
