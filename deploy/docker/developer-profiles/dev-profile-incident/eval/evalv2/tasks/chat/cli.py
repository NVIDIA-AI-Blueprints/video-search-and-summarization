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

"""``python -m evalv2.tasks.chat prepare | estimate | run | score | readiness`` (C1c-chat).

- ``prepare --config C --out DIR`` (eval Python): plan the work items and freeze the corpus snapshots into
  ``DIR/plan.json`` and ``DIR/corpora/`` (the agent's Python cannot read the ground truth workbook loader).
- ``estimate --config C --out DIR``: the offline token estimate of every planned call, from the registered chat
  prompts and the prepared corpus contexts (agent Python; no call).
- ``run --config C --out DIR``: the harness (agent Python). Without ``--live`` the chat model is the fake and the
  network is blocked. ``--live --approval M`` (with ``EVALV2_ALLOW_LIVE=1``) uses the agent's own chat client under
  the config's and the marker's caps. The same command resumes; ``--retry-errors`` re-asks ``http_error`` items.
- ``score --config C --out DIR``: score a run's answers offline (eval Python): ``DIR/score/report.json``.
- ``readiness --config C [--out DIR]``: the reference readiness report (eval Python).

``plan`` and ``preflight`` are the generic ``python -m evalv2 plan|preflight --config C``. Private data is read
from ``--data-root`` (else ``EVALV2_DATA_ROOT``); every output directory must be private.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from collections.abc import Sequence
from contextlib import nullcontext
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from evalv2.core.budget import BudgetLedger
from evalv2.core.config import RunConfig, load_config
from evalv2.core.hashing import sha256_json
from evalv2.core.netguard import no_network
from evalv2.core.private import ensure_private
from evalv2.tasks.chat import corpus as corpora
from evalv2.tasks.chat import harness
from evalv2.tasks.base import WorkItem
from evalv2.tasks.chat.runner import TASK, ChatTask, load_references, options, references_path

LIVE_ENV = "EVALV2_ALLOW_LIVE"
READINESS_DIR = Path("v2/references/chat-references-v1/readiness")
REFERENCE_SEED = 20261101  # the reference runs' seed (cli.REFERENCE_SEED), so the review sample matches RA2's


def _datasets(config: RunConfig, data_root: Path | None) -> dict[str, Any]:
    from evalv2.datasets.loader import load_for_config

    return load_for_config(config, data_root=data_root)


def _config_sha256(config: RunConfig) -> str:
    return sha256_json(config.model_dump(mode="json"))


def snapshots(config: RunConfig, datasets: dict[str, Any], data_root: Path | None) -> dict[str, dict[str, Any]]:
    from evalv2.datasets.registry import data_root as root

    opts = options(config)
    clips = list(datasets["clips"])
    out = {}
    if "oracle" in opts["corpora"]:
        out["oracle"] = corpora.oracle_snapshot(datasets["incident_gt"], clips)
    if "system" in opts["corpora"]:
        system = opts["system"]
        predictions = corpora.load_system_predictions(root(data_root) / system["store"], system["run"],
                                                      system["subject"], repeat=int(system.get("repeat", 1)))
        out["system"] = corpora.system_snapshot(predictions, clips, stage=system.get("stage", "final"),
                                                source={k: system[k] for k in ("run", "subject") if k in system})
    return out


def _prepare(args: argparse.Namespace) -> int:
    """Plan the items and freeze the corpora into the run directory (eval Python: it reads the ground truth)."""
    ensure_private(args.out)
    config = load_config(args.config)
    datasets = _datasets(config, args.data_root)
    records = load_references(options(config), args.data_root)
    items = ChatTask(data_root=args.data_root).plan(config, {**datasets, "chat_references": records})
    snaps = snapshots(config, datasets, args.data_root)
    (args.out / "corpora").mkdir(parents=True, exist_ok=True)
    digests = {}
    for name, snap in snaps.items():
        (args.out / "corpora" / f"{name}.json").write_text(json.dumps(snap, sort_keys=True, ensure_ascii=False),
                                                         encoding="utf-8")
        digests[name] = corpora.snapshot_sha256(snap)
    plan = {"experiment": config.experiment, "config_sha256": _config_sha256(config),
            "references_sha256": options(config)["references"]["sha256"], "snapshots": digests,
            "items": [{"key": i.key, "task": i.task, "subject": i.subject, "item_id": i.item_id, "repeat": i.repeat,
                       "spec": dict(i.spec)} for i in items]}
    path = args.out / "plan.json"
    if path.is_file():
        stored = json.loads(path.read_text(encoding="utf-8"))
        if stored != plan:
            print(json.dumps({"ok": False, "problems": [f"{path} holds another plan; use a new --out"]}, indent=2))
            return 1
    path.write_text(json.dumps(plan, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"ok": True, "plan": str(path), "items": len(items),
                      "by_corpus": dict(Counter(i.spec["corpus"] for i in items)),
                      "by_scope": dict(Counter(i.spec["scope"] for i in items)),
                      "snapshots": {n: {"reports": len(s["reports"]), "missing": s["source"].get("missing", []),
                                        "sha256": digests[n]} for n, s in snaps.items()}}, indent=2))
    return 0


def load_prepared(config: RunConfig, out: Path, data_root: Path | None
                  ) -> tuple[list[WorkItem], dict[str, Any], dict[str, dict[str, Any]]]:
    """The prepared plan, references and snapshots, each checked against the config and the plan's hashes."""
    path = Path(out) / "plan.json"
    if not path.is_file():
        raise harness.HarnessError(f"{path} not found: run `prepare` first (eval Python)")
    plan = json.loads(path.read_text(encoding="utf-8"))
    if plan["config_sha256"] != _config_sha256(config):
        raise harness.HarnessError("the prepared plan is for another config; prepare a new --out")
    items = [WorkItem(i["task"], i["subject"], i["item_id"], int(i["repeat"]), i["spec"]) for i in plan["items"]]
    if any(item.key != row["key"] for item, row in zip(items, plan["items"], strict=True)):
        raise harness.HarnessError("the prepared plan's work keys do not match its items")
    records = {r["record_id"]: r for r in load_references(options(config), data_root)}
    snaps = {}
    for name, digest in plan["snapshots"].items():
        snap = json.loads((Path(out) / "corpora" / f"{name}.json").read_text(encoding="utf-8"))
        if corpora.snapshot_sha256(snap) != digest:
            raise harness.HarnessError(f"corpus snapshot {name} changed since it was prepared")
        snaps[name] = snap
    return items, records, snaps


