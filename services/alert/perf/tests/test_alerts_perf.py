# SPDX-License-Identifier: Apache-2.0
from pathlib import Path
import sys
import subprocess

import pytest
import yaml

PERF = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PERF / "benchmark/core"))
from alert_results_adapter import to_test_cases  # noqa: E402
import vss_perf_benchmark as runner  # noqa: E402


def test_scenarios_use_only_agreed_modes_and_dataset():
    config = PERF / "configs/config.yaml"
    for name, mode in (
        ("quick_test", "synthetic_kafka"),
        ("full_alerts_latency", "e2e_kafka"),
    ):
        _, selected, dataset, workload = runner.scenario_settings(config, name)
        assert selected["benchmark_mode"] == mode
        assert dataset["tenant_id"] == "0573334707593577"
        assert workload["alert_category"] == "FOV Count Violation"
        args = runner.native_arguments(
            Path("/tmp/private"),
            selected,
            workload,
            Path("/datasets/test"),
            dataset,
            "H100",
            Path("/tmp/eval.env"),
        )
        assert ("--synthetic-alerts-injection" in args) == (name == "quick_test")
        assert ("--e2e-eval" in args) == (name == "full_alerts_latency")
        assert "--eval-type" in args and "accuracy" not in args
        assert args[args.index("--host-ip") + 1] == "127.0.0.1"


@pytest.mark.parametrize("port", [0, 65536, True, 8001.0, "8001"])
def test_agent_host_port_rejects_invalid_values(tmp_path, port):
    config = yaml.safe_load((PERF / "configs/config.yaml").read_text())
    config["global"]["deployment"]["agent_host_port"] = port
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(config))
    with pytest.raises(ValueError, match="deployment.agent_host_port"):
        runner.scenario_settings(path, "quick_test")


def test_agent_port_preflight_detects_docker_nat_publication(monkeypatch):
    monkeypatch.setattr(
        runner.subprocess, "check_output",
        lambda *_, **__: "another-service\t0.0.0.0:8001->8000/tcp\n",
    )
    with pytest.raises(ValueError, match="8001 is already published by another-service"):
        runner.ensure_agent_port_available(8001, {})


def test_agent_port_preflight_detects_host_listener(monkeypatch):
    import socket

    monkeypatch.setattr(runner.subprocess, "check_output", lambda *_, **__: "")
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("0.0.0.0", 0))
        listener.listen()
        port = listener.getsockname()[1]
        with pytest.raises(ValueError, match=f"Cannot bind Alerts agent host port {port}"):
            runner.ensure_agent_port_available(port, {})


def test_native_latency_values_and_reliability_are_preserved():
    summary = {
        "summary_status": "success",
        "e2e_count": 25,
        "e2e_avg_sec": 1.0301,
        "e2e_p90_sec": 2.1827,
        "verification_failed_events": 25,
        "top_verification_failure_reason": "vlm_invalid_payload",
        "e2e_p99_censored": True,
    }
    case = to_test_cases({"status": "success", "summary": summary})[0]
    assert case["status"] == "FAIL"
    assert case["metrics"]["latency"]["e2e_p90_seconds"] == summary["e2e_p90_sec"]
    assert case["native_summary"] == summary
    assert "verification_failed_events=25" in case["error"]["message"]


def test_success_and_missing_data_have_distinct_results():
    passing = {
        "status": "success",
        "summary": {"summary_status": "success", "e2e_count": 10},
    }
    assert to_test_cases(passing)[0]["status"] == "PASS"
    passing["summary"]["e2e_count"] = 0
    case = to_test_cases(passing)[0]
    assert case["status"] == "FAIL"
    assert "e2e_seconds" not in case["metrics"]["latency"]
    with pytest.raises(ValueError, match="aggregate summary"):
        to_test_cases({"status": "success"})


