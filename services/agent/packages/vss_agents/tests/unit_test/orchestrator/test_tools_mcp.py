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
"""Tests for vss_orchestrator MCP tool handlers in orchestrator/tools.py."""

from collections import deque
from collections.abc import AsyncIterator
from collections.abc import Iterator
from contextlib import asynccontextmanager
import os
from pathlib import Path
import subprocess
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest

from vss_agents.orchestrator import tools as tools_mod
from vss_agents.orchestrator.tools import ComposeAction
from vss_agents.orchestrator.tools import ComposeArtifactsInput
from vss_agents.orchestrator.tools import ComposeDownOperationInput
from vss_agents.orchestrator.tools import ComposeOperation
from vss_agents.orchestrator.tools import ComposeStatus
from vss_agents.orchestrator.tools import ComposeStatusInput
from vss_agents.orchestrator.tools import ComposeUpOperationInput
from vss_agents.orchestrator.tools import ContainerLogsInput
from vss_agents.orchestrator.tools import DockerPrereqsInput
from vss_agents.orchestrator.tools import DockerProfilesInput
from vss_agents.orchestrator.tools import GenerateInput
from vss_agents.orchestrator.tools import HardwareResolutionConfig
from vss_agents.orchestrator.tools import ModelArtifactEntry
from vss_agents.orchestrator.tools import ModelPackageConfig
from vss_agents.orchestrator.tools import ModelResolutionConfig
from vss_agents.orchestrator.tools import OrchestratorToolConfig
from vss_agents.orchestrator.tools import RtspSampleProbeInput
from vss_agents.orchestrator.tools import vss_orchestrator


class _FakePopen:
    """Minimal subprocess.Popen stand-in for compose watcher tests."""

    def __init__(self, *args, lines: list[str] | None = None, exit_code: int = 0, **kwargs):
        self.args = args
        self.pid = 4321
        self._lines = lines or ["Container started"]
        self._exit_code = exit_code
        self.terminate_calls = 0
        self.kill_calls = 0

    @property
    def stdout(self) -> Iterator[str]:
        return iter(self._lines)

    def poll(self):
        return None

    def wait(self, timeout=None):
        if timeout is not None and getattr(self, "_wait_timeout_expired", False):
            raise subprocess.TimeoutExpired(cmd=self.args, timeout=timeout)
        return self._exit_code

    def terminate(self):
        self.terminate_calls += 1

    def kill(self):
        self.kill_calls += 1


def _run_target_immediately(*_args, **kwargs):
    mock = MagicMock()

    def start() -> None:
        kwargs["target"]()

    mock.start = start
    return mock


def _make_orchestrator_config(
    tmp_path: Path,
    *,
    include: list[str] | None = None,
    model_artifacts: dict[str, tuple[ModelPackageConfig, ...]] | None = None,
) -> OrchestratorToolConfig:
    mdx = tmp_path / "mdx"
    mdx.mkdir(parents=True, exist_ok=True)
    out = tmp_path / "out"
    out.mkdir(parents=True, exist_ok=True)
    dep = tmp_path / "deploy"
    dep.mkdir(parents=True, exist_ok=True)
    (tmp_path / "compose.yml").write_text("services: {}\n")

    if include is None:
        include = [
            "profiles",
            "prereqs",
            "rtsp_sample_probe",
            "docker_generate",
            "docker_read",
            "docker_list",
            "docker_logs",
            "docker_up",
            "docker_status",
            "docker_down",
        ]

    return OrchestratorToolConfig(
        deployments_dir=str(dep),
        source_compose_yaml=str(tmp_path / "compose.yml"),
        source_env=str(tmp_path / "dev-{profile}.env"),
        mdx_data_dir=str(mdx),
        output_dir=str(out),
        mdx_data_directories=("models",),
        model_artifacts={} if model_artifacts is None else model_artifacts,
        model_resolution=ModelResolutionConfig(
            hardware=HardwareResolutionConfig(
                edge_profiles=("DGX-SPARK",),
                edge_allowed_profiles=("base",),
                edge_device_ids={"llm": "0"},
                hardware_profiles={"H100": {}},
            )
        ),
        include=include,
    )


@pytest.fixture(autouse=True)
def _clear_compose_registries():
    tools_mod._COMPOSE_OPERATIONS._entries.clear()
    tools_mod._COMPOSE_SPECS._entries.clear()
    yield
    tools_mod._COMPOSE_OPERATIONS._entries.clear()
    tools_mod._COMPOSE_SPECS._entries.clear()


@asynccontextmanager
async def _orchestrator_group(
    tmp_path: Path,
    *,
    config: OrchestratorToolConfig | None = None,
) -> AsyncIterator[tuple]:
    if config is None:
        config = _make_orchestrator_config(tmp_path)
    builder = MagicMock()
    async with vss_orchestrator(config, builder) as group:
        yield group, config, tmp_path


async def _call(group, name: str, input_model):
    return await group._functions[name].ainvoke(input_model)


def _register_compose_spec(
    *,
    docker_compose_id: str,
    env_path: Path,
    compose_path: Path,
    profile: str = "base",
    env_text: str | None = None,
) -> None:
    env_path.parent.mkdir(parents=True, exist_ok=True)
    env_path.write_text(env_text or "VSS_DATA_DIR=/tmp/vss-data\nNGC_CLI_API_KEY=test\n")
    compose_path.write_text("services: {}\n")
    tools_mod._COMPOSE_SPECS.set(
        docker_compose_id,
        {
            "docker_compose_id": docker_compose_id,
            "profile": profile,
            "env_file": str(env_path),
            "compose_file": str(compose_path),
        },
    )


def _register_running_compose_operation(
    *,
    docker_compose_ops_id: str,
    docker_compose_id: str,
    action: str,
    process: MagicMock | _FakePopen | None = None,
) -> ComposeOperation:
    op = ComposeOperation(
        docker_compose_ops_id=docker_compose_ops_id,
        docker_compose_id=docker_compose_id,
        action=action,
        pid=9999 if process is None else getattr(process, "pid", 9999),
        process=process,
        status=ComposeStatus.RUNNING.value,
        running=True,
        exit_code=None,
        command=f"docker compose {action}",
        env_file="/tmp/env",
        compose_file="/tmp/compose.yml",
        started_at_epoch_s=1,
    )
    tools_mod._COMPOSE_OPERATIONS.set(docker_compose_ops_id, op)
    return op


def _register_compose_operation(
    *,
    docker_compose_ops_id: str,
    docker_compose_id: str = "base-abc12345",
    action: str = ComposeAction.UP.value,
    status: str = ComposeStatus.RUNNING.value,
    log_lines: list[str] | None = None,
) -> None:
    tools_mod._COMPOSE_OPERATIONS.set(
        docker_compose_ops_id,
        ComposeOperation(
            docker_compose_ops_id=docker_compose_ops_id,
            docker_compose_id=docker_compose_id,
            action=action,
            pid=4242,
            process=None,
            status=status,
            running=True,
            exit_code=None,
            command=f"docker compose {action} -d",
            env_file="/tmp/env",
            compose_file="/tmp/compose.yml",
            started_at_epoch_s=1,
            log_lines=deque(log_lines or ["line-one", "line-two"], maxlen=4000),
        ),
    )


