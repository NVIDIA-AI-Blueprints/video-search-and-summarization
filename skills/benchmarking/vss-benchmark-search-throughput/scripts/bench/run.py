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

"""Executing one sweep cell, and writing what it produced."""

from __future__ import annotations

import csv
import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .budget import SampleBudget
from .cli import EnvironmentFault
from .console import RULE, say
from .decompose import DecompositionError, LiveDecomposer
from .models import (
    LATENCY_FIELDS,
    PER_QUERY_FIELDS,
    RESOURCE_FIELDS,
    SUMMARY_FIELDS,
    QueryItem,
    QueryPlan,
    RequestRecord,
    RunConfig,
    RunSummary,
)
from .plan import read_pin_paths, samples_per_query
from .resources import (
    PodLogStreamer,
    capture_es_index_counts,
    collect_pod_logs,
    collect_run_metrics,
    query_prometheus_instant,
    scan_pod_log_errors,
)
from .routing import EVENT, path_distribution, pinned_plan, plan_for, route
from .stats import compute_per_query_stats, compute_run_stats, format_duration
from .worker import run_worker, send_search_request, utc_now_iso


def _start_pod_log_stream(
    config: dict[str, Any],
    run_output_dir: Path,
    start_epoch: float,
    skip_pod_logs: bool,
) -> PodLogStreamer | None:
    """Begin following pod logs, or return None when logs are switched off.

    Returns the entered streamer so the caller can stop it in a ``finally``; a
    failure to start is logged inside the streamer and yields an object that
    simply has nothing to stop.
    """
    if skip_pod_logs:
        return None
    pod_log_config = config.get("pod_log_scan", {}) or {}
    if not pod_log_config.get("enabled", False):
        return None
    streamer = PodLogStreamer(
        namespace=pod_log_config.get("namespace", "vss-search"),
        pod_prefixes=pod_log_config.get("pods", []),
        since_time=datetime.fromtimestamp(start_epoch, timezone.utc),
        output_dir=run_output_dir / "pod_logs",
    )
    return streamer.__enter__()


