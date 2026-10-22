# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

"""Bring up the image under test with Milvus and Kafka, then tear it down."""

import logging
import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

import pytest
import requests

logger = logging.getLogger(__name__)

COMPOSE_FILE = Path(__file__).resolve().parent / "compose.reid-test.yml"
PROJECT = "reid-image-test"
READY_TIMEOUT_S = 300
# The consumer uses auto.offset.reset=latest. Wait until its group is
# assigned before the test produces, or the embedding is skipped.
POST_READY_SETTLE_S = 6.0


@dataclass
class ReidStack:
    service_url: str
    kafka_bootstrap: str
    image_ref: str


def _compose(*args: str, env: dict, check: bool = True) -> subprocess.CompletedProcess:
    cmd = ["docker", "compose", "-p", PROJECT, "-f", str(COMPOSE_FILE), *args]
    result = subprocess.run(cmd, env=env, check=False, capture_output=True, text=True)
    if check and result.returncode != 0:
        raise RuntimeError(
            f"docker compose {' '.join(args)} failed ({result.returncode}):\n"
            f"{result.stdout}\n{result.stderr}"
        )
    return result


def _service_logs(env: dict) -> str:
    return _compose("logs", "--no-color", "--tail", "200", "reid-embed", env=env, check=False).stdout


@pytest.fixture(scope="session")
def reid_stack(request, image_ref):
    env = {**os.environ, "IMAGE_REF": image_ref}
    keep = request.config.getoption("--keep-stack")
    service_url = "http://localhost:8000"
    kafka_bootstrap = request.config.getoption("--kafka-bootstrap")

    logger.info("Bringing up reid stack (image=%s)", image_ref)
    _compose("down", "-v", "--remove-orphans", env=env, check=False)
    _compose("up", "-d", "--force-recreate", env=env)

    try:
        _wait_for_ready(env, service_url)
        time.sleep(POST_READY_SETTLE_S)
        yield ReidStack(service_url=service_url, kafka_bootstrap=kafka_bootstrap, image_ref=image_ref)
    finally:
        if keep:
            logger.warning("--keep-stack set; leaving project %s running", PROJECT)
        else:
            logger.info("Tearing down reid stack %s", PROJECT)
            _compose("down", "-v", "--remove-orphans", env=env, check=False)


def _wait_for_ready(env: dict, service_url: str) -> None:
    deadline = time.monotonic() + READY_TIMEOUT_S
    while time.monotonic() < deadline:
        ps = _compose("ps", "-a", "--format", "{{.Service}} {{.State}}", env=env, check=False)
        if "reid-embed exited" in ps.stdout:
            raise RuntimeError(f"reid-embed exited early:\n{_service_logs(env)}")
        try:
            response = requests.get(f"{service_url}/health/ready", timeout=3)
        except requests.RequestException:
            response = None
        if response is not None and response.status_code == 200 and response.json().get("status") == "ready":
            logger.info("reid-embed ready at %s", service_url)
            return
        time.sleep(2)
    raise TimeoutError(f"reid-embed not ready within {READY_TIMEOUT_S}s:\n{_service_logs(env)}")
