# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

import json
import logging
import os
import pytest
from pathlib import Path

from scripts.container_monitor import get_monitor, cleanup_monitor
from scripts.nvenc_capacity import (
    NvencSlotPool,
    nvenc_slot_count,
    probe_nvenc_capacity,
    read_cached_capacity,
    write_cached_capacity,
)

logger = logging.getLogger(__name__)


def _load_config(config_path: str) -> dict:
    """Load configuration from JSON file."""
    config_file = Path(config_path)
    if not config_file.exists():
        return {}
    with open(config_file, 'r') as f:
        return json.load(f)


def pytest_addoption(parser):
    """Add custom command line options."""
    parser.addoption(
        "--config",
        action="store",
        default="config.json",
        help="Path to configuration file"
    )
    parser.addoption(
        "--base-url",
        action="store",
        default=None,
        help="VST API base URL (overrides config.json, e.g., http://10.24.217.99:30888)"
    )
    parser.addoption(
        "--monitor-interval",
        action="store",
        default=None,
        type=int,
        help="Container monitoring interval in seconds (overrides config.json)"
    )
    parser.addoption(
        "--disable-container-monitor",
        action="store_true",
        default=False,
        help="Disable container resource monitoring"
    )
    parser.addoption(
        "--perf-iterations",
        action="store",
        default=None,
        type=int,
        help="Number of iterations per scenario for performance/latency tests (overrides config.json)"
    )
    parser.addoption(
        "--perf-output-json",
        action="store",
        default=None,
        metavar="PATH",
        help="Write VSS result JSON to this path (directory or file). Enables dashboard integration."
    )
    parser.addoption(
        "--perf-upload",
        action="store_true",
        default=False,
        help="Upload the VSS result JSON to MinIO (use with --perf-output-json)."
    )
    parser.addoption(
        "--perf-config-id",
        action="store",
        default="h100-rtsp",
        help="Platform config id for VSS result metadata (default: h100-rtsp)."
    )
    parser.addoption(
        "--perf-release",
        action="store",
        default="",
        help="Release identifier to embed in VSS result metadata (e.g. EA1, EA2)."
    )


def pytest_configure(config):
    """Configure pytest options, including dynamic paths for container environment."""
    # Check if we're running in container environment
    container_reports_dir = Path("/app/reports")
    if container_reports_dir.exists() and container_reports_dir.is_dir():
        # We're in container - update paths to use the mounted directory
        container_reports_dir.mkdir(parents=True, exist_ok=True)

        # Update CSV path (only if it's the default from pyproject.toml, not an explicit --csv override)
        if hasattr(config.option, 'csv_path') and config.option.csv_path:
            default_csv = "reports/test_results.csv"
            if config.option.csv_path == default_csv:
                new_csv_path = str(container_reports_dir / "test_results.csv")
                config.option.csv_path = new_csv_path
                logger.info("Updated CSV output path for container: %s", new_csv_path)
        
        # Update HTML report path (only if it's the default from pyproject.toml, not an explicit --html override)
        if hasattr(config.option, 'htmlpath') and config.option.htmlpath:
            default_html = "reports/report.html"
            if config.option.htmlpath == default_html:
                new_html_path = str(container_reports_dir / "report.html")
                config.option.htmlpath = new_html_path
                logger.info("Updated HTML report path for container: %s", new_html_path)
        
        # Update JUnit XML path (only if it's the default from pyproject.toml, not an explicit --junitxml override)
        if hasattr(config.option, 'xmlpath') and config.option.xmlpath:
            default_xml = "reports/junit.xml"
            if config.option.xmlpath == default_xml:
                new_xml_path = str(container_reports_dir / "junit.xml")
                config.option.xmlpath = new_xml_path
                logger.info("Updated JUnit XML path for container: %s", new_xml_path)

    # NVENC capacity probe. Runs once on the xdist controller (or the
    # only process when xdist is not in use) and writes the result to a
    # small cache file under reports/. Workers read the cached value to
    # size their NvencSlotPool, so we never reprobe per worker. To force
    # a re-probe (e.g. after a driver upgrade) delete
    # reports/nvenc_capacity.txt.
    is_controller = "PYTEST_XDIST_WORKER" not in os.environ
    reports_dir = _reports_dir(config)
    if is_controller and read_cached_capacity(reports_dir) is None:
        capacity = probe_nvenc_capacity()
        write_cached_capacity(reports_dir, capacity)
        logger.info(
            "NVENC probe: capacity=%d, slot_count=%d (cached at %s)",
            capacity, nvenc_slot_count(capacity), reports_dir,
        )


def _reports_dir(config) -> Path:
    container_reports_dir = Path("/app/reports")
    if container_reports_dir.exists() and container_reports_dir.is_dir():
        return container_reports_dir
    return Path(config.rootdir) / "reports"


@pytest.fixture(scope="session")
def nvenc_pool(request):
    """Process-wide NVENC slot pool, sized from the cached probe result.

    Cross-process safe (lock files under reports/nvenc_slots/). Returns a
    pool with ``slot_count == 0`` when NVENC is unavailable or disabled --
    callers can still ``acquire()`` and will get an immediate ``None``,
    so test code never branches on the platform.
    """
    reports_dir = _reports_dir(request.config)
    capacity = read_cached_capacity(reports_dir) or 0
    slots = nvenc_slot_count(capacity)
    pool = NvencSlotPool(reports_dir / "nvenc_slots", slots)
    if slots > 0:
        logger.info(
            "NVENC slot pool: %d slots (probed_capacity=%d)",
            slots, capacity,
        )
    return pool