@pytest.mark.asyncio
async def test_profiles_lists_supported_profiles(tmp_path: Path):
    async with _orchestrator_group(tmp_path) as (group, _config, _tmp_path):
        result = await _call(group, "profiles", DockerProfilesInput())
    assert result["status"] == ComposeStatus.SUCCESS.value
    assert result["profiles"] == ["alerts", "base", "lvs", "search"]


def test_default_config_exposes_rtsp_sample_probe() -> None:
    default_include = OrchestratorToolConfig.model_fields["include"].default
    assert isinstance(default_include, list)
    assert "rtsp_sample_probe" in default_include


@pytest.mark.asyncio
async def test_prereqs_success(tmp_path: Path):
    async with _orchestrator_group(tmp_path) as (group, _config, _tmp_path):
        with patch("vss_agents.orchestrator.tools.run_prereqs_checks", return_value={"docker": "ok"}):
            result = await _call(group, "prereqs", DockerPrereqsInput())
    assert result["status"] == ComposeStatus.SUCCESS.value
    assert result["details"] == {"docker": "ok"}


@pytest.mark.asyncio
async def test_prereqs_runtime_error(tmp_path: Path):
    async with _orchestrator_group(tmp_path) as (group, _config, _tmp_path):
        with patch(
            "vss_agents.orchestrator.tools.run_prereqs_checks",
            side_effect=RuntimeError("docker missing"),
        ):
            result = await _call(group, "prereqs", DockerPrereqsInput())
    assert result["status"] == ComposeStatus.ERROR.value
    assert "docker missing" in result["error"]


@pytest.mark.parametrize(
    "url",
    [
        "https://example.test/video",
        "rtsp://example.test/live\ninjected",
        "rtsps://example.test/live\x7f",
        "rtsp://example.test/live\x85",
        "rtsp://example.test/live\u202e",
        "rtsp://example.test/live\u2066",
        "rtsp://example.test/live\u200d",
        "rtsp://example.test/live path",
        "rtsp:///missing-host",
        "rtsp://example.test:70000/live",
    ],
)
@pytest.mark.asyncio
async def test_rtsp_sample_probe_rejects_invalid_config_without_disclosure(
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    url: str,
) -> None:
    with patch.dict(os.environ, {"RTSP_SAMPLE_URL": url}, clear=False):
        async with _orchestrator_group(tmp_path) as (group, _config, _tmp_path):
            with patch(
                "vss_agents.orchestrator.tools.asyncio.to_thread",
            ) as to_thread:
                result = await _call(
                    group,
                    "rtsp_sample_probe",
                    RtspSampleProbeInput(),
                )

    output = capsys.readouterr().out
    assert result["status"] == ComposeStatus.ERROR.value
    assert result["error_code"] == "invalid_sample_url"
    assert url not in output
    assert url not in repr(result)
    to_thread.assert_not_called()


@pytest.mark.asyncio
async def test_rtsp_sample_probe_requires_configured_url(
    tmp_path: Path,
) -> None:
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("RTSP_SAMPLE_URL", None)
        async with _orchestrator_group(tmp_path) as (group, _config, _tmp_path):
            with patch(
                "vss_agents.orchestrator.tools.asyncio.to_thread",
            ) as to_thread:
                result = await _call(
                    group,
                    "rtsp_sample_probe",
                    RtspSampleProbeInput(),
                )

    assert result["status"] == ComposeStatus.ERROR.value
    assert result["error_code"] == "sample_url_unconfigured"
    to_thread.assert_not_called()


