#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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
RTVI Embed Test Harness Runner

Comprehensive test runner for RTVI Embed Server components:
- Unit tests
- Integration tests
- Performance benchmarks

Usage:
    python run_rtvi_embed_tests.py [--unit] [--integration] [--perf] [--all]
    python run_rtvi_embed_tests.py --unit --verbose
    python run_rtvi_embed_tests.py --perf --config perf_config.yaml
"""

import argparse
import importlib.util
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

    if markers:
        cmd.extend(["-m", markers])

    if coverage:
        cmd.extend(["--cov=server", "--cov=cli", "--cov-report=html", "--cov-report=term"])

    if parallel:
        # Only enable xdist if the plugin is installed
        if importlib.util.find_spec("xdist") is not None:
            cmd.extend(["-n", "auto"])
        else:
            print("Warning: pytest-xdist not installed, running sequentially")

    cmd.extend(test_paths)

    print(f"Running: {' '.join(cmd)}")

    # Run from parent tests/ directory so imports work
    this_file = os.path.abspath(__file__)
    tests_dir = os.path.dirname(os.path.dirname(this_file))

    # Add src folder to PYTHONPATH
    workspace_root = os.path.dirname(tests_dir)
    src_path = os.path.join(workspace_root, "src")

    env = os.environ.copy()
    if "PYTHONPATH" in env:
        env["PYTHONPATH"] = f"{src_path}{os.pathsep}{env['PYTHONPATH']}"
    else:
        env["PYTHONPATH"] = src_path

    print(f"PYTHONPATH: {env['PYTHONPATH']}")

    result = subprocess.run(cmd, cwd=tests_dir, env=env)
    return result.returncode == 0


# def run_performance_benchmark(config_file=None):
#     """Run performance benchmark"""
#     if config_file is None:
#         config_file = os.path.join(
#             os.path.dirname(__file__), "..", "perf", "benchmark", "rtvi_embed_config.yaml"
#         )

#     if not os.path.exists(config_file):
#         print(f"Error: Config file not found: {config_file}")
#         return False

#     benchmark_script = os.path.join(
#         os.path.dirname(__file__), "..", "perf", "benchmark", "rtvi_embed_benchmark.py"
#     )

#     if not os.path.exists(benchmark_script):
#         print(f"Warning: Benchmark script not found: {benchmark_script}")
#         print("Skipping performance benchmarks")
#         return True

#     cmd = ["python3", benchmark_script, config_file]
#     print(f"Running performance benchmark: {' '.join(cmd)}")
#     result = subprocess.run(cmd)
#     return result.returncode == 0


def main():
    parser = argparse.ArgumentParser(
        description="RTVI Embed Test Harness - Run unit, integration, and performance tests"
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
        default="http://localhost:8000",
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

    # Compute absolute paths for test files
    this_dir = os.path.dirname(os.path.abspath(__file__))

    # Run unit tests
    if run_unit:
        print("\n" + "=" * 80)
        print("Running Unit Tests")
        print("=" * 80)
        unit_tests = [
            "test_rtvi_embed_server.py",
            "test_rtvi_embed_stream_handler.py",
            "test_rtvi_embed_client_cli.py",
            "test_stream_cv_api.py",
            "test_video_embeddings_base64.py",
            "test_video_embeddings_file_url.py",
            "test_video_embeddings_url_headers.py",
            "test_create_triton_model_repo.py",
        ]
        unit_paths = [os.path.join(this_dir, test) for test in unit_tests]
        # Filter to only existing test files
        unit_paths = [path for path in unit_paths if os.path.exists(path)]

        if not unit_paths:
            print("Warning: No unit test files found")
            results["unit"] = True
        else:
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
        integration_tests = [
            "test_rtvi_embed_integration.py",
        ]
        integration_paths = [os.path.join(this_dir, test) for test in integration_tests]
        # Filter to only existing test files
        integration_paths = [path for path in integration_paths if os.path.exists(path)]

        if not integration_paths:
            print("Warning: No integration test files found")
            results["integration"] = True
        else:
            results["integration"] = run_pytest(
                integration_paths,
                markers=args.markers,
                verbose=args.verbose,
                parallel=args.parallel,
            )

    # # Run performance benchmarks
    # if run_perf:
    #     print("\n" + "=" * 80)
    #     print("Running Performance Benchmarks")
    #     print("=" * 80)
    #     results["perf"] = run_performance_benchmark(args.config)

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
