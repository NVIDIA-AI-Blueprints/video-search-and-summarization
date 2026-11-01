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
  (each planned attempt plus every retry the policy allows), summed over every task and subject the cap scopes,
  and whether every charged task, subject and ledger has a cap. Exit 1 on any problem.
- ``run --config C --dry-run``: plan plus preflight, printed as one statement. A live run is never started from
  here without ``--live`` and ``EVALV2_ALLOW_LIVE=1``; live execution is gated by the owner's approval.
- ``audit frozen-v1``: V1's frozen artifacts are unchanged. ``audit run --store S --run R``: a sealed run's
  index and artifacts are intact.
- ``datasets list`` and ``datasets verify [--data-root D] [--id X ...]``: the registered datasets, and whether each
  one on this machine still has its pinned hashes (a private dataset that is absent is reported, not failed,
  unless named with ``--id``).
- ``review sample|export|import|apply|kappa``: the human-review loop for reference records (a stratified sample,
  the xlsx workbook, its decisions, the records they verify, and agreement between two raters).
- ``verify-v1 --record R [--record ...] [--report P] [--data-root D]``: V2's incident-and-object scoring reproduces
  V1's stored Stage 3 records (no call). Running it on the real records is the approved check RA0.

``plan``, ``preflight`` and ``run`` resolve the config's datasets through the registry; private ones are read from
``--data-root`` (else ``EVALV2_DATA_ROOT``, else ``eval/eval_data``).
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
from evalv2.datasets import registry as dataset_registry
from evalv2.datasets.loader import load_for_config
from evalv2.datasets.records import RecordError, load_records, write_records
from evalv2.datasets.registry import DatasetError, DatasetMissingError
from evalv2.review import kappa as review_kappa
from evalv2.review import sampler as review_sampler
from evalv2.review import workbook as review_workbook
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


def worst_cases(config: RunConfig, items: dict[str, list[WorkItem]]) -> dict[tuple[str, str, str], tuple[int, int]]:
    """The worst-case attempts and tokens of every (task, subject, ledger) the run charges, retries included."""
    retries = POLICIES[config.retry_policy].max_retries
    needs: dict[tuple[str, str, str], tuple[int, int]] = {}
    for name, task_items in items.items():
        estimate = getattr(TASKS[name](), "budget_estimate", None)
        if estimate is None:
            continue
        by_subject: dict[str, list[WorkItem]] = {}
        for item in task_items:
            by_subject.setdefault(item.subject, []).append(item)
        for subject, subject_items in by_subject.items():
            for ledger, need in estimate(config, subject_items).items():
                needs[(name, subject, ledger)] = (need["attempts"] * (1 + retries), need["tokens"] * (1 + retries))
    return needs


def preflight(config: RunConfig, items: dict[str, list[WorkItem]]) -> list[str]:
    """Problems that must be fixed before a run: today, caps that cannot cover the worst case.

    Every (task, subject, ledger) the run charges must fall under at least one cap, honouring each cap's subject
    and task scope. A cap is checked against the summed worst case of every (task, subject) it matches, since all
    of them draw on it: two tasks that each fit a shared cap can still pass it together.
    """
    problems: list[str] = []
    needs = worst_cases(config, items)
    caps = config.budget_caps()
    for (name, subject, ledger) in sorted(needs):
        if not any(cap.matches(ledger, subject, name) for cap in caps):
            problems.append(f"task {name!r} needs ledger {ledger!r} for subject {subject!r} but no cap is set")
    for cap in caps:
        drawn = sorted((key, need) for key, need in needs.items() if cap.matches(key[2], key[1], key[0]))
        if not drawn:
            continue
        attempts, tokens = sum(n[0] for _, n in drawn), sum(n[1] for _, n in drawn)
        covers = ", ".join(f"{name}/{subject}" for (name, subject, _), _ in drawn)
        scope = f"(cap subject={cap.subject}, task={cap.task}; draws: {covers})"
        if cap.max_attempts is not None and cap.max_attempts < attempts:
            problems.append(f"{cap.ledger} attempts cap {cap.max_attempts} < worst case {attempts} {scope}")
        if cap.max_tokens is not None and cap.max_tokens < tokens:
            problems.append(f"{cap.ledger} tokens cap {cap.max_tokens} < worst case {tokens} {scope}")
    return problems


