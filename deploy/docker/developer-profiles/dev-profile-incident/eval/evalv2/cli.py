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
  and whether every charged task, subject and ledger has a cap. A task that names judges (``options.judges``) must
  name registered judges whose roles, kinds and families fit, and the run must cap the judge ledger. Exit 1 on any
  problem.
- ``run --config C --dry-run``: plan plus preflight, printed as one statement.
- ``run --config C --live --approval M --out DIR``: the live executor (:mod:`evalv2.executor`), only with
  ``EVALV2_ALLOW_LIVE=1`` and the owner's approval marker (:mod:`evalv2.approval`); the same command resumes.
  ``approval draft --config C`` prints a marker skeleton for the owner (it approves nothing).
- ``score --config C --out DIR [--offline]``: score a live run's stored predictions (:mod:`evalv2.score_stage`); a
  live score calls the judge under the marker's judge budget, ``--offline`` uses the caches only.
- ``audit frozen-v1``: V1's frozen artifacts are unchanged. ``audit run --store S --run R``: a sealed run's
  index and artifacts are intact.
- ``datasets list`` and ``datasets verify [--data-root D] [--id X ...]``: the registered datasets, and whether each
  one on this machine still has its pinned hashes (a private dataset that is absent is reported, not failed,
  unless named with ``--id``).
- ``review sample|export|import|apply|kappa``: the human-review loop for reference records (a stratified sample,
  the xlsx workbook, its decisions, the records they verify, and agreement between two raters).
- ``verify-v1 --record R [--record ...] [--report P] [--data-root D]``: V2's incident-and-object scoring reproduces
  V1's stored Stage 3 records (no call). Running it on the real records is the approved check RA0.
- ``references ra1|ra2|rb2 --out DIR``: advance a reference run as far as its passes' answers allow. No model is
  called: packets go to ``DIR/generator`` and ``DIR/validator`` for Claude Code sessions, and their answers come
  back as files. ``--dry-run`` uses scripted passes (and, for RA1, a synthetic detector) for plumbing checks only.
  Running one for real is an approved run (RA1, RA2, RB2); RA1's detector is X5.

``plan``, ``preflight`` and ``run`` resolve the config's datasets through the registry; private ones are read from
``--data-root`` (else ``EVALV2_DATA_ROOT``, else ``eval/eval_data``).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shlex
import sys
from collections import Counter
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from evalv2.core import frozen_v1
from evalv2.core.artifacts import ArtifactStore
from evalv2.core.config import RunConfig, load_config
from evalv2.core.private import ensure_private
from evalv2.core.retry import POLICIES
from evalv2.datasets import registry as dataset_registry
from evalv2.datasets.loader import load_for_config
from evalv2.datasets.records import RecordError, load_records, write_records
from evalv2.datasets.registry import DatasetError, DatasetMissingError
from evalv2.review import kappa as review_kappa
from evalv2.review import sampler as review_sampler
from evalv2.review import workbook as review_workbook
from evalv2.tasks.base import TASKS, WorkItem
from evalv2.tasks.judges import judge_problems

LIVE_ENV = "EVALV2_ALLOW_LIVE"
DatasetLoader = Callable[[RunConfig], dict[str, Any]]
REFERENCE_SEED = 20261101
REFERENCE_CLOCK = "2026-11-01T12:00:00+00:00"  # the dry run's clock; the C2 freeze pins the real one
DEFAULT_REFERENCE_DATASETS = {"ra1": "box-references-v1", "ra2": "chat-references-v1",
                              "rb2": "dashboard-references-v1"}


def _no_datasets(config: RunConfig) -> dict[str, Any]:
    return {}


def plan(config: RunConfig, load_datasets: DatasetLoader = _no_datasets) -> dict[str, list[WorkItem]]:
    """Every task's work items. Raises ``KeyError`` for a task no package has registered."""
    unknown = sorted(set(config.tasks) - set(TASKS))
    if unknown:
        raise KeyError(f"no task registered for {unknown}; registered: {sorted(TASKS)}")
    datasets = load_datasets(config)
    items = {name: TASKS[name]().plan(config, datasets) for name in config.tasks}
    return items if config.recovery is None else _recovery_items(config, items)