def execute_single_run(
    run_config: RunConfig,
    query_set: list[QueryItem],
    config: dict[str, Any],
    output_dir: Path,
    query_plans: dict[str, QueryPlan],
    vss_cmd: list[str],
    cli_spawn_cost_sec: float = 0.0,
    skip_prometheus: bool = False,
    skip_pod_logs: bool = False,
    health_gate_sec: float | None = None,
    cooldown: dict[str, Any] | None = None,
) -> RunSummary:
    """Execute one sweep cell = (critic, top_k, concurrency[, path]).

    Writes the run's own files but not ``run_summary.json``, the completion
    marker: :func:`append_run_summary` writes that last, once the shared
    summary files hold the row too.
    """
    exec_config = config.get("execution", {}) or {}
    warmup_rounds = int(exec_config.get("warmup_rounds", 1))
    min_samples = samples_per_query(
        exec_config, run_config.concurrency, len(query_set)
    )
    timeout_sec = int(exec_config.get("request_timeout_sec", 300))
    inter_delay = float(exec_config.get("inter_request_delay_sec", 0) or 0)
    min_duration = float(exec_config.get("min_run_duration_sec", 0) or 0)
    cli_options = config.get("cli", {}) or {}

    run_output_dir = output_dir / run_config.run_id
    run_output_dir.mkdir(parents=True, exist_ok=True)

    warmup_total = warmup_rounds * len(query_set) * run_config.concurrency
    measured_target = min_samples * len(query_set)

    say("")
    say(RULE)
    say(f"  RUN: {run_config.run_id}")
    say(f"  critic={run_config.critic_label} "
        f"top_k={run_config.top_k} concurrency={run_config.concurrency}")
    if run_config.path:
        # Worth stating per run: at concurrency > 1 this is every worker sending
        # the same path at once, not a mixture, so these numbers are not
        # comparable with an unfiltered sweep's.
        say(f"  path={run_config.path} only "
            f"({', '.join(q.id for q in query_set)}) -- no round-robin mix")
    else:
        mix = path_distribution(
            {q.id: query_plans[q.id].plan for q in query_set}
        )
        say("  Paths in the mix: "
            + ", ".join(f"{p}={n}" for p, n in sorted(mix.items()) if n))
    say(f"  Warmup:  {warmup_rounds} round(s) x {len(query_set)} queries x "
        f"{run_config.concurrency} worker(s) = {warmup_total} requests (discarded)")
    say(f"  Measure: {min_samples} samples/query x {len(query_set)} queries = "
        f"{measured_target} requests, shared across all workers "
        f"({measured_target / run_config.concurrency:.1f} rounds/worker)")
    if min_duration > 0:
        # A floor, so the budget above is a minimum rather than a target: a run
        # that spends it sooner keeps measuring until the clock is up. Quoted in
        # metric samples as well as seconds, since sizing the window against the
        # Prometheus step is the whole reason the floor exists -- and read from
        # the configured step rather than assumed, because a sweep that changes
        # the step changes what any given floor is worth.
        step = float((config.get("metrics", {}) or {}).get("range_step_sec", 15) or 15)
        points = min_duration / step if step > 0 else 0.0
        say(f"  Floor:   >= {min_duration:g}s measured window "
            f"({points:.1f} metric samples at a {step:g}s step); the request "
            f"budget above is a minimum, not a cap")
    say(RULE)

    started_at = utc_now_iso()
    start_epoch = time.time()
    start_mono = time.perf_counter()

    budget = SampleBudget(
        query_ids=[q.id for q in query_set],
        target_per_query=min_samples,
        min_duration_sec=min_duration,
    )
    measurement_start_mono: list[float] = []

    def _on_barrier_release() -> None:
        measurement_start_mono.append(time.perf_counter())
        # The duration floor measures the same window the throughput does, so it
        # starts here and not at run start: warmup would otherwise eat into it,
        # and at c20 warmup is a fifth of a 300 s floor.
        budget.open_window()

    # The barrier action fires once, on the last worker to arrive, which is
    # exactly the moment the measured window opens at full concurrency.
    barrier = threading.Barrier(run_config.concurrency, action=_on_barrier_release)

    all_records: list[RequestRecord] = []
    # Started before the first request and stopped after the last, so a service
    # that dies mid-run has its own death in the file. Collecting afterwards
    # does not: the kubelet rotates a screaming container's log away first.
    streamer = _start_pod_log_stream(config, run_output_dir, start_epoch, skip_pod_logs)
    streamed_files: set[str] = set()
    # Shared across workers so one worker hitting an unrecoverable CLI state
    # stops the others too, instead of every thread independently rediscovering
    # that the backend is down.
    abort = threading.Event()
    try:
        with ThreadPoolExecutor(max_workers=run_config.concurrency) as executor:
            futures = [
                executor.submit(
                    run_worker,
                    worker_id=wid,
                    query_set=query_set,
                    query_plans=query_plans,
                    run_config=run_config,
                    vss_cmd=vss_cmd,
                    warmup_rounds=warmup_rounds,
                    budget=budget,
                    barrier=barrier,
                    timeout_sec=timeout_sec,
                    inter_request_delay_sec=inter_delay,
                    cli_options=cli_options,
                    abort=abort,
                )
                for wid in range(1, run_config.concurrency + 1)
            ]
            for i, future in enumerate(as_completed(futures), 1):
                worker_records = future.result()
                all_records.extend(worker_records)
                measured = sum(
                    1 for r in worker_records if not r.is_warmup and r.succeeded
                )
                say(f"    Worker done [{i}/{run_config.concurrency}]: "
                    f"total={len(worker_records)} measured={measured}")
    finally:
        if streamer is not None:
            streamer.__exit__(None, None, None)
            streamed_files = set(streamer.streamed)

    if abort.is_set():
        # The records are kept for debugging, but no run_summary.json: that
        # would mark the cell complete and --resume would skip it forever.
        _write_records(run_output_dir, all_records)
        culprit = next(
            (r for r in all_records if r.error and r.exit_code > 0),
            None,
        )
        detail = culprit.error if culprit is not None else "unknown CLI fault"
        raise EnvironmentFault(
            f"{run_config.run_id} stopped: the CLI reported a state no retry "
            f"fixes -- {detail}\n"
            "  Nothing after this cell would measure anything either, so the "
            "sweep stops here. Fix the deployment or the CLI flags and "
            "re-run with --resume to keep the cells already finished."
        )

    end_mono = time.perf_counter()
    wall_clock = end_mono - start_mono
    # Throughput must exclude warmup from both numerator and denominator. The
    # barrier action only fires when the last worker arrives in time, so an empty
    # list means the barrier broke and there is no honest measured window to
    # divide by; fall back to the whole run but mark the run as not comparable
    # instead of publishing a throughput that quietly includes warmup.
    window_clean = bool(measurement_start_mono)
    measurement_wall = (
        end_mono - measurement_start_mono[0] if window_clean else wall_clock
    )
    end_epoch = time.time()
    measurement_start_epoch = (
        start_epoch + (measurement_start_mono[0] - start_mono) if window_clean
        else start_epoch
    )
    completed_at = utc_now_iso()

    warmup_records = [r for r in all_records if r.is_warmup]
    measured_records = [r for r in all_records if not r.is_warmup]
    success_records = [r for r in measured_records if r.succeeded]
    failure_records = [r for r in measured_records if not r.succeeded]

    say("")
    say(f"  Completed in {format_duration(wall_clock)} "
        f"(measured window {format_duration(measurement_wall)})")
    say(f"  Total requests: {len(all_records)} "
        f"(warmup={len(warmup_records)}, measured={len(measured_records)})")
    say(f"  Success: {len(success_records)} | Failures: {len(failure_records)}")

    # Said out loud because critic is a sweep axis: a run where the critic
    # quietly did not run looks like a fast run, not like a broken one.
    critic_ran = sum(1 for r in measured_records if r.critic_ran)
    confirmed = sum(r.critic_confirmed for r in measured_records)
    rejected = sum(r.critic_rejected for r in measured_records)
    unverified = sum(r.critic_unverified for r in measured_records)
    if not run_config.critic:
        if critic_ran:
            say(f"  !! Critic: set off, yet {critic_ran} request(s) carried "
                "verification evidence -- the critic-off config was not used.")
        else:
            say("  Critic: off (no RT-VLM route in this run's CLI config)")
    elif measured_records and not critic_ran:
        say("  !! Critic: no request carried verification evidence. This run "
            "is not comparable with one where it did -- check the deployment "
            "has a VLM.")
    elif critic_ran:
        say(f"  Critic: ran on {critic_ran}/{len(measured_records)} measured "
            f"request(s); {confirmed} confirmed, {rejected} rejected, "
            f"{unverified} left unverified")

    per_query_stats = compute_per_query_stats(all_records)
    for qid, stats in sorted(per_query_stats.items()):
        path = query_plans[qid].path if qid in query_plans else "unknown"
        say(f"    {qid} [{path}]: n={stats['count']:.0f} "
            f"p50={stats['p50'] * 1000:.0f}ms p95={stats['p95'] * 1000:.0f}ms "
            f"p99={stats['p99'] * 1000:.0f}ms")

    if len(success_records) < measured_target:
        say(f"  !! Only {len(success_records)}/{measured_target} measured samples "
            f"collected -- percentiles are below the target of {min_samples}/query")
    elif budget.extended:
        # Not a warning: the extra samples are the point of the floor, and this
        # is the line that tells a reader why n is above the configured budget.
        # Asked of the budget rather than inferred from the record count, so it
        # says what actually happened even when the two disagree.
        say(f"  Duration floor held the window open past the request budget: "
            f"{len(success_records)} samples against {measured_target} asked for "
            f"({len(success_records) / measured_target:.1f}x)")

    qps = len(success_records) / measurement_wall if measurement_wall > 0 else 0.0
    say(f"  Throughput: {qps:.2f} queries/sec")
    # Every query here is a Python interpreter start. When the measured latency
    # is within a few multiples of that, the number describes this host's
    # ability to fork processes at least as much as it describes the cluster,
    # and nobody reading a throughput table later would guess it.
    if cli_spawn_cost_sec > 0 and success_records:
        median = sorted(r.latency_sec for r in success_records)[
            len(success_records) // 2
        ]
        share = cli_spawn_cost_sec / median if median > 0 else 0.0
        if share >= 0.2:
            say(f"  !! CLI process startup is {cli_spawn_cost_sec:.2f}s against a "
                f"{median:.2f}s median query ({share:.0%}). At concurrency "
                f"{run_config.concurrency} this host runs that many interpreters "
                "at once, so part of this throughput is the load generator.")
    if not window_clean:
        say("  !! The post-warmup barrier broke, so this run never ran at the full "
            f"concurrency of {run_config.concurrency} and its throughput is measured "
            "over the whole run including warmup. Exclude it from the throughput "
            "comparison or re-measure (measurement_window_clean=false).")

    prometheus_metrics: dict[str, Any] = {}
    if not skip_prometheus:
        prom_url = (config.get("services", {}) or {}).get("prometheus_url", "")
        metrics_config = config.get("metrics", {}) or {}
        range_queries = metrics_config.get("range_queries", {}) or {}
        counter_queries = metrics_config.get("counter_queries", {}) or {}
        step_sec = int(metrics_config.get("range_step_sec", 15))
        if prom_url and range_queries:
            say("  Collecting Prometheus metrics...")
            # Scoped to the measured window, not the whole run: resource figures
            # averaged over warmup as well would describe a different window
            # than the latency and throughput they are meant to explain.
            prometheus_metrics = collect_run_metrics(
                prom_url, range_queries, measurement_start_epoch, end_epoch, step_sec,
                counter_queries=counter_queries,
            )
            prometheus_metrics["window"] = {
                "start_epoch": measurement_start_epoch,
                "end_epoch": end_epoch,
                "excludes_warmup": window_clean,
            }
            log_prometheus_coverage(prometheus_metrics, prom_url)
        else:
            missing = (
                "services.prometheus_url" if not prom_url else "metrics.range_queries"
            )
            say(f"  !! No GPU/CPU/memory metrics: {missing} is empty, so this run "
                "reports latency with nothing to explain it.")

    pod_log_errors: dict[str, Any] = {"enabled": False}
    if not skip_pod_logs:
        pod_log_config = config.get("pod_log_scan", {}) or {}
        if pod_log_config.get("enabled", False):
            if streamed_files:
                say(f"  Pod logs: {len(streamed_files)} followed live during the run")
            else:
                say("  Collecting pod logs...")
            namespace = pod_log_config.get("namespace", "vss-search")
            pod_prefixes = pod_log_config.get("pods", [])
            tail_lines = int(pod_log_config.get("tail_lines", 50000))
            # From the start of the run, not of the measured window, and unlike
            # every other collector here. A service that crashed or threw during
            # warmup is why the measured window then looked the way it did, and
            # a file that began after warmup would not show it. The cost is that
            # anything counted out of these logs spans a longer window than the
            # latency beside it, so the boundary is published as
            # measurement_started_at for whatever reads them to cut on.
            since_time = datetime.fromtimestamp(start_epoch, timezone.utc)
            collect_pod_logs(
                namespace, pod_prefixes, since_time,
                run_output_dir / "pod_logs", tail_lines=tail_lines,
                skip_files=streamed_files,
            )
            pod_log_errors = scan_pod_log_errors(
                run_output_dir / "pod_logs",
                pod_log_config.get("patterns", []),
                pod_log_config.get("exclude_patterns", []),
            )
            total_errors = pod_log_errors.get("total_errors", 0)
            if total_errors > 0:
                say(f"  !! Pod log errors detected: {total_errors}")

    summary = RunSummary(
        run_id=run_config.run_id,
        critic_eval_count=run_config.critic_eval_count,
        top_k=run_config.top_k,
        concurrency=run_config.concurrency,
        path=run_config.path,
        critic=run_config.critic,
        cli_spawn_cost_sec=cli_spawn_cost_sec,
        query_count=len(query_set),
        started_at=started_at,
        completed_at=completed_at,
        measurement_started_at=datetime.fromtimestamp(
            measurement_start_epoch, timezone.utc
        ).isoformat().replace("+00:00", "Z"),
        wall_clock_sec=wall_clock,
        measurement_wall_clock_sec=measurement_wall,
        measurement_window_clean=window_clean,
        total_requests=len(all_records),
        warmup_requests=len(warmup_records),
        measured_requests=len(measured_records),
        success_count=len(success_records),
        failure_count=len(failure_records),
        queries_per_sec=qps,
        critic_ran_count=critic_ran,
        critic_confirmed=confirmed,
        critic_rejected=rejected,
        critic_unverified=unverified,
        latency_stats=compute_run_stats(all_records),
        resolved_paths=";".join(
            f"{q.id}={query_plans[q.id].path}" for q in query_set if q.id in query_plans
        ),
        health_gate_sec=health_gate_sec,
        per_query_stats=per_query_stats,
        prometheus_metrics=prometheus_metrics,
        pod_log_errors=pod_log_errors,
        cooldown=cooldown or {},
    )

    write_run_artifacts(run_output_dir, summary, all_records, prometheus_metrics)
    return summary


