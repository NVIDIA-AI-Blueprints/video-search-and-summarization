# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Behavioral tests: subprocess contract, concurrent identities, and ES accounting."""

from __future__ import annotations

import argparse
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from artifacts import build_error_rows, summarize_point
from completion import EsReadinessMonitor, ReadinessResult
from config import ConfigError, load_config
from corpus import VideoItem
from es_readiness import EsReadinessConfig, build_query, expected_counts
from httpio import JsonResponse
import run
from upload import delete_asset, upload_one
from validate import ValidationResult
from vss_cli import CliResult, VssCli


def video(path="/tmp/clip with spaces; touch OOPS.mp4"):
    return VideoItem("clip", "50MB", path, 50000000, 60.0, 30.0, 1920, 1080, "1920x1080", "h264", "mp4", "video/mp4")


def ack(sensor="real-uuid"):
    return CliResult(0, {"added": True, "type": "video", "sensor_id": sensor})


def compatibility():
    return {
        "status": "compatible", "skill_version": "v3.3.0", "deployed_vss_version": "3.3.0+tree.fixture",
        "requires_vss": "==3.3.0", "version_url": "http://vss.test/api/v1/version",
        "checked_at_utc": "2026-09-28T00:00:00+00:00",
    }


def ready(completed_at=""):
    return ReadinessResult(
        True,
        "confirmed",
        "ready",
        polls=2,
        completed_at=completed_at,
        metrics={
            "expected_frames": 1785,
            "es_frame_count": 1800,
            "expected_chunks": 12,
            "es_chunk_count": 12,
            "raw_completion_ratio": 1.0,
        },
    )


class UploadTests(unittest.TestCase):
    def send(self, response, monitor=None):
        cli = Mock()
        cli.call.return_value = response
        monitor = monitor or Mock(confirm=Mock(return_value=ready()))
        record = upload_one(
            video(),
            worker_index=1,
            upload_sequence=1,
            concurrency=1,
            run_uuid="unique",
            cli=cli,
            readiness_monitor=monitor,
        )
        return record, cli, monitor

    def test_ack_is_followed_by_es_with_returned_uuid(self):
        record, cli, monitor = self.send(ack())
        self.assertEqual(record.outcome, "confirmed")
        self.assertEqual(record.cli_exit_code, 0)
        self.assertEqual(record.http_status, "")
        self.assertGreaterEqual(record.latency_sec, record.cli_duration_sec)
        args = cli.call.call_args.args
        self.assertEqual(
            args,
            ("vios", "add", "--type", "video", str(Path(video().source_path).resolve()), "--name", "unique-00001.mp4"),
        )
        ctx = monitor.confirm.call_args.args[0]
        self.assertEqual(ctx.sensor_id, "real-uuid")
        self.assertEqual(ctx.camera_name, "unique-00001")

    def test_successful_upload_is_not_indexing_completion(self):
        record, _, _ = self.send(
            ack(), Mock(confirm=Mock(return_value=ReadinessResult(False, "unconfirmed", "no frames")))
        )
        self.assertEqual(record.outcome, "unconfirmed")
        self.assertEqual(record.transmitted_bytes, video().bytes)
        self.assertEqual(record.ingest_confirmed_at, "")
        summary = summarize_point(video_class="50MB", concurrency=1, records=[record], wall_clock_sec=10)
        self.assertFalse(summary["result_valid"])
        self.assertEqual(summary["success_count"], 0)

    def test_exit_codes_no_retry_no_readiness_and_no_invented_http(self):
        for code, outcome in [(2, "failed"), (3, "failed"), (4, "failed"), (7, "timed_out")]:
            with self.subTest(code=code):
                record, cli, monitor = self.send(CliResult(code, {}, "backend diagnostic"))
                self.assertEqual(record.outcome, outcome)
                self.assertEqual(record.cli_exit_code, code)
                self.assertEqual(record.http_status, "")
                self.assertEqual(record.transmitted_bytes, 0)
                cli.call.assert_called_once()
                monitor.confirm.assert_not_called()
                self.assertEqual(build_error_rows([record])[0]["cli_exit_code"], code)

    def test_no_sensor_id_does_not_invent_one(self):
        record, _, monitor = self.send(ack(""))
        self.assertEqual(record.outcome, "unconfirmed")
        monitor.confirm.assert_not_called()

    def test_es_transport_failure_preserves_upload(self):
        monitor = Mock(confirm=Mock(side_effect=OSError("ES offline")))
        record, _, _ = self.send(ack(), monitor)
        self.assertEqual(record.outcome, "unconfirmed")
        self.assertEqual(record.sensor_id, "real-uuid")
        self.assertIn("ES offline", record.error_detail)

    def test_malformed_success_not_confirmed(self):
        record, _, monitor = self.send(CliResult(0, {"error": "bad contract"}))
        self.assertEqual(record.outcome, "failed")
        monitor.confirm.assert_not_called()

    def test_cli_delete_only_returned_identity(self):
        cli = Mock(call=Mock(return_value=CliResult(0, {"deleted": True})))
        self.assertTrue(delete_asset(cli, "returned-uuid")[0])
        cli.call.assert_called_once_with("vios", "delete", "--type", "video", "--sensor", "returned-uuid")


