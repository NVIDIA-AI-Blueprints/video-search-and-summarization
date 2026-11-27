#!/usr/bin/env python3
"""Report what a Docker Compose stack is still waiting on during long deploys.

Used as a background watcher during ``blueprint-deploy.sh`` and by
``wait_services`` so CI logs show *which* containers block readiness instead
of only a final timeout.

Usage::

    # Background during deploy (warehouse locked CI):
    python3 -m eval.scripts.infra.compose_progress watch --project vss --interval 30 &

    # One-shot status line:
    python3 -m eval.scripts.infra.compose_progress once --project vss
"""

from __future__ import annotations

import argparse
import signal
import sys
import time
from dataclasses import dataclass

from eval.scripts._lib._docker import docker_ps_json, inspect_exit_code, inspect_health

_TAG = "[compose-progress]"

# Services that commonly gate warehouse kafka/redis 3d / mv3dt sanity.
# ``vss-auto-calibration`` is only present for bp_wh_auto_calib; missing gates
# are ignored (not treated as blockers).
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

# Eval pipeline init containers that commonly block ``docker compose up`` on
# alerts / video-upload-only runs (bp-configurator → eval-orchestrator chain).
EVAL_INIT_GATE_CONTAINERS = frozenset(
    {
        "bp-configurator-init",
        "bp-configurator",
        "eval-orchestrator-init",
        "eval-orchestrator",
        "alert-bridge-evaluator-init",
        "vss-alert-bridge-evaluator",
    }
)


@dataclass(frozen=True)
class ContainerState:
    name: str
    status: str
    running: bool
    health: str | None
    exit_code: int | None


def _short_name(full_name: str) -> str:
    name = (full_name or "").lstrip("/")
    for prefix in ("vss-", "mdx-"):
        if name.startswith(prefix):
            # compose project prefix on auto-generated names
            parts = name.split("-", 2)
            if len(parts) == 3 and parts[0] in ("vss", "mdx"):
                return parts[2]
            break
    return name


def collect_states(*, project: str) -> list[ContainerState]:
    states: list[ContainerState] = []
    for row in docker_ps_json(project=project, all_states=True):
        cid = row.get("ID", "")
        name = _short_name(row.get("Names", ""))
        status = row.get("Status", "")
        running = status.lower().startswith("up")
        health = inspect_health(cid) if cid else None
        exit_code = inspect_exit_code(cid) if cid and not running else None
        states.append(
            ContainerState(
                name=name,
                status=status,
                running=running,
                health=health,
                exit_code=exit_code,
            )
        )
    return sorted(states, key=lambda s: s.name)


def _is_blocker(
    state: ContainerState,
    *,
    gate_only: bool,
    gate_names: frozenset[str] | None = None,
) -> bool:
    if gate_names is not None:
        if state.name not in gate_names:
            return False
    elif gate_only and state.name not in WAREHOUSE_GATE_CONTAINERS:
        return False
    if not state.running:
        return True
    if state.health in {"starting", "unhealthy"}:
        return True
    return False


def _blocker_detail(state: ContainerState) -> str:
    if not state.running:
        code = f" exit={state.exit_code}" if state.exit_code not in (None, 0) else ""
        return f"{state.name}: {state.status}{code}"
    if state.health:
        return f"{state.name}: health={state.health} ({state.status})"
    return f"{state.name}: {state.status}"


def summarize(
    states: list[ContainerState],
    *,
    gate_only: bool = False,
    gate_names: frozenset[str] | None = None,
) -> tuple[int, int, int, list[str]]:
    """Return (total, running, healthy_or_no_hc, blocker_details)."""
    total = len(states)
    running = sum(1 for s in states if s.running)
    healthy = sum(
        1
        for s in states
        if s.running and (s.health is None or s.health == "healthy")
    )
    blockers = [
        _blocker_detail(s)
        for s in states
        if _is_blocker(s, gate_only=gate_only, gate_names=gate_names)
    ]
    return total, running, healthy, blockers


def format_status_line(
    states: list[ContainerState],
    *,
    elapsed_sec: float,
    gate_only: bool = False,
    gate_names: frozenset[str] | None = None,
) -> str:
    total, running, healthy, blockers = summarize(
        states, gate_only=gate_only, gate_names=gate_names
    )
    elapsed = int(elapsed_sec)
    if not states:
        return f"{_TAG} +{elapsed}s: no containers found for project yet"
    if blockers:
        shown = ", ".join(blockers[:8])
        extra = f" (+{len(blockers) - 8} more)" if len(blockers) > 8 else ""
        return (
            f"{_TAG} +{elapsed}s: {running}/{total} up, "
            f"{healthy} ready — still waiting on: {shown}{extra}"
        )
    return (
        f"{_TAG} +{elapsed}s: {running}/{total} up, "
        f"{healthy} ready — no blockers in scope"
    )


