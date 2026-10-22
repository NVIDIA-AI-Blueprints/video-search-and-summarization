# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Direct NAT commands need the same local-client key as the agent entrypoint."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

DEPLOY = Path(__file__).resolve().parents[1]
LOCAL_KEY = "NOAPIKEYSET"


@pytest.mark.skipif(shutil.which("docker") is None, reason="Docker Compose is required")
@pytest.mark.parametrize("provided", [None, "", "operator-provided-test-key"])
def test_compose_key_default_and_operator_override(tmp_path, provided):
    compose = yaml.safe_load((DEPLOY / "docker/services/agent/compose.yml").read_text())
    expression = compose["services"]["vss-agent"]["environment"]["OPENAI_API_KEY"]
    # Render only the actual key expression, without loading deployment credentials.
    model = {
        "services": {
            "agent": {
                "image": "test",
                "environment": {"OPENAI_API_KEY": expression},
            }
        }
    }
    path = tmp_path / "compose.yml"
    path.write_text(yaml.safe_dump(model))
    env = {
        key: os.environ[key]
        for key in ("PATH", "HOME", "DOCKER_CONFIG")
        if key in os.environ
    }
    if provided is not None:
        env["OPENAI_API_KEY"] = provided
    rendered = subprocess.run(
        [
            "docker",
            "compose",
            "--env-file",
            "/dev/null",
            "-f",
            str(path),
            "config",
            "--format",
            "json",
        ],
        cwd=tmp_path,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    agent = json.loads(rendered.stdout)["services"]["agent"]
    assert agent["environment"]["OPENAI_API_KEY"] == (provided or LOCAL_KEY)


def _helm_agent(tmp_path, profile=None, extra_env=None):
    chart = (
        DEPLOY / "helm/developer-profiles" / f"dev-profile-{profile}"
        if profile
        else DEPLOY / "helm/services/agent/charts/agent"
    )
    if profile:
        subprocess.run(
            ["helm", "dependency", "build", str(chart)],
            check=True,
            capture_output=True,
            text=True,
        )
    args = ["helm", "template", "client-key", str(chart)]
    if not profile:
        args.extend(["--set", "enabled=true"])
    if extra_env:
        values = tmp_path / "values.yaml"
        values.write_text(yaml.safe_dump({"extraEnv": extra_env}))
        args.extend(["-f", str(values)])
    rendered = subprocess.run(args, check=True, capture_output=True, text=True)
    for item in yaml.safe_load_all(rendered.stdout):
        if item and item.get("kind") == "Deployment":
            for container in item["spec"]["template"]["spec"]["containers"]:
                if container["name"] == "vss-agent":
                    return container
    raise AssertionError("The chart did not render a vss-agent container")


@pytest.mark.skipif(shutil.which("helm") is None, reason="Helm is required")
@pytest.mark.parametrize("profile", [None, "base", "alerts", "lvs", "search"])
def test_helm_default_is_available_to_direct_commands(tmp_path, profile):
    agent = _helm_agent(tmp_path, profile)
    keys = [entry for entry in agent["env"] if entry["name"] == "OPENAI_API_KEY"]
    assert keys == [{"name": "OPENAI_API_KEY", "value": LOCAL_KEY}]


@pytest.mark.skipif(shutil.which("helm") is None, reason="Helm is required")
@pytest.mark.parametrize(
    "override",
    [
        {"name": "OPENAI_API_KEY", "value": "operator-provided-test-key"},
        {
            "name": "OPENAI_API_KEY",
            "valueFrom": {"secretKeyRef": {"name": "operator-key", "key": "api-key"}},
        },
    ],
)
def test_helm_preserves_operator_keys_and_secret_references(tmp_path, override):
    agent = _helm_agent(tmp_path, extra_env=[override])
    keys = [entry for entry in agent["env"] if entry["name"] == "OPENAI_API_KEY"]
    assert keys == [override]
