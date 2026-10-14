# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Focused lifecycle checks; hardware integration is exercised on the GPU host."""

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

SPEC = importlib.util.spec_from_file_location(
    "stage", Path(__file__).parents[1] / "scripts/stage.py"
)
stage = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(stage)


class DeploymentTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "perf"
        self.root.mkdir()
        shutil.copytree(stage.ROOT / "configs", self.root / "configs")
        self.source_root = stage.ROOT
        for name, value in (
            ("ROOT", self.root),
            ("STATE", self.root / ".runtime-state.json"),
        ):
            mock = patch.object(stage, name, value)
            mock.start()
            self.addCleanup(mock.stop)
        mock = patch.dict(os.environ, {"PATH": os.environ["PATH"]}, clear=True)
        mock.start()
        self.addCleanup(mock.stop)
        self.state, self.config = stage.context("pre_flight")

    def test_fresh_config_and_worker_reuse_without_persisting_credentials(self):
        os.environ.update(
            NGC_API_KEY="secret", VSS_PERF_STREAM_URL="rtsp://user:secret@camera/path"
        )
        next_state, _ = stage.context("pre_flight")
        self.assertNotEqual(self.state["vios_config"], next_state["vios_config"])
        with patch.object(sys, "argv", ["stage.py", "pre_flight", "--worker"]):
            self.assertEqual(stage.context("pre_flight")[0], next_state)
        self.assertNotIn("secret", stage.STATE.read_text())
        self.assertEqual(
            next_state["video"], str(Path(next_state["data"]) / "fixture/Camera_03.mp4")
        )

    def test_preflight_installs_editable_cli_and_keeps_typed_model_precision(self):
        self.state["video"] = str(self.root / "video.mp4")
        Path(self.state["video"]).touch()
        calls = []
        with patch.object(
            stage, "command", side_effect=lambda args, **kw: calls.append(args)
        ):
            stage.pre_flight(self.state, self.config, stage.environment(self.state))
        install = next(args for args in calls if args[:3] == ["uv", "pip", "install"])
        self.assertEqual(install.count("-e"), 2)
        pgie = (
            Path(self.state["data"]) / "rt-cv-configs/ds-ppl-analytics-pgie-config.yml"
        ).read_text()
        self.assertIn("strongly-typed: 1", pgie)
        self.assertIn("network-mode: 0", pgie)
        self.assertIn("rtdetr_warehouse_v1.0.2.fp16.onnx", pgie)

    def test_kafka_init_is_waited_separately_from_long_running_services(self):
        calls = []
        with patch.object(
            stage, "command", side_effect=lambda args, **kw: calls.append(args)
        ):
            stage.deploy(self.state, self.config, {})
        self.assertEqual(calls[1][-2:], ["wait", "kafka-topic-init-container"])
        self.assertIn("--wait", calls[2])
        self.assertNotIn("kafka-topic-init-container", calls[2])
        self.assertIn("logstash", calls[2])

    def test_teardown_resets_only_discovered_project_database_and_is_idempotent(self):
        calls, volumes = [], iter(["actual-project-pg-volume\n", ""])

        def docker(args, **kwargs):
            calls.append(args)
            return subprocess.CompletedProcess(
                args, 0, next(volumes) if args[1:3] == ["volume", "ls"] else ""
            )

        with patch.object(stage, "command", side_effect=docker):
            stage.teardown(self.state, self.config, {})
            stage.teardown(self.state, self.config, {})
        self.assertEqual(
            [args for args in calls if args[1:3] == ["volume", "rm"]],
            [["docker", "volume", "rm", "actual-project-pg-volume"]],
        )
        for args in calls:
            if args[1:3] == ["volume", "ls"]:
                self.assertIn("label=com.docker.compose.project=" + stage.PROJECT, args)
                self.assertIn("label=com.docker.compose.volume=vios_pg_data", args)
        self.assertIn("*", calls[0])
        with (
            patch.object(
                stage,
                "command",
                return_value=subprocess.CompletedProcess([], 0, "leftover\n"),
            ),
            self.assertRaisesRegex(RuntimeError, "still owns"),
        ):
            stage.teardown(self.state, self.config, {})

    def test_post_deploy_uses_actual_sensor_identifier_and_checks_fps(self):
        configs = Path(self.state["data"]) / "rt-cv-configs"
        shutil.copytree(
            stage.REPO / "services/rtvi/rt-cv/reference-configs/warehouse-2d", configs
        )
        calls = []

        def cli(args, **kwargs):
            calls.append(args)
            listing = {
                "count": 1,
                "sensors": [{"sensor_id": "actual-id", "name": "compose-base-perf"}],
            }
            return subprocess.CompletedProcess(args, 0, json.dumps(listing))

        with (
            patch.object(stage, "command", side_effect=cli),
            patch.object(stage, "capture"),
            patch.object(stage, "inference_ready") as ready,
        ):
            stage.post_deploy(self.state, self.config, {})
        self.assertIn(
            "sensor-id-list=actual-id", (configs / "ds-main-config.txt").read_text()
        )
        self.assertFalse(any("add" in args for args in calls))
        ready.assert_called_once()

    def test_stage_timeout_kills_its_process_group_and_records_failure_once(self):
        child = Mock(pid=4321)
        child.wait.side_effect = [subprocess.TimeoutExpired("worker", 1), -15]
        with (
            patch.object(stage.subprocess, "Popen", return_value=child),
            patch.object(stage.os, "killpg") as kill,
            patch.object(stage.subprocess, "run") as record,
        ):
            self.assertEqual(stage.stage("deploy"), 124)
        kill.assert_called_once_with(4321, stage.signal.SIGTERM)
        self.assertEqual(record.call_count, 1)
        self.assertIn("--stage-failure", record.call_args.args[0])

    def test_direct_failed_stage_emits_one_result_and_teardown_never_recurses(self):
        scripts, binary = self.root / "scripts", self.root / ".venv/bin"
        scripts.mkdir()
        binary.mkdir(parents=True)
        shutil.copyfile(self.source_root / "scripts/stage.py", scripts / "stage.py")
        (binary / "python").symlink_to(sys.executable)
        core = self.root / "benchmark/core"
        core.mkdir(parents=True)
        (core / "vss_perf_benchmark.py").write_text(
            "import pathlib,sys\n"
            "output=pathlib.Path(sys.argv[sys.argv.index('--output-json')+1]); output.mkdir(parents=True,exist_ok=True)\n"
            "(output/'failure.json').write_text('{\"success\":false}')\n"
        )
        (binary / "docker").write_text("#!/bin/sh\nexit 7\n")
        (binary / "docker").chmod(0o755)
        env = {**os.environ, "PATH": str(binary) + os.pathsep + os.environ["PATH"]}
        for action in ("deploy", "teardown"):
            result = subprocess.run(
                [sys.executable, str(scripts / "stage.py"), action],
                env=env,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 1, result.stderr.decode())
        results = list((self.root / "results/local").glob("*.json"))
        self.assertEqual(len(results), 1)
        self.assertFalse(json.loads(results[0].read_text())["success"])


if __name__ == "__main__":
    unittest.main()