def _write_records(output_dir: Path, records: list[RequestRecord]) -> None:
    with open(output_dir / "request_records.jsonl", "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(asdict(r), default=str) + "\n")


def _cell_columns(cell: RunSummary | RequestRecord) -> dict[str, Any]:
    """The columns of :data:`CELL_FIELDS`, formatted the same in every CSV."""
    return {
        "run_id": cell.run_id,
        "critic": "on" if cell.critic else "off",
        "critic_eval_count": "" if cell.critic_eval_count is None else cell.critic_eval_count,
        "top_k": cell.top_k,
        "concurrency": cell.concurrency,
        "path": cell.path,
    }


def write_run_artifacts(
    output_dir: Path,
    summary: RunSummary,
    records: list[RequestRecord],
    prometheus_metrics: dict[str, Any],
) -> None:
    """Write per-run output files: raw records, metrics, per-request CSV."""
    _write_records(output_dir, records)

    if prometheus_metrics:
        with open(output_dir / "prometheus_metrics.json", "w", encoding="utf-8") as f:
            json.dump(prometheus_metrics, f, indent=2, default=str)

    if summary.pod_log_errors.get("total_errors"):
        with open(output_dir / "pod_log_errors.json", "w", encoding="utf-8") as f:
            json.dump(summary.pod_log_errors, f, indent=2, default=str)

    with open(
        output_dir / "per_query_latency.csv", "w", newline="", encoding="utf-8"
    ) as f:
        writer = csv.DictWriter(f, fieldnames=LATENCY_FIELDS)
        writer.writeheader()
        for r in records:
            if r.is_warmup:
                continue
            row = {k: v for k, v in asdict(r).items() if k in LATENCY_FIELDS}
            writer.writerow({
                **row,
                **_cell_columns(r),
                "path": r.path,
                "latency_sec": f"{r.latency_sec:.6f}",
            })


def _check_header(path: Path, fields: list[str]) -> None:
    """Refuse to append rows under a header with different columns.

    A resumed directory written by an older version of this script would
    otherwise get rows whose values sit under the wrong column names.
    """
    with path.open(encoding="utf-8", newline="") as f:
        header = next(csv.reader(f), [])
    if header != fields:
        raise ValueError(
            f"{path} has columns from a different version of this benchmark; "
            "resume into a new output directory, or move the file aside"
        )


def _append_csv_rows(path: Path, fields: list[str], rows: list[dict[str, Any]]) -> None:
    """Append rows, writing the header only when creating the file."""
    exists = path.exists()
    if exists:
        _check_header(path, fields)
    with path.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        if not exists:
            writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in fields})


