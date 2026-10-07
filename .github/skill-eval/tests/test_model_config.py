#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Tests for independent coding and operational model routes."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "model_config", Path(__file__).resolve().parents[1] / "model_config.py"
)
model_config = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = model_config
_SPEC.loader.exec_module(model_config)

DEFAULT_ENV = {
    "ANTHROPIC_MODEL": "aws/anthropic/bedrock-claude-opus-4-6",
    # The legacy runner endpoint is intentionally different: model_config
    # must consolidate routes on the public NVIDIA inference source.
    "ANTHROPIC_BASE_URL": "https://legacy-gateway.example.test/v1",
    "ANTHROPIC_API_KEY": "secret",
}


def test_catalog_source_is_distinct_from_fixed_inference_api() -> None:
    assert model_config.NVIDIA_INFERENCE_SOURCE_URL == "https://inference.nvidia.com/"
    assert (
        model_config.NVIDIA_INFERENCE_API_BASE_URL
        == "https://inference-api.nvidia.com/v1"
    )


def test_default_routes_use_codex_sol_and_nemoclaw_opus() -> None:
    routes = model_config.resolve_model_routes(DEFAULT_ENV)

    assert routes.coding.runtime == "codex"
    assert routes.operational.runtime == "nemoclaw"
    assert routes.coding.model == "azure/openai/gpt-6.1-sol"
    assert routes.operational.model == "aws/anthropic/bedrock-claude-opus-5-5"
    assert routes.coding.provider == model_config.NVIDIA_INFERENCE_PROVIDER
    assert routes.operational.provider == model_config.NVIDIA_INFERENCE_PROVIDER
    assert routes.coding.endpoint_url == model_config.NVIDIA_INFERENCE_API_BASE_URL
    assert (
        routes.operational.endpoint_url
        == model_config.NVIDIA_INFERENCE_API_BASE_URL
    )


def test_legacy_hosted_deployment_name_resolves_to_new_choice() -> None:
    route = model_config.resolve_model_config(
        {**DEFAULT_ENV, "SKILLS_EVAL_CODING_DEPLOYMENT": "nvidia-inference"},
        role="coding",
    )

    assert route.provider == "hosted-nvidia-inference"


def test_coding_and_operational_overrides_are_independent() -> None:
    routes = model_config.resolve_model_routes(
        {
            **DEFAULT_ENV,
            "CODEX_MODEL": "configured/codex",
            "SKILLS_EVAL_CODING_HARNESS": "codex",
            "SKILLS_EVAL_CODING_MODEL": "coding/model",
            "SKILLS_EVAL_CODING_API_KEY": "coding-secret",
            "SKILLS_EVAL_OPERATIONAL_HARNESS": "nemoclaw",
            "SKILLS_EVAL_OPERATIONAL_MODEL": "operational/model",
            "SKILLS_EVAL_OPERATIONAL_API_KEY": "ops-secret",
        }
    )

    assert routes.coding.runtime == "codex"
    assert routes.coding.model == "coding/model"
    assert routes.coding.endpoint_url == model_config.NVIDIA_INFERENCE_API_BASE_URL
    assert routes.coding.api_key == "coding-secret"
    assert routes.operational.runtime == "nemoclaw"
    assert routes.operational.model == "operational/model"
    assert (
        routes.operational.endpoint_url
        == model_config.NVIDIA_INFERENCE_API_BASE_URL
    )
    assert routes.operational.api_key == "ops-secret"


def test_legacy_eval_agent_remains_operational_default() -> None:
    routes = model_config.resolve_model_routes(
        {
            **DEFAULT_ENV,
            "EVAL_AGENT": "nemoclaw",
            "NEMOCLAW_PROVIDER": "build",
            "NEMOCLAW_MODEL": "nvidia/model",
            "NEMOCLAW_ENDPOINT_URL": "https://untrusted.example.test/v1",
        }
    )

    assert routes.coding.runtime == "codex"
    assert routes.operational.runtime == "nemoclaw"
    assert routes.operational.provider == model_config.NVIDIA_INFERENCE_PROVIDER
    assert (
        routes.operational.endpoint_url
        == model_config.NVIDIA_INFERENCE_API_BASE_URL
    )


def test_coding_route_rejects_nemoclaw() -> None:
    with pytest.raises(ValueError, match="unsupported coding harness"):
        model_config.resolve_model_config(
            {**DEFAULT_ENV, "SKILLS_EVAL_CODING_HARNESS": "nemoclaw"},
            role="coding",
        )


def test_configured_codex_model_overrides_fallback() -> None:
    route = model_config.resolve_model_config(
        {**DEFAULT_ENV, "CODEX_MODEL": "configured/codex"}, role="coding"
    )
    assert route.model == "configured/codex"


