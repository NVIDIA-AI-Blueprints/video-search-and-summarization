# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""NIM resolution, architecture gating, reuse, and cleanup without a GPU."""

import json
import subprocess
import sys
import urllib.error
from pathlib import Path
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import local_nim as nim
import model_config
import run_leg

DIGEST = "sha256:" + "a" * 64


@pytest.mark.parametrize("case", ["running", "absent", "foreign", "stopped", "unknown", "exhausted"])
def test_launch_timeout_reconciles_container_before_retry(monkeypatch, case):
    owner, image, name = "a" * 24, "nvcr.io/nim/nvidia/test@" + DIGEST, "job-nim"
    launches = []

    def docker(*args, **kwargs):
        if args[0] == "run":
            assert kwargs["timeout"] == 600
            launches.append(args)
            if case == "absent" and len(launches) == 2:
                return subprocess.CompletedProcess(args, 0, "id", "")
            raise subprocess.TimeoutExpired(args, 120)
        assert args == ("inspect", name)
        if case in ("absent", "exhausted"):
            return subprocess.CompletedProcess(args, 1, "", "Error: No such object: job-nim")
        if case == "unknown":
            return subprocess.CompletedProcess(args, 1, "", "Cannot connect to Docker daemon")
        info = {"Config": {"Image": image, "Labels": {nim.LABEL: "foreign" if case == "foreign" else owner}},
                "State": {"Running": case == "running"}}
        return subprocess.CompletedProcess(args, 0, json.dumps([info]), "")

    monkeypatch.setattr(nim, "docker", docker)
    pause = Mock()
    monkeypatch.setattr(nim.time, "sleep", pause)
    if case in ("running", "absent"):
        nim.run_nim_container(["run", "--name", name, image], owner, image, name)
    else:
        with pytest.raises(nim.NimError):
            nim.run_nim_container(["run", "--name", name, image], owner, image, name)
    expected = 2 if case == "absent" else 3 if case == "exhausted" else 1
    assert len(launches) == expected
    assert pause.call_count == expected - 1


def registry(monkeypatch, *, arch="arm64", tags=None, fail=None):
    calls = []

    def request(url, headers=None, payload=None):
        calls.append(url)
        if fail:
            raise urllib.error.HTTPError(url, fail, "registry error", {}, None)
        if "nvcr.io/proxy_auth" in url:
            return {"token": "test-token"}, {}
        if url.endswith("tags/list"):
            return {"tags": tags or ["1.9.0", "1.10.0", "1.11.0-rc1"]}, {}
        return {
            "manifests": [
                {"digest": DIGEST, "platform": {"os": "linux", "architecture": arch}}
            ]
        }, {}

    monkeypatch.setattr(nim, "request_json", request)
    return calls


def test_resolves_latest_release_and_pins_digest(monkeypatch):
    calls = registry(monkeypatch)
    image = nim.resolve_image("nvidia/test", "arm64", "secret")
    assert image["image"] == f"nvcr.io/nim/nvidia/test@{DIGEST}"
    assert image["tag"] == "1.10.0"
    assert calls[-1].endswith("/manifests/1.10.0")


def test_spark_packaging_keeps_model_identity(monkeypatch):
    calls = registry(monkeypatch)
    result = nim.resolve_image("qwen/qwen3-32b", "arm64", "secret")
    assert result["model"] == "qwen/qwen3-32b"
    assert calls[0].startswith("https://nvcr.io/proxy_auth?")
    assert "/nim/qwen/qwen3-32b-dgx-spark/" in calls[-1]


def test_llama_nim_enables_documented_tool_parser():
    expected = "--enable-auto-tool-choice --tool-call-parser llama3_json"
    assert nim.tool_parser_args("meta/llama-3.1-8b-instruct") == expected
    assert nim.tool_parser_args("meta/llama-3.1-8b-instruct-dgx-spark") == expected
    assert nim.tool_parser_args("meta/llama-3.3-70b-instruct") == expected
    assert nim.tool_parser_args("qwen/qwen3-32b") is None


def test_nemotron_nims_enable_documented_reasoning_and_tool_parsers():
    assert nim.tool_parser_args("nvidia/nemotron-3.5-lightning-30b-a3b") == (
        "--reasoning-parser nemotron_v3 "
        "--enable-auto-tool-choice --tool-call-parser qwen3_coder"
    )
    assert nim.tool_parser_args("nvidia/nemotron-3-ultra-550b-a55b") == (
        "--reasoning-parser-plugin ultra_v3_reasoning_parser.py "
        "--reasoning-parser ultra_v3 "
        "--enable-auto-tool-choice --tool-call-parser qwen3_coder"
    )