def _estimate(args: argparse.Namespace) -> int:
    from vss_agents.utils import grounded_chat

    config = load_config(args.config)
    items, records, snaps = load_prepared(config, args.out, args.data_root)
    calls: dict[str, list[int]] = {"incident": [], "library_plan": [], "library_answer": []}
    for item in items:
        payload = records[item.spec["record_id"]]["payload"]
        snap = snaps[item.spec["corpus"]]
        history = [{"role": "user", "content": payload["question"]}]
        if payload["scope"] == "incident":
            report = snap["reports"].get(payload["params"]["incident_id"])
            if report is None:
                continue
            context = grounded_chat.report_context(report["payload"], report["evidence"])
            calls["incident"].append(harness.call_tokens_bound(grounded_chat.chat_messages(context, history)))
            continue
        calls["library_plan"].append(harness.call_tokens_bound(grounded_chat.plan_messages(payload["question"],
                                                                                           history)))
        library = [grounded_chat.LibraryReport(incident_id=i, version_id=r["version_id"], payload=r["payload"],
                                               evidence=r["evidence"]) for i, r in sorted(snap["reports"].items())]
        biggest = sorted(library, key=lambda r: -len(json.dumps(r.payload)))[:grounded_chat.MAX_LIBRARY_REPORTS]
        plan = {"incident_type": None, "severity": None, "instrument": None, "asset": None,
                "query": payload["question"], "wants_count": False, "group_by": None, "dropped": {}}
        context = grounded_chat.library_context(biggest, library, plan)
        calls["library_answer"].append(harness.call_tokens_bound(grounded_chat.library_messages(context, history)))
    summary = {kind: {"calls": len(values), "max_tokens_bound": max(values) if values else 0,
                      "mean_tokens_bound": round(sum(values) / len(values)) if values else 0,
                      "sum_tokens_bound": sum(values)} for kind, values in calls.items()}
    print(json.dumps({"experiment": config.experiment, "items": len(items),
                      "by_corpus": dict(Counter(i.spec["corpus"] for i in items)),
                      "by_scope": dict(Counter(i.spec["scope"] for i in items)),
                      "chars_per_token": harness.CHARS_PER_TOKEN, "max_output_tokens": harness.MAX_OUTPUT_TOKENS,
                      "calls": summary,
                      "worst_case": {"calls": sum(v["calls"] for v in summary.values()),
                                     "tokens": sum(v["sum_tokens_bound"] for v in summary.values())},
                      "configured_bounds": options(config)["tokens_bound"]}, indent=2))
    return 0


