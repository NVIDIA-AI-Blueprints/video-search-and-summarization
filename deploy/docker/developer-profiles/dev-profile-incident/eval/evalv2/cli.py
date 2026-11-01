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

"""``python -m evalv2``: plan, preflight, dry-run and audit an Evaluation V2 run.

- ``plan --config C``: the work items each task would run (no calls).
- ``preflight --config C``: config rules, registered tasks, and whether every budget cap covers the worst case
  (each planned attempt plus every retry the policy allows). Exit 1 on any problem.
- ``run --config C --dry-run``: plan plus preflight, printed as one statement. A live run is never started from
  here without ``--live`` and ``EVALV2_ALLOW_LIVE=1``; live execution is gated by the owner's approval.
- ``audit frozen-v1``: V1's frozen artifacts are unchanged. ``audit run --store S --run R``: a sealed run's
  index and artifacts are intact.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from evalv2.core import frozen_v1
from evalv2.core.artifacts import ArtifactStore
from evalv2.core.config import RunConfig, load_config
from evalv2.core.retry import POLICIES
from evalv2.tasks.base import TASKS, WorkItem

LIVE_ENV = "EVALV2_ALLOW_LIVE"
DatasetLoader = Callable[[RunConfig], dict[str, Any]]


def _no_datasets(config: RunConfig) -> dict[str, Any]:
    return {}


def plan(config: RunConfig, load_datasets: DatasetLoader = _no_datasets) -> dict[str, list[WorkItem]]:
    """Every task's work items. Raises ``KeyError`` for a task no package has registered."""
    unknown = sorted(set(config.tasks) - set(TASKS))
    if unknown:
        raise KeyError(f"no task registered for {unknown}; registered: {sorted(TASKS)}")
    datasets = load_datasets(config)
    return {name: TASKS[name]().plan(config, datasets) for name in config.tasks}


def preflight(config: RunConfig, items: dict[str, list[WorkItem]]) -> list[str]:
    """Problems that must be fixed before a run: today, caps that cannot cover the worst case."""
    problems: list[str] = []
    retries = POLICIES[config.retry_policy].max_retries
    for name, task_items in items.items():
        runner = TASKS[name]()
        estimate = getattr(runner, "budget_estimate", None)
        if estimate is None:
            continue
        for ledger, need in estimate(config, task_items).items():
            worst_attempts = need["attempts"] * (1 + retries)
            worst_tokens = need["tokens"] * (1 + retries)
            caps = [c for c in config.budget_caps() if c.ledger == ledger and c.task in ("*", name)]
            if not caps:
                problems.append(f"task {name!r} needs ledger {ledger!r} but no cap is set")
            for cap in caps:
                if cap.max_attempts is not None and cap.max_attempts < worst_attempts:
                    problems.append(f"{ledger} attempts cap {cap.max_attempts} < worst case {worst_attempts} "
                                    f"for task {name!r}")
                if cap.max_tokens is not None and cap.max_tokens < worst_tokens:
                    problems.append(f"{ledger} tokens cap {cap.max_tokens} < worst case {worst_tokens} "
                                    f"for task {name!r}")
    return problems


def _summary(items: dict[str, list[WorkItem]]) -> dict[str, Any]:
    return {name: {"items": len(task_items), "by_subject": dict(Counter(i.subject for i in task_items))}
            for name, task_items in items.items()}


def main(argv: Sequence[str] | None = None, *, load_datasets: DatasetLoader = _no_datasets) -> int:
    parser = argparse.ArgumentParser(prog="python -m evalv2", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("plan", "preflight", "run"):
        command = sub.add_parser(name)
        command.add_argument("--config", type=Path, required=True)
        if name == "run":
            command.add_argument("--dry-run", action="store_true")
            command.add_argument("--live", action="store_true")
    audit = sub.add_parser("audit")
    audit_sub = audit.add_subparsers(dest="target", required=True)
    audit_sub.add_parser("frozen-v1")
    audit_run = audit_sub.add_parser("run")
    audit_run.add_argument("--store", type=Path, required=True)
    audit_run.add_argument("--run", required=True)
    args = parser.parse_args(argv)

    if args.command == "audit":
        problems = (frozen_v1.drift() if args.target == "frozen-v1"
                    else ArtifactStore(args.store).run(args.run).verify())
        print(json.dumps({"ok": not problems, "problems": problems}, indent=2))
        return 1 if problems else 0

    config = load_config(args.config)
    try:
        items = plan(config, load_datasets)
    except KeyError as exc:
        print(json.dumps({"ok": False, "problems": [str(exc)]}, indent=2))
        return 1
    if args.command == "plan":
        print(json.dumps({"experiment": config.experiment, "tasks": _summary(items)}, indent=2))
        return 0
    problems = preflight(config, items)
    if args.command == "preflight" or args.dry_run:
        print(json.dumps({"experiment": config.experiment, "half": config.split.half, "tasks": _summary(items),
                          "retry_policy": config.retry_policy, "ok": not problems, "problems": problems}, indent=2))
        return 1 if problems else 0
    if not args.live or os.environ.get(LIVE_ENV) != "1":
        print(f"refusing a live run: pass --live and set {LIVE_ENV}=1, and only with the owner's approval",
              file=sys.stderr)
        return 2
    print("live execution is not available yet (it arrives with the adapters and is approval-gated)", file=sys.stderr)
    return 2
