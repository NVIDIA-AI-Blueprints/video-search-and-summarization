# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Model sharing, ownership and onboarding contracts, with no live GPU needed."""

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import local_nim as nim
import model_config
import run_leg
import shared_vss_llm as shared

MODEL = "nvidia/nemotron-3.5-lightning-30b-a3b"
OWNER = "a" * 24
REPO = Path(__file__).resolve().parents[3]


def container(repo, *, model=MODEL, project="vss", service="llm", running=True, name="nim"):
    return {
        "Id": name, "Name": "/" + name, "Image": "sha256:image",
        "State": {"Running": running},
        "Config": {
            "Image": f"nvcr.io/nim/{model}:2.0.9-variant",
            "Env": [f"NIM_MODEL_NAME={model}", f"NIM_SERVED_MODEL_NAME={model}"],
            "Labels": {
                "com.docker.compose.project": project,
                "com.docker.compose.service": service,
                "com.docker.compose.project.config_files": str(repo / "_builds/custom/resolved.yml"),
            },
        },
        "NetworkSettings": {"Ports": {"8000/tcp": [{"HostIp": "0.0.0.0", "HostPort": "30081"}]}},
    }


def discovery(monkeypatch, repo, containers):
    ingress = container(repo, service="vss-haproxy-ingress", model="ignored", name="ingress")
    monkeypatch.setattr(shared.subprocess, "run", lambda *a, **k: Mock(stdout="ids"))

    def inspect(*args):
        if args[:2] == ("image", "inspect"):
            return [{"Architecture": "arm64", "RepoDigests": ["nvcr.io/nim/model@sha256:digest"]}]
        if args[1:] == ("ids",):
            return [ingress, *containers]
        return [next(c for c in containers if c["Id"] == args[1])]

    monkeypatch.setattr(shared, "docker_json", inspect)

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def read(self):
            return json.dumps({"data": [{"id": MODEL}]}).encode()

    monkeypatch.setattr(shared, "urlopen", lambda *a, **k: Response())


def test_discovers_actual_compose_project_and_records_binding(monkeypatch, tmp_path):
    target = container(tmp_path)
    discovery(monkeypatch, tmp_path, [target])
    binding = shared.discover(tmp_path, MODEL)
    assert binding["container"] == "nim"
    assert binding["endpoint"] == "http://127.0.0.1:30081/v1"
    assert binding["source"] == "vss"
    assert binding["architecture"] == "arm64"
    assert binding["served_model"] == MODEL


@pytest.mark.parametrize("other", ["qwen/qwen3-32b", "nvidia/nemotron-3-ultra-550b-a55b"])
def test_different_model_is_not_shared(monkeypatch, tmp_path, other):
    discovery(monkeypatch, tmp_path, [container(tmp_path, model=other)])
    assert shared.discover(tmp_path, MODEL) is None


def test_ignores_matching_model_from_other_deployment(monkeypatch, tmp_path):
    discovery(monkeypatch, tmp_path, [container(tmp_path / "other", project="other")])
    assert shared.discover(tmp_path, MODEL) is None


def test_stopped_matching_service_refuses_duplicate(monkeypatch, tmp_path):
    discovery(monkeypatch, tmp_path, [container(tmp_path, running=False)])
    with pytest.raises(ValueError, match="stopped"):
        shared.discover(tmp_path, MODEL)


def test_ambiguous_matching_services_fail(monkeypatch, tmp_path):
    discovery(monkeypatch, tmp_path, [container(tmp_path), container(tmp_path, name="second")])
    with pytest.raises(ValueError, match="ambiguous"):
        shared.discover(tmp_path, MODEL)


def test_wrong_served_model_is_rejected(monkeypatch, tmp_path):
    target = container(tmp_path)
    target["Config"]["Env"][-1] = "NIM_SERVED_MODEL_NAME=wrong"
    discovery(monkeypatch, tmp_path, [target])
    with pytest.raises(ValueError, match="advertises"):
        shared.discover(tmp_path, MODEL)


