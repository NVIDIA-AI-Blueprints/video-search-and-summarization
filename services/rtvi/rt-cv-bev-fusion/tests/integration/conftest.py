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
"""
Fixtures for the fusion-service integration test.

Brings up tests/integration/compose.fusion-test.yml (single-node Kafka + the
fusion image under test), waits for the service's /tmp/fusion_ready sentinel,
and tears the stack down afterwards. Kafka only — it's the broker the warehouse
deployment uses (BP_PROFILE=bp_wh_kafka).
"""

import logging
import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

import pytest

logger = logging.getLogger(__name__)

COMPOSE_FILE = Path(__file__).resolve().parent / "compose.fusion-test.yml"
PROJECT = "mv3dt-fusion-test"
READY_TIMEOUT_S = 180
# After the service subscribes, give the Kafka consumer group time to be assigned
# partitions before we produce — the service consumes from auto.offset.reset=latest,
# so anything produced before assignment is missed.
POST_READY_SETTLE_S = 6.0


@dataclass
class FusionStack:
    kafka_bootstrap: str
    project: str


def _compose(*args: str, env: dict, check: bool = True) -> subprocess.CompletedProcess:
    cmd = ["docker", "compose", "-p", PROJECT, "-f", str(COMPOSE_FILE), *args]
    return subprocess.run(cmd, env=env, check=check, capture_output=True, text=True)


def _service_logs(env: dict) -> str:
    return _compose("logs", "--no-color", "measurement-fusion", env=env, check=False).stdout


@pytest.fixture
def fusion_stack(request, image_ref):
    env = {**os.environ, "IMAGE_REF": image_ref}
    keep = request.config.getoption("--keep-stack")

    logger.info("Bringing up fusion stack (kafka, image=%s)", image_ref)
    # Clean any stale stack from a previous aborted run, then start fresh.
    _compose("down", "-v", "--remove-orphans", env=env, check=False)
    _compose("up", "-d", "--force-recreate", env=env)

    try:
        _wait_for_ready(env)
        time.sleep(POST_READY_SETTLE_S)
        yield FusionStack(
            kafka_bootstrap=request.config.getoption("--kafka-bootstrap"),
            project=PROJECT,
        )
    finally:
        if keep:
            logger.warning("--keep-stack set; leaving project %s running", PROJECT)
        else:
            logger.info("Tearing down fusion stack %s", PROJECT)
            _compose("down", "-v", "--remove-orphans", env=env, check=False)


def _container_id(env: dict) -> str:
    out = _compose("ps", "-q", "measurement-fusion", env=env, check=False).stdout.strip()
    return out.splitlines()[0] if out else ""


def _wait_for_ready(env: dict) -> None:
    """Wait until the fusion service signals READY.

    Primary gate is the service's own /tmp/fusion_ready sentinel, surfaced via the
    compose healthcheck (State.Health == healthy). Falls back to the "Subscribed to"
    log line if no health status is available.
    """
    deadline = time.monotonic() + READY_TIMEOUT_S
    while time.monotonic() < deadline:
        # Fail fast if the container has exited.
        ps = _compose("ps", "-a", "--format", "{{.Service}} {{.State}}", env=env, check=False)
        if "measurement-fusion exited" in ps.stdout:
            raise RuntimeError(f"fusion container exited early:\n{_service_logs(env)}")

        cid = _container_id(env)
        if cid:
            health = subprocess.run(
                ["docker", "inspect", "--format", "{{if .State.Health}}{{.State.Health.Status}}{{end}}", cid],
                capture_output=True, text=True,
            ).stdout.strip()
            if health == "healthy":
                logger.info("Fusion service READY (/tmp/fusion_ready healthcheck passed)")
                return
            if not health and "Subscribed to" in _service_logs(env):
                logger.info("Fusion service READY (subscribed; no healthcheck configured)")
                return
        time.sleep(2)

    raise TimeoutError(f"fusion service not ready within {READY_TIMEOUT_S}s:\n{_service_logs(env)}")