def _summary(items: dict[str, list[WorkItem]]) -> dict[str, Any]:
    return {name: {"items": len(task_items), "by_subject": dict(Counter(i.subject for i in task_items))}
            for name, task_items in items.items()}


def _datasets(args: argparse.Namespace) -> int:
    registry = dataset_registry.load_registry()
    if args.action == "list":
        print(json.dumps([{key: item[key] for key in ("id", "kind", "committed", "verification", "description")}
                          for item in registry["datasets"]], indent=2))
        return 0
    problems = dataset_registry.validate_registry(registry)
    report: dict[str, str] = {}
    for item in registry["datasets"]:
        if args.id and item["id"] not in args.id:
            continue
        try:
            dataset_registry.resolve(item["id"], data_root_path=args.data_root, registry=registry)
            report[item["id"]] = "ok"
        except DatasetMissingError as exc:
            if item["committed"] or args.id:
                problems.append(str(exc))
            report[item["id"]] = "absent" if not (item["committed"] or args.id) else f"missing: {exc}"
        except DatasetError as exc:
            problems.append(str(exc))
            report[item["id"]] = f"changed: {exc}"
    unknown = sorted(set(args.id or []) - {item["id"] for item in registry["datasets"]})
    problems += [f"no dataset {name!r} in the registry" for name in unknown]
    print(json.dumps({"ok": not problems, "datasets": report, "problems": problems}, indent=2))
    return 1 if problems else 0


def _labels(path: Path) -> dict[str, str]:
    """Item id to label from a decisions JSONL (``record_id``, ``decision``) or a plain one (``id``, ``label``).

    Labels are compared as text, like ``--categories``: a severity written as ``3`` in one file and ``"3"`` in the
    other is the same label.
    """
    out = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            out[str(row.get("record_id", row.get("id")))] = str(row["decision"] if "decision" in row else row["label"])
    return out