def _fmt(value: float | None, digits: int = 6) -> str:
    return "" if value is None else f"{value:.{digits}f}"


def summary_row(summary: RunSummary) -> dict[str, Any]:
    """One ``summary.csv`` row: every scalar field, plus the flattened ones."""
    scalars = {k: v for k, v in asdict(summary).items() if k in SUMMARY_FIELDS}
    stats = summary.latency_stats
    return {
        **scalars,
        **_cell_columns(summary),
        "wall_clock_sec": f"{summary.wall_clock_sec:.2f}",
        "measurement_wall_clock_sec": f"{summary.measurement_wall_clock_sec:.2f}",
        "queries_per_sec": f"{summary.queries_per_sec:.4f}",
        "cli_spawn_cost_sec": f"{summary.cli_spawn_cost_sec:.4f}",
        "p50_sec": _fmt(stats.get("p50")),
        "p95_sec": _fmt(stats.get("p95")),
        "p99_sec": _fmt(stats.get("p99")),
        "mean_sec": _fmt(stats.get("mean")),
        "health_gate_sec": _fmt(summary.health_gate_sec, 2),
        "pod_log_error_count": summary.pod_log_errors.get("total_errors", ""),
        "cooldown_reason": summary.cooldown.get("reason") or summary.cooldown.get("skipped", ""),
        "cooldown_waited_sec": summary.cooldown.get("waited_sec", ""),
    }


