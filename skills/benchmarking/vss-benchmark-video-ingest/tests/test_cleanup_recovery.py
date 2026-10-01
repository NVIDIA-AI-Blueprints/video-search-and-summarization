# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Interrupted uploads preserve ownership; recovery never selects unrelated videos."""

from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from recover_cleanup import UploadLedger, load_ledger, recover
from run import cleanup_records, parse_args
import run
from test_cli_ingest import ack, compatibility, video
from upload import upload_one
from validate import ValidationResult
from vss_cli import CliResult

DEPLOYMENT = {"services": {"vst": {"url": "http://vss.test/vst"}}}
POINT_ID = "ab" * 16


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "upload_ledger.jsonl"
        self.ledger = UploadLedger(self.path, "test-run", DEPLOYMENT)

    def identity(self, sensor="owned-id", sequence=1):
        camera = f"{POINT_ID}-{sequence:05d}"
        self.ledger.record(sensor_id=sensor, upload_filename=f"{camera}.mp4", camera_name=camera,
                           request_sent_at="2026-09-29T00:00:00Z", cli_exit_code=0)
        return {"sensor_id": sensor, "name": camera, "type": "video"}

    def cli(self, sensors):
        cli = Mock()
        cli.deployment.return_value = DEPLOYMENT
        cli.call.side_effect = lambda *args: (
            CliResult(0, {"sensors": sensors}) if args[1] == "list" else CliResult(0, {"deleted": True})
        )
        return cli

    def test_identity_is_durable_before_interrupted_readiness(self):
        cli = Mock(call=Mock(return_value=ack("owned-id")))

        def interrupt(ctx):
            _, entries = load_ledger(self.path)
            self.assertEqual(entries[0]["sensor_id"], ctx.sensor_id)
            self.assertEqual(entries[0]["camera_name"], ctx.camera_name)
            raise KeyboardInterrupt

        with self.assertRaises(KeyboardInterrupt):
            upload_one(video(), worker_index=1, upload_sequence=1, concurrency=1, run_uuid=POINT_ID,
                       cli=cli, readiness_monitor=Mock(confirm=Mock(side_effect=interrupt)),
                       record_identity=self.ledger.record)
        self.assertEqual(len(load_ledger(self.path)[1]), 1)
        cli.call.assert_called_once()

    def test_failed_cli_with_returned_handle_is_recoverable(self):
        cli = Mock(call=Mock(return_value=CliResult(7, {"sensor_id": "owned-id"}, "timeout")))
        monitor = Mock()
        record = upload_one(video(), worker_index=1, upload_sequence=1, concurrency=1, run_uuid=POINT_ID,
                            cli=cli, readiness_monitor=monitor, record_identity=self.ledger.record)
        self.assertEqual(record.outcome, "timed_out")
        self.assertEqual(load_ledger(self.path)[1][0]["sensor_id"], "owned-id")
        monitor.confirm.assert_not_called()

    def test_default_is_dry_run_and_unrelated_inventory_is_untouched(self):
        target = self.identity()
        cli = self.cli([target, {"sensor_id": "other", "name": "unrelated", "type": "video"}])
        result = recover(cli, self.path)
        self.assertEqual(result["assets"][0]["status"], "would_delete")
        cli.call.assert_called_once_with("vios", "list", "--type", "video")

    def test_apply_only_deletes_exact_returned_id(self):
        target = self.identity()
        cli = self.cli([target, {"sensor_id": "other", "name": target["name"], "type": "video"}])
        result = recover(cli, self.path, apply=True)
        self.assertEqual(result["assets"][0]["status"], "deleted")
        self.assertFalse(result["downstream_cleanup_verified"])
        self.assertEqual(cli.call.call_args_list[-1].args, ("vios", "delete", "--type", "video", "--sensor", "owned-id"))

    def test_absent_id_does_not_delete_another_sensor_with_same_name(self):
        target = self.identity()
        cli = self.cli([{**target, "sensor_id": "another-id"}])
        self.assertEqual(recover(cli, self.path, apply=True)["assets"][0]["status"], "already_absent")
        cli.call.assert_called_once()

    def test_name_mismatch_refuses_entire_batch_before_deleting(self):
        first = self.identity()
        second = self.identity("second-owned-id", 2)
        cli = self.cli([first, {**second, "name": "different"}])
        with self.assertRaisesRegex(ValueError, "current identity differs"):
            recover(cli, self.path, apply=True)
        cli.call.assert_called_once_with("vios", "list", "--type", "video")

    def test_wrong_deployment_refuses_cleanup(self):
        self.identity()
        cli = self.cli([])
        cli.deployment.return_value = {"services": {"vst": {"url": "http://another.test/vst"}}}
        with self.assertRaisesRegex(ValueError, "differs from the recorded run"):
            recover(cli, self.path, apply=True)
        cli.call.assert_not_called()

    def test_partial_or_corrupt_ledger_refuses_cleanup(self):
        self.identity()
        with self.path.open("a") as stream:
            stream.write('{"sensor_id":')
        cli = self.cli([])
        with self.assertRaises(ValueError):
            recover(cli, self.path, apply=True)
        cli.call.assert_not_called()

    def test_existing_ledger_is_not_overwritten(self):
        self.identity()
        previous = self.path.read_bytes()
        with self.assertRaises(FileExistsError):
            UploadLedger(self.path, "another-run", DEPLOYMENT)
        self.assertEqual(self.path.read_bytes(), previous)

    def test_rerun_refuses_before_overwriting_previous_corpus(self):
        root = Path(self.tmp.name)
        (root / "raw").mkdir()
        self.path.rename(root / "raw/upload_ledger.jsonl")
        (root / "csv").mkdir()
        corpus_csv = root / "csv/ingest_corpus.csv"
        corpus_csv.write_text("previous corpus evidence\n")
        cli = Mock()
        validation = ValidationResult(cli=cli, deployment=DEPLOYMENT,
                                      elasticsearch_url="http://vss.test/es", version_compatibility=compatibility())
        with (patch("run.validate", return_value=validation), patch("run.load_class", return_value=[video()]),
              redirect_stdout(io.StringIO())):
            code = run.main(["--no-config", "--corpus", str(root), "--results-dir", str(root)])
        self.assertEqual(code, 2)
        self.assertEqual(corpus_csv.read_text(), "previous corpus evidence\n")
        cli.call.assert_not_called()

    def test_incomplete_cleanup_stops_next_point_and_preserves_partial_artifacts(self):
        for mode in ("delete_failure", "missing_handle"):
            with self.subTest(mode=mode):
                root = Path(self.tmp.name) / mode
                cli = Mock()
                cli.command = ("vss",)
                cli.config_home = "/fixture/cli"
                cli.call.side_effect = lambda *args: (
                    ack("owned-id" if mode == "delete_failure" else "") if args[1] == "add"
                    else CliResult(3, {}, "delete failed")
                )
                validation = ValidationResult(cli=cli, deployment=DEPLOYMENT,
                                              elasticsearch_url="http://vss.test/es", version_compatibility=compatibility())
                from test_cli_ingest import ready
                with (patch("run.validate", return_value=validation), patch("run.load_class", return_value=[video()]),
                      patch("run.EsReadinessMonitor", return_value=Mock(confirm=Mock(return_value=ready()))),
                      redirect_stdout(io.StringIO())):
                    code = run.main(["--no-config", "--corpus", self.tmp.name, "--profile", "custom",
                                     "--video-class", "50MB", "--concurrency", "1", "--concurrency", "2",
                                     "--warmup", "0", "--results-dir", str(root)])
                self.assertEqual(code, 1)
                self.assertEqual(sum(call.args[1] == "add" for call in cli.call.call_args_list), 1)
                metadata = json.loads((root / "run-metadata.json").read_text())
                self.assertEqual(metadata["sweep_points_completed"], 1)
                self.assertIn("Cleanup incomplete", metadata["stop_reason"])
                from validate_artifacts import validate_artifacts
                self.assertEqual(validate_artifacts(root), [])
                self.assertIn("**Run stopped:**", (root / "visualizations/summary.md").read_text())

    def test_cleanup_always_includes_failed_and_unconfirmed_handles(self):
        cli = Mock(call=Mock(return_value=CliResult(0, {"deleted": True})))
        records = [Mock(sensor_id="failed-id", outcome="failed"), Mock(sensor_id="unconfirmed-id", outcome="unconfirmed")]
        self.assertEqual(cleanup_records(cli, records, "always")["deleted"], 2)
        self.assertEqual(cleanup_records(cli, records, "on-success")["attempted"], 0)
        self.assertEqual(parse_args(["--no-config"]).cleanup, "always")


if __name__ == "__main__":
    unittest.main()
