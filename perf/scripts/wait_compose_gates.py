#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Poll warehouse compose gate containers, matching ci-vss-oss wait_services.

Eval's wait_until_ready logs blockers and continues when the budget expires.
This job keeps that contract: exit 0 after the budget so mdx-raw / VST waits
can fail closed on the ingest path.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import time

TAG = "[compose-progress]"

# Same set as ci-vss-oss eval/scripts/infra/compose_progress.py.
WAREHOUSE_GATE_CONTAINERS = frozenset(
    {
        "vss-rtvi-cv",
        "vss-auto-calibration",
        "vss-configurator",
        "vss-video-analytics-api",
        "vss-vios-nvstreamer",
        "vss-vios-sensor",
        "vss-vios-ingress",
        "kafka",
        "redis",
        "elasticsearch",
        "kibana",
        "logstash",
        "sdr-controller",
    }
)


def short_name(full_name: str) -> str:
    """Strip compose project decoration; keep explicit container_name values."""
    name = (full_name or "").lstrip("/")
    parts = name.split("-")
    if len(parts) >= 2 and parts[-1].isdigit():
        name = "-".join(parts[:-1])
    return name


def gate_name(full_name: str, gates: frozenset[str]) -> str | None:
    name = short_name(full_name)
    if name in gates:
        return name
    for gate in sorted(gates, key=len, reverse=True):
        if name == gate or name.endswith(f"-{gate}"):
            return gate
    return None


def inspect_health(container_id: str) -> str | None:
    try:
        raw = subprocess.check_output(
            [
                "docker",
                "inspect",
                "-f",
                "{{if .State.Health}}{{.State.Health.Status}}{{end}}",
                container_id,
            ],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None
    return raw or None


def inspect_exit_code(container_id: str) -> int | None:
    try:
        raw = subprocess.check_output(
            ["docker", "inspect", "-f", "{{.State.ExitCode}}", container_id],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def collect_states(project: str) -> list[dict[str, object]]:
    try:
        raw = subprocess.check_output(
            [
                "docker",
                "ps",
                "-a",
                "--filter",
                f"label=com.docker.compose.project={project}",
                "--format",
                '{"ID":"{{.ID}}","Names":"{{.Names}}","Status":"{{.Status}}"}',
            ],
            text=True,
            stderr=subprocess.DEVNULL,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return []
    states: list[dict[str, object]] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        cid = str(row.get("ID") or "")
        status = str(row.get("Status") or "")
        running = status.lower().startswith("up")
        states.append(
            {
                "name": short_name(str(row.get("Names") or "")),
                "full_name": str(row.get("Names") or ""),
                "status": status,
                "running": running,
                "health": inspect_health(cid) if cid else None,
                "exit_code": inspect_exit_code(cid) if cid and not running else None,
            }
        )
    return sorted(states, key=lambda item: str(item["name"]))


def is_blocker(state: dict[str, object], gates: frozenset[str]) -> bool:
    matched = gate_name(str(state.get("full_name") or state.get("name") or ""), gates)
    if matched is None:
        return False
    if not state.get("running"):
        return True
    return state.get("health") in {"starting", "unhealthy"}


def blocker_detail(state: dict[str, object]) -> str:
    name = str(state.get("name") or "?")
    status = str(state.get("status") or "")
    if not state.get("running"):
        code = state.get("exit_code")
        extra = f" exit={code}" if code not in (None, 0) else ""
        return f"{name}: {status}{extra}"
    health = state.get("health")
    if health:
        return f"{name}: health={health} ({status})"
    return f"{name}: {status}"


def summarize(
    states: list[dict[str, object]], gates: frozenset[str]
) -> tuple[int, int, int, list[str]]:
    total = len(states)
    running = sum(1 for item in states if item.get("running"))
    healthy = sum(
        1
        for item in states
        if item.get("running")
        and (item.get("health") is None or item.get("health") == "healthy")
    )
    blockers = [
        blocker_detail(item) for item in states if is_blocker(item, gates)
    ]
    return total, running, healthy, blockers


def format_status_line(
    states: list[dict[str, object]],
    *,
    elapsed_sec: float,
    gates: frozenset[str],
) -> str:
    total, running, healthy, blockers = summarize(states, gates)
    elapsed = int(elapsed_sec)
    if not states:
        return f"{TAG} +{elapsed}s: no containers found for project yet"
    if blockers:
        shown = ", ".join(blockers[:8])
        extra = f" (+{len(blockers) - 8} more)" if len(blockers) > 8 else ""
        return (
            f"{TAG} +{elapsed}s: {running}/{total} up, {healthy} ready — "
            f"still waiting on: {shown}{extra}"
        )
    return (
        f"{TAG} +{elapsed}s: {running}/{total} up, {healthy} ready — "
        "no blockers in scope"
    )


def wait_until_ready(
    *,
    project: str,
    budget_sec: int,
    poll_sec: float = 15.0,
    gates: frozenset[str] = WAREHOUSE_GATE_CONTAINERS,
) -> int:
    """Return 0 when gates are ready, or 0 after the budget (eval continue)."""
    start = time.monotonic()
    deadline = start + budget_sec
    print(
        f"{TAG} Waiting up to {budget_sec}s for warehouse gate containers "
        f"(project={project!r}, poll={poll_sec:.0f}s)",
        flush=True,
    )
    last_line = ""
    while time.monotonic() < deadline:
        elapsed = time.monotonic() - start
        states = collect_states(project)
        _total, _running, _healthy, blockers = summarize(states, gates)
        line = format_status_line(states, elapsed_sec=elapsed, gates=gates)
        if line != last_line:
            print(line, flush=True)
            last_line = line
        if states and not blockers:
            print(f"{TAG} Gate containers ready after {int(elapsed)}s", flush=True)
            return 0
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(poll_sec, remaining))
    print(
        f"{TAG} Gate wait budget exhausted ({budget_sec}s); "
        "continuing with ingest waits (blockers may remain)",
        flush=True,
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True)
    parser.add_argument("--budget-sec", type=int, default=180)
    parser.add_argument("--poll-sec", type=float, default=15.0)
    parser.add_argument(
        "--gate-only",
        action="store_true",
        default=True,
        help="Only warehouse gate containers (default, matches eval wait_services)",
    )
    args = parser.parse_args(argv)
    return wait_until_ready(
        project=args.project,
        budget_sec=args.budget_sec,
        poll_sec=args.poll_sec,
        gates=WAREHOUSE_GATE_CONTAINERS,
    )


if __name__ == "__main__":
    raise SystemExit(main())