#: Pod name -> service name: drops a Deployment's ``-<hash>-<id>`` or a
#: StatefulSet's ``-<ordinal>``, and a GPU series' ``/gpuN``.
_POD_SUFFIX = re.compile(r"(-[a-z0-9]{6,10}-[a-z0-9]{5}|-\d+)$")


def service_of(series: str) -> str:
    return _POD_SUFFIX.sub("", series.split("/", 1)[0])


def resource_rows(summary: RunSummary) -> list[dict[str, Any]]:
    """Flatten ``prometheus_metrics`` into one row per (metric, series).

    Gauges carry the window's average and peak. Counters carry their mean rate
    over the window as ``avg`` -- for CPU that is the trustworthy per-run figure,
    where a ``rate()`` gauge over a short run still holds the previous run's work.
    """
    cell = _cell_columns(summary)
    rows: list[dict[str, Any]] = []
    metrics = summary.prometheus_metrics or {}
    for metric, info in sorted((metrics.get("summary") or {}).items()):
        for series, values in sorted((info.get("series") or {}).items()):
            rows.append({**cell, "metric": metric, "service": service_of(series),
                         "series": series, "avg": values.get("avg"),
                         "peak": values.get("peak")})
    for metric, info in sorted((metrics.get("window_mean") or {}).items()):
        for series, values in sorted((info.get("series") or {}).items()):
            rows.append({**cell, "metric": f"{metric}:mean_rate",
                         "service": service_of(series), "series": series,
                         "avg": values.get("mean_rate"), "peak": ""})
    return rows


