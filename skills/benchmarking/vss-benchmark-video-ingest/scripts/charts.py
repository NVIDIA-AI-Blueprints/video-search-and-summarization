#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Required charts, rendered from the normalized CSVs.

Charts are generated from ``csv/`` only -- never from ad hoc parsing of raw
logs -- so any finished run can be re-plotted without re-uploading anything.

Three charts are required, and only these three are produced::

    ingest-throughput-vs-concurrency.png          throughput (vmin/s) vs concurrency
    ingest-latency-p95-by-concurrency.png         p95 upload latency vs concurrency
    ingest-outcome-by-concurrency.png             upload outcomes per sweep point

Every one of the three has concurrency on the x-axis, and the ticks are exactly
the concurrencies that were swept -- if the run was ``[1, 5, 10]`` the axis
reads 1, 5, 10 and nothing else. An automatic integer locator would add 2, 3, 4
and so on, implying sweep points that were never measured.

p95 is the only latency series plotted. p50 and max stay in
``ingest_summary.csv`` and in ``summary.md``; putting three near-parallel lines
on one axis made the chart harder to read without adding a decision anyone
makes from it.

Axis labels, figure size, and dpi match ``chart_throughput_vs_concurrency.py``
in ``vss_ingest_perf`` so a Phase 1 chart can be laid beside a harness chart.
"""

from __future__ import annotations

import argparse
import csv
import math
from pathlib import Path
import sys

FIGSIZE = (9, 5.5)
DPI = 150

# Ordered smallest class first, so a chart stays comparable across runs even
# when a run is missing a class. Colour-blind-safe and distinguishable in print.
CLASS_ORDER = ("50MB", "500MB", "2GB", "10GB")
CLASS_COLOURS = {
    "50MB": "#1f77b4",
    "500MB": "#ff7f0e",
    "2GB": "#2ca02c",
    "10GB": "#d62728",
}
OUTCOME_ORDER = ("confirmed", "unconfirmed", "failed", "timed_out")
OUTCOME_COLOURS = {
    "confirmed": "#2ca02c",
    "unconfirmed": "#ff7f0e",
    "failed": "#d62728",
    "timed_out": "#7f7f7f",
}
#: The single latency series plotted. p50 and max remain in the CSVs.
LATENCY_COLUMN = "p95_latency_sec"
LATENCY_LABEL = "p95"
LATENCY_COLOUR = "#ff7f0e"


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def as_float(value: object, default: float = 0.0) -> float:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return default


def as_int(value: object, default: int = 0) -> int:
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return default


def sorted_classes(rows: list[dict[str, str]]) -> list[str]:
    present = {row.get("video_class", "") for row in rows}
    ordered = [c for c in CLASS_ORDER if c in present]
    return ordered + sorted(present - set(ordered) - {""})


def point_is_valid(row: dict) -> bool:
    """A blank result_valid means the signal could not judge; treat as valid."""
    return str(row.get("result_valid", "")).strip().lower() != "false"


def split_valid(series_rows):
    """Return (valid_rows, invalid_rows) preserving order."""
    valid = [r for r in series_rows if point_is_valid(r)]
    invalid = [r for r in series_rows if not point_is_valid(r)]
    return valid, invalid


def swept_concurrencies(rows: list[dict[str, str]]) -> list[int]:
    """The concurrencies actually measured, ascending and de-duplicated."""
    return sorted({as_int(r["concurrency"]) for r in rows if r.get("concurrency")})


def _finish(
    axes, figure, output: Path, title: str, xlabel: str, ylabel: str, *, xticks=None
) -> Path:
    if xticks:
        # The x-axis carries exactly the swept concurrencies. A generic integer
        # locator fills in 2, 3, 4 ... between 1 and 5, which reads as sweep
        # points that were never run.
        axes.set_xticks(list(xticks))
        axes.set_xticklabels([str(t) for t in xticks])
        span = max(xticks) - min(xticks)
        pad = max(0.5, span * 0.05)
        axes.set_xlim(min(xticks) - pad, max(xticks) + pad)
    axes.set_title(title)
    axes.set_xlabel(xlabel)
    axes.set_ylabel(ylabel)
    axes.grid(True, alpha=0.3, linestyle=":")
    axes.spines["top"].set_visible(False)
    axes.spines["right"].set_visible(False)
    handles, _labels = axes.get_legend_handles_labels()
    if handles:
        axes.legend(frameon=False)
    output.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(output, dpi=DPI, bbox_inches="tight")
    return output


def chart_throughput_vs_concurrency(plt, rows, output_dir: Path) -> Path:
    """video_min_per_sec against concurrency, one series per class.

    Mirrors the harness chart of the same name.
    """
    figure, axes = plt.subplots(figsize=FIGSIZE)
    any_invalid = False
    for video_class in sorted_classes(rows):
        class_rows = [r for r in rows if r.get("video_class") == video_class]
        valid, invalid = split_valid(class_rows)
        colour = CLASS_COLOURS.get(video_class, "#9467bd")
        # Blank throughput means the timing window could not be measured;
        # plotting it as zero would invent a measurement.
        series = sorted(
            (as_int(r["concurrency"]), throughput)
            for r in valid
            if math.isfinite(throughput := as_float(r.get("video_min_per_sec"), math.nan))
        )
        if series:
            axes.plot(
                [x for x, _ in series],
                [y for _, y in series],
                marker="o",
                markersize=7,
                color=colour,
                label=video_class,
            )
        # Invalid points are shown but never joined to the line: their
        # throughput was computed over streams that did not finish, so
        # connecting them would draw a scaling curve that does not exist.
        bad = sorted(
            (as_int(r["concurrency"]), throughput)
            for r in invalid
            if math.isfinite(throughput := as_float(r.get("video_min_per_sec"), math.nan))
        )
        if bad:
            any_invalid = True
            axes.plot(
                [x for x, _ in bad],
                [y for _, y in bad],
                linestyle="none",
                marker="o",
                markersize=8,
                markerfacecolor="none",
                markeredgecolor=colour,
                markeredgewidth=1.6,
            )
    if any_invalid:
        axes.plot(
            [], [], linestyle="none", marker="o", markersize=8,
            markerfacecolor="none", markeredgecolor="#555555", markeredgewidth=1.6,
            label="invalid (result_valid=False)",
        )
    return _finish(
        axes,
        figure,
        output_dir / "ingest-throughput-vs-concurrency.png",
        "Client-observed ingest throughput vs concurrency",
        "Concurrency",
        "Throughput (vmin / sec)",
        xticks=swept_concurrencies(rows),
    )


def chart_latency_p95(plt, rows, output_dir: Path) -> Path:
    """p95 upload latency against concurrency, one line per class.

    p95 alone: it is the number a capacity decision is made on, and plotting
    p50 and max beside it added two near-parallel lines nobody reads. Both
    remain in ``ingest_summary.csv`` and in the summary table.

    The percentile is taken across the uploads inside one point, so it measures
    contention rather than differences between videos.
    """
    figure, axes = plt.subplots(figsize=FIGSIZE)
    classes = sorted_classes(rows)
    multi_class = len(classes) > 1
    for video_class in classes:
        class_rows = [r for r in rows if r.get("video_class") == video_class]
        series = sorted(
            (as_int(r["concurrency"]), as_float(r[LATENCY_COLUMN])) for r in class_rows
        )
        if not series:
            continue
        axes.plot(
            [x for x, _ in series],
            [y for _, y in series],
            marker="o",
            markersize=6,
            color=CLASS_COLOURS.get(video_class, "#9467bd") if multi_class else LATENCY_COLOUR,
            linestyle="-",
            label=f"{video_class} {LATENCY_LABEL}" if multi_class else LATENCY_LABEL,
        )
    return _finish(
        axes,
        figure,
        output_dir / "ingest-latency-p95-by-concurrency.png",
        "Upload latency (p95) by concurrency",
        "Concurrency",
        "Latency (s)",
        xticks=swept_concurrencies(rows),
    )


def chart_outcome_by_concurrency(plt, request_rows, output_dir: Path) -> Path:
    """Confirmed, unconfirmed, failed, and timed-out counts per sweep point."""
    keys = sorted(
        {(r.get("video_class", ""), as_int(r["concurrency"])) for r in request_rows},
        key=lambda k: (CLASS_ORDER.index(k[0]) if k[0] in CLASS_ORDER else 99, k[1]),
    )
    labels = [f"{video_class}\nc{concurrency}" for video_class, concurrency in keys]

    figure, axes = plt.subplots(figsize=FIGSIZE)
    bottoms = [0.0] * len(keys)
    for outcome in OUTCOME_ORDER:
        counts = [
            sum(
                1
                for r in request_rows
                if r.get("video_class") == video_class
                and as_int(r["concurrency"]) == concurrency
                and r.get("outcome") == outcome
            )
            for video_class, concurrency in keys
        ]
        if not any(counts):
            continue
        axes.bar(
            range(len(keys)),
            counts,
            bottom=bottoms,
            color=OUTCOME_COLOURS[outcome],
            label=outcome,
        )
        bottoms = [b + c for b, c in zip(bottoms, counts)]
    axes.set_xticks(range(len(keys)))
    axes.set_xticklabels(labels)
    return _finish(
        axes,
        figure,
        output_dir / "ingest-outcome-by-concurrency.png",
        "Upload outcomes by sweep point",
        "Sweep point",
        "Uploads",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Render the required VSS ingest benchmark charts.")
    parser.add_argument("--results-dir", type=Path, required=True)
    # Accepted and ignored. Both flags belonged to charts this script no longer
    # draws; they stay so an existing command line does not fail, and
    # --uplink-mbps is still meaningful to summarize.py.
    parser.add_argument("--fixed-concurrency", type=int, default=None, help=argparse.SUPPRESS)
    parser.add_argument("--uplink-mbps", type=float, default=None, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("ERROR  matplotlib is required: pip install -r scripts/requirements.txt")
        return 2

    csv_dir = args.results_dir / "csv"
    summary_rows = read_csv(csv_dir / "ingest_summary.csv")
    request_rows = read_csv(csv_dir / "ingest_requests.csv")
    if not summary_rows:
        print(f"ERROR  no ingest_summary.csv under {csv_dir}")
        return 2

    output_dir = args.results_dir / "visualizations"
    if args.fixed_concurrency is not None or args.uplink_mbps is not None:
        print(
            "NOTE   --fixed-concurrency and --uplink-mbps no longer affect any chart; "
            "pass --uplink-mbps to summarize.py instead."
        )
    written = [
        chart_throughput_vs_concurrency(plt, summary_rows, output_dir),
        chart_latency_p95(plt, summary_rows, output_dir),
    ]
    if request_rows:
        written.append(chart_outcome_by_concurrency(plt, request_rows, output_dir))
    else:
        print("WARN   no ingest_requests.csv; skipping ingest-outcome-by-concurrency.png")

    for path in written:
        print(f"Wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