def _review(args: argparse.Namespace) -> int:
    if args.action == "kappa":
        a, b = _labels(args.a), _labels(args.b)
        if args.categories:
            categories = [c.strip() for c in args.categories.split(",")]
            result = review_kappa.weighted_kappa(a, b, categories=categories, weights=args.weights)
        else:
            result = review_kappa.cohen_kappa(a, b)
        print(json.dumps(result, indent=2))
        return 0
    if args.action == "import":
        decisions = review_workbook.read_workbook(args.workbook)
        review_workbook.write_decisions(decisions, args.out)
        print(json.dumps({"decisions": len(decisions), "out": str(args.out)}, indent=2))
        return 0
    records = load_records(args.records)
    if args.action == "sample":
        sample = review_sampler.sample_for_review(records, seed=args.seed, fraction=args.fraction)
        text = json.dumps(sample.to_json(), indent=2)
        if args.out:
            Path(args.out).write_text(text + "\n", encoding="utf-8")
        print(json.dumps({"sampled": len(sample.ids), "flagged": len(sample.flagged), "sha256": sample.sha256},
                         indent=2))
        return 0
    if args.action == "export":
        sample = None
        if args.seed is not None:
            sample = review_sampler.sample_for_review(records, seed=args.seed, fraction=args.fraction)
        written = review_workbook.export_workbook(records, args.out, sample=sample,
                                                  meta={"records": str(args.records)})
        print(json.dumps(written, indent=2))
        return 0
    updated, counts = review_workbook.apply_decisions(records, review_workbook.read_decisions(args.decisions))
    digest = write_records(updated, args.out)
    print(json.dumps({**counts, "out": str(args.out), "sha256": digest}, indent=2))
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m evalv2", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("plan", "preflight", "run"):
        command = sub.add_parser(name)
        command.add_argument("--config", type=Path, required=True)
        command.add_argument("--data-root", type=Path, default=None)
        if name == "run":
            command.add_argument("--dry-run", action="store_true")
            command.add_argument("--live", action="store_true")
    audit = sub.add_parser("audit")
    audit_sub = audit.add_subparsers(dest="target", required=True)
    audit_sub.add_parser("frozen-v1")
    audit_run = audit_sub.add_parser("run")
    audit_run.add_argument("--store", type=Path, required=True)
    audit_run.add_argument("--run", required=True)
    datasets = sub.add_parser("datasets")
    datasets_sub = datasets.add_subparsers(dest="action", required=True)
    datasets_sub.add_parser("list")
    verify = datasets_sub.add_parser("verify")
    verify.add_argument("--data-root", type=Path, default=None)
    verify.add_argument("--id", action="append")
    review = sub.add_parser("review")
    review_sub = review.add_subparsers(dest="action", required=True)
    sample = review_sub.add_parser("sample")
    sample.add_argument("--records", type=Path, required=True)
    sample.add_argument("--seed", type=int, required=True)
    sample.add_argument("--fraction", type=float, default=0.2)
    sample.add_argument("--out", type=Path)
    export = review_sub.add_parser("export")
    export.add_argument("--records", type=Path, required=True)
    export.add_argument("--out", type=Path, required=True)
    export.add_argument("--seed", type=int, help="sample with this seed; without it every record is exported")
    export.add_argument("--fraction", type=float, default=0.2)
    imported = review_sub.add_parser("import")
    imported.add_argument("--workbook", type=Path, required=True)
    imported.add_argument("--out", type=Path, required=True)
    apply = review_sub.add_parser("apply")
    apply.add_argument("--records", type=Path, required=True)
    apply.add_argument("--decisions", type=Path, required=True)
    apply.add_argument("--out", type=Path, required=True)
    agreement = review_sub.add_parser("kappa")
    agreement.add_argument("--a", type=Path, required=True)
    agreement.add_argument("--b", type=Path, required=True)
    agreement.add_argument("--categories", help="ordered labels, comma-separated, for weighted kappa")
    agreement.add_argument("--weights", choices=("linear", "quadratic"), default="quadratic")
    verify = sub.add_parser("verify-v1")
    verify.add_argument("--record", type=Path, action="append", required=True)
    verify.add_argument("--report", type=Path)
    verify.add_argument("--data-root", type=Path, default=None)
    return parser


def _verify_v1(args: argparse.Namespace) -> int:
    from evalv2.datasets.ground_truth import load_gt_v1_sheets
    from evalv2.verify_v1 import verify

    result = verify(args.record, load_gt_v1_sheets(data_root=args.data_root), args.report)
    print(json.dumps(result, indent=2))
    return 0 if result["ok"] else 1


def main(argv: Sequence[str] | None = None, *, load_datasets: DatasetLoader | None = None) -> int:
    args = _parser().parse_args(argv)

    if args.command == "audit":
        problems = (frozen_v1.drift() if args.target == "frozen-v1"
                    else ArtifactStore(args.store).run(args.run).verify())
        print(json.dumps({"ok": not problems, "problems": problems}, indent=2))
        return 1 if problems else 0
    if args.command == "verify-v1":
        try:
            return _verify_v1(args)
        except DatasetError as exc:
            print(json.dumps({"ok": False, "problems": [str(exc)]}, indent=2))
            return 1
    if args.command in ("datasets", "review"):
        try:
            return _datasets(args) if args.command == "datasets" else _review(args)
        except (DatasetError, RecordError, review_workbook.ReviewImportError, PermissionError, ValueError) as exc:
            print(json.dumps({"ok": False, "problems": [str(exc)]}, indent=2))
            return 1

    config = load_config(args.config)
    loader = load_datasets or (lambda cfg: load_for_config(cfg, data_root=args.data_root))
    try:
        items = plan(config, loader)
    except (KeyError, DatasetError) as exc:
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
