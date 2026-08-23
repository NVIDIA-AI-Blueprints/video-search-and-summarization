# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""The startup-ordering conditions that gate a deploy's critical path.

`condition: service_healthy` makes Compose hold a container in `Created` until
its upstream reports healthy, so each one serialises the deploy by however long
that upstream takes to warm up. It is worth paying only where the dependent
cannot tolerate an upstream that is not yet serving.

Each edge below has a dependent that already retries, so the condition was
duplicating the wait in the orchestrator and charging for it in wall clock.
The retry lives in the cited code: if that code changes, this test should be
revisited rather than deleted.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

DOCKER = Path(__file__).resolve().parents[3] / "deploy" / "docker"

# (file, dependent, upstream, expected condition, why)
EDGES = [
    ("services/ui/compose.yml", "vss-ui", "vss-agent", "service_started",
     "custom-server.js requires the prebuilt server without contacting any API"),
    ("services/agent/compose.yml", "vss-agent", "rtvi-vlm", "service_started",
     "config supplies base URLs to lazy clients; startup does not probe them"),
    ("services/agent/compose.yml", "vss-agent", "lvs-server", "service_started",
     "lvs_video_understanding.py registers the tool by logging the URL only"),
    ("services/infra/compose.yml", "elasticsearch-init-container", "elasticsearch",
     "service_started", "each init script retries the cluster 20x"),
    ("services/infra/compose.yml", "kafka-topic-init-container", "kafka",
     "service_started", "create-kafka-topics.sh opens with an unbounded until-loop"),
    ("services/infra/compose.yml", "kibana", "elasticsearch", "service_started",
     "kibana retries its elasticsearch connection during startup"),
    ("services/vios/foundational/docker-compose.yaml", "vst-ingress", "sensor-ms",
     "service_started", "nginx proxy_pass resolves at request time, not at load"),
    ("services/vios/initiator/docker-compose.yaml", "sensor-ms", "centralizedb",
     "service_started", "postgresql_helper retries the pool 60x at 1s"),
]

# Edges that must stay `service_healthy`: the dependent has a second consumer or
# genuinely cannot start without its upstream serving.
KEEP_HEALTHY = [
    ("services/video-summarization/compose.yml", "lvs-server", "rtvi-vlm",
     "RtviVlmClient calls _wait_for_ready() in its constructor and raises"),
]


def _condition(rel: str, dependent: str, upstream: str) -> str | None:
    """The condition on one depends_on edge, or None if the edge is absent."""
    path = DOCKER / rel
    if not path.is_file():
        pytest.skip(f"{rel} is not in this checkout")
    svc = dep = None
    in_dep = False
    for line in path.read_text().splitlines():
        m = re.match(r"^  ([a-z][a-z0-9_.-]*):\s*$", line)
        if m:
            svc, in_dep, dep = m.group(1), False, None
            continue
        if re.match(r"^    depends_on:\s*$", line):
            in_dep = True
            continue
        if in_dep and re.match(r"^    [a-z]", line):
            in_dep = False
        if not in_dep:
            continue
        m = re.match(r"^      ([a-z][a-z0-9_.-]*):\s*$", line)
        if m:
            dep = m.group(1)
            continue
        m = re.match(r"^\s*condition:\s*(\S+)\s*$", line)
        if m and svc == dependent and dep == upstream:
            return m.group(1)
    return None


@pytest.mark.parametrize("rel,dependent,upstream,expected,why", EDGES)
def test_edge_is_not_gated_on_health(rel, dependent, upstream, expected, why) -> None:
    got = _condition(rel, dependent, upstream)
    assert got is not None, f"{dependent} no longer declares a {upstream} dependency"
    assert got == expected, (
        f"{dependent} -> {upstream} is {got}, expected {expected}. "
        f"Waiting for health here serialises the deploy for no gain: {why}."
    )


@pytest.mark.parametrize("rel,dependent,upstream,why", KEEP_HEALTHY)
def test_a_load_bearing_edge_keeps_its_health_gate(rel, dependent, upstream, why) -> None:
    got = _condition(rel, dependent, upstream)
    if got is None:
        pytest.skip(f"{dependent} -> {upstream} is not declared in this checkout")
    assert got == "service_healthy", (
        f"{dependent} -> {upstream} must stay service_healthy until the dependent "
        f"reconnects in the background: {why}."
    )