def test_architecture_mismatch_rejected_without_deployment(monkeypatch):
    registry(monkeypatch, arch="amd64")
    with pytest.raises(nim.NimError, match="supports linux/arm64"):
        nim.resolve_image("nvidia/test", "arm64", "secret")


def test_exited_nim_reports_oom_without_waiting_for_timeout(monkeypatch):
    def unavailable(*args, **kwargs):
        raise urllib.error.URLError("connection refused")

    def docker(*args, **kwargs):
        if args[0] == "inspect":
            return subprocess.CompletedProcess(args, 0, "false true 1\n", "")
        return subprocess.CompletedProcess(args, 0, "", "CUDA out of memory")

    monkeypatch.setattr(nim, "request_json", unavailable)
    monkeypatch.setattr(nim, "docker", docker)
    with pytest.raises(nim.NimError, match="CUDA out of memory"):
        nim.wait_ready("http://127.0.0.1:18410/v1/health/ready", "", 1800, "nim")


@pytest.mark.parametrize(
    "code,message",
    [
        (404, "No model-specific NIM"),
        (401, "access denied"),
        (403, "access denied"),
        (503, "registry failed"),
    ],
)
def test_registry_errors_are_distinct(monkeypatch, code, message):
    registry(monkeypatch, fail=code)
    with pytest.raises(nim.NimError, match=message):
        nim.resolve_image("nvidia/test", "amd64", "secret")


@pytest.mark.parametrize(
    "model,role",
    [
        ("azure/openai/gpt-6-astra", "coding"),
        ("nvidia/nvidia/nemotron-3.5-lightning", "operational"),
        ("../model", "coding"),
        ("nvidia/model;id", "operational"),
        ("nvidia/model:latest", "coding"),
    ],
)
def test_unsupported_model_id_fails_before_worker(model, role):
    prefix = f"SKILLS_EVAL_{role.upper()}"
    with pytest.raises(
        ValueError,
        match=rf"{prefix}_MODEL: Invalid local NIM image ID .*expected publisher/model",
    ):
        model_config.resolve_model_config(
            {
                f"{prefix}_MODEL": model,
                f"{prefix}_DEPLOYMENT": "local-nim",
                "NGC_API_KEY": "secret",
            },
            role=role,
        )


def test_nim_image_id_is_accepted_for_operational_model():
    route = model_config.resolve_model_config(
        {
            "SKILLS_EVAL_OPERATIONAL_MODEL": "nvidia/nemotron-3.5-lightning-30b-a3b",
            "SKILLS_EVAL_OPERATIONAL_DEPLOYMENT": "local-nim",
            "NGC_API_KEY": "secret",
        },
        role="operational",
    )
    assert route.model == "nvidia/nemotron-3.5-lightning-30b-a3b"


def test_independent_deployment_and_no_hosted_key_leak():
    routes = model_config.resolve_model_routes(
        {
            "ANTHROPIC_MODEL": "hosted/model",
            "ANTHROPIC_API_KEY": "hosted-secret",
            "NGC_API_KEY": "ngc-secret",
            "SKILLS_EVAL_CODING_MODEL": "nvidia/test",
            "SKILLS_EVAL_CODING_DEPLOYMENT": "local-nim",
        }
    )
    assert routes.coding.provider == "local-nim"
    assert routes.coding.api_key == "local-nim"
    assert routes.operational.provider == "hosted-nvidia-inference"
    assert routes.operational.api_key == "hosted-secret"


def plan():
    return {
        "owner": "a" * 24,
        "routes": [
            {"role": "coding", "runtime": "claude-code", "model": "qwen/qwen3-32b"},
            {"role": "operational", "runtime": "codex", "model": "qwen/qwen3-32b"},
        ],
    }


def test_two_roles_deploy_one_nim_and_one_adapter(monkeypatch, tmp_path):
    registry(monkeypatch)
    monkeypatch.setenv("NGC_API_KEY", "ngc-secret")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(nim.platform, "machine", lambda: "aarch64")
    monkeypatch.setattr(nim, "publish", lambda root: None)
    monkeypatch.setattr(nim, "wait_ready", lambda *a, **kw: {})
    real_request = nim.request_json

    def request(url, *args):
        if url.endswith("/models"):
            return {"data": [{"id": "Qwen/Qwen3-32B"}]}, {}
        if "127.0.0.1" in url:
            return {}, {}
        return real_request(url, *args)

    monkeypatch.setattr(nim, "request_json", request)
    commands = []

    def docker(*args, **kwargs):
        commands.append(args)
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(nim, "docker", docker)
    nim.start(plan())
    launches = [c for c in commands if c[0] == "run"]
    assert len(launches) == 2
    model_launch = next(c for c in launches if any("nvcr.io/nim/" in a for a in c))
    assert "TOOL_CALL_PARSER=1" in model_launch
    assert "NIM_MAX_MODEL_LEN=32768" in model_launch
    assert sum(any("nvcr.io/nim/" in a for a in c) for c in launches) == 1
    config = json.loads((nim.owner_paths(plan()["owner"]) / "proxy.json").read_text())
    assert len(config["model_list"]) == 1
    assert config["model_list"][0]["litellm_params"]["model"].endswith("Qwen/Qwen3-32B")
    assert not config.get("general_settings", {}).get("master_key")
    # Next task sees the same owned containers and does not pull or run again.
    monkeypatch.setattr(
        nim,
        "docker",
        lambda *a, **kw: subprocess.CompletedProcess(a, 0, "c1\nc2\n", ""),
    )
    monkeypatch.setattr(
        nim, "resolve_image", Mock(side_effect=AssertionError("must reuse"))
    )
    nim.start(plan())


