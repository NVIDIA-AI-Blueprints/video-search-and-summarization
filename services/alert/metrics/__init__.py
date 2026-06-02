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

"""Prometheus metrics module with feature flag support."""

import logging
import os
import sys
import tempfile

_logger = logging.getLogger(__name__)

_PROMETHEUS_MULTIPROC_AUTO_ENV = "ALERT_AGENT_PROMETHEUS_MULTIPROC_AUTO"
_PROMETHEUS_MULTIPROC_PREPARED_ENV = "ALERT_AGENT_PROMETHEUS_MULTIPROC_PREPARED"


def _env_flag_enabled(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).lower() in {"1", "true", "yes", "on"}


PROMETHEUS_ENABLED = _env_flag_enabled('PROMETHEUS_METRICS_ENABLED')


def _default_multiproc_dir() -> str:
    port = os.getenv("PROMETHEUS_PORT", "9081")
    pid = os.getpid()
    # Include the PID so that two AB processes on the same host each get their
    # own shard directory. Without the PID, processes sharing the same port
    # would clobber each other's .db files and collapse observed counts to zero.
    return os.path.join(tempfile.gettempdir(), f"alert-agent-prometheus-{port}-{pid}")


def _clear_prometheus_multiproc_dir(multiproc_dir: str) -> None:
    os.makedirs(multiproc_dir, exist_ok=True)
    for entry in os.scandir(multiproc_dir):
        if entry.is_file() and entry.name.endswith(".db"):
            try:
                os.remove(entry.path)
            except FileNotFoundError:
                pass


def reset_prometheus_multiproc_dir() -> None:
    """Prepare prometheus_client multiprocess state before metric import."""
    if not PROMETHEUS_ENABLED:
        return
    multiproc_dir = os.getenv("PROMETHEUS_MULTIPROC_DIR")
    if not multiproc_dir:
        return
    if "metrics.prometheus_metrics" in sys.modules:
        os.environ[_PROMETHEUS_MULTIPROC_PREPARED_ENV] = "1"
        return
    if os.environ.get(_PROMETHEUS_MULTIPROC_PREPARED_ENV) == "1":
        return

    _clear_prometheus_multiproc_dir(multiproc_dir)
    os.environ[_PROMETHEUS_MULTIPROC_PREPARED_ENV] = "1"


if PROMETHEUS_ENABLED:
    # The FastAPI API runs in a child process while the scrape server runs in
    # the main process. Prometheus client multiprocess mode must be configured
    # before metrics.prometheus_metrics imports prometheus_client, so keep the
    # environment setup in this package initializer.
    if "PROMETHEUS_MULTIPROC_DIR" not in os.environ:
        _auto_dir = _default_multiproc_dir()
        os.environ["PROMETHEUS_MULTIPROC_DIR"] = _auto_dir
        os.environ[_PROMETHEUS_MULTIPROC_AUTO_ENV] = "1"
        _logger.info(
            "PROMETHEUS_MULTIPROC_DIR auto-set to %r (port=%s pid=%d). "
            "Each AB process must use a unique directory; set "
            "PROMETHEUS_MULTIPROC_DIR explicitly if running multiple instances.",
            _auto_dir,
            os.getenv("PROMETHEUS_PORT", "9081"),
            os.getpid(),
        )
    os.makedirs(os.environ["PROMETHEUS_MULTIPROC_DIR"], exist_ok=True)
    reset_prometheus_multiproc_dir()
else:
    os.environ.pop(_PROMETHEUS_MULTIPROC_PREPARED_ENV, None)
    if os.environ.get(_PROMETHEUS_MULTIPROC_AUTO_ENV) == "1":
        os.environ.pop("PROMETHEUS_MULTIPROC_DIR", None)
        os.environ.pop(_PROMETHEUS_MULTIPROC_AUTO_ENV, None)
