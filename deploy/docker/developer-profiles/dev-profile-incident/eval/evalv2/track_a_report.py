# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""``track-a-report``: the Track A model comparison from saved runs (no model, judge or network).

Each ``--run DIR`` is a run directory ``run --live`` wrote and ``score`` scored; a pilot and its recovery run can both
be given (a work item found twice is refused). Per subject it reports:

- **Full set** (every planned item, nothing excluded): outcomes, contract-valid share, model-output share.
- **Valid outputs:** type accuracy, severity (``severity-v2-mae``: MAE primary, exact and QWK), start/end absolute
  error and window IoU, and object P/R/F1 (V1's pooled matching), from :func:`aggregate.aggregate`.
- **Reliability and cost** from ``attempts.jsonl``: HTTP attempts sent, attempts per item, failure kinds, tokens and
  latency of answered calls.
- **Duration buckets** (≤ 30 s, 30-60 s, > 60 s, from ``dataset-manifest-v1``): the same, per bucket.
- **Paired clips:** the (clip, repeat) items every subject scored, so models are compared on identical inputs.
"""

from __future__ import annotations

import json
import statistics
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from evalv2.core.private import ensure_private
from evalv2.tasks.incident_objects.aggregate import aggregate

REPORT_VERSION = "evalv2-track-a-report-v1"
BUCKETS = (("<=30s", 0.0, 30.0), ("30-60s", 30.0, 60.0), (">60s", 60.0, float("inf")))


class TrackAReportError(ValueError):
    pass


def _bucket(duration: float | None) -> str:
    if duration is None:
        return "unknown"
    return next(name for name, low, high in BUCKETS if low < duration <= high or (low == 0.0 and duration <= high))


def load(run_dirs: Sequence[Path], score_version: str, slot: str = "primary") -> tuple[list[dict], list[dict]]:
    videos, attempts, seen = [], [], {}
    for run in run_dirs:
        path = Path(run) / "scoring" / score_version / slot / "offline-videos.jsonl"
        if not path.exists():
            raise TrackAReportError(f"{path} does not exist: score the run with {score_version} first")
        for line in path.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            if row.get("stage") not in (None,):
                continue  # Track A only
            key = (row["subject"], row["incident_id"], row["repeat"])
            if key in seen:
                raise TrackAReportError(f"work item {key} appears in both {seen[key]} and {run}")
            seen[key] = str(run)
            videos.append(row)
        attempts_path = Path(run) / "attempts.jsonl"
        if attempts_path.exists():
            attempts += [json.loads(line) for line in attempts_path.read_text(encoding="utf-8").splitlines() if line]
    return videos, attempts


def _counts(videos: list[Mapping[str, Any]]) -> dict[str, Any]:
    """Every planned item: outcomes and shares, plus end-to-end type accuracy (an invalid output is a miss)."""
    block = aggregate(videos)
    n = block["videos"]
    return {"attempted": n, "outcomes": block["outcomes"], "scored": block["outcomes"].get("scored", 0),
            "contract_valid_share": round(block["outcomes"].get("scored", 0) / n, 4) if n else None,
            "model_output_share": round(block["with_model_output"] / n, 4) if n else None,
            "type_accuracy_end_to_end": round(sum(1 for v in videos if ((v.get("incident_field_scores") or {})
                                                                         .get("type") or {}).get("pass")) / n, 4) if n else None,
            "severity_coverage": block["severity"]["coverage"]}


def _valid(videos: list[Mapping[str, Any]]) -> dict[str, Any] | None:
    """Metrics over contract-valid outputs only (outcome ``scored``): one denominator for every metric."""
    rows = [v for v in videos if v.get("outcome") == "scored"]
    return _metrics(rows) if rows else None


def _metrics(videos: list[Mapping[str, Any]]) -> dict[str, Any]:
    block = aggregate(videos)
    items = block["continuity_v1"]["items"]
    ts = block["timestamps"]
    sev = block["severity"]
    return {"n": block["videos"],
            "type_accuracy": block["type"]["accuracy"], "type_n": block["type"]["n"],
            "severity": {k: sev[k] for k in ("mae", "exact", "qwk", "within_one", "n", "coverage", "counts")},
            "start_mae_s": ts["start_timestamp"]["mean_abs_error_s"], "end_mae_s": ts["end_timestamp"]["mean_abs_error_s"],
            "window_iou": ts["window_iou"]["mean"],
            "objects_f1": {kind: items[kind].get("f1") for kind in ("entities", "instruments", "assets")},
            "objects": {kind: {m: items[kind].get(m) for m in ("tp", "fp", "fn", "precision", "recall", "f1")}
                        for kind in ("entities", "instruments", "assets")}}


def _reliability(attempts: list[Mapping[str, Any]], items: int) -> dict[str, Any]:
    sent = [a for a in attempts if a.get("sent", True) is not False and "tokens" in a]
    ok = [a for a in sent if a.get("outcome") == "ok"]
    tokens = [a["tokens"]["total"] for a in ok if (a.get("tokens") or {}).get("total")]
    latency = sorted(a["latency_ms"] / 1000 for a in ok if a.get("latency_ms") is not None)
    return {"attempts_sent": len(sent), "attempts_per_item": round(len(sent) / items, 3) if items else None,
            "failures": dict(Counter(a.get("failure_kind") for a in sent if a.get("outcome") != "ok")),
            "budget_refusals": sum(1 for a in attempts if a.get("outcome") == "budget_exceeded"),
            "tokens_total": sum(tokens), "tokens_mean_ok": round(statistics.mean(tokens)) if tokens else None,
            "latency_p50_s": round(statistics.median(latency), 1) if latency else None,
            "latency_p95_s": round(latency[max(0, int(0.95 * len(latency)) - 1)], 1) if latency else None}


REFERENCE = "super-direct"  # the agent default; every other subject is compared with it, plus named extra pairs
EXTRA_PAIRS = (("nano-direct", "super-direct"), ("qwen35-9b-openrouter", "gemma4-31b-openrouter"))


def default_pairs(subjects: Sequence[str]) -> list[tuple[str, str]]:
    pairs = [(s, REFERENCE) for s in subjects if s != REFERENCE and REFERENCE in subjects]
    return list(dict.fromkeys([*pairs, *((a, b) for a, b in EXTRA_PAIRS if a in subjects and b in subjects)]))


def report(videos: list[dict], attempts: list[dict], durations: Mapping[str, float], *,
           bootstrap: bool = True, resamples: int | None = None) -> dict[str, Any]:
    by_subject: dict[str, list[dict]] = defaultdict(list)
    for row in videos:
        by_subject[row["subject"]].append(row)
    scored_keys = {s: {(r["incident_id"], r["repeat"]) for r in rows if r["outcome"] == "scored"}
                   for s, rows in by_subject.items()}
    paired = set.intersection(*scored_keys.values()) if scored_keys else set()
    subjects: dict[str, Any] = {}
    for subject, rows in sorted(by_subject.items()):
        buckets: dict[str, list[dict]] = defaultdict(list)
        for row in rows:
            buckets[_bucket(durations.get(row["incident_id"]))].append(row)
        subjects[subject] = {
            "full_set": _counts(rows),
            "valid": _valid(rows),
            "reliability": _reliability([a for a in attempts if a.get("subject") == subject], len(rows)),
            "by_duration": {name: {"full_set": _counts(buckets[name]), "valid": _valid(buckets[name])}
                            for name, _, _ in BUCKETS if buckets.get(name)},
            "paired": _valid([r for r in rows if (r["incident_id"], r["repeat"]) in paired]) if paired else None,
        }
    stats = None
    if bootstrap:
        from evalv2 import cluster_bootstrap as cb

        stats = cb.analysis(videos, default_pairs(sorted(by_subject)), resamples=resamples or cb.RESAMPLES)
    return {"report_version": REPORT_VERSION, "paired_items": len(paired), "bootstrap": stats,
            "duration_buckets": {name: sum(1 for d in durations.values() if _bucket(d) == name) for name, _, _ in BUCKETS},
            "subjects": subjects}


def _f(value: Any, digits: int = 2) -> str:
    return "—" if value is None else f"{value:.{digits}f}" if isinstance(value, float) else str(value)


def markdown(result: Mapping[str, Any], title: str) -> str:
    out = [f"# {title}", "", ("Severity MAE is primary (lower is better); exact and QWK secondary. Valid-output metrics use "
           "contract-valid outputs; full-set columns count every planned item."), "",
           "## Full set and reliability", "",
           ("| Model | Items | Contract-valid | Outcomes | Type acc end-to-end | Attempts/item | Tokens (mean ok) | "
           "Latency p50 / p95 s |"), "|---|---|---|---|---|---|---|---|"]
    for s, b in result["subjects"].items():
        f, r = b["full_set"], b["reliability"]
        out.append(f"| {s} | {f['attempted']} | {_f(f['contract_valid_share'])} | "
                   f"{', '.join(f'{k} {v}' for k, v in sorted(f['outcomes'].items()))} | "
                   f"{_f(f['type_accuracy_end_to_end'])} | {_f(r['attempts_per_item'])} | "
                   f"{r['tokens_mean_ok']} | {_f(r['latency_p50_s'], 1)} / {_f(r['latency_p95_s'], 1)} |")
    for heading, key in (("Contract-valid outputs (all clips)", "valid"),
                         (f"Paired clips ({result['paired_items']} items every model scored)", "paired")):
        out += ["", f"## {heading}", "",
                ("| Model | Sev MAE ↓ | Sev exact | Sev QWK | Sev n | Type acc | Start MAE s | End MAE s | Window IoU | "
                "Entities F1 | Instruments F1 | Assets F1 |"), "|---|---|---|---|---|---|---|---|---|---|---|---|"]
        for s, b in result["subjects"].items():
            m = b[key]
            if not m:
                continue
            sv, of = m["severity"], m["objects_f1"]
            out.append(f"| {s} | **{_f(sv['mae'])}** | {_f(sv['exact'])} | {_f(sv['qwk'])} | {sv['n']} | "
                       f"{_f(m['type_accuracy'])} | {_f(m['start_mae_s'], 1)} | {_f(m['end_mae_s'], 1)} | "
                       f"{_f(m['window_iou'])} | {_f(of['entities'])} | {_f(of['instruments'])} | {_f(of['assets'])} |")
    out += ["", "## By clip duration (metrics over contract-valid outputs)", "",
            "| Model | Bucket | Items | Contract-valid | Sev MAE ↓ | Sev n | Type acc | Entities F1 |",
            "|---|---|---|---|---|---|---|---|"]
    for s, b in result["subjects"].items():
        for name, d in b["by_duration"].items():
            c, m = d["full_set"], d["valid"]
            out.append(f"| {s} | {name} | {c['attempted']} | {_f(c['contract_valid_share'])} | "
                       f"{_f(m['severity']['mae']) if m else '—'} | {m['severity']['n'] if m else 0} | "
                       f"{_f(m['type_accuracy']) if m else '—'} | {_f(m['objects_f1']['entities']) if m else '—'} |")
    return "\n".join(out) + "\n"


_LABELS = {"contract_valid": "Contract-valid (all runs)", "severity_mae": "Severity MAE ↓", "severity_exact": "Severity exact",
           "severity_qwk": "Severity QWK", "type_accuracy": "Type accuracy", "start_mae_s": "Start error s ↓",
           "end_mae_s": "End error s ↓", "entities_f1": "Entities F1"}


def _ci(entry: Mapping[str, Any]) -> str:
    ci = entry.get("ci95")
    return f"[{ci[0]:.2f}, {ci[1]:.2f}]" if ci else "n/a"


def bootstrap_markdown(stats: Mapping[str, Any]) -> str:
    m = stats["method"]
    method = (f"Unit: {m['unit']}; {m['resamples']} resamples, seed {m['seed']}, {int(m['confidence'] * 100)}% "
              f"{m['interval']} intervals. Contract validity is over all runs; every other metric is conditional on a "
              "valid output, so the two must be read together.")
    out = ["", "## Uncertainty (video-level cluster bootstrap)", "", method, "",
           "| Model | Valid runs | " + " | ".join(_LABELS[k] for k in _LABELS) + " |",
           "|---|---|" + "---|" * len(_LABELS)]
    for subject, block in stats["subjects"].items():
        cells = [f"{_f(block['metrics'][k]['estimate'])} {_ci(block['metrics'][k])}" for k in _LABELS]
        out.append(f"| {subject} | {block['valid_runs']}/{block['runs']} | " + " | ".join(cells) + " |")
    note = ("Conditional metrics use only the runs where both models are valid; contract validity uses all runs. "
            "A bold interval excludes zero.")
    out += ["", "### Paired differences (A − B)", "", note, "",
            "| A − B | Both-valid runs | " + " | ".join(_LABELS[k] for k in _LABELS) + " |",
            "|---|---|" + "---|" * len(_LABELS)]
    for pair, block in stats["paired"].items():
        cells = []
        for k in _LABELS:
            e = block["metrics"][k]
            text = f"{_f(e['difference'])} {_ci(e)}"
            cells.append(f"**{text}**" if e.get("excludes_zero") else text)
        out.append(f"| {pair} | {block['both_valid_runs']} | " + " | ".join(cells) + " |")
    return "\n".join(out) + "\n"


def write(result: Mapping[str, Any], out: Path, title: str) -> Path:
    out = Path(out)
    ensure_private(out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "track-a-report.json").write_text(json.dumps(result, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    text = markdown(result, title) + (bootstrap_markdown(result["bootstrap"]) if result.get("bootstrap") else "")
    (out / "track-a-report.md").write_text(text, encoding="utf-8")
    return out