@pytest.mark.parametrize("agent_port", [8001, 18001])
def test_runtime_keeps_compose_interpolation_and_original_tree_unchanged(
    tmp_path, monkeypatch, agent_port
):
    source = tmp_path / "source"
    docker = source / "deploy/docker"
    (docker / "services/alert").mkdir(parents=True)
    (docker / "developer-profiles/dev-profile-alerts").mkdir(parents=True)
    profile = docker / "developer-profiles/dev-profile-alerts/.env"
    profile.write_text('NGC_API_KEY="private-value"\nMODEL_NAME_2D="GDINO"\n')
    overrides = profile.with_name("overrides.env")
    overrides.write_text("VSS_AGENT_HOST_PORT=8000\nVSS_AGENT_PORT=8000\n")
    nvstreamer = docker / "services/nvstreamer/.env"
    nvstreamer.parent.mkdir(parents=True)
    nvstreamer_text = 'NVSTREAMER_HTTP_PORT=31000\nNVSTREAMER_IMAGE_TAG="fixture-tag"\nexport NVIDIA_API_KEY="private-nv-value"\n'
    nvstreamer.write_text(nvstreamer_text)
    unrelated = docker / "services/unrelated"
    unrelated.mkdir()
    (unrelated / ".env").write_text("SECRET=local-only\n")
    (unrelated / "generated.env").write_text("SECRET=stale-generated\n")
    compose = docker / "services/alert/compose.yml"
    original = "services:\n  alert:\n    image: ${VSS_ALERT_IMAGE:-registry/vss-alert-ms}:${VSS_ALERT_TAG:-latest}\n"
    compose.write_text(original)
    monkeypatch.setattr(runner, "REPO_ROOT", source)
    deployment = runner.prepare_runtime(tmp_path / "runtime", agent_port)
    staged_overrides = deployment / "developer-profiles/dev-profile-alerts/overrides.env"
    assert staged_overrides.read_text() == (
        f"VSS_AGENT_HOST_PORT={agent_port}\nVSS_AGENT_PORT=8000\n"
    )
    assert overrides.read_text() == "VSS_AGENT_HOST_PORT=8000\nVSS_AGENT_PORT=8000\n"
    assert (deployment / "services/alert/compose.yml").read_text() == original
    assert compose.read_text() == original
    assert (
        "private-value"
        not in (deployment / "developer-profiles/dev-profile-alerts/.env").read_text()
    )
    staged_nvstreamer = deployment / "services/nvstreamer/.env"
    assert "NVSTREAMER_HTTP_PORT=31000" in staged_nvstreamer.read_text()
    assert 'NVSTREAMER_IMAGE_TAG="fixture-tag"' in staged_nvstreamer.read_text()
    assert "private-nv-value" not in staged_nvstreamer.read_text()
    assert 'NVIDIA_API_KEY="${NVIDIA_API_KEY:-}"' in staged_nvstreamer.read_text()
    assert nvstreamer.read_text() == nvstreamer_text
    assert "private-value" in profile.read_text()
    for relative in runner.REQUIRED_DEPLOYMENT_ENV_TEMPLATES:
        assert (deployment / relative).stat().st_mode & 0o777 == 0o600
    assert not (deployment / "services/unrelated/.env").exists()
    assert not (deployment / "services/unrelated/generated.env").exists()
    # Exercise the real native shell's path discovery and Python package imports
    # inside the relocated layout without deploying or contacting services.
    subprocess.run(
        ["bash", str(tmp_path / "runtime/eval/run_eval.sh"), "--help"],
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        [sys.executable, "-m", "eval.scripts.deploy.apply_profile_config", "--help"],
        cwd=tmp_path / "runtime",
        check=True,
        capture_output=True,
        text=True,
    )


def test_dataset_paths_cannot_escape_dataset_root(tmp_path):
    config = yaml.safe_load((PERF / "configs/config.yaml").read_text())
    config["global"]["dataset"]["videos_subpath"] = "../../other"
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(config))
    with pytest.raises(ValueError, match="dataset-relative"):
        runner.scenario_settings(path, "quick_test")