def append_run_summary(
    output_dir: Path,
    summary: RunSummary,
    query_plans: dict[str, QueryPlan],
) -> None:
    """Append this run to the shared summary files, then mark it complete.

    Appending after every run keeps partial results usable when a sweep is split
    across invocations or interrupted part-way. ``run_summary.json`` is written
    last: a crash before it leaves the run pending for --resume, rather than
    marked complete with no row in ``summary.csv``.
    """
    _append_csv_rows(output_dir / "summary.csv", SUMMARY_FIELDS, [summary_row(summary)])
    _append_csv_rows(output_dir / "per_query_summary.csv", PER_QUERY_FIELDS, [
        {
            **_cell_columns(summary),
            "query_id": qid,
            "resolved_path": query_plans[qid].path if qid in query_plans else "unknown",
            "count": int(stats["count"]),
            **{f"{k}_sec": _fmt(stats[k])
               for k in ("p50", "p95", "p99", "mean", "min", "max", "std")},
        }
        for qid, stats in sorted(summary.per_query_stats.items())
    ])
    _append_csv_rows(output_dir / "resources_summary.csv", RESOURCE_FIELDS,
                     resource_rows(summary))

    with (output_dir / "summary.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(asdict(summary), default=str, sort_keys=True) + "\n")

    with open(output_dir / summary.run_id / "run_summary.json", "w", encoding="utf-8") as f:
        json.dump(asdict(summary), f, indent=2, default=str)


def log_prometheus_coverage(
    prometheus_metrics: dict[str, Any], prom_url: str
) -> None:
    """Report how many queries came back with data, and say so when none did.

    An unreachable Prometheus, a missing exporter and a mislabelled query all
    fail the same silent way: the summary returns zero samples and the run
    finishes with empty resource columns nobody notices until the report is
    being written. This turns that into a line in the log.
    """
    summary = prometheus_metrics.get("summary", {})
    if not summary:
        return

    populated = {name: info for name, info in summary.items() if info.get("samples")}
    pods = {
        series
        for info in populated.values()
        for series in info.get("series", {})
    }

    if not populated:
        errors = {
            str(payload["error"])
            for payload in prometheus_metrics.get("raw", {}).values()
            if isinstance(payload, dict) and payload.get("error")
        }
        say(f"  !! All {len(summary)} resource queries came back empty -- this run "
            "has no GPU, CPU or memory data.")
        if errors:
            say(f"     Prometheus at {prom_url} is not answering: "
                f"{sorted(errors)[0][:120]}")
        else:
            say(f"     Prometheus at {prom_url} answered, so the exporters are the "
                "suspect: no pod running in the namespace, DCGM not scraped, or "
                "kubelet/cAdvisor and kube-state-metrics missing.")
        return

    say(f"  Resource metrics: {len(populated)}/{len(summary)} queries with data, "
        f"{len(pods)} pod(s)/device(s) covered")
    empty = [name for name in summary if name not in populated]
    if empty:
        say(f"     No data for: {', '.join(sorted(empty))}")

    # Said here, while the run is in front of the operator, rather than leaving
    # a short run to be spotted in the report -- or not spotted, which is what
    # happens: a sweep once reported a CPU figure for 24 runs that were all too
    # short for the rate() window behind it.
    cpu = prometheus_metrics.get("window_mean", {}).get("cpu_core_seconds_total", {})
    if cpu:
        coverage = cpu.get("coverage_ratio") or 0.0
        if cpu.get("mean_rate") is None:
            say("     !! Run too short to difference the CPU counter -- its mean CPU "
                "is unmeasurable, and the rate() series for it carries the previous "
                "run's work. Use window_mean, not summary, for per-run CPU.")
        elif 0 < coverage < 0.75:
            say(f"     !! CPU counter spans only {coverage:.0%} of the measured "
                f"window ({cpu.get('covered_sec', 0):.0f}s) -- the scrape interval "
                "is coarse against a run this short.")
        if cpu.get("resets"):
            say(f"     !! CPU counter reset {cpu['resets']}x during the window: a "
                "container restarted mid-run.")


def log_es_counts(metadata: dict[str, Any]) -> None:
    es_counts = metadata.get("es_index_counts", {})
    if not es_counts.get("enabled"):
        say("  ES doc counts: skipped (set services.elasticsearch_url and "
            "corpus.es_index_patterns)")
        return
    for pattern, info in es_counts.get("indices", {}).items():
        if "error" in info:
            say(f"  ES {pattern}: ERROR {info['error']}")
        else:
            say(f"  ES {pattern}: {info['doc_count']:,} docs")


def capture_deployment_metadata(
    config: dict[str, Any], output_dir: Path
) -> dict[str, Any]:
    """Capture cluster state: helm values, pod list, nvidia-smi, ES doc counts."""
    import subprocess

    metadata: dict[str, Any] = {"captured_at": utc_now_iso()}
    deploy_config = config.get("deployment", {}) or {}
    namespace = deploy_config.get("namespace", "vss-search")
    release = deploy_config.get("helm_release", "vss-search")

    commands = {
        "kubectl_pods": (
            ["kubectl", "get", "pods", "-n", namespace, "-o", "wide"],
            "kubectl_pods.txt", 30,
        ),
        # The ClusterIPs cli.service_urls pointed at, as they were at this run.
        "kubectl_services": (
            ["kubectl", "get", "svc", "-n", namespace, "-o", "wide"],
            "kubectl_services.txt", 30,
        ),
        "helm_values": (
            ["helm", "get", "values", release, "-n", namespace, "-a"],
            "helm_values.yaml", 30,
        ),
        "nvidia_smi": (
            ["nvidia-smi",
             "--query-gpu=index,name,memory.total,memory.used,utilization.gpu",
             "--format=csv"],
            "nvidia_smi.csv", 15,
        ),
    }

    for key, (cmd, filename, timeout) in commands.items():
        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True, check=False, timeout=timeout
            )
            (output_dir / filename).write_text(proc.stdout, encoding="utf-8")
            metadata[key] = proc.stdout[:5000]
            if proc.returncode != 0:
                metadata[f"{key}_error"] = (
                    f"exit {proc.returncode}: {(proc.stderr or '').strip()[:500]}"
                )
        except (subprocess.TimeoutExpired, OSError) as e:
            metadata[f"{key}_error"] = str(e)

    es_url = (config.get("services", {}) or {}).get("elasticsearch_url", "")
    index_patterns = (config.get("corpus", {}) or {}).get("es_index_patterns", [])
    metadata["es_index_counts"] = capture_es_index_counts(es_url, index_patterns)

    (output_dir / "deployment_metadata.json").write_text(
        json.dumps(metadata, indent=2, default=str) + "\n", encoding="utf-8"
    )
    return metadata


