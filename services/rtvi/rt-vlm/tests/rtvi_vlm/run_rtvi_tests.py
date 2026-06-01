#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2024-2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

"""
RTVI Test Harness Runner

Comprehensive test runner for RTVI VLM Server components:
- Unit tests
- Integration tests
- Performance benchmarks

Usage:
    python run_rtvi_tests.py [--unit] [--integration] [--perf] [--all]
    python run_rtvi_tests.py --unit --verbose
    python run_rtvi_tests.py --perf --config perf_config.yaml
"""

import argparse
import os
import subprocess
import sys
import time


def run_pytest(test_paths, markers=None, verbose=False, coverage=False, parallel=False):
    """Run pytest with specified options"""
    cmd = ["python3", "-m", "pytest"]

    if verbose:
        cmd.append("-v")
    else:
        cmd.append("-q")
    # Always add -s to show print statements (disable output capturing)
    cmd.append("-s")

    if markers:
        cmd.extend(["-m", markers])

    if coverage:
        cmd.extend(["--cov=server", "--cov=cli", "--cov-report=html", "--cov-report=term"])

    if parallel:
        try:
            import pytest_xdist  # noqa: F401

            cmd.extend(["-n", "auto"])
        except ImportError:
            print("Warning: pytest-xdist not installed, running sequentially")

    cmd.extend(test_paths)

    print(f"Running: {' '.join(cmd)}")
    # Run from parent tests/ directory so imports work
    tests_dir = os.path.dirname(os.path.dirname(__file__))
    # Add src/ directory to PYTHONPATH so server module can be imported
    project_root = os.path.dirname(tests_dir)
    src_dir = os.path.join(project_root, "src")
    env = os.environ.copy()
    # Prepend src/ to PYTHONPATH if it exists, otherwise set it
    if "PYTHONPATH" in env:
        env["PYTHONPATH"] = f"{src_dir}{os.pathsep}{env['PYTHONPATH']}"
    else:
        env["PYTHONPATH"] = src_dir
    print(f"PYTHONPATH: {env['PYTHONPATH']}")
    result = subprocess.run(cmd, cwd=tests_dir, env=env)
    return result.returncode == 0


def run_performance_benchmark(config_file=None):
    """Run performance benchmark"""
    if config_file is None:
        # Get the project root (two levels up from tests/rtvi_vlm/)
        project_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        config_file = os.path.join(project_root, "perf", "benchmark", "rtvi_vlm_config.yaml")

    if not os.path.exists(config_file):
        print(f"Error: Config file not found: {config_file}")
        return False

    # Get the project root (same as above)
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    benchmark_script = os.path.join(project_root, "perf", "benchmark", "rtvi_perf_benchmark.py")

    cmd = ["python3", benchmark_script, "--config", config_file]
    print(f"Running performance benchmark: {' '.join(cmd)}")
    result = subprocess.run(cmd)
    return result.returncode == 0


def main():
    parser = argparse.ArgumentParser(
        description="RTVI Test Harness - Run unit, integration, and performance tests"
    )
    parser.add_argument(
        "--unit",
        action="store_true",
        help="Run unit tests only",
    )
    parser.add_argument(
        "--integration",
        action="store_true",
        help="Run integration tests only",
    )
    parser.add_argument(
        "--perf",
        action="store_true",
        help="Run performance benchmarks",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Run all tests (unit + integration + perf)",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Verbose output",
    )
    parser.add_argument(
        "--coverage",
        action="store_true",
        help="Generate coverage report",
    )
    parser.add_argument(
        "--parallel",
        action="store_true",
        help="Run tests in parallel (requires pytest-xdist)",
    )
    parser.add_argument(
        "--config",
        type=str,
        help="Performance benchmark config file",
    )
    parser.add_argument(
        "--markers",
        type=str,
        help="Pytest markers to filter tests (e.g., 'no_gpu')",
    )
    parser.add_argument(
        "--server",
        type=str,
        default=os.environ.get("RTVI_BACKEND", "http://rtvi-server:8000"),
        help="Server URL for integration tests",
    )

    args = parser.parse_args()

    # Set server URL for integration tests
    os.environ["RTVI_BACKEND"] = args.server

    # Determine what to run
    run_unit = args.unit or args.all
    run_integration = args.integration or args.all
    run_perf = args.perf or args.all

    if not (run_unit or run_integration or run_perf):
        parser.print_help()
        print(
            "\nError: Must specify at least one test type (--unit, --integration, --perf, or --all)"
        )
        sys.exit(1)

    results = {}
    start_time = time.time()

    # Run unit tests
    if run_unit:
        print("\n" + "=" * 80)
        print("Running Unit Tests")
        print("=" * 80)
        unit_tests = [
            "test_rtvi_vlm_server.py",
            "test_rtvi_stream_handler.py",
            "test_rtvi_client_cli.py",
        ]
        unit_paths = [os.path.join(os.path.dirname(__file__), test) for test in unit_tests]
        results["unit"] = run_pytest(
            unit_paths,
            markers=args.markers,
            verbose=args.verbose,
            coverage=args.coverage,
            parallel=args.parallel,
        )

    # Run integration tests
    if run_integration:
        print("\n" + "=" * 80)
        print("Running Integration Tests")
        print("=" * 80)
        integration_tests = ["test_rtvi_integration.py"]
        integration_paths = [
            os.path.join(os.path.dirname(__file__), test) for test in integration_tests
        ]
        results["integration"] = run_pytest(
            integration_paths,
            markers=args.markers,
            verbose=args.verbose,
            parallel=args.parallel,
        )

    # Run performance benchmarks
    if run_perf:
        print("\n" + "=" * 80)
        print("Running Performance Benchmarks")
        print("=" * 80)
        results["perf"] = run_performance_benchmark(args.config)

    # Summary
    elapsed_time = time.time() - start_time
    print("\n" + "=" * 80)
    print("Test Summary")
    print("=" * 80)
    for test_type, passed in results.items():
        status = "PASSED" if passed else "FAILED"
        print(f"{test_type.upper()}: {status}")
    print(f"\nTotal time: {elapsed_time:.2f} seconds")

    # Exit with error if any tests failed
    if not all(results.values()):
        sys.exit(1)


if __name__ == "__main__":
    main()
