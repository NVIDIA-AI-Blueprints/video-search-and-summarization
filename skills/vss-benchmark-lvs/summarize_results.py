#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Summarize LVS benchmark results as a compact, SSH-friendly table.

Reads ``execution_summary.json`` — the benchmark harness's stable per-scenario
summary — and prints the key latency / throughput / GPU numbers. Use this
instead of opening the XLSX report when working over SSH.

Stdlib only: runs with a bare ``python3`` (no venv needed).

Usage:
    summarize_results.py <output_dir | scenario_dir> [--json]

    <output_dir>     a benchmark output directory (contains one subdir per
                     scenario), or a single scenario directory.
    --json           emit a flattened machine-readable summary instead of the
                     text table (handy for automation / diffing runs).

Examples:
    summarize_results.py vss-perf-report-warehouse
    summarize_results.py vss-perf-report-warehouse/single_file_test --json
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import statistics
import sys
from urllib.parse import urlparse


# --------------------------------------------------------------------------- #
# Discovery
# --------------------------------------------------------------------------- #
def find_summaries(path: str) -> list[str]:
    """Return execution_summary.json paths under an output dir or scenario dir."""
    if os.path.isfile(path):
        return [path]
    hits = set(glob.glob(os.path.join(path, "execution_summary.json")))
    hits |= set(glob.glob(os.path.join(path, "*", "execution_summary.json")))
    return sorted(hits)


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #
def _video_name(tc: dict) -> str:
    base = os.path.basename(urlparse(tc.get("video_url", "")).path)
    return os.path.splitext(base)[0] or tc.get("test_case_id", "?")


def _agg(values) -> dict | None:
    """Mean/min/max over the non-None values; None if nothing usable."""
    vals = [v for v in values if isinstance(v, (int, float))]
    if not vals:
        return None
    return {"mean": statistics.mean(vals), "min": min(vals), "max": max(vals), "n": len(vals)}


def _num(x, fmt="{:.2f}") -> str:
    return fmt.format(x) if isinstance(x, (int, float)) else "-"


def _e2e_cell(a: dict | None) -> str:
    """'15.99' for a single iteration, '16.0 [15.4-16.6]' when n>1."""
    if not a:
        return "-"
    if a["n"] > 1:
        return f"{a['mean']:.2f} [{a['min']:.2f}-{a['max']:.2f}]"
    return f"{a['mean']:.2f}"


def _table(headers: list[str], rows: list[list[str]]) -> str:
    widths = [len(h) for h in headers]
    for r in rows:
        for i, c in enumerate(r):
            widths[i] = max(widths[i], len(c))
    fmt = "  ".join("{:<%d}" % w for w in widths)
    out = [fmt.format(*headers), fmt.format(*["-" * w for w in widths])]
    out += [fmt.format(*r) for r in rows]
    return "\n".join(out)


# --------------------------------------------------------------------------- #
# Per-mode extraction (returns plain dicts so --json reuses them)
# --------------------------------------------------------------------------- #
def extract_single_file(data: dict) -> list[dict]:
    rows = []
    for tc in data.get("test_cases", []):
        iters = [it for it in tc.get("iteration_results", []) if it.get("success")]
        base = {"video": _video_name(tc), "chunk_size": tc.get("chunk_size"),
                "iterations": len(iters)}
        if not iters:
            rows.append({**base, "status": "FAILED"})
            continue

        def _vlm_ran(it):
            # An iteration that ran the full pipeline has a non-zero VLM-stage span;
            # an iteration that reused cached captions skipped VLM (span == 0). Only
            # full-pipeline iterations carry valid per-stage latency and VLM GPU usage.
            v = it.get("api_metrics", {}).get("vlm_latency_seconds_latest")
            return isinstance(v, (int, float)) and v > 0

        cold = [it for it in iters if _vlm_ran(it)]
        warm = [it for it in iters if not _vlm_ran(it)]
        # Report from full-pipeline iterations only; fall back to all if none qualify.
        src = cold or iters

        def api(k):
            # Per-run `_latest` gauges (most recent iteration), aggregated across the
            # cold iterations. The cumulative `_sum`/`_count` histogram counters are
            # intentionally NOT used — they accumulate across all requests/iterations.
            return _agg([it.get("api_metrics", {}).get(k) for it in src])

        def gpu(k, agg):
            vals = [it.get("gpu_metrics", {}).get(k) for it in src]
            vals = [v for v in vals if isinstance(v, (int, float))]
            return agg(vals) if vals else None

        mean = lambda xs: sum(xs) / len(xs)
        rows.append({
            **base,
            "status": "ok",
            "cold_iterations": len(cold),
            "warm_iterations": len(warm),
            "e2e_s": api("e2e_latency_seconds_latest"),
            "ca_rag_s": api("ca_rag_latency_seconds_latest"),
            # VLM-stage wall-clock SPAN (first-chunk start -> last-chunk end), NOT
            # per-chunk; chunks run concurrently so this sits well below the summed
            # per-chunk VLM time.
            "vlm_s": api("vlm_latency_seconds_latest"),
            "decode_s": api("decode_latency_seconds_latest"),
            # GPU util aggregated across cold iterations: mean of per-iter means, peak
            # of p90s (so one noisy iteration can't blank the column).
            "vlm_gpu_mean": gpu("vlm_gpu_usage_mean", mean),
            "vlm_gpu_p90": gpu("vlm_gpu_usage_p90", max),
            "llm_gpu_mean": gpu("llm_gpu_usage_mean", mean),
            "llm_gpu_p90": gpu("llm_gpu_usage_p90", max),
            "events_detected": src[-1].get("events_detected"),
        })
    return rows


