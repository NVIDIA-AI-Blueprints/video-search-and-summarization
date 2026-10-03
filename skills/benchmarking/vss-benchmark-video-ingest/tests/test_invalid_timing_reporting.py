# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Unavailable throughput must stay unavailable in saved reports and charts."""

from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from artifacts import write_summary_csv
import charts
import summarize


def row(concurrency, throughput, valid=True):
    return {
        "video_class": "50MB",
        "concurrency": concurrency,
        "upload_count": concurrency,
        "success_count": concurrency,
        "failure_count": 0,
        "success_rate_pct": 100.0,
        "result_valid": valid,
        "result_invalid_reason": "" if valid else "confirmed upload 1 has a nonpositive timestamp window",
        "min_raw_completion_ratio": 1.0,
        "total_video_duration_min": float(concurrency),
        "total_video_size_gb": 0.05 * concurrency,
        "wall_clock_min": 1.0,
        "video_min_per_sec": throughput,
        "success_window_sec": 10.0 if throughput != "" else "",
        "aggregate_mb_per_sec": 1.0,
        "p50_latency_sec": 12.0,
        "p95_latency_sec": 12.0,
        "max_latency_sec": 12.0,
        "cli_exit_statuses": f"0:{concurrency}",
    }


class InvalidTimingReportingTests(unittest.TestCase):
    def tearDown(self):
        plt.close("all")

    def test_chart_skips_unavailable_values_and_preserves_numeric_invalid_markers(self):
        rows = [row(1, 0.1), row(5, 0.2, False)]
        rows += [row(10 + i, value, False) for i, value in enumerate(("", "nan", "inf", "-inf"))]
        rows.append(row(20, ""))  # A missing value must not become zero even in older artifacts.
        with tempfile.TemporaryDirectory() as directory:
            output = charts.chart_throughput_vs_concurrency(plt, rows, Path(directory))
            self.assertTrue(output.is_file())
            axes = plt.gcf().axes[0]
            measured = [line for line in axes.lines if len(line.get_xdata())]
            self.assertEqual(len(measured), 2)
            self.assertEqual(list(measured[0].get_xdata()), [1])
            self.assertEqual(list(measured[0].get_ydata()), [0.1])
            self.assertEqual(list(measured[1].get_xdata()), [5])
            self.assertEqual(list(measured[1].get_ydata()), [0.2])
            self.assertEqual(measured[1].get_linestyle(), "None")
            self.assertEqual(measured[1].get_markerfacecolor(), "none")
            self.assertEqual(list(axes.get_xticks()), [1, 5, 10, 11, 12, 13, 20])

    def test_chart_with_only_invalid_timing_has_no_invented_zero_point(self):
        with tempfile.TemporaryDirectory() as directory:
            charts.chart_throughput_vs_concurrency(plt, [row(1, "", False)], Path(directory))
            axes = plt.gcf().axes[0]
            self.assertFalse(any(len(line.get_xdata()) for line in axes.lines))
            self.assertEqual(list(axes.get_xticks()), [1])

    def saved_report(self, rows):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_summary_csv(root / "csv" / "ingest_summary.csv", rows)
            with redirect_stdout(io.StringIO()):
                self.assertEqual(summarize.main(["--results-dir", str(root)]), 0)
            summary = json.loads((root / "visualizations" / "summary.json").read_text())
            markdown = (root / "visualizations" / "summary.md").read_text()
            return summary, markdown

    def test_all_invalid_points_save_null_peak_and_explicit_unavailable_prose(self):
        for throughput in ("", 0.4):
            with self.subTest(throughput=throughput):
                summary, markdown = self.saved_report([row(1, throughput, False)])
                self.assertIsNone(summary["peak_video_min_per_sec"])
                self.assertEqual(summary["peak_point"], "")
                self.assertEqual(summary["valid_sweep_points"], 0)
                self.assertEqual(summary["invalid_sweep_points"], 1)
                self.assertIn("**unavailable** (no valid sweep points)", markdown)
                self.assertNotIn("**0.0 video-min/s**", markdown)
                self.assertIn("nonpositive timestamp window", markdown)
                self.assertIn("completion timestamps", markdown)

    def test_mixed_points_preserve_valid_peak_and_blank_timing(self):
        summary, markdown = self.saved_report([row(1, 0.1), row(5, "", False), row(10, 10.0, False)])
        self.assertEqual(summary["peak_video_min_per_sec"], 0.1)
        self.assertEqual(summary["peak_point"], "50MB @ c1")
        self.assertEqual(summary["valid_sweep_points"], 1)
        self.assertEqual(summary["invalid_sweep_points"], 2)
        self.assertEqual(summary["sweep_points_detail"][1]["video_min_per_sec"], "")
        self.assertIn("**0.1 video-min/s** at 50MB @ c1", markdown)
        self.assertIn("2 excluded", markdown)
        self.assertIn("points with unavailable throughput have no throughput marker", markdown)


if __name__ == "__main__":
    unittest.main()
