# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Exercise shared VIOS staging and scoped Thor runtime validation without a GPU."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

TOOLS = Path(__file__).resolve().parents[1]
ROOT = TOOLS.parents[1]


def module(name):
    spec = importlib.util.spec_from_file_location(name, TOOLS / (name + ".py"))
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


@pytest.mark.parametrize("service", ["rt-vlm", "rt-embed"])
def test_both_services_stage_complete_repository_deployment(service, tmp_path):
    if not shutil.which("docker"):
        pytest.skip("Compose CLI required for offline configuration rendering")
    staged = tmp_path / service
    videos = tmp_path / "original-embed-videos"
    subprocess.run([
        sys.executable, str(ROOT / "services/rtvi" / service / "perf/scripts/stage_vst.py"),
    ], check=True, capture_output=True, text=True, env={
        "PATH": os.environ["PATH"], "VST_DIR": str(staged),
        "PERF_VIDEOS_DIR": str(videos), "VST_COMPOSE_PROJECT": "run-123",
    })
    for relative in (
        "nvstreamer/configs/vst_config.json", "nvstreamer/configs/vst_storage.json",
        "nvstreamer/configs/notification_config.json", "scripts/user_additional_install.sh",
    ):
        assert (staged / relative).is_file()
    result = subprocess.run([
        "docker", "compose", "--env-file", str(staged / "nvstreamer/compose.env"),
        "-f", str(staged / "nvstreamer/docker-compose.yaml"), "config", "--format", "json",
    ], env={"PATH": os.environ["PATH"]}, check=True, capture_output=True, text=True)
    service = json.loads(result.stdout)["services"]["nvstreamer-1"]
    assert service["image"] == "nvcr.io/nvidia/vss-core/vss-vios-nvstreamer:3.2.0"
    assert service["network_mode"] == "host"
    assert service["environment"]["HTTP_PORT"] == "31000"
    assert service["environment"]["RTSP_SERVER_PORT"] == "31554"
    assert any(v.get("source") == str(videos) for v in service["volumes"])
    assert "if [ -x /home/vst/vst_release/tools/configure_nvstreamer_ui.sh ]" in service["entrypoint"][-1]
    assert not videos.exists()  # Staging must not create or replace workload data.


def test_missing_source_files_fail_before_staging(tmp_path, monkeypatch):
    staged = tmp_path / "staged"
    monkeypatch.setenv("VST_DIR", str(staged))
    with pytest.raises(ValueError, match="checkout is incomplete"):
        module("stage_vst").stage(source=tmp_path / "missing")
    assert not staged.exists()


@pytest.mark.parametrize("component", ["vst", "nvstreamer"])
def test_shared_deploy_scopes_projects_and_runtime_overlay(tmp_path, component):
    deploy = tmp_path / "deploy.sh"
    shutil.copyfile(TOOLS / "deploy_vst.sh", deploy)
    (tmp_path / ".compose-project").write_text("run-123\n")
    directory = tmp_path / ("stream-processing" if component == "vst" else component)
    directory.mkdir()
    (directory / "thor-runtime.json").write_text("{}")
    docker = tmp_path / "docker"
    docker.write_text("#!/usr/bin/env python3\nimport json, sys; print(json.dumps(sys.argv[1:]))\n")
    docker.chmod(0o755)
    env = {"PATH": str(tmp_path) + os.pathsep + os.environ["PATH"],
           "COMPOSE_PROFILES": "unrelated-profile", "COMPOSE_PROJECT_NAME": "run-123-" + component,
           "PERF_VST_COMPONENT_PROJECT": "run-123-" + component}
    result = subprocess.run(["bash", str(deploy), "down", component],
                            env=env, check=True, capture_output=True, text=True)
    args = json.loads(result.stdout)
    assert args[:3] == ["compose", "--project-name", "run-123-" + component]
    assert str(directory / "thor-runtime.json") in args
    assert args[-1] == "down"
    env["COMPOSE_PROJECT_NAME"] = "unrelated-monitoring"
    result = subprocess.run(["bash", str(deploy), "down", component], env=env, capture_output=True)
    assert result.returncode != 0
    assert not result.stdout


def completed(stdout="", stderr="", returncode=0):
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


