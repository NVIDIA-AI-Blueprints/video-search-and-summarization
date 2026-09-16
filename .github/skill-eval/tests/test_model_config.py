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
    "ANTHROPIC_BASE_URL": "https://inference-api.nvidia.com/v1",
    "ANTHROPIC_API_KEY": "secret",
}


def test_default_routes_preserve_claude_runner_configuration() -> None:
    routes = model_config.resolve_model_routes(DEFAULT_ENV)

    assert routes.coding.runtime == "claude-code"
    assert routes.operational.runtime == "claude-code"
    assert routes.coding.model == DEFAULT_ENV["ANTHROPIC_MODEL"]
    assert routes.operational.model == DEFAULT_ENV["ANTHROPIC_MODEL"]


def test_coding_and_operational_overrides_are_independent() -> None:
    routes = model_config.resolve_model_routes(
        {
            **DEFAULT_ENV,
            "CODEX_MODEL": "configured/codex",
            "SKILLS_EVAL_CODING_HARNESS": "codex",
            "SKILLS_EVAL_CODING_PROVIDER": "custom",
            "SKILLS_EVAL_CODING_MODEL": "coding/model",
            "SKILLS_EVAL_CODING_ENDPOINT_URL": "https://coding.example.test/v1/",
            "SKILLS_EVAL_CODING_API_KEY": "coding-secret",
            "SKILLS_EVAL_OPERATIONAL_HARNESS": "nemoclaw",
            "SKILLS_EVAL_OPERATIONAL_PROVIDER": "custom",
            "SKILLS_EVAL_OPERATIONAL_MODEL": "operational/model",
            "SKILLS_EVAL_OPERATIONAL_ENDPOINT_URL": "https://ops.example.test/v1/",
            "SKILLS_EVAL_OPERATIONAL_API_KEY": "ops-secret",
        }
    )

    assert routes.coding.runtime == "codex"
    assert routes.coding.model == "coding/model"
    assert routes.coding.endpoint_url == "https://coding.example.test/v1"
    assert routes.coding.api_key == "coding-secret"
    assert routes.operational.runtime == "nemoclaw"
    assert routes.operational.model == "operational/model"
    assert routes.operational.endpoint_url == "https://ops.example.test/v1"
    assert routes.operational.api_key == "ops-secret"


def test_legacy_eval_agent_remains_operational_default() -> None:
    routes = model_config.resolve_model_routes(
        {
            **DEFAULT_ENV,
            "EVAL_AGENT": "nemoclaw",
            "NEMOCLAW_PROVIDER": "custom",
            "NEMOCLAW_MODEL": "nvidia/model",
            "NEMOCLAW_ENDPOINT_URL": "https://models.example.test/v1",
        }
    )

    assert routes.coding.runtime == "claude-code"
    assert routes.operational.runtime == "nemoclaw"


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


def test_operational_nvidia_inference_maps_to_nemoclaw_custom() -> None:
    config = model_config.resolve_model_config(
        {
            **DEFAULT_ENV,
            "SKILLS_EVAL_OPERATIONAL_HARNESS": "nemoclaw",
            "SKILLS_EVAL_OPERATIONAL_PROVIDER": "nvidia-inference",
            "SKILLS_EVAL_OPERATIONAL_MODEL": "nvidia/model",
            "SKILLS_EVAL_OPERATIONAL_ENDPOINT_URL": "https://models.example.test/v1",
            "SKILLS_EVAL_OPERATIONAL_API_KEY": "ops-secret",
        },
        role="operational",
    )

    assert config.nemoclaw_provider == "custom"
    assert config.api_key == "ops-secret"


def test_operational_nvidia_build_uses_provider_managed_endpoint() -> None:
    config = model_config.resolve_model_config(
        {
            **DEFAULT_ENV,
            "SKILLS_EVAL_OPERATIONAL_HARNESS": "nemoclaw",
            "SKILLS_EVAL_OPERATIONAL_PROVIDER": "nvidia-build",
            "SKILLS_EVAL_OPERATIONAL_MODEL": "nvidia/model",
            "NVIDIA_API_KEY": "nvidia-secret",
        },
        role="operational",
    )

    assert config.nemoclaw_provider == "build"
    assert config.endpoint_url == ""


def test_coding_route_rejects_nvidia_build() -> None:
    with pytest.raises(ValueError, match="only supported by nemoclaw"):
        model_config.resolve_model_config(
            {
                **DEFAULT_ENV,
                "SKILLS_EVAL_CODING_PROVIDER": "nvidia-build",
                "SKILLS_EVAL_CODING_MODEL": "nvidia/model",
            },
            role="coding",
        )


@pytest.mark.parametrize("provider", ["nvidia-build", "build"])
def test_managed_nemoclaw_provider_rejects_endpoint(provider: str) -> None:
    environment = {
        **DEFAULT_ENV,
        "SKILLS_EVAL_OPERATIONAL_HARNESS": "nemoclaw",
        "SKILLS_EVAL_OPERATIONAL_PROVIDER": "nvidia-build",
        "SKILLS_EVAL_OPERATIONAL_MODEL": "nvidia/model",
        "SKILLS_EVAL_OPERATIONAL_ENDPOINT_URL": "https://wrong.example.test/v1",
        "NVIDIA_API_KEY": "secret",
    }
    if provider == "build":
        environment["SKILLS_EVAL_OPERATIONAL_PROVIDER"] = "default"
        environment["NEMOCLAW_PROVIDER"] = "build"

    with pytest.raises(ValueError, match="manages its own endpoint"):
        model_config.resolve_model_config(environment, role="operational")


def test_custom_route_requires_model_and_endpoint() -> None:
    with pytest.raises(ValueError, match="SKILLS_EVAL_OPERATIONAL_MODEL"):
        model_config.resolve_model_config(
            {
                **DEFAULT_ENV,
                "SKILLS_EVAL_OPERATIONAL_PROVIDER": "custom",
            },
            role="operational",
        )

    with pytest.raises(ValueError, match="SKILLS_EVAL_OPERATIONAL_ENDPOINT_URL"):
        model_config.resolve_model_config(
            {
                **DEFAULT_ENV,
                "SKILLS_EVAL_OPERATIONAL_PROVIDER": "custom",
                "SKILLS_EVAL_OPERATIONAL_MODEL": "custom/model",
            },
            role="operational",
        )


@pytest.mark.parametrize("endpoint", ["not-a-url", "https://"])
def test_malformed_endpoint_names_its_route(endpoint: str) -> None:
    with pytest.raises(
        ValueError, match="SKILLS_EVAL_CODING_ENDPOINT_URL.*HTTP\\(S\\)"
    ):
        model_config.resolve_model_config(
            {
                **DEFAULT_ENV,
                "SKILLS_EVAL_CODING_PROVIDER": "custom",
                "SKILLS_EVAL_CODING_MODEL": "custom/model",
                "SKILLS_EVAL_CODING_ENDPOINT_URL": endpoint,
            },
            role="coding",
        )
