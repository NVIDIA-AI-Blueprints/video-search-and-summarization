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

import argparse
import logging
import os
import sys
import time
from typing import Any, Dict, List, Optional

from base import BenchmarkBase
from dotenv import load_dotenv
from file_burst_benchmark import FileBurstBenchmark
from single_file_benchmark import SingleFileBenchmark
from vss_perf_common import (
    build_and_save,
    discover_platform,
    get_nvidia_smi_gpu_name,
    perf_row_metadata,
    resolve_perf_config,
    upload_result_file,
)
from vss_perf_lvs_adapter import execution_results_to_test_cases

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


def _split_config_id(config_id: str) -> list[str]:
    """Split LVS config_id (dash or underscore separated)."""
    text = (config_id or "").strip()
    if not text:
        return []
    if "-" in text:
        return text.split("-")
    return text.split("_")


def _gpu_count_from_config(config_id: str, global_config: Dict) -> int:
    """Benchmark GPU count from config_id (e.g. h100-4-2x2-... → 4) or vlm/llm GPU lists."""
    explicit = global_config.get("gpu_count")
    if explicit is not None and str(explicit).strip() != "":
        return int(explicit)

    parts = _split_config_id(config_id)
    if len(parts) >= 2 and parts[1].isdigit():
        return int(parts[1])

    vlm_gpus = global_config.get("vlm_gpus") or []
    llm_gpus = global_config.get("llm_gpus") or []
    unique = set(vlm_gpus + llm_gpus)
    if unique:
        return len(unique)

    raise ValueError(
        "Cannot determine gpu_count: use --config-id like h100-4-2x2-... "
        "or set global gpu_count / vlm_gpus+llm_gpus."
    )


def _gpu_topology_from_config(config_id: str, global_config: Dict) -> str:
    """GPU topology from global config or config_id (e.g. h100-4-2x2-... → 2x2)."""
    topo = str(global_config.get("topology") or "").strip()
    if topo:
        return topo
    parts = _split_config_id(config_id)
    if len(parts) >= 3:
        return parts[2]
    return ""


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


def write_vss_result_json(
    args,
    global_config: Dict,
    runtime_metadata: Dict,
    config_id: str,
    test_cases: List[Dict[str, Any]],
    *,
    benchmark_names: List[str],
    benchmark_modes: List[str],
    output_base_dir: str,
    duration_seconds: float,
    passed: int,
    failed: int,
) -> Optional[str]:
    """Write (and optionally upload) one combined VSS result JSON for all scenarios."""
    if not test_cases or not getattr(args, "output_json", None):
        return None
    try:
        gpu_count = _gpu_count_from_config(config_id, global_config)
        gpu_topology = (
            str(runtime_metadata.get("gpu_topology") or "").strip()
            or _gpu_topology_from_config(config_id, global_config)
        )
        try:
            platform = discover_platform(config_id=config_id)
        except Exception:
            platform = {"gpu": {"model": "Unknown", "count": gpu_count}}
        gpu_info = platform.get("gpu", {}) if isinstance(platform, dict) else {}
        gpu_model = str(
            runtime_metadata.get("gpu_model")
            or gpu_info.get("full_name")
            or gpu_info.get("model")
            or ""
        )
        if isinstance(platform, dict):
            gpu_block = platform.setdefault("gpu", {})
            gpu_block["count"] = gpu_count
            if gpu_topology:
                gpu_block["topology"] = gpu_topology
            if gpu_model:
                gpu_block["model"] = gpu_model
        result_config = {
            "config_id": config_id,
            "gpu_topology": gpu_topology,
            "vlm": str(runtime_metadata.get("vlm_model") or ""),
            "llm": str(runtime_metadata.get("llm_model") or ""),
            "vision_input_tokens": str(runtime_metadata.get("vision_input_tokens") or "9k"),
        }
        out_path = (
            args.output_json
            if os.path.isabs(args.output_json)
            else os.path.join(output_base_dir, args.output_json)
        )
        json_path = build_and_save(
            out_path,
            "LVS",
            test_cases,
            config_id=config_id,
            platform=platform,
            config=result_config,
            benchmark_name=",".join(benchmark_names),
            benchmark_mode=",".join(dict.fromkeys(benchmark_modes)),
            triggered_by=getattr(args, "triggered_by", "manual"),
            pipeline_url=(getattr(args, "pipeline_url", None) or "").strip() or "",
            duration_seconds=duration_seconds,
            release=(getattr(args, "release", None) or global_config.get("release") or "").strip(),
            passed=passed,
            failed=failed,
        )
        logger.info("VSS result JSON: %s", json_path)
        if getattr(args, "upload", False):
            if upload_result_file(str(json_path), "LVS"):
                logger.info("Uploaded to MinIO: LVS/%s", json_path.name)
            else:
                raise RuntimeError(
                    "Upload failed. Check MinIO credentials and ensure MINIO_ENDPOINT is reachable."
                )
        return str(json_path)
    except Exception as e:
        logger.error("VSS JSON/upload failed: %s", e)
        return None


