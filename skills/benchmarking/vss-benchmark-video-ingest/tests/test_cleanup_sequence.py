# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Sweep transitions wait for downstream cleanup without changing upload metrics."""

from contextlib import redirect_stderr, redirect_stdout
import csv
from datetime import datetime, timedelta, timezone
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from config import ConfigError
from completion import ReadinessResult
from httpio import JsonResponse
import run
from test_cli_ingest import ack, compatibility, ready, video
from validate import ValidationResult
from vss_cli import CliResult


class Clock:
    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class CleanupSequenceTests(unittest.TestCase):
    def execute(self, root, *, warmup=0, mode="delayed", policy="always",
                upload_exit=0, confirmation=None, output=None):
        clock = Clock()
        events = []
        last_delete = 0.0

        def invoke(*args):
            nonlocal last_delete
            events.append((args[1], clock.now))
            if args[1] == "add":
                clock.sleep(1)
                if upload_exit:
                    return CliResult(upload_exit, {}, "Upload timeline wait failed")
                return ack(args[-1])
            if args[1] == "list":
                return CliResult(0, {"sensors": []})
            last_delete = clock.now
            if mode == "unconfirmed":
                return CliResult(0, {"confirmed": False, "recordings": "unconfirmed", "deleted": ["sensor"]})
            return CliResult(0, {"confirmed": True, "recordings": "removed", "deleted": ["storage"]})

        def search(*args, **kwargs):
            events.append(("es", clock.now))
            count = 1 if mode == "timeout" or clock.now - last_delete < 10 else 0
            return JsonResponse(200, {
                "timed_out": False,
                "_shards": {"total": 1, "successful": 1, "skipped": 0, "failed": 0},
                "hits": {"total": {"value": count * 2, "relation": "eq"}},
                "aggregations": {"pipelines": {"buckets": {
                    "rt_cv": {"doc_count": count}, "rt_embed": {"doc_count": count},
                }}},
            }, "", 0)

        cli = Mock(call=Mock(side_effect=invoke), command=("vss",), config_home="/fixture/cli")
        validation = ValidationResult(
            cli=cli, deployment={"base_url": "http://vss.test", "services": {"vst": {"url": "http://vss.test/vst"}}},
            elasticsearch_url="http://es.test", version_compatibility=compatibility(),
        )
        with (
            patch("run.validate", return_value=validation),
            patch("run.load_class", return_value=[video()]),
            patch("run.EsReadinessMonitor", return_value=Mock(confirm=Mock(
                return_value=confirmation or ready("2026-09-28T00:01:00Z")))),
            patch("upload.utc_now", side_effect=lambda: (
                datetime(2026, 9, 28, tzinfo=timezone.utc) + timedelta(seconds=clock.now)
            ).isoformat()),
            patch("cleanup.request_json", side_effect=search),
            patch("cleanup.time", clock), patch("run.time", clock), patch("upload.time", clock),
            redirect_stdout(output if output is not None else io.StringIO()),
        ):
            code = run.main([
                "--no-config", "--corpus", str(root), "--results-dir", str(root),
                "--profile", "custom", "--video-class", "50MB", "--concurrency", "1", "--concurrency", "2",
                "--warmup", str(warmup), "--stagger-sec", "0", "--cleanup", policy,
                "--cleanup-timeout", "60", "--cleanup-settle-sec", "10",
            ])
        return code, events

    def test_warmup_and_each_point_wait_without_inflating_measurements(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            code, events = self.execute(root, warmup=1)
            self.assertEqual(code, 0)
            uploads = [at for kind, at in events if kind == "add"]
            self.assertEqual(len(uploads), 4)
            # Each prior cleanup needs 10s to disappear and 10s to remain absent.
            self.assertGreaterEqual(uploads[1] - uploads[0], 21)
            self.assertGreaterEqual(uploads[2] - uploads[1], 21)
            with (root / "csv/ingest_summary.csv").open() as handle:
                rows = list(csv.DictReader(handle))
            self.assertTrue(all(float(row["wall_clock_min"]) < 0.1 for row in rows))
            with (root / "csv/ingest_requests.csv").open() as handle:
                requests = list(csv.DictReader(handle))
            self.assertTrue(all(float(row["latency_sec"]) < 10 for row in requests))
            metadata = json.loads((root / "run-metadata.json").read_text())
            self.assertEqual(metadata["cleanup_totals"]["deleted"], 3)
            self.assertEqual(metadata["warmup_cleanup"]["deleted"], 1)
            self.assertEqual(metadata["cleanup_verification"]["settle_sec"], 10)
            self.assertEqual(len(metadata["cleanup_checks"]), 3)
            for check in metadata["cleanup_checks"]:
                self.assertEqual(check["es_wait"]["status"], "confirmed")
                self.assertGreaterEqual(check["es_wait"]["elapsed_sec"], 20)
                self.assertGreater(check["es_wait"]["polls"], 1)

    def test_incomplete_cli_or_es_cleanup_stops_later_points(self):
        for mode in ("unconfirmed", "timeout"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                code, events = self.execute(root, mode=mode)
                self.assertEqual(code, 1)
                self.assertEqual(sum(kind == "add" for kind, _ in events), 1)
                metadata = json.loads((root / "run-metadata.json").read_text())
                self.assertEqual(metadata["sweep_points_completed"], 1)
                self.assertEqual(metadata["cleanup_totals"]["failed"], 1)
                self.assertEqual(metadata["cleanup_totals"]["deleted"], 0)
                self.assertIn("Cleanup incomplete", metadata["stop_reason"])
                self.assertTrue((root / "visualizations/summary.json").is_file())
                if mode == "unconfirmed":
                    self.assertFalse(any(kind == "es" for kind, _ in events))

    def test_failed_warmup_cleanup_prevents_first_point(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            code, events = self.execute(root, warmup=1, mode="timeout")
            self.assertEqual(code, 1)
            self.assertEqual(sum(kind == "add" for kind, _ in events), 1)
            self.assertTrue((root / "raw/warmup_details.jsonl").is_file())
            metadata = json.loads((root / "run-metadata.json").read_text())
            self.assertEqual(metadata["sweep_points_completed"], 0)
            self.assertEqual(metadata["vss_service_url"], "http://vss.test")
            self.assertEqual(metadata["es_readiness"]["elasticsearch_url"], "http://es.test")
            self.assertIn("Warmup cleanup failed", metadata["stop_reason"])
            self.assertEqual(metadata["cleanup_checks"][0]["es_wait"]["status"], "incomplete")
            self.assertEqual(metadata["warmup_cleanup"]["failed"], 1)

    def test_warmup_reports_ingestion_diagnostic_without_inventing_cleanup_failure(self):
        for policy in ("always", "on-success", "never"):
            with self.subTest(policy=policy), tempfile.TemporaryDirectory() as directory:
                root, output = Path(directory), io.StringIO()
                code, events = self.execute(
                    root, warmup=1, policy=policy, output=output,
                    confirmation=ReadinessResult(False, "unconfirmed", "Raw frames below expected count"),
                )
                self.assertEqual(code, 1)
                self.assertEqual(sum(kind == "add" for kind, _ in events), 1)
                self.assertIn("ERROR  Warmup ingestion 1", output.getvalue())
                self.assertIn("unconfirmed; Raw frames below expected count", output.getvalue())
                self.assertNotIn("ERROR  Warmup cleanup", output.getvalue())
                self.assertIn(str(root / "raw/warmup_details.jsonl"), output.getvalue())

    def test_warmup_reports_upload_failure_and_unresolved_cleanup_identity(self):
        for exit_code, outcome in ((3, "failed"), (7, "timed_out")):
            with self.subTest(exit_code=exit_code), tempfile.TemporaryDirectory() as directory:
                root, output = Path(directory), io.StringIO()
                code, events = self.execute(root, warmup=1, upload_exit=exit_code, output=output)
                self.assertEqual(code, 1)
                self.assertEqual(sum(kind == "add" for kind, _ in events), 1)
                self.assertFalse(any(kind == "delete" for kind, _ in events))
                self.assertIn(f": {outcome};", output.getvalue())
                self.assertIn("Upload timeline wait failed", output.getvalue())
                self.assertIn("ERROR  Warmup cleanup identity unresolved for 1 upload(s)", output.getvalue())
                self.assertIn("Cannot resolve cleanup identity", output.getvalue())
                self.assertIn(str(root / "raw/upload_ledger.jsonl"), output.getvalue())
                self.assertNotIn("ERROR  Warmup cleanup failed", output.getvalue())

    def test_warmup_reports_cleanup_failure_with_its_saved_diagnostic(self):
        for mode in ("unconfirmed", "timeout"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                root, output = Path(directory), io.StringIO()
                code, events = self.execute(root, warmup=1, mode=mode, output=output)
                self.assertEqual(code, 1)
                self.assertEqual(sum(kind == "add" for kind, _ in events), 1)
                self.assertIn("ERROR  Warmup cleanup failed for 1 upload(s)", output.getvalue())
                self.assertNotIn("ERROR  Warmup ingestion", output.getvalue())
                self.assertNotIn("ERROR  Warmup cleanup identity", output.getvalue())
                record = json.loads((root / "raw/warmup_details.jsonl").read_text())
                self.assertTrue(record["cleanup_detail"])
                self.assertIn(record["cleanup_detail"], output.getvalue())

    def test_warmup_reports_ingestion_and_cleanup_failures_together(self):
        with tempfile.TemporaryDirectory() as directory:
            root, output = Path(directory), io.StringIO()
            code, events = self.execute(
                root, warmup=1, mode="unconfirmed", output=output,
                confirmation=ReadinessResult(False, "unconfirmed", ""),
            )
            self.assertEqual(code, 1)
            self.assertEqual(sum(kind == "add" for kind, _ in events), 1)
            self.assertIn("ERROR  Warmup ingestion 1", output.getvalue())
            self.assertIn("unconfirmed; ingestion was not confirmed", output.getvalue())
            self.assertIn("ERROR  Warmup cleanup failed", output.getvalue())
            self.assertIn("Stopping before the measured sweep", output.getvalue())

    def test_never_policy_performs_no_deletion_or_es_cleanup_reads(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            code, events = self.execute(root, policy="never")
            self.assertEqual(code, 0)
            self.assertEqual([kind for kind, _ in events], ["add", "add", "add"])
            summary = json.loads((root / "visualizations/summary.json").read_text())
            self.assertTrue(any("skipped deletion and cleanup verification" in step for step in summary["execution_steps"]))


class CleanupConfigurationTests(unittest.TestCase):
    def test_shipped_config_and_no_config_use_the_same_execution_defaults(self):
        shipped = run.parse_args(["--config", str(Path(__file__).resolve().parents[1] / "config.yml")])
        bare = run.parse_args(["--no-config"])
        for key in ("warmup", "results_dir", "cleanup", "cleanup_timeout", "cleanup_settle_sec",
                    "readiness_poll_interval", "raw_drop_grace_sec", "stagger_sec"):
            with self.subTest(key=key):
                self.assertEqual(getattr(shipped, key), getattr(bare, key))

    def test_removed_ratio_settings_fail_before_upload(self):
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            run.parse_args(["--no-config", "--raw-completion-ratio", "0.95"])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.yml"
            path.write_text("es_readiness:\n  raw_completion_ratio: 0.95\n")
            with self.assertRaisesRegex(ConfigError, "full expected raw-frame count"):
                run.parse_args(["--config", str(path)])

    def test_cleanup_config_defaults_and_flag_precedence(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.yml"
            path.write_text("cleanup:\n  timeout_sec: 90\n  settle_sec: 15\n")
            args = run.parse_args(["--config", str(path), "--cleanup-timeout", "120"])
            self.assertEqual((args.cleanup_timeout, args.cleanup_settle_sec), (120, 15))

    def test_invalid_cleanup_deadlines_are_rejected(self):
        for timeout, settle in (("nan", "10"), ("60", "inf"), ("30", "30"), ("60", "0"), ("-1", "10")):
            with self.subTest(timeout=timeout, settle=settle), redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    run.parse_args(["--no-config", "--cleanup-timeout", timeout, "--cleanup-settle-sec", settle])


if __name__ == "__main__":
    unittest.main()