def test_explicit_claude_harness_retains_its_configured_model() -> None:
    route = model_config.resolve_model_config(
        {**DEFAULT_ENV, "SKILLS_EVAL_CODING_HARNESS": "claude-code"}, role="coding"
    )
    assert route.runtime == "claude-code"
    assert route.model == DEFAULT_ENV["ANTHROPIC_MODEL"]


def test_operational_nemoclaw_uses_nvidia_inference() -> None:
    config = model_config.resolve_model_config(
        {
            **DEFAULT_ENV,
            "SKILLS_EVAL_OPERATIONAL_HARNESS": "nemoclaw",
            "SKILLS_EVAL_OPERATIONAL_MODEL": "nvidia/model",
            "SKILLS_EVAL_OPERATIONAL_API_KEY": "ops-secret",
        },
        role="operational",
    )

    assert config.provider == model_config.NVIDIA_INFERENCE_PROVIDER
    assert config.api_key == "ops-secret"
    assert config.endpoint_url == model_config.NVIDIA_INFERENCE_API_BASE_URL


def test_legacy_provider_inputs_cannot_change_nvidia_inference_route() -> None:
    config = model_config.resolve_model_config(
        {
            **DEFAULT_ENV,
            "SKILLS_EVAL_OPERATIONAL_HARNESS": "nemoclaw",
            "SKILLS_EVAL_OPERATIONAL_PROVIDER": "nvidia-build",
            "SKILLS_EVAL_OPERATIONAL_MODEL": "nvidia/model",
            "SKILLS_EVAL_OPERATIONAL_API_KEY": "ops-secret",
            "NEMOCLAW_PROVIDER": "build",
            "NEMOCLAW_ENDPOINT_URL": "https://untrusted.example.test/v1",
            "NVIDIA_API_KEY": "nvidia-secret",
        },
        role="operational",
    )

    assert config.provider == model_config.NVIDIA_INFERENCE_PROVIDER
    assert config.endpoint_url == model_config.NVIDIA_INFERENCE_API_BASE_URL
    assert config.api_key == "ops-secret"


def test_endpoint_override_cannot_redirect_runner_credential() -> None:
    config = model_config.resolve_model_config(
        {
            **DEFAULT_ENV,
            "SKILLS_EVAL_CODING_MODEL": "nvidia/model",
            "SKILLS_EVAL_CODING_ENDPOINT_URL": "https://attacker.example.test/v1",
        },
        role="coding",
    )

    assert config.endpoint_url == model_config.NVIDIA_INFERENCE_API_BASE_URL
    assert config.api_key == DEFAULT_ENV["ANTHROPIC_API_KEY"]


def test_cancelled_launch_leaves_no_config_for_next_invocation(tmp_path):
    import asyncio
    from pathlib import PurePosixPath
    import tomllib
    import types
    from unittest.mock import patch

    homes = []
    secrets = []

    class Codex:
        async def run(self, instruction, environment, context):
            home = tmp_path / self._REMOTE_CODEX_HOME.relative_to("/tmp")
            secret = tmp_path / self._REMOTE_CODEX_SECRETS_DIR.relative_to("/tmp")
            home.mkdir(parents=True, exist_ok=True)
            secret.mkdir(parents=True, exist_ok=True)
            homes.append(home)
            secrets.append(secret)
            # Reproduce Harbor's append, leaving both files after interruption.
            with (home / "config.toml").open("a") as handle:
                handle.write('openai_base_url = "http://current-provider/v1"\n')
            (secret / "auth.json").write_text("current credential")
            assert tomllib.loads((home / "config.toml").read_text())["openai_base_url"] == "http://current-provider/v1"
            if len(homes) == 1:
                (home / "sessions").mkdir()
                (home / "sessions/old.jsonl").write_text("old deployment evidence")
                raise asyncio.CancelledError()
            assert not (home / "sessions").exists()

    stub = types.ModuleType("harbor.agents.installed.codex")
    stub.Codex = Codex
    with patch.dict("sys.modules", {"harbor.agents.installed.codex": stub}):
        spec = importlib.util.spec_from_file_location("nv_codex_isolation_test", Path(__file__).resolve().parents[1] / "agents/nv_codex.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    agent = module.NvCodex()
    agent.model_name = "azure/openai/gpt-6-astra"
    assert agent.model_name.split("/")[-1] == "azure/openai/gpt-6-astra"
    assert agent.model_name.split("/", 1) == ["azure", "openai/gpt-6-astra"]
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(agent.run("first", None, None))
    asyncio.run(agent.run("retry", None, None))
    assert homes[0] != homes[1]
    assert secrets[0] != secrets[1]
    assert homes[1].parent == secrets[1].parent
    assert isinstance(agent._REMOTE_CODEX_HOME, PurePosixPath)