def test_thor_detection_uses_selected_gpu_on_mixed_host(monkeypatch):
    runtime = module("vst_runtime")
    monkeypatch.setattr(runtime, "command", lambda _: completed("0, NVIDIA Thor\n1, NVIDIA RTX PRO 6000 Blackwell\n"))
    assert runtime.selected_thor("0", "aarch64") == {"host_gpu": "0", "name": "NVIDIA Thor"}
    assert runtime.selected_thor("1", "aarch64") is None
    with pytest.raises(RuntimeError, match="one explicitly selected"):
        runtime.selected_thor("0,1", "aarch64")
    assert runtime.selected_thor("0", "x86_64") is None


@pytest.mark.parametrize("error", ["libcuda.so.1: file too short", "cuInit returned 100"])
def test_cuda_failure_retains_diagnostics_and_never_generates_overlay(tmp_path, monkeypatch, error):
    runtime = module("vst_runtime")
    monkeypatch.setattr(runtime, "selected_thor", lambda _: {"host_gpu": "0", "name": "NVIDIA Thor"})
    monkeypatch.setattr(runtime, "command", lambda _: completed('{"nvidia": {}}'))
    def probe(*args):
        raise RuntimeError(error)
    monkeypatch.setattr(runtime, "probe_image", probe)
    report = tmp_path / "diagnostics/vst-runtime.json"
    with pytest.raises(RuntimeError, match="global runtime"):
        runtime.prepare(tmp_path, "0", ["example:3.2.0"], report)
    assert json.loads(report.read_text())["reason"] == error
    assert not list(tmp_path.glob("*/thor-runtime.json"))


def test_non_thor_runtime_is_unchanged(tmp_path, monkeypatch):
    runtime = module("vst_runtime")
    monkeypatch.setattr(runtime, "selected_thor", lambda _: None)
    def forbidden(*args, **kwargs):
        pytest.fail("Non-Thor must not inspect, pull or launch any probe containers")
    monkeypatch.setattr(runtime, "probe_image", forbidden)
    runtime.prepare(tmp_path, "0", ["example:3.2.0"], tmp_path / "report.json",
                    rtvi_image="rtvi:test", sensor_image="sensor:test", stream_image="stream:test",
                    rtvi_overlay=tmp_path / "rtvi-thor-runtime.json")
    assert not list(tmp_path.iterdir())


def test_runtime_selection_applies_only_after_successful_thor_probe(tmp_path, monkeypatch):
    runtime = module("vst_runtime")
    monkeypatch.setattr(runtime, "selected_thor", lambda _: {"host_gpu": "0", "name": "NVIDIA Thor"})
    monkeypatch.setattr(runtime, "command", lambda _: completed('{"nvidia-thor": {}}'))
    monkeypatch.setattr(runtime, "probe_image", lambda *args: {"image": args[0], "image_id": "sha256:test", "devices": ["NVIDIA Thor"], "ld_library_path": "/usr/lib/aarch64-linux-gnu/nvidia:/existing"})
    for directory in ["stream-processing", "nvstreamer"]:
        (tmp_path / directory).mkdir()
    report = tmp_path / "report.json"
    runtime.prepare(tmp_path, "0", ["example:3.2.0"], report, "nvidia-thor")
    model = json.loads((tmp_path / "nvstreamer/thor-runtime.json").read_text())
    assert set(model["services"]) == {"nvstreamer-1"}
    service = model["services"]["nvstreamer-1"]
    assert service["runtime"] == "nvidia-thor"
    assert service["environment"]["NVIDIA_VISIBLE_DEVICES"] == "0"
    assert json.loads(report.read_text())["status"] == "PASS"


def test_probe_checks_arm64_and_cleans_up_without_privileged_access():
    runtime = module("vst_runtime")
    calls = []
    def docker(args, **kwargs):
        calls.append(args)
        if args[1:3] == ["image", "inspect"]:
            return completed('{"Architecture":"arm64","Os":"linux","Id":"sha256:test"}')
        if args[1] == "run":
            return completed('{"devices":["NVIDIA Thor"]}')
        return completed()
    assert runtime.probe_image("example:3.2.0", "nvidia", "0", docker)["devices"] == ["NVIDIA Thor"]
    run = next(args for args in calls if args[1] == "run")
    assert "--privileged" not in run
    assert not any(arg.startswith("--device") or arg.startswith("--mount") for arg in run)
    assert "NVIDIA_VISIBLE_DEVICES=0" in run
    assert calls[-1][1:3] == ["rm", "-f"]