@pytest.fixture(scope="session")
def config(request):
    """Load configuration from JSON file."""
    config_path = request.config.getoption("--config")
    config_file = Path(config_path)
    
    if not config_file.exists():
        raise FileNotFoundError(f"Configuration file not found: {config_path}")
    
    with open(config_file, 'r') as f:
        config_data = json.load(f)
    return config_data


@pytest.fixture(scope="session")
def api_config(config, request):
    """
    Get API configuration.
    
    Priority:
    1. Command line --base-url flag
    2. Config file api.base_url
    """
    if 'api' not in config:
        raise KeyError("Missing 'api' key in configuration file")
    
    api_conf = config['api'].copy()
    
    # Override with CLI parameter if provided
    cli_base_url = request.config.getoption("--base-url")
    if cli_base_url:
        api_conf['base_url'] = cli_base_url
    
    return api_conf


@pytest.fixture(scope="session")
def perf_iterations(request, config):
    """
    Get performance test iterations.
    
    Priority:
    1. Command line --perf-iterations flag
    2. Config file latency_test_iterations
    3. Default: 10
    """
    cli_iterations = request.config.getoption("--perf-iterations")
    if cli_iterations is not None:
        return cli_iterations
    
    perf_config = config.get('tests', {}).get('performance_tests', {}).get('test_parameters', {})
    return perf_config.get('latency_test_iterations', 10)


def pytest_collection_modifyitems(config, items):
    """Reorder tests and skip mcp_gateway tests unless explicitly selected.

    MCP gateway tests require the vios-mcp container which is not always
    deployed.  They are skipped by default and can be included with:
        pytest -m mcp_gateway
    """
    # Skip mcp_gateway tests unless the user explicitly selected them via -m
    markexpr = config.getoption("-m", default="")
    run_mcp = "mcp_gateway" in markexpr
    run_longrun = "longrun" in markexpr
    run_iptables = "needs_iptables" in markexpr
    run_bbox = "needs_bbox_metadata" in markexpr

    skip_mcp = pytest.mark.skip(reason="MCP gateway tests skipped by default (use -m mcp_gateway)")
    skip_longrun = pytest.mark.skip(reason="Long-running test skipped by default (use -m longrun)")
    skip_iptables = pytest.mark.skip(reason="Test requires iptables/privileged runner (use -m needs_iptables)")
    skip_bbox = pytest.mark.skip(reason="Test requires stored bbox metadata (use -m needs_bbox_metadata)")

    priority_order = [
        "test_live_webrtc_stream",
        "test_replay_webrtc_stream",
    ]

    priority_buckets = {name: [] for name in priority_order}
    rest = []

    for item in items:
        if not run_mcp and item.get_closest_marker("mcp_gateway"):
            item.add_marker(skip_mcp)
        if not run_longrun and item.get_closest_marker("longrun"):
            item.add_marker(skip_longrun)
        if not run_iptables and item.get_closest_marker("needs_iptables"):
            item.add_marker(skip_iptables)
        if not run_bbox and item.get_closest_marker("needs_bbox_metadata"):
            item.add_marker(skip_bbox)

        module_name = item.module.__name__.rsplit(".", 1)[-1]
        if module_name in priority_buckets:
            priority_buckets[module_name].append(item)
        else:
            rest.append(item)

    items[:] = []
    for name in priority_order:
        items.extend(priority_buckets[name])
    items.extend(rest)


def pytest_sessionstart(session):
    """
    Called after the Session object has been created and before performing
    collection and entering the run test loop.
    """
    # Check CLI override first
    if session.config.getoption("--disable-container-monitor"):
        return

    # Load config to check container_monitoring settings
    config_path = session.config.getoption("--config")
    config = _load_config(config_path)
    monitoring_config = config.get('container_monitoring', {})

    # Check if monitoring is disabled in config
    if not monitoring_config.get('enabled', True):
        return

    # CLI option overrides config file
    cli_interval = session.config.getoption("--monitor-interval")
    interval = cli_interval if cli_interval is not None else monitoring_config.get('interval_seconds', 30)
    
    # Get the reports directory - use mounted volume if in container, otherwise relative to test root
    container_reports_dir = Path("/app/reports")
    if container_reports_dir.exists() and container_reports_dir.is_dir():
        # Running in container with mounted volume
        reports_dir = container_reports_dir
    else:
        # Running locally - relative to the test root
        reports_dir = Path(session.config.rootdir) / "reports"
    
    # Initialize and start the container monitor
    monitor = get_monitor(
        reports_dir=str(reports_dir),
        interval_seconds=interval
    )
    monitor.log_test_start()
    monitor.start_background_monitoring()


def pytest_sessionfinish(session, exitstatus):
    """
    Called after whole test run finished, right before returning the exit
    status to the system.
    """
    # Check CLI override first
    if session.config.getoption("--disable-container-monitor"):
        return

    # Load config to check container_monitoring settings
    config_path = session.config.getoption("--config")
    config = _load_config(config_path)
    monitoring_config = config.get('container_monitoring', {})

    # Check if monitoring is disabled in config
    if not monitoring_config.get('enabled', True):
        return

    # CLI option overrides config file
    cli_interval = session.config.getoption("--monitor-interval")
    interval = cli_interval if cli_interval is not None else monitoring_config.get('interval_seconds', 30)
    
    # Use mounted volume if in container, otherwise relative to test root  
    container_reports_dir = Path("/app/reports")
    if container_reports_dir.exists() and container_reports_dir.is_dir():
        reports_dir = container_reports_dir
    else:
        reports_dir = Path(session.config.rootdir) / "reports"
    
    monitor = get_monitor(
        reports_dir=str(reports_dir),
        interval_seconds=interval
    )
    monitor.stop_background_monitoring()
    monitor.log_test_end()
    
    # Cleanup
    cleanup_monitor()

