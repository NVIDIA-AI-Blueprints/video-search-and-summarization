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
from .cli import EnvironmentFault, is_environment_fault, is_overload
from .console import RULE, say
from .decompose import DecompositionError, LiveDecomposer, QueryRouter
from .models import (
    LATENCY_FIELDS,
    PER_QUERY_FIELDS,
    RESOURCE_FIELDS,
    STAGE_FIELDS,
    SUMMARY_FIELDS,
    QueryItem,
    QueryPlan,
    RequestRecord,
    RunConfig,
    RunSummary,
)
from .plan import read_pin_paths, samples_per_query
from .resources import (
    HostSampler,
    PodLogStreamer,
    capture_es_index_counts,
    collect_pod_logs,
    collect_run_metrics,
    query_prometheus_instant,
    rt_vlm_window_stats,
    scan_pod_log_errors,
    host_resource_rows,
    scrape_rt_vlm_histograms,
)
from .routing import EVENT, path_distribution, plan_for
from .stats import (
    compute_per_query_stats,
    compute_run_stats,
    compute_stage_stats,
    format_duration,
    latency_samples,
)
from .worker import DECOMPOSE_ERROR_EXIT, run_worker, send_search_request, utc_now_iso


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
    *,
    router: QueryRouter,
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
    e2e_timeout_sec = e2e_timeout(config)
    min_stability = float(exec_config.get("min_path_stability", 0.95))
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
    rt_vlm_url = (config.get("services", {}) or {}).get("rt_vlm_metrics_url", "")
    rt_vlm_before: list[dict[str, Any] | None] = []
    host_sampler = HostSampler()

    def _on_barrier_release() -> None:
        measurement_start_mono.append(time.perf_counter())
        host_sampler.start()
        # Read as the window opens: every worker has finished warmup and none
        # has sent a measured request, so the difference is the window alone.
        if rt_vlm_url and run_config.critic:
            rt_vlm_before.append(scrape_rt_vlm_histograms(rt_vlm_url))
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
                    router=router,
                    e2e_timeout_sec=e2e_timeout_sec,
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
        # Before the log streamer stops: its kubectl children would otherwise
        # be reaped into the CLI's CPU figure.
        host_resources = host_sampler.stop()
    finally:
        if streamer is not None:
            streamer.__exit__(None, None, None)
            streamed_files = set(streamer.streamed)

    rt_vlm_stats = rt_vlm_window_stats(
        rt_vlm_before[0] if rt_vlm_before else None,
        scrape_rt_vlm_histograms(rt_vlm_url) if rt_vlm_before else None,
    )

    stopped_reason = ""
    if abort.is_set():
        culprit = next(
            (r for r in all_records if r.error and r.exit_code > 0),
            None,
        )
        detail = culprit.error if culprit is not None else "unknown CLI fault"
        fault_codes = {r.exit_code for r in all_records if is_environment_fault(r.exit_code)}
        backend_was_up = health_gate_sec is not None or any(r.succeeded for r in all_records)
        if is_overload(fault_codes, backend_was_up):
            faults = sum(1 for r in all_records if r.exit_code in fault_codes)
            stopped_reason = (f"overloaded: {faults} request(s) failed with "
                              f"{detail}")
            say(f"  !! {run_config.run_id} overloaded the backend and was stopped: "
                f"{faults}/{len(all_records)} request(s) failed with {detail}")
            say("     Recorded as a failed cell; the sweep continues, and the next "
                "health gate stops it if the backend is really down.")
    if abort.is_set() and not stopped_reason:
        # The records are kept for debugging, but no run_summary.json: that
        # would mark the cell complete and --resume would skip it forever.
        _write_records(run_output_dir, all_records)
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
    window_clean = bool(measurement_start_mono) and not stopped_reason
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
    per_query_e2e = compute_per_query_stats(all_records, "e2e_latency_sec")
    per_query_decompose = compute_per_query_stats(all_records, "decompose_sec")
    for qid, stats in per_query_stats.items():
        e2e = per_query_e2e[qid]
        stats.update({f"e2e_{k}": e2e[k] for k in ("p50", "p95", "p99", "mean")})
        stats["decompose_mean"] = per_query_decompose[qid]["mean"]
    for qid, stats in sorted(per_query_stats.items()):
        path = query_plans[qid].path if qid in query_plans else "unknown"
        say(f"    {qid} [{path}]: n={stats['count']:.0f} "
            f"e2e p50={stats['e2e_p50'] * 1000:.0f}ms p95={stats['e2e_p95'] * 1000:.0f}ms "
            f"p99={stats['e2e_p99'] * 1000:.0f}ms | "
            f"decompose mean={stats['decompose_mean'] * 1000:.0f}ms | "
            f"CLI p50={stats['p50'] * 1000:.0f}ms")

    e2e_stats = compute_run_stats(all_records, "e2e_latency_sec")
    decompose_stats = compute_run_stats(all_records, "decompose_sec")
    llm_share = (
        decompose_stats["mean"] / e2e_stats["mean"]
        if e2e_stats and e2e_stats["mean"] > 0 else None
    )
    routed = [r for r in measured_records if r.resolved_path]
    off_target = [r for r in routed if not r.on_target]
    decompose_errors = sum(1 for r in measured_records if r.exit_code == DECOMPOSE_ERROR_EXIT)
    path_stability = (len(routed) - len(off_target)) / len(routed) if routed else None
    cell_valid = path_stability is not None and path_stability >= min_stability
    if llm_share is not None:
        say(f"  LLM share of e2e latency: {llm_share:.0%} "
            f"(decompose mean {decompose_stats['mean']:.2f}s of {e2e_stats['mean']:.2f}s)")
    stage_stats = compute_stage_stats(all_records)
    wall_steps = [("decompose", "decompose"), ("cli overhead", "cli overhead"),
                  ("search: retrieval", "retrieval"), ("critic: verify", "verify")]
    shown = [f"{name} {stage_stats[label]['mean']:.2f}s"
             for label, name in wall_steps if label in stage_stats]
    if shown:
        say("  Steps (mean wall per request): " + " | ".join(shown)
            + " -- every stage in stage_summary.csv")
    if host_resources.get("cli_cpu_cores") is not None:
        say(f"  Load generator: CLI processes used {host_resources['cli_cpu_cores']:.1f} cores"
            + (f"; host {host_resources['host_cpu_cores_avg']:.1f}/"
               f"{host_resources['host_cores']:.0f} cores avg, "
               f"{host_resources['host_cpu_cores_peak']:.1f} peak"
               if "host_cpu_cores_avg" in host_resources else ""))
    if rt_vlm_stats:
        say("  RT-VLM per clip (mean): " + " | ".join(
            f"{step.replace(' (RT-VLM)', '')} {st['per_clip_mean']:.2f}s"
            for step, st in rt_vlm_stats.items())
            + f" over {rt_vlm_stats[next(iter(rt_vlm_stats))]['clips']:.0f} clip(s)")
    elif rt_vlm_url and run_config.critic:
        say("  !! RT-VLM per-clip split unavailable: services.rt_vlm_metrics_url "
            "gave no reading, or no clip was verified in the window")
    if path_stability is not None:
        say(f"  Path stability: {path_stability:.1%} "
            f"({len(off_target)} of {len(routed)} measured request(s) routed off target)")
        if off_target:
            where = sorted({r.resolved_path for r in off_target})
            say(f"    off target went to: {', '.join(where)} -- counted in throughput, "
                "left out of this cell's latency")
    if not cell_valid:
        say(f"  !! Cell INVALID: path stability below {min_stability:.0%}. It mixes "
            "pipelines and is left out of the comparisons (cell_valid=false).")
    if decompose_errors:
        say(f"  !! {decompose_errors} measured request(s) failed to decompose "
            "(decompose_error); the CLI was not run for them.")

    on_target_count = len(latency_samples(all_records))
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
    say(f"  Throughput: {qps:.2f} queries/sec end-to-end "
        f"({on_target_count} on-target sample(s) in the latency figures)")
    # Every query here is a Python interpreter start. When the measured latency
    # is within a few multiples of that, the number describes this host's
    # ability to fork processes at least as much as it describes the cluster,
    # and nobody reading a throughput table later would guess it.
    if cli_spawn_cost_sec > 0 and success_records:
        median = sorted(r.e2e_latency_sec for r in success_records)[
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
        e2e_latency_stats=e2e_stats,
        decompose_stats=decompose_stats,
        llm_share=llm_share,
        off_target_count=len(off_target),
        decompose_error_count=decompose_errors,
        path_stability=path_stability,
        cell_valid=cell_valid,
        resolved_paths=";".join(
            f"{q.id}={query_plans[q.id].path}" for q in query_set if q.id in query_plans
        ),
        health_gate_sec=health_gate_sec,
        per_query_stats=per_query_stats,
        stage_stats=stage_stats,
        rt_vlm_stats=rt_vlm_stats,
        host_resources=host_resources,
        prometheus_metrics=prometheus_metrics,
        pod_log_errors=pod_log_errors,
        cooldown=cooldown or {},
        stopped_reason=stopped_reason,
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
                "on_target": r.on_target,
                "decompose_sec": f"{r.decompose_sec:.6f}",
                "latency_sec": f"{r.latency_sec:.6f}",
                "e2e_latency_sec": f"{r.e2e_latency_sec:.6f}",
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
    e2e = summary.e2e_latency_stats
    decompose = summary.decompose_stats
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
        **{f"e2e_{k}_sec": _fmt(e2e.get(k)) for k in ("p50", "p95", "p99", "mean")},
        **{f"decompose_{k}_sec": _fmt(decompose.get(k)) for k in ("p50", "p95", "p99", "mean")},
        "llm_share": _fmt(summary.llm_share, 4),
        "path_stability": _fmt(summary.path_stability, 4),
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
    rows.extend({**cell, **row} for row in host_resource_rows(summary.host_resources))
    return rows


def stage_rows(summary: RunSummary) -> list[dict[str, Any]]:
    """Flatten ``stage_stats`` into one row per stage, steps in request order."""
    order = ["decompose", "cli overhead", "search total", "retrieval (wall)", "encode",
             "knn", "frame lookups", "verify (wall)", "resolve clip url",
             "download clip (client)", "verify clip (VLM)", "download clip (RT-VLM)",
             "decode clip (RT-VLM)", "vlm inference (RT-VLM)", "clip total (RT-VLM)",
             "other", "e2e"]
    cell = _cell_columns(summary)
    rows = []
    for label, st in summary.stage_stats.items():
        rows.append({
            **cell, "step": st["step"], "stage": label, "requests": int(st["count"]),
            "calls_per_request": _fmt(st["calls_per_request"], 2),
            "per_call_mean_sec": _fmt(st["per_call_mean"]),
            **{f"{k}_sec": _fmt(st[k]) for k in ("p50", "p95", "p99", "mean")},
            "concurrent": st["concurrent"],
            "source": "cli" if st["step"] not in ("decompose", "cli overhead", "e2e")
                      else "benchmark",
        })
    measured = summary.measured_requests or 0
    for step, st in summary.rt_vlm_stats.items():
        rows.append({
            **cell, "step": step, "stage": step, "requests": measured,
            "calls_per_request": _fmt(st["clips"] / measured, 2) if measured else "",
            "per_call_mean_sec": _fmt(st["per_clip_mean"]),
            "per_call_p50_sec": _fmt(st["per_clip_p50"]),
            "per_call_p95_sec": _fmt(st["per_clip_p95"]),
            "mean_sec": _fmt(st["sum"] / measured) if measured else "",
            "concurrent": False, "source": "rt-vlm /v1/metrics",
        })
    rank = {step: i for i, step in enumerate(order)}
    return sorted(rows, key=lambda r: (rank.get(r["step"], len(order)), r["stage"]))


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
            **{f"{k}_sec": _fmt(stats.get(k))
               for k in ("p50", "p95", "p99", "mean", "min", "max", "std",
                         "e2e_p50", "e2e_p95", "e2e_p99", "e2e_mean", "decompose_mean")},
        }
        for qid, stats in sorted(summary.per_query_stats.items())
    ])
    _append_csv_rows(output_dir / "resources_summary.csv", RESOURCE_FIELDS,
                     resource_rows(summary))
    _append_csv_rows(output_dir / "stage_summary.csv", STAGE_FIELDS,
                     stage_rows(summary))

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