def run_single_scenario(
    scenario_name: str,
    scenario_config: Dict,
    global_config: Dict,
    runtime_metadata: Dict,
    base_url: str,
    output_base_dir: str,
    args,
) -> Dict:
    """Run a single test scenario"""
    effective_config_id = (args.config_id or "").strip() or "default"

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
        failed = execution_results.get("failed_test_cases", max(total - successful, 0))
        scenario_success = total > 0 and successful == total and failed == 0

        # Update scenario result
        result_update = {
            "success": scenario_success,
            "results_dir": results_dir,
            "report_file": report_file,
            "total_test_cases": total,
            "successful_test_cases": successful,
            "failed_test_cases": failed,
        }

        # Add explicit failure reason with counts for clearer CI diagnostics.
        if not scenario_success:
            if total == 0:
                result_update["error"] = "No test cases were executed"
            else:
                result_update["error"] = f"{failed}/{total} test case(s) failed"

        scenario_result.update(result_update)

        if scenario_success:
            logger.info(f"Scenario '{scenario_name}' completed successfully!")
        else:
            failed_case_ids = [
                tc.get("test_case_id", "<unknown>")
                for tc in execution_results.get("test_cases", [])
                if not tc.get("success", False)
            ]
            logger.error(
                "[SCENARIO_FAILURE] Scenario '%s' failed: %s (passed=%s/%s, failed=%s)",
                scenario_name,
                result_update.get("error", "unknown reason"),
                successful,
                total,
                failed,
            )
            if failed_case_ids:
                logger.error(
                    "[SCENARIO_FAILURE] Failed test_case_ids: %s", ", ".join(failed_case_ids)
                )
        logger.info(f"Results: {successful}/{total} test cases passed")
        logger.info(f"Report generated: {report_file}")

        # Collect every test case, including failures, for combined JSON output.
        if getattr(args, "output_json", None):
            try:
                test_cases = execution_results_to_test_cases(benchmark, execution_results)
                if test_cases:
                    scenario_result["vss_test_cases"] = test_cases
            except Exception as e:
                scenario_result["result_conversion_error"] = str(e)
                logger.error("VSS test case conversion failed: %s", e)

    except Exception as e:
        scenario_result.update({"success": False, "error": str(e)})
        logger.error(f"Scenario '{scenario_name}' failed: {e}")

    finally:
        scenario_result["execution_time_seconds"] = time.time() - start_time
        scenario_status = "success" if scenario_result.get("success", False) else "failure"
        logger.info(
            "[SCENARIO_COMPLETE] status=%s scenario=%s benchmark_mode=%s config_id=%s "
            "passed=%s/%s failed=%s duration_seconds=%.1f",
            scenario_status,
            scenario_name,
            benchmark_mode,
            effective_config_id,
            scenario_result.get("successful_test_cases", 0),
            scenario_result.get("total_test_cases", 0),
            scenario_result.get("failed_test_cases", 0),
            scenario_result["execution_time_seconds"],
        )

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

  # Export VSS result JSON and upload to MinIO (CI)
  python vss_perf_benchmark.py --output-json benchmark_results --upload --config-id tp1-9k \\
      --triggered-by ci_pipeline --pipeline-url "$BUILD_URL"
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
        help="Test scenario(s) to run. Multiple: --scenario s1 s2. Omit to run all in config.",
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
        "--vlm-gpus",
        type=str,
        default=None,
        help="Override VLM GPU list (comma-separated, e.g., 0,1,2)",
    )
    parser.add_argument(
        "--llm-gpus",
        type=str,
        default=None,
        help="Override LLM GPU list (comma-separated, e.g., 3,4)",
    )
    parser.add_argument(
        "--output-json",
        type=str,
        default=None,
        metavar="PATH",
        help="Write VSS result JSON here (dir or file path). Standard schema + LVS metrics.",
    )
    parser.add_argument(
        "--upload",
        action="store_true",
        help="Upload the result JSON to MinIO (use with --output-json).",
    )
    parser.add_argument(
        "--config-id",
        type=str,
        default=None,
        help="Canonical config id from ci/perf-configs.yaml (required for --output-json).",
    )
    parser.add_argument(
        "--compose-file",
        type=str,
        default=None,
        help="Compose file path used for perf-configs lookup when --config-id is omitted.",
    )
    parser.add_argument(
        "--triggered-by",
        type=str,
        default="manual",
        choices=["ci_pipeline", "manual", "scheduled"],
        help="Trigger source for result metadata (default: %(default)s).",
    )
    parser.add_argument(
        "--pipeline-url",
        type=str,
        default=None,
        help="CI pipeline/build URL to embed in result JSON (optional).",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default=None,
        help="Override the output directory for benchmark results (default: from config.yaml)",
    )
    parser.add_argument(
        "--vlm-model",
        type=str,
        default=None,
        help="Optional VLM model metadata for result JSON.",
    )
    parser.add_argument(
        "--llm-model",
        type=str,
        default=None,
        help="Optional LLM model metadata for result JSON.",
    )
    parser.add_argument(
        "--vision-input-tokens",
        type=str,
        default=None,
        help="Optional vision input token metadata for result JSON (e.g., 9k).",
    )
    parser.add_argument(
        "--gpu-model",
        type=str,
        default=None,
        help="Optional GPU model override for result metadata config block.",
    )
    parser.add_argument(
        "--release",
        type=str,
        default=None,
        help="Release identifier for result metadata (e.g., the LVS image tag). Overrides config.yaml.",
    )

    args = parser.parse_args()

    # Configure logging with color coding
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(ColoredFormatter("%(asctime)s - %(levelname)s - %(message)s"))
    console_handler.setLevel(logging.DEBUG if args.debug else logging.INFO)

    # Set root logger to WARNING to suppress third-party debug logs
    logging.basicConfig(
        level=logging.WARNING if args.debug else logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
        handlers=[console_handler],
    )

    # Enable DEBUG only for our benchmark modules when --debug is set
    if args.debug:
        logging.getLogger(__name__).setLevel(logging.DEBUG)
        # Add other benchmark module names here
        for module in [
            "base",
            "single_file_benchmark",
            "file_burst_benchmark",
            "gpu_monitor",
            "cpu_monitor",
            "latency_tracker",
            "BenchmarkBase",
            "SingleFileBenchmark",
            "FileBurstBenchmark",
        ]:
            logging.getLogger(module).setLevel(logging.DEBUG)

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

    def parse_gpu_list(gpu_str: str, label: str):
        if gpu_str is None:
            return None
        if gpu_str.strip() == "":
            return []
        try:
            return [int(x.strip()) for x in gpu_str.split(",") if x.strip() != ""]
        except ValueError as e:
            raise ValueError(f"Invalid {label} GPU list: '{gpu_str}'") from e

    # Optional GPU overrides
    vlm_override = parse_gpu_list(
        args.vlm_gpus if args.vlm_gpus else os.environ.get("VIA_VLM_GPUS"), "VLM"
    )
    llm_override = parse_gpu_list(
        args.llm_gpus if args.llm_gpus else os.environ.get("VIA_LLM_GPUS"), "LLM"
    )
    if vlm_override is not None:
        global_config["vlm_gpus"] = vlm_override
    if llm_override is not None:
        global_config["llm_gpus"] = llm_override

    compose_path = (
        args.compose_file
        or os.environ.get("COMPOSE_FILE_PATH")
        or os.environ.get("COMPOSE_FILE")
        or ""
    ).strip()
    config_id = (args.config_id or os.environ.get("CONFIG_ID") or "").strip()
    gpu_smi_name = get_nvidia_smi_gpu_name()

    perf_row = None
    if config_id or compose_path:
        perf_row = resolve_perf_config(
            config_id=config_id,
            compose_path=compose_path,
            gpu_name=gpu_smi_name,
        )

    row_meta = None
    if perf_row:
        row_meta = perf_row_metadata(
            perf_row,
            vlm_gpus=vlm_override,
            llm_gpus=llm_override,
        )
        # Explicit CLI/environment assignments override the profile. Otherwise,
        # monitor the same canonical assignments used by the deployment.
        global_config["vlm_gpus"] = row_meta["vlm_gpu_ids"]
        global_config["llm_gpus"] = row_meta["llm_gpu_ids"]

    if args.output_json:
        if not perf_row:
            raise ValueError(
                "Could not resolve perf config for JSON output. "
                f"config_id={config_id!r}, compose_file={compose_path!r}, "
                f"gpu={gpu_smi_name!r}. Check ci/perf-configs.yaml."
            )
        config_id = row_meta["config_id"]
        args.config_id = config_id
        runtime_metadata = {
            "vlm_gpu_ids": row_meta["vlm_gpu_ids"],
            "llm_gpu_ids": row_meta["llm_gpu_ids"],
            "vlm_model": row_meta["vlm_model"],
            "llm_model": row_meta["llm_model"],
            "vision_input_tokens": row_meta["vision_input_tokens"],
            "gpu_topology": row_meta["gpu_topology"],
            "gpu_model": gpu_smi_name,
        }
        logger.info("Resolved perf config: %s", config_id)
    else:
        runtime_metadata = {
            "vlm_gpu_ids": vlm_override,
            "llm_gpu_ids": llm_override,
            "vlm_model": (args.vlm_model or os.environ.get("VIA_VLM_MODEL") or "").strip(),
            "llm_model": (args.llm_model or os.environ.get("VIA_LLM_MODEL") or "").strip(),
            "vision_input_tokens": (
                args.vision_input_tokens or os.environ.get("VIA_VISION_INPUT_TOKENS") or "9k"
            ).strip(),
            "gpu_topology": "",
            "gpu_model": gpu_smi_name,
        }

    # Get VSS backend URL (environment variable takes precedence)
    base_url = os.environ.get(
        "VIA_BACKEND", global_config.get("vss_backend", "http://localhost:8000")
    )
    output_base_dir = os.environ.get(
        "VIA_OUTPUT_DIR", global_config.get("output_dir", "vss-perf-report")
    )
    if args.output_dir:
        output_base_dir = args.output_dir

    if os.environ.get("VIA_VLM_GPUS"):
        global_config["vlm_gpus"] = [int(g) for g in os.environ["VIA_VLM_GPUS"].split(",")]
    if os.environ.get("VIA_LLM_GPUS"):
        global_config["llm_gpus"] = [int(g) for g in os.environ["VIA_LLM_GPUS"].split(",")]

    logger.info(f"Using VSS backend: {base_url}")
    logger.info(f"Output directory: {output_base_dir}")
    logger.info(f"VLM GPUs: {global_config.get('vlm_gpus', [])}")
    logger.info(f"LLM GPUs: {global_config.get('llm_gpus', [])}")

    if os.path.exists(output_base_dir) and os.listdir(output_base_dir):
        logger.error(
            f"Output directory '{output_base_dir}' already exists and is not empty. "
            "Remove it before running the benchmark."
        )
        sys.exit(1)

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
            scenario_name,
            scenario_config,
            global_config,
            runtime_metadata,
            base_url,
            output_base_dir,
            args,
        )
        scenario_results.append(scenario_result)

        # Brief pause between scenarios
        time.sleep(2)

    total_execution_time = time.time() - total_start_time

    result_output_failed = False
    if getattr(args, "output_json", None):
        combined_test_cases: List[Dict[str, Any]] = []
        for result in scenario_results:
            cases = result.get("vss_test_cases") or []
            if cases:
                combined_test_cases.extend(cases)
            elif not result.get("success", False):
                combined_test_cases.append(
                    {
                        "test_case_id": f"{result['scenario_name']}_scenario",
                        "description": f"Scenario {result['scenario_name']}",
                        "status": "FAIL",
                        "error": result.get("error") or "Scenario failed before producing test cases",
                        "config": {},
                        "metrics": {},
                    }
                )

        effective_config_id = (args.config_id or "").strip() or "default"
        total_passed = sum(r.get("successful_test_cases", 0) for r in scenario_results)
        total_failed = sum(
            r.get("failed_test_cases", 1 if not r.get("success", False) else 0)
            for r in scenario_results
        )
        json_path = write_vss_result_json(
            args,
            global_config,
            runtime_metadata,
            effective_config_id,
            combined_test_cases,
            benchmark_names=[r["scenario_name"] for r in scenario_results],
            benchmark_modes=[r["benchmark_mode"] for r in scenario_results],
            output_base_dir=output_base_dir,
            duration_seconds=total_execution_time,
            passed=total_passed,
            failed=total_failed,
        )
        result_output_failed = json_path is None or any(
            r.get("result_conversion_error") for r in scenario_results
        )

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
    sys.exit(0 if failed_scenarios == 0 and not result_output_failed else 1)


if __name__ == "__main__":
    main()
