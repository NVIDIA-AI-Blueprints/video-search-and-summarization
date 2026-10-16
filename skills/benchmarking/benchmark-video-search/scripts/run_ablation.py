#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Aggregate per-knob mAP deltas from a set of scored runs.

To turn the ranked hypotheses into measured contributions, run the comparable
config once (the baseline), then re-run with exactly one retrieval knob
toggled per run, score each with the same clip AP@10/min(R,10) scorer, and
point this tool at the resulting report.json files. It extracts the chosen
metric (default ``mAP@10``) from each report and prints a delta table vs
the baseline, sorted by absolute delta so the biggest contributors surface
first.

The extraction and table are pure and unit-testable without a deployment;
``ABLATION_CONFIGS`` documents the toggle set the comparable run should be
re-run with (one flag change per row). ``--emit-configs`` prints them as a
copy-paste shell snippet.
"""
import argparse
import csv
import json
import sys
from pathlib import Path

# One knob toggled per config, relative to the comparable baseline. The
# baseline itself is single-path attribute, original query, no cosine floor,
# no critic, full gallery, clip-relevance scoring.
ABLATION_CONFIGS = [
    {"name": "baseline", "flags": "--fixed-search-path attribute --min-cosine-similarity -1 --no-critic --full-attribute-gallery"},
    {"name": "fusion_on", "flags": "--fusion-method rrf --w-attribute 0.55 --w-embed 0.35 --w-tag 0"},
    {"name": "decompose_on", "flags": "--decompositions --llm-model nvidia/nemotron-3.5-lightning-30b-a3b"},
    {"name": "threshold_0.3", "flags": "--min-cosine-similarity 0.3"},
    {"name": "critic_on", "flags": "--critic-eval-count 10"},
    {"name": "top10_only", "flags": "--top-k 10 --retrieval-depth top-k"},
    {"name": "embed_path_only", "flags": "--fixed-search-path embed --full-gallery"},
]


def extract_metric(report: dict, metric: str = "mAP@10") -> float | None:
    """Tolerantly dig a metric out of a report.json-shaped dict."""
    if not isinstance(report, dict):
        return None
    for bucket in (report, report.get("overall"), report.get("summary"),
                    report.get("clip"), report.get("pas"), report.get("metrics")):
        if isinstance(bucket, dict) and metric in bucket and isinstance(bucket[metric], (int, float)):
            return float(bucket[metric])
    # Last resort: a flat {"mAP@10": v} or nested under a domain key.
    for value in report.values():
        if isinstance(value, dict) and metric in value and isinstance(value[metric], (int, float)):
            return float(value[metric])
    return None


def ablation_table(baseline: float, runs: list[dict], metric: str = "mAP@10") -> list[dict]:
    """Attach delta-vs-baseline to each run; sort by absolute delta desc."""
    rows = []
    for run in runs:
        value = run["value"]
        rows.append({
            "run": run["name"],
            metric: value,
            "delta_vs_baseline": round(value - baseline, 6),
            "mAP_scope": run.get("mAP_scope"),
        })
    rows.sort(key=lambda r: abs(r["delta_vs_baseline"]), reverse=True)
    return rows


def load_run(path: Path, metric: str = "mAP@10") -> dict:
    """Read one report.json -> {name, value, mAP_scope}."""
    report = json.loads(Path(path).read_text(encoding="utf-8"))
    return {
        "name": Path(path).parent.name,
        "value": extract_metric(report, metric),
        "mAP_scope": extract_metric(report, "mAP_scope") if isinstance(extract_metric(report, "mAP_scope"), str) else None,
    }


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--baseline", type=Path, required=True, help="Baseline report.json.")
    p.add_argument("--run", type=Path, action="append", required=True,
                   help="A run report.json (its parent dir names the run). Repeat once per toggle.")
    p.add_argument("--metric", default="mAP@10", help="Metric key to extract (default mAP@10).")
    p.add_argument("--out", type=Path, help="Write the delta table CSV here (default: stdout).")
    p.add_argument("--emit-configs", action="store_true", help="Print ABLATION_CONFIGS as a shell snippet and exit.")
    return p


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    if args.emit_configs:
        for cfg in ABLATION_CONFIGS:
            print(f"# {cfg['name']}\npython scripts/run_eval_flows.py {cfg['flags']} --scoring clip-relevance\n")
        return 0
    baseline = extract_metric(json.loads(args.baseline.read_text(encoding="utf-8")), args.metric)
    if baseline is None:
        print(f"ERROR: {args.metric} not found in {args.baseline}", file=sys.stderr)
        return 1
    runs = [load_run(p, args.metric) for p in args.run]
    missing = [r["name"] for r in runs if r["value"] is None]
    if missing:
        print(f"ERROR: {args.metric} missing in: {missing}", file=sys.stderr)
        return 1
    rows = ablation_table(baseline, runs, args.metric)
    out_cols = ["run", args.metric, "delta_vs_baseline", "mAP_scope"]
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        with args.out.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=out_cols, lineterminator="\n")
            writer.writeheader()
            for r in rows:
                writer.writerow({c: r[c] for c in out_cols})
    print(f"baseline {args.metric}={baseline}")
    for r in rows:
        print(f"  {r['run']:<20} {args.metric}={r[args.metric]}  delta={r['delta_vs_baseline']:+.6f}  scope={r['mAP_scope']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
