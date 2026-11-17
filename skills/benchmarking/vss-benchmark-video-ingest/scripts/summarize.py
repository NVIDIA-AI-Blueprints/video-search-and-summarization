#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Turn the normalized CSVs into ``summary.json`` and ``summary.md``.

Summaries are generated from ``csv/`` and ``run-metadata.json``, never from ad
hoc parsing of raw logs, so a summary can always be regenerated from a finished
run without re-uploading anything.

Two warnings are mandatory in every summary:

1. The client uplink may limit ingestion. Average acknowledged payload rate
   cannot establish or exclude that bottleneck without wire measurements.
2. The endpoint may refuse the requested concurrency. This benchmark does not change
   the deployed stream cap. Above that cap,
   surplus uploads are rejected or never complete.

Never claim an internal bottleneck -- detection, embedding, media store, index,
queue, GPU, or worker. When a point degrades, report the status codes, the
timeout and unconfirmed counts, and the transfer rate.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
import sys
from typing import Any

#: Flag a high average payload rate without attributing the limiting resource.
UPLINK_WARNING_RATIO = 0.80


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return default


def as_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return default


#: What each artifact in a results directory is. Rendered verbatim into
#: summary.md so a reader never has to guess what a file holds or which files
#: are safe to hand to someone else.
OUTPUT_FILES: list[dict[str, str]] = [
    {
        "path": "csv/ingest_corpus.csv",
        "holds": "One row per source video, as ffprobe measured it",
        "why": "The video-minute denominator of every throughput number. Check here "
        "first when a throughput figure looks wrong -- a mis-measured clip moves "
        "every metric in the run.",
    },
    {
        "path": "csv/ingest_requests.csv",
        "holds": "One row per upload: timestamps, CLI exit, outcome, latency, "
        "bytes, and the raw/embed document counts the readiness check saw",
        "why": "The per-upload evidence. `outcome` and `phase` distinguish an upload "
        "that failed from one that uploaded fine and never confirmed.",
    },
    {
        "path": "csv/ingest_summary.csv",
        "holds": "One row per video class and concurrency: confirmed video-minutes, "
        "client-observed throughput, latency percentiles and result validity",
        "why": "The results table. `video_min_per_sec`, `p50/p95/max_latency_sec`, and "
        "`result_valid` are the three things to read.",
    },
    {
        "path": "csv/ingest_errors.csv",
        "holds": "Error taxonomy: counts by video class, concurrency, phase, CLI "
        "status, and outcome",
        "why": "Where a point degraded and at which phase. Empty file means every "
        "upload confirmed.",
    },
    {
        "path": "raw/*.jsonl",
        "holds": "The unaggregated per-upload records the CSVs were built from",
        "why": "Re-derive any metric without re-running the sweep.",
    },
    {
        "path": "run-metadata.json",
        "holds": "Every input: endpoints, sweep matrix, corpus, timeouts, readiness "
        "tuning, cleanup policy, projected bytes, and which "
        "config file supplied what",
        "why": "Makes the run reproducible from the artifact alone. Carries no token.",
    },
    {
        "path": "visualizations/summary.md",
        "holds": "This file: inputs, method, results table, and warnings",
        "why": "The human-readable report.",
    },
    {
        "path": "visualizations/summary.json",
        "holds": "The same summary as machine-readable JSON",
        "why": "For diffing runs or feeding a dashboard.",
    },
    {
        "path": "visualizations/ingest-throughput-vs-concurrency.png",
        "holds": "Throughput (video-min/s) against concurrency, one line per class",
        "why": "The capacity curve: where throughput stops scaling with concurrency.",
    },
    {
        "path": "visualizations/ingest-latency-p95-by-concurrency.png",
        "holds": "p95 upload latency against concurrency",
        "why": "What one upload costs under load. p50 and max are in the CSVs.",
    },
    {
        "path": "visualizations/ingest-outcome-by-concurrency.png",
        "holds": "Confirmed / unconfirmed / failed / timed-out counts per sweep point",
        "why": "Whether a point is trustworthy before its throughput is quoted.",
    },
]


