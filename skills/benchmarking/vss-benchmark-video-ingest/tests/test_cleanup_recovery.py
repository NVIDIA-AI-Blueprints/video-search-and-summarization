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
from cleanup import CleanupWaitResult
from es_readiness import EsReadinessConfig
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
        wait_patch = patch("run.wait_for_cleanup", return_value=CleanupWaitResult(True, "ES documents absent", 2, 30.0))
        self.cleanup_wait = wait_patch.start()
        self.addCleanup(wait_patch.stop)
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
            CliResult(0, {"sensors": sensors}) if args[1] == "list" else CliResult(0, {"confirmed": True, "recordings": "removed", "deleted": ["storage"]})
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

    def pending(self, sequence=1):
        camera = f"{POINT_ID}-{sequence:05d}"
        self.ledger.record(sensor_id="", upload_filename=f"{camera}.mp4", camera_name=camera,
                           request_sent_at="2026-09-29T00:00:00Z", cli_exit_code="")
        return {"sensor_id": "listed-id", "name": camera, "type": "video"}

    def test_timeline_timeout_without_json_is_cleaned_and_identity_pinned(self):
        camera = f"{POINT_ID}-00001"
        target = {"sensor_id": "listed-id", "name": camera, "type": "video"}

        def invoke(*args):
            if args[1] == "add":
                self.assertEqual(load_ledger(self.path)[1][0]["sensor_id"], "")
                self.assertEqual(load_ledger(self.path)[1][0]["upload_filename"], args[-1])
                return CliResult(7, {}, "timeline timeout")
            if args[1] == "list":
                return CliResult(0, {"sensors": [target, {**target, "sensor_id": "unrelated", "name": camera + "-other"}]})
            self.assertEqual(args, ("vios", "delete", "--type", "video", "--sensor", "listed-id"))
            self.assertEqual(load_ledger(self.path)[1][0]["sensor_id"], "listed-id")
            return CliResult(0, {"confirmed": True, "recordings": "removed", "deleted": ["storage"]})

        cli = Mock(call=Mock(side_effect=invoke))
        monitor = Mock()
        record = upload_one(video(), worker_index=1, upload_sequence=1, concurrency=1, run_uuid=POINT_ID,
                            cli=cli, readiness_monitor=monitor, record_identity=self.ledger.record)
        before = (record.latency_sec, record.cli_duration_sec, record.outcome)
        stats = cleanup_records(cli, [record], "always", es_config=EsReadinessConfig(), record_identity=self.ledger.record)
        self.assertEqual(stats, {"attempted": 1, "deleted": 1, "failed": 0, "no_handle": 0})
        self.assertEqual(record.sensor_id, "listed-id")
        self.assertEqual((record.latency_sec, record.cli_duration_sec, record.outcome), before)
        self.assertEqual(record.outcome, "timed_out")
        self.assertEqual(record.transmitted_bytes, 0)
        monitor.confirm.assert_not_called()
        self.assertEqual([c.args[1] for c in cli.call.call_args_list], ["add", "list", "delete"])
        replacement_cli = self.cli([{**target, "sensor_id": "replacement-id"}])
        self.assertEqual(recover(replacement_cli, self.path, apply=True)["assets"][0]["status"], "already_absent")
        replacement_cli.call.assert_called_once()

    def test_interrupt_during_cli_is_recoverable_from_pre_upload_intent(self):
        cli = Mock(call=Mock(side_effect=KeyboardInterrupt))
        with self.assertRaises(KeyboardInterrupt):
            upload_one(video(), worker_index=1, upload_sequence=1, concurrency=1, run_uuid=POINT_ID,
                       cli=cli, readiness_monitor=Mock(), record_identity=self.ledger.record)
        pending = load_ledger(self.path)[1][0]
        self.assertEqual(pending["sensor_id"], "")
        recovery_cli = self.cli([{"sensor_id": "listed-id", "name": pending["camera_name"], "type": "video"}])
        before = self.path.read_bytes()
        self.assertEqual(recover(recovery_cli, self.path)["assets"][0]["status"], "would_delete")
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(recover(recovery_cli, self.path, apply=True)["assets"][0]["status"], "deleted")
        self.assertEqual(load_ledger(self.path)[1][0]["sensor_id"], "listed-id")

    def test_intent_write_failure_prevents_upload(self):
        cli = Mock()
        record = upload_one(video(), worker_index=1, upload_sequence=1, concurrency=1, run_uuid=POINT_ID,
                            cli=cli, readiness_monitor=Mock(), record_identity=Mock(side_effect=OSError("disk full")))
        self.assertEqual(record.outcome, "failed")
        self.assertIn("upload not started", record.error_detail)
        self.assertFalse(record.cleanup_intent_recorded)
        cli.call.assert_not_called()

    def test_returned_id_persistence_failure_retains_media_without_retrying_append(self):
        writes = []
        def write(**identity):
            writes.append(identity)
            if len(writes) == 1:
                self.ledger.record(**identity)
            else:
                raise OSError("disk full")
        cli = Mock(call=Mock(return_value=ack("owned-id")))
        record = upload_one(video(), worker_index=1, upload_sequence=1, concurrency=1, run_uuid=POINT_ID,
                            cli=cli, readiness_monitor=Mock(), record_identity=write)
        self.assertEqual(record.sensor_id, "owned-id")
        self.assertFalse(record.cleanup_identity_recorded)
        self.assertEqual(cleanup_records(cli, [record], "always", es_config=EsReadinessConfig(), record_identity=write)["failed"], 1)
        cli.call.assert_called_once()
        self.assertEqual(len(writes), 2)
        self.assertEqual(load_ledger(self.path)[1][0]["sensor_id"], "")

    def test_unrelated_unknown_sensor_does_not_block_pending_or_known_recovery(self):
        pending = self.pending()
        unknown = {"sensor_id": "", "name": "unrelated-post-upload", "type": "unknown", "error": "no sensorId"}
        cli = self.cli([pending, unknown])
        self.assertEqual(recover(cli, self.path, apply=True)["assets"][0]["status"], "deleted")
        # The pinned-ID path must also tolerate the unrelated diagnostic row.
        self.assertEqual(recover(self.cli([pending, unknown]), self.path)["assets"][0]["status"], "would_delete")

    def test_automatic_cleanup_resolution_write_failure_never_deletes(self):
        record = upload_one(video(), worker_index=1, upload_sequence=1, concurrency=1, run_uuid=POINT_ID,
                            cli=Mock(call=Mock(return_value=CliResult(7, {}, "timeout"))),
                            readiness_monitor=Mock(), record_identity=self.ledger.record)
        cli = self.cli([{"sensor_id": "listed-id", "name": record.upload_filename.rsplit(".", 1)[0], "type": "video"}])
        stats = cleanup_records(cli, [record], "always", es_config=EsReadinessConfig(), record_identity=Mock(side_effect=OSError("disk full")))
        self.assertEqual(stats["no_handle"], 1)
        self.assertEqual(record.sensor_id, "")
        cli.call.assert_called_once()

    def test_pending_absence_is_unresolved_and_recovery_exits_nonzero(self):
        target = self.pending()
        cli = self.cli([{**target, "sensor_id": "other", "name": target["name"] + "-other"}])
        self.assertEqual(recover(cli, self.path, apply=True)["assets"][0]["status"], "unresolved")
        cli.call.assert_called_once()
        import recover_cleanup
        with patch("recover_cleanup.VssCli", return_value=cli), redirect_stdout(io.StringIO()):
            self.assertEqual(recover_cleanup.main(["--ledger", str(self.path), "--apply"]), 1)

    def test_ambiguous_pending_name_refuses_entire_batch(self):
        known = self.identity()
        pending = self.pending(sequence=2)
        cli = self.cli([known, pending, {**pending, "sensor_id": "duplicate-name-id"}])
        with self.assertRaisesRegex(ValueError, "Ambiguous pending upload name"):
            recover(cli, self.path, apply=True)
        cli.call.assert_called_once()
        self.assertEqual(load_ledger(self.path)[1][1]["sensor_id"], "")

    def test_invalid_inventory_never_resolves_or_deletes_pending_upload(self):
        pending = self.pending()
        cases = [
            [{**pending, "sensor_id": ""}],
            [{**pending, "sensor_id": "  "}],
            [{**pending, "sensor_id": " padded "}],
            [{**pending, "type": "stream"}],
            [pending, {**pending, "name": "another-name"}],
        ]
        for sensors in cases:
            with self.subTest(sensors=sensors):
                cli = self.cli(sensors)
                with self.assertRaises(ValueError):
                    recover(cli, self.path, apply=True)
                cli.call.assert_called_once()

    def test_resolution_write_failure_prevents_recovery_deletion(self):
        target = self.pending()
        cli = self.cli([target])
        with patch("recover_cleanup.append_identity", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                recover(cli, self.path, apply=True)
        cli.call.assert_called_once()

    def test_recovery_resolution_is_persisted_before_delete_and_not_reselected(self):
        target = self.pending()
        cli = self.cli([target])
        def delete_check(*args):
            if args[1] == "list":
                return CliResult(0, {"sensors": [target]})
            self.assertEqual(load_ledger(self.path)[1][0]["sensor_id"], target["sensor_id"])
            return CliResult(0, {"confirmed": True, "recordings": "removed", "deleted": ["storage"]})
        cli.call.side_effect = delete_check
        self.assertEqual(recover(cli, self.path, apply=True)["assets"][0]["status"], "deleted")
        replacement_cli = self.cli([{**target, "sensor_id": "replacement-id"}])
        self.assertEqual(recover(replacement_cli, self.path, apply=True)["assets"][0]["status"], "already_absent")
        replacement_cli.call.assert_called_once()

    def test_cleanup_policy_retains_timed_out_pending_uploads(self):
        record = upload_one(video(), worker_index=1, upload_sequence=1, concurrency=1, run_uuid=POINT_ID,
                            cli=Mock(call=Mock(return_value=CliResult(7, {}, "timeout"))),
                            readiness_monitor=Mock(), record_identity=self.ledger.record)
        cli = Mock()
        for policy in ("never", "on-success"):
            self.assertEqual(cleanup_records(cli, [record], policy, es_config=EsReadinessConfig(), record_identity=self.ledger.record),
                             {"attempted": 0, "deleted": 0, "failed": 0, "no_handle": 0})
        cli.call.assert_not_called()

    def test_unsupported_ledger_schema_is_rejected_before_deletion(self):
        target = self.identity()
        self.path.write_text(self.path.read_text().replace("vss-ingest-upload-ledger-v2", "unknown-schema"))
        cli = self.cli([target])
        with self.assertRaisesRegex(ValueError, "Invalid upload ledger header"):
            recover(cli, self.path, apply=True)
        cli.call.assert_not_called()

    def test_conflicting_resolution_is_rejected(self):
        target = self.identity()
        self.ledger.record(sensor_id="replacement", upload_filename=target["name"] + ".mp4",
                           camera_name=target["name"])
        cli = self.cli([target])
        with self.assertRaisesRegex(ValueError, "Duplicate or conflicting"):
            recover(cli, self.path, apply=True)
        cli.call.assert_not_called()

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

    def test_runner_cleans_empty_json_timeout_and_preserves_failed_metrics(self):
        root = Path(self.tmp.name) / "timeout-run"
        cli = Mock()
        cli.command = ("vss",)
        cli.config_home = "/fixture/cli"
        inventory = []
        def invoke(*args):
            if args[1] == "add":
                inventory.append({"sensor_id": "listed-id", "name": args[-1].rsplit(".", 1)[0], "type": "video"})
                return CliResult(7, {}, "timeline timeout")
            if args[1] == "list":
                return CliResult(0, {"sensors": inventory})
            self.assertEqual(args, ("vios", "delete", "--type", "video", "--sensor", "listed-id"))
            return CliResult(0, {"confirmed": True, "recordings": "removed", "deleted": ["storage"]})
        cli.call.side_effect = invoke
        validation = ValidationResult(cli=cli, deployment=DEPLOYMENT,
                                      elasticsearch_url="http://vss.test/es", version_compatibility=compatibility())
        with (patch("run.validate", return_value=validation), patch("run.load_class", return_value=[video()]),
              patch("run.EsReadinessMonitor"), redirect_stdout(io.StringIO())):
            code = run.main(["--no-config", "--corpus", self.tmp.name, "--profile", "custom",
                             "--video-class", "50MB", "--concurrency", "1", "--warmup", "0",
                             "--results-dir", str(root)])
        self.assertEqual(code, 1)
        self.assertEqual([call.args[1] for call in cli.call.call_args_list], ["add", "list", "delete"])
        record = json.loads((root / "raw/upload_details.jsonl").read_text())
        self.assertEqual(record["outcome"], "timed_out")
        self.assertEqual(record["sensor_id"], "listed-id")
        self.assertEqual(record["ingest_confirmed_at"], "")
        metadata = json.loads((root / "run-metadata.json").read_text())
        self.assertEqual(metadata["cleanup_totals"], {"attempted": 1, "deleted": 1, "failed": 0, "no_handle": 0})
        self.assertEqual(metadata["stop_reason"], "")
        from validate_artifacts import validate_artifacts
        self.assertEqual(validate_artifacts(root), [])

    def test_cleanup_always_includes_failed_and_unconfirmed_handles(self):
        cli = Mock(call=Mock(return_value=CliResult(0, {"confirmed": True, "recordings": "removed", "deleted": ["storage"]})))
        records = [Mock(sensor_id="failed-id", outcome="failed", upload_filename="first.mp4"), Mock(sensor_id="unconfirmed-id", outcome="unconfirmed", upload_filename="second.mp4")]
        self.assertEqual(cleanup_records(cli, records, "always", es_config=EsReadinessConfig())["deleted"], 2)
        self.assertEqual(cleanup_records(cli, records, "on-success", es_config=EsReadinessConfig())["attempted"], 0)
        self.assertEqual(parse_args(["--no-config"]).cleanup, "always")


if __name__ == "__main__":
    unittest.main()