def test_reuse_rebuilds_a_proxy_that_still_requires_auth(monkeypatch, tmp_path):
    registry(monkeypatch)
    monkeypatch.setenv("NGC_API_KEY", "ngc-secret")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(nim.platform, "machine", lambda: "aarch64")
    monkeypatch.setattr(nim, "publish", lambda root: None)
    monkeypatch.setattr(nim, "wait_ready", lambda *args, **kwargs: {})
    original_request = nim.request_json

    def request(url, *args):
        if url.endswith("/models"):
            return {"data": [{"id": "Qwen/Qwen3-32B"}]}, {}
        if url.startswith("http://127.0.0.1:"):
            return {}, {}
        return original_request(url, *args)

    monkeypatch.setattr(nim, "request_json", request)
    calls = []

    def docker(*args, **kwargs):
        calls.append(args)
        output = "owned-nim\nowned-proxy\n" if args[0] == "ps" else ""
        return subprocess.CompletedProcess(args, 0, output, "")

    monkeypatch.setattr(nim, "docker", docker)
    nim.start(plan())
    config_file = nim.owner_paths(plan()["owner"]) / "proxy.json"
    old_config = json.loads(config_file.read_text())
    old_config["general_settings"] = {"master_key": "old-eval-secret"}
    config_file.write_text(json.dumps(old_config))
    calls.clear()

    nim.start(plan())

    assert ("rm", "-f", "owned-proxy") in calls
    assert len([call for call in calls if call[0] == "run"]) == 2
    assert not json.loads(config_file.read_text()).get("general_settings", {}).get("master_key")


def test_nemoclaw_uses_unauthenticated_proxy_and_loopback_nim(monkeypatch, tmp_path):
    registry(monkeypatch)
    monkeypatch.setenv("NGC_API_KEY", "ngc-secret")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(nim.platform, "machine", lambda: "aarch64")
    monkeypatch.setattr(nim, "worker_host", lambda: "10.229.20.2")
    monkeypatch.setattr(nim, "publish", lambda root: None)
    ready_urls = []

    def wait_ready(url, *args, **kwargs):
        ready_urls.append(url)
        return {}

    monkeypatch.setattr(nim, "wait_ready", wait_ready)
    original_request = nim.request_json
    probes = []

    def request(url, headers=None, payload=None):
        if url.endswith("/models"):
            return {"data": [{"id": "nvidia/nemotron-3.5-lightning-30b-a3b"}]}, {}
        if url.startswith(("http://127.0.0.1:", "http://10.229.20.2:")):
            probes.append((url, headers))
            return {}, {}
        return original_request(url, headers, payload)

    monkeypatch.setattr(nim, "request_json", request)
    commands = []

    def docker(*args, **kwargs):
        commands.append(args)
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(nim, "docker", docker)
    local_plan = {
        "owner": "b" * 24,
        "routes": [{
            "role": "operational",
            "runtime": "nemoclaw",
            "model": "nvidia/nemotron-3.5-lightning-30b-a3b",
        }],
    }
    nim.start(local_plan)

    launch = next(c for c in commands if c[0] == "run" and "--gpus" in c)
    assert "127.0.0.1:18410:8000" in launch
    assert launch[-2:] == (
        "--served-model-name", "nvidia/nemotron-3.5-lightning-30b-a3b"
    )
    assert "http://127.0.0.1:18410/v1/health/ready" in ready_urls
    assert (
        "http://10.229.20.2:18400/v1/chat/completions",
        {
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        },
    ) in probes
    ready = json.loads((nim.owner_paths(local_plan["owner"]) / "ready.json").read_text())
    assert ready["nemoclaw_endpoint"] == "http://10.229.20.2:18400/v1"
    proxy = json.loads((nim.owner_paths(local_plan["owner"]) / "proxy.json").read_text())
    assert "master_key" not in proxy.get("general_settings", {})

    def onboard_inputs():
        # Read the inputs as the notebook will: source the worker env in a
        # fresh shell. A reachable private URL alone still fails SSRF preflight.
        return subprocess.run(
            [
                "bash", "-c",
                'source "$1"; printf "%s\\n%s\\n" '
                '"$NEMOCLAW_ENDPOINT_URL" "$NEMOCLAW_TRUSTED_PRIVATE_INFERENCE_HOSTS"',
                "nim-test", str(tmp_path / ".eval_env"),
            ],
            capture_output=True, text=True, check=True,
        ).stdout.splitlines()

    assert onboard_inputs() == ["http://10.229.20.2:18400/v1", "10.229.20.2"]
    # Later tasks rewrite ~/.eval_env during bootstrap before reusing NIM.
    # Reuse must restore the exact host declaration as well as the URL.
    (tmp_path / ".eval_env").write_text("")
    monkeypatch.setattr(
        nim, "docker", lambda *a, **kw: subprocess.CompletedProcess(a, 0, "c1\nc2\n", "")
    )
    monkeypatch.setattr(
        nim, "resolve_image", Mock(side_effect=AssertionError("must reuse"))
    )
    nim.start(local_plan)
    assert onboard_inputs() == ["http://10.229.20.2:18400/v1", "10.229.20.2"]


