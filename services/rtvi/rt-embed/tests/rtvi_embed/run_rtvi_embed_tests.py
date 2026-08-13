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
import stat
import subprocess
import sys
import time


def _pytest_env(coverage=False):
    """Build cwd/env for pytest subprocess invocations."""
    this_file = os.path.abspath(__file__)
    tests_dir = os.path.dirname(os.path.dirname(this_file))
    workspace_root = os.path.dirname(tests_dir)
    src_path = os.path.join(workspace_root, "src")

    env = os.environ.copy()
    if "PYTHONPATH" in env:
        env["PYTHONPATH"] = f"{src_path}{os.pathsep}{env['PYTHONPATH']}"
    else:
        env["PYTHONPATH"] = src_path

    if coverage:
        _prepare_coverage_layout(tests_dir, env)

    return tests_dir, env


def _prepare_coverage_layout(tests_dir, env):
    """Original coverage layout: tests/.coverage and tests/htmlcov/ (pytest cwd)."""
    os.makedirs(os.path.join(tests_dir, "htmlcov"), exist_ok=True)
    env["COVERAGE_FILE"] = os.path.join(tests_dir, ".coverage")
    if os.access(tests_dir, os.W_OK):
        return
    try:
        writable = stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH
        os.chmod(tests_dir, os.stat(tests_dir).st_mode | writable)
        htmlcov = os.path.join(tests_dir, "htmlcov")
        if os.path.isdir(htmlcov):
            os.chmod(htmlcov, os.stat(htmlcov).st_mode | writable)
    except OSError:
        pass


def _pytest_succeeded(returncode):
    # pytest exit code 5 means no tests were collected for the given marker filter.
    return returncode in (0, 5)


def _invoke_pytest(
    test_paths,
    markers=None,
    verbose=False,
    coverage=False,
    parallel=False,
    cov_append=False,
    cov_report=True,
):
    """Run a single pytest subprocess."""
    cmd = ["python3", "-m", "pytest"]

    if verbose:
        cmd.append("-v")
    else:
        cmd.append("-q")

    if markers:
        cmd.extend(["-m", markers])

    if coverage:
        cmd.extend(["--cov=server", "--cov=cli"])
        if cov_report:
            cmd.extend(["--cov-report=html", "--cov-report=term"])
        if cov_append:
            cmd.append("--cov-append")

    if parallel:
        if importlib.util.find_spec("xdist") is not None:
            cmd.extend(["-n", "auto"])
        else:
            print("Warning: pytest-xdist not installed, running sequentially")

    cmd.extend(test_paths)

    tests_dir, env = _pytest_env(coverage=coverage)
    print(f"Running: {' '.join(cmd)}")
    print(f"PYTHONPATH: {env['PYTHONPATH']}")
    if coverage:
        print(f"COVERAGE_FILE: {env['COVERAGE_FILE']}")

    result = subprocess.run(cmd, cwd=tests_dir, env=env)
    return _pytest_succeeded(result.returncode)


def run_pytest(test_paths, markers=None, verbose=False, coverage=False, parallel=False):
    """Run pytest with specified options.

    When ``parallel`` is enabled without an explicit marker filter, run in two
    phases (NVBug 6183036): ``no_gpu`` tests in parallel via xdist, then GPU
    tests serially so only one worker loads the shared session RTVIServer/VLM.
    """
    if not parallel or markers:
        return _invoke_pytest(
            test_paths,
            markers=markers,
            verbose=verbose,
            coverage=coverage,
            parallel=parallel,
        )

    if importlib.util.find_spec("xdist") is None:
        print("Warning: pytest-xdist not installed, running sequentially")
        return _invoke_pytest(
            test_paths,
            verbose=verbose,
            coverage=coverage,
            parallel=False,
        )

    print(
        "Parallel mode: running no_gpu tests with xdist, then GPU tests serially "
        "(single RTVIServer session; NVBug 6183036)"
    )
    no_gpu_ok = _invoke_pytest(
        test_paths,
        markers="no_gpu",
        verbose=verbose,
        coverage=coverage,
        parallel=True,
        cov_report=not coverage,
    )
    gpu_ok = _invoke_pytest(
        test_paths,
        markers="not no_gpu",
        verbose=verbose,
        coverage=coverage,
        parallel=False,
        cov_append=coverage,
        cov_report=True,
    )
    return no_gpu_ok and gpu_ok


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
            "test_ce1_nim_backend.py",
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