def build_inputs(metadata: dict[str, Any], corpus_rows: list[dict[str, str]]) -> list[dict[str, str]]:
    """The inputs that define this run, as (name, value, note) rows.

    Everything here comes from run-metadata.json, so the summary states what
    was actually run rather than what the operator meant to run. No secret is
    included: the token is reported as present or absent only.
    """
    classes = metadata.get("video_classes") or []
    concurrencies = metadata.get("concurrencies") or []
    user_videos = metadata.get("user_videos") or []
    user_class = metadata.get("user_video_class") or ""
    es = metadata.get("es_readiness") or {}
    total_bytes = sum(as_float(r.get("bytes")) for r in corpus_rows)
    total_minutes = sum(as_float(r.get("duration_sec")) for r in corpus_rows) / 60.0

    rows: list[dict[str, str]] = [
        {
            "name": "Profile",
            "value": f"`{metadata.get('profile', '')}`",
            "note": "Selects the default class and concurrency lists; anything typed on "
            "the command line overrides it.",
        },
        {
            "name": "Video class(es)",
            "value": ", ".join(f"`{c}`" for c in classes) or "-",
            "note": "One sweep series per class.",
        },
        {
            "name": "Video source",
            "value": (
                "user-provided via `--video`: " + ", ".join(f"`{v}`" for v in user_videos)
                if user_videos
                else f"corpus `{metadata.get('corpus_root', '')}`"
            ),
            "note": (
                f"Measured as class `{user_class}`. These are the operator's own files, "
                "not one of the predefined size classes."
                if user_videos
                else "Each class is the folder of that name under the corpus root."
            ),
        },
        {
            "name": "Corpus measured",
            "value": f"{len(corpus_rows)} file(s), {total_bytes / 1_000_000_000:.3f} GB, "
            f"{total_minutes:.2f} video-minutes",
            "note": "ffprobe-measured. This is the denominator of every throughput number.",
        },
        {
            "name": "Concurrencies",
            "value": ", ".join(str(c) for c in concurrencies) or "-",
            "note": "Simultaneous client workers per sweep point. These are the only "
            "x-axis points on the charts.",
        },
        {
            "name": "Uploads per point",
            "value": "concurrency x files in the class",
            "note": "Every worker uploads the whole class once, so a 2-file class doubles "
            "the upload count.",
        },
        {
            "name": "VSS service URL",
            "value": f"`{metadata.get('vss_service_url', '')}`",
            "note": "The system under test. Nothing about it was changed by this run.",
        },
        {
            "name": "Upload route",
            "value": f"`{metadata.get('upload_route', '')}`",
            "note": "One CLI process per upload; VIOS webhooks dispatch inference.",
        },
        {
            "name": "Elasticsearch readiness",
            "value": f"`{es.get('elasticsearch_url', '') or '-'}`",
            "note": f"Raw `{es.get('raw_index', '')}`, embed `{es.get('embed_index', '')}`; "
            "both expected counts must be reached.",
        },
        {
            "name": "ES request timeout",
            "value": f"{metadata.get('es_request_timeout_sec', '')} s",
            "note": "ES reads only. Upload and VIOS timeline timeouts are owned by the CLI.",
        },
        {
            "name": "Readiness timeout / poll",
            "value": (
                f"{es.get('timeout_sec')} s"
                if es.get("timeout_sec") is not None
                else "duration/concurrency-derived"
            )
            + f" / {es.get('poll_interval_sec', '')} s",
            "note": "Polling and ES request latency delay observation of completion.",
        },
        {
            "name": "Warmup uploads",
            "value": str(metadata.get("warmup_uploads", "")),
            "note": "Sent first and discarded, so first-upload effects stay out of the results.",
        },
        {
            "name": "Corpus limit",
            "value": str(metadata.get("corpus_limit") or "none"),
            "note": "Use only the first N files of each class.",
        },
        {
            "name": "Ramp",
            "value": f"{metadata.get('stagger_sec', '')} s per worker, "
            f"capped at {metadata.get('max_ramp_sec', '')} s",
            "note": "Workers start staggered rather than all at once.",
        },
        {
            "name": "Cleanup policy",
            "value": f"`{metadata.get('cleanup_policy', '')}`",
            "note": (
                "All media retained; deletion and cleanup verification skipped."
                if metadata.get("cleanup_policy") == "never"
                else "CLI recording deletion followed by sustained raw/Embed absence in ES, outside measurement windows. "
                "Only assets selected by the cleanup policy are checked."
                if metadata.get("cleanup_verification")
                else "CLI deletion only; downstream ES removal was not verified."
            ),
        },
        {
            "name": "Projected transfer",
            "value": f"{as_float(metadata.get('projected_bytes_total')) / 1_000_000_000:.2f} GB",
            "note": f"Confirmation required above {metadata.get('transfer_ceiling_gb', '')} GB.",
        },
        {
            "name": "Auth",
            "value": "token provided" if metadata.get("auth_token_provided") else "none",
            "note": "Read from $VSS_AUTH_TOKEN. The value is never printed or stored.",
        },
        {
            "name": "Config file",
            "value": f"`{metadata.get('config_file', '') or 'none -- built-in defaults'}`",
            "note": "Supplied defaults only; every flag still won over it.",
        },
    ]
    compatibility = metadata.get("version_compatibility") or {}
    cleanup = metadata.get("cleanup_verification")
    if cleanup:
        rows.append({
            "name": "Cleanup timeout / settling",
            "value": f"{cleanup.get('timeout_sec')} s / {cleanup.get('settle_sec')} s",
            "note": "Incomplete cleanup stops subsequent points. ES absence does not prove all backend work has stopped.",
        })
    rows[0:0] = [
        {"name": "Benchmark skill version", "value": compatibility.get("skill_version", "not recorded"),
         "note": "Stamped version read from SKILL.md frontmatter before this run."},
        {"name": "Deployed VSS version", "value": compatibility.get("deployed_vss_version", "not recorded"),
         "note": "Version reported by the deployed VSS API, not the local CLI package."},
        {"name": "Version compatibility", "value": compatibility.get("status", "not recorded"),
         "note": f"Rule: {compatibility.get('requires_vss', 'not recorded')}; checked before corpus measurement and upload."},
    ]
    return rows


