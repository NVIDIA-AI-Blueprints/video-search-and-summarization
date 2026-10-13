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

"""Load generation: one thread per unit of concurrency.

Each worker runs two phases:

1. **Warmup** -- ``warmup_rounds`` full cycles of the query set. Discarded.
2. **Measurement** -- all workers pass a barrier together, then draw from a
   shared :class:`bench.budget.SampleBudget`.

Each request is ``vss search run <path> ... --raw`` as a subprocess, because
that is the interface the product ships: the agent's ``POST /api/v1/search``
has been removed. The path is already decided -- :mod:`bench.decompose` and
:mod:`bench.routing` resolved it once per query before the sweep -- so a worker
never calls the LLM and never guesses.

One consequence is worth keeping in view while reading any result from here: a
thread spends most of its time waiting on a process, and the process spends its
first second or so starting a Python interpreter. See
:func:`bench.cli.measure_spawn_cost`.
"""

from __future__ import annotations

import logging
import os
import subprocess
import threading
import time
from datetime import datetime, timezone
from typing import Any

from .budget import SampleBudget
from .cli import (
    CONFIG_HOME_ENV,
    build_argv,
    explain_exit,
    is_environment_fault,
    parse_cli_output,
)
from .models import QueryItem, QueryPlan, RequestRecord, RunConfig

logger = logging.getLogger(__name__)


def utc_now_iso() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def _extract_hit_evidence(hits: list[dict[str, Any]], record: RequestRecord) -> None:
    """Count what the critic did to the hits this request returned."""
    record.result_count = len(hits)
    for hit in hits:
        if not isinstance(hit, dict):
            continue
        # Two spellings across flows: the agent names it critic_result, the
        # search_core path names it verification. Accept either -- reading only
        # one against the other flow silently counts zero verifications.
        critic = hit.get("critic_result")
        if not isinstance(critic, dict):
            critic = hit.get("verification")
        if not isinstance(critic, dict):
            continue
        verdict = str(critic.get("result", "")).strip().lower()
        # With the critic off the CLI still labels every hit "unverified", so
        # only a verdict proves the critic ran.
        if verdict == "confirmed":
            record.critic_ran = True
            record.critic_confirmed += 1
        elif verdict == "rejected":
            record.critic_ran = True
            record.critic_rejected += 1
        elif verdict == "unverified":
            record.critic_unverified += 1


def send_search_request(
    vss_cmd: list[str],
    query_item: QueryItem,
    query_plan: QueryPlan,
    run_config: RunConfig,
    worker_id: int,
    iteration: int,
    is_warmup: bool,
    timeout_sec: int = 300,
    cli_options: dict[str, Any] | None = None,
) -> RequestRecord:
    """Run one ``vss search run`` and record the outcome."""
    options = cli_options or {}
    record = RequestRecord(
        run_id=run_config.run_id,
        query_id=query_item.id,
        query_text=query_item.query,
        worker_id=worker_id,
        iteration=iteration,
        is_warmup=is_warmup,
        critic_eval_count=run_config.critic_eval_count,
        top_k=run_config.top_k,
        concurrency=run_config.concurrency,
        path=query_plan.path,
        critic=run_config.critic,
    )

    # The sweep's own CLI config, never whatever the shell or ~/.vss holds.
    home = options.get("config_home" if run_config.critic else "critic_off_config_home")
    if not home and not run_config.critic:
        raise ValueError(
            f"{run_config.run_id} has the critic off but no critic-off "
            "config home was prepared"
        )
    env = {**os.environ, CONFIG_HOME_ENV: str(home)} if home else None

    argv = build_argv(
        vss_cmd=vss_cmd,
        plan=query_plan.plan,
        top_k=run_config.top_k,
        critic_eval_count=run_config.critic_eval_count if run_config.critic else None,
        min_cosine_similarity=options.get("min_cosine_similarity"),
        merge_adjacent=bool(options.get("merge_adjacent", False)),
        original_query=(
            query_item.query if options.get("pass_original_query", True) else ""
        ),
    )

    record.started_at = utc_now_iso()
    start = time.perf_counter()
    try:
        proc = subprocess.run(
            argv, capture_output=True, check=False, text=True, timeout=timeout_sec,
            env=env,
        )
        record.latency_sec = time.perf_counter() - start
        record.completed_at = utc_now_iso()
        record.exit_code = proc.returncode

        if proc.returncode == 0:
            try:
                hits, messages = parse_cli_output(proc.stdout)
            except ValueError as e:
                record.error = f"unreadable CLI output: {e}"
            else:
                record.search_messages = messages
                _extract_hit_evidence(hits, record)
        else:
            detail = (proc.stderr or proc.stdout or "").strip()
            record.stderr_excerpt = detail[:400]
            record.error = f"{explain_exit(proc.returncode)}: {detail[:200]}"

    except subprocess.TimeoutExpired:
        record.latency_sec = time.perf_counter() - start
        record.completed_at = utc_now_iso()
        record.exit_code = 124
        record.error = f"the CLI did not return within {timeout_sec}s"
    except OSError as e:
        record.latency_sec = time.perf_counter() - start
        record.completed_at = utc_now_iso()
        record.exit_code = -1
        record.error = f"could not start the CLI: {e}"

    return record