def resolve_query_plans(
    config: dict[str, Any],
    query_set: list[QueryItem],
    output_dir: Path,
    repo_root: Path,
    video_sources: list[str] | None = None,
    reuse: bool = False,
) -> dict[str, QueryPlan]:
    """Decompose every query once, and fix the search path it will run on.

    This replaces what used to be a log-scraping probe. The agent used to pick
    the path internally and announce it only in an INFO line carrying no
    request id, so attribution needed a serialized run at concurrency 1 and
    still came out ambiguous whenever two replicas disagreed. Driving the CLI
    means the benchmark makes the choice itself, so the path is simply known,
    at any concurrency, for every request.

    Resolved once for the whole sweep rather than per request. The LLM runs at
    temperature 0 but is not contractually deterministic, and a query that
    routed to ``fusion`` on one request and ``attribute`` on the next would mix
    two pipelines into one latency distribution with nothing saying so.

    For the same reason a resumed sweep reuses the plans an earlier invocation
    wrote to ``query_plans.json`` (``reuse=True``) and decomposes only queries
    it has no plan for. A query whose text changed since is refused: its
    finished runs measured a different question.
    """
    decompose_config = config.get("decomposition", {}) or {}
    source_type = config.get("source_type", "video_file")
    pinned = read_pin_paths(config, query_set)

    plans: dict[str, QueryPlan] = {}
    previous = _load_query_plans(output_dir / "query_plans.json") if reuse else {}
    for item in query_set:
        old = previous.get(item.id)
        if old is None:
            continue
        if old.query_text != item.query:
            raise DecompositionError(
                f"{item.id} changed since this sweep started "
                f"({old.query_text!r} -> {item.query!r}); its finished runs "
                "measured the old text. Resume into a new output directory."
            )
        plans[item.id] = old
        say(f"  == {item.id}: {old.path} (reused from query_plans.json)")

    decomposer: LiveDecomposer | None = None
    needs_llm = any(q.id not in pinned and q.id not in plans for q in query_set)

    if needs_llm:
        from .decompose import require_llm_url

        llm_url = require_llm_url(config.get("services", {}) or {})
        decomposer = LiveDecomposer(
            llm_url=llm_url,
            repo_root=repo_root,
            model=decompose_config.get("model") or None,
            timeout_sec=float(decompose_config.get("timeout_sec", 120)),
        )
        say(f"  Decomposing against {llm_url} ({decomposer.model})")

    for item in query_set:
        if item.id in plans:
            continue
        plan = QueryPlan(
            query_id=item.id,
            query_text=item.query,
            expected_path=item.expected_path,
        )
        if item.id in pinned:
            # The only way to reach `tag`: nothing in the decomposition
            # contract asks for keyword retrieval over VLM tag documents, so a
            # tag run has to be requested explicitly.
            plan.path = pinned[item.id]
            plan.plan = pinned_plan(item.query, plan.path, source_type)
            plan.routed = False
            plan.status = "pinned"
            say(f"  -- {item.id}: {plan.path} (pinned, not routed)")
        else:
            assert decomposer is not None
            try:
                decomposition, elapsed = decomposer.decompose(
                    item.query, video_sources
                )
                plan.plan = plan_for(item.query, decomposition, source_type)
            except (DecompositionError, ValueError) as e:
                plan.status = "failed"
                plan.error = str(e)
                say(f"  !! {item.id}: {e}")
                plans[item.id] = plan
                continue
            plan.decomposition = decomposition
            plan.decompose_sec = elapsed
            plan.path = route(decomposition)
            plan.status = "ok"
            expected = (
                f" (expected {item.expected_path})" if item.expected_path else ""
            )
            say(f"  OK {item.id}: {plan.path}{expected} in {elapsed:.1f}s")
            if item.expected_path and item.expected_path != plan.path:
                say("       Routed somewhere other than the configured "
                    "expectation -- analysis groups by the resolved path, so "
                    "update expected_path or accept the grouping as measured")
        plans[item.id] = plan

    failed = [p for p in plans.values() if p.status == "failed"]
    if failed:
        raise DecompositionError(
            f"{len(failed)} of {len(query_set)} queries could not be decomposed: "
            + "; ".join(f"{p.query_id} ({p.error[:80]})" for p in failed)
            + "\n  Every query needs a path before the sweep starts, because the "
            "CLI takes decomposed arguments rather than a question."
        )

    payload = {
        "resolved_at": utc_now_iso(),
        "decomposer": decomposer.describe() if decomposer is not None else None,
        "distribution": path_distribution({k: v.plan for k, v in plans.items()}),
        "plans": [asdict(p) for p in plans.values()],
    }
    (output_dir / "query_plans.json").write_text(
        json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8"
    )

    mix = payload["distribution"]
    say("  Path distribution: "
        + ", ".join(f"{p}={n}" for p, n in sorted(mix.items())))
    unmeasured = sorted(p for p, n in mix.items() if not n)
    if unmeasured:
        # Printed because a zero here is a gap in what the sweep covers, and a
        # missing row is far easier to miss than a zero.
        say(f"  Not measured at all: {', '.join(unmeasured)} -- no query routes "
            "there, so this sweep says nothing about those paths")
    return plans


def _load_query_plans(path: Path) -> dict[str, QueryPlan]:
    """Plans a previous invocation resolved, keyed by query id; {} if none."""
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        plans = [QueryPlan(**p) for p in payload.get("plans", [])]
    except (json.JSONDecodeError, OSError, TypeError) as e:
        raise DecompositionError(f"cannot reuse {path}: {e}") from e
    for p in plans:
        # Sweeps from before the path was renamed recorded the CLI's name.
        if p.path == "embed":
            p.path = p.plan["path"] = EVENT
    return {p.query_id: p for p in plans if p.status in ("ok", "pinned")}