def build_steps(metadata: dict[str, Any]) -> list[str]:
    """The steps the runner actually performed, in order."""
    classes = ", ".join(metadata.get("video_classes") or []) or "the selected classes"
    concurrencies = ", ".join(str(c) for c in metadata.get("concurrencies") or []) or "the sweep list"
    warmup = metadata.get("warmup_uploads", 0)
    if metadata.get("cleanup_policy") == "never":
        cleanup_step = "**Retained all media** (`cleanup: never`); skipped deletion and cleanup verification"
    else:
        cleanup_step = "**Applied the cleanup policy** through `vss vios delete`"
        if metadata.get("cleanup_policy") == "on-success":
            cleanup_step += " for confirmed uploads only, retaining other uploads"
        cleanup_step += (
            "; required sustained raw/Embed absence in ES for the selected assets before continuing"
            if metadata.get("cleanup_verification") else " (downstream ES removal was not verified)"
        )
    return [
        "**Validated** the requested inputs and CLI configuration; checked VIOS through VSS CLI.",
        (
            "**Confirmed version compatibility** using the deployed VSS API and the skill's metadata rules, "
            "before measuring corpus files or uploading any video."
            if (metadata.get("version_compatibility") or {}).get("status") == "compatible"
            else "**Version compatibility evidence was not recorded** in these existing run artifacts."
        ),
        "**Measured** every source video with ffprobe and wrote `csv/ingest_corpus.csv`. "
        "Throughput is counted in video-minutes, so a guessed duration would corrupt "
        "the whole run. Projected source payload bytes for each sweep point and stopped for "
        "confirmation if the total exceeded the transfer ceiling.",
        "**Preflighted** Elasticsearch readiness before any upload.",
        (
            f"**Warmed up** with {warmup} discarded upload(s)."
            if warmup
            else "**Skipped warmup** (`--warmup 0`), so the first measured upload carries "
            "any first-upload cost."
        ),
        f"**Started the requested sweep** ({classes}) x ({concurrencies}). For each executed point: workers "
        "started on a stagger, each uploaded the whole class once under a generated "
        "filename, and each upload was then polled until Elasticsearch readiness or "
        "its timeout expired.",
        cleanup_step + "; wrote the four CSVs, the raw JSONL records, and `run-metadata.json`.",
        "**Summarized and charted** from those CSVs only -- which is why both can be "
        "re-run on a finished run without uploading anything again. The runner then checks required artifacts, "
        "version evidence, and ES-token disclosure before reporting completion.",
    ]


