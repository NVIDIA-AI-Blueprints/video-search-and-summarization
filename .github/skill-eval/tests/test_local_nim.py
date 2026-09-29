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
    "model",
    ["azure/openai/gpt-6-astra", "../model", "nvidia/model;id", "nvidia/model:latest"],
)
def test_unsupported_model_id_fails_before_worker(model):
    with pytest.raises(ValueError, match="No model-specific NIM"):
        model_config.resolve_model_config(
            {
                "SKILLS_EVAL_CODING_MODEL": model,
                "SKILLS_EVAL_CODING_DEPLOYMENT": "local-nim",
                "NGC_API_KEY": "secret",
            },
            role="coding",
        )


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
    assert routes.coding.api_key != "hosted-secret"
    assert routes.operational.provider == "nvidia-inference"
    assert routes.operational.api_key == "hosted-secret"


def plan():
    return {
        "owner": "a" * 24,
        "token": "sk-test-local",
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
