# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Throughput requires a usable client-to-Elasticsearch timestamp window."""

from __future__ import annotations

import csv
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from artifacts import summarize_point, write_summary_csv
from upload import UploadRecord


def confirmed_record(**overrides) -> UploadRecord:
    fields = {
        "worker_index": 0,
        "upload_sequence": 1,
        "video_id": "clip-1",
        "video_class": "custom",
        "concurrency": 1,
        "upload_filename": "run-00001.mp4",
        "bytes": 50_000_000,
        "duration_sec": 60.0,
        "request_sent_at": "2026-10-01T00:00:00Z",
        "ingest_confirmed_at": "2026-10-01T00:00:10Z",
        "latency_sec": 12.0,
        "es_indexed_latency_sec": 10.0,
        "http_status": "",
        "outcome": "confirmed",
        "transmitted_bytes": 50_000_000,
        "readiness_metrics": {"raw_completion_ratio": 1.0},
    }
    fields.update(overrides)
    return UploadRecord(**fields)


def summary(*records: UploadRecord) -> dict:
    return summarize_point(video_class="custom", concurrency=len(records), records=records, wall_clock_sec=50.0)


class ArtifactTimingTests(unittest.TestCase):
    def assert_timing_invalid(self, row: dict) -> None:
        self.assertFalse(row["result_valid"])
        self.assertEqual(row["video_min_per_sec"], "")
        self.assertEqual(row["success_window_sec"], "")
        self.assertIn("confirmed upload", row["result_invalid_reason"])

    def test_zero_and_negative_windows_do_not_report_throughput(self):
        for completed in (
            "2026-09-30T23:59:59Z",
            "2026-10-01T00:00:00Z",
            "2026-10-01T02:00:00+02:00",  # same instant with another offset
        ):
            with self.subTest(completed=completed):
                row = summary(confirmed_record(ingest_confirmed_at=completed))
                self.assert_timing_invalid(row)
                self.assertIn("nonpositive timestamp window", row["result_invalid_reason"])
                self.assertIn("clock synchronization", row["result_invalid_reason"])
                self.assertEqual(row["success_count"], 1)
                self.assertEqual(row["p50_latency_sec"], 12.0)

    def test_missing_malformed_and_timezone_naive_timestamps_are_invalid(self):
        for field in ("request_sent_at", "ingest_confirmed_at"):
            for value in ("", None, "not-a-timestamp", "2026-10-01T00:00:05", "2026-10-01", 123):
                with self.subTest(field=field, value=value):
                    row = summary(confirmed_record(**{field: value}))
                    self.assert_timing_invalid(row)
                    self.assertIn(field, row["result_invalid_reason"])
                    self.assertIn("missing, malformed, or timezone-naive", row["result_invalid_reason"])

    def test_positive_aggregate_does_not_hide_an_invalid_upload(self):
        good = confirmed_record()
        for bad_fields in (
            {"request_sent_at": "2026-10-01T00:00:20Z", "ingest_confirmed_at": "2026-10-01T00:00:15Z"},
            {"request_sent_at": "2026-10-01T00:00:15Z", "ingest_confirmed_at": "2026-10-01T00:00:15Z"},
            {"request_sent_at": "", "ingest_confirmed_at": "2026-10-01T00:00:15Z"},
        ):
            with self.subTest(bad_fields=bad_fields):
                bad = confirmed_record(upload_sequence=2, **bad_fields)
                for records in ((good, bad), (bad, good)):
                    row = summary(*records)
                    self.assert_timing_invalid(row)
                    self.assertIn("upload 2", row["result_invalid_reason"])
                    self.assertEqual(row["success_count"], 2)

    def test_valid_windows_with_different_offsets_preserve_metric_math(self):
        first = confirmed_record(
            request_sent_at="2026-10-01T05:30:00+05:30",
            ingest_confirmed_at="2026-10-01T00:00:15Z",
            latency_sec=20.0,
        )
        second = confirmed_record(
            upload_sequence=2,
            duration_sec=120.0,
            request_sent_at="2026-10-01T02:00:10+02:00",
            ingest_confirmed_at="2026-09-30T20:00:40-04:00",
            latency_sec=40.0,
        )
        row = summary(second, first)
        self.assertTrue(row["result_valid"])
        self.assertEqual(row["result_invalid_reason"], "")
        self.assertEqual(row["total_video_duration_min"], 3.0)
        self.assertEqual(row["success_window_sec"], 40.0)
        self.assertEqual(row["video_min_per_sec"], 0.075)
        self.assertEqual(row["aggregate_mb_per_sec"], 2.0)
        self.assertEqual(row["p50_latency_sec"], 30.0)
        self.assertEqual(row["p95_latency_sec"], 39.0)
        self.assertEqual(row["max_latency_sec"], 40.0)

    def test_invalid_timing_remains_blank_in_csv_and_keeps_other_invalid_reasons(self):
        invalid = confirmed_record(
            ingest_confirmed_at="2026-10-01T00:00:00Z",
            readiness_metrics={"raw_completion_ratio": 0.5},
        )
        row = summary(invalid)
        self.assertIn("slowest stream indexed 50.0%", row["result_invalid_reason"])
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "summary.csv"
            write_summary_csv(path, [row])
            with path.open(newline="", encoding="utf-8") as handle:
                exported = next(csv.DictReader(handle))
        self.assertEqual(exported["result_valid"], "False")
        self.assertEqual(exported["video_min_per_sec"], "")
        self.assertEqual(exported["success_window_sec"], "")
        self.assertIn("nonpositive timestamp window", exported["result_invalid_reason"])

    def test_no_confirmed_uploads_keep_zero_throughput_convention(self):
        row = summary(confirmed_record(outcome="failed", ingest_confirmed_at=""))
        self.assertFalse(row["result_valid"])
        self.assertIn("no successful uploads", row["result_invalid_reason"])
        self.assertEqual(row["video_min_per_sec"], 0.0)
        self.assertEqual(row["success_window_sec"], 50.0)


if __name__ == "__main__":
    unittest.main()