def build_summary(results_dir: Path, uplink_mbps: float | None) -> dict[str, Any]:
    csv_dir = results_dir / "csv"
    summary_rows = read_csv(csv_dir / "ingest_summary.csv")
    error_rows = read_csv(csv_dir / "ingest_errors.csv")
    corpus_rows = read_csv(csv_dir / "ingest_corpus.csv")

    metadata_path = results_dir / "run-metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8")) if metadata_path.exists() else {}

    total_uploads = sum(as_int(row.get("upload_count")) for row in summary_rows)
    total_confirmed = sum(as_int(row.get("success_count")) for row in summary_rows)
    peak_mb_per_sec = max((as_float(row.get("aggregate_mb_per_sec")) for row in summary_rows), default=0.0)
    peak_mbps = peak_mb_per_sec * 8

    uplink_verdict = "not declared -- pass --uplink-mbps to evaluate"
    uplink_threshold_reached = None
    if uplink_mbps:
        uplink_threshold_reached = peak_mbps >= UPLINK_WARNING_RATIO * uplink_mbps
        uplink_verdict = (
            f"peak {peak_mbps:.1f} Mb/s against a declared {uplink_mbps:.1f} Mb/s uplink -- "
            + (
                "high average payload rate is consistent with an uplink limit, but does not prove one"
                if uplink_threshold_reached
                else "lower average payload rate cannot rule out transfer bursts reaching the uplink limit"
            )
        )

    def is_valid(row) -> bool:
        # Blank means the signal could not judge validity; treat as valid.
        return str(row.get("result_valid", "")).strip().lower() != "false"

    invalid_rows = [r for r in summary_rows if not is_valid(r)]
    valid_rows = [r for r in summary_rows if is_valid(r)]
    # The headline is taken over valid points only. Throughput computed across
    # streams that failed reads as clean scaling, which is exactly how a
    # degraded run gets mistaken for a healthy one.
    best_point = max(
        (row for row in valid_rows if math.isfinite(as_float(row.get("video_min_per_sec"), math.nan))),
        key=lambda row: as_float(row.get("video_min_per_sec")),
        default=None,
    )
    degraded = [
        row
        for row in summary_rows
        if as_float(row.get("success_rate_pct"), 100.0) < 100.0
    ]

    inputs = build_inputs(metadata, corpus_rows)
    steps = build_steps(metadata)

    return {
        "run_id": metadata.get("run_id", ""),
        "version_compatibility": metadata.get("version_compatibility", {}),
        "stop_reason": metadata.get("stop_reason", ""),
        "blueprint": "vss-ingest",
        "phase": 1,
        "profile": metadata.get("profile", ""),
        "measurement_scope": (
            "All metrics are client-observed. No Kubernetes, Prometheus, GPU, pod, trace, "
            "log, or internal pipeline telemetry was collected."
        ),
        "readiness_stops_clock_at": (
            metadata.get("es_readiness", {}) or {}
        ).get("clock_stops_at", "client observation of both expected ES counts"),
        "comparability_note": metadata.get("comparability_note", ""),
        "byte_accounting": metadata.get("byte_accounting", ""),
        "upload_route": metadata.get("upload_route", ""),
        "es_request_timeout_sec": metadata.get("es_request_timeout_sec"),
        "inputs": inputs,
        "execution_steps": steps,
        "output_files": OUTPUT_FILES,
        "corpus_videos": len(corpus_rows),
        "sweep_points": len(summary_rows),
        "total_uploads": total_uploads,
        "total_confirmed": total_confirmed,
        "overall_success_rate_pct": (
            round(100.0 * total_confirmed / total_uploads, 1) if total_uploads else None
        ),
        "valid_sweep_points": len(valid_rows),
        "invalid_sweep_points": len(invalid_rows),
        "invalid_points": [
            {
                "video_class": r.get("video_class"),
                "concurrency": as_int(r.get("concurrency")),
                "reason": r.get("result_invalid_reason", ""),
                "min_raw_completion_ratio": r.get("min_raw_completion_ratio", ""),
            }
            for r in invalid_rows
        ],
        "peak_is_over_valid_points_only": True,
        "peak_video_min_per_sec": as_float(best_point.get("video_min_per_sec")) if best_point else None,
        "peak_point": (
            f"{best_point.get('video_class')} @ c{best_point.get('concurrency')}" if best_point else ""
        ),
        "peak_aggregate_mb_per_sec": round(peak_mb_per_sec, 3),
        "peak_aggregate_mbps": round(peak_mbps, 1),
        "declared_uplink_mbps": uplink_mbps,
        "uplink_threshold_reached": uplink_threshold_reached,
        "uplink_verdict": uplink_verdict,
        "degraded_points": [
            {
                "video_class": row.get("video_class"),
                "concurrency": as_int(row.get("concurrency")),
                "success_rate_pct": as_float(row.get("success_rate_pct")),
                "cli_exit_statuses": row.get("cli_exit_statuses", ""),
            }
            for row in degraded
        ],
        "error_taxonomy": [
            {
                "video_class": row.get("video_class"),
                "concurrency": as_int(row.get("concurrency")),
                "phase": row.get("phase"),
                "cli_exit_code": row.get("cli_exit_code"),
                "outcome": row.get("outcome"),
                "count": as_int(row.get("count")),
            }
            for row in error_rows
        ],
        "mandatory_warnings": [
            "The client uplink may limit ingestion. aggregate_mb_per_sec measures acknowledged "
            "source payload over the full point, not wire traffic; it cannot establish or exclude "
            "an uplink bottleneck.",
            "The endpoint may refuse the requested concurrency. This benchmark does not change "
            "the deployed stream cap. Above that cap, surplus "
            "uploads are rejected or never complete.",
        ],
        "sweep_points_detail": summary_rows,
    }