def print_once(
    *,
    project: str,
    gate_only: bool = False,
    gate_names: frozenset[str] | None = None,
) -> int:
    states = collect_states(project=project)
    line = format_status_line(states, elapsed_sec=0, gate_only=gate_only, gate_names=gate_names)
    print(line, flush=True)
    return 0


def watch_loop(
    *,
    project: str,
    interval_sec: float,
    gate_only: bool = False,
    gate_names: frozenset[str] | None = None,
    max_seconds: float | None = None,
) -> int:
    start = time.monotonic()
    stop = False

    def _handle(_signum: int, _frame: object) -> None:
        nonlocal stop
        stop = True

    signal.signal(signal.SIGTERM, _handle)
    signal.signal(signal.SIGINT, _handle)

    scope = "eval-init-gates" if gate_names is EVAL_INIT_GATE_CONTAINERS else (
        "warehouse-gates" if gate_only else "all"
    )
    print(
        f"{_TAG} Watching compose project={project!r} every {interval_sec:.0f}s "
        f"(scope={scope})",
        flush=True,
    )
    while not stop:
        elapsed = time.monotonic() - start
        if max_seconds is not None and elapsed >= max_seconds:
            break
        try:
            states = collect_states(project=project)
            print(
                format_status_line(
                    states,
                    elapsed_sec=elapsed,
                    gate_only=gate_only,
                    gate_names=gate_names,
                ),
                flush=True,
            )
        except Exception as exc:
            print(f"{_TAG} +{int(elapsed)}s: status check failed: {exc}", flush=True)
        if stop:
            break
        time.sleep(max(1.0, interval_sec))
    return 0


def wait_until_ready(
    *,
    project: str,
    budget_sec: int,
    poll_sec: float = 15.0,
    gate_only: bool = True,
    gate_names: frozenset[str] | None = None,
) -> int:
    """Poll compose status until gate containers are ready or *budget_sec* elapses."""
    start = time.monotonic()
    deadline = start + budget_sec
    print(
        f"{_TAG} Waiting up to {budget_sec}s for warehouse gate containers "
        f"(project={project!r}, poll={poll_sec:.0f}s)",
        flush=True,
    )
    last_line = ""
    while time.monotonic() < deadline:
        elapsed = time.monotonic() - start
        states = collect_states(project=project)
        _total, _running, _healthy, blockers = summarize(
            states, gate_only=gate_only, gate_names=gate_names
        )
        line = format_status_line(
            states, elapsed_sec=elapsed, gate_only=gate_only, gate_names=gate_names
        )
        if line != last_line:
            print(line, flush=True)
            last_line = line
        if not blockers:
            print(f"{_TAG} Gate containers ready after {int(elapsed)}s", flush=True)
            return 0
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(poll_sec, remaining))
    print(
        f"{_TAG} Gate wait budget exhausted ({budget_sec}s); "
        f"continuing with tests (blockers may remain)",
        flush=True,
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--project", default="vss", help="Compose project name")
    common.add_argument(
        "--gate-only",
        action="store_true",
        help="Only report warehouse gate / sanity-critical containers",
    )
    common.add_argument(
        "--eval-init-gates",
        action="store_true",
        help="Only report eval init chain containers (bp-configurator / eval-orchestrator)",
    )

    once = sub.add_parser("once", parents=[common], help="Print one status line")
    once.set_defaults(command="once")

    watch = sub.add_parser("watch", parents=[common], help="Poll until SIGTERM/SIGINT")
    watch.add_argument("--interval", type=float, default=30.0, help="Seconds between polls")
    watch.add_argument("--max-seconds", type=float, default=None, help="Optional stop time")

    wait = sub.add_parser("wait", parents=[common], help="Wait until gate containers ready")
    wait.add_argument("--budget-sec", type=int, required=True)
    wait.add_argument("--poll-sec", type=float, default=15.0)

    args = parser.parse_args(argv)
    gate_names: frozenset[str] | None = None
    if getattr(args, "eval_init_gates", False):
        gate_names = EVAL_INIT_GATE_CONTAINERS

    if args.command == "once":
        return print_once(project=args.project, gate_only=args.gate_only, gate_names=gate_names)
    if args.command == "watch":
        return watch_loop(
            project=args.project,
            interval_sec=args.interval,
            gate_only=args.gate_only,
            gate_names=gate_names,
            max_seconds=args.max_seconds,
        )
    if args.command == "wait":
        return wait_until_ready(
            project=args.project,
            budget_sec=args.budget_sec,
            poll_sec=args.poll_sec,
            gate_only=args.gate_only,
            gate_names=gate_names,
        )
    return 1


if __name__ == "__main__":
    sys.exit(main())
