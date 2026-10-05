# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import subprocess
from pathlib import Path

import pytest
import yaml

PROFILE = Path(__file__).resolve().parents[1] / "helm/developer-profiles/dev-profile-lvs"


@pytest.fixture(scope="module", autouse=True)
def dependencies():
    subprocess.run(["helm", "dependency", "build", str(PROFILE)], check=True, capture_output=True)


def render(opus=True, overrides=()):
    cmd = ["helm", "template", "vss", str(PROFILE), "--set", "global.externalHost=lvs.test"]
    if opus:
        cmd += ["-f", str(PROFILE / "values-opus5.yaml")]
    for value in overrides:
        cmd += ["--set", value]
    result = subprocess.run(cmd, check=True, capture_output=True, text=True)
    return [doc for doc in yaml.safe_load_all(result.stdout) if doc]


def lvs_env(docs):
    deployment = next(
        doc for doc in docs
        if doc["kind"] == "Deployment" and "vss-summarization" in doc["metadata"]["name"]
    )
    return deployment["spec"]["template"]["spec"]["containers"][0]["env"]


def gpu_requests(docs):
    total = 0
    for doc in docs:
        spec = doc.get("spec", {})
        if doc["kind"] == "NIMService":
            total += int(spec["resources"]["requests"].get("nvidia.com/gpu", 0)) * spec.get("replicas", 1)
        elif doc["kind"] in {"Deployment", "StatefulSet", "DaemonSet"}:
            containers = spec["template"]["spec"]["containers"]
            total += sum(
                int(c.get("resources", {}).get("requests", {}).get("nvidia.com/gpu", 0))
                for c in containers
            ) * spec.get("replicas", 1)
    return total


def test_opus_routes_credentials_and_two_gpu_budget():
    docs = render()
    env = lvs_env(docs)
    by_name = {item["name"]: item for item in env}
    assert by_name["LVS_LLM_BASE_URL"]["value"] == "https://inference-api.nvidia.com/v1"
    assert by_name["LVS_LLM_MODEL_NAME"]["value"] == "azure/anthropic/claude-opus-5"
    assert by_name["NVIDIA_API_KEY"] == {
        "name": "NVIDIA_API_KEY",
        "valueFrom": {"secretKeyRef": {"name": "lvs-inference-api", "key": "api-key"}},
    }
    assert sum(item["name"] == "NVIDIA_API_KEY" for item in env) == 1
    assert not any(doc["kind"] == "NIMService" for doc in docs)
    assert gpu_requests(docs) == 2


def test_stock_profile_retains_three_gpus_and_literal_credentials():
    docs = render(opus=False)
    assert gpu_requests(docs) == 3
    key = next(item for item in lvs_env(docs) if item["name"] == "NVIDIA_API_KEY")
    assert key == {"name": "NVIDIA_API_KEY", "value": ""}


def test_secret_reference_overrides_literal_key():
    docs = render(overrides=(
        "vss-summarization.llmApiKeySecret.name=existing-inference",
        "vss-summarization.llmApiKeySecret.key=token",
        "vss-summarization.extraEnv[0].name=NVIDIA_API_KEY",
        "vss-summarization.extraEnv[0].value=must-not-render",
    ))
    env = lvs_env(docs)
    key = next(item for item in env if item["name"] == "NVIDIA_API_KEY")
    assert key["valueFrom"]["secretKeyRef"] == {"name": "existing-inference", "key": "token"}
    assert "must-not-render" not in str(env)


def test_named_secret_requires_key():
    with pytest.raises(subprocess.CalledProcessError) as error:
        render(overrides=("vss-summarization.llmApiKeySecret.key=",))
    assert "llmApiKeySecret.key is required" in error.value.stderr


def sampling(docs):
    config = next(
        doc["data"]["config.yaml"] for doc in docs
        if doc["kind"] == "ConfigMap" and "vss-summarization" in doc["metadata"]["name"]
    )
    class Loader(yaml.SafeLoader):
        pass
    Loader.add_constructor("!ENV", lambda loader, node: loader.construct_scalar(node))
    return yaml.load(config, Loader=Loader)["tools"]["summarization_llm"]["params"]


def test_opus_omits_unsupported_sampling_and_stock_retains_defaults():
    opus = sampling(render())
    assert opus["temperature"] is None
    assert opus["top_p"] is None
    stock = sampling(render(opus=False))
    assert stock["temperature"] == 0.2
    assert stock["top_p"] == 0.7
