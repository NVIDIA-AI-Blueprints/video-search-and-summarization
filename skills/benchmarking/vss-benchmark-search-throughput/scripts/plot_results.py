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
    cpu.png          CPU cores per service vs concurrency at one top_k;
                     one row per path, one column per critic setting
    gpu.png          GPU utilisation per service, same grid

Runs whose measured window did not open cleanly are drawn hollow: their
throughput is not comparable with the rest.
"""

from __future__ import annotations

import argparse
import csv
import sys
from collections import defaultdict
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

CPU_METRICS = ("cpu_core_seconds_total:mean_rate", "cpu_cores")
GPU_METRIC = "gpu_utilization"


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


def plot_resources(resources: list[dict[str, str]], out: Path, top_k: int | None) -> None:
    if not resources:
        print("  no resources_summary.csv rows; skipping CPU and GPU")
        return
    present = sorted({int(r["top_k"]) for r in resources})
    top_k = top_k if top_k in present else present[0]
    rows = [r for r in resources if int(r["top_k"]) == top_k]
    names = {r["metric"] for r in rows}
    cpu_metric = next((m for m in CPU_METRICS if m in names), None)

    for name, metric, title in (
        ("cpu.png", cpu_metric, f"CPU cores per service (top_k={top_k})"),
        ("gpu.png", GPU_METRIC, f"GPU utilisation %, summed over GPUs (top_k={top_k})"),
    ):
        picked = [r for r in rows if r["metric"] == metric]
        if not metric or not picked:
            print(f"  no {name[:-4].upper()} metric in resources_summary.csv; skipping {name}")
            continue
        # Several pods or GPUs of one service add up to that service's load.
        totals: dict[tuple, float] = defaultdict(float)
        for r in picked:
            value = _num(r.get("avg"))
            if value is not None:
                key = (r["path"] or "mix", _critic(r), r["service"], int(r["concurrency"]))
                totals[key] += value
        paths = _ordered(k[0] for k in totals)
        critics = _ordered(k[1] for k in totals)
        services = sorted({k[2] for k in totals})
        colours = {s: f"C{i % 10}" for i, s in enumerate(services)}
        fig, axes = _grid(paths, critics, title, sorted({k[3] for k in totals}))
        lines: dict[tuple, list[tuple[int, float]]] = defaultdict(list)
        for (path, critic, service, conc), value in totals.items():
            lines[(path, critic, service)].append((conc, value))
        drawn = 0
        for (path, critic, service), points in lines.items():
            if max(v for _, v in points) <= 0:
                continue
            points.sort()
            ax = axes[paths.index(path)][critics.index(critic)]
            ax.plot([p[0] for p in points], [p[1] for p in points], "o-",
                    color=colours[service], label=service)
            drawn += 1
        if not drawn:
            plt.close(fig)
            print(f"  every {metric} value is 0 (runs shorter than the scrape "
                  f"interval read as idle); skipping {name}")
            continue
        for ax in axes.flat:
            ax.grid(alpha=0.3)
        _save(fig, out, name)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("results_dir", type=Path)
    parser.add_argument("--out", type=Path, help="default: <results_dir>/plots")
    parser.add_argument("--top-k", type=int,
                        help="top_k the CPU/GPU plots show (default: the smallest)")
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
