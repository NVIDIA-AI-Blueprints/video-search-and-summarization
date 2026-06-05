# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

"""Integration coverage for prometheus_client multiprocess export.

This test starts a fresh Python interpreter with the same environment an
enabled alert-agent process uses, records metrics from a child process, and
then scrapes via ``MultiProcessCollector`` in the parent. It verifies the
core production requirement behind the FastAPI/realtime metrics fix: metrics
written outside the main process are visible to the scrape process.
"""

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("prometheus_client")


def test_child_process_metrics_are_exported_by_parent_collector(tmp_path):
    repo_root = Path(__file__).resolve().parents[2]
    script = r'''
import os
import subprocess
import sys

import metrics
from metrics.prometheus_metrics import REALTIME_RULES_CREATED, REALTIME_RULES_ACTIVE
from prometheus_client import CollectorRegistry, generate_latest, multiprocess

# Touch parent-side metrics too, matching the real process that owns the
# scrape server and constructs the registry before forking FastAPI.
REALTIME_RULES_ACTIVE.set(0)

child_code = """
from metrics.prometheus_metrics import (
    EVENTS_TOTAL_BY_SENSOR,
    REALTIME_RULES_ACTIVE,
    REALTIME_RULES_CREATED,
)

REALTIME_RULES_CREATED.inc()
REALTIME_RULES_ACTIVE.set(3)
EVENTS_TOTAL_BY_SENSOR.labels(verdict='confirmed', sensorId='cam-int').inc(2)
"""
subprocess.check_call([sys.executable, "-c", child_code])

registry = CollectorRegistry()
multiprocess.MultiProcessCollector(registry)
print(generate_latest(registry).decode("utf-8"))
'''

    env = os.environ.copy()
    env["PROMETHEUS_METRICS_ENABLED"] = "true"
    env["PROMETHEUS_MULTIPROC_DIR"] = str(tmp_path / "prometheus-shards")
    env["PYTHONPATH"] = (
        str(repo_root)
        if not env.get("PYTHONPATH")
        else str(repo_root) + os.pathsep + env["PYTHONPATH"]
    )

    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=repo_root,
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )
    output = result.stdout

    assert re.search(r"^alert_bridge_realtime_rules_created_total\s+1\.0$", output, re.M)
    assert re.search(r"^alert_bridge_realtime_rules_active\s+3\.0$", output, re.M)
    assert "alert_bridge_events_by_sensor_total" in output
    assert 'sensorId="cam-int"' in output
    assert 'verdict="confirmed"' in output
    assert "alert_bridge_events_total_by_sensor" not in output