def run_health_gate(
    run_config: RunConfig,
    query_set: list[QueryItem],
    query_plans: dict[str, QueryPlan],
    vss_cmd: list[str],
    timeout_sec: int,
    cli_options: dict[str, Any] | None = None,
) -> RequestRecord:
    """One search, alone, to check the service still answers before a run starts.

    Readiness is not something a benchmark can gate on: through a whole outage
    ``vss-agent`` returned 200 on ``/v1/health/ready`` while every search
    request hung to the full client timeout, so only a real search
    distinguishes a working service from a wedged one.

    Sent with the upcoming run's own critic setting and ``top_k`` rather than
    something cheaper, because the critic is what reaches the VLM: a gate that
    skipped it cannot observe a wedged VLM at all, which is the failure this
    exists to catch. At concurrency 1 that costs a few seconds when healthy.

    The deadline is deliberately separate from and shorter than
    ``request_timeout_sec``. Measurement should give the service every chance;
    a gate should find out quickly, and the difference between the two is the
    whole saving.
    """
    return send_search_request(
        vss_cmd=vss_cmd,
        query_item=query_set[0],
        query_plan=query_plans[query_set[0].id],
        # concurrency=1 so the discarded record does not describe itself as
        # part of a load level it was never sent at.
        run_config=replace(run_config, concurrency=1),
        worker_id=0,
        iteration=-1,
        is_warmup=True,
        timeout_sec=timeout_sec,
        cli_options=cli_options,
    )


def wait_for_cooldown(config: dict[str, Any], previous_run_id: str) -> dict[str, Any]:
    """Hold the sweep until the previous run's work has drained out of the pods.

    A run ends when its own requests have been answered, which is not the same
    as the cluster being idle: work the agent has already dispatched keeps
    burning CPU after the last response is in. So the next run opens its
    measurement window on a cluster still busy with the last one, and attributes
    that CPU to itself.

    The size of it, measured: the embed path makes no call to ``vss-rtvi-cv`` at
    all -- zero across 200 requests, verified in the pod log -- yet its short
    cells reported 68 to 111 CV cores. Those cells run 20 to 30 s and follow an
    attribute-path cell whose CV work takes 20 to 25 s to drain, so the whole
    measured window sat inside the drain. The c1 cells are 67 to 80 s, long
    enough to show what the others hide: CV falls to exactly 0.0 cores by 25 s
    and stays there.

    Polling beats sleeping because the drain scales with the run that produced
    it. ``idle_cores`` is read against the same expression as the ``cpu_cores``
    metric, so a threshold here means the same thing it does in the report.

    Prometheus returning nothing is not treated as idle -- an absent series and
    a quiet pod are indistinguishable from here, and guessing idle would
    silently restore the bug this exists to fix. In that case the wait falls
    back to ``max_wait_sec``, which is slower and always correct.
    """
    cooldown = (config.get("execution", {}) or {}).get("cooldown", {}) or {}
    if not cooldown.get("enabled", True):
        return {"skipped": "disabled"}

    max_wait = float(cooldown.get("max_wait_sec", 180) or 0)
    if max_wait <= 0:
        return {"skipped": "max_wait_sec is 0"}

    idle_cores = float(cooldown.get("idle_cores", 5.0))
    poll_every = max(1.0, float(cooldown.get("poll_interval_sec", 5)))
    query = str(cooldown.get("query", "") or "")
    prom_url = (config.get("services", {}) or {}).get("prometheus_url", "")

    say(f"  Cooldown: waiting for the cluster to drain after {previous_run_id} "
        f"(idle is under {idle_cores:g} cores, giving up after {max_wait:g}s)")

    started = time.monotonic()
    readings: list[float] = []
    blind = False
    while True:
        waited = time.monotonic() - started
        busiest = query_prometheus_instant(prom_url, query)
        if busiest is None:
            blind = True
        else:
            readings.append(busiest)
            if busiest <= idle_cores:
                say(f"  Cooldown: drained after {waited:.0f}s "
                    f"(busiest pod at {busiest:.1f} cores)")
                return {"waited_sec": round(waited, 1), "reason": "drained",
                        "final_cores": round(busiest, 1)}
        if waited >= max_wait:
            break
        time.sleep(min(poll_every, max_wait - waited))

    waited = time.monotonic() - started
    if blind and not readings:
        say(f"  Cooldown: !! Prometheus gave no reading, so the full {waited:.0f}s "
            "was spent blind. Per-service CPU for the next run is trustworthy, "
            "but check prometheus_url and execution.cooldown.query.")
        return {"waited_sec": round(waited, 1), "reason": "no_prometheus_reading"}

    last = readings[-1] if readings else None
    say(f"  Cooldown: !! still busy after {max_wait:.0f}s"
        + (f" (busiest pod at {last:.1f} cores)" if last is not None else "")
        + ". The next run's per-service CPU will include some of this one's "
          "work; raise execution.cooldown.max_wait_sec to separate them.")
    return {"waited_sec": round(waited, 1), "reason": "timeout",
            "final_cores": round(last, 1) if last is not None else None}
