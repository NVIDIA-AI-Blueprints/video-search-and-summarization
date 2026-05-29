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

"""Unit tests for Prometheus multiprocess environment setup.

These tests avoid importing ``prometheus_client``. They pin the local
``metrics`` package contract that must be true before any metric object is
constructed: an enabled run has a shard directory, stale shard files are
cleared once, and disabled mode removes only the auto-managed environment.
"""

import importlib.util
import os
import sys
import uuid
from pathlib import Path


def _load_metrics_init(monkeypatch, enabled: bool):
    """Execute metrics/__init__.py under an isolated module name.

    Importing the real ``metrics`` package repeatedly can interact badly with
    prometheus_client's global registry if another test already imported
    ``metrics.prometheus_metrics``. Loading just the initializer under a
    throwaway name tests the environment logic without touching the real
    package modules.
    """
    monkeypatch.setenv("PROMETHEUS_METRICS_ENABLED", "true" if enabled else "false")
    monkeypatch.delitem(sys.modules, "metrics.prometheus_metrics", raising=False)

    module_path = Path(__file__).resolve().parents[2] / "metrics" / "__init__.py"
    module_name = f"_metrics_init_under_test_{uuid.uuid4().hex}"
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_enabled_mode_sets_auto_multiprocess_dir(monkeypatch):
    monkeypatch.delenv("PROMETHEUS_MULTIPROC_DIR", raising=False)
    monkeypatch.delenv("ALERT_AGENT_PROMETHEUS_MULTIPROC_AUTO", raising=False)
    monkeypatch.delenv("ALERT_AGENT_PROMETHEUS_MULTIPROC_PREPARED", raising=False)
    monkeypatch.setenv("PROMETHEUS_PORT", "19876")

    metrics = _load_metrics_init(monkeypatch, enabled=True)

    multiproc_dir = os.environ["PROMETHEUS_MULTIPROC_DIR"]
    assert metrics.PROMETHEUS_ENABLED is True
    # Path now includes the PID to prevent cross-process shard collisions.
    assert "alert-agent-prometheus-19876-" in multiproc_dir
    assert multiproc_dir.split("-")[-1].isdigit(), "expected PID suffix"
    assert os.path.isdir(multiproc_dir)
    assert os.environ["ALERT_AGENT_PROMETHEUS_MULTIPROC_AUTO"] == "1"
    assert os.environ["ALERT_AGENT_PROMETHEUS_MULTIPROC_PREPARED"] == "1"


def test_enabled_mode_respects_explicit_dir_and_clears_stale_db_files(monkeypatch, tmp_path):
    explicit_dir = tmp_path / "prometheus-shards"
    explicit_dir.mkdir()
    stale_db = explicit_dir / "counter_123.db"
    stale_db.write_text("stale")
    non_db_file = explicit_dir / "keep.txt"
    non_db_file.write_text("keep")

    monkeypatch.setenv("PROMETHEUS_MULTIPROC_DIR", str(explicit_dir))
    monkeypatch.delenv("ALERT_AGENT_PROMETHEUS_MULTIPROC_AUTO", raising=False)
    monkeypatch.delenv("ALERT_AGENT_PROMETHEUS_MULTIPROC_PREPARED", raising=False)

    _load_metrics_init(monkeypatch, enabled=True)

    assert os.environ["PROMETHEUS_MULTIPROC_DIR"] == str(explicit_dir)
    assert "ALERT_AGENT_PROMETHEUS_MULTIPROC_AUTO" not in os.environ
    assert not stale_db.exists()
    assert non_db_file.exists()


def test_reset_noops_after_metric_module_is_loaded(monkeypatch, tmp_path):
    explicit_dir = tmp_path / "prometheus-shards"
    explicit_dir.mkdir()
    monkeypatch.setenv("PROMETHEUS_MULTIPROC_DIR", str(explicit_dir))
    monkeypatch.delenv("ALERT_AGENT_PROMETHEUS_MULTIPROC_PREPARED", raising=False)

    metrics = _load_metrics_init(monkeypatch, enabled=True)
    new_db = explicit_dir / "counter_after_import.db"
    new_db.write_text("active shard")

    monkeypatch.delenv("ALERT_AGENT_PROMETHEUS_MULTIPROC_PREPARED", raising=False)
    monkeypatch.setitem(sys.modules, "metrics.prometheus_metrics", object())

    metrics.reset_prometheus_multiproc_dir()

    assert new_db.exists()
    assert os.environ["ALERT_AGENT_PROMETHEUS_MULTIPROC_PREPARED"] == "1"


def test_disabled_mode_removes_auto_managed_multiprocess_env(monkeypatch, tmp_path):
    monkeypatch.setenv("PROMETHEUS_MULTIPROC_DIR", str(tmp_path / "auto-shards"))
    monkeypatch.setenv("ALERT_AGENT_PROMETHEUS_MULTIPROC_AUTO", "1")
    monkeypatch.setenv("ALERT_AGENT_PROMETHEUS_MULTIPROC_PREPARED", "1")

    metrics = _load_metrics_init(monkeypatch, enabled=False)

    assert metrics.PROMETHEUS_ENABLED is False
    assert "PROMETHEUS_MULTIPROC_DIR" not in os.environ
    assert "ALERT_AGENT_PROMETHEUS_MULTIPROC_AUTO" not in os.environ
    assert "ALERT_AGENT_PROMETHEUS_MULTIPROC_PREPARED" not in os.environ
