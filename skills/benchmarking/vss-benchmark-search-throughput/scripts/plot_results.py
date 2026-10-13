#!/usr/bin/env python3
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

# /// script
# requires-python = ">=3.10"
# dependencies = ["matplotlib"]
# ///

"""Plot a results directory written by ``run_throughput.py``.

    uv run scripts/plot_results.py results/search_throughput_<stamp>
    uv run scripts/plot_results.py <dir> --top-k 10 --out /tmp/plots

Writes PNGs into ``<dir>/plots/`` (or ``--out``):

    throughput_<path>.png  one chart per path, critic off and on side by side
    throughput.png   queries/sec vs concurrency; one row per path, one
                     column per critic setting, one colour per top_k
    latency.png      p50 (solid) and p95 (dashed), same grid
    resources_{cpu,memory,gpu,gpu_memory}.png
                     per-service load vs concurrency; one row per path, one
                     column per critic setting, one line style per top_k
    resources_timeline.png
                     CPU, memory, GPU and GPU memory per service over the
                     whole sweep, each run's window shaded by path and critic
    latency_breakdown_<path>.png
                     mean seconds per CLI stage (embedding, ES, VLM, ...),
                     stacked; needs request records with stage timings

Runs whose measured window did not open cleanly are drawn hollow: their
throughput is not comparable with the rest.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402

CPU_METRICS = ("cpu_core_seconds_total:mean_rate", "cpu_cores")
GIB = 1 / 2**30
#: (file key, metrics in preference order, title, unit, scale to unit)
RESOURCE_PLOTS = (
    ("cpu", CPU_METRICS, "CPU cores per service", "cores", 1.0),
    ("memory", ("memory_working_set_bytes",), "Memory (working set) per service", "GiB", GIB),
    ("gpu", ("gpu_utilization",), "GPU utilisation per service, summed over GPUs", "%", 1.0),
    ("gpu_memory", ("gpu_memory_used",), "GPU memory used per service", "GiB", 1 / 1024),
)
#: (file key, raw Prometheus metric, title, unit, scale to unit)
TIMELINE_PLOTS = (
    ("cpu", "cpu_cores", "CPU cores", "cores", 1.0),
    ("memory", "memory_working_set_bytes", "Memory (working set)", "GiB", GIB),
    ("gpu", "gpu_utilization", "GPU utilisation", "%", 1.0),
    ("gpu_memory", "gpu_memory_used", "GPU memory used", "GiB", 1 / 1024),
)
TOP_SERVICES = 6
CELL_SERVICES = 3
#: Samples in the timeline's moving average (15 s scrape step -> 75 s).
TIMELINE_SMOOTH = 5
TOP_K_STYLES = ("-", "--", ":", "-.")
TIMELINE_GAP_SEC = 60
_POD_SUFFIX = re.compile(r"(-[a-z0-9]{6,10}-[a-z0-9]{5}|-\d+)$")


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _num(value: str | None) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except ValueError:
        return None


def _critic(row: dict[str, str]) -> str:
    if row.get("critic") in ("off", "False", "false"):
        return "critic off"
    count = row.get("critic_eval_count") or "all"
    return f"critic on ({count})"


def _ordered(values) -> list:
    return sorted(set(values), key=lambda v: (str(v) != "critic off", str(v)))


def _grid(rows: list, cols: list, title: str, concurrencies: list[int]):
    fig, axes = plt.subplots(
        len(rows), len(cols), figsize=(4.2 * len(cols), 3.2 * len(rows)),
        squeeze=False,
    )
    fig.suptitle(title)
    for ax in axes.flat:
        ax.set_xticks(concurrencies)
    for r, row in enumerate(rows):
        axes[r][0].set_ylabel(str(row))
    for c, col in enumerate(cols):
        axes[0][c].set_title(str(col))
        axes[-1][c].set_xlabel("concurrency")
    return fig, axes


def _save(fig, out: Path, name: str) -> None:
    handles, labels = [], []
    for ax in fig.axes:
        for h, label in zip(*ax.get_legend_handles_labels()):
            if label not in labels:
                handles.append(h)
                labels.append(label)
    if handles:
        fig.legend(handles, labels, loc="lower center", ncol=min(len(labels), 6),
                   fontsize="small")
    fig.tight_layout(rect=(0, 0.06, 1, 0.97))
    target = out / name
    fig.savefig(target, dpi=120)
    plt.close(fig)
    print(f"  wrote {target}")


def plot_summary(summary: list[dict[str, str]], out: Path) -> None:
    if not summary or "p50_sec" not in summary[0]:
        print("  summary.csv has no p50_sec column (written by an older version); "
              "skipping latency and throughput")
        return
    paths = _ordered(r["path"] or "mix" for r in summary)
    critics = _ordered(_critic(r) for r in summary)
    top_ks = sorted({int(r["top_k"]) for r in summary})
    concurrencies = sorted({int(r["concurrency"]) for r in summary})
    colours = {k: f"C{i}" for i, k in enumerate(top_ks)}

    cells: dict[tuple, list[dict[str, str]]] = defaultdict(list)
    for r in summary:
        cells[(r["path"] or "mix", _critic(r), int(r["top_k"]))].append(r)

    for name, title, series, unit in (
        ("throughput.png", "Throughput per path (queries/sec)",
         (("queries_per_sec", "-", ""),), "queries/sec"),
        ("latency.png", "Latency per path: p50 (solid), p95 (dashed)",
         (("p50_sec", "-", "p50"), ("p95_sec", "--", "p95")), "seconds"),
    ):
        fig, axes = _grid(paths, critics, title, concurrencies)
        for (path, critic, top_k), rows in cells.items():
            ax = axes[paths.index(path)][critics.index(critic)]
            rows = sorted(rows, key=lambda r: int(r["concurrency"]))
            xs = [int(r["concurrency"]) for r in rows]
            for column, style, tag in series:
                ys = [_num(r.get(column)) for r in rows]
                label = f"top_k {top_k}" if style == "-" else None
                ax.plot(xs, ys, style, color=colours[top_k], label=label,
                        alpha=1.0 if style == "-" else 0.6)
                for x, y, r in zip(xs, ys, rows):
                    clean = r.get("measurement_window_clean", "True") == "True"
                    ax.plot(x, y, "o", color=colours[top_k],
                            fillstyle="full" if clean else "none")
        for ax in axes.flat:
            ax.grid(alpha=0.3)
        for row in axes:
            row[0].set_ylabel(f"{row[0].get_ylabel()}\n{unit}")
        _save(fig, out, name)


def plot_path_throughput(summary: list[dict[str, str]], out: Path) -> None:
    """One ``throughput_<path>.png`` per path: critic off and on side by side."""
    if not summary:
        return
    critics = _ordered(_critic(r) for r in summary)
    top_ks = sorted({int(r["top_k"]) for r in summary})
    concurrencies = sorted({int(r["concurrency"]) for r in summary})
    colours = {k: f"C{i}" for i, k in enumerate(top_ks)}
    for path in _ordered(r["path"] or "mix" for r in summary):
        rows = [r for r in summary if (r["path"] or "mix") == path]
        fig, axes = plt.subplots(1, len(critics), figsize=(5.5 * len(critics), 4),
                                 squeeze=False)
        fig.suptitle(f"Throughput — path {path} (queries/sec)")
        for ax, critic in zip(axes[0], critics):
            ax.set_title(critic)
            ax.set_xlabel("concurrency")
            ax.set_ylabel("queries/sec")
            ax.set_xticks(concurrencies)
            ax.set_xlim(min(concurrencies) - 2.5, max(concurrencies) + 2.5)
            ax.grid(alpha=0.3)
            picked = [r for r in rows if _critic(r) == critic]
            if not picked:
                ax.text(0.5, 0.5, "not run yet", ha="center", va="center",
                        transform=ax.transAxes, color="gray")
                continue
            labels: dict[int, list[tuple[int, float]]] = defaultdict(list)
            for k in top_ks:
                points = sorted(
                    (int(r["concurrency"]), _num(r["queries_per_sec"]))
                    for r in picked if int(r["top_k"]) == k
                )
                if not points:
                    continue
                xs, ys = zip(*points)
                ax.plot(xs, ys, "o-", color=colours[k], label=f"top_k {k}")
                for x, y in points:
                    if y is not None:
                        labels[x].append((k, y))
            ymax = max(y for pts in labels.values() for _, y in pts)
            for x, pts in labels.items():
                pts.sort(key=lambda p: -p[1])
                # Values closer than this would print on top of each other, so
                # each run of close values shares one label.
                groups: list[list[tuple[int, float]]] = [[pts[0]]]
                for point in pts[1:]:
                    if groups[-1][-1][1] - point[1] < 0.08 * ymax:
                        groups[-1].append(point)
                    else:
                        groups.append([point])
                for group in groups:
                    text = " / ".join(f"{y:.2f}" for _, y in group)
                    colour = colours[group[0][0]] if len(group) == 1 else "dimgray"
                    ax.annotate(text, (x, group[0][1]), textcoords="offset points",
                                xytext=(0, 6), ha="center", fontsize=7, color=colour)
            ax.set_ylim(0, ymax * 1.15)
            ax.set_xlim(min(concurrencies) - 2.5, max(concurrencies) + 2.5)
        _save(fig, out, f"throughput_{path}.png")


def _top_services(values: dict[str, float], limit: int = TOP_SERVICES) -> list[str]:
    """The busiest services, dropping any below 2% of the busiest one."""
    ranked = sorted(values.items(), key=lambda kv: -kv[1])
    if not ranked or ranked[0][1] <= 0:
        return []
    return [s for s, v in ranked[:limit] if v >= 0.02 * ranked[0][1]]


def plot_resources(resources: list[dict[str, str]], out: Path, top_k: int | None) -> None:
    """``resources_<metric>.png``: per-service load vs concurrency.

    One row per path, one column per critic setting, one colour per service,
    one line style per top_k -- every sweep dimension on one page.
    """
    if not resources:
        print("  no resources_summary.csv rows; skipping resource plots")
        return
    if top_k is not None:
        resources = [r for r in resources if int(r["top_k"]) == top_k]
    names = {r["metric"] for r in resources}
    top_ks = sorted({int(r["top_k"]) for r in resources})
    styles = {k: TOP_K_STYLES[i % len(TOP_K_STYLES)] for i, k in enumerate(top_ks)}

    for key, metrics, title, unit, scale in RESOURCE_PLOTS:
        metric = next((m for m in metrics if m in names), None)
        picked = [r for r in resources if r["metric"] == metric]
        if not picked:
            print(f"  no {key} metric in resources_summary.csv; skipping resources_{key}.png")
            continue
        # Several pods or GPUs of one service add up to that service's load.
        totals: dict[tuple, float] = defaultdict(float)
        for r in picked:
            value = _num(r.get("avg"))
            if value is not None:
                totals[(r["path"] or "mix", _critic(r), r["service"], int(r["top_k"]),
                        int(r["concurrency"]))] += value * scale
        # Busiest services per cell: the services that matter for the event
        # path are not the ones that matter for attribute.
        peak: dict[tuple, dict[str, float]] = defaultdict(lambda: defaultdict(float))
        for (path, critic, service, _, _), value in totals.items():
            cell = peak[(path, critic)]
            cell[service] = max(cell[service], value)
        shown = {cell: set(_top_services(values, CELL_SERVICES))
                 for cell, values in peak.items()}
        services = sorted(set().union(*shown.values()))
        if not services:
            print(f"  every {metric} value is 0 (runs shorter than the scrape "
                  f"interval read as idle); skipping resources_{key}.png")
            continue
        colours = {s: f"C{i % 10}" for i, s in enumerate(services)}
        paths = _ordered(k[0] for k in totals)
        critics = _ordered(k[1] for k in totals)
        fig, axes = _grid(paths, critics,
                          f"{title} (line style = top_k)",
                          sorted({k[4] for k in totals}))
        lines: dict[tuple, list[tuple[int, float]]] = defaultdict(list)
        for (path, critic, service, k, conc), value in totals.items():
            if service in shown[(path, critic)]:
                lines[(path, critic, service, k)].append((conc, value))
        for (path, critic, service, k), points in sorted(lines.items()):
            points.sort()
            ax = axes[paths.index(path)][critics.index(critic)]
            ax.plot([p[0] for p in points], [p[1] for p in points], styles[k],
                    marker="o", markersize=3, color=colours[service], label=service)
        for k in top_ks:
            axes[0][0].plot([], [], styles[k], color="gray", label=f"top_k {k}")
        for ax in axes.flat:
            ax.grid(alpha=0.3)
            ax.set_ylim(bottom=0)
        for row in axes:
            row[0].set_ylabel(f"{row[0].get_ylabel()}\n{unit}")
        _save(fig, out, f"resources_{key}.png")


def _run_dirs(results_dir: Path, summary: list[dict[str, str]]) -> list[tuple[dict, Path]]:
    runs = sorted(summary, key=lambda r: r.get("started_at") or "")
    return [(r, results_dir / r["run_id"]) for r in runs
            if (results_dir / r["run_id"]).is_dir()]


def _segments(points: list[tuple[float, float]]) -> list[list[tuple[float, float]]]:
    """Split at gaps longer than ``TIMELINE_GAP_SEC`` so lines break between runs."""
    segments: list[list[tuple[float, float]]] = []
    for point in points:
        if not segments or point[0] - segments[-1][-1][0] > TIMELINE_GAP_SEC:
            segments.append([])
        segments[-1].append(point)
    return segments


def _moving_average(values: list[float], window: int) -> list[float]:
    half = window // 2
    return [sum(values[max(0, i - half):i + half + 1]) / len(values[max(0, i - half):i + half + 1])
            for i in range(len(values))]


def plot_timeline(results_dir: Path, summary: list[dict[str, str]], out: Path) -> None:
    """``resources_timeline.png``: every service's load across the whole sweep.

    Built from each run's raw Prometheus matrices (measured window only), so
    gaps between runs -- warmup, cooldown, ingest -- show as breaks. The shaded
    band behind each run is coloured by path; darker means the critic was on.
    """
    series: dict[str, dict[str, dict[float, float]]] = {
        key: defaultdict(lambda: defaultdict(float)) for key, *_ in TIMELINE_PLOTS
    }
    bands: list[tuple[float, float, str, bool]] = []
    for row, run_dir in _run_dirs(results_dir, summary):
        metrics_file = run_dir / "prometheus_metrics.json"
        if not metrics_file.exists():
            continue
        data = json.loads(metrics_file.read_text(encoding="utf-8"))
        window = data.get("window") or {}
        if window.get("start_epoch") and window.get("end_epoch"):
            bands.append((window["start_epoch"], window["end_epoch"],
                          row["path"] or "mix", _critic(row) != "critic off"))
        raw = data.get("raw") or {}
        for key, metric, _, _, scale in TIMELINE_PLOTS:
            for result in ((raw.get(metric) or {}).get("data") or {}).get("result") or []:
                labels = result.get("metric") or {}
                pod = labels.get("exported_pod") or labels.get("pod") or ""
                if not pod:
                    continue
                service = _POD_SUFFIX.sub("", pod)
                for ts, value in result.get("values") or []:
                    try:
                        series[key][service][float(ts)] += float(value) * scale
                    except ValueError:
                        continue
    if not any(series.values()):
        print("  no raw Prometheus series in the run directories; skipping resources_timeline.png")
        return

    start = min(min(points) for per in series.values() for points in per.values())
    paths = _ordered(b[2] for b in bands)
    path_colours = {p: f"C{i}" for i, p in enumerate(paths)}
    fig, axes = plt.subplots(len(TIMELINE_PLOTS), 1, figsize=(16, 2.8 * len(TIMELINE_PLOTS)),
                             sharex=True, squeeze=False)
    fig.suptitle("Resource usage over the sweep (measured windows, 75 s moving average; "
                 "band = path, darker band = critic on)")
    for ax, (key, _, title, unit, _) in zip(axes[:, 0], TIMELINE_PLOTS):
        for lo, hi, path, critic in bands:
            ax.axvspan((lo - start) / 3600, (hi - start) / 3600,
                       color=path_colours[path], alpha=0.32 if critic else 0.12, lw=0)
        peak = {s: max(points.values()) for s, points in series[key].items()}
        services = _top_services(peak)
        for i, service in enumerate(services):
            xs, ys = [], []
            for segment in _segments(sorted(series[key][service].items())):
                values = [v for _, v in segment]
                xs += [(ts - start) / 3600 for ts, _ in segment] + [float("nan")]
                ys += _moving_average(values, TIMELINE_SMOOTH) + [float("nan")]
            ax.plot(xs, ys, lw=0.9, color=f"C{(i + len(paths)) % 10}", label=service)
        ax.set_title(title, fontsize="medium", loc="left")
        ax.set_ylabel(unit)
        ax.set_ylim(bottom=0)
        ax.grid(alpha=0.3)
        ax.legend(loc="upper left", fontsize="x-small", ncol=3)
    axes[-1][0].set_xlabel("hours since the first measured window")
    for path in paths:
        axes[0][0].plot([], [], lw=8, alpha=0.3, color=path_colours[path], label=f"path {path}")
    axes[0][0].legend(loc="upper left", fontsize="x-small", ncol=4)
    fig.tight_layout(rect=(0, 0, 1, 0.97))
    target = out / "resources_timeline.png"
    fig.savefig(target, dpi=110)
    plt.close(fig)
    print(f"  wrote {target}")


def latency_breakdown(results_dir: Path, summary: list[dict[str, str]]) -> list[dict]:
    """Mean seconds per stage for each run, from ``request_records.jsonl``.

    ``other`` is wall clock minus the CLI's own ``total_s``: process startup,
    config load and client setup. Only measured, successful requests count.
    """
    rows: list[dict] = []
    for run, run_dir in _run_dirs(results_dir, summary):
        records = run_dir / "request_records.jsonl"
        if not records.exists():
            continue
        stages: dict[str, float] = defaultdict(float)
        wall = other = 0.0
        count = 0
        with records.open(encoding="utf-8") as f:
            for line in f:
                rec = json.loads(line)
                if rec.get("is_warmup") or rec.get("error") or rec.get("exit_code") != 0:
                    continue
                if rec.get("search_total_sec") is None:
                    continue
                count += 1
                wall += rec["latency_sec"]
                other += max(0.0, rec["latency_sec"] - rec["search_total_sec"])
                for label, entry in (rec.get("stage_timings") or {}).items():
                    stages[label] += entry.get("self_s") or 0.0
        if not count:
            continue
        rows.append({
            "run_id": run["run_id"], "path": run["path"] or "mix",
            "critic": _critic(run), "top_k": int(run["top_k"]),
            "concurrency": int(run["concurrency"]), "requests": count,
            "wall_sec": wall / count, "other_sec": other / count,
            "stages": {label: total / count for label, total in stages.items()},
        })
    return rows


def plot_latency_breakdown(results_dir: Path, summary: list[dict[str, str]],
                           out: Path) -> list[dict]:
    """``latency_breakdown_<path>.png``: where one request's time goes.

    Stacked bars of mean self time per stage; the black tick is the mean wall
    clock. Stages that ran concurrently (fusion's two legs, the critic's
    per-hit VLM calls) can stack above the tick: their sum is work, not
    elapsed time.
    """
    rows = latency_breakdown(results_dir, summary)
    if not rows:
        print("  no request carries stage timings (the sweep predates them); "
              "skipping latency_breakdown_*.png")
        return rows
    totals: dict[str, float] = defaultdict(float)
    for r in rows:
        for label, value in r["stages"].items():
            totals[label] += value
    labels = [lbl for lbl, v in sorted(totals.items(), key=lambda kv: -kv[1]) if v > 0]
    labels.append("other (CLI startup, config, clients)")
    colours = {lbl: plt.get_cmap("tab20")(i % 20) for i, lbl in enumerate(labels)}
    critics = _ordered(r["critic"] for r in rows)
    width = 0.8 / max(1, len(critics))

    for path in _ordered(r["path"] for r in rows):
        mine = [r for r in rows if r["path"] == path]
        top_ks = sorted({r["top_k"] for r in mine})
        concs = sorted({r["concurrency"] for r in mine})
        fig, axes = plt.subplots(1, len(top_ks), figsize=(4.5 * len(top_ks) + 5, 4.8),
                                 squeeze=False, sharey=True)
        fig.suptitle(f"Latency per stage — path {path}\nmean seconds per request; "
                     "black tick = wall clock; hatched = critic on")
        for ax, k in zip(axes[0], top_ks):
            ax.set_title(f"top_k {k}")
            ax.set_xlabel("concurrency\n(bars left to right: " + " | ".join(critics) + ")")
            ax.set_xticks(range(len(concs)))
            ax.set_xticklabels([str(c) for c in concs])
            ax.grid(alpha=0.3, axis="y")
            for j, critic in enumerate(critics):
                for i, conc in enumerate(concs):
                    r = next((x for x in mine if x["top_k"] == k and x["critic"] == critic
                              and x["concurrency"] == conc), None)
                    if r is None:
                        continue
                    x = i - 0.4 + width * (j + 0.5)
                    bottom = 0.0
                    for lbl in labels:
                        value = r["other_sec"] if lbl.startswith("other") else r["stages"].get(lbl, 0.0)
                        if value <= 0:
                            continue
                        ax.bar(x, value, width * 0.9, bottom=bottom, color=colours[lbl],
                               hatch="//" if critic != "critic off" else None,
                               edgecolor="white", linewidth=0.3, label=lbl)
                        bottom += value
                    ax.plot([x - width * 0.45, x + width * 0.45], [r["wall_sec"]] * 2,
                            color="black", lw=1.5)
        axes[0][0].set_ylabel("seconds")
        drawn = {label for ax in axes[0] for label in ax.get_legend_handles_labels()[1]}
        ordered = [lbl for lbl in labels if lbl in drawn]
        fig.legend([Patch(color=colours[lbl]) for lbl in ordered], ordered, loc="center right",
                   fontsize="x-small", title="stage", title_fontsize="small")
        fig.tight_layout(rect=(0, 0, 1 - 3.6 / fig.get_figwidth(), 1))
        target = out / f"latency_breakdown_{path}.png"
        fig.savefig(target, dpi=120)
        plt.close(fig)
        print(f"  wrote {target}")
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("results_dir", type=Path)
    parser.add_argument("--out", type=Path, help="default: <results_dir>/plots")
    parser.add_argument("--top-k", type=int,
                        help="limit the resource plots to one top_k (default: all, "
                             "one line style each)")
    args = parser.parse_args(argv)

    summary = _read_csv(args.results_dir / "summary.csv")
    if not summary:
        print(f"ERROR: no summary.csv rows in {args.results_dir}", file=sys.stderr)
        return 2
    out = args.out or args.results_dir / "plots"
    out.mkdir(parents=True, exist_ok=True)
    print(f"{len(summary)} run(s) from {args.results_dir}")
    plot_summary(summary, out)
    plot_path_throughput(summary, out)
    plot_resources(_read_csv(args.results_dir / "resources_summary.csv"), out, args.top_k)
    plot_timeline(args.results_dir, summary, out)
    plot_latency_breakdown(args.results_dir, summary, out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
