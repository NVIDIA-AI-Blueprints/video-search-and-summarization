# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""The release Search prompt policy is mounted, importable, and preserves time queries."""

from __future__ import annotations

import ast
import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import ClassVar

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / "deploy"
POLICY = DEPLOY / "docker/services/agent/search_prompt_policy.py"
HELM_POLICY = (
    DEPLOY
    / "helm/developer-profiles/dev-profile-search/configs/vss-agent/search_prompt_policy.py"
)
SEARCH_SOURCE = (
    ROOT / "services/agent/packages/vss_agents/src/vss_agents/tools/search.py"
)
WORKER = "search_prompt_policy.SearchPromptWorker"


def _source_constant(name):
    for node in ast.parse(SEARCH_SOURCE.read_text()).body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == name
            for target in node.targets
        ):
            return ast.literal_eval(node.value)
    raise AssertionError(f"Missing frozen search constant {name}")


def _load_policy(monkeypatch, prompt=None):
    packages = (
        "nat",
        "nat.data_models",
        "nat.data_models.config",
        "vss_agents",
        "vss_agents.api",
        "vss_agents.api.custom_fastapi_worker",
        "vss_agents.tools",
        "vss_agents.tools.search",
    )
    modules = {name: ModuleType(name) for name in packages}
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)
    modules["nat.data_models.config"].Config = object

    class StandardWorker:
        initialized: ClassVar[list[object]] = []

        def __init__(self, config):
            self.initialized.append(config)

    modules[
        "vss_agents.api.custom_fastapi_worker"
    ].CustomFastApiFrontEndWorker = StandardWorker
    search = modules["vss_agents.tools.search"]
    search.QUERY_DECOMPOSITION_PROMPT = (
        _source_constant("QUERY_DECOMPOSITION_PROMPT") if prompt is None else prompt
    )
    modules["vss_agents.tools"].search = search
    spec = importlib.util.spec_from_file_location("release_search_prompt_test", POLICY)
    policy = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(policy)
    return policy, search, StandardWorker


def test_policy_preserves_original_prompt_and_standard_worker(monkeypatch):
    policy, search, base = _load_policy(monkeypatch)
    original = search.QUERY_DECOMPOSITION_PROMPT
    config = object()
    worker = policy.SearchPromptWorker(config)
    assert isinstance(worker, base)
    assert base.initialized == [config]
    assert search.QUERY_DECOMPOSITION_PROMPT == original.replace(
        "User query: {user_query}",
        policy.TEMPORAL_GROUNDING + "\n\nUser query: {user_query}",
    )
    rendered = search.QUERY_DECOMPOSITION_PROMPT.format(
        video_sources="Video files: warehouse",
        few_shot_examples=_source_constant("DEFAULT_FEW_SHOT_EXAMPLES"),
        user_query="Find a person from 1 pm to 2 pm",
    )
    assert "omit timestamp_start and timestamp_end" in rendered
    assert "explicitly requests a time interval" in rendered
    assert '{"timestamp_start": null, "timestamp_end": null}' in rendered
    assert '"timestamp_start": "2025-01-01T13:00:00Z"' in rendered
    assert rendered.endswith("User query: Find a person from 1 pm to 2 pm")


def test_policy_is_idempotent(monkeypatch):
    policy, search, base = _load_policy(monkeypatch)
    policy.SearchPromptWorker(object())
    policy.SearchPromptWorker(object())
    assert search.QUERY_DECOMPOSITION_PROMPT.count(policy.TEMPORAL_GROUNDING) == 1
    assert len(base.initialized) == 2


def test_policy_rejects_unknown_image_prompt(monkeypatch):
    policy, search, base = _load_policy(monkeypatch, "Unsupported prompt")
    with pytest.raises(RuntimeError, match="VSS 3.3 query template"):
        policy.SearchPromptWorker(object())
    assert search.QUERY_DECOMPOSITION_PROMPT == "Unsupported prompt"
    assert not base.initialized