def _recovery_items(config: RunConfig, items: dict[str, list[WorkItem]]) -> dict[str, list[WorkItem]]:
    """Keep only the open items a recovery run names; a name that matches no planned item is an error."""
    wanted = {(i.subject, i.item_id, i.repeat) for i in config.recovery.items}
    kept = {name: [i for i in task_items if (i.subject, i.item_id, i.repeat) in wanted]
            for name, task_items in items.items()}
    found = {(i.subject, i.item_id, i.repeat) for task_items in kept.values() for i in task_items}
    missing = sorted(wanted - found)
    if missing:
        raise ValueError(f"recovery.items names {len(missing)} item(s) the plan does not hold: {missing[:5]}")
    return kept


def worst_cases(config: RunConfig, items: dict[str, list[WorkItem]]) -> dict[tuple[str, str, str], tuple[int, int]]:
    """The worst-case attempts and tokens of every (task, subject, ledger) the run charges, retries included."""
    needs: dict[tuple[str, str, str], tuple[int, int]] = {}
    for name, task_items in items.items():
        runner = TASKS[name]()
        estimate = getattr(runner, "budget_estimate", None)
        if estimate is None:
            continue
        # A pipeline task's worst case already counts the pipeline's own retries, and it never re-sends a run.
        retries = 0 if getattr(runner, "retries_included", False) else POLICIES[config.retry_policy].max_retries
        by_subject: dict[str, list[WorkItem]] = {}
        for item in task_items:
            by_subject.setdefault(item.subject, []).append(item)
        for subject, subject_items in by_subject.items():
            for ledger, need in estimate(config, subject_items).items():
                needs[(name, subject, ledger)] = (need["attempts"] * (1 + retries), need["tokens"] * (1 + retries))
    return needs


def preflight(config: RunConfig, items: dict[str, list[WorkItem]]) -> list[str]:
    """Problems that must be fixed before a run: caps that cannot cover the worst case, and judge selections.

    Every (task, subject, ledger) the run charges must fall under at least one cap, honouring each cap's subject
    and task scope. A cap is checked against the summed worst case of every (task, subject) it matches, since all
    of them draw on it: two tasks that each fit a shared cap can still pass it together.
    """
    problems, shortfalls = preflight_report(config, items)
    return problems + shortfalls


def preflight_problems(config: RunConfig, items: dict[str, list[WorkItem]]) -> tuple[list[str], list[str]]:
    """``(blocking, hard_stop)``: what stops a run, and what only says it may stop at a cap.

    A shortfall (a cap below the worst case) blocks under ``budget_mode: worst_case``. Under ``hard_stop`` (caps set
    to an approved budget below the pessimistic worst case) it is only a risk that the run stops at a cap, since the
    ledger refuses any attempt that would pass a cap before it is sent.
    """
    problems, shortfalls = preflight_report(config, items)
    if config.budget_mode == "hard_stop":
        return problems, shortfalls
    return problems + shortfalls, []