@pytest.mark.parametrize("architecture,names", [
    ("amd64", ["NVIDIA Thor"]),
    ("arm64", ["NVIDIA RTX PRO 6000 Blackwell"]),
    ("arm64", ["NVIDIA Thor", "NVIDIA RTX PRO 6000 Blackwell"]),
])
def test_probe_rejects_wrong_architecture_or_gpu(architecture, names):
    runtime = module("vst_runtime")
    calls = []
    def docker(args, **kwargs):
        calls.append(args)
        if args[1:3] == ["image", "inspect"]:
            return completed(json.dumps({"Architecture": architecture, "Os": "linux", "Id": "sha256:test"}))
        if args[1] == "run":
            return completed(json.dumps({"devices": names}))
        return completed()
    with pytest.raises(RuntimeError, match="linux/arm64|selected Thor GPU"):
        runtime.probe_image("example:3.2.0", "nvidia", "0", docker)
    if architecture == "amd64":
        assert not any(args[1] == "run" for args in calls)


def test_rtvi_failure_does_not_leave_vios_runtime_overrides(tmp_path, monkeypatch):
    runtime = module("vst_runtime")
    monkeypatch.setattr(runtime, "selected_thor", lambda _: {"host_gpu": "0", "name": "NVIDIA Thor"})
    monkeypatch.setattr(runtime, "command", lambda _: completed('{"nvidia": {}}'))
    calls = []
    def probe(image, *args, **kwargs):
        calls.append((image, kwargs))
        if image == "rtvi:test":
            raise RuntimeError("cuInit returned 100")
        return {"image_id": "sha256:test", "devices": ["NVIDIA Thor"]}
    monkeypatch.setattr(runtime, "probe_image", probe)
    with pytest.raises(RuntimeError, match="cuInit returned 100"):
        runtime.prepare(tmp_path, "0", ["vios:3.2.0"], tmp_path / "report.json", rtvi_image="rtvi:test")
    assert calls[-1] == ("rtvi:test", {"vios": False})
    assert not list(tmp_path.glob("*/thor-runtime.json"))
    assert json.loads((tmp_path / "report.json").read_text())["status"] == "ERROR"


@pytest.mark.parametrize('existing', ['', '/usr/local/cuda/lib64:/opt/rtvi/lib',
                                      '/usr/lib/aarch64-linux-gnu/nvidia:/opt/rtvi/lib',
                                      '$ORIGIN:/opt/path with spaces'])
def test_thor_probe_retains_image_library_paths(existing):
    runtime = module('vst_runtime')
    calls = []
    def docker(args, **kwargs):
        calls.append(args)
        if args[1:3] == ['image', 'inspect']:
            return completed(json.dumps({'Architecture': 'arm64', 'Os': 'linux', 'Id': 'sha256:test',
                                         'Config': {'Env': ['LD_LIBRARY_PATH=' + existing]}}))
        if args[1] == 'run':
            return completed('{"devices":["NVIDIA Thor"]}')
        return completed()
    result = runtime.probe_image('example:3.2.0', 'nvidia', '0', docker)
    expected = existing if existing.startswith(runtime.THOR_DRIVER_LIBRARY_DIR + ':') else (
        runtime.THOR_DRIVER_LIBRARY_DIR + (':' + existing if existing else ''))
    run = next(args for args in calls if args[1] == 'run')
    assert 'LD_LIBRARY_PATH=' + expected in run
    assert result['ld_library_path'] == expected
    assert all('--privileged' not in args for args in calls)