def test_policy_copies_and_compose_import_path():
    assert POLICY.read_bytes() == HELM_POLICY.read_bytes()
    compose = yaml.safe_load((DEPLOY / "docker/services/agent/compose.yml").read_text())
    agent = compose["services"]["vss-agent"]
    assert agent["environment"]["PYTHONPATH"] == "${VSS_AGENT_PYTHONPATH:-}"
    overrides = DEPLOY / "docker/developer-profiles/dev-profile-search/overrides.env"
    assert (
        "VSS_AGENT_PYTHONPATH=/vss-agent/deploy/docker/services/agent"
        in overrides.read_text()
    )
    assert "${VSS_APPS_DIR}:/vss-agent/deploy/docker:ro" in agent["volumes"]
    path = (
        DEPLOY
        / "docker/developer-profiles/dev-profile-search/vss-agent/configs/config.yml"
    )
    config = yaml.safe_load(path.read_text())
    assert config["general"]["front_end"]["runner_class"] == WORKER


@pytest.mark.skipif(
    shutil.which("helm") is None, reason="Helm is required for chart rendering"
)
def test_standalone_search_chart_keeps_policy_opt_in():
    chart = DEPLOY / "helm/services/agent/charts/agent"
    rendered = subprocess.run(
        [
            "helm",
            "template",
            "standalone",
            str(chart),
            "--set",
            "enabled=true,profile=search",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    documents = [item for item in yaml.safe_load_all(rendered.stdout) if item]
    deployment = next(item for item in documents if item.get("kind") == "Deployment")
    agent = deployment["spec"]["template"]["spec"]["containers"][0]
    assert not any(
        mount.get("subPath") == "search_prompt_policy.py"
        for mount in agent["volumeMounts"]
    )


@pytest.mark.parametrize("profile", ["base", "alerts", "lvs", "search"])
@pytest.mark.skipif(
    shutil.which("helm") is None, reason="Helm is required for chart rendering"
)
def test_helm_policy_is_mounted_only_for_search(profile):
    chart = DEPLOY / "helm/developer-profiles" / f"dev-profile-{profile}"
    subprocess.run(
        ["helm", "dependency", "build", str(chart)],
        check=True,
        capture_output=True,
        text=True,
    )
    rendered = subprocess.run(
        ["helm", "template", "prompt-policy", str(chart)],
        check=True,
        capture_output=True,
        text=True,
    )
    documents = [item for item in yaml.safe_load_all(rendered.stdout) if item]
    pod = next(
        item["spec"]["template"]["spec"]
        for item in documents
        if item.get("kind") == "Deployment"
        and any(
            container["name"] == "vss-agent"
            for container in item["spec"]["template"]["spec"]["containers"]
        )
    )
    agent = next(
        container for container in pod["containers"] if container["name"] == "vss-agent"
    )
    mounts = [
        mount
        for mount in agent["volumeMounts"]
        if mount.get("subPath") == "search_prompt_policy.py"
    ]
    if profile != "search":
        assert not mounts
        return
    assert mounts == [
        {
            "name": "config",
            "mountPath": "/etc/vss-agent/search_prompt_policy.py",
            "subPath": "search_prompt_policy.py",
            "readOnly": True,
        }
    ]
    env = {item["name"]: item.get("value") for item in agent["env"]}
    assert env["PYTHONPATH"] == "/etc/vss-agent"
    config_name = next(
        volume["configMap"]["name"]
        for volume in pod["volumes"]
        if volume["name"] == "config"
    )
    config_map = next(
        item
        for item in documents
        if item.get("kind") == "ConfigMap" and item["metadata"]["name"] == config_name
    )
    assert (
        config_map["data"]["search_prompt_policy.py"].rstrip()
        == POLICY.read_text().rstrip()
    )
    config = yaml.safe_load(config_map["data"]["config.yml"])
    assert config["general"]["front_end"]["runner_class"] == WORKER