def test_reuse_rechecks_nim_inference_before_the_agent_runs(monkeypatch, tmp_path):
    local_plan = plan()
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    root = nim.owner_paths(local_plan["owner"])
    (root / "ready.json").write_text(json.dumps({
        "roles": local_plan["routes"],
        "models": [{"model": "qwen/qwen3-32b", "served_model": "Qwen/Qwen3-32B"}],
    }))
    (root / "proxy.json").write_text(json.dumps({
    }))
    monkeypatch.setattr(nim, "docker", lambda *a, **kw: subprocess.CompletedProcess(
        a, 0, "c1\nc2\n", "",
    ))
    def unhealthy(url, *args, **kwargs):
        if ":18410/" in url:
            raise nim.NimError("Local NIM readiness timed out")
    monkeypatch.setattr(nim, "wait_ready", unhealthy)
    with pytest.raises(nim.NimError, match="readiness timed out"):
        nim.start(local_plan)


def test_alias_provider_deduplicates():
    assert nim.unique_models(
        [{"model": "nvidia_nim/nvidia/test"}, {"model": "nvidia/test"}]
    ) == ["nvidia/test"]


def test_cleanup_is_owner_scoped_and_retains_cache(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    commands = []

    def docker(*args, **kwargs):
        commands.append(args)
        return subprocess.CompletedProcess(
            args, 0, "owned\n" if args[0] == "ps" else "", ""
        )

    monkeypatch.setattr(nim, "docker", docker)
    root = nim.owner_paths("a" * 24)
    (root / "proxy.json").write_text("secret")
    nim.cleanup("a" * 24)
    assert commands == [
        ("ps", "-aq", "--filter", f"label={nim.LABEL}={'a' * 24}"),
        ("rm", "-f", "owned"),
    ]
    assert not (root / "proxy.json").exists()


def test_cancellation_runs_outer_cleanup(monkeypatch, tmp_path):
    config = model_config.SkillEvalModelConfig(
        "coding", "codex", "local-nim", "nvidia/test", "", ""
    )
    routes = model_config.SkillEvalModelRoutes(config, config)
    cleanup = Mock()
    monkeypatch.setattr(run_leg, "cleanup_local_nims", cleanup)
    monkeypatch.setattr(
        run_leg, "_run_invocations", Mock(side_effect=KeyboardInterrupt)
    )
    with pytest.raises(KeyboardInterrupt):
        run_leg.run_invocations(
            [], "Spark-ba-WiFi", tmp_path, tmp_path, "test", "SPARK", 100, routes
        )
    cleanup.assert_called_once()


def test_spark_resolves_registered_node_id_even_if_renamed(monkeypatch):
    monkeypatch.setattr(
        run_leg,
        "_list_registered_nodes",
        lambda: [
            {
                "external_node_id": nim.SPARK_NODE_ID,
                "name": "Spark-renamed",
                "status": "Connected",
            }
        ],
    )
    assert run_leg.spark_instance() == "Spark-renamed"


@pytest.mark.parametrize(
    "nodes",
    [
        [],
        [{"name": nim.SPARK_NODE_NAME, "status": "Disconnected"}],
        [
            {
                "name": nim.SPARK_NODE_NAME,
                "external_node_id": "different-id",
                "status": "Connected",
            }
        ],
    ],
)
def test_spark_never_falls_back_to_other_workers(monkeypatch, nodes):
    monkeypatch.setattr(run_leg, "_list_registered_nodes", lambda: nodes)
    with pytest.raises(ValueError):
        run_leg.spark_instance()