def render_markdown(summary: dict[str, Any]) -> str:
    rows = summary["sweep_points_detail"]
    lines: list[str] = []
    add = lines.append

    add("# VSS Ingest Benchmark Summary")
    add("")
    add(f"- Run: `{summary['run_id']}`")
    add(f"- Profile: `{summary['profile']}`")
    if summary.get("stop_reason"):
        add(f"- **Run stopped:** {summary['stop_reason']}")
    add(f"- Upload route: `{summary['upload_route']}`")
    add(f"- Comparability: {summary['comparability_note']}")
    add(f"- Byte accounting: {summary['byte_accounting']}")
    add(f"- Readiness: `es_readiness` -- stops the clock at {summary['readiness_stops_clock_at']}")
    add(f"- Sweep points: {summary['sweep_points']} | uploads: {summary['total_uploads']} | confirmed: {summary['total_confirmed']}")
    if summary["overall_success_rate_pct"] is not None:
        add(f"- Overall success rate: {summary['overall_success_rate_pct']}%")
    add("")

    add("## Inputs")
    add("")
    add("Every value below is read back from `run-metadata.json`, so this is what the run")
    add("actually used, not what was intended. No secret appears here or in any artifact.")
    add("")
    add("| Input | Value | What it means |")
    add("|---|---|---|")
    for row in summary["inputs"]:
        add(f"| {row['name']} | {row['value']} | {row['note']} |")
    add("")

    add("## What this run measured")
    add("")
    add(summary["measurement_scope"])
    add("")
    add(summary["comparability_note"])
    add("")

    add("## How the benchmark was run")
    add("")
    for index, step in enumerate(summary["execution_steps"], start=1):
        add(f"{index}. {step}")
    add("")

    add("## Sweep points")
    add("")
    add(
        "| valid | class | c | uploads | confirmed | success % | video-min | wall-clock min | "
        "video_min_per_sec | MB/s | p50 s | p95 s | max s | statuses |"
    )
    add("|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|")
    for row in rows:
        valid_mark = "no" if str(row.get("result_valid", "")).strip().lower() == "false" else "yes"
        add(
            f"| {valid_mark} | {row.get('video_class')} | {row.get('concurrency')} | {row.get('upload_count')} "
            f"| {row.get('success_count')} | {row.get('success_rate_pct')} "
            f"| {row.get('total_video_duration_min')} | {row.get('wall_clock_min')} "
            f"| {row.get('video_min_per_sec')} | {row.get('aggregate_mb_per_sec')} "
            f"| {row.get('p50_latency_sec')} | {row.get('p95_latency_sec')} "
            f"| {row.get('max_latency_sec')} | {row.get('cli_exit_statuses') or '-'} |"
        )
    add("")
    if summary["peak_video_min_per_sec"] is None:
        reason = (
            "no valid sweep points"
            if not summary["valid_sweep_points"]
            else "no valid sweep points with numeric throughput"
        )
        add(f"Peak client-observed throughput: **unavailable** ({reason}).")
    else:
        add(
            f"Peak client-observed throughput: **{summary['peak_video_min_per_sec']} video-min/s** "
            f"at {summary['peak_point']}"
            + (
                f" (over the {summary['valid_sweep_points']} valid point(s) only; "
                f"{summary['invalid_sweep_points']} excluded)."
                if summary["invalid_sweep_points"]
                else "."
            )
        )
    add("")
    if summary["invalid_points"]:
        add("## Invalid points — excluded from the headline")
        add("")
        add("| class | c | min raw coverage | why |")
        add("|---|---:|---:|---|")
        for pt in summary["invalid_points"]:
            add(
                f"| {pt['video_class']} | {pt['concurrency']} | {pt['min_raw_completion_ratio'] or '-'} "
                f"| {pt['reason'] or '-'} |"
            )
        add("")
        add(
            "A point is invalid when an upload failed, the slowest stream indexed "
            "below the required raw-frame coverage, or completion timestamps do not define "
            "a valid positive throughput window. Invalid points are excluded from "
            "headline throughput. Charts show numeric invalid points only as hollow "
            "markers; points with unavailable throughput have no throughput marker."
        )
        add("")

    if summary["degraded_points"]:
        add("## Points that did not fully confirm")
        add("")
        add("| class | c | success % | CLI exit histogram |")
        add("|---|---:|---:|---|")
        for point in summary["degraded_points"]:
            add(
                f"| {point['video_class']} | {point['concurrency']} | {point['success_rate_pct']} "
                f"| {point['cli_exit_statuses'] or '-'} |"
            )
        add("")
        add(
            "These are client-observed outcomes. The status codes, timeout counts, and "
            "unconfirmed counts are the evidence; no internal stage is named as the cause, "
            "because Phase 1 collected no telemetry that could identify one."
        )
        add("")

    if summary["error_taxonomy"]:
        add("## Error taxonomy")
        add("")
        add("| class | c | phase | CLI exit | outcome | count |")
        add("|---|---:|---|---|---|---:|")
        for entry in summary["error_taxonomy"]:
            add(
                f"| {entry['video_class']} | {entry['concurrency']} | {entry['phase']} "
                f"| {entry.get('cli_exit_code')} | {entry['outcome']} | {entry['count']} |"
            )
        add("")

    add("## Mandatory warnings")
    add("")
    add(f"1. **Client uplink.** {summary['mandatory_warnings'][0]} Observed: {summary['uplink_verdict']}.")
    add(f"2. **Deployed concurrency cap.** {summary['mandatory_warnings'][1]}")
    add("")

    add("## Output files")
    add("")
    add("Everything this run produced, and what each file is for. The three charts are")
    add("plotted from the CSVs, so they can be regenerated at any time without")
    add("re-uploading anything.")
    add("")
    add("| File | What it holds | Why it matters |")
    add("|---|---|---|")
    for entry in summary["output_files"]:
        add(f"| `{entry['path']}` | {entry['holds']} | {entry['why']} |")
    add("")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Summarize a VSS ingest benchmark run.")
    parser.add_argument("--results-dir", type=Path, required=True)
    parser.add_argument(
        "--uplink-mbps",
        type=float,
        default=None,
        help="Declared client uplink in Mb/s, used to evaluate the mandatory uplink warning.",
    )
    args = parser.parse_args(argv)

    if not (args.results_dir / "csv" / "ingest_summary.csv").exists():
        print(f"ERROR  no ingest_summary.csv under {args.results_dir / 'csv'}")
        return 2

    summary = build_summary(args.results_dir, args.uplink_mbps)
    output_dir = args.results_dir / "visualizations"
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (output_dir / "summary.md").write_text(render_markdown(summary), encoding="utf-8")
    print(f"Wrote {output_dir / 'summary.json'}")
    print(f"Wrote {output_dir / 'summary.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
