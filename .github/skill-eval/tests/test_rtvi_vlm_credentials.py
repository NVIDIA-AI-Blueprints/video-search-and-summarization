# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Remote RT-VLM auth must not leak NVIDIA credentials to arbitrary hosts."""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parents[3]


def _load_brev_env():
    harbor = types.ModuleType("harbor")
    environments = types.ModuleType("harbor.environments")
    base = types.ModuleType("harbor.environments.base")
    base.BaseEnvironment = type("BaseEnvironment", (), {})
    base.ExecResult = lambda **kwargs: types.SimpleNamespace(**kwargs)
    path = ROOT / ".github/skill-eval/envs/brev_env.py"
    spec = importlib.util.spec_from_file_location("brev_env_rtvi_key_test", path)
    module = importlib.util.module_from_spec(spec)
    with mock.patch.dict(
        sys.modules,
        {
            "harbor": harbor,
            "harbor.environments": environments,
            "harbor.environments.base": base,
        },
    ):
        spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://inference-api.nvidia.com",
        "https://inference-api.nvidia.com/v1",
        "https://integrate.api.nvidia.com:443/v1",
    ],
)
def test_nvidia_hosted_vlm_uses_nvidia_key(endpoint):
    resolve = _load_brev_env()._resolve_rtvi_vlm_api_key
    assert (
        resolve({"VLM_REMOTE_URL": endpoint, "NVIDIA_API_KEY": "nvidia-test"})
        == "nvidia-test"
    )


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://customer.example/v1",
        "https://inference-api.nvidia.com.evil.example/v1",
        "http://inference-api.nvidia.com/v1",
        "https://inference-api.nvidia.com:8443/v1",
        "https://user@inference-api.nvidia.com/v1",
        "https://inference-api.nvidia.com:invalid/v1",
        "",
    ],
)
def test_other_vlm_endpoints_never_inherit_nvidia_key(endpoint):
    resolve = _load_brev_env()._resolve_rtvi_vlm_api_key
    assert resolve({"VLM_REMOTE_URL": endpoint, "NVIDIA_API_KEY": "nvidia-test"}) == ""


def test_explicit_rtvi_key_wins_for_any_endpoint():
    resolve = _load_brev_env()._resolve_rtvi_vlm_api_key
    assert (
        resolve(
            {
                "VLM_REMOTE_URL": "https://customer.example/v1",
                "RTVI_VLM_API_KEY": "customer-test",
                "NVIDIA_API_KEY": "nvidia-test",
            }
        )
        == "customer-test"
    )


def test_nvidia_key_is_not_required_for_keyless_endpoint():
    resolve = _load_brev_env()._resolve_rtvi_vlm_api_key
    assert resolve({"VLM_REMOTE_URL": "http://localhost:30081"}) == ""


def test_compose_keeps_ngc_and_remote_inference_credentials_distinct():
    compose = (
        ROOT / "deploy/docker/services/rtvi/rtvi-vlm/rtvi-vlm-docker-compose.yml"
    ).read_text()
    assert 'VIA_VLM_API_KEY: "${RTVI_VLM_API_KEY:-${NGC_CLI_API_KEY:-}}"' in compose
    assert 'NGC_API_KEY: "${NGC_CLI_API_KEY:-${RTVI_VLM_API_KEY:-}}"' in compose