@pytest.mark.parametrize('service', ['rt-vlm', 'rt-embed'])
def test_thor_startup_matches_preflight_and_preserves_compose_configuration(tmp_path, monkeypatch, service):
    if not shutil.which('docker'):
        pytest.skip('Compose CLI required for offline configuration rendering')
    runtime = module('vst_runtime')
    monkeypatch.setenv('VST_DIR', str(tmp_path))
    module('stage_vst').stage()
    images = {
        'nvstreamer': 'nvcr.io/nvidia/vss-core/vss-vios-nvstreamer:3.2.0',
        'sensor': 'nvcr.io/nvidia/vss-core/vss-vios-sensor:3.2.0',
        'stream': 'nvcr.io/nvidia/vss-core/vss-vios-streamprocessing:3.2.0',
        'rtvi': 'example/rtvi:test',
    }
    existing = {image: f'/opt/{key}/lib:$ORIGIN' for key, image in images.items()}
    def inspect(image, docker=None):
        return {'Architecture': 'arm64', 'Os': 'linux', 'Id': 'sha256:' + image,
                'Config': {'Env': ['LD_LIBRARY_PATH=' + existing[image]]}}
    monkeypatch.setattr(runtime, 'inspect_image', inspect)
    monkeypatch.setattr(runtime, 'selected_thor', lambda _: {'host_gpu': '0', 'name': 'NVIDIA Thor'})
    monkeypatch.setattr(runtime, 'command', lambda _: completed('{"nvidia": {}}'))
    actual_probe = runtime.probe_image
    probe_paths = {}
    def docker(args, **kwargs):
        if args[1] == 'run':
            image = args[args.index('--entrypoint') + 2]
            probe_paths[image] = next(arg.partition('=')[2] for arg in args if arg.startswith('LD_LIBRARY_PATH='))
            return completed('{"devices":["NVIDIA Thor"]}')
        return completed()
    monkeypatch.setattr(runtime, 'probe_image', lambda image, mode, devices, **kwargs:
                        actual_probe(image, mode, devices, docker, **kwargs))
    rtvi_overlay = tmp_path / 'rtvi-thor-runtime.json'
    runtime.prepare(tmp_path, '0', [images['nvstreamer']], tmp_path / 'report.json',
                    rtvi_image=images['rtvi'], sensor_image=images['sensor'],
                    stream_image=images['stream'], rtvi_overlay=rtvi_overlay)

    def render(base, overlay=None, env_file=None, env=None):
        args = ['docker', 'compose']
        if env_file:
            args += ['--env-file', str(env_file)]
        args += ['-f', str(base)]
        if overlay:
            args += ['-f', str(overlay)]
        result = subprocess.run(args + ['config', '--format', 'json'], check=True, capture_output=True,
                                text=True, env={'PATH': os.environ['PATH'], **(env or {})})
        # Compose config escapes dollars again so its output can be reloaded.
        return json.loads(result.stdout.replace('$$', '$'))

    for component, selected in [('nvstreamer', {'nvstreamer-1': images['nvstreamer']}),
                                ('stream-processing', {'sensor-ms': images['sensor'],
                                                       'streamprocessing-ms-1': images['stream']})]:
        directory = tmp_path / component
        before = render(directory / 'docker-compose.yaml', env_file=directory / 'compose.env')
        after = render(directory / 'docker-compose.yaml', directory / 'thor-runtime.json', directory / 'compose.env')
        for name, image in selected.items():
            target = after['services'][name]
            expected = runtime.THOR_DRIVER_LIBRARY_DIR + ':' + existing[image]
            assert target['environment']['LD_LIBRARY_PATH'] == expected
            assert target['entrypoint'] == before['services'][name]['entrypoint']
            if image in probe_paths:
                assert target['environment']['LD_LIBRARY_PATH'] == probe_paths[image]

    base = ROOT / f'services/rtvi/{service}/perf/setup/compose/compose.perf.yaml'
    env = {'RTVI_IMAGE': images['rtvi'], 'BACKEND_PORT': '8017', 'USER': 'tester',
           'PERF_PROMETHEUS_IMAGE': 'example/prometheus:test', 'PERF_DCGM_EXPORTER_IMAGE': 'example/dcgm:test',
           'PERF_NODE_EXPORTER_IMAGE': 'example/node:test'}
    before = render(base, env=env)
    after = render(base, rtvi_overlay, env=env)
    assert after['services']['rtvi-server']['environment'].pop('LD_LIBRARY_PATH') == probe_paths[images['rtvi']]
    assert after == before  # No entrypoint, workload, GPU or monitoring changes.

    setup = (ROOT / f'services/rtvi/{service}/perf/scripts/native_setup_perf_env.sh').read_text()
    assert '_thor_compose_args=()\nif [[ "${PLATFORM}" == "jetson" ]]' in setup
    assert '--rtvi-compose-output "${VST_DIR}/rtvi-thor-runtime.json"' in setup
    assert '"${_thor_compose_args[@]}" --env-file "${ENV_PERF_FILE}" up -d' in setup