def test_bound_container_cannot_be_replaced_silently(monkeypatch, tmp_path):
    target = container(tmp_path)
    discovery(monkeypatch, tmp_path, [target])
    binding = shared.discover(tmp_path, MODEL)
    target["Image"] = "sha256:different"
    with pytest.raises(ValueError, match="identity"):
        shared.verify(binding)


def test_unready_matching_service_refuses_duplicate(monkeypatch, tmp_path):
    discovery(monkeypatch, tmp_path, [container(tmp_path)])
    monkeypatch.setattr(shared, "urlopen", Mock(side_effect=shared.URLError("not ready")))
    monkeypatch.setattr(shared.time, "monotonic", Mock(side_effect=[0, 121]))
    with pytest.raises(ValueError, match="refusing to provision a duplicate"):
        shared.discover(tmp_path, MODEL)


def eval_plan(coding=None):
    routes = [{"role": "operational", "runtime": "nemoclaw", "model": MODEL}]
    if coding:
        routes.insert(0, {"role": "coding", "runtime": "codex", "model": coding})
    return {"owner": OWNER, "routes": routes, "reuse_vss": True}


def worker(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(nim.platform, "machine", lambda: "aarch64")
    monkeypatch.setattr(nim, "worker_host", lambda: "10.229.20.2")
    monkeypatch.setattr(nim, "publish", lambda *_: None)
    monkeypatch.setattr(nim, "wait_ready", lambda *a, **k: {})
    monkeypatch.setattr(nim, "smoke_routes", Mock())
    monkeypatch.setattr(nim, "request_json", lambda *a, **k: ({"data": [{"id": MODEL}]}, {}))
    commands = []

    def docker(*args, **kwargs):
        commands.append(args)
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(nim, "docker", docker)
    binding = {
        "model": MODEL, "served_model": MODEL, "source": "vss", "architecture": "arm64",
        "endpoint": "http://127.0.0.1:30081/v1", "container": "vss-container",
    }
    helper = Mock()
    helper.discover.return_value = binding
    monkeypatch.setattr(nim, "sharing_helper", lambda _: helper)
    return commands, helper, binding


def test_hosted_coding_defers_local_operational_model_until_vss(monkeypatch, tmp_path):
    commands, helper, _ = worker(monkeypatch, tmp_path)
    nim.start(eval_plan())
    assert commands == []
    helper.discover.assert_not_called()
    marker = json.loads((nim.owner_paths(OWNER) / "ready.json").read_text())
    assert marker["operational_pending"]


def test_local_coding_is_started_without_provisioning_nemoclaw(monkeypatch, tmp_path):
    plan = eval_plan("qwen/qwen3-32b")
    monkeypatch.setattr(nim, "owner_paths", lambda _: tmp_path)
    original = nim.start
    starts = []
    monkeypatch.setattr(nim, "start", lambda p: starts.append(p))
    original(plan)
    assert starts[0]["routes"] == plan["routes"][:1]
    assert not starts[0]["reuse_vss"]


def test_shared_nim_starts_only_adapter_without_registry_or_ngc_key(monkeypatch, tmp_path):
    commands, helper, binding = worker(monkeypatch, tmp_path)
    monkeypatch.delenv("NGC_API_KEY", raising=False)
    monkeypatch.delenv("NGC_CLI_API_KEY", raising=False)
    monkeypatch.setattr(nim, "resolve_image", Mock(side_effect=AssertionError("must not resolve")))
    nim.prepare(eval_plan(), tmp_path)
    runs = [command for command in commands if command[0] == "run"]
    assert len(runs) == 1 and "--gpus" not in runs[0]
    assert not any(command[0] in ("pull", "login") for command in commands)
    helper.discover.assert_called_once_with(tmp_path, MODEL)
    marker = json.loads((nim.owner_paths(OWNER) / "ready.json").read_text())
    assert marker["models"] == [binding]
    assert marker["selection"] == "matching-vss-nim"
    assert marker["operational_prepared"]
    config = json.loads((nim.owner_paths(OWNER) / "proxy.json").read_text())
    assert config["model_list"][0]["litellm_params"]["api_base"] == binding["endpoint"]
    assert nim.configure_nemoclaw(marker)["NEMOCLAW_ENDPOINT_URL"] == "http://10.229.20.2:18400/v1"
    # Later Harbor task retains the same upstream and does not reprovision.
    commands.clear()
    monkeypatch.setattr(nim, "docker", lambda *a, **k: subprocess.CompletedProcess(a, 0, "proxy\n", ""))
    nim.start(eval_plan())
    helper.discover.assert_called_once()
    helper.verify.assert_called()


def test_no_match_provisions_eval_nim(monkeypatch, tmp_path):
    commands, helper, _ = worker(monkeypatch, tmp_path)
    helper.discover.return_value = None
    monkeypatch.setenv("NGC_API_KEY", "test-key")
    monkeypatch.setattr(nim, "resolve_image", lambda model, arch, key: {
        "model": model, "architecture": arch, "image": "nvcr.io/nim/model@sha256:digest",
    })
    nim.prepare(eval_plan(), tmp_path)
    assert sum(command[0] == "run" for command in commands) == 2
    assert sum("--gpus" in command for command in commands) == 1
    evidence = json.loads((nim.owner_paths(OWNER) / "ready.json").read_text())
    assert evidence["selection"] == "no-matching-vss-nim"
    assert evidence["models"][0]["source"] == "eval"


def test_bad_match_fails_before_starting_any_container(monkeypatch, tmp_path):
    commands, helper, _ = worker(monkeypatch, tmp_path)
    helper.discover.side_effect = ValueError("matching service not ready")
    with pytest.raises(ValueError, match="not ready"):
        nim.prepare(eval_plan(), tmp_path)
    assert commands == []


def test_cleanup_never_removes_vss_container(monkeypatch, tmp_path):
    commands, _, _ = worker(monkeypatch, tmp_path)
    nim.prepare(eval_plan(), tmp_path)
    commands.clear()

    def docker(*args, **kwargs):
        commands.append(args)
        return subprocess.CompletedProcess(args, 0, "proxy\n" if args[0] == "ps" else "", "")

    monkeypatch.setattr(nim, "docker", docker)
    nim.cleanup(OWNER)
    assert commands == [("ps", "-aq", "--filter", f"label={nim.LABEL}={OWNER}"), ("rm", "-f", "proxy")]


@pytest.mark.parametrize("runtime,provider,expected", [
    ("nemoclaw", "local-nim", True), ("nemoclaw", "hosted-nvidia-inference", False),
    ("codex", "local-nim", False),
])
def test_sharing_applies_only_to_local_operational_nemoclaw(monkeypatch, tmp_path, runtime, provider, expected):
    monkeypatch.setenv("EVAL_SPEC_PATH", "skills/operations/vss-search-archive/evals/search.json")
    hosted = model_config.SkillEvalModelConfig("coding", "codex", "hosted-nvidia-inference", "hosted", "url", "key")
    operational = model_config.SkillEvalModelConfig("operational", runtime, provider, MODEL, "url", "key")
    coding = hosted if provider == "local-nim" else model_config.SkillEvalModelConfig("coding", "codex", "local-nim", MODEL, "url", "key")
    calls = []
    monkeypatch.setattr(run_leg, "_run_invocations", lambda *a, **k: calls.append(k) or 0)
    monkeypatch.setattr(run_leg, "cleanup_local_nims", lambda *a: None)
    run_leg.run_invocations([], "Spark", tmp_path, tmp_path, "search", "DGX-SPARK", 100, model_config.SkillEvalModelRoutes(coding, operational))
    assert calls[0]["nim_plan"]["reuse_vss"] is expected


def test_notebook_prepares_before_environment_snapshot():
    notebook = json.loads((REPO / "deploy/docker/scripts/deploy_nemoclaw.ipynb").read_text())
    cell = next("".join(c["source"]) for c in notebook["cells"] if "_eval_model_updates" in "".join(c["source"]))
    hook = cell[cell.index("_eval_model_updates ="):cell.index("HOME_DIR =")]
    updates = {"NEMOCLAW_PROVIDER": "custom", "NEMOCLAW_ENDPOINT_URL": "http://worker:18400/v1", "NEMOCLAW_MODEL": MODEL, "COMPATIBLE_API_KEY": "local-nim"}
    import os
    import runpy
    from unittest import mock
    namespace = {"os": os, "json": json, "Path": Path}
    with mock.patch.dict(os.environ, {"SKILL_EVAL_LOCAL_NIM_PLAN": json.dumps(eval_plan())}, clear=True), mock.patch.object(runpy, "run_path", return_value={"prepare_for_notebook": lambda repo: updates}):
        exec(hook, namespace)
        assert namespace["NEMOCLAW_ENDPOINT_URL"] == updates["NEMOCLAW_ENDPOINT_URL"]
        assert namespace["SHELL_ENV"]["NEMOCLAW_MODEL"] == MODEL
    with mock.patch.dict(os.environ, {}, clear=True), mock.patch.object(runpy, "run_path") as load:
        exec(hook, {"os": os, "json": json, "Path": Path})
        load.assert_not_called()


def test_eval_hook_routes_for_every_spark_operational_spec(monkeypatch, tmp_path):
    routes = model_config.SkillEvalModelRoutes(
        model_config.SkillEvalModelConfig("coding", "codex", "hosted-nvidia-inference", "hosted", "url", "key"),
        model_config.SkillEvalModelConfig("operational", "nemoclaw", "local-nim", MODEL, "url", "local-nim"),
    )
    calls = []
    monkeypatch.setattr(run_leg, "_run_invocations", lambda *a, **k: calls.append(k) or 0)
    monkeypatch.setattr(run_leg, "cleanup_local_nims", lambda *a: None)
    specs = []
    for path in (REPO / "skills/operations").glob("*/evals/*.json"):
        spec = json.loads(path.read_text())
        if isinstance(spec, dict) and "DGX-SPARK" in spec.get("resources", {}).get("platforms", {}):
            specs.append(path)
    assert len(specs) == 20
    for path in specs:
        monkeypatch.setenv("EVAL_SPEC_PATH", str(path.relative_to(REPO)))
        run_leg.run_invocations([], "Spark", tmp_path, tmp_path, path.stem, "DGX-SPARK", 100, routes)
        assert calls[-1]["nim_plan"]["reuse_vss"], path
    assert len(calls) == len(specs)


def test_sharing_is_not_enabled_for_build_skill_eval(monkeypatch, tmp_path):
    monkeypatch.setenv("EVAL_SPEC_PATH", "skills/vss-build-vision-ai/evals/base.json")
    routes = model_config.SkillEvalModelRoutes(
        model_config.SkillEvalModelConfig("coding", "codex", "hosted-nvidia-inference", "hosted", "url", "key"),
        model_config.SkillEvalModelConfig("operational", "nemoclaw", "local-nim", MODEL, "url", "local-nim"),
    )
    calls = []
    monkeypatch.setattr(run_leg, "_run_invocations", lambda *a, **k: calls.append(k) or 0)
    monkeypatch.setattr(run_leg, "cleanup_local_nims", lambda *a: None)
    run_leg.run_invocations([], "Spark", tmp_path, tmp_path, "base", "DGX-SPARK", 100, routes)
    assert not calls[0]["nim_plan"]["reuse_vss"]