class CliProcessTests(unittest.TestCase):
    def test_argument_boundaries_real_subprocess_and_config_isolation(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            project = root / "repo with spaces/services/agent"
            project.mkdir(parents=True)
            (project / "pyproject.toml").touch()
            fake_uv = root / "fake-uv"
            fake_uv.write_text(
                f'#!{sys.executable}\nimport sys, os, json\nprint(json.dumps({{"args": sys.argv[1:], "home": os.environ.get("VSS_CONFIG_HOME")}}))\n'
            )
            fake_uv.chmod(0o755)
            cli = VssCli(root / "repo with spaces", root / "private config", str(fake_uv))
            source = str(root / "video; touch SHOULD_NOT_EXIST.mp4")
            result = cli.call("vios", "add", source)
            self.assertEqual(result.body["args"][-3:], ["vios", "add", source])
            self.assertIn("--no-sync", result.body["args"])
            self.assertEqual(result.body["home"], str(root / "private config"))
            self.assertFalse((root / "SHOULD_NOT_EXIST.mp4").exists())

    def test_failed_process_json_does_not_override_exit(self):
        with tempfile.TemporaryDirectory() as d:
            project = Path(d) / "services/agent"
            project.mkdir(parents=True)
            (project / "pyproject.toml").touch()
            cli = VssCli(Path(d))
            with patch(
                "vss_cli.subprocess.run", return_value=subprocess.CompletedProcess([], 7, '{"added":true}', "timeout")
            ) as proc:
                self.assertEqual(cli.call("vios", "add", "clip.mp4").exit_code, 7)
                self.assertNotIn("shell", proc.call_args.kwargs)
                self.assertNotIn("timeout", proc.call_args.kwargs)


class SweepTests(unittest.TestCase):
    def test_concurrency_and_unique_names_with_worker_completion_wait(self):
        barrier = threading.Barrier(3)
        active = 0
        peak = 0
        lock = threading.Lock()
        calls = []

        def send(*args):
            nonlocal active, peak
            with lock:
                calls.append(args)
                active += 1
                peak = max(peak, active)
            return ack(args[-1].removesuffix(".mp4"))

        def confirm(ctx):
            nonlocal active
            barrier.wait(timeout=5)
            with lock:
                active -= 1
            return ready()

        args = argparse.Namespace(stagger_sec=0, max_ramp_sec=0, cli=Mock(call=Mock(side_effect=send)))
        records, _ = run.run_sweep_point(
            items=[video(), video()],
            video_class="50MB",
            concurrency=3,
            run_uuid=uuid.uuid4().hex,
            args=args,
            readiness_monitor=Mock(confirm=Mock(side_effect=confirm)),
        )
        self.assertEqual(len(records), 6)
        self.assertEqual(peak, 3)
        self.assertEqual(len({r.upload_filename for r in records}), 6)
        self.assertEqual(len({r.upload_sequence for r in records}), 6)
        for worker in (1, 2, 3):
            self.assertEqual(sum(r.worker_index == worker for r in records), 2)

    def test_full_runner_multiple_points_artifacts_and_cleanup(self):
        with tempfile.TemporaryDirectory() as d:
            cli = Mock()
            cli.command = ("uv", "run", "--no-sync", "vss")
            cli.config_home = "/configured/cli"
            cli.call.side_effect = lambda *args: ack(args[-1]) if args[1] == "add" else CliResult(0, {"deleted": True})
            validation = ValidationResult(
                cli=cli, deployment={"base_url": "http://vss.test", "services": {"vst": {"url": "http://vss.test/vst"}}}, elasticsearch_url="http://vss.test/elasticsearch",
                version_compatibility=compatibility(),
            )
            monitor = Mock(confirm=Mock(side_effect=lambda ctx: ready("2026-09-28T00:01:00Z")))
            with (
                patch("upload.utc_now", return_value="2026-09-28T00:00:00Z"),
                patch("run.validate", return_value=validation),
                patch("run.load_class", return_value=[video()]),
                patch("run.EsReadinessMonitor", return_value=monitor),
                redirect_stdout(io.StringIO()),
            ):
                code = run.main(
                    [
                        "--no-config",
                        "--corpus",
                        d,
                        "--profile",
                        "custom",
                        "--video-class",
                        "50MB",
                        "--concurrency",
                        "1",
                        "--concurrency",
                        "2",
                        "--warmup",
                        "0",
                        "--stagger-sec",
                        "0",
                        "--results-dir",
                        d,
                    ]
                )
            self.assertEqual(code, 0)
            details = [json.loads(s) for s in (Path(d) / "raw/upload_details.jsonl").read_text().splitlines()]
            self.assertEqual(len({r["upload_filename"] for r in details}), 3)
            self.assertTrue(all(r["cli_exit_code"] == 0 and r["http_status"] == "" for r in details))
            metadata = json.loads((Path(d) / "run-metadata.json").read_text())
            self.assertFalse(metadata["harness_comparable"])
            self.assertEqual(metadata["cleanup_totals"]["deleted"], 3)
            self.assertEqual(metadata["upload_flow"], "vss-cli")
            self.assertIs(metadata["es_readiness"]["expect_raw"], True)
            self.assertIs(metadata["es_readiness"]["expect_embed"], True)
            self.assertEqual(metadata["version_compatibility"], compatibility())
            from validate_artifacts import validate_artifacts

            self.assertEqual(validate_artifacts(Path(d)), [])
            written_summary = json.loads((Path(d) / "visualizations/summary.json").read_text())
            self.assertEqual(written_summary["version_compatibility"], compatibility())
            self.assertEqual(sum(c.args[1] == "add" for c in cli.call.call_args_list), 3)
            from summarize import build_summary, render_markdown

            summary = build_summary(Path(d), None)
            self.assertFalse(summary["harness_comparable"])
            self.assertIn("Harness-comparable: **no**", render_markdown(summary))

    def test_invalid_timing_fails_run_after_cleanup_and_preserves_evidence(self):
        import csv

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cli = Mock(call=Mock(return_value=ack()))
            cli.command = ("/installed/vss",)
            cli.config_home = "/configured/cli"
            validation = ValidationResult(
                cli=cli,
                deployment={"base_url": "http://vss.test", "services": {"vst": {"url": "http://vss.test/vst"}}},
                elasticsearch_url="http://vss.test/elasticsearch", version_compatibility=compatibility(),
            )
            with (
                patch("upload.utc_now", return_value="2026-09-28T00:01:00Z"),
                patch("run.validate", return_value=validation),
                patch("run.load_class", return_value=[video()]),
                patch("run.EsReadinessMonitor", return_value=Mock(confirm=Mock(return_value=ready("2026-09-28T00:00:00Z")))),
                redirect_stdout(io.StringIO()),
            ):
                code = run.main([
                    "--no-config", "--corpus", directory, "--profile", "smoke", "--warmup", "0",
                    "--results-dir", directory,
                ])
            self.assertEqual(code, 1)
            with (root / "csv/ingest_summary.csv").open(newline="") as handle:
                row = next(csv.DictReader(handle))
            self.assertEqual(row["result_valid"], "False")
            self.assertEqual(row["video_min_per_sec"], "")
            self.assertIn("nonpositive timestamp window", row["result_invalid_reason"])
            metadata = json.loads((root / "run-metadata.json").read_text())
            self.assertEqual(metadata["cleanup_totals"]["deleted"], 1)
            from validate_artifacts import validate_artifacts
            self.assertEqual(validate_artifacts(root), [])

    def test_report_failures_fail_the_run_but_retain_benchmark_records(self):
        for chart_status, artifact_errors in ((2, []), (0, ["Missing required chart"])):
            with self.subTest(chart_status=chart_status, artifact_errors=artifact_errors), tempfile.TemporaryDirectory() as d:
                cli = Mock(call=Mock(return_value=ack()))
                cli.command = ("/installed/vss",)
                cli.config_home = "/configured/cli"
                validation = ValidationResult(
                    cli=cli, deployment={"base_url": "http://vss.test", "services": {"vst": {"url": "http://vss.test/vst"}}},
                    elasticsearch_url="http://vss.test/elasticsearch", version_compatibility=compatibility(),
                )
                with (
                    patch("run.validate", return_value=validation),
                    patch("run.load_class", return_value=[video()]),
                    patch("run.EsReadinessMonitor", return_value=Mock(confirm=Mock(return_value=ready()))),
                    patch("charts.main", return_value=chart_status),
                    patch("summarize.main", return_value=0),
                    patch("validate_artifacts.validate_artifacts", return_value=artifact_errors),
                    redirect_stdout(io.StringIO()),
                ):
                    code = run.main([
                        "--no-config", "--corpus", d, "--profile", "smoke", "--warmup", "0",
                        "--cleanup", "never", "--results-dir", d,
                    ])
                self.assertEqual(code, 2)
                self.assertTrue((Path(d) / "csv/ingest_requests.csv").is_file())
                self.assertTrue((Path(d) / "run-metadata.json").is_file())
                cli.call.assert_called_once()

    def test_missing_chart_dependency_stops_before_upload(self):
        with tempfile.TemporaryDirectory() as d:
            validation = ValidationResult(
                cli=Mock(), deployment={"base_url": "http://vss.test", "services": {"vst": {"url": "http://vss.test/vst"}}},
                elasticsearch_url="http://vss.test/elasticsearch", version_compatibility=compatibility(),
            )
            with (
                patch("run.validate", return_value=validation),
                patch("run.importlib.util.find_spec", return_value=None),
                patch("run.upload_one") as upload,
                redirect_stdout(io.StringIO()),
            ):
                code = run.main(["--no-config", "--corpus", d, "--profile", "smoke", "--results-dir", d])
            self.assertEqual(code, 2)
            upload.assert_not_called()

    def test_old_configuration_rejected_and_new_template_loads(self):
        root = Path(__file__).resolve().parents[1]
        defaults = load_config(root / "config.yml")
        self.assertEqual(defaults["sweep.profile"], "smoke")
        self.assertEqual(defaults["es_readiness.elasticsearch_url"], "")
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "old.yml"
            p.write_text("upload:\n  flow: put\n")
            with self.assertRaises(ConfigError):
                load_config(p)

    def test_failed_warmup_stops_before_measured_uploads(self):
        with tempfile.TemporaryDirectory() as d:
            cli = Mock(call=Mock(return_value=CliResult(7, {}, "timeline timeout")))
            validation = ValidationResult(
                cli=cli,
                deployment={"base_url": "http://fixture.invalid", "services": {"vst": {"url": "http://fixture.invalid/vst"}}},
                elasticsearch_url="http://fixture.invalid/elasticsearch",
            )
            with (
                patch("run.validate", return_value=validation),
                patch("run.load_class", return_value=[video()]),
                patch("run.EsReadinessMonitor", return_value=Mock()),
                redirect_stdout(io.StringIO()),
            ):
                code = run.main(
                    ["--no-config", "--corpus", d, "--profile", "smoke", "--warmup", "1", "--results-dir", d]
                )
            self.assertEqual(code, 1)
            self.assertEqual([call.args[1] for call in cli.call.call_args_list], ["add", "list"])
            detail = json.loads((Path(d) / "raw/warmup_details.jsonl").read_text())
            self.assertEqual(detail["outcome"], "timed_out")
            self.assertFalse((Path(d) / "csv/ingest_summary.csv").exists())

    def test_dry_run_never_starts_uploads(self):
        with tempfile.TemporaryDirectory() as d:
            cli = Mock()
            validation = ValidationResult(
                cli=cli,
                deployment={"base_url": "http://fixture.invalid", "services": {"vst": {"url": "http://fixture.invalid/vst"}}},
                elasticsearch_url="http://fixture.invalid/elasticsearch",
            )
            with (
                patch("run.validate", return_value=validation),
                patch("run.load_class", return_value=[video()]),
                patch("run.EsReadinessMonitor") as monitor,
                redirect_stdout(io.StringIO()),
            ):
                code = run.main(["--no-config", "--corpus", d, "--profile", "smoke", "--dry-run", "--results-dir", d])
            self.assertEqual(code, 0)
            cli.call.assert_not_called()
            monitor.assert_not_called()

    def test_config_precedence(self):
        args = run.parse_args(["--profile", "smoke", "--concurrency", "2", "--cleanup", "never"])
        self.assertEqual(args.concurrencies, [2])
        self.assertEqual(args.cleanup, "never")


class ReadinessTests(unittest.TestCase):
    def test_real_readiness_requires_both_pipelines_and_correct_keys(self):
        cfg = EsReadinessConfig(poll_interval_sec=0.5)
        query = build_query(camera_name="unique-name", sensor_id="returned-id", config=cfg)
        filters = query["query"]["bool"]["should"]
        self.assertEqual(filters[0]["bool"]["filter"][1], {"term": {"sensorId.keyword": "unique-name"}})
        self.assertEqual(filters[1]["bool"]["filter"][1], {"term": {"sensor.id.keyword": "returned-id"}})
        chunks, frames = expected_counts(duration_sec=60, fps=30, reported_chunks=0, config=cfg)
        self.assertEqual((chunks, frames), (12, 1785))

        def payload(raw, embed):
            return JsonResponse(
                200,
                {
                    "aggregations": {
                        "pipelines": {"buckets": {"rt_cv": {"doc_count": raw}, "rt_embed": {"doc_count": embed}}}
                    }
                },
                "",
                0,
            )

        monitor = EsReadinessMonitor("http://es.test", 0.5, 4, cfg)
        from completion import UploadContext

        with (
            patch("es_readiness.request_json", side_effect=[payload(frames, 0), payload(frames, chunks)]) as req,
            patch("es_readiness.time.sleep"),
        ):
            result = monitor.confirm(UploadContext("unique.mp4", "unique-name", "returned-id", {}, 60, 30))
        self.assertTrue(result.confirmed)
        self.assertEqual(req.call_count, 2)
        self.assertLessEqual(req.call_args.kwargs["timeout_sec"], 4)


if __name__ == "__main__":
    unittest.main()
