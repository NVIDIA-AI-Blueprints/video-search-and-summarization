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

"""Benchmark visualization: generates plots from benchmark_summary.csv."""
import logging
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as ticker

logger = logging.getLogger(__name__)

COLORS = ["#4C72B0", "#DD8452", "#55A868", "#C44E52", "#8172B3", "#937860"]


def generate_plots(csv_path: Path, output_dir: Path) -> list[Path]:
    """Read benchmark CSV and save all plots. Returns list of saved file paths."""
    df = pd.read_csv(csv_path)
    df = df[df["status"] == "success"].copy()
    if df.empty:
        logger.warning("No successful runs in CSV, skipping plots")
        return []

    output_dir.mkdir(parents=True, exist_ok=True)
    saved = []

    saved.append(_plot_focal_per_run(df, output_dir))
    saved.append(_plot_focal_distribution(df, output_dir))
    saved.append(_plot_runtime(df, output_dir))
    saved.append(_plot_summary_bar(df, output_dir))

    logger.info(f"Saved {len(saved)} benchmark plots to {output_dir}")
    return saved


def _plot_focal_per_run(df: pd.DataFrame, output_dir: Path) -> Path:
    """Focal length per run, one line per model."""
    fig, ax = plt.subplots(figsize=(10, 5))
    models = df["model"].unique()

    for i, model in enumerate(models):
        sub = df[df["model"] == model]
        ax.plot(sub["run"], sub["focal_length_px"], "o-",
                color=COLORS[i % len(COLORS)], label=model, markersize=4, linewidth=1.2)

    ax.set_xlabel("Run #")
    ax.set_ylabel("Focal Length (px)")
    ax.set_title("Focal Length per Run")
    ax.legend()
    ax.xaxis.set_major_locator(ticker.MaxNLocator(integer=True))
    ax.grid(True, alpha=0.3)
    fig.tight_layout()

    path = output_dir / "plot_focal_per_run.png"
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return path


def _plot_focal_distribution(df: pd.DataFrame, output_dir: Path) -> Path:
    """Box + strip plot of focal length per model."""
    models = df["model"].unique()
    fig, ax = plt.subplots(figsize=(max(6, len(models) * 2.5), 5))

    data = [df[df["model"] == m]["focal_length_px"].values for m in models]
    bp = ax.boxplot(data, labels=models, patch_artist=True, widths=0.5)
    for i, patch in enumerate(bp["boxes"]):
        patch.set_facecolor(COLORS[i % len(COLORS)])
        patch.set_alpha(0.6)

    for i, (m, vals) in enumerate(zip(models, data)):
        jitter = np.random.default_rng(42).uniform(-0.1, 0.1, len(vals))
        ax.scatter(np.full_like(vals, i + 1) + jitter, vals,
                   color=COLORS[i % len(COLORS)], s=20, alpha=0.7, zorder=3)

    ax.set_ylabel("Focal Length (px)")
    ax.set_title("Focal Length Distribution by Model")
    ax.grid(True, axis="y", alpha=0.3)
    fig.tight_layout()

    path = output_dir / "plot_focal_distribution.png"
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return path


def _plot_runtime(df: pd.DataFrame, output_dir: Path) -> Path:
    """Runtime box plot per model."""
    models = df["model"].unique()
    fig, ax = plt.subplots(figsize=(max(6, len(models) * 2.5), 5))

    data = [df[df["model"] == m]["runtime_ms"].astype(float).values for m in models]
    bp = ax.boxplot(data, labels=models, patch_artist=True, widths=0.5)
    for i, patch in enumerate(bp["boxes"]):
        patch.set_facecolor(COLORS[i % len(COLORS)])
        patch.set_alpha(0.6)

    ax.set_ylabel("Runtime (ms)")
    ax.set_title("Inference Runtime by Model")
    ax.grid(True, axis="y", alpha=0.3)
    fig.tight_layout()

    path = output_dir / "plot_runtime.png"
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return path


def _plot_summary_bar(df: pd.DataFrame, output_dir: Path) -> Path:
    """Bar chart: median focal per model with std error bars."""
    models = df["model"].unique()
    medians, stds = [], []
    for m in models:
        vals = df[df["model"] == m]["focal_length_px"].values
        medians.append(float(np.median(vals)))
        stds.append(float(np.std(vals)))

    fig, ax = plt.subplots(figsize=(max(6, len(models) * 2), 5))
    x = np.arange(len(models))
    bars = ax.bar(x, medians, yerr=stds, capsize=6, width=0.5,
                  color=[COLORS[i % len(COLORS)] for i in range(len(models))],
                  alpha=0.8, edgecolor="black", linewidth=0.5)

    for bar, med, std in zip(bars, medians, stds):
        label = f"{med:.1f}"
        if std > 0:
            label += f"\n±{std:.1f}"
        ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + std + 2,
                label, ha="center", va="bottom", fontsize=9)

    ax.set_xticks(x)
    ax.set_xticklabels(models)
    ax.set_ylabel("Focal Length (px)")
    ax.set_title("Median Focal Length by Model (±std)")
    ax.grid(True, axis="y", alpha=0.3)
    fig.tight_layout()

    path = output_dir / "plot_focal_summary.png"
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return path