def e2e_timeout(config: dict[str, Any]) -> float | None:
    """``execution.e2e_timeout_sec``: the cap on one whole request, or None."""
    value = (config.get("execution", {}) or {}).get("e2e_timeout_sec", 360)
    return float(value) if value else None


def build_router(
    config: dict[str, Any],
    query_set: list[QueryItem],
    repo_root: Path,
    video_sources: list[str] | None = None,
) -> QueryRouter:
    """The router every request of the sweep decomposes through.

    Connects to the LLM only when some query is not pinned, so a tag-only
    sweep needs no ``services.llm_url``.
    """
    decompose_config = config.get("decomposition", {}) or {}
    pinned = read_pin_paths(config, query_set)
    router = QueryRouter(
        video_sources=video_sources,
        source_type=config.get("source_type", "video_file"),
        pinned=pinned,
    )
    if any(q.id not in pinned for q in query_set):
        from .decompose import require_llm_url

        llm_url = require_llm_url(config.get("services", {}) or {})
        router.decomposer = LiveDecomposer(
            llm_url=llm_url,
            repo_root=repo_root,
            model=decompose_config.get("model") or None,
            timeout_sec=float(
                decompose_config.get("request_timeout_sec")
                or decompose_config.get("timeout_sec")
                or 60
            ),
        )
        router.decomposer.probe()
        say(f"  LLM: {llm_url} serves {router.decomposer.model}; every measured "
            "request decomposes against it")
    return router


