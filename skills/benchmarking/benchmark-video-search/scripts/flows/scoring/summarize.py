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

"""Summary aggregation + console report for the segment-overlap scorer.

Moved verbatim out of ``flows.pipeline`` so the scoring backends own their
metric aggregation and the pipeline module only orchestrates stages. Key names
deliberately match ``run_eval.run_evaluation``'s summary block so baselines from
either script line up field for field.
"""

from __future__ import annotations

import collections
import statistics
from datetime import UTC, datetime
from typing import Any

import flows

#: Stages whose own time is below this are wrappers or noise; the console hides
#: them so the table reads as "where did the time go" without a nesting caveat.
_STAGE_FLOOR_S = 0.001


def _summarize(
    results: list[dict[str, Any]],
    dataset: str,
    subset: str,
    wall_clock_s: float,
    concurrency: int,
    sources_seen: set[str],
    upload_stats: dict[str, Any] | None,
    verdicts: dict[str, int] | None = None,
    path_counts: dict[str, int] | None = None,
    unanswered: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Aggregate per-query metrics."""
    n = len(results)
    unanswered = unanswered or []

    def avg(key):
        return sum(r[key] for r in results) / n

    def avg_hit(k):
        return sum(r["hit_at_k"][k] for r in results) / n

    summary: dict[str, Any] = {
        "dataset": dataset,
        "subset": subset or "default",
        # `total_queries` is the SCORED count, matching run_eval.py's field so
        # baselines line up. When they differ, `queries_attempted` is what a
        # reader needs to know the metrics cover less than the dataset.
        "total_queries": n,
        "queries_attempted": n + len(unanswered),
        "mAP": round(avg("average_precision"), 4),
        "MRR": round(avg("reciprocal_rank"), 4),
        "avg_precision": round(avg("precision"), 4),
        "avg_recall": round(avg("recall"), 4),
        "avg_f1": round(avg("f1"), 4),
    }
    for k in flows.HIT_K_VALUES:
        summary[f"HIT@{k}"] = round(avg_hit(k), 4)

    # Absent, not zero, when every query was answered -- so its presence in a
    # result file is itself the signal that the metrics above are partial.
    if unanswered:
        summary["unanswered"] = {
            "count": len(unanswered),
            "reasons": dict(
                collections.Counter(e["reason"].split(":")[0] for e in unanswered)
            ),
            "queries": unanswered[:50],
        }

    # Only report critic-filtered metrics when a verification block was
    # actually present. Emitting them off an unverified response would publish
    # unfiltered numbers under a filtered label -- see flows.py for why that
    # failure mode is the one worth engineering against.
    # A verification block on every hit does not mean the critic formed an
    # opinion. Every critic failure path -- unreachable VLM, a 4xx on a clip URL
    # the VLM cannot resolve, a parse error -- degrades that candidate to
    # `unverified` and carries on, so a totally broken critic still attaches a
    # block to everything. Filtering then drops nothing and critic-filtered
    # metrics come out numerically identical to raw, which reads as "the critic
    # agreed with retrieval" when it never rendered an opinion at all.
    #
    # A VST-returned clip URL that RT-VLM cannot fetch can leave every hit
    # unverified while the CLI search itself exits 0. The CLI's base URL is
    # not necessarily the clip URL: video_url_scope is "internal".
    real_sources = sources_seen - {flows.VERIFICATION_ABSENT}
    verdicts = verdicts or {}
    opinions = verdicts.get("confirmed", 0) + verdicts.get("rejected", 0)
    if real_sources and not opinions:
        summary["critic"] = {
            "verdicts": dict(verdicts),
            "status": "no_opinion",
            "detail": (
                "Verification blocks were present but every verdict was 'unverified': "
                "the critic ran and formed no opinion. Critic-filtered metrics are "
                "suppressed because they would equal raw. Common cause: the clip URL "
                "handed to RT-VLM is not reachable from its container -- inspect "
                "the actual VST-returned videoUrl and RT-VLM connectivity."
            ),
        }
        real_sources = set()
    elif real_sources:
        summary["critic"] = {"verdicts": dict(verdicts), "status": "ok"}
    if real_sources:

        def favg(key):
            return sum(r["critic_filtered"][key] for r in results) / n

        def favg_hit(k):
            return sum(r["critic_filtered"]["hit_at_k"][k] for r in results) / n

        critic_filtered: dict[str, Any] = {
            "mAP": round(favg("average_precision"), 4),
            "MRR": round(favg("reciprocal_rank"), 4),
            "avg_precision": round(favg("precision"), 4),
            "avg_recall": round(favg("recall"), 4),
            "avg_f1": round(favg("f1"), 4),
        }
        for k in flows.HIT_K_VALUES:
            critic_filtered[f"HIT@{k}"] = round(favg_hit(k), 4)
        summary["critic_filtered"] = critic_filtered
        summary["total_rejected"] = sum(r.get("num_rejected", 0) for r in results)
        summary["queries_with_rejections"] = sum(
            1 for r in results if r.get("num_rejected", 0) > 0
        )

    # A routed run mixes retrieval paths, so the headline metric averages over
    # different mechanisms. The split has to sit next to it or the number is
    # uninterpretable.
    if path_counts:
        summary["search_paths"] = path_counts

    summary["verification"] = {
        "field_sources": sorted(sources_seen),
        "available": bool(real_sources),
    }

    latencies = [r["latency_s"] for r in results if r.get("latency_s") is not None]
    if latencies:
        sorted_lat = sorted(latencies)
        summary["latency"] = {
            "mean_s": round(statistics.mean(sorted_lat), 3),
            "median_s": round(statistics.median(sorted_lat), 3),
            "min_s": round(min(sorted_lat), 3),
            "max_s": round(max(sorted_lat), 3),
            "p90_s": round(
                sorted_lat[min(int(len(sorted_lat) * 0.9), len(sorted_lat) - 1)], 3
            ),
            "p95_s": round(
                sorted_lat[min(int(len(sorted_lat) * 0.95), len(sorted_lat) - 1)], 3
            ),
            "std_dev_s": round(statistics.stdev(sorted_lat), 3)
            if len(sorted_lat) > 1
            else 0.0,
        }

    # Per-stage breakdown, when the deployment reports it. This is the CLI-path
    # replacement for the Phoenix span breakdown, which does not exist here
    # because search_core emits no OTel spans.
    stage_rows: dict[str, dict[str, list[float]]] = {}
    reported = 0
    for r in results:
        stages = (r.get("timings") or {}).get("stages") or {}
        if stages:
            reported += 1
        for label, entry in stages.items():
            row = stage_rows.setdefault(
                label, {"total": [], "self": [], "calls": [], "conc": []}
            )
            row["total"].append(float(entry.get("total_s", 0.0)))
            row["self"].append(float(entry.get("self_s", 0.0)))
            row["calls"].append(float(entry.get("calls", 0.0)))
            row["conc"].append(float(entry.get("concurrent_children", 0.0)))

    if stage_rows:
        # Ranked by SELF time: that is where the work actually happened. Ranking
        # by inclusive total puts wrappers on top, which says nothing.
        summary["stage_latency"] = {
            "queries_reporting": reported,
            "stages": {
                label: {
                    "self_total_s": round(sum(row["self"]), 4),
                    "self_mean_s": round(statistics.mean(row["self"]), 4),
                    "inclusive_total_s": round(sum(row["total"]), 4),
                    "inclusive_mean_s": round(statistics.mean(row["total"]), 4),
                    "queries": len(row["total"]),
                    "calls": int(sum(row["calls"])),
                    "concurrent_children": bool(any(row["conc"])),
                }
                for label, row in sorted(
                    stage_rows.items(), key=lambda kv: -sum(kv[1]["self"])
                )
            },
        }

    # What the query cost outside search() itself: CLI process launch, imports
    # and the ~/.vss/config.json read. Only derivable when the deployment
    # reports timings, since it is latency minus the search's own total.
    overheads = [
        r["latency_s"] - (r.get("timings") or {}).get("total_s", 0.0)
        for r in results
        if r.get("latency_s") is not None and (r.get("timings") or {}).get("total_s")
    ]
    if overheads and "latency" in summary:
        summary["latency"]["cli_startup_mean_s"] = round(statistics.mean(overheads), 3)
        summary["latency"]["search_internal_mean_s"] = round(
            statistics.mean(
                [
                    (r.get("timings") or {}).get("total_s", 0.0)
                    for r in results
                    if (r.get("timings") or {}).get("total_s")
                ]
            ),
            3,
        )

    decompose_times = [
        r["decompose_s"] for r in results if r.get("decompose_s") is not None
    ]
    if decompose_times:
        # Reported next to, not inside, the search latency: decomposition is an
        # LLM call the CLI never makes, so folding it into latency_s would make
        # runs with and without it incomparable.
        summary["decomposition"] = {
            "queries": len(decompose_times),
            "mean_s": round(statistics.mean(decompose_times), 3),
            "median_s": round(statistics.median(decompose_times), 3),
            "max_s": round(max(decompose_times), 3),
            "total_s": round(sum(decompose_times), 3),
            "share_of_query_pct": round(
                100
                * sum(decompose_times)
                / max(
                    sum(
                        r["latency_s"] + r["decompose_s"]
                        for r in results
                        if r.get("decompose_s") is not None
                    ),
                    1e-9,
                ),
                1,
            ),
            "routed_to": flows.path_distribution(
                [
                    flows.plan_for(r["query"], r.get("decomposition"))
                    for r in results
                    if r.get("decomposition")
                ]
            ),
        }

    summary["throughput"] = {
        "wall_clock_s": round(wall_clock_s, 3),
        "qps": round(n / wall_clock_s, 3) if wall_clock_s > 0 else 0.0,
        "successful_qps": round(len(latencies) / wall_clock_s, 3)
        if wall_clock_s > 0
        else 0.0,
        "concurrency": concurrency,
    }

    if upload_stats and upload_stats.get("total_uploads"):
        summary["upload"] = upload_stats

    summary["timestamp"] = datetime.now(UTC).isoformat()
    return summary


def _print_summary(summary: dict[str, Any]) -> None:
    print(f"{'=' * 60}")
    print("RESULTS SUMMARY")
    print(f"{'=' * 60}")
    print(f"Dataset:            {summary['dataset']} / {summary['subset']}")
    print(f"Queries:            {summary['total_queries']}")

    if summary.get("search_paths"):
        split = "  ".join(f"{k}={v}" for k, v in summary["search_paths"].items())
        print(f"Search paths:       {split}")

    cf = summary.get("critic_filtered")
    if cf:
        print(
            f"Rejections:         {summary['total_rejected']} across "
            f"{summary['queries_with_rejections']} queries"
        )
    else:
        print(
            "Rejections:         n/a -- no verification block in the response "
            "(critic-filtered metrics suppressed)"
        )

    def _cf(key: str) -> str:
        return f"{cf[key]:.4f}" if cf else "NA"

    print()
    print(f"{'':<20}{'Raw':<12}Critic Filtered")
    for label, key in (
        ("mAP", "mAP"),
        ("MRR", "MRR"),
        ("Avg Precision", "avg_precision"),
        ("Avg Recall", "avg_recall"),
        ("Avg F1", "avg_f1"),
    ):
        print(f"{label + ':':<20}{summary[key]:<12.4f}{_cf(key)}")
    for k in flows.HIT_K_VALUES:
        print(f"HIT@{k}:".ljust(20) + f"{summary[f'HIT@{k}']:<12.4f}{_cf(f'HIT@{k}')}")

    if "latency" in summary:
        lat = summary["latency"]
        print("\nLatency (client-observed):")
        for label, key in (
            ("Mean", "mean_s"),
            ("Median", "median_s"),
            ("Min", "min_s"),
            ("Max", "max_s"),
            ("P90", "p90_s"),
            ("P95", "p95_s"),
            ("Std Dev", "std_dev_s"),
        ):
            print(f"  {label + ':':<18}{lat[key]:.3f}s")
        if "cli_startup_mean_s" in lat:
            print(f"  {'  of which:':<18}")
            print(f"  {'    search:':<18}{lat['search_internal_mean_s']:.3f}s")
            print(f"  {'    CLI startup:':<18}{lat['cli_startup_mean_s']:.3f}s")

    crit = summary.get("critic")
    if crit and crit.get("status") == "no_opinion":
        print(f"\n!! CRITIC RENDERED NO OPINION  {crit['verdicts']}")
        for line in crit["detail"].split(". "):
            if line.strip():
                print(f"   {line.strip().rstrip('.')}.")

    dec = summary.get("decomposition")
    if dec:
        print(f"\nQuery decomposition ({dec['queries']} queries, live)")
        print(f"  {'Mean:':<18}{dec['mean_s']:.3f}s")
        print(f"  {'Median:':<18}{dec['median_s']:.3f}s")
        print(f"  {'Max:':<18}{dec['max_s']:.3f}s")
        print(f"  {'Share of query:':<18}{dec['share_of_query_pct']:.1f}%")
        print(f"  {'Routed to:':<18}{dec['routed_to']}")

    stage = summary.get("stage_latency")
    if stage:
        # One number per stage: time spent IN it, children excluded. Wrappers
        # like "search: embed search" contribute nothing of their own, so they
        # are hidden rather than shown at ~0 next to real work. Full detail --
        # inclusive totals, call counts, overlap flags -- stays in the JSON.
        # Filter on the MEAN, not the total: a total grows with query count, so
        # a wrapper contributing microseconds per query would reappear on a
        # large dataset.
        rows = [
            (k, v)
            for k, v in stage["stages"].items()
            if v["self_mean_s"] >= _STAGE_FLOOR_S
        ]

        total_queries = stage["queries_reporting"]
        # `queries` is a column rather than a parenthetical: a stage that runs
        # only on fusion queries has its mean taken over those, and the count
        # is what stops a 5.9s mean over 3 reading like a 7.8s mean over 9.
        width = max((len(label) for label, _ in rows), default=20)
        print(f"\nStage latency ({total_queries} queries reported)")
        print(f"  {'stage':<{width}}  {'mean':>10}  {'total':>10}  {'queries':>7}")
        print(f"  {'-' * width}  {'-' * 10}  {'-' * 10}  {'-' * 7}")
        for label, v in rows:
            print(
                f"  {label:<{width}}  {v['self_mean_s']:9.3f}s  "
                f"{v['self_total_s']:9.3f}s  {v['queries']:>7}"
            )

    tp = summary["throughput"]
    print("\nThroughput:")
    print(f"  Wall clock:       {tp['wall_clock_s']:.3f}s")
    print(f"  QPS:              {tp['qps']:.3f}")
    print(f"  Successful QPS:   {tp['successful_qps']:.3f}  (excludes failed queries)")
    print(f"  Concurrency:      {tp['concurrency']}")

    if summary.get("upload"):
        flows.print_upload_summary(summary["upload"])
    print(f"{'=' * 60}\n")