@pytest.mark.asyncio
async def test_rtsp_sample_probe_uses_only_server_config_and_requires_video(
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    rtsp_url = "rtsps://alice:s3cr3t@example.test:7441/live/camera?token=top-secret"
    completed = subprocess.CompletedProcess(
        args=[],
        returncode=0,
        stdout='{"streams":[{"index":0,"codec_type":"video","codec_name":"h264"}]}',
        stderr="",
    )

    with patch.dict(os.environ, {"RTSP_SAMPLE_URL": rtsp_url}, clear=False):
        async with _orchestrator_group(tmp_path) as (group, _config, _tmp_path):
            with patch(
                "vss_agents.orchestrator.tools.asyncio.to_thread",
                return_value=completed,
            ) as to_thread:
                result = await _call(
                    group,
                    "rtsp_sample_probe",
                    RtspSampleProbeInput(),
                )

    output = capsys.readouterr().out
    assert result == {
        "status": ComposeStatus.SUCCESS.value,
        "reachable": True,
        "has_video": True,
        "video_stream_count": 1,
        "video_streams": [{"index": 0, "codec_name": "h264"}],
        "transport": "tcp",
        "timeout_s": tools_mod._RTSP_PROBE_TIMEOUT_S,
        "message": "RTSP endpoint is reachable and carries a video stream.",
    }
    assert rtsp_url not in output
    assert rtsp_url not in repr(result)

    run_fn, command = to_thread.call_args.args[:2]
    assert run_fn is subprocess.run
    assert command[-1] == rtsp_url
    assert command[:5] == ["ffprobe", "-v", "error", "-rtsp_transport", "tcp"]
    assert command[5:7] == ["-select_streams", "v:0"]
    assert to_thread.call_args.kwargs["timeout"] == tools_mod._RTSP_PROBE_TIMEOUT_S
    assert to_thread.call_args.kwargs["stdin"] is subprocess.DEVNULL
    assert to_thread.call_args.kwargs["stdout"] is subprocess.PIPE
    assert to_thread.call_args.kwargs["stderr"] is subprocess.DEVNULL
    assert to_thread.call_args.kwargs["check"] is False
    assert to_thread.call_args.kwargs["shell"] is False


@pytest.mark.asyncio
async def test_rtsp_sample_probe_omits_failure_diagnostics(
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    rtsp_url = "rtsp://alice:s3cr3t@example.test/live?token=top-secret"
    completed = subprocess.CompletedProcess(
        args=[],
        returncode=1,
        stdout="",
        stderr=(f"Unable to open {rtsp_url}\nRedirect from rtsp://mirror.example.test/other failed."),
    )

    with patch.dict(os.environ, {"RTSP_SAMPLE_URL": rtsp_url}, clear=False):
        async with _orchestrator_group(tmp_path) as (group, _config, _tmp_path):
            with patch(
                "vss_agents.orchestrator.tools.asyncio.to_thread",
                return_value=completed,
            ):
                result = await _call(
                    group,
                    "rtsp_sample_probe",
                    RtspSampleProbeInput(),
                )

    output = capsys.readouterr().out
    assert result["status"] == ComposeStatus.ERROR.value
    assert result["error_code"] == "probe_failed"
    assert "diagnostic" not in result
    assert rtsp_url not in output
    assert rtsp_url not in repr(result)
    assert "mirror.example.test" not in output
    assert "mirror.example.test" not in repr(result)


@pytest.mark.asyncio
async def test_rtsp_sample_probe_timeout_is_bounded_and_secret_safe(
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    rtsp_url = "rtsp://alice:s3cr3t@example.test/live"
    timeout = subprocess.TimeoutExpired(
        cmd=["ffprobe", rtsp_url],
        timeout=tools_mod._RTSP_PROBE_TIMEOUT_S,
    )

    with patch.dict(os.environ, {"RTSP_SAMPLE_URL": rtsp_url}, clear=False):
        async with _orchestrator_group(tmp_path) as (group, _config, _tmp_path):
            with patch(
                "vss_agents.orchestrator.tools.asyncio.to_thread",
                side_effect=timeout,
            ):
                result = await _call(
                    group,
                    "rtsp_sample_probe",
                    RtspSampleProbeInput(),
                )

    output = capsys.readouterr().out
    assert result["status"] == ComposeStatus.ERROR.value
    assert result["error_code"] == "probe_timeout"
    assert result["timeout_s"] == tools_mod._RTSP_PROBE_TIMEOUT_S
    assert rtsp_url not in output
    assert rtsp_url not in repr(result)


@pytest.mark.asyncio
async def test_rtsp_sample_probe_unexpected_error_does_not_disclose_secret(
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    rtsp_url = "rtsp://user:password@example.test/live"

    with patch.dict(os.environ, {"RTSP_SAMPLE_URL": rtsp_url}, clear=False):
        async with _orchestrator_group(tmp_path) as (group, _config, _tmp_path):
            with patch(
                "vss_agents.orchestrator.tools.asyncio.to_thread",
                side_effect=RuntimeError(f"unexpected failure while probing {rtsp_url}"),
            ):
                result = await _call(
                    group,
                    "rtsp_sample_probe",
                    RtspSampleProbeInput(),
                )

    output = capsys.readouterr().out
    assert result["status"] == ComposeStatus.ERROR.value
    assert result["error_code"] == "probe_internal_error"
    assert "diagnostic" not in result
    assert rtsp_url not in output
    assert rtsp_url not in repr(result)


@pytest.mark.asyncio
async def test_rtsp_sample_probe_rejects_successful_non_video_probe(
    tmp_path: Path,
) -> None:
    completed = subprocess.CompletedProcess(
        args=[],
        returncode=0,
        stdout='{"streams":[]}',
        stderr="",
    )

    with patch.dict(
        os.environ,
        {"RTSP_SAMPLE_URL": "rtsp://example.test/audio-only"},
        clear=False,
    ):
        async with _orchestrator_group(tmp_path) as (group, _config, _tmp_path):
            with patch(
                "vss_agents.orchestrator.tools.asyncio.to_thread",
                return_value=completed,
            ):
                result = await _call(
                    group,
                    "rtsp_sample_probe",
                    RtspSampleProbeInput(),
                )

    assert result["status"] == ComposeStatus.ERROR.value
    assert result["error_code"] == "no_video_stream"
    assert result["video_stream_count"] == 0


@pytest.mark.asyncio
async def test_rtsp_sample_probe_rejects_invalid_ffprobe_json(
    tmp_path: Path,
) -> None:
    completed = subprocess.CompletedProcess(
        args=[],
        returncode=0,
        stdout="not-json",
        stderr="",
    )
    with patch.dict(
        os.environ,
        {"RTSP_SAMPLE_URL": "rtsp://example.test/video"},
        clear=False,
    ):
        async with _orchestrator_group(tmp_path) as (group, _config, _tmp_path):
            with patch(
                "vss_agents.orchestrator.tools.asyncio.to_thread",
                return_value=completed,
            ):
                result = await _call(
                    group,
                    "rtsp_sample_probe",
                    RtspSampleProbeInput(),
                )

    assert result["status"] == ComposeStatus.ERROR.value
    assert result["error_code"] == "invalid_probe_output"
    assert "diagnostic" not in result


@pytest.mark.asyncio
async def test_prereqs_gpu_indices_reject_unavailable_generate_override(tmp_path: Path):
    async with _orchestrator_group(tmp_path) as (group, _config, _tmp_path):
        with patch(
            "vss_agents.orchestrator.tools.run_prereqs_checks",
            return_value={"gpus": [{"index": 0}]},
        ):
            prereqs = await _call(group, "prereqs", DockerPrereqsInput())

        with patch("vss_agents.orchestrator.tools.create_dry_run_recipe") as mock_recipe:
            result = await _call(
                group,
                "docker_generate",
                GenerateInput(
                    profile="base",
                    env_overrides=["LLM_DEVICE_ID=0", "VLM_DEVICE_ID=1"],
                ),
            )

    assert prereqs["status"] == ComposeStatus.SUCCESS.value
    assert result["status"] == ComposeStatus.ERROR.value
    assert "VLM_DEVICE_ID=1" in result["error"]
    assert "detected GPU indices: 0" in result["error"]
    assert "Retry docker_generate without GPU device overrides" in result["error"]
    mock_recipe.assert_not_called()


@pytest.mark.asyncio
async def test_docker_read_unknown_compose_id(tmp_path: Path):
    async with _orchestrator_group(tmp_path) as (group, _config, _tmp_path):
        result = await _call(group, "docker_read", ComposeArtifactsInput(docker_compose_id="missing-id"))
    assert result["status"] == ComposeStatus.ERROR.value
    assert "Unknown docker_compose_id" in result["error"]


@pytest.mark.asyncio
async def test_docker_read_returns_artifact_contents(tmp_path: Path):
    async with _orchestrator_group(tmp_path) as (group, config, _tmp_path):
        compose_id = "base-test0001"
        env_path = Path(config.output_dir) / f"generated.{compose_id}.dry-run.env"
        compose_path = Path(config.output_dir) / f"compose.resolved.{compose_id}.dry-run.yml"
        _register_compose_spec(
            docker_compose_id=compose_id,
            env_path=env_path,
            compose_path=compose_path,
            profile="base",
        )

        result = await _call(group, "docker_read", ComposeArtifactsInput(docker_compose_id=compose_id))
    assert result["status"] == ComposeStatus.SUCCESS.value
    assert "VSS_DATA_DIR" in result["env_content"]
    assert "services:" in result["compose_yaml_content"]


@pytest.mark.asyncio
async def test_docker_list_success(tmp_path: Path):
    async with _orchestrator_group(tmp_path) as (group, _config, _tmp_path):
        completed = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=(
                "alpha\trunning\tUp 2 minutes (healthy)\talpha:latest\t0.0.0.0:8000->8000/tcp\n"
                "beta\tcreated\tCreated\tbeta:latest\t\n"
            ),
            stderr="",
        )

        with patch("vss_agents.orchestrator.tools.asyncio.to_thread", return_value=completed):
            result = await _call(
                group,
                "docker_list",
                tools_mod.ComposeContainersInput(all_containers=False),
            )
    assert result["status"] == ComposeStatus.SUCCESS.value
    assert result["container_names"] == ["alpha", "beta"]
    assert result["running_container_names"] == ["alpha"]
    assert result["containers"] == [
        {
            "name": "alpha",
            "state": "running",
            "status": "Up 2 minutes (healthy)",
            "health": "healthy",
            "image": "alpha:latest",
            "ports": "0.0.0.0:8000->8000/tcp",
        },
        {
            "name": "beta",
            "state": "created",
            "status": "Created",
            "health": "none",
            "image": "beta:latest",
            "ports": "",
        },
    ]
    assert result["includes_stopped"] is False
    assert "created or stopped" in result["readiness_warning"]


@pytest.mark.asyncio
async def test_docker_list_failure(tmp_path: Path):
    async with _orchestrator_group(tmp_path) as (group, _config, _tmp_path):
        completed = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="daemon down")

        with patch("vss_agents.orchestrator.tools.asyncio.to_thread", return_value=completed):
            result = await _call(
                group,
                "docker_list",
                tools_mod.ComposeContainersInput(all_containers=True),
            )
    assert result["status"] == ComposeStatus.ERROR.value
    assert "daemon down" in result["error"]


@pytest.mark.asyncio
async def test_docker_logs_success(tmp_path: Path):
    async with _orchestrator_group(tmp_path) as (group, _config, _tmp_path):
        completed = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="stdout line\nstderr line\n",
            stderr=None,
        )

        with patch("vss_agents.orchestrator.tools.asyncio.to_thread", return_value=completed) as to_thread:
            result = await _call(group, "docker_logs", ContainerLogsInput(container_name="vss-agent"))
    assert result["status"] == ComposeStatus.SUCCESS.value
    assert result["logs"] == "stdout line\nstderr line\n"
    assert result["logs_truncated"] is False
    assert result["streams_merged"] is True
    assert to_thread.call_args.kwargs["stdout"] is subprocess.PIPE
    assert to_thread.call_args.kwargs["stderr"] is subprocess.STDOUT
    assert "capture_output" not in to_thread.call_args.kwargs


@pytest.mark.asyncio
async def test_docker_logs_truncates_large_output(tmp_path: Path):
    async with _orchestrator_group(tmp_path) as (group, _config, _tmp_path):
        huge = "x" * (2 * 1024 * 1024)
        completed = subprocess.CompletedProcess(args=[], returncode=0, stdout=huge, stderr="")

        with patch("vss_agents.orchestrator.tools.asyncio.to_thread", return_value=completed):
            result = await _call(group, "docker_logs", ContainerLogsInput(container_name="vss-agent", tail=50))
    assert result["status"] == ComposeStatus.SUCCESS.value
    assert result["logs_truncated"] is True
    assert result["log_bytes"] <= tools_mod._MAX_DOCKER_LOG_RESPONSE_BYTES


@pytest.mark.asyncio
async def test_docker_logs_failure(tmp_path: Path):
    async with _orchestrator_group(tmp_path) as (group, _config, _tmp_path):
        completed = subprocess.CompletedProcess(
            args=[],
            returncode=1,
            stdout="No such container",
            stderr=None,
        )

        with patch("vss_agents.orchestrator.tools.asyncio.to_thread", return_value=completed):
            result = await _call(group, "docker_logs", ContainerLogsInput(container_name="missing"))
    assert result["status"] == ComposeStatus.ERROR.value
    assert "No such container" in result["error"]


@pytest.mark.asyncio
async def test_docker_status_unknown_operation(tmp_path: Path):
    async with _orchestrator_group(tmp_path) as (group, _config, _tmp_path):
        result = await _call(group, "docker_status", ComposeStatusInput(docker_compose_ops_id="missing-op"))
    assert result["status"] == ComposeStatus.ERROR.value
    assert "Unknown docker_compose_ops_id" in result["error"]


@pytest.mark.asyncio
async def test_docker_status_returns_operation_snapshot(tmp_path: Path):
    async with _orchestrator_group(tmp_path) as (group, _config, _tmp_path):
        ops_id = "up-base-deadbeef"
        _register_compose_operation(docker_compose_ops_id=ops_id, log_lines=["boot", "ready"])

        result = await _call(group, "docker_status", ComposeStatusInput(docker_compose_ops_id=ops_id, tail_lines=5))
    assert result["status"] == ComposeStatus.RUNNING.value
    assert result["docker_compose_ops_id"] == ops_id
    assert result["pid"] == 4242
    assert "ready" in result["log_excerpt"]


@pytest.mark.asyncio
async def test_docker_up_unknown_compose_id(tmp_path: Path):
    async with _orchestrator_group(tmp_path) as (group, _config, _tmp_path):
        result = await _call(group, "docker_up", ComposeUpOperationInput(docker_compose_id="missing"))
    assert result["status"] == ComposeStatus.ERROR.value
    assert "Unknown docker_compose_id" in result["error"]


@pytest.mark.asyncio
async def test_docker_up_starts_background_operation(tmp_path: Path):
    async with _orchestrator_group(tmp_path) as (group, config, _tmp_path):
        compose_id = "base-up00001"
        env_path = Path(config.output_dir) / f"generated.{compose_id}.dry-run.env"
        compose_path = Path(config.output_dir) / f"compose.resolved.{compose_id}.dry-run.yml"
        _register_compose_spec(docker_compose_id=compose_id, env_path=env_path, compose_path=compose_path)

        with patch("vss_agents.orchestrator.tools.threading.Thread") as mock_thread:
            mock_thread.return_value.start = MagicMock()
            result = await _call(
                group,
                "docker_up",
                ComposeUpOperationInput(
                    docker_compose_id=compose_id,
                    build=False,
                    force_recreate=True,
                    pull_always=True,
                ),
            )
    assert result["status"] == ComposeStatus.STARTED.value
    assert result["action"] == ComposeAction.UP.value
    assert "docker_compose_ops_id" in result
    mock_thread.return_value.start.assert_called_once()


@pytest.mark.asyncio
async def test_docker_down_unknown_compose_id(tmp_path: Path):
    async with _orchestrator_group(tmp_path) as (group, _config, _tmp_path):
        result = await _call(group, "docker_down", ComposeDownOperationInput(docker_compose_id="missing"))
    assert result["status"] == ComposeStatus.ERROR.value


@pytest.mark.asyncio
async def test_docker_down_starts_with_deep_clean_callback(tmp_path: Path):
    async with _orchestrator_group(tmp_path) as (group, config, _tmp_path):
        compose_id = "base-down0001"
        env_path = Path(config.output_dir) / f"generated.{compose_id}.dry-run.env"
        compose_path = Path(config.output_dir) / f"compose.resolved.{compose_id}.dry-run.yml"
        _register_compose_spec(docker_compose_id=compose_id, env_path=env_path, compose_path=compose_path)

        with patch("vss_agents.orchestrator.tools.threading.Thread") as mock_thread:
            mock_thread.return_value.start = MagicMock()
            result = await _call(
                group,
                "docker_down",
                ComposeDownOperationInput(
                    docker_compose_id=compose_id,
                    remove_volumes=False,
                    remove_orphans=False,
                    deep_clean=True,
                ),
            )
    assert result["status"] == ComposeStatus.STARTED.value
    assert result["action"] == ComposeAction.DOWN.value
    mock_thread.return_value.start.assert_called_once()


@pytest.mark.asyncio
async def test_docker_generate_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    async with _orchestrator_group(tmp_path) as (group, config, _tmp_path):
        mdx_data_dir = Path(config.mdx_data_dir)
        env_path = Path(config.output_dir) / "generated.base-abc12345.dry-run.env"
        compose_path = Path(config.output_dir) / "compose.resolved.base-abc12345.dry-run.yml"

        monkeypatch.setenv("NGC_CLI_API_KEY", "ngc-key")  # pragma: allowlist secret
        monkeypatch.setenv("NVIDIA_API_KEY", "nv-key")  # pragma: allowlist secret

        fake_recipe = MagicMock()
        resolved_env = {
            "VSS_DATA_DIR": str(mdx_data_dir),
            "HARDWARE_PROFILE": "H100",
            "HOST_IP": "127.0.0.1",
            "EXTERNALLY_ACCESSIBLE_IP": "127.0.0.1",
            "LLM_MODE": "local",
            "LLM_NAME": "test-llm",
            "VLM_MODE": "local",
            "VLM_NAME": "test-vlm",
            "COMPOSE_PROFILES": "base",
        }

        with (
            patch("vss_agents.orchestrator.tools.create_dry_run_recipe", return_value=fake_recipe) as mock_recipe,
            patch(
                "vss_agents.orchestrator.tools.generate_dry_run_artifacts",
                return_value=(resolved_env, env_path, compose_path),
            ),
        ):
            result = await _call(group, "docker_generate", GenerateInput(profile="base"))
    assert result["status"] == ComposeStatus.SUCCESS.value
    assert result["hardware_profile"] == "H100"
    assert "docker_compose_id" in result
    mock_recipe.assert_called_once()
    assert tools_mod._COMPOSE_SPECS.peek(result["docker_compose_id"]) is not None


@pytest.mark.asyncio
async def test_docker_generate_validation_error(tmp_path: Path):
    async with _orchestrator_group(tmp_path) as (group, _config, _tmp_path):
        from vss_agents.orchestrator.docker_compose_util import ValidationError

        with patch(
            "vss_agents.orchestrator.tools.create_dry_run_recipe",
            side_effect=ValidationError("bad profile mode"),
        ):
            result = await _call(group, "docker_generate", GenerateInput(profile="base"))
    assert result["status"] == ComposeStatus.ERROR.value
    assert "bad profile mode" in result["error"]


@pytest.mark.asyncio
async def test_docker_read_missing_artifact_files(tmp_path: Path):
    async with _orchestrator_group(tmp_path) as (group, config, _tmp_path):
        compose_id = "base-missing01"
        env_path = Path(config.output_dir) / f"generated.{compose_id}.dry-run.env"
        compose_path = Path(config.output_dir) / f"compose.resolved.{compose_id}.dry-run.yml"
        tools_mod._COMPOSE_SPECS.set(
            compose_id,
            {
                "docker_compose_id": compose_id,
                "profile": "base",
                "env_file": str(env_path),
                "compose_file": str(compose_path),
            },
        )
        result = await _call(group, "docker_read", ComposeArtifactsInput(docker_compose_id=compose_id))
    assert result["status"] == ComposeStatus.ERROR.value
    assert "not found" in result["error"]


@pytest.mark.asyncio
async def test_docker_status_coerces_unknown_status_to_error(tmp_path: Path):
    async with _orchestrator_group(tmp_path) as (group, _config, _tmp_path):
        ops_id = "up-base-unknown1"
        tools_mod._COMPOSE_OPERATIONS.set(
            ops_id,
            ComposeOperation(
                docker_compose_ops_id=ops_id,
                docker_compose_id="base-abc",
                action=ComposeAction.UP.value,
                pid=1,
                process=None,
                status="unexpected-state",
                running=False,
                exit_code=0,
                command="docker compose up",
                env_file="/tmp/env",
                compose_file="/tmp/compose.yml",
                started_at_epoch_s=1,
            ),
        )
        result = await _call(group, "docker_status", ComposeStatusInput(docker_compose_ops_id=ops_id))
    assert result["status"] == ComposeStatus.ERROR.value


@pytest.mark.asyncio
async def test_docker_up_watcher_marks_error_when_docker_missing(tmp_path: Path):
    async with _orchestrator_group(tmp_path) as (group, config, _tmp_path):
        compose_id = "base-watcher1"
        env_path = Path(config.output_dir) / f"generated.{compose_id}.dry-run.env"
        compose_path = Path(config.output_dir) / f"compose.resolved.{compose_id}.dry-run.yml"
        _register_compose_spec(docker_compose_id=compose_id, env_path=env_path, compose_path=compose_path)

        with (
            patch("vss_agents.orchestrator.tools.threading.Thread", side_effect=_run_target_immediately),
            patch("vss_agents.orchestrator.tools.subprocess.Popen", side_effect=FileNotFoundError("docker")),
        ):
            result = await _call(group, "docker_up", ComposeUpOperationInput(docker_compose_id=compose_id))

    assert result["status"] == ComposeStatus.STARTED.value
    ops_id = result["docker_compose_ops_id"]
    op = tools_mod._COMPOSE_OPERATIONS.peek(ops_id)
    assert op is not None
    assert op.status == ComposeStatus.ERROR.value
    assert op.exit_code == 127


@pytest.mark.asyncio
async def test_include_subset_exposes_only_requested_tools(tmp_path: Path):
    config = _make_orchestrator_config(tmp_path, include=["profiles"])
    builder = MagicMock()
    async with vss_orchestrator(config, builder) as group:
        assert set(group._functions.keys()) == {"profiles"}


# ---------------------------------------------------------------------------
# Easy coverage: startup, validation errors, docker_generate branches
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_startup_bootstrap_failure(tmp_path: Path):
    config = _make_orchestrator_config(tmp_path)
    builder = MagicMock()
    with (
        patch(
            "vss_agents.orchestrator.tools.ensure_data_directories",
            side_effect=RuntimeError("permission denied"),
        ),
        pytest.raises(RuntimeError, match="Startup directory bootstrap failed"),
    ):
        async with vss_orchestrator(config, builder):
            pass


@pytest.mark.asyncio
async def test_startup_masks_secret_env_values(capsys, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("NGC_CLI_API_KEY", "super-secret-key")  # pragma: allowlist secret
    monkeypatch.setenv("HARDWARE_PROFILE", "H100")
    config = _make_orchestrator_config(tmp_path, include=["profiles"])
    builder = MagicMock()
    async with vss_orchestrator(config, builder) as group:
        await _call(group, "profiles", DockerProfilesInput())
    output = capsys.readouterr().out
    assert "NGC_CLI_API_KEY=<set, 16 chars>" in output
    assert "HARDWARE_PROFILE=H100" in output
    assert "super-secret-key" not in output


@pytest.mark.asyncio
async def test_docker_up_missing_compose_file(tmp_path: Path):
    async with _orchestrator_group(tmp_path) as (group, config, _tmp_path):
        compose_id = "base-nocompose"
        env_path = Path(config.output_dir) / f"generated.{compose_id}.dry-run.env"
        compose_path = Path(config.output_dir) / f"compose.resolved.{compose_id}.dry-run.yml"
        env_path.parent.mkdir(parents=True, exist_ok=True)
        env_path.write_text("VSS_DATA_DIR=/tmp/vss-data\n")
        tools_mod._COMPOSE_SPECS.set(
            compose_id,
            {
                "docker_compose_id": compose_id,
                "profile": "base",
                "env_file": str(env_path),
                "compose_file": str(compose_path),
            },
        )
        result = await _call(group, "docker_up", ComposeUpOperationInput(docker_compose_id=compose_id))
    assert result["status"] == ComposeStatus.ERROR.value
    assert "Resolved compose file not found" in result["error"]


@pytest.mark.asyncio
async def test_docker_up_missing_vss_data_dir(tmp_path: Path):
    async with _orchestrator_group(tmp_path) as (group, config, _tmp_path):
        compose_id = "base-novssdir"
        env_path = Path(config.output_dir) / f"generated.{compose_id}.dry-run.env"
        compose_path = Path(config.output_dir) / f"compose.resolved.{compose_id}.dry-run.yml"
        _register_compose_spec(
            docker_compose_id=compose_id,
            env_path=env_path,
            compose_path=compose_path,
            env_text="HOST_IP=127.0.0.1\n",
        )
        result = await _call(group, "docker_up", ComposeUpOperationInput(docker_compose_id=compose_id))
    assert result["status"] == ComposeStatus.ERROR.value
    assert "VSS_DATA_DIR is missing" in result["error"]


@pytest.mark.asyncio
async def test_docker_generate_applies_device_ids_from_runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("LLM_DEVICE_ID", "2")
    monkeypatch.setenv("VLM_DEVICE_ID", "3")
    config = _make_orchestrator_config(tmp_path)
    builder = MagicMock()
    async with vss_orchestrator(config, builder) as group:
        mdx_data_dir = Path(config.mdx_data_dir)
        env_path = Path(config.output_dir) / "generated.base-devices.dry-run.env"
        compose_path = Path(config.output_dir) / "compose.resolved.base-devices.dry-run.yml"
        resolved_env = {"VSS_DATA_DIR": str(mdx_data_dir)}
        fake_recipe = MagicMock()

        with (
            patch("vss_agents.orchestrator.tools.create_dry_run_recipe", return_value=fake_recipe) as mock_recipe,
            patch(
                "vss_agents.orchestrator.tools.generate_dry_run_artifacts",
                return_value=(resolved_env, env_path, compose_path),
            ),
        ):
            result = await _call(group, "docker_generate", GenerateInput(profile="base"))

    assert result["status"] == ComposeStatus.SUCCESS.value
    env_overrides = mock_recipe.call_args.kwargs["env_overrides"]
    assert env_overrides["LLM_DEVICE_ID"] == "2"
    assert env_overrides["VLM_DEVICE_ID"] == "3"


@pytest.mark.asyncio
async def test_docker_generate_alerts_profile(tmp_path: Path):
    config = _make_orchestrator_config(tmp_path).model_copy(
        update={
            "profile_mode_to_env_modes": {
                "alerts": {"verification": "2d_cv", "real-time": "2d_vlm"},
            },
        }
    )
    builder = MagicMock()
    async with vss_orchestrator(config, builder) as group:
        mdx_data_dir = Path(config.mdx_data_dir)
        env_path = Path(config.output_dir) / "generated.alerts-abc.dry-run.env"
        compose_path = Path(config.output_dir) / "compose.resolved.alerts-abc.dry-run.yml"
        resolved_env = {"VSS_DATA_DIR": str(mdx_data_dir)}
        fake_recipe = MagicMock()

        with (
            patch("vss_agents.orchestrator.tools.create_dry_run_recipe", return_value=fake_recipe),
            patch(
                "vss_agents.orchestrator.tools.generate_dry_run_artifacts",
                return_value=(resolved_env, env_path, compose_path),
            ),
            patch("vss_agents.orchestrator.tools.ensure_alerts_engine_directories") as mock_alerts_dirs,
        ):
            result = await _call(group, "docker_generate", GenerateInput(profile="alerts", profile_mode="verification"))

    assert result["status"] == ComposeStatus.SUCCESS.value
    mock_alerts_dirs.assert_called_once()


@pytest.mark.asyncio
async def test_prereqs_uncaught_exception_propagates_through_mcp_wrapper(tmp_path: Path):
    async with _orchestrator_group(tmp_path) as (group, _config, _tmp_path):
        with patch(
            "vss_agents.orchestrator.tools.asyncio.to_thread",
            side_effect=ValueError("unexpected prereqs failure"),
        ):
            with pytest.raises(ValueError, match="unexpected prereqs failure"):
                await _call(group, "prereqs", DockerPrereqsInput())


# ---------------------------------------------------------------------------
# Medium coverage: pre-compose checks, conflicts, watcher success paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_docker_up_precompose_check_failure(tmp_path: Path):
    async with _orchestrator_group(tmp_path) as (group, config, _tmp_path):
        compose_id = "base-precheck"
        env_path = Path(config.output_dir) / f"generated.{compose_id}.dry-run.env"
        compose_path = Path(config.output_dir) / f"compose.resolved.{compose_id}.dry-run.yml"
        _register_compose_spec(docker_compose_id=compose_id, env_path=env_path, compose_path=compose_path)

        with (
            patch("vss_agents.orchestrator.tools.threading.Thread", side_effect=_run_target_immediately),
            patch(
                "vss_agents.orchestrator.tools.ensure_data_directories",
                side_effect=RuntimeError("data dir not writable"),
            ),
        ):
            result = await _call(group, "docker_up", ComposeUpOperationInput(docker_compose_id=compose_id))

    assert result["status"] == ComposeStatus.STARTED.value
    op = tools_mod._COMPOSE_OPERATIONS.peek(result["docker_compose_ops_id"])
    assert op is not None
    assert op.status == ComposeStatus.ERROR.value
    assert op.exit_code == 1
    assert any("Pre-compose check failed" in line for line in op.log_lines)


@pytest.mark.asyncio
async def test_docker_up_search_profile_runs_model_artifact_check(tmp_path: Path):
    config = _make_orchestrator_config(
        tmp_path,
        model_artifacts={
            "search": (
                ModelPackageConfig(
                    package_ref="nvidia/pkg:1",
                    artifacts=(ModelArtifactEntry(src="model.onnx", out="model.onnx", kind="file"),),
                ),
            ),
        },
    )
    async with _orchestrator_group(tmp_path, config=config) as (group, cfg, _tmp_path):
        compose_id = "search-models"
        env_path = Path(cfg.output_dir) / f"generated.{compose_id}.dry-run.env"
        compose_path = Path(cfg.output_dir) / f"compose.resolved.{compose_id}.dry-run.yml"
        _register_compose_spec(
            docker_compose_id=compose_id,
            env_path=env_path,
            compose_path=compose_path,
            profile="search",
        )

        with (
            patch("vss_agents.orchestrator.tools.threading.Thread", side_effect=_run_target_immediately),
            patch("vss_agents.orchestrator.tools.ensure_model_artifacts") as mock_models,
            patch("vss_agents.orchestrator.tools.subprocess.Popen", side_effect=FileNotFoundError("docker")),
        ):
            result = await _call(group, "docker_up", ComposeUpOperationInput(docker_compose_id=compose_id))

    assert result["status"] == ComposeStatus.STARTED.value
    mock_models.assert_called_once()


@pytest.mark.parametrize(
    ("profile_mode", "expected_model_checks"),
    (("2d_vlm", 0), ("2d_cv", 1), ("", 1)),
)
@pytest.mark.asyncio
async def test_docker_up_alerts_model_check_matches_profile_mode(
    tmp_path: Path,
    profile_mode: str,
    expected_model_checks: int,
) -> None:
    config = _make_orchestrator_config(
        tmp_path,
        model_artifacts={
            "alerts": (
                ModelPackageConfig(
                    package_ref="nvidia/pkg:1",
                    artifacts=(
                        ModelArtifactEntry(
                            src="model.onnx",
                            out="model.onnx",
                            kind="file",
                        ),
                    ),
                ),
            ),
        },
    )
    async with _orchestrator_group(tmp_path, config=config) as (group, cfg, _tmp_path):
        compose_id = f"alerts-models-{profile_mode}"
        env_path = Path(cfg.output_dir) / f"generated.{compose_id}.dry-run.env"
        compose_path = Path(cfg.output_dir) / f"compose.resolved.{compose_id}.dry-run.yml"
        _register_compose_spec(
            docker_compose_id=compose_id,
            env_path=env_path,
            compose_path=compose_path,
            profile="alerts",
            env_text=(f"VSS_DATA_DIR={cfg.mdx_data_dir}\nNGC_CLI_API_KEY=test\nMODE={profile_mode}\n"),
        )

        with (
            patch(
                "vss_agents.orchestrator.tools.threading.Thread",
                side_effect=_run_target_immediately,
            ),
            patch("vss_agents.orchestrator.tools.ensure_model_artifacts") as mock_models,
            patch("vss_agents.orchestrator.tools.ensure_alerts_engine_directories"),
            patch(
                "vss_agents.orchestrator.tools.subprocess.Popen",
                lambda *args, **kwargs: _FakePopen(*args, **kwargs),
            ),
        ):
            result = await _call(
                group,
                "docker_up",
                ComposeUpOperationInput(docker_compose_id=compose_id),
            )

    assert result["status"] == ComposeStatus.STARTED.value
    assert mock_models.call_count == expected_model_checks


@pytest.mark.asyncio
async def test_docker_up_alerts_profile_runs_engine_directory_check(tmp_path: Path):
    async with _orchestrator_group(tmp_path) as (group, config, _tmp_path):
        compose_id = "alerts-engines"
        env_path = Path(config.output_dir) / f"generated.{compose_id}.dry-run.env"
        compose_path = Path(config.output_dir) / f"compose.resolved.{compose_id}.dry-run.yml"
        _register_compose_spec(
            docker_compose_id=compose_id,
            env_path=env_path,
            compose_path=compose_path,
            profile="alerts",
        )

        with (
            patch("vss_agents.orchestrator.tools.threading.Thread", side_effect=_run_target_immediately),
            patch("vss_agents.orchestrator.tools.ensure_alerts_engine_directories") as mock_engines,
            patch("vss_agents.orchestrator.tools.subprocess.Popen", side_effect=FileNotFoundError("docker")),
        ):
            result = await _call(group, "docker_up", ComposeUpOperationInput(docker_compose_id=compose_id))

    assert result["status"] == ComposeStatus.STARTED.value
    mock_engines.assert_called_once()


@pytest.mark.asyncio
async def test_docker_up_rejected_when_up_already_running(tmp_path: Path):
    async with _orchestrator_group(tmp_path) as (group, config, _tmp_path):
        compose_id = "base-conflict1"
        env_path = Path(config.output_dir) / f"generated.{compose_id}.dry-run.env"
        compose_path = Path(config.output_dir) / f"compose.resolved.{compose_id}.dry-run.yml"
        _register_compose_spec(docker_compose_id=compose_id, env_path=env_path, compose_path=compose_path)
        _register_running_compose_operation(
            docker_compose_ops_id="up-base-running01",
            docker_compose_id=compose_id,
            action=ComposeAction.UP.value,
        )

        result = await _call(group, "docker_up", ComposeUpOperationInput(docker_compose_id=compose_id))
    assert result["status"] == ComposeStatus.ERROR.value
    assert "already running" in result["error"]


@pytest.mark.asyncio
async def test_docker_down_rejected_when_down_already_running(tmp_path: Path):
    async with _orchestrator_group(tmp_path) as (group, config, _tmp_path):
        compose_id = "base-conflict2"
        env_path = Path(config.output_dir) / f"generated.{compose_id}.dry-run.env"
        compose_path = Path(config.output_dir) / f"compose.resolved.{compose_id}.dry-run.yml"
        _register_compose_spec(docker_compose_id=compose_id, env_path=env_path, compose_path=compose_path)
        _register_running_compose_operation(
            docker_compose_ops_id="down-base-running01",
            docker_compose_id=compose_id,
            action=ComposeAction.DOWN.value,
        )

        result = await _call(group, "docker_down", ComposeDownOperationInput(docker_compose_id=compose_id))
    assert result["status"] == ComposeStatus.ERROR.value
    assert "already running" in result["error"]


@pytest.mark.asyncio
async def test_docker_up_ignored_when_down_running(tmp_path: Path):
    async with _orchestrator_group(tmp_path) as (group, config, _tmp_path):
        compose_id = "base-conflict3"
        env_path = Path(config.output_dir) / f"generated.{compose_id}.dry-run.env"
        compose_path = Path(config.output_dir) / f"compose.resolved.{compose_id}.dry-run.yml"
        _register_compose_spec(docker_compose_id=compose_id, env_path=env_path, compose_path=compose_path)
        _register_running_compose_operation(
            docker_compose_ops_id="down-base-running02",
            docker_compose_id=compose_id,
            action=ComposeAction.DOWN.value,
        )

        result = await _call(group, "docker_up", ComposeUpOperationInput(docker_compose_id=compose_id))
    assert result["status"] == ComposeStatus.IGNORED.value
    assert "Ignoring incoming compose up" in result["message"]


@pytest.mark.asyncio
async def test_docker_down_cancels_running_up_and_terminates_process(tmp_path: Path):
    async with _orchestrator_group(tmp_path) as (group, config, _tmp_path):
        compose_id = "base-preempt"
        env_path = Path(config.output_dir) / f"generated.{compose_id}.dry-run.env"
        compose_path = Path(config.output_dir) / f"compose.resolved.{compose_id}.dry-run.yml"
        _register_compose_spec(docker_compose_id=compose_id, env_path=env_path, compose_path=compose_path)

        up_process = MagicMock()
        up_process.poll.return_value = None
        up_process.wait.side_effect = [None]
        up_op = _register_running_compose_operation(
            docker_compose_ops_id="up-base-preempt01",
            docker_compose_id=compose_id,
            action=ComposeAction.UP.value,
            process=up_process,
        )

        with patch("vss_agents.orchestrator.tools.threading.Thread") as mock_thread:
            mock_thread.return_value.start = MagicMock()
            result = await _call(group, "docker_down", ComposeDownOperationInput(docker_compose_id=compose_id))

    assert result["status"] == ComposeStatus.STARTED.value
    assert up_op.status == ComposeStatus.CANCELLED.value
    assert up_op.running is False
    up_process.terminate.assert_called_once()


@pytest.mark.asyncio
async def test_docker_up_watcher_success_streams_logs_and_finishes(tmp_path: Path):
    async with _orchestrator_group(tmp_path) as (group, config, _tmp_path):
        compose_id = "base-success1"
        env_path = Path(config.output_dir) / f"generated.{compose_id}.dry-run.env"
        compose_path = Path(config.output_dir) / f"compose.resolved.{compose_id}.dry-run.yml"
        _register_compose_spec(docker_compose_id=compose_id, env_path=env_path, compose_path=compose_path)

        with (
            patch("vss_agents.orchestrator.tools.threading.Thread", side_effect=_run_target_immediately),
            patch(
                "vss_agents.orchestrator.tools.subprocess.Popen",
                lambda *args, **kwargs: _FakePopen(*args, lines=["pull complete", "started"], exit_code=0, **kwargs),
            ),
        ):
            result = await _call(group, "docker_up", ComposeUpOperationInput(docker_compose_id=compose_id))

    assert result["status"] == ComposeStatus.STARTED.value
    op = tools_mod._COMPOSE_OPERATIONS.peek(result["docker_compose_ops_id"])
    assert op is not None
    assert op.status == ComposeStatus.SUCCESS.value
    assert op.exit_code == 0
    assert op.pid == 4321
    assert any("started" in line for line in op.log_lines)
    assert any("Compose operation succeeded" in line for line in op.log_lines)


@pytest.mark.asyncio
async def test_docker_up_watcher_stops_on_terminal_dependency_failure(tmp_path: Path):
    async with _orchestrator_group(tmp_path) as (group, config, _tmp_path):
        compose_id = "base-depfail1"
        env_path = Path(config.output_dir) / f"generated.{compose_id}.dry-run.env"
        compose_path = Path(config.output_dir) / f"compose.resolved.{compose_id}.dry-run.yml"
        _register_compose_spec(docker_compose_id=compose_id, env_path=env_path, compose_path=compose_path)

        process = _FakePopen(
            lines=[
                "Container vss-vios-postgres Waiting",
                "Container vss-vios-postgres Error dependency centralizedb failed to start",
            ],
            exit_code=0,
        )
        with (
            patch("vss_agents.orchestrator.tools.threading.Thread", side_effect=_run_target_immediately),
            patch("vss_agents.orchestrator.tools.subprocess.Popen", return_value=process),
        ):
            result = await _call(group, "docker_up", ComposeUpOperationInput(docker_compose_id=compose_id))

    op = tools_mod._COMPOSE_OPERATIONS.peek(result["docker_compose_ops_id"])
    assert op is not None
    assert op.status == ComposeStatus.ERROR.value
    assert op.running is False
    assert op.exit_code == 1
    assert process.terminate_calls == 1
    assert any("Detected terminal compose dependency failure" in line for line in op.log_lines)
    assert any("retry docker_up after remediation" in line for line in op.log_lines)


@pytest.mark.asyncio
async def test_docker_down_deep_clean_failure_after_successful_compose(tmp_path: Path):
    async with _orchestrator_group(tmp_path) as (group, config, _tmp_path):
        compose_id = "base-deepfail"
        env_path = Path(config.output_dir) / f"generated.{compose_id}.dry-run.env"
        compose_path = Path(config.output_dir) / f"compose.resolved.{compose_id}.dry-run.yml"
        _register_compose_spec(docker_compose_id=compose_id, env_path=env_path, compose_path=compose_path)

        with (
            patch("vss_agents.orchestrator.tools.threading.Thread", side_effect=_run_target_immediately),
            patch(
                "vss_agents.orchestrator.tools.subprocess.Popen",
                lambda *args, **kwargs: _FakePopen(*args, exit_code=0, **kwargs),
            ),
            patch(
                "vss_agents.orchestrator.tools._run_deep_clean",
                side_effect=RuntimeError("deep clean failed"),
            ),
        ):
            result = await _call(
                group,
                "docker_down",
                ComposeDownOperationInput(docker_compose_id=compose_id, deep_clean=True),
            )

    assert result["status"] == ComposeStatus.STARTED.value
    op = tools_mod._COMPOSE_OPERATIONS.peek(result["docker_compose_ops_id"])
    assert op is not None
    assert op.status == ComposeStatus.ERROR.value
    assert op.exit_code == 1
    assert any("Post-success step failed" in line for line in op.log_lines)


@pytest.mark.asyncio
async def test_docker_up_watcher_exits_before_popen_when_cancelled(tmp_path: Path):
    async with _orchestrator_group(tmp_path) as (group, config, _tmp_path):
        compose_id = "base-cancel1"
        env_path = Path(config.output_dir) / f"generated.{compose_id}.dry-run.env"
        compose_path = Path(config.output_dir) / f"compose.resolved.{compose_id}.dry-run.yml"
        _register_compose_spec(docker_compose_id=compose_id, env_path=env_path, compose_path=compose_path)

        def _cancel_ops_after_precheck(*_args, **_kwargs):
            for op in list(tools_mod._COMPOSE_OPERATIONS.values()):
                if op.docker_compose_id == compose_id and op.action == ComposeAction.UP.value:
                    op.running = False
                    op.status = ComposeStatus.CANCELLED.value

        popen_calls: list[tuple] = []

        def _track_popen(*args, **kwargs):
            popen_calls.append((args, kwargs))
            return _FakePopen(*args, **kwargs)

        with (
            patch("vss_agents.orchestrator.tools.threading.Thread", side_effect=_run_target_immediately),
            patch(
                "vss_agents.orchestrator.tools.ensure_data_directories",
                side_effect=_cancel_ops_after_precheck,
            ),
            patch("vss_agents.orchestrator.tools.subprocess.Popen", side_effect=_track_popen),
        ):
            result = await _call(group, "docker_up", ComposeUpOperationInput(docker_compose_id=compose_id))

    assert result["status"] == ComposeStatus.STARTED.value
    assert popen_calls == []


@pytest.mark.asyncio
async def test_terminate_running_op_kills_on_wait_timeout(tmp_path: Path):
    async with _orchestrator_group(tmp_path) as (group, config, _tmp_path):
        compose_id = "base-kill1"
        env_path = Path(config.output_dir) / f"generated.{compose_id}.dry-run.env"
        compose_path = Path(config.output_dir) / f"compose.resolved.{compose_id}.dry-run.yml"
        _register_compose_spec(docker_compose_id=compose_id, env_path=env_path, compose_path=compose_path)

        up_process = _FakePopen("docker", "compose", "up")
        up_process._wait_timeout_expired = True

        def _wait_with_timeout(timeout=None):
            if timeout is not None:
                raise subprocess.TimeoutExpired(cmd=up_process.args, timeout=timeout)
            return 0

        up_process.wait = _wait_with_timeout  # type: ignore[method-assign]
        _register_running_compose_operation(
            docker_compose_ops_id="up-base-kill01",
            docker_compose_id=compose_id,
            action=ComposeAction.UP.value,
            process=up_process,
        )

        with patch("vss_agents.orchestrator.tools.threading.Thread") as mock_thread:
            mock_thread.return_value.start = MagicMock()
            await _call(group, "docker_down", ComposeDownOperationInput(docker_compose_id=compose_id))

    assert up_process.terminate_calls == 1
    assert up_process.kill_calls == 1
