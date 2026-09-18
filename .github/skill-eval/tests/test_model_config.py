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


def test_default_routes_preserve_claude_runner_configuration() -> None:
    routes = model_config.resolve_model_routes(DEFAULT_ENV)

    assert routes.coding.runtime == "claude-code"
    assert routes.operational.runtime == "claude-code"
    assert routes.coding.model == DEFAULT_ENV["ANTHROPIC_MODEL"]
    assert routes.operational.model == DEFAULT_ENV["ANTHROPIC_MODEL"]
    assert routes.coding.provider == model_config.NVIDIA_INFERENCE_PROVIDER
    assert routes.operational.provider == model_config.NVIDIA_INFERENCE_PROVIDER
    assert routes.coding.endpoint_url == model_config.NVIDIA_INFERENCE_API_BASE_URL
    assert (
        routes.operational.endpoint_url
        == model_config.NVIDIA_INFERENCE_API_BASE_URL
    )


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

    assert routes.coding.runtime == "claude-code"
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


def test_codex_requires_its_own_model() -> None:
    with pytest.raises(ValueError, match="CODEX_MODEL is required for codex"):
        model_config.resolve_model_config(
            {**DEFAULT_ENV, "SKILLS_EVAL_CODING_HARNESS": "codex"},
            role="coding",
        )


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
