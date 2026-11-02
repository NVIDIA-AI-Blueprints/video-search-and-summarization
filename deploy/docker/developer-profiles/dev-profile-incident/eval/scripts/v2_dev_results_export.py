# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Export the Evaluation V2 development-pilot aggregates as chart-ready CSV and SVG (no model, judge or network).

Inputs are private (the run directories and ``track-a-report.json`` under the data root); outputs are aggregates only
(no ground-truth text, prompts, replies, URLs or clip content), so they may be committed:

    uv run --frozen python scripts/v2_dev_results_export.py --data-root <private root> --out methodology/results/v2-dev

Tables: ``track_a_models.csv`` (per model, with 95% video-level cluster-bootstrap intervals),
``track_a_paired_differences.csv``, ``track_a_by_duration.csv``, ``track_b_pipeline.csv``,
``tokens_cost_latency.csv``, ``dimension_status.csv``. Figures: ``fig_*.svg`` (inline SVG, no dependencies).
"""

from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # the eval dir, for evalv2

NAMES = {"nano-direct": "Cosmos 3 Nano", "super-direct": "Cosmos 3 Super", "omni-direct": "Nemotron 3 Nano Omni",
         "qwen35-9b-openrouter": "Qwen3.5-9B", "gemma4-31b-openrouter": "Gemma 4 31B",
         "nano-pipeline": "Cosmos 3 Nano", "super-pipeline": "Cosmos 3 Super"}
ORDER = ["nano-direct", "super-direct", "omni-direct", "qwen35-9b-openrouter", "gemma4-31b-openrouter"]
ROUTE = {"nano-direct": "Switchyard", "super-direct": "Switchyard", "omni-direct": "Switchyard",
         "qwen35-9b-openrouter": "OpenRouter (DeepInfra, Parasail)",
         "gemma4-31b-openrouter": "OpenRouter (Crusoe, Parasail)"}
TRACK_A_RUNS = ("c1a", "c1a-recovery", "c1e-openrouter-gemma4-31b", "c1f-openrouter-qwen35-9b")
SCORE = "scoring/evalv2-score-stage-v3/primary/offline-summary.json"


def _write(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _ci(entry: dict[str, Any]) -> tuple[Any, Any]:
    return tuple(entry.get("ci95") or (None, None))  # type: ignore[return-value]


def track_a(report: dict[str, Any], out: Path) -> list[dict[str, Any]]:
    boot, rows = report["bootstrap"], []
    for s in ORDER:
        m, full, rel = boot["subjects"][s]["metrics"], report["subjects"][s]["full_set"], report["subjects"][s]["reliability"]
        row: dict[str, Any] = {"model": NAMES[s], "route": ROUTE[s], "runs": boot["subjects"][s]["runs"],
                               "valid_runs": boot["subjects"][s]["valid_runs"]}
        for k in ("contract_valid", "severity_mae", "severity_exact", "severity_qwk", "type_accuracy", "start_mae_s",
                  "end_mae_s", "entities_f1"):
            row[k] = m[k]["estimate"]
            row[k + "_ci_low"], row[k + "_ci_high"] = _ci(m[k])
        valid = report["subjects"][s]["valid"]
        row.update(instruments_f1=valid["objects_f1"]["instruments"], assets_f1=valid["objects_f1"]["assets"],
                   type_accuracy_end_to_end=full["type_accuracy_end_to_end"],
                   tokens_per_answered_call=rel["tokens_mean_ok"], latency_p50_s=rel["latency_p50_s"],
                   latency_p95_s=rel["latency_p95_s"], attempts_per_run=rel["attempts_per_item"],
                   outcomes=";".join(f"{k}={v}" for k, v in sorted(full["outcomes"].items())))
        rows.append(row)
    _write(out / "track_a_models.csv", rows)
    paired = []
    for pair, block in boot["paired"].items():
        a, b = pair.split(" - ")
        for k, e in block["metrics"].items():
            low, high = _ci(e)
            paired.append({"model_a": NAMES[a], "model_b": NAMES[b], "metric": k, "a": e["a"], "b": e["b"],
                           "difference": e["difference"], "ci_low": low, "ci_high": high,
                           "excludes_zero": e["excludes_zero"], "lower_is_better": e["lower_is_better"],
                           "runs_used": "all runs" if k == "contract_valid" else f"{block['both_valid_runs']} both-valid"})
    _write(out / "track_a_paired_differences.csv", paired)
    by_duration = []
    for s in ORDER:
        for bucket, d in report["subjects"][s]["by_duration"].items():
            v = d["valid"]
            by_duration.append({"model": NAMES[s], "duration_bucket": bucket, "runs": d["full_set"]["attempted"],
                                "contract_valid": d["full_set"]["contract_valid_share"],
                                "severity_mae": v["severity"]["mae"] if v else None,
                                "severity_n": v["severity"]["n"] if v else 0,
                                "type_accuracy": v["type_accuracy"] if v else None,
                                "entities_f1": v["objects_f1"]["entities"] if v else None})
    _write(out / "track_a_by_duration.csv", by_duration)
    return rows


def _latency(attempts: list[dict[str, Any]], ok: set[str]) -> dict[str, tuple[float, float]]:
    by: dict[str, list[float]] = defaultdict(list)
    for a in attempts:
        if a.get("latency_ms") is not None and a.get("outcome") in ok:
            by[a["subject"]].append(a["latency_ms"] / 1000)
    return {s: (round(statistics.median(v), 1), round(sorted(v)[max(0, int(0.95 * len(v)) - 1)], 1))
            for s, v in by.items() for v in [sorted(v)]}


def track_b(runs: Path, out: Path) -> list[dict[str, Any]]:
    summary = json.loads((runs / "c1b" / SCORE).read_text())
    attempts = [json.loads(line) for line in (runs / "c1b" / "attempts.jsonl").read_text().splitlines() if line]
    latency = _latency(attempts, {"answered", "contract_rejected"})
    tokens: dict[str, Counter[str]] = defaultdict(Counter)
    run_keys: dict[str, set[tuple[str, int]]] = defaultdict(set)
    for a in attempts:
        run_keys[a["subject"]].add((a["item_id"], a["repeat"]))
        for ledger, v in (a.get("tokens") or {}).items():
            tokens[a["subject"]][ledger] += (v or {}).get("tokens") or 0
    rows = []
    for sub, block in summary["subjects"].items():
        n = len(run_keys[sub])
        for stage in ("p1", "p1_agent", "final"):
            b = block["pipeline"]["stages"][stage]
            sev, items, ts = b["severity"], b["continuity_v1"]["items"], b["timestamps"]
            rows.append({"model": NAMES[sub], "stage": stage, "runs": sev["attempted"], "valid": sev["n"],
                         "severity_mae": sev["mae"], "severity_exact": sev["exact"], "severity_qwk": sev["qwk"],
                         "type_accuracy": b["type"]["accuracy"], "start_mae_s": ts["start_timestamp"]["mean_abs_error_s"],
                         "end_mae_s": ts["end_timestamp"]["mean_abs_error_s"],
                         "entities_f1": round(items["entities"]["f1"], 4),
                         "instruments_f1": round(items["instruments"]["f1"], 4),
                         "assets_f1": round(items["assets"]["f1"], 4),
                         "tokens_per_run_all_stages": round(sum(tokens[sub].values()) / n) if n else None,
                         **{f"tokens_per_run_{k}": round(v / n) for k, v in sorted(tokens[sub].items())},
                         "latency_p50_s": latency.get(sub, (None, None))[0],
                         "latency_p95_s": latency.get(sub, (None, None))[1]})
    _write(out / "track_b_pipeline.csv", rows)
    return rows


def cost(runs: Path, out: Path, track_a_rows: list[dict[str, Any]], track_b_rows: list[dict[str, Any]]) -> None:
    """Tokens, latency and money: Switchyard charges the project nothing (tokens recorded); OpenRouter's per-call
    ``usage.cost`` is summed from the saved responses."""
    or_cost: dict[str, float] = {}
    for run, subject in (("c1e-openrouter-gemma4-31b", "gemma4-31b-openrouter"),
                         ("c1f-openrouter-qwen35-9b", "qwen35-9b-openrouter")):
        from evalv2.core.artifacts import ArtifactRef, ArtifactStore

        total, store = 0.0, ArtifactStore(runs / run / "store")
        for line in (runs / run / "attempts.jsonl").read_text().splitlines():
            ref = json.loads(line).get("response_ref")
            if ref:
                body = store.get(ArtifactRef.parse(ref)) or {}
                total += float((body.get("usage") or {}).get("cost") or 0)
        or_cost[subject] = total
    rows = []
    for r, s in zip(track_a_rows, ORDER, strict=True):
        spent = or_cost.get(s)
        rows.append({"track": "A (P1 only)", "model": r["model"], "route": r["route"], "runs": r["runs"],
                     "tokens_per_answered_call": r["tokens_per_answered_call"], "latency_p50_s": r["latency_p50_s"],
                     "latency_p95_s": r["latency_p95_s"], "attempts_per_run": r["attempts_per_run"],
                     "usd_actual": round(spent, 4) if spent is not None else 0.0,
                     "usd_per_run": round(spent / r["runs"], 5) if spent else 0.0,
                     "billing": "OpenRouter prepaid, per-call usage.cost" if spent is not None
                     else "Switchyard: no charge to the project"})
    for r in track_b_rows:
        if r["stage"] == "final":
            rows.append({"track": "B (production pipeline)", "model": r["model"], "route": "Switchyard, experiment agent",
                         "runs": r["runs"], "tokens_per_answered_call": r["tokens_per_run_all_stages"],
                         "latency_p50_s": r["latency_p50_s"], "latency_p95_s": r["latency_p95_s"],
                         "attempts_per_run": None, "usd_actual": 0.0, "usd_per_run": 0.0,
                         "billing": "Switchyard: no charge to the project"})
    _write(out / "tokens_cost_latency.csv", rows)


DIMENSIONS = [
    ("Incident extraction", "complete (development)", ("Track A five models and Track B (Nano, Super), dev split; "
     "severity MAE primary with cluster-bootstrap CIs; description fields await the judge"), ("track_a_models.csv; "
     "track_b_pipeline.csv")),
    ("Object identification", "partial (development)", ("Entity/instrument/asset matching F1 (MiniLM, V1 rules) done; "
     "attribute correctness (names, descriptions, threat level) awaits the judge"), "track_a_models.csv"),
    ("Evidence localisation", "pending", ("Box references (RA1) still being generated; crops recorded in C1b but "
     "unscored"), "-"),
    ("Querying (chatbot)", "pending", ("References RA2 built (189; 163 ai_validated, 26 flagged); grounded chat blocked "
     "on Gate M2; no chat pilot run; judge needed"), "-"),
    ("AI dashboard", "pending", ("References RB2 built (75 ai_validated after blind re-validation); dashboard pilot "
     "(C1c) not run"), "-"),
    ("Token usage / cost", "complete (development)", ("Per-call tokens for every model and stage; OpenRouter cost "
     "from per-call usage; Switchyard is not charged to the project"), "tokens_cost_latency.csv"),
    ("Latency", "complete (development)", "Per-call p50/p95 for Track A, per-run p50/p95 for Track B",
     "tokens_cost_latency.csv"),
]


def status(out: Path) -> None:
    _write(out / "dimension_status.csv", [{"dimension": d, "status": s, "evidence": e, "files": f}
                                          for d, s, e, f in DIMENSIONS])


def _bars(title: str, rows: list[tuple[str, float | None, tuple[Any, Any] | None]], scale: float, unit: str,
          note: str) -> str:
    left, width, row_h, top = 190, 380, 28, 48
    height = top + row_h * len(rows) + 52
    parts = [(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {left + width + 150} {height}" role="img" '
             f'aria-label="{title}" font-family="sans-serif" font-size="12">'),
             f'<text x="10" y="22" font-size="14" font-weight="bold">{title}</text>',
             f'<text x="10" y="38" font-size="11" fill="#555">{note}</text>']
    for i, (label, value, ci) in enumerate(rows):
        y = top + i * row_h
        parts.append(f'<text x="{left - 8}" y="{y + 17}" text-anchor="end">{label}</text>')
        if value is None:
            parts.append(f'<text x="{left + 6}" y="{y + 17}" fill="#555">n/a</text>')
            continue
        w = width * min(value, scale) / scale
        parts.append(f'<rect x="{left}" y="{y + 6}" width="{w:.1f}" height="16" fill="#4e79a7"/>')
        if ci and ci[0] is not None:
            x0, x1 = left + width * min(ci[0], scale) / scale, left + width * min(ci[1], scale) / scale
            parts.append(f'<line x1="{x0:.1f}" y1="{y + 14}" x2="{x1:.1f}" y2="{y + 14}" stroke="#222" stroke-width="1.5"/>')
            parts += [f'<line x1="{x:.1f}" y1="{y + 9}" x2="{x:.1f}" y2="{y + 19}" stroke="#222"/>' for x in (x0, x1)]
        label_x = left + max(w, width * min((ci or (0, 0))[1] or 0, scale) / scale) + 6
        parts.append(f'<text x="{label_x:.1f}" y="{y + 17}">{value:.2f}{unit}</text>')
    axis = top + row_h * len(rows) + 8
    parts.append(f'<line x1="{left}" y1="{axis}" x2="{left + width}" y2="{axis}" stroke="#888"/>')
    for t in range(5):
        x = left + width * t / 4
        parts.append(f'<text x="{x:.0f}" y="{axis + 15}" text-anchor="middle" fill="#555">{scale * t / 4:g}</text>')
    parts.append("</svg>")
    return "\n".join(parts) + "\n"


def figures(rows: list[dict[str, Any]], out: Path) -> None:
    def series(key: str) -> list[tuple[str, float | None, tuple[Any, Any]]]:
        return [(r["model"], r[key], (r.get(key + "_ci_low"), r.get(key + "_ci_high"))) for r in rows]

    note = "Development split, 25 clips x 2 repeats; bars: estimate, whiskers: 95% video-level bootstrap CI"
    (out / "fig_contract_validity.svg").write_text(_bars("Contract-valid outputs (all runs)", series("contract_valid"),
                                                         1.0, "", note))
    (out / "fig_severity_mae.svg").write_text(_bars("Severity MAE on valid outputs (lower is better)",
                                                    series("severity_mae"), 2.0, "", note))
    (out / "fig_type_accuracy.svg").write_text(_bars("Incident type accuracy on valid outputs", series("type_accuracy"),
                                                     1.0, "", note))
    (out / "fig_entities_f1.svg").write_text(_bars("Entity matching F1 on valid outputs", series("entities_f1"), 1.0, "",
                                                   note))
    (out / "fig_latency_p50.svg").write_text(_bars("Track A latency, median per call (s)",
                                                   [(r["model"], r["latency_p50_s"], None) for r in rows], 100.0, " s",
                                                   "Development split; p95 in tokens_cost_latency.csv"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    runs = args.data_root / "v2" / "runs"
    report = json.loads((args.data_root / "v2" / "reports" / "track-a-five-models" / "track-a-report.json").read_text())
    args.out.mkdir(parents=True, exist_ok=True)
    rows = track_a(report, args.out)
    b_rows = track_b(runs, args.out)
    cost(runs, args.out, rows, b_rows)
    status(args.out)
    figures(rows, args.out)
    print(json.dumps({"out": str(args.out), "files": sorted(p.name for p in args.out.iterdir())}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