@pytest.mark.parametrize(
    "native_exit,verification_failures,expected_exit", [(0, 0, 0), (0, 5, 0), (1, 0, 1)]
)
@pytest.mark.parametrize("inherited_path", [None, "/path/to/stale-data", "/tmp/other-deployment"])
@pytest.mark.parametrize("scenario", ["quick_test", "full_alerts_latency"])
@pytest.mark.parametrize("gpu_model", [None, "RTXPRO6000BW-SE"])
def test_pipeline_outcome_and_canonical_report(
    tmp_path, monkeypatch, native_exit, verification_failures, expected_exit,
    inherited_path, scenario, gpu_model,
):
    import json
    import os
    from types import SimpleNamespace

    output = tmp_path / "results"
    dataset = tmp_path / "dataset"
    (dataset / "warehouse_chunks").mkdir(parents=True)
    (dataset / "warehouse_chunks/warehouse_1_540_7.mp4").touch()
    monkeypatch.setenv("IMAGE_TAG", "registry/vss-alert-ms:poc")
    monkeypatch.setenv("NGC_API_KEY", "test-value")
    monkeypatch.setenv("VSS_AGENT_HOST_PORT", "8000")
    monkeypatch.setenv("HOST_IP", "<HOST_IP>")
    checked_ports = []
    monkeypatch.setattr(
        runner, "ensure_agent_port_available",
        lambda port, env: checked_ports.append(port),
    )
    for key in ("VSS_APPS_DIR", "VSS_DATA_DIR"):
        if inherited_path is None:
            monkeypatch.delenv(key, raising=False)
        else:
            monkeypatch.setenv(key, inherited_path)
    monkeypatch.setattr(runner.shutil, "which", lambda _: "/usr/bin/tool")
    monkeypatch.setattr(runner, "hardware_profile", lambda: "H100")
    monkeypatch.setattr(runner, "prepare_dataset", lambda *_: dataset)

    def fake_runtime(work, agent_port):
        assert agent_port == 8001
        (work / "eval").mkdir()

    monkeypatch.setattr(runner, "prepare_runtime", fake_runtime)
    monkeypatch.setattr(runner, "run", lambda *_, **__: None)
    monkeypatch.setattr(
        runner.subprocess, "check_output",
        lambda command, **_: "registry/vss-alert-ms:poc" if "inspect" in command else "",
    )
    monkeypatch.setattr(
        runner.subprocess, "run", lambda *_, **__: SimpleNamespace(returncode=0)
    )
    monkeypatch.setattr(runner, "collect_diagnostics", lambda *_: None)
    monkeypatch.setattr(
        runner, "discover_platform", lambda **_: {"gpu": {
            "model": "H100", "full_name": "NVIDIA H100", "count": 1,
            "memory_gb": 80.0, "driver_version": "595.58.03", "cuda_version": "13.2",
        }}
    )
    native_summary = {
        "summary_status": "success",
        "e2e_count": 5,
        "e2e_avg_sec": 0.377736,
        "e2e_p90_sec": 0.9109926445779066,
        "verification_failed_events": verification_failures,
    }

    fetched_urls = []

    def fake_urlopen(url, **kwargs):
        fetched_urls.append(url)
        raise AssertionError("Local evaluation must not poll the removed evaluator API")

    monkeypatch.delenv("ALERT_BRIDGE_EVALUATOR_PORT", raising=False)
    monkeypatch.setattr(runner.urllib.request, "urlopen", fake_urlopen)

    class NativeProcess:
        def __init__(self, argv, **kwargs):
            assert kwargs["env"]["PERF_ALERT_IMAGE"] == "registry/vss-alert-ms:poc"
            assert kwargs["env"]["PERF_KAFKA_IMAGE"] == "confluentinc/cp-kafka:8.3.0"
            assert "VSS_APPS_DIR" not in kwargs["env"]
            assert "VSS_DATA_DIR" not in kwargs["env"]
            assert "VSS_AGENT_HOST_PORT" not in kwargs["env"]
            assert argv[argv.index("--host-ip") + 1] == "127.0.0.1"
            self.returncode = native_exit

        def wait(self, **kwargs):
            if not native_exit:
                report = output / "native/ab-eval/alert_bridge_evaluation_status.json"
                report.parent.mkdir()
                report.write_text(
                    json.dumps(
                        {
                            "status": "success",
                            "summary": native_summary,
                        }
                    )
                )
            return native_exit

        def poll(self):
            return native_exit

    monkeypatch.setattr(runner.subprocess, "Popen", NativeProcess)
    arguments = ["--output-json", str(output), "--scenario", scenario,
                 "--release", "v3.4.0", "--pipeline-url", "https://ci.example/run/135",
                 "--triggered-by", "manual"]
    if gpu_model:
        arguments += ["--gpu-model", gpu_model]
    assert runner.main(arguments) == expected_exit
    assert checked_ports == [8001]
    assert fetched_urls == []
    assert os.environ["HOST_IP"] == "<HOST_IP>"
    assert os.environ["VSS_AGENT_HOST_PORT"] == "8000"
    for key in ("VSS_APPS_DIR", "VSS_DATA_DIR"):
        assert os.environ.get(key) == inherited_path
    reports = list((output / "vss-perf-results").glob("*.json"))
    assert len(reports) == 1
    report = json.loads(reports[0].read_text())
    assert list(report) == ["schema_version", "metadata", "summary", "test_cases"]
    assert report["schema_version"] == "0.1"
    assert reports[0].name.startswith("alerts-verification_alerts-verification-compose_")
    assert report["metadata"]["service"] == "Alerts-Verification"
    gpu = report["metadata"]["platform"]["gpu"]
    assert gpu["model"] == (gpu_model or "H100")
    assert gpu["full_name"] == "NVIDIA H100"
    assert gpu["count"] == 1
    assert gpu["memory_gb"] == 80.0
    assert gpu["driver_version"] == "595.58.03"
    assert gpu["cuda_version"] == "13.2"
    run_info = report["metadata"]["run_info"]
    assert run_info["release"] == "v3.4.0"
    assert run_info["pipeline_url"] == "https://ci.example/run/135"
    assert run_info["triggered_by"] == "manual"
    assert report["metadata"]["config"]["artifact_ref"] == "registry/vss-alert-ms:poc"
    assert report["metadata"]["config"]["setup"] == (
        "synthetic_kafka" if scenario == "quick_test" else "e2e_kafka"
    )
    assert report["metadata"]["config"]["agent_host_port"] == 8001
    if native_exit:
        assert report["test_cases"][-1]["error"]["stage"] == "benchmark"
    else:
        case = report["test_cases"][0]
        assert case["native_summary"] == native_summary
        assert case["metrics"]["latency"]["e2e_seconds"] == native_summary["e2e_avg_sec"]
        assert case["metrics"]["latency"]["e2e_p90_seconds"] == native_summary["e2e_p90_sec"]
    assert report["summary"]["overall_status"] == (
        "FAIL" if native_exit or verification_failures else "PASS"
    )