def route_check(
    config: dict[str, Any],
    query_set: list[QueryItem],
    output_dir: Path,
    router: QueryRouter,
    reuse: bool = False,
) -> dict[str, QueryPlan]:
    """Decompose each query ``route_check_calls`` times; refuse an unstable one.

    Every measured request decomposes again, so this does not fix the path a
    request runs. It checks that each query is stable enough to be measured
    as the path it targets: a single call that routes elsewhere stops the
    sweep before anything is measured, rather than leaving a cell to be
    marked invalid afterwards. The target is ``expected_path`` when set, else
    what the first call produced.

    A resumed sweep (``reuse=True``) re-runs the check -- it is a few dozen
    LLM calls -- and refuses a query whose text or target path changed since
    ``query_plans.json`` was written: its finished runs measured something
    else.
    """
    decompose_config = config.get("decomposition", {}) or {}
    calls = max(1, int(decompose_config.get("route_check_calls", 10)))
    previous = _load_query_plans(output_dir / "query_plans.json") if reuse else {}

    plans: dict[str, QueryPlan] = {}
    for item in query_set:
        old = previous.get(item.id)
        if old is not None and old.query_text != item.query:
            raise DecompositionError(
                f"{item.id} changed since this sweep started "
                f"({old.query_text!r} -> {item.query!r}); its finished runs "
                "measured the old text. Resume into a new output directory."
            )
        plan = QueryPlan(
            query_id=item.id,
            query_text=item.query,
            expected_path=item.expected_path,
        )
        plans[item.id] = plan
        if item.id in router.pinned:
            # The only way to reach `tag`: nothing in the decomposition
            # contract asks for keyword retrieval over VLM tag documents, so a
            # tag run has to be requested explicitly.
            plan.path = router.pinned[item.id]
            plan.plan = router.plan(item.id, item.query)
            plan.routed = False
            plan.status = "pinned"
            say(f"  -- {item.id}: {plan.path} (pinned, not routed)")
            continue

        assert router.decomposer is not None
        try:
            for _ in range(calls):
                decomposition, elapsed = router.decomposer.decompose(
                    item.query, router.video_sources
                )
                routed = plan_for(item.query, decomposition, router.source_type)
                plan.route_check_paths.append(routed["path"])
                if not plan.plan:
                    plan.plan, plan.decomposition, plan.decompose_sec = (
                        routed, decomposition, elapsed
                    )
        except (DecompositionError, ValueError) as e:
            plan.status, plan.error = "failed", str(e)
            say(f"  !! {item.id}: {e}")
            continue

        target = item.expected_path or plan.route_check_paths[0]
        plan.path = target
        misses = [p for p in plan.route_check_paths if p != target]
        if misses:
            counts = {p: plan.route_check_paths.count(p)
                      for p in sorted(set(plan.route_check_paths))}
            plan.status = "failed"
            plan.error = (f"{len(misses)}/{calls} call(s) missed {target}: "
                          + ", ".join(f"{p}={n}" for p, n in counts.items()))
            say(f"  !! {item.id}: {plan.error}")
            continue
        if old is not None and old.path != target:
            raise DecompositionError(
                f"{item.id} targeted {old.path} when this sweep started and now "
                f"routes to {target}; its finished runs measured another path. "
                "Resume into a new output directory."
            )
        plan.status = "ok"
        say(f"  OK {item.id}: {target} on {calls}/{calls} call(s), "
            f"first in {plan.decompose_sec:.1f}s")

    payload = {
        "resolved_at": utc_now_iso(),
        "decomposer": (router.decomposer.describe()
                       if router.decomposer is not None else None),
        "route_check_calls": calls,
        "distribution": path_distribution(
            {k: v.plan for k, v in plans.items() if v.status != "failed"}
        ),
        "plans": [asdict(p) for p in plans.values()],
    }
    (output_dir / "query_plans.json").write_text(
        json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8"
    )

    failed = [p for p in plans.values() if p.status == "failed"]
    if failed:
        raise DecompositionError(
            f"route check failed for {len(failed)} of {len(query_set)} queries: "
            + "; ".join(f"{p.query_id} ({p.error[:120]})" for p in failed)
            + "\n  A query that does not route to its path on every call would "
            "mix pipelines into its cell. Fix expected_path, reword the query, "
            "or check the LLM (details in query_plans.json)."
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
    *,
    router: QueryRouter,
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
    It decomposes first, like every measured request, so a down LLM fails it
    too.

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
        router=router,
        e2e_timeout_sec=timeout_sec,
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