def preflight_report(config: RunConfig, items: dict[str, list[WorkItem]]) -> tuple[list[str], list[str]]:
    """``(problems, shortfalls)``: every problem, with the caps below the worst case kept apart."""
    problems: list[str] = []
    shortfalls: list[str] = []
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
            shortfalls.append(f"{cap.ledger} attempts cap {cap.max_attempts} < worst case {attempts} {scope}")
        if cap.max_tokens is not None and cap.max_tokens < tokens:
            shortfalls.append(f"{cap.ledger} tokens cap {cap.max_tokens} < worst case {tokens} {scope}")
    return problems + judge_problems(config), shortfalls


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
    strata = _strata(args.strata) if args.action in ("sample", "export") else review_sampler.DEFAULT_STRATA
    if args.action == "sample":
        sample = review_sampler.sample_for_review(records, seed=args.seed, fraction=args.fraction, strata=strata)
        text = json.dumps(sample.to_json(), indent=2)
        if args.out:
            Path(args.out).write_text(text + "\n", encoding="utf-8")
        print(json.dumps({"sampled": len(sample.ids), "flagged": len(sample.flagged), "sha256": sample.sha256},
                         indent=2))
        return 0
    if args.action == "export":
        sample = None
        if args.seed is not None:
            sample = review_sampler.sample_for_review(records, seed=args.seed, fraction=args.fraction, strata=strata)
        links = json.loads(Path(args.links).read_text(encoding="utf-8")) if args.links else None
        written = review_workbook.export_workbook(records, args.out, sample=sample, links=links,
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
            command.add_argument("--approval", type=Path, help="the owner's evalv2-live-approval-v1 marker")
            command.add_argument("--out", type=Path, help="private run directory; the same directory resumes the run")
            command.add_argument("--env-file", type=Path, action="append",
                                 help="credentials file (default: eval/.env.local, then the profile's .env.local)")
            command.add_argument("--allow-dirty", action="store_true",
                                 help="run from an eval tree with uncommitted changes (recorded in the manifest)")
            command.add_argument("--rpm", type=int, help="Track A requests per minute (default: the gateway's 60)")
            command.add_argument("--parallel", type=int,
                                 help="Track A calls in flight (default and maximum: the gateway's 4)")
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
    score = sub.add_parser("score", help="score a live run's stored predictions (judge and embeddings cached)")
    score.add_argument("--config", type=Path, required=True)
    score.add_argument("--out", type=Path, required=True, help="the run directory run --live wrote")
    score.add_argument("--data-root", type=Path, default=None)
    score.add_argument("--offline", action="store_true", help="caches only, under the network guard: no call")
    score.add_argument("--embed-local", action="store_true",
                       help="with --offline: compute missing MiniLM vectors locally (no network, no judge)")
    score.add_argument("--approval", type=Path, help="the marker approving this run's judge budget (live only)")
    score.add_argument("--slot", choices=("primary", "sensitivity"), default="primary")
    score.add_argument("--judge-ledger-dir", type=Path,
                       help="judge ledger shared by every scoring stage (default $EVALV2_DATA_ROOT/v2/judge-ledger)")
    score.add_argument("--env-file", type=Path, action="append")
    approval = sub.add_parser("approval", help="draft a live-run approval marker for the owner (approves nothing)")
    approval_sub = approval.add_subparsers(dest="action", required=True)
    drafted = approval_sub.add_parser("draft")
    drafted.add_argument("--config", type=Path, required=True)
    drafted.add_argument("--data-root", type=Path, default=None)
    review = sub.add_parser("review")
    review_sub = review.add_subparsers(dest="action", required=True)
    strata_help = ("comma-separated record fields to stratify by (default kind,tier,strata.category; chat records "
                   "have strata.class, dashboard records strata.class and strata.dimension)")
    sample = review_sub.add_parser("sample")
    sample.add_argument("--records", type=Path, required=True)
    sample.add_argument("--seed", type=int, required=True)
    sample.add_argument("--fraction", type=float, default=0.2)
    sample.add_argument("--strata", help=strata_help)
    sample.add_argument("--out", type=Path)
    export = review_sub.add_parser("export")
    export.add_argument("--records", type=Path, required=True)
    export.add_argument("--out", type=Path, required=True)
    export.add_argument("--seed", type=int, help="sample with this seed; without it every record is exported")
    export.add_argument("--fraction", type=float, default=0.2)
    export.add_argument("--strata", help=strata_help)
    export.add_argument("--links", type=Path, help="JSON record id -> link (box review sheets: v2_box_frames.py sheet)")
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
    severity = sub.add_parser("severity-report", help="severity MAE (primary), exact and QWK per model from saved "
                              "per-video scores; no model, judge or network")
    severity.add_argument("--videos", type=Path, action="append", required=True,
                          help="a score output's *-videos.jsonl; repeat for a pilot and its recovery run")
    severity.add_argument("--out", type=Path, required=True, help="private output directory")
    severity.add_argument("--title", default="Severity")
    references = sub.add_parser("references", help="advance a reference run (RA1, RA2, RB2); no model is called")
    references_sub = references.add_subparsers(dest="run", required=True)
    for name in ("ra1", "ra2", "rb2"):
        command = references_sub.add_parser(name)
        command.add_argument("--out", type=Path, required=True, help="private run directory (resumable)")
        command.add_argument("--data-root", type=Path, default=None)
        command.add_argument("--seed", type=int, default=REFERENCE_SEED)
        command.add_argument("--dataset", help="dataset id of the records (a dry run appends -dryrun)")
        command.add_argument("--dry-run", action="store_true",
                             help="scripted passes (and RA1's synthetic detector): plumbing only, never references")
        command.add_argument("--generator-model", help="the model the generator session runs (required unless dry)")
        command.add_argument("--validator-model", help="the model the blind validator session runs")
        if name in ("ra1", "ra2"):
            command.add_argument("--half", choices=("dev", "test", "all"), default="all")
        if name == "ra1":
            command.add_argument("--detector-command", help="the offline detector (JSON protocol; weights are X5)")
            command.add_argument("--frames-root", type=Path, help="extracted frames (default <out>/frames)")
            command.add_argument("--frame-error", type=float, default=0.01, help="dry run: scripted error per frame")
            command.add_argument("--plan-only", action="store_true",
                                 help="write the extraction plan and the plan's counts, then stop (no detector)")
        if name == "ra2":
            command.add_argument("--per-class-dev", type=int, default=5)
            command.add_argument("--per-class-test", type=int, default=10)
        if name == "rb2":
            command.add_argument("--allowlist", type=Path, help="default: the console's export in contracts/")
            command.add_argument("--unsupported-share", type=float, default=0.2)
            command.add_argument("--clock", default=REFERENCE_CLOCK, help="frozen clock of the corpus snapshot")
    return parser


def _strata(text: str | None) -> tuple[str, ...]:
    if not text:
        return review_sampler.DEFAULT_STRATA
    fields = tuple(part.strip() for part in text.split(",") if part.strip())
    if not fields:
        raise ValueError("--strata names no field")
    return fields


def _passes(args: argparse.Namespace, generator_prompt: str, validator_prompt: str) -> tuple[Any, Any, str]:
    """The run's generator and validator passes and its dataset id: scripted for a dry run, else file handoff."""
    from evalv2.datasets import generation_prompts
    from evalv2.references import passes

    if args.dry_run:
        return None, None, f"{args.dataset or DEFAULT_REFERENCE_DATASETS[args.run]}-dryrun"
    if not args.generator_model or not args.validator_model:
        raise ValueError("a reference run names --generator-model and --validator-model (the sessions' models)")

    def identity(model: str, prompt: str) -> dict[str, Any]:
        return {"model": model, "prompt": prompt,
                "prompt_sha256": hashlib.sha256(generation_prompts.prompt_text(prompt).encode()).hexdigest()}

    generator = passes.FilePass(args.out / "generator", identity(args.generator_model, generator_prompt))
    validator = passes.FilePass(args.out / "validator", {**identity(args.validator_model, validator_prompt),
                                                         "id": f"{args.validator_model}:{validator_prompt}"})
    dataset = args.dataset or DEFAULT_REFERENCE_DATASETS[args.run]
    if dataset.endswith("-dryrun"):
        raise ValueError("a -dryrun dataset id is for scripted passes only")
    return generator, validator, dataset


def _references(args: argparse.Namespace) -> int:
    from evalv2.datasets.ground_truth import half, load_gt_v1, load_split_v2
    from evalv2.datasets.registry import EVAL_DIR, data_root
    from evalv2.references import dry_run, ra1, ra2, rb2

    if args.run == "rb2":
        from evalv2.references import dashboard

        allowlist = dashboard.load_allowlist(args.allowlist) if args.allowlist else dashboard.load_allowlist()
        generator, validator, dataset = _passes(args, rb2.GENERATOR_PROMPT, rb2.VALIDATOR_PROMPT)
        if args.dry_run:  # a 5% scripted validator error exercises the disagreement path
            generator, validator = dry_run.dashboard_generator(), dry_run.dashboard_validator(seed=args.seed,
                                                                                              error=0.05)
        gt = load_gt_v1(data_root=args.data_root)
        planned = rb2.spec_plan(allowlist, seed=args.seed, unsupported_share=args.unsupported_share)
        summary = rb2.run(allowlist, planned, generator=generator, validator=validator, out=args.out, dataset=dataset)
        summary["corpus"] = rb2.corpus_figures(
            {clip: entry["p1_shaped"] for clip, entry in gt["by_incident_id"].items()},
            [e["expected"] for e in planned if not dashboard.is_refusal(e["expected"])], allowlist,
            taken_at=args.clock, model_name="ground-truth-v1.0", out=args.out)
        print(json.dumps(summary, indent=2, sort_keys=True))
        return 0
    gt, split = load_gt_v1(data_root=args.data_root), load_split_v2()
    halves = ("dev", "test") if args.half == "all" else (args.half,)
    clips = {name: half(split, name, gt_split=gt["split"]) for name in halves}
    if args.run == "ra2":
        gt_halves = {name: ra2.restrict(gt, clips[name]) for name in halves}
        per_class = {"dev": args.per_class_dev, "test": args.per_class_test}
        plans = {name: ra2.question_plan(gt_halves[name], half=name, seed=args.seed, per_class=per_class[name])
                 for name in halves}
        generator, validator, dataset = _passes(args, ra2.GENERATOR_PROMPT, ra2.VALIDATOR_PROMPT)
        if args.dry_run:
            generator, validator = dry_run.chat_generator(gt_halves), dry_run.chat_validator()
        summary = ra2.run(gt_halves, plans, generator=generator, validator=validator, out=args.out, dataset=dataset)
        print(json.dumps(summary, indent=2, sort_keys=True))
        return 0
    manifest = json.loads((EVAL_DIR / "eval_reproducibility" / "dataset-manifest-v1.json").read_text(encoding="utf-8"))
    root = data_root(args.data_root)
    durations = {clip["incident_id"]: float(clip["duration_seconds"]) for clip in manifest["clips"]}
    videos = {clip["incident_id"]: root / Path(clip["local_path"]).relative_to("eval_data")
              for clip in manifest["clips"]}
    chosen = [clip for name in halves for clip in clips[name]]
    planned = ra1.plan(gt, chosen, durations, videos=videos)
    frames_root = args.frames_root or args.out / "frames"
    ensure_private(args.out)
    args.out.mkdir(parents=True, exist_ok=True)
    extraction = args.out / "extraction-plan.json"
    extraction.write_text(json.dumps(planned["extraction"], indent=1) + "\n", encoding="utf-8")
    if args.plan_only:
        frames = sum(len(c["times"]) for c in planned["extraction"]["clips"])
        print(json.dumps({"run": "RA1", "plan_only": True, "clips": len(planned["clips"]),
                          "items": len(planned["items"]), "frames": frames, "extraction_plan": str(extraction)},
                         indent=2))
        return 0
    generator, validator, dataset = _passes(args, ra1.GENERATOR_PROMPT, ra1.VALIDATOR_PROMPT)
    detector: Any = None
    if args.dry_run:
        scene = dry_run.SyntheticScene(planned, seed=args.seed)
        detector = scene.detector(frames_root)
        generator = dry_run.box_pass(scene, role="generator", prompt=ra1.GENERATOR_PROMPT,
                                     frame_error=args.frame_error)
        validator = dry_run.box_pass(scene, role="validator", prompt=ra1.VALIDATOR_PROMPT,
                                     frame_error=args.frame_error)
    elif args.detector_command:
        from evalv2.references.detector import SubprocessDetector

        detector = SubprocessDetector(shlex.split(args.detector_command))
    summary = ra1.run(planned, detector=detector, generator=generator, validator=validator, out=args.out,
                      frames_root=frames_root, dataset=dataset)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


def _verify_v1(args: argparse.Namespace) -> int:
    from evalv2.datasets.ground_truth import load_gt_v1_sheets
    from evalv2.verify_v1 import verify

    result = verify(args.record, load_gt_v1_sheets(data_root=args.data_root), args.report)
    print(json.dumps(result, indent=2))
    return 0 if result["ok"] else 1


def _score(args: argparse.Namespace, load_datasets: DatasetLoader | None,
           load_sheets: Callable[[], dict[str, Any]] | None) -> int:
    from evalv2.approval import ApprovalRefused, read_approval
    from evalv2.datasets.ground_truth import load_gt_v1_sheets
    from evalv2.datasets.registry import data_root
    from evalv2.score_stage import ScoreRefused, run_score

    config = load_config(args.config)
    loader = load_datasets or (lambda cfg: load_for_config(cfg, data_root=args.data_root))
    marker = None
    if not args.offline:
        if os.environ.get(LIVE_ENV) != "1":
            print(f"refusing a live score (judge calls): set {LIVE_ENV}=1 with the owner's approval, or pass "
                  "--offline", file=sys.stderr)
            return 2
        if args.approval is not None:
            try:
                marker = read_approval(args.approval)
            except ApprovalRefused as exc:
                print(json.dumps({"ok": False, "refused": [str(exc)]}, indent=2))
                return 2
        try:
            load_env_files(args.env_file)
        except FileNotFoundError as exc:
            print(json.dumps({"ok": False, "refused": [str(exc)]}, indent=2))
            return 2
    ledger_dir = args.judge_ledger_dir or (data_root(args.data_root) / "v2" / "judge-ledger")
    try:
        datasets = loader(config)
        sheets = load_sheets() if load_sheets else load_gt_v1_sheets(data_root=args.data_root)
        result = run_score(config, args.out, datasets=datasets, sheets=sheets, offline=args.offline, slot=args.slot, embed_local=args.embed_local,
                           marker=marker, judge_ledger_dir=ledger_dir)
    except ScoreRefused as exc:
        print(json.dumps({"ok": False, "refused": exc.problems}, indent=2))
        return 2
    except DatasetError as exc:
        print(json.dumps({"ok": False, "problems": [str(exc)]}, indent=2))
        return 1
    summary = result["summary"]
    print(json.dumps({"ok": True, "experiment": summary["experiment"], "final_items": summary["final_items"],
                      "unscored": summary["unscored"], "mode": summary["mode"],
                      "outputs": str(args.out / "scoring" / args.slot)}, indent=2))
    return 0


def _approval(args: argparse.Namespace, load_datasets: DatasetLoader | None) -> int:
    from evalv2.approval import draft

    config = load_config(args.config)
    loader = load_datasets or (lambda cfg: load_for_config(cfg, data_root=args.data_root))
    print(json.dumps(draft(config, plan(config, loader)), indent=2))
    return 0


def main(argv: Sequence[str] | None = None, *, load_datasets: DatasetLoader | None = None,
         load_sheets: Callable[[], dict[str, Any]] | None = None) -> int:
    args = _parser().parse_args(argv)

    if args.command == "score":
        return _score(args, load_datasets, load_sheets)
    if args.command == "approval":
        try:
            return _approval(args, load_datasets)
        except (KeyError, DatasetError) as exc:
            print(json.dumps({"ok": False, "problems": [str(exc)]}, indent=2))
            return 1

    if args.command == "audit":
        problems = (frozen_v1.drift() if args.target == "frozen-v1"
                    else ArtifactStore(args.store).run(args.run).verify())
        print(json.dumps({"ok": not problems, "problems": problems}, indent=2))
        return 1 if problems else 0
    if args.command == "severity-report":
        from evalv2 import severity_report

        try:
            result = severity_report.report(severity_report.load_rows(args.videos))
        except severity_report.SeverityReportError as exc:
            print(json.dumps({"ok": False, "problems": [str(exc)]}, indent=2))
            return 1
        written = severity_report.write(result, args.out, args.title)
        print(json.dumps({"ok": True, "out": str(written), "rows": [
            {"subject": r["subject"], "stage": r["stage"], **{k: r["severity"][k] for k in
                                                            ("mae", "exact", "qwk", "n", "attempted", "coverage")}}
            for r in result["rows"]]}, indent=2))
        return 0
    if args.command == "verify-v1":
        try:
            return _verify_v1(args)
        except DatasetError as exc:
            print(json.dumps({"ok": False, "problems": [str(exc)]}, indent=2))
            return 1
    if args.command in ("datasets", "review", "references"):
        handlers = {"datasets": _datasets, "review": _review, "references": _references}
        try:
            return handlers[args.command](args)
        except (DatasetError, RecordError, review_workbook.ReviewImportError, PermissionError, ValueError) as exc:
            print(json.dumps({"ok": False, "problems": [str(exc)]}, indent=2))
            return 1

    config = load_config(args.config)
    base_loader = load_datasets or (lambda cfg: load_for_config(cfg, data_root=args.data_root))
    loaded: dict[str, Any] = {}

    def loader(cfg: RunConfig) -> dict[str, Any]:  # loaded and verified once, for the plan and a live run
        if "datasets" not in loaded:
            loaded["datasets"] = base_loader(cfg)
        return loaded["datasets"]

    try:
        items = plan(config, loader)
    except (KeyError, ValueError, DatasetError) as exc:
        print(json.dumps({"ok": False, "problems": [str(exc)]}, indent=2))
        return 1
    if args.command == "plan":
        print(json.dumps({"experiment": config.experiment, "tasks": _summary(items)}, indent=2))
        return 0
    problems, hard_stop = preflight_problems(config, items)
    if args.command == "preflight" or args.dry_run:
        report = {"experiment": config.experiment, "half": config.split.half, "tasks": _summary(items),
                  "retry_policy": config.retry_policy, "ok": not problems, "problems": problems}
        if config.budget_mode == "hard_stop":
            report.update(budget_mode="hard_stop", hard_stop=hard_stop)
        print(json.dumps(report, indent=2))
        return 1 if problems else 0
    if not args.live or os.environ.get(LIVE_ENV) != "1":
        print(f"refusing a live run: pass --live and set {LIVE_ENV}=1, and only with the owner's approval",
              file=sys.stderr)
        return 2
    return _run_live(args, config, loader, items, problems, hard_stop)


def load_env_files(paths: Sequence[Path] | None = None) -> list[str]:
    """Load credentials from ``.env.local`` files into the environment; the names of the files read are returned,
    never a value.

    A non-empty value already in the environment wins; an empty one does not. V1's ``eval/config.py`` loads the
    profile's committed ``.env``, whose credential names are blank placeholders, so a plain ``override=False`` load
    would leave them blank and the run would refuse for a credential the file holds."""
    from dotenv import dotenv_values

    from evalv2.executor import EVAL_DIR

    if paths:
        absent = [str(path) for path in paths if not Path(path).is_file()]
        if absent:  # an explicit file that is not there is an error, never a silent skip
            raise FileNotFoundError(f"--env-file names a file that does not exist: {absent}")
    candidates = list(paths) if paths else [EVAL_DIR / ".env.local", EVAL_DIR.parent / ".env.local"]
    read = []
    for path in candidates:
        if Path(path).is_file():
            for name, value in dotenv_values(path).items():
                if value and not os.environ.get(name):
                    os.environ[name] = value
            read.append(str(path))
    return read


def _lazy_signer() -> Callable[[str, int], str]:
    from evalv2.clips import r2_signer

    made: list[Callable[[str, int], str]] = []

    def sign(key: str, ttl: int) -> str:
        if not made:
            made.append(r2_signer())
        return made[0](key, ttl)

    return sign


def _run_live(args: argparse.Namespace, config: RunConfig, loader: DatasetLoader, items: dict[str, list[WorkItem]],
              problems: list[str], hard_stop: list[str]) -> int:
    from evalv2.approval import ApprovalRefused, read_approval
    from evalv2.executor import LiveExecutor, LiveRefused, RunStopped, default_pacer, load_manifest_clips, _track

    refusals = list(problems)
    marker: dict[str, Any] = {}
    if args.approval is None:
        refusals.append("a live run needs an approval marker (--approval FILE, evalv2-live-approval-v1)")
    else:
        try:
            marker = read_approval(args.approval)
        except ApprovalRefused as exc:
            refusals.append(str(exc))
    if args.out is None:
        refusals.append("a live run needs --out DIR (a private run directory; the same DIR resumes it)")
    if refusals:
        print(json.dumps({"ok": False, "refused": refusals}, indent=2))
        return 2
    try:
        env_files = load_env_files(args.env_file)
    except FileNotFoundError as exc:
        print(json.dumps({"ok": False, "refused": [str(exc)]}, indent=2))
        return 2
    try:
        track = _track(config)
        executor = LiveExecutor(config, loader(config), items, out=args.out, marker=marker, hard_stop=hard_stop,
                                manifest_clips=load_manifest_clips(), signer=_lazy_signer(),
                                pacer=default_pacer(track, rpm=args.rpm, parallel=args.parallel),
                                allow_dirty=args.allow_dirty, log=lambda line: print(line, file=sys.stderr))
        summary = executor.run()
    except LiveRefused as exc:
        print(json.dumps({"ok": False, "refused": exc.problems, "env_files": env_files}, indent=2))
        return 2
    except RunStopped as exc:
        print(json.dumps({"ok": False, "stopped": str(exc), "resume": "fix the cause, then run the same command"},
                         indent=2))
        return 3
    print(json.dumps({"ok": True, **summary}, indent=2))
    return 0 if summary["sealed"] else 4