def _live_model(subject: Any) -> Any:
    from vss_agents.utils import grounded_chat

    model = grounded_chat.OpenAIChatModel(model=subject.model)
    if not model.base_url:
        raise harness.HarnessError(f"no chat endpoint: set {' or '.join(grounded_chat.BASE_URL_ENVS)}")
    return model


def _run(args: argparse.Namespace) -> int:
    from evalv2.approval import ApprovalRefused, marker_caps, read_approval, run_problems

    if args.live and (os.environ.get(LIVE_ENV) != "1" or not args.approval):
        print(f"refusing a live chat run: pass --live and --approval, set {LIVE_ENV}=1, and only with the "
              "owner's approval", file=sys.stderr)
        return 2
    ensure_private(args.out)
    config = load_config(args.config)
    subjects = config.task_subjects(TASK)
    if len(subjects) != 1:
        raise harness.HarnessError("a chat run asks one chat subject (one manifest per --out); split the config")
    items, records, snaps = load_prepared(config, args.out, args.data_root)
    marker = None
    caps = config.budget_caps()
    if args.live:
        try:
            marker = read_approval(args.approval)
        except ApprovalRefused as exc:
            print(json.dumps({"ok": False, "problems": [str(exc)]}, indent=2))
            return 1
        problems = run_problems(marker, config, {TASK: items}, {"inference"})
        if problems:
            print(json.dumps({"ok": False, "problems": problems}, indent=2))
            return 1
        caps = caps + marker_caps(marker)
    ledger = BudgetLedger(caps, args.out / "budget.jsonl")
    results = {}
    for subject in subjects:
        inner = _live_model(subject) if args.live else harness.FakeChatModel()
        model = harness.MeteredChatModel(inner, ledger=ledger, subject=subject.id, task=TASK)
        manifest = {"experiment": config.experiment, "config_sha256": _config_sha256(config),
                    "live": bool(args.live), "model": model.model,
                    "approval_sha256": marker["sha256"] if marker else None,
                    "references_sha256": options(config)["references"]["sha256"],
                    "started_at": datetime.now(UTC).isoformat()}
        subject_items = [i for i in items if i.subject == subject.id]
        guard = nullcontext() if args.live else no_network()
        with guard:
            results[subject.id] = harness.run(subject_items, records, snaps, model, args.out, manifest=manifest,
                                              retry_errors=args.retry_errors, limit=args.limit)
    print(json.dumps({"experiment": config.experiment, "live": bool(args.live), "out": str(args.out),
                      "subjects": results}, indent=2))
    return 3 if any(r.get("stopped") for r in results.values()) else 0


