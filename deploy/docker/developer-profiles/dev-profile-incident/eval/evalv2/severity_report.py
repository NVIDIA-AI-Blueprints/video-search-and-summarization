# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""``severity-report``: severity MAE (primary), exact accuracy and QWK (secondary) per model, from saved scores.

It reads the per-video rows ``score`` wrote (``offline-videos.jsonl`` or ``live-videos.jsonl``) of one or more runs,
for example a pilot and its recovery run, and recomputes ``severity-v2-mae`` (:func:`aggregate.severity_metrics`)
per subject and, for Track B, per stage. No model, judge or network is used.

- **One row per work item:** a (subject, stage, clip, repeat) found in two inputs is refused, so a recovery run
  can only add the items its pilot left open.
- **Outputs:** ``severity-report.json`` (rows with counts, coverage and per-category metrics), ``severity-report.md``
  (the model comparison and per-category tables) and ``severity-mae.svg`` (MAE per row, with ``n/attempted``).
  MAE is an error (lower is better); it is never added to or averaged with the higher-is-better metrics.
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

from evalv2.core.private import ensure_private
from evalv2.tasks.incident_objects.aggregate import (
    SEVERITY_METRIC_VERSION,
    severity_metrics,
    with_failed_runs,
)

REPORT_VERSION = "evalv2-severity-report-v1"


class SeverityReportError(ValueError):
    pass


def load_rows(paths: Sequence[Path]) -> list[dict[str, Any]]:
    rows, seen = [], {}
    for path in paths:
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            key = (row.get("subject"), row.get("stage"), row.get("incident_id"), row.get("repeat"))
            if key in seen:
                raise SeverityReportError(f"work item {key} appears in both {seen[key]} and {path}")
            seen[key] = str(path)
            rows.append(row)
    return rows


def report(rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    groups: dict[tuple[str, str | None], list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[(str(row.get("subject")), row.get("stage"))].append(row)
    out = []
    for (subject, stage), videos in sorted(groups.items(), key=lambda kv: (kv[0][0], kv[0][1] or "")):
        rows_for = list(videos)
        if stage not in (None, "p1"):  # Track B: runs that never reached this stage stay in its denominator
            rows_for = with_failed_runs(rows_for, groups.get((subject, "p1"), []), stage)
        out.append({"subject": subject, "stage": stage, "severity": severity_metrics(rows_for)})
    return {"report_version": REPORT_VERSION, "metric_version": SEVERITY_METRIC_VERSION, "primary": "mae",
            "rows": out}


def _fmt(value: Any, digits: int = 2) -> str:
    return "—" if value is None else f"{value:.{digits}f}" if isinstance(value, float) else str(value)


def markdown(result: Mapping[str, Any], title: str) -> str:
    intro = (f"Metric `{result['metric_version']}`: **MAE primary** (severity levels, lower is better); exact accuracy "
             "and QWK secondary (higher is better). All three over the same valid pairs (n).")
    header = ("| Model | Stage | MAE ↓ | Exact ↑ | QWK ↑ | Within ±1 | Signed | n / attempted | Coverage | "
              "Invalid output | Invalid label | Missing | No output |")
    lines = [f"# {title}", "", intro, "", header, "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for row in result["rows"]:
        s, c = row["severity"], row["severity"]["counts"]
        lines.append(f"| {row['subject']} | {row['stage'] or 'P1 (Track A)'} | **{_fmt(s['mae'])}** | {_fmt(s['exact'])} | "
                     f"{_fmt(s['qwk'])} | {_fmt(s['within_one'])} | {_fmt(s['mean_signed_error'])} | "
                     f"{s['n']} / {s['attempted']} | {_fmt(s['coverage'])} | {c['invalid_output']} | "
                     f"{c['invalid_label']} | {c['missing_prediction']} | {c['no_model_output']} |")
    lines += ["", "## Per category (ground-truth incident type)", "",
              "| Model | Stage | Category | MAE ↓ | Exact ↑ | QWK ↑ | n / attempted |", "|---|---|---|---|---|---|---|"]
    for row in result["rows"]:
        for category, m in row["severity"]["by_category"].items():
            lines.append(f"| {row['subject']} | {row['stage'] or 'P1'} | {category} | {_fmt(m['mae'])} | "
                         f"{_fmt(m['exact'])} | {_fmt(m['qwk'])} | {m['n']} / {m['attempted']} |")
    return "\n".join(lines) + "\n"


def svg(result: Mapping[str, Any], title: str) -> str:
    """A horizontal bar chart of MAE per row (scale 0-4, the largest possible error), labelled with n/attempted."""
    rows = result["rows"]
    left, bar_w, row_h, top = 210, 360, 26, 40
    height = top + row_h * len(rows) + 40
    opening = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {left + bar_w + 140} {height}" role="img" '
               f'aria-label="{title}: severity MAE per model, lower is better" font-family="sans-serif" font-size="12">')
    parts = [opening,
             f'<text x="10" y="22" font-size="14" font-weight="bold">{title}: severity MAE (lower is better)</text>']
    for i, row in enumerate(rows):
        y = top + i * row_h
        s = row["severity"]
        label = row["subject"] + (f" · {row['stage']}" if row["stage"] else "")
        parts.append(f'<text x="{left - 8}" y="{y + 16}" text-anchor="end">{label}</text>')
        if s["mae"] is not None:
            width = bar_w * s["mae"] / 4
            parts.append(f'<rect x="{left}" y="{y + 4}" width="{width:.1f}" height="16" fill="#4e79a7"/>')
            parts.append(f'<text x="{left + width + 6:.1f}" y="{y + 16}">{s["mae"]:.2f}  (n {s["n"]}/{s["attempted"]})'
                         "</text>")
        else:
            parts.append(f'<text x="{left + 6}" y="{y + 16}">no valid pair (n 0/{s["attempted"]})</text>')
    axis_y = top + row_h * len(rows) + 6
    parts.append(f'<line x1="{left}" y1="{axis_y}" x2="{left + bar_w}" y2="{axis_y}" stroke="#666"/>')
    for tick in range(5):
        x = left + bar_w * tick / 4
        parts.append(f'<text x="{x:.0f}" y="{axis_y + 16}" text-anchor="middle">{tick}</text>')
    parts.append("</svg>")
    return "\n".join(parts) + "\n"


def write(result: Mapping[str, Any], out: Path, title: str) -> Path:
    out = Path(out)
    ensure_private(out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "severity-report.json").write_text(json.dumps(result, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    (out / "severity-report.md").write_text(markdown(result, title), encoding="utf-8")
    (out / "severity-mae.svg").write_text(svg(result, title), encoding="utf-8")
    return out
