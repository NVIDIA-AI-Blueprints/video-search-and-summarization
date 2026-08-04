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

"""Shared fixtures for the public unit-test suite."""

import os

import prometheus_client as prom
import pytest

# Ensure via_logger can open its default log file during collection.
os.makedirs(os.environ.get("VIA_LOG_DIR", "/tmp/via-logs"), exist_ok=True)


@pytest.fixture(autouse=True)
def use_temp_env(monkeypatch):
    monkeypatch.setenv("VIA_SKIP_PIPELINE_WARMUP", "1")
    yield


@pytest.fixture(autouse=True)
def reset_sse_appstatus_event():
    from sse_starlette.sse import AppStatus

    AppStatus.should_exit_event = None

    import threading

    all_threads = threading.enumerate()
    main_thread = threading.main_thread()
    running_threads = [
        t for t in all_threads if t is not main_thread and t.is_alive() and not t.daemon
    ]
    if running_threads:
        print("Non-daemon threads still running: %s" % [t.name for t in running_threads])


@pytest.fixture(autouse=True)
def cleanup_prom_registry():
    for collector in list(prom.REGISTRY._names_to_collectors.values()):
        try:
            prom.REGISTRY.unregister(collector)
        except KeyError:
            # Handle the case where a collector is already unregistered
            pass