def _score(args: argparse.Namespace) -> int:
    from evalv2.tasks.chat import evaluate

    ensure_private(args.out)
    config = load_config(args.config)
    datasets = _datasets(config, args.data_root)
    records = {r["record_id"]: r for r in load_references(options(config), args.data_root)}
    rows = harness.read_rows(args.out / "answers.jsonl")
    if not rows:
        print(json.dumps({"ok": False, "problems": [f"no answers in {args.out}"]}, indent=2))
        return 1
    manifest = json.loads((args.out / "manifest.json").read_text(encoding="utf-8"))
    with no_network():
        scores = evaluate.score_run(rows, records, datasets.get("incident_gt"))
        result = evaluate.report(scores, resamples=args.resamples)
    result["run"] = {k: manifest.get(k) for k in ("experiment", "live", "model", "config_sha256", "snapshots")}
    target = args.out / "score"
    target.mkdir(parents=True, exist_ok=True)
    (target / "scores.jsonl").write_text("".join(json.dumps(s, sort_keys=True) + "\n" for s in scores),
                                         encoding="utf-8")
    (target / "report.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"ok": True, "report": str(target / "report.json"), "live": manifest.get("live"),
                      "coverage": {c: v["coverage"] for c, v in result["corpora"].items()}}, indent=2))
    return 0


def _readiness(args: argparse.Namespace) -> int:
    from evalv2.datasets.ground_truth import half
    from evalv2.datasets.registry import data_root as root
    from evalv2.tasks.chat.readiness import readiness

    config = load_config(args.config)
    datasets = _datasets(config, args.data_root)
    opts = options(config)
    records = load_references(opts, args.data_root)
    gt = datasets["incident_gt"]
    halves = {name: half(datasets["split"], name, gt_split=gt["split"]) for name in ("dev", "test")}
    out = args.out or root(args.data_root) / READINESS_DIR
    ensure_private(out)
    result = readiness(records, gt, halves, seed=args.seed, pilot_half=config.split.half)
    result["references"] = {"path": str(references_path(opts, args.data_root)), "sha256": opts["references"]["sha256"]}
    Path(out).mkdir(parents=True, exist_ok=True)
    (Path(out) / "readiness.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    brief = {k: v for k, v in result.items() if k not in ("inconsistent", "needs_review", "by_class")}
    brief["inconsistent"] = len(result["inconsistent"])
    brief["needs_review"] = {k: v for k, v in result["needs_review"].items() if k != "records"}
    print(json.dumps({"out": str(Path(out) / "readiness.json"), **brief}, indent=2))
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m evalv2.tasks.chat", description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("prepare", "estimate", "run", "score", "readiness"):
        command = sub.add_parser(name)
        command.add_argument("--config", type=Path, required=True)
        command.add_argument("--data-root", type=Path, default=None)
        if name in ("prepare", "estimate", "run", "score"):
            command.add_argument("--out", type=Path, required=True, help="private run directory (resumable)")
        if name == "run":
            command.add_argument("--live", action="store_true", help="the real chat model (approval required)")
            command.add_argument("--approval", type=Path, help="the owner's approval marker")
            command.add_argument("--retry-errors", action="store_true", help="re-ask items that ended http_error")
            command.add_argument("--limit", type=int, default=None, help="ask at most N open items this time")
        if name == "score":
            command.add_argument("--resamples", type=int, default=2000)
        if name == "readiness":
            command.add_argument("--out", type=Path, default=None, help="default <data root>/" + str(READINESS_DIR))
            command.add_argument("--seed", type=int, default=REFERENCE_SEED)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    handlers = {"prepare": _prepare, "estimate": _estimate, "run": _run, "score": _score, "readiness": _readiness}
    try:
        return handlers[args.command](args)
    except (ValueError, RuntimeError, OSError) as exc:
        print(json.dumps({"ok": False, "problems": [f"{type(exc).__name__}: {exc}"]}, indent=2))
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
