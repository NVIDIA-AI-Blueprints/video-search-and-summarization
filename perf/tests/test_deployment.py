# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Focused lifecycle checks; hardware integration is exercised on the GPU host."""

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

SOURCE = Path(__file__).parents[1]
sys.path.insert(0, str(SOURCE / "scripts"))
SPEC = importlib.util.spec_from_file_location("stage", SOURCE / "scripts/stage.py")
stage = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(stage)


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    root = tmp_path / "perf"
    root.mkdir()
    shutil.copytree(SOURCE / "configs", root / "configs")
    monkeypatch.setattr(stage, "ROOT", root)
    monkeypatch.setattr(stage, "STATE", root / ".runtime-state.json")
    with patch.dict(os.environ, {"PATH": os.environ["PATH"]}, clear=True):
        yield stage.context("pre_flight")


def test_fresh_config_and_worker_reuse_without_persisting_credentials(runtime):
    state, _ = runtime
    os.environ.update(
        NGC_API_KEY="secret", VSS_PERF_STREAM_URL="rtsp://user:secret@camera/path"
    )
    fresh, _ = stage.context("pre_flight")
    assert state["vios_config"] != fresh["vios_config"]
    with patch.object(sys, "argv", ["stage.py", "pre_flight", "--worker"]):
        assert stage.context("pre_flight")[0] == fresh
    assert "secret" not in stage.STATE.read_text()


def test_preflight_editable_cli_model_precision_and_direct_promotion(runtime):
    state, config = runtime
    Path(state["video"]).parent.mkdir(parents=True)
    Path(state["video"]).touch()
    os.environ.update(
        IMAGE_TAG="org/team/vss-blueprints-compose-base:version",
        VSS_CONTAINER_TAG="caller-latest",
    )
    published = {
        "compose_root": state["apps"],
        "env_files": [state["apps"] + "/containers.env"],
    }
    with (
        patch.object(stage, "command") as commands,
        patch.object(stage, "acquire", return_value=published) as acquire,
    ):
        stage.pre_flight(state, config, stage.environment(state))
    install = next(
        call.args[0]
        for call in commands.call_args_list
        if call.args[0][:3] == ["uv", "pip", "install"]
    )
    assert install.count("-e") == 2
    assert acquire.call_args.args[0] == os.environ["IMAGE_TAG"]
    assert acquire.call_args.args[2] == Path(state["compose"])
    assert "VSS_CONTAINER_TAG" not in stage.environment(state)
    pgie = (
        Path(state["data"]) / "rt-cv-configs/ds-ppl-analytics-pgie-config.yml"
    ).read_text()
    assert "strongly-typed: 1" in pgie and "network-mode: 0" in pgie
    assert "rtdetr_warehouse_v1.0.2.fp16.onnx" in pgie


def test_kafka_init_waits_separately_from_long_running_services(runtime):
    with patch.object(stage, "command") as commands:
        stage.deploy(*runtime, {})
    calls = [call.args[0] for call in commands.call_args_list]
    assert calls[1][-2:] == ["wait", "kafka-topic-init-container"]
    assert "--wait" in calls[2] and "kafka-topic-init-container" not in calls[2]
    assert "logstash" in calls[2]


def test_scoped_teardown_database_reset_and_idempotence(runtime):
    calls, volumes = [], iter(["actual-project-pg-volume\n", ""])

    def docker(args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(
            args, 0, next(volumes) if args[1:3] == ["volume", "ls"] else ""
        )

    with patch.object(stage, "command", side_effect=docker):
        stage.teardown(*runtime, {})
        stage.teardown(*runtime, {})
    assert [args for args in calls if args[1:3] == ["volume", "rm"]] == [
        ["docker", "volume", "rm", "actual-project-pg-volume"]
    ]
    for args in calls:
        if args[1:3] == ["volume", "ls"]:
            assert "label=com.docker.compose.project=" + stage.PROJECT in args
            assert "label=com.docker.compose.volume=vios_pg_data" in args
    assert "*" in calls[0]
    with (
        patch.object(
            stage,
            "command",
            return_value=subprocess.CompletedProcess([], 0, "leftover\n"),
        ),
        pytest.raises(RuntimeError, match="still owns"),
    ):
        stage.teardown(*runtime, {})


def test_post_deploy_uses_returned_sensor_identifier_and_checks_fps(runtime):
    state, _ = runtime
    configs = Path(state["data"]) / "rt-cv-configs"
    shutil.copytree(
        stage.REPO / "services/rtvi/rt-cv/reference-configs/warehouse-2d", configs
    )
    response = subprocess.CompletedProcess(
        [], 0, json.dumps({"sensor_id": "actual-id", "name": "compose-base-perf"})
    )
    with (
        patch.object(stage, "command", return_value=response) as cli,
        patch.object(stage, "capture"),
        patch.object(stage, "inference_ready") as ready,
    ):
        stage.post_deploy(*runtime, {})
    assert "sensor-id-list=actual-id" in (configs / "ds-main-config.txt").read_text()
    assert sum("add" in call.args[0] for call in cli.call_args_list) == 1
    ready.assert_called_once()


def test_native_watchdog_and_one_failure_result(runtime):
    with patch.object(
        stage.subprocess, "run", return_value=subprocess.CompletedProcess([], 124)
    ) as commands:
        assert stage.stage("deploy") == 124
    assert commands.call_count == 2
    assert commands.call_args_list[0].args[0][:3] == [
        "timeout",
        "--kill-after=5s",
        "45m",
    ]
    assert "--worker" in commands.call_args_list[0].args[0]
    assert "--stage-failure" in commands.call_args_list[1].args[0]


def test_direct_failed_stage_writes_once_and_teardown_never_recurses(runtime):
    scripts, binary = stage.ROOT / "scripts", stage.ROOT / ".venv/bin"
    scripts.mkdir()
    binary.mkdir(parents=True)
    for name in ("stage.py", "promoted_artifact.py"):
        shutil.copyfile(SOURCE / "scripts" / name, scripts / name)
    (binary / "python").symlink_to(sys.executable)
    core = stage.ROOT / "benchmark/core"
    core.mkdir(parents=True)
    (core / "vss_perf_benchmark.py").write_text(
        "import pathlib,sys\n"
        "output=pathlib.Path(sys.argv[sys.argv.index('--output-json')+1]); output.mkdir(parents=True,exist_ok=True)\n"
        "(output/'failure.json').write_text('{\"success\":false}')\n"
    )
    (binary / "timeout").write_text('#!/bin/sh\nshift 2\nexec "$@"\n')
    (binary / "timeout").chmod(0o755)
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
        assert result.returncode == 1, result.stderr.decode()
    results = list((stage.ROOT / "results/local").glob("*.json"))
    assert len(results) == 1 and not json.loads(results[0].read_text())["success"]