def test_docker_shim_preserves_image_variables_and_exact_arguments(tmp_path):
    import json
    import os
    import shlex

    sudo = tmp_path / "sudo"
    sudo.write_text("#!/bin/sh\nexec " + shlex.quote(sys.executable) +
                    " -c 'import json,sys; print(json.dumps(sys.argv[1:]))' \"$@\"\n")
    sudo.chmod(0o700)
    shim = tmp_path / "docker"
    runner.write_docker_shim(shim, "/path with spaces/docker",
                            ["PERF_KAFKA_IMAGE", "PERF_ALERT_IMAGE"])
    result = subprocess.run([str(shim), "compose", "--env-file", "a file.env", "config"],
                            env={**os.environ, "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"]},
                            capture_output=True, text=True, check=True)
    assert json.loads(result.stdout) == ["-n", "--preserve-env=PERF_ALERT_IMAGE,PERF_KAFKA_IMAGE",
                                        "/path with spaces/docker", "compose", "--env-file", "a file.env", "config"]


@pytest.mark.parametrize("missing", runner.REQUIRED_DEPLOYMENT_ENV_TEMPLATES)
def test_required_environment_templates_fail_early_with_clear_path(tmp_path, monkeypatch, missing):
    source = tmp_path / "source"
    for relative in runner.REQUIRED_DEPLOYMENT_ENV_TEMPLATES:
        if relative != missing:
            file = source / "deploy/docker" / relative
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_text("SETTING=preserve\n")
    monkeypatch.setattr(runner, "REPO_ROOT", source)
    with pytest.raises(ValueError) as error:
        runner.prepare_runtime(tmp_path / "runtime")
    assert missing in str(error.value)
    assert "Required deployment environment template" in str(error.value)
    assert not (tmp_path / "runtime").exists()