def run_worker(
    worker_id: int,
    query_set: list[QueryItem],
    query_plans: dict[str, QueryPlan],
    run_config: RunConfig,
    vss_cmd: list[str],
    warmup_rounds: int,
    budget: SampleBudget,
    barrier: threading.Barrier,
    timeout_sec: int,
    inter_request_delay_sec: float = 0.0,
    cli_options: dict[str, Any] | None = None,
    abort: threading.Event | None = None,
) -> list[RequestRecord]:
    """Run one worker thread through warmup and measurement.

    An exception here stops the other workers too: without that they would
    wait out the barrier timeout and then measure a full run whose records are
    thrown away when this one re-raises.
    """
    try:
        return _run_worker(
            worker_id, query_set, query_plans, run_config, vss_cmd, warmup_rounds,
            budget, barrier, timeout_sec, inter_request_delay_sec, cli_options, abort,
        )
    except BaseException:
        if abort is not None:
            abort.set()
        barrier.abort()
        raise


def _run_worker(
    worker_id: int,
    query_set: list[QueryItem],
    query_plans: dict[str, QueryPlan],
    run_config: RunConfig,
    vss_cmd: list[str],
    warmup_rounds: int,
    budget: SampleBudget,
    barrier: threading.Barrier,
    timeout_sec: int,
    inter_request_delay_sec: float,
    cli_options: dict[str, Any] | None,
    abort: threading.Event | None,
) -> list[RequestRecord]:
    records: list[RequestRecord] = []
    num_queries = len(query_set)
    by_id = {q.id: q for q in query_set}
    iteration = 0

    def _send(query_item: QueryItem, warmup: bool) -> RequestRecord:
        record = send_search_request(
            vss_cmd=vss_cmd,
            query_item=query_item,
            query_plan=query_plans[query_item.id],
            run_config=run_config,
            worker_id=worker_id,
            iteration=iteration,
            is_warmup=warmup,
            timeout_sec=timeout_sec,
            cli_options=cli_options,
        )
        # An unknown flag, an unreachable backend, a CLI pointed at the wrong
        # origin, a missing index: none of these get better by sending the
        # request again, and a sweep that kept going would spend hours
        # producing a wall of failures and report them as measurements.
        if is_environment_fault(record.exit_code) and abort is not None:
            abort.set()
        return record

    for _ in range(warmup_rounds * num_queries):
        if abort is not None and abort.is_set():
            break
        records.append(_send(query_set[iteration % num_queries], warmup=True))
        iteration += 1
        if inter_request_delay_sec > 0:
            time.sleep(inter_request_delay_sec)

    # Hold every worker until all have warmed up, so the measured window runs
    # at the full concurrency under test from its first request.
    try:
        barrier.wait(timeout=max(timeout_sec, 60))
    except threading.BrokenBarrierError:
        logger.warning("Worker %s: warmup barrier broken, continuing", worker_id)
        # The barrier action never ran, so the duration floor would otherwise
        # be skipped without a trace. The run is already marked unclean.
        budget.open_window()

    cursor = 0
    while True:
        if abort is not None and abort.is_set():
            break
        claimed = budget.next_claimable(cursor)
        if claimed is None:
            break
        index, query_id = claimed
        cursor = index + 1

        record = _send(by_id[query_id], warmup=False)
        records.append(record)
        iteration += 1

        if record.succeeded:
            budget.commit(query_id)
        else:
            # Return the slot so the run can still reach its target count; the
            # budget's attempt cap is what stops a runaway retry loop.
            budget.release(query_id)

        if inter_request_delay_sec > 0:
            time.sleep(inter_request_delay_sec)

    return records