def extract_file_burst(data: dict) -> list[dict]:
    out = []
    for tc in data.get("test_cases", []):
        opt = tc.get("optimal_target_concurrency") or {}
        levels = sorted(tc.get("concurrency_results", []),
                        key=lambda x: x.get("concurrency_level", 0))
        out.append({
            "video": _video_name(tc),
            "target_latency_s": tc.get("target_latency_seconds"),
            "optimal_concurrency": opt.get("estimated_concurrency"),
            "levels": [{
                "concurrency": l.get("concurrency_level"),
                "throughput_files_per_s": l.get("throughput_files_per_second"),
                "p90_latency_s": l.get("p90_latency"),
                "avg_latency_s": l.get("avg_latency"),
                "failed_files": l.get("failed_files"),
            } for l in levels],
        })
    return out


# --------------------------------------------------------------------------- #
# Text rendering
# --------------------------------------------------------------------------- #
def render_single_file(rows: list[dict]) -> str:
    # Latency columns are per-run `_latest` gauges from COLD (full-pipeline)
    # iterations only — mean across them, with [min-max] when more than one.
    # ITERS is the cold iteration count. "VLM s" is the VLM-stage wall-clock span
    # (first-chunk start -> last-chunk end), NOT per-chunk.
    headers = ["VIDEO", "CHUNK", "ITERS", "E2E s", "CA-RAG s", "VLM s", "DECODE s",
               "VLM GPU% mn/p90", "LLM GPU% mn/p90", "EVENTS"]
    table_rows = []
    for r in rows:
        if r.get("status") == "FAILED":
            table_rows.append([r["video"], _num(r["chunk_size"], "{}"), "0",
                               "FAILED", "-", "-", "-", "-", "-", "-"])
            continue
        table_rows.append([
            r["video"], _num(r["chunk_size"], "{}"),
            str(r.get("cold_iterations", r["iterations"])),
            _e2e_cell(r["e2e_s"]),
            _e2e_cell(r["ca_rag_s"]),
            _e2e_cell(r["vlm_s"]),
            _e2e_cell(r["decode_s"]),
            f"{_num(r['vlm_gpu_mean'], '{:.0f}')}/{_num(r['vlm_gpu_p90'], '{:.0f}')}",
            f"{_num(r['llm_gpu_mean'], '{:.0f}')}/{_num(r['llm_gpu_p90'], '{:.0f}')}",
            _num(r["events_detected"], "{}"),
        ])
    return _table(headers, table_rows)


def render_file_burst(items: list[dict]) -> str:
    blocks = []
    for it in items:
        head = (f"{it['video']}   target_latency={_num(it['target_latency_s'], '{:.0f}')}s"
                f"   optimal_concurrency={_num(it['optimal_concurrency'], '{}')}")
        headers = ["CONCURRENCY", "THROUGHPUT files/s", "P90 LAT s", "AVG LAT s", "FAILED"]
        rows = [[
            _num(l["concurrency"], "{}"),
            _num(l["throughput_files_per_s"], "{:.3f}"),
            _num(l["p90_latency_s"]),
            _num(l["avg_latency_s"]),
            _num(l["failed_files"], "{}"),
        ] for l in it["levels"]]
        blocks.append(head + "\n" + _table(headers, rows))
    return "\n\n".join(blocks)


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def summarize(path: str) -> list[dict]:
    summaries = find_summaries(path)
    if not summaries:
        sys.exit(f"ERROR: no execution_summary.json found under '{path}'. "
                 "Pass a benchmark output dir or scenario dir.")
    results = []
    for sfile in summaries:
        with open(sfile) as fh:
            data = json.load(fh)
        mode = data.get("benchmark_mode", "?")
        entry = {
            "scenario": data.get("scenario_name", "?"),
            "mode": mode,
            "total": data.get("total_test_cases"),
            "passed": data.get("successful_test_cases"),
            "failed": data.get("failed_test_cases"),
            "source": sfile,
        }
        if mode == "single_file":
            entry["test_cases"] = extract_single_file(data)
        elif mode == "file_burst":
            entry["test_cases"] = extract_file_burst(data)
        else:
            entry["test_cases"] = []
            entry["note"] = f"unrecognized benchmark_mode '{mode}'"
        results.append(entry)
    return results


def main() -> None:
    ap = argparse.ArgumentParser(description="Summarize LVS benchmark results.")
    ap.add_argument("path", help="benchmark output dir or scenario dir")
    ap.add_argument("--json", action="store_true", help="emit JSON instead of a table")
    args = ap.parse_args()

    results = summarize(args.path)

    if args.json:
        print(json.dumps(results, indent=2))
        return

    for entry in results:
        print("=" * 88)
        print(f"Scenario: {entry['scenario']}   mode: {entry['mode']}   "
              f"({entry['passed']}/{entry['total']} test cases passed)")
        print("=" * 88)
        if entry.get("note"):
            print(f"  {entry['note']}")
        elif entry["mode"] == "single_file":
            print(render_single_file(entry["test_cases"]))
        elif entry["mode"] == "file_burst":
            print(render_file_burst(entry["test_cases"]))
        print()


if __name__ == "__main__":
    main()
