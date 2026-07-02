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
Tier-2 end-to-end test: full warehouse-3d-app-mv3dt pipeline on the 4-camera
sample dataset (RTX PRO 6000).

Asserts the deployed stack is healthy and metadata flows end to end:
  - perception (perception-3d-mv3dt) ingests all sample streams at ~30 FPS
  - measurement-fusion (measurement-fusion-3d) reports healthy + publishes frames
  - the raw (mdx-mv3dt-raw) and fused (mdx-bev) broker topics grow

Skips unless a deployment is resolvable (see e2e/conftest.py). Pass --e2e-deploy
to bring the stack up/down in-test, optionally with --e2e-run-setup.
"""

import logging
import re
import subprocess
import time

import pytest

logger = logging.getLogger(__name__)

pytestmark = [pytest.mark.e2e, pytest.mark.slow, pytest.mark.timeout(3000)]

PERCEPTION_NAME = "perception-3d-mv3dt"
FUSION_NAME = "measurement-fusion-3d"
RAW_TOPIC = "mdx-mv3dt-raw"
FUSED_TOPIC = "mdx-bev"

READY_TIMEOUT_S = 1800   # first run includes TRT engine builds
MIN_FPS = 10.0           # ~30 FPS expected on datacenter GPUs; floor guards regressions
OFFSET_WAIT_S = 30


# --------------------------------------------------------------------------- #
# docker helpers
# --------------------------------------------------------------------------- #
def _sh(*args, check=False, timeout=120):
    return subprocess.run(args, check=check, capture_output=True, text=True, timeout=timeout)


def _container(name_substr):
    out = _sh("docker", "ps", "--filter", f"name={name_substr}", "--format", "{{.Names}}").stdout
    names = [n for n in out.splitlines() if n.strip()]
    return names[0] if names else None


def _logs(name, tail=400):
    return _sh("docker", "logs", "--tail", str(tail), name).stdout + _sh(
        "docker", "logs", "--tail", str(tail), name
    ).stderr


def _health(name):
    out = _sh("docker", "inspect", "--format", "{{.State.Health.Status}}", name).stdout.strip()
    return out or "none"


def _wait(predicate, timeout_s, what, poll=10):
    deadline = time.monotonic() + timeout_s
    last = None
    while time.monotonic() < deadline:
        ok, last = predicate()
        if ok:
            return last
        time.sleep(poll)
    raise AssertionError(f"timed out after {timeout_s}s waiting for {what}; last={last}")


# --------------------------------------------------------------------------- #
# broker offset helpers
# --------------------------------------------------------------------------- #
def _kafka_total_offset(kafka_name, topic):
    out = _sh("docker", "exec", kafka_name, "kafka-get-offsets",
              "--bootstrap-server", "localhost:9092", "--topic", topic).stdout
    total = 0
    for ln in out.splitlines():
        parts = ln.strip().split(":")
        if len(parts) == 3 and parts[2].isdigit():
            total += int(parts[2])
    return total


def _redis_xlen(redis_name, topic):
    out = _sh("docker", "exec", redis_name, "redis-cli", "XLEN", topic).stdout.strip()
    return int(out) if out.isdigit() else 0


def _topic_total(broker, raw_or_fused, deployment):
    if broker == "kafka":
        kafka = _container("kafka")
        if not kafka:
            pytest.skip("kafka container not found for offset check")
        return _kafka_total_offset(kafka, raw_or_fused)
    redis = _container("redis")
    if not redis:
        pytest.skip("redis container not found for offset check")
    return _redis_xlen(redis, raw_or_fused)


# --------------------------------------------------------------------------- #
# test
# --------------------------------------------------------------------------- #
def test_warehouse_pipeline_e2e(deployed_stack):
    dep = deployed_stack
    expected_streams = dep.num_streams

    # 1. Perception + fusion containers come up.
    perception = _wait(lambda: (_container(PERCEPTION_NAME) is not None, _container(PERCEPTION_NAME)),
                        READY_TIMEOUT_S, f"{PERCEPTION_NAME} to start")
    fusion = _wait(lambda: (_container(FUSION_NAME) is not None, _container(FUSION_NAME)),
                   READY_TIMEOUT_S, f"{FUSION_NAME} to start")

    # 2. Perception ingests all sample streams.
    def _streams_added():
        logs = _logs(perception)
        added = len(re.findall(r"(?:Source.*added|Stream \d+ added|stream added)", logs, re.I))
        active = re.findall(r"Active sources\s*:\s*(\d+)", logs)
        n = max([int(active[-1])] if active else [0] + [added])
        return n >= expected_streams, n
    n_streams = _wait(_streams_added, READY_TIMEOUT_S, f"{expected_streams} streams to be added")
    logger.info("Perception streams active: %s (expected %d)", n_streams, expected_streams)

    # 3. Perception FPS reaches a healthy floor.
    def _fps_ok():
        fps_vals = [float(x) for x in re.findall(r"FPS\s*[=:]\s*([0-9]+\.?[0-9]*)", _logs(perception), re.I)]
        recent = fps_vals[-expected_streams:] if fps_vals else []
        return (bool(recent) and min(recent) >= MIN_FPS), (recent or fps_vals[-5:])
    fps = _wait(_fps_ok, READY_TIMEOUT_S, f"perception FPS >= {MIN_FPS}")
    logger.info("Perception FPS sample: %s", fps)

    # 4. Fusion service healthy (its /tmp/fusion_ready sentinel drives the compose health check).
    fusion_health = _wait(lambda: (_health(fusion) == "healthy", _health(fusion)),
                          300, f"{FUSION_NAME} healthy")
    assert fusion_health == "healthy"

    # 5. Both broker topics grow (metadata flows perception -> fusion -> downstream).
    raw0 = _topic_total(dep.broker, RAW_TOPIC, dep)
    bev0 = _topic_total(dep.broker, FUSED_TOPIC, dep)
    time.sleep(OFFSET_WAIT_S)
    raw1 = _topic_total(dep.broker, RAW_TOPIC, dep)
    bev1 = _topic_total(dep.broker, FUSED_TOPIC, dep)
    logger.info("MV3DT_E2E broker=%s raw %d->%d  bev %d->%d  streams=%s fps=%s",
                dep.broker, raw0, raw1, bev0, bev1, n_streams, fps)

    assert raw1 > raw0, f"raw topic {RAW_TOPIC} not growing ({raw0}->{raw1})"
    assert bev1 > bev0, f"fused topic {FUSED_TOPIC} not growing ({bev0}->{bev1})"

    # 6. No mv3dt service crashed.
    for name in (perception, fusion):
        state = _sh("docker", "inspect", "--format", "{{.State.Status}}", name).stdout.strip()
        assert state == "running", f"{name} not running (state={state})"
