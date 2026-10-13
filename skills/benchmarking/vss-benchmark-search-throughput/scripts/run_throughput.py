#!/usr/bin/env python3
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

# Inline dependency metadata (PEP 723). `uv run scripts/run_throughput.py`
# reads this and builds a throwaway environment, so nothing has to be
# installed on the node to run the benchmark. Keep in step with
# bench.THIRD_PARTY, which is what reports them missing under a plain
# interpreter.
# /// script
# requires-python = ">=3.10"
# dependencies = ["pyyaml", "requests"]
# ///

"""Throughput and latency sweep over VSS search, driven through the ``vss`` CLI.

Measures how fast ``vss search run`` answers, and what the cluster spends doing
it, across a matrix of (critic on/off, top_k, concurrency, path): each run
measures one search path with only the queries that route there. It does not score accuracy -- the
``benchmark-video-search`` skill next door does that, against a different
corpus.

The CLI, not the agent's REST API: ``POST /api/v1/search`` has been removed
from ``services/agent`` outright, so the CLI is the search interface a caller
has. The agent does not shell out to it either -- they are separate surfaces --
so what is measured here is the CLI's own path through ``search_core``, plus a
Python interpreter start per query. See ``bench.cli``.

    uv run scripts/run_throughput.py --dry-run     # the plan, touching nothing
    uv run scripts/run_throughput.py --smoke       # every stage, a few requests per path
    uv run scripts/run_throughput.py               # the sweep, hours

Dependencies are declared inline above, so those commands need no install step
and no --with flags. Under a plain interpreter the run stops with every
missing package named at once; see ``bench.require_third_party``.

Exit codes, which a wrapper script needs to tell apart:

    0  every requested run completed
    1  at least one run raised, the rest continued
    2  bad config or bad flags, or the lock store is unreachable; nothing ran
    3  the vss CLI could not be found, run, or pointed at the deployment
       (includes a cli.service_urls address that does not answer, and
       critic-on runs planned against a deployment with no RT-VLM)
    4  the corpus gate failed; nothing was measured
    5  a query could not be decomposed, or a swept path has no query
       routing to it
    6  the sweep aborted deliberately, remaining runs skipped
    7  another benchmark holds this namespace
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# sys.path[0] is this script's directory, so `bench` resolves without an install
# step. Nothing here is importable as a library on purpose: the entrypoint is
# the interface.
#
# Nothing third-party is imported above this line on purpose: importing
# `bench` runs its dependency check first, so a missing package is reported as
# one message naming all of them rather than as a traceback on whichever
# import happened to come first.
from bench.cli import (
    CliNotFoundError,
    EnvironmentFault,
    measure_spawn_cost,
    preflight,
    prepare_sweep_config,
    probe_service_urls,
    require_origin,
    resolve_vss_cmd,
    supports_flag,
    write_critic_off_config,
)
from bench.console import RULE, attach_log_file, close_log_file, say
from bench.corpus import (
    DEFAULT_GATE_INDICES,
    normalize_gate_indices,
    prepare_corpus,
    record_corpus_drift,
    resolve_ingest_videos,
    validate_corpus_config,
)
from bench.decompose import DecompositionError, find_repo_root, require_llm_url
from bench.lock import (
    LockStoreUnavailable,
    RunLock,
    RunLockError,
    acquire_run_lock,
    lock_config,
)
from bench.models import RunSummary
from bench.plan import (
    build_execution_plan,
    build_query_set,
    completed_run_ids,
    dump_config,
    load_config,
    queries_for_path,
    samples_per_query,
    validate_path_selection,
)
from bench.resources import capture_wedge_state
from bench.run import (
    append_run_summary,
    capture_deployment_metadata,
    execute_single_run,
    log_es_counts,
    resolve_query_plans,
    run_health_gate,
    wait_for_cooldown,
)
from bench.worker import utc_now_iso

logger = logging.getLogger(__name__)

DEFAULT_CONFIG = Path(__file__).resolve().parent.parent / "config.yaml"
# Measured requests per smoke run: a few per run proves the path repeats.
SMOKE_REQUESTS = 6


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="run_throughput.py",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--config", type=Path, default=DEFAULT_CONFIG,
        help="benchmark config (default: the skill's own config.yaml)",
    )
    parser.add_argument(
        "--dataset", type=Path,
        help="directory holding queries.yaml and videos.yaml; overrides "
             "dataset.dir in the config (default: the skill's own dataset/)",
    )
    parser.add_argument(
        "--output-dir", type=Path,
        help="where to create the timestamped results directory "
             "(default: output.base_dir from the config)",
    )
    parser.add_argument(
        "--resume", type=Path,
        help="continue into an existing results directory, skipping the runs "
             "that already have a run_summary.json. Reuses its query_plans.json "
             "and skips ingest unless --ingest is given",
    )
    parser.add_argument(
        "--smoke", action="store_true",
        help="one cheap cell per critic setting and path, end to end, to prove "
             "the pipeline works before committing to a sweep. NOT a "
             "measurement: six requests per run at concurrency 2, top_k 5, no "
             "duration floor. Results land in a smoke_* directory. Combine with "
             "--dry-run to see the shrunken plan",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="print the plan, the request budget and the active guards, then exit "
             "without touching the cluster",
    )

    matrix = parser.add_argument_group("sweep matrix (overrides the config)")
    matrix.add_argument("--top-k", type=int, nargs="+")
    matrix.add_argument("--concurrency", type=int, nargs="+")
    matrix.add_argument(
        "--critic-eval-count", nargs="+", metavar="N",
        help="how many hits a critic-on run verifies, as integers >= 1, or 'all' "
             "to verify every hit (default: the config's sweep.critic_eval_count)",
    )
    matrix.add_argument(
        "--critic", nargs="+", choices=["on", "off"],
        help="run with the critic on, off, or both (default: the config's "
             "sweep.critic). Off runs the CLI against a copy of its config "
             "without the RT-VLM route.",
    )
    matrix.add_argument(
        "--path", nargs="+", metavar="PATH",
        help="measure one CLI path at a time, e.g. --path event fusion "
             "(default: the config's sweep.paths). Each run then cycles only "
             "the queries routing there, so its latency and resource use "
             "attribute to one path instead of to a mixture.",
    )
    matrix.add_argument(
        "--path-order", choices=["grouped", "interleaved"],
        help="whether a whole path finishes before the next begins (grouped) or "
             "the paths of one cell run back to back (interleaved)",
    )

    stages = parser.add_argument_group("stages")
    stages.add_argument(
        "--ingest", action="store_true",
        help="upload the corpus before sweeping (same as corpus.ingest.enabled)",
    )
    stages.add_argument("--skip-ingest", action="store_true")
    stages.add_argument(
        "--no-purge-es", action="store_true",
        help="do not clear the indices before ingesting. The sweep then measures "
             "whatever was already there plus the new upload, which no later "
             "count can separate.",
    )
    stages.add_argument("--skip-deployment-capture", action="store_true")
    stages.add_argument("--skip-prometheus", action="store_true")
    stages.add_argument("--skip-pod-logs", action="store_true")

    cli_group = parser.add_argument_group("vss CLI")
    cli_group.add_argument(
        "--vss-cmd", metavar="CMD",
        help="exact command to invoke the CLI, e.g. 'vss' or "
             "'uv run --project /path/libs/vss vss' (default: cli.vss_cmd, "
             "then cli.vss_repo_root, then vss on PATH)",
    )
    cli_group.add_argument(
        "--vss-repo-root", type=Path, metavar="DIR",
        help="VSS checkout holding libs/vss/cli and the agent source the "
             "decomposition prompt is read from",
    )
    cli_group.add_argument(
        "--llm-url", metavar="URL",
        help="OpenAI-compatible endpoint used to decompose each query once "
             "before the sweep (default: services.llm_url)",
    )
    cli_group.add_argument(
        "--skip-vss-configure", action="store_true",
        help="do not run 'vss configure'. The sweep's CLI config is then "
             "copied from ~/.vss/config.json as it stands, which may point at "
             "another deployment. ~/.vss itself is never written either way.",
    )

    guards = parser.add_argument_group("guards")
    guards.add_argument(
        "--skip-health-gate", action="store_true",
        help="do not send one request before each run. The gate is what turns a "
             "wedged service into an abort instead of hours of timeouts.",
    )
    guards.add_argument("--no-abort-on-empty-run", action="store_true")
    guards.add_argument("--skip-cooldown", action="store_true")
    guards.add_argument(
        "--no-run-lock", action="store_true",
        help="run without the cluster-wide single-run lock. Nothing then stops a "
             "second benchmark or a deploy from wiping the namespace mid-sweep.",
    )
    return parser


def apply_setup_overrides(config: dict[str, Any], args: argparse.Namespace) -> None:
    """Let ``--ingest`` turn on a stage the config leaves off.

    The matching ``--skip-*`` flags are handled at the point of use so the
    config written into the results directory still shows what was configured,
    not what was skipped.

    ``--llm-url`` is folded in here rather than where it is used, so that the
    dry run names the endpoint the sweep would really contact. Applied at the
    point of use it would be invisible to the plan, which is the one output
    read before committing hours to a run.
    """
    if getattr(args, "ingest", False):
        config.setdefault("corpus", {}).setdefault("ingest", {})["enabled"] = True
    if getattr(args, "llm_url", ""):
        config.setdefault("services", {})["llm_url"] = args.llm_url
    if getattr(args, "smoke", False):
        apply_smoke_overrides(config)


def apply_smoke_overrides(config: dict[str, Any]) -> None:
    """Shrink the sweep to one cheap cell that still touches every stage.

    Written as a config override rather than as a separate code path, so
    ``--smoke --dry-run`` prints exactly what ``--smoke`` would run, and so
    the smoke run exercises the same plan, worker and artifact code the real
    sweep does. A second code path would prove the second code path works.

    The numbers are chosen to reach every stage at the lowest cost:
    concurrency 2 rather than 1 so the barrier and the shared sample budget
    are exercised; one warmup round so that path runs; ``critic_eval_count``
    1 rather than every hit, because the critic is a VLM call per hit and
    verifying one proves the flag is accepted just as well.

    What it does NOT shrink: the corpus gate, the health gate, the run lock,
    Prometheus and pod-log capture. Those are among the least-exercised parts
    of the sweep, and they are cheap.
    """
    sweep = config.setdefault("sweep", {})
    sweep["critic_eval_count"] = [1]
    sweep["top_k"] = [5]
    sweep["concurrency"] = [2]

    execution = config.setdefault("execution", {})
    execution["warmup_rounds"] = 1
    execution["min_rounds_per_worker"] = 1
    execution["min_measured_requests_per_run"] = SMOKE_REQUESTS
    # The floor exists so a fast cell keeps measuring. Here that is the
    # opposite of what is wanted.
    execution["min_run_duration_sec"] = 0
    execution.setdefault("cooldown", {})["enabled"] = False


def _print_dry_run(
    config: dict[str, Any],
    runs: list,
    pending: list,
    already_done: set[str],
    query_set: list,
    args: argparse.Namespace,
) -> None:
    say("")
    say("[DRY RUN] Execution plan:")
    for i, run in enumerate(runs, 1):
        state = "SKIP (done)" if run.run_id in already_done else "run"
        # Selected by expected_path: the real routing is only known once
        # decomposition runs, which a dry run does not do.
        picked = queries_for_path(query_set, run.path)
        scope = (
            f" path={run.path} [{', '.join(q.id for q in picked)} (expected)]"
            if run.path else ""
        )
        say(f"  {i:2d}. [{state}] {run.run_id} "
            f"(critic={run.critic_label} top_k={run.top_k} "
            f"c={run.concurrency}{scope})")

    exec_config = config.get("execution", {}) or {}
    # Per run varies with both --path and concurrency: the budget is per query so
    # a path run cycles through fewer of them, and the run total scales with
    # concurrency to hold the rounds-per-worker floor. Broken out per concurrency
    # rather than shown as one figure so the total stays honest.
    per_run = [len(queries_for_path(query_set, r.path)) for r in runs]
    budgets = [
        samples_per_query(exec_config, r.concurrency, n) * n
        for r, n in zip(runs, per_run)
    ]
    say("")
    say(f"  Total: {len(runs)} runs ({len(pending)} pending)")
    by_conc: dict[int, list[int]] = defaultdict(list)
    for run, total in zip(runs, budgets):
        by_conc[run.concurrency].append(total)
    for conc in sorted(by_conc):
        totals = by_conc[conc]
        span = (
            f"{min(totals)}-{max(totals)}"
            if min(totals) != max(totals) else f"{min(totals)}"
        )
        say(f"  c={conc:<3} {len(totals):2d} run(s), {span} measured requests each "
            f"({min(totals) / conc:.1f}-{max(totals) / conc:.1f} rounds/worker)")
    say(f"  Measured requests, all runs: {sum(budgets):,}")

    floor_sec = float(exec_config.get("min_run_duration_sec", 0) or 0)
    if floor_sec > 0:
        # Stated rather than folded into the figures above, because the real
        # count depends on latency the plan cannot know: a cell that spends its
        # budget inside the floor keeps measuring until the clock is up.
        say(f"    ...a floor, not a total: min_run_duration_sec={floor_sec:.0f} "
            f"keeps any run")
        say("    that finishes sooner measuring, so fast cells collect "
            "several times this many")
    say(f"  Analysis data points: {sum(per_run)} (one per run per query)")

    # A lower bound, from arithmetic rather than from a guess at latency: every
    # run is held open for the floor, and every run after the first drains.
    # Worth printing because this is the decision the dry run exists to inform,
    # and because the serial cells are far longer than the floor -- 201
    # subprocesses back to back is a different proposition from 201 of them
    # twenty at a time, and nothing above says so.
    cooldown_config = (exec_config.get("cooldown") or {})
    cooldown_sec = (
        float(cooldown_config.get("max_wait_sec", 180) or 0)
        if cooldown_config.get("enabled", True) and not args.skip_cooldown
        else 0.0
    )
    floor_total = len(pending) * floor_sec + max(0, len(pending) - 1) * cooldown_sec
    if floor_total > 0:
        hours, minutes = divmod(int(floor_total // 60), 60)
        say(f"  Minimum sweep duration: {hours}h {minutes:02d}m, and it is a "
            f"minimum")
        say(f"    {len(pending)} x {floor_sec:.0f}s measurement floor"
            + (f", plus up to {cooldown_sec:.0f}s cooldown between runs"
               if cooldown_sec else ""))
    serial = [r for r in pending if r.concurrency == 1]
    if serial:
        serial_requests = max(
            samples_per_query(exec_config, 1, n) * n
            for r, n in zip(runs, per_run) if r.concurrency == 1
        )
        say(f"    The {len(serial)} c=1 run(s) are serial: {serial_requests} "
            "requests back to back, each its")
        say("    own subprocess. These, not the floor, set how long the sweep "
            "really takes")

    corpus_config = config.get("corpus", {}) or {}
    ingest_config = corpus_config.get("ingest", {}) or {}
    ingest_on = bool(ingest_config.get("enabled")) and not args.skip_ingest
    uploads = resolve_ingest_videos(ingest_config) if ingest_on else []
    say("")
    say("  Pre-sweep stages:")
    say(f"    Ingest videos:         {'ENABLED' if ingest_on else 'skipped'}"
        + (f" ({len(uploads)} upload(s) of "
           f"{ingest_config.get('video')})" if ingest_on else ""))
    floors = corpus_config.get("require_min_docs", {}) or {}
    say("    Corpus gate:           always enforced"
        + (f" ({', '.join(f'{p} >= {n:,}' for p, n in floors.items())})"
           if floors else " (no floors configured)"))
    if ingest_on:
        readiness = ingest_config.get("readiness", {}) or {}
        purge_on = (
            bool(ingest_config.get("purge_before_ingest", True))
            and not args.no_purge_es
        )
        say("    Clear ES first:        "
            + (f"ENABLED -- {', '.join(corpus_config.get('es_index_patterns', []))} "
               "deleted before upload"
               if purge_on
               else "disabled (abort if ES is non-empty: "
                    f"{ingest_config.get('require_empty_before_ingest', True)})"))
        say(f"    Unique sensor ids:     "
            f"{ingest_config.get('require_unique_sensor_ids', True)} -- the corpus "
            f"is one file uploaded repeatedly, so this is what catches a content "
            f"dedup collapsing it")
        say(f"    Exclusive corpus:      "
            f"{ingest_config.get('require_exclusive_corpus', True)}")
        gate_indices = normalize_gate_indices(
            readiness.get("gate_indices") or DEFAULT_GATE_INDICES
        )
        per_video = readiness.get("expected_docs_per_video") or {}
        say(f"    Upload concurrency:    "
            f"{ingest_config.get('upload_concurrency', len(uploads))}")
        say(f"    Ingest completion:     every video holds its own count in "
            f"{', '.join(gate_indices)} under its sensorId, then "
            f"{readiness.get('stable_for_sec', 300)}s quiet")
        # Whether the bar is configured or converged changes what a pass is
        # worth, so the dry run says which it will be rather than only naming
        # the indices.
        if all(int(per_video.get(p) or 0) > 0 for p in gate_indices):
            say("    Expected per video:    "
                + ", ".join(f"{p}={int(per_video[p]):,}" for p in gate_indices)
                + f" from config, x {readiness.get('min_ratio', 1.0)}")
        else:
            say("    Expected per video:    NOT configured -- the copies will "
                "check each other, with the furthest-along one setting the bar. "
                "Weaker: it cannot tell all-finished from all-stalled, so the "
                "quiet window is doing that work")

    cli_config = config.get("cli", {}) or {}
    cmd_shown = (
        args.vss_cmd
        or cli_config.get("vss_cmd")
        or "auto (cli.vss_repo_root, then vss on PATH)"
    )
    try:
        origin = require_origin(cli_config)
    except CliNotFoundError as e:
        origin = f"NOT SET -- {str(e).splitlines()[0]}"
    # Listed among the pre-sweep stages because it is one, and because it
    # reaches a service: a dry run naming only ingest and the corpus gate
    # implies the sweep contacts no LLM, and then an unreachable endpoint is a
    # surprise two hours in rather than a line read here.
    decompose_config = config.get("decomposition", {}) or {}
    pinned = decompose_config.get("pin_paths") or {}
    try:
        llm_url = require_llm_url(config.get("services", {}) or {})
    except DecompositionError as e:
        llm_url = f"NOT SET -- {str(e).splitlines()[0]}"
    say(f"    Resolve paths:         1 LLM call per query at {llm_url}"
        + (f" ({len(pinned)} pinned, not routed)" if pinned else ""))

    settings = lock_config(config)
    say("    Single-run lock:       "
        + (f"ENABLED ({settings['index']}, ttl {settings['ttl_sec']:.0f}s)"
           if settings["enabled"] else "disabled"))

    say("")
    say("  Search interface:")
    say(f"    CLI command:           {cmd_shown}")
    say(f"    CLI origin:            {origin}"
        + (" (vss configure skipped)" if args.skip_vss_configure else ""))
    # Printed because it changes what the numbers mean, not just how they are
    # routed: with it, a plateau is the services; without it, the ingress is
    # inside every measured request and could be the thing that plateaus.
    overrides = cli_config.get("service_urls") or {}
    if overrides:
        say("    Ingress bypassed for:")
        for name, url in sorted(overrides.items()):
            say(f"      {name:<16} {url}")
    else:
        say("    Ingress bypassed for:  nothing -- every measured request "
            "crosses HAProxy")
    # Said in the plan, not only after the fact: a reader deciding whether to
    # commit hours to a sweep should know up front that each query is a process.
    say("    Per query:             one 'vss search run' subprocess, so each "
        "latency includes a Python start")

    gate, empty_abort = _guard_settings(config, args)
    say("")
    say("  Between-run guards:")
    say("    Health gate:           "
        + (f"1 request per run, {gate}s deadline" if gate else "disabled"))
    say("    Abort on empty run:    " + ("ENABLED" if empty_abort else "disabled"))


def _log_abort_guidance(config: dict[str, Any], reason: str) -> None:
    """Say why the sweep stopped, and what to look at before resuming.

    Printed rather than left for the reader to reconstruct, because the state
    this aborts on is silent by design: no pod restarts, nothing is logged, and
    every health endpoint keeps answering. Someone reading only this output has
    no way to guess where to look.
    """
    namespace = (config.get("deployment", {}) or {}).get("namespace", "vss-search")
    say("")
    say(f"  ABORTING SWEEP: {reason}.")
    say("    Continuing would spend the same time again on every remaining run and")
    say("    collect nothing. The VLM keeps answering 200 on /v1/health/ready")
    say("    throughout, so check progress, not readiness:")
    say(f"      kubectl logs -n {namespace} deploy/vss-rtvi-vlm --tail=50 "
        f"| grep -c 'percent complete is 0.00'")
    say("      nvidia-smi --query-gpu=index,utilization.gpu,power.draw,memory.used "
        "--format=csv")
    say("    Two signatures seen so far, both with queries stuck at 0.00%:")
    say("      - 100% utilisation at near-idle power = a wedged CUDA context.")
    say("      - memory collapsed to ~2 GiB = the model is gone from the GPU.")
    say("    DO NOT restart before reading wedge_diagnostics/ -- the wedge survives")
    say("    for hours, and restarting first is why the root call is still unknown.")
    say(f"    Recovery: kubectl rollout restart -n {namespace} "
        f"deploy/vss-rtvi-vlm deploy/vss-agent")


def _capture_wedge(config: dict[str, Any], output_dir: Path, reason: str) -> None:
    """Photograph the wedged service while it is still wedged.

    A wedge survives for hours, so all that is ever missing is a command run at
    the time. This is the only moment the harness knows it is looking at one, so
    it takes the photograph here rather than advising a human to.
    """
    pod_log_config = config.get("pod_log_scan", {}) or {}
    if not pod_log_config.get("enabled", False):
        return
    target = output_dir / "wedge_diagnostics"
    say("")
    say(f"  Capturing wedge diagnostics to {target.name}/ before aborting...")
    try:
        result = capture_wedge_state(
            namespace=pod_log_config.get("namespace", "vss-search"),
            pod_prefixes=pod_log_config.get("pods", []),
            output_dir=target,
            reason=reason,
        )
    except Exception as e:  # noqa: BLE001 - diagnostics must never mask the abort
        say(f"    !! Diagnostics capture failed: {e}")
        return
    say(f"    {len(result['captured'])} artifact(s) captured")
    for failure in result["failed"]:
        say(f"    !! {failure}")


class _Stop(Exception):
    """Ends the sweep early with an exit code; the reason is already printed."""

    def __init__(self, code: int) -> None:
        super().__init__(code)
        self.code = code


def _fail(code: int, message: str) -> _Stop:
    print(f"ERROR: {message}", file=sys.stderr)
    return _Stop(code)


@dataclass
class _Outcome:
    completed: list[RunSummary] = field(default_factory=list)
    failed_runs: list[str] = field(default_factory=list)
    aborted: str = ""
    not_attempted: int = 0


def _guard_settings(config: dict[str, Any], args: argparse.Namespace) -> tuple[int, bool]:
    """``(health gate timeout, abort on empty run)`` after the flags."""
    exec_config = config.get("execution", {}) or {}
    gate_timeout = (
        0 if args.skip_health_gate
        else int(exec_config.get("health_gate_timeout_sec", 120) or 0)
    )
    abort_on_empty = (
        bool(exec_config.get("abort_on_empty_run", True))
        and not args.no_abort_on_empty_run
    )
    return gate_timeout, abort_on_empty


def _build_plan(config: dict[str, Any], args: argparse.Namespace) -> tuple[list, list]:
    # Scoped to these calls rather than to the whole sweep: they raise
    # ValueError only for a config or flag the user can fix, and everything
    # after them can raise ValueError for reasons that are bugs.
    try:
        validate_corpus_config(config)
        query_set = build_query_set(config)
        runs = build_execution_plan(
            config,
            top_k=args.top_k,
            concurrency=args.concurrency,
            critic_eval_count=args.critic_eval_count,
            critic=args.critic,
            paths=args.path,
            path_order=args.path_order,
            query_set=query_set,
        )
    except ValueError as e:
        raise _fail(2, str(e)) from e
    return query_set, runs


def _output_dir(config: dict[str, Any], args: argparse.Namespace) -> Path:
    if args.resume:
        output_dir = Path(args.resume)
        if not output_dir.exists():
            raise _fail(2, f"--resume directory does not exist: {output_dir}")
        return output_dir
    base = args.output_dir or Path(
        (config.get("output", {}) or {}).get("base_dir", "results")
    )
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    # Named apart so a smoke run's artifacts are never mistaken for a
    # measurement, by a person or by a later --resume.
    prefix = "smoke" if args.smoke else "search_throughput"
    return base / f"{prefix}_{stamp}"


def _print_banner(
    config: dict[str, Any],
    args: argparse.Namespace,
    output_dir: Path,
    query_set: list,
    runs: list,
    pending: list,
) -> None:
    skipped = len(runs) - len(pending)
    say(RULE)
    say("VSS Search Throughput Benchmark"
        + ("  --  SMOKE RUN, NOT A MEASUREMENT" if args.smoke else ""))
    if args.smoke:
        # Said here and again at the end, because the thing that makes a
        # smoke run useful -- it produces the same artifacts as a real one --
        # is also what makes its numbers easy to quote by mistake.
        say(f"  {SMOKE_REQUESTS} requests per run with no duration floor: enough "
            "to prove every")
        say("  stage runs, far too few to say anything about latency.")
    say(f"  Started:    {utc_now_iso()}")
    say(f"  Config:     {args.config}")
    dataset_dir = (config.get("dataset") or {}).get("dir")
    if dataset_dir:
        say(f"  Dataset:    {dataset_dir}")
    say(f"  Output:     {output_dir.resolve()}")
    say(f"  Queries:    {len(query_set)} ({', '.join(q.id for q in query_set)})")
    swept_paths = [p for p in dict.fromkeys(r.path for r in runs) if p]
    if swept_paths:
        say(f"  Paths:      {', '.join(swept_paths)} -- one run each per matrix "
            f"cell, only the queries routing there")
    say("  Critic:     "
        + " | ".join(dict.fromkeys(
            "off" if not r.critic else f"on (verifies {r.critic_label} hit(s))"
            for r in runs
        )))
    say(f"  Runs:       {len(pending)} to execute"
        + (f", {skipped} already complete (skipped)" if skipped else ""))
    say(f"  Agent URL:  {config['services']['vss_agent_url']}")
    say(RULE)


def _check_service_urls(prepared: dict[str, Any], namespace: str) -> None:
    """Refuse a ``cli.service_urls`` address that does not answer.

    ClusterIPs change on every clean redeploy, and a stale one otherwise
    surfaces hours later as timeouts. Discovered URLs go through the ingress,
    so one of those failing is only warned about: the sweep may not use it.
    """
    urls = {n: u for n, u in prepared["service_urls"].items() if u}
    failures = probe_service_urls(urls)
    for name, reason in sorted(failures.items()):
        say(f"  !! {name} does not accept connections: {reason}")
    fatal = sorted(n for n in failures if n in prepared["applied"])
    if fatal:
        raise CliNotFoundError(
            f"cli.service_urls points {', '.join(fatal)} at an address that does "
            f"not answer. ClusterIPs change on every clean redeploy; read the "
            f"current ones with: kubectl get svc -n {namespace}"
        )


def _prepare_cli(
    config: dict[str, Any],
    args: argparse.Namespace,
    output_dir: Path,
    pending: list,
) -> tuple[list[str], Path, float]:
    """Find the CLI and write the config every request of this sweep uses.

    First, before ingest spends half an hour: a CLI that cannot run makes every
    later stage pointless, and finding that out here costs a subprocess.
    Returns ``(vss_cmd, repo_root, spawn_cost_sec)``.
    """
    cli_config = config.setdefault("cli", {})
    repo_root = args.vss_repo_root or cli_config.get("vss_repo_root") or find_repo_root()
    if repo_root is None:
        raise _fail(
            3,
            "no VSS checkout found above this skill, and neither --vss-repo-root "
            "nor cli.vss_repo_root is set. The decomposition prompt is read from "
            "the product source at run time, so the benchmark cannot route "
            "queries without one.",
        )
    namespace = (config.get("deployment", {}) or {}).get("namespace", "vss-search")

    try:
        vss_cmd, how = resolve_vss_cmd(
            explicit_cmd=args.vss_cmd or cli_config.get("vss_cmd"),
            repo_root=repo_root,
        )
        say(f"  CLI: {preflight(vss_cmd)} via {how}")

        origin = require_origin(cli_config)
        overrides = {
            str(k): str(v) for k, v in (cli_config.get("service_urls") or {}).items()
        }
        sweep_home = output_dir / "vss_config"
        prepared = prepare_sweep_config(
            vss_cmd, origin, sweep_home, overrides,
            configure=not args.skip_vss_configure,
        )
        cli_config["config_home"] = str(sweep_home.resolve())
        say(f"  CLI config: {sweep_home}/config.json "
            + ("(copied from ~/.vss as it stands, --skip-vss-configure)"
               if args.skip_vss_configure else f"(vss configure --base-url {origin})"))
        say("    Every request runs with VSS_CONFIG_HOME set to it; ~/.vss is "
            "not touched.")
        if prepared["applied"]:
            say("  Bypassing the ingress for: "
                + ", ".join(f"{n} -> {u}" for n, u in sorted(prepared["applied"].items())))
        _check_service_urls(prepared, namespace)

        if any(r.critic for r in pending) and not prepared["has_rt_vlm"]:
            raise CliNotFoundError(
                "critic-on runs are planned, but the deployment exposes no rt_vlm "
                "service. The CLI would skip the critic and the 'on' runs would "
                "measure critic off. Deploy the RT-VLM, or sweep with --critic off."
            )
        if any(not r.critic for r in pending):
            off_home = output_dir / "vss_config_critic_off"
            write_critic_off_config(sweep_home, off_home)
            cli_config["critic_off_config_home"] = str(off_home.resolve())
            say(f"  Critic-off runs use {off_home}/config.json "
                "(same routes, no RT-VLM)")

        # The deployment's CLI comes from its image, not from this checkout, so
        # "older than the flag" is a normal state. Click exits 2 on an unknown
        # option, and exit 2 aborts the sweep -- so one optional flag would
        # otherwise end the run on request one. Asked once, here.
        if cli_config.get("pass_original_query", True) and not supports_flag(
            vss_cmd, "--original-query"
        ):
            cli_config["pass_original_query"] = False
            say("  !! This CLI has no --original-query, so the critic will judge "
                "hits against the decomposed arguments rather than the question "
                "as asked. Expect lower confirmation rates than a newer CLI.")

        spawn_cost = measure_spawn_cost(vss_cmd)
        say(f"  Process startup: {spawn_cost:.2f}s per query, measured. Every "
            "latency below includes it.")
    except CliNotFoundError as e:
        raise _fail(3, str(e)) from e
    return vss_cmd, Path(repo_root), spawn_cost


def _write_effective_config(config: dict[str, Any], output_dir: Path) -> None:
    """Record the config as the sweep runs it, CLI adjustments included.

    A resume keeps the first invocation's copy and writes its own beside it,
    so the record of what the earlier runs used is never overwritten.
    """
    target = output_dir / "config.yaml"
    if target.exists():
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        target = output_dir / f"config.resume_{stamp}.yaml"
    target.write_text(dump_config(config), encoding="utf-8")


def _prepare_corpus_stage(
    config: dict[str, Any],
    args: argparse.Namespace,
    output_dir: Path,
) -> Any:
    # A resume must measure the corpus the earlier runs measured. Ingesting
    # again would purge it first, so it takes an explicit --ingest.
    skip_ingest = args.skip_ingest or (bool(args.resume) and not args.ingest)
    if skip_ingest and not args.skip_ingest:
        say("  --resume: keeping the corpus the earlier runs measured "
            "(pass --ingest to re-ingest anyway)")
    corpus_state = prepare_corpus(
        config, output_dir,
        skip_ingest=skip_ingest,
        purge=False if args.no_purge_es else None,
    )
    if not corpus_state.gate_passed:
        say("")
        say(f"  Corpus gate FAILED: {corpus_state.gate_message}")
        say("")
        say("ABORTED: the search indices are not in a state worth measuring.")
        say("Ingest first (--ingest with corpus.ingest.video set), or lower")
        say("corpus.require_min_docs if this is intentional. See corpus_state.json.")
        raise _Stop(4)
    say(f"  {corpus_state.gate_message}")
    return corpus_state


def _resolve_paths(
    config: dict[str, Any],
    args: argparse.Namespace,
    output_dir: Path,
    repo_root: Path,
    query_set: list,
    runs: list,
    corpus_state: Any,
) -> tuple[dict, dict[str, str]]:
    try:
        query_plans = resolve_query_plans(
            config, query_set, output_dir, repo_root,
            video_sources=[u.upload_filename for u in corpus_state.uploads
                           if u.success] or None,
            reuse=bool(args.resume),
        )
        resolved_paths = {qid: plan.path for qid, plan in query_plans.items()}
        # The plan was checked against expected_path, which is a guess; this
        # checks it against what decomposition produced.
        swept = [p for p in dict.fromkeys(r.path for r in runs) if p]
        if swept:
            validate_path_selection(swept, query_set, resolved_paths)
    except (DecompositionError, ValueError) as e:
        raise _fail(5, str(e)) from e
    return query_plans, resolved_paths


def _run_sweep(
    config: dict[str, Any],
    args: argparse.Namespace,
    output_dir: Path,
    pending: list,
    query_set: list,
    query_plans: dict,
    resolved_paths: dict[str, str],
    vss_cmd: list[str],
    spawn_cost: float,
    lock: RunLock | None,
) -> _Outcome:
    cli_config = config.get("cli", {}) or {}
    gate_timeout, abort_on_empty = _guard_settings(config, args)
    outcome = _Outcome()

    for i, run_config in enumerate(pending, 1):
        say("")
        say(f"--- Run {i}/{len(pending)} ---")
        if lock is not None and lock.lost:
            outcome.aborted = f"lost the single-run lock ({lock.lost_reason})"
            outcome.not_attempted = len(pending) - i + 1
            break
        run_queries = queries_for_path(query_set, run_config.path, resolved_paths)

        gate_sec: float | None = None
        if gate_timeout > 0:
            gate = run_health_gate(
                run_config, run_queries, query_plans, vss_cmd, gate_timeout,
                cli_options=cli_config,
            )
            if not gate.succeeded:
                say(f"  Health gate: FAILED after {gate.latency_sec:.1f}s "
                    f"(exit={gate.exit_code}, {gate.error or 'no error text'})")
                outcome.aborted = (f"the health gate before {run_config.run_id} got "
                                   f"no usable answer in {gate_timeout}s")
                # This run never started, so it counts as not attempted too.
                outcome.not_attempted = len(pending) - i + 1
                _capture_wedge(config, output_dir, outcome.aborted)
                _log_abort_guidance(config, outcome.aborted)
                break
            gate_sec = gate.latency_sec
            say(f"  Health gate: ok in {gate_sec:.1f}s")

        # After the gate rather than before it, because the gate is itself a real
        # search and leaves real work behind -- on the attribute path a single
        # one costs vss-rtvi-cv about 22 CPU-core-seconds. Draining once, here,
        # covers both the previous run and the gate.
        #
        # Keyed on the loop index, not on completed runs, so a run that raised
        # still gets drained -- it failed after sending load, not instead of
        # sending it. The first run of an invocation needs nothing: --resume
        # starts a fresh process against an idle cluster.
        if args.skip_cooldown:
            cooldown: dict[str, Any] = {"skipped": "--skip-cooldown"}
        elif i == 1:
            cooldown = {"skipped": "first run"}
        else:
            cooldown = wait_for_cooldown(config, previous_run_id=pending[i - 2].run_id)

        try:
            summary = execute_single_run(
                run_config=run_config,
                query_set=run_queries,
                config=config,
                output_dir=output_dir,
                query_plans=query_plans,
                vss_cmd=vss_cmd,
                cli_spawn_cost_sec=spawn_cost,
                skip_prometheus=args.skip_prometheus,
                skip_pod_logs=args.skip_pod_logs,
                health_gate_sec=gate_sec,
                cooldown=cooldown,
            )
            append_run_summary(output_dir, summary, query_plans)
            outcome.completed.append(summary)
        except EnvironmentFault as e:
            # Not "one bad run": the CLI reported a state every remaining cell
            # would hit too, so continuing collects nothing but wastes hours.
            outcome.aborted = str(e).splitlines()[0]
            outcome.not_attempted = len(pending) - i + 1
            say(f"  {e}")
            break
        except Exception as e:  # noqa: BLE001 - one bad run must not end the sweep
            logger.error("Run %s failed: %s", run_config.run_id, e, exc_info=True)
            outcome.failed_runs.append(run_config.run_id)
            say(f"  Run FAILED: {e}")
            continue

        # A run with no successful request is not a slow data point, it is the
        # absence of one: every percentile is undefined and throughput is zero.
        # It is recorded anyway, so --resume does not retry it blindly.
        if abort_on_empty and summary.success_count == 0:
            outcome.aborted = (f"{summary.run_id} finished with "
                               f"{summary.failure_count} failures and no "
                               f"successful request")
            outcome.not_attempted = len(pending) - i
            _log_abort_guidance(config, outcome.aborted)
            break
    return outcome


def _report_after_sweep(config: dict[str, Any], output_dir: Path, lock: RunLock | None) -> None:
    # On the abort path too: a sweep that stopped early still needs to say
    # what corpus it measured against. The gate's counts are taken seconds
    # before the first run, so only this second sample can show that the
    # index moved underneath the sweep.
    drift = record_corpus_drift(config, output_dir)
    if drift.get("drifted"):
        say("")
        say("  CORPUS DRIFTED DURING THE SWEEP -- the runs did not all query the "
            "same index:")
        for pattern, entry in sorted((drift.get("indices") or {}).items()):
            if not entry.get("drifted"):
                continue
            say(f"      {pattern}: {entry.get('at_gate'):,} at the gate -> "
                f"{entry.get('at_end'):,} at the end ({entry.get('delta'):+,})")
        say("    Either ingestion was still running when the sweep started, or")
        say("    another process wrote to these indices. Treat per-run comparisons")
        say("    as unsafe and see corpus_drift in corpus_state.json.")
    if lock is not None and lock.lost:
        say("")
        say(f"  LOST THE SINGLE-RUN LOCK DURING THE SWEEP: {lock.lost_reason}.")
        say("    Another run was active on this namespace, so these results may be "
            "mixed.")


def _print_summary(
    args: argparse.Namespace,
    output_dir: Path,
    runs: list,
    pending: list,
    already_done: set[str],
    outcome: _Outcome,
) -> int:
    total_done = len(already_done) + len(outcome.completed)
    say(RULE)
    say("Benchmark Aborted" if outcome.aborted else "Benchmark Complete")
    say(f"  Runs this invocation: {len(outcome.completed)}/{len(pending)}")
    say(f"  Runs in directory:    {total_done}/{len(runs)} requested")
    if outcome.aborted:
        say(f"  Aborted because:      {outcome.aborted}")
        say(f"  Not attempted:        {outcome.not_attempted} run(s)")
    if outcome.failed_runs:
        say(f"  Failed:               {', '.join(outcome.failed_runs)}")
    say(f"  Output:               {output_dir.resolve()}")
    say("  Files:")
    say("    summary.csv            -- one row per run (appended)")
    say("    summary.jsonl          -- same, JSON lines")
    say("    per_query_summary.csv  -- one row per (run, query_id)")
    say("    resources_summary.csv  -- CPU/GPU/memory per (run, service)")
    say("    query_plans.json       -- each query's decomposition, path and argv")
    say("    corpus_state.json      -- what was ingested and what ES held")
    say("    vss_config/            -- the CLI config every request used")
    say("    <run_id>/per_query_latency.csv  -- raw measured latencies")
    say("    <run_id>/request_records.jsonl  -- all requests incl. warmup")
    say("  Plot them with:")
    say(f"    uv run scripts/plot_results.py {output_dir.resolve()}")
    if total_done < len(runs):
        say("")
        say("  Resume the remainder with:")
        say(f"    --resume {output_dir.resolve()}")
    if args.smoke:
        say("")
        say("  This was a smoke run. The files above have the shape of")
        say(f"  results without being results -- {SMOKE_REQUESTS} requests per run,")
        say("  no duration floor, concurrency 2. What it proves is that every")
        say("  stage runs. Delete the directory and sweep for real:")
        say("    uv run scripts/run_throughput.py")
    say(RULE)

    # Distinct from the "a run raised" exit code: an abort means the remaining
    # runs were skipped on purpose and the service needs looking at before
    # --resume, so a wrapper retrying on exit 1 must not treat the two the same.
    if outcome.aborted:
        return 6
    return 1 if outcome.failed_runs else 0


def execute(args: argparse.Namespace, lock: RunLock | None) -> int:
    """Load config, build the plan, run the sweep, write the summary."""
    try:
        return _execute(args, lock)
    except _Stop as stop:
        return stop.code


def _execute(args: argparse.Namespace, lock: RunLock | None) -> int:
    try:
        config = load_config(args.config, args.dataset)
    except (OSError, ValueError) as e:
        raise _fail(2, str(e)) from e
    apply_setup_overrides(config, args)
    query_set, runs = _build_plan(config, args)
    output_dir = _output_dir(config, args)

    # A dry run must leave no trace, so the directory and its transcript are
    # created only once there is something to record. completed_run_ids answers
    # for a directory that does not exist yet.
    if not args.dry_run:
        output_dir.mkdir(parents=True, exist_ok=True)
        attach_log_file(output_dir / "benchmark.log")

    already_done = completed_run_ids(output_dir)
    pending = [r for r in runs if r.run_id not in already_done]
    _print_banner(config, args, output_dir, query_set, runs, pending)

    if args.dry_run:
        _print_dry_run(config, runs, pending, already_done, query_set, args)
        return 0
    if not pending:
        say("")
        say("All requested runs are already complete in this directory. "
            "Nothing to do.")
        return 0

    say("")
    say("[1/6] Checking the vss CLI")
    vss_cmd, repo_root, spawn_cost = _prepare_cli(config, args, output_dir, pending)
    _write_effective_config(config, output_dir)

    say("")
    say("[2/6] Preparing corpus")
    corpus_state = _prepare_corpus_stage(config, args, output_dir)

    say("")
    say("[3/6] Capturing deployment metadata")
    if args.skip_deployment_capture:
        say("  Skipped (--skip-deployment-capture)")
    else:
        log_es_counts(capture_deployment_metadata(config, output_dir))

    say("")
    say("[4/6] Resolving each query's search path")
    query_plans, resolved_paths = _resolve_paths(
        config, args, output_dir, repo_root, query_set, runs, corpus_state
    )

    say("")
    say(f"[5/6] Executing {len(pending)} benchmark run(s)")
    outcome = _run_sweep(
        config, args, output_dir, pending, query_set, query_plans,
        resolved_paths, vss_cmd, spawn_cost, lock,
    )
    _report_after_sweep(config, output_dir, lock)

    say("")
    say("[6/6] Summary")
    return _print_summary(args, output_dir, runs, pending, already_done, outcome)


def main(argv: list[str] | None = None) -> int:
    """Take the cluster-wide single-run lock, then run the sweep under it.

    A wrapper rather than a block inside :func:`execute` because the lock has to
    outlive every one of that function's exit paths.

    The config is loaded here as well as there. That is deliberate: the lock only
    needs the namespace, the Elasticsearch URL and ``execution.run_lock``, none
    of which ``apply_setup_overrides`` touches, and loading it twice costs one
    YAML parse against the alternative of threading the lock through the plan
    builders.
    """
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    args = build_parser().parse_args(argv)

    try:
        if args.dry_run:
            # A dry run prints the plan and touches nothing, so holding the
            # namespace against a real sweep would be pure obstruction.
            return execute(args, None)
        if args.no_run_lock:
            say("WARNING: --no-run-lock given. Nothing will stop a second benchmark")
            say("         or a deploy from wiping this namespace mid-sweep.")
            return execute(args, None)

        try:
            lock_probe_config = load_config(args.config, args.dataset)
        except (OSError, ValueError) as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return 2

        try:
            lock = acquire_run_lock(
                lock_probe_config,
                label="run_throughput.py",
                output_dir=str(args.resume or args.output_dir or ""),
            )
        except LockStoreUnavailable as e:
            print(f"ERROR: cannot reach the single-run lock store: {e}\n"
                  "  Check services.elasticsearch_url and execution.run_lock, or "
                  "pass --no-run-lock to run unprotected.", file=sys.stderr)
            return 2
        except RunLockError as e:
            print(RULE, file=sys.stderr)
            print("ABORTED: another benchmark run holds this namespace.\n",
                  file=sys.stderr)
            print(f"  {e}\n", file=sys.stderr)
            print("Two runs sharing a namespace destroy each other's measurements:",
                  file=sys.stderr)
            print("an ingest stage empties Elasticsearch, and the sweep already",
                  file=sys.stderr)
            print("running then queries an index that no longer holds its corpus.",
                  file=sys.stderr)
            print(RULE, file=sys.stderr)
            return 7

        if lock.stole_from:
            say(f"NOTE: took over a stale lock from {lock.stole_from}")
        try:
            return execute(args, lock)
        finally:
            lock.release()
    finally:
        close_log_file()


if __name__ == "__main__":
    raise SystemExit(main())