def test_staged_blueprint_compose_keeps_required_environment_inputs(tmp_path, monkeypatch):
    import io
    import json
    import os
    import shutil
    import tarfile

    if not shutil.which("docker") or not shutil.which("git"):
        pytest.skip("Docker Compose CLI and git required for offline staging validation")
    if subprocess.run(["docker", "compose", "version"], capture_output=True).returncode:
        pytest.skip("Docker Compose CLI unavailable")
    root = runner.REPO_ROOT
    # Use only version-controlled deployment inputs, excluding local datasets,
    # modified credentials, and other untracked workspace state.
    archive = subprocess.run(["git", "archive", "HEAD", "deploy/docker"],
                             cwd=root, check=True, capture_output=True)
    source = tmp_path / "source"
    source.mkdir()
    with tarfile.open(fileobj=io.BytesIO(archive.stdout)) as stream:
        stream.extractall(source, filter="data")
    monkeypatch.setattr(runner, "REPO_ROOT", source)
    deployment = runner.prepare_runtime(tmp_path / "runtime")
    profile = deployment / "developer-profiles/dev-profile-alerts"
    # Supply generated configuration as a fixture: never run deployment scripts,
    # kernel tuning, image pulls, or containers during this test.
    shutil.copyfile(profile / "overrides.env", profile / "generated.env")
    data = deployment / "data-dir"
    with (profile / "generated.env").open("a") as generated:
        generated.write(
            f'\nVSS_APPS_DIR="{deployment}"\nVSS_DATA_DIR="{data}"\n'
            'HOST_IP="127.0.0.1"\nEXTERNAL_IP="127.0.0.1"\n'
        )
    env = {key: os.environ[key] for key in ("PATH", "HOME") if key in os.environ}
    env.update(runner.image_environment(runner.load_images(PERF / "setup/configs/images.yaml"), {}))
    env["PERF_ALERT_IMAGE"] = "registry.example/alerts:staging-test"
    command = ["docker", "compose", "-f", str(deployment / "compose.yml"),
               "--env-file", str(deployment / "containers.env"),
               "--env-file", str(profile / ".env"),
               "--env-file", str(profile / "generated.env"), "config", "--format", "json"]
    result = subprocess.run(command, cwd=deployment, env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    resolved = json.loads(result.stdout)
    agent = resolved["services"]["vss-agent"]
    assert any(
        str(port["published"]) == "8001" and port["target"] == 8000
        for port in agent["ports"]
    )
    assert str(agent["environment"]["VSS_AGENT_PORT"]) == "8000"
    assert ":8000/health" in str(agent["healthcheck"]["test"])
    for volume, subpath in (
        ("kafka-data", "data_log/kafka"),
        ("elastic-data", "data_log/elastic/data"),
        ("elastic-logs", "data_log/elastic/logs"),
    ):
        assert resolved["volumes"][volume]["driver_opts"]["device"] == str(data / subpath)
    # Reproduce #128 explicitly: this include is required even before containers start.
    (deployment / "services/nvstreamer/.env").unlink()
    missing = subprocess.run(command, cwd=deployment, env=env, capture_output=True, text=True)
    assert missing.returncode != 0
    assert "services/nvstreamer/.env" in missing.stderr


def test_native_host_cli_overrides_profile_placeholder(tmp_path):
    import os
    import shlex

    profile_env = tmp_path / "profile.env"
    profile_env.write_text('HOST_IP="<HOST_IP>"\nBLUEPRINT_IP="<HOST_IP>"\n')
    native = (PERF / "benchmark/native/eval/run_eval.sh").read_text()
    # Execute the existing CLI override block after loading a profile template;
    # avoid all deployment commands and keep the upstream runner untouched.
    block = native.split('    if [[ -n "${host_ip}" ]]; then', 1)[1]
    block = 'if [[ -n "${host_ip}" ]]; then' + block.split(
        '    if [[ "${external_ip_via_cli}"', 1
    )[0]
    command = (
        "set -euo pipefail\nsource " + shlex.quote(str(profile_env))
        + '\nhost_ip=127.0.0.1\nhost_ip_via_cli=true\n' + block
        + '\nprintf "%s\\n" "$HOST_IP" "$BLUEPRINT_IP" "$HOST_IP_VIA_CLI"'
    )
    result = subprocess.run(
        ["bash", "-c", command], env={"PATH": os.environ["PATH"]},
        text=True, capture_output=True, check=True,
    )
    assert result.stdout.splitlines() == ["127.0.0.1", "127.0.0.1", "true"]
