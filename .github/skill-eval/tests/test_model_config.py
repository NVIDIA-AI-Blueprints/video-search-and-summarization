#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Tests for the evaluated-agent model route resolver."""

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


def test_claude_default_preserves_runner_route() -> None:
    config = model_config.resolve_model_config(
        {
            "EVAL_AGENT": "claude-code",
            "ANTHROPIC_MODEL": "aws/anthropic/bedrock-claude-opus-4-6",
            "ANTHROPIC_BASE_URL": "https://inference-api.nvidia.com/v1",
            "ANTHROPIC_API_KEY": "secret",
        }
    )

    assert config.provider == "nvidia-inference"
    assert config.model == "aws/anthropic/bedrock-claude-opus-4-6"
    assert config.endpoint_url == "https://inference-api.nvidia.com/v1"


def test_claude_model_and_endpoint_apply_together() -> None:
    config = model_config.resolve_model_config(
        {
            "SKILLS_EVAL_HARNESS": "claude-code",
            "SKILLS_EVAL_PROVIDER": "custom",
            "SKILLS_EVAL_MODEL": "custom/claude",
            "SKILLS_EVAL_ENDPOINT_URL": "https://models.example.test/v1/",
            "ANTHROPIC_API_KEY": "secret",
        }
    )

    assert config.model == "custom/claude"
    assert config.endpoint_url == "https://models.example.test/v1"


def test_claude_rejects_nvidia_build() -> None:
    with pytest.raises(ValueError, match="only supported by nemoclaw"):
        model_config.resolve_model_config(
            {
                "EVAL_AGENT": "claude-code",
                "SKILLS_EVAL_PROVIDER": "nvidia-build",
                "SKILLS_EVAL_MODEL": "nvidia/model",
            }
        )


def test_claude_rejects_generic_key_instead_of_ignoring_it() -> None:
    with pytest.raises(ValueError, match="SKILLS_EVAL_API_KEY is not supported"):
        model_config.resolve_model_config(
            {
                "EVAL_AGENT": "claude-code",
                "SKILLS_EVAL_API_KEY": "wrong-secret",
            }
        )


def test_codex_requires_its_own_default_model() -> None:
    with pytest.raises(
        ValueError, match="SKILLS_EVAL_MODEL or CODEX_MODEL is required for codex"
    ):
        model_config.resolve_model_config(
            {
                "EVAL_AGENT": "codex",
                "ANTHROPIC_MODEL": "aws/anthropic/bedrock-claude-opus-4-6",
                "ANTHROPIC_BASE_URL": "https://inference-api.nvidia.com/v1",
                "ANTHROPIC_API_KEY": "secret",
            }
        )


def test_codex_accepts_explicit_evaluated_model_override() -> None:
    config = model_config.resolve_model_config(
        {
            "EVAL_AGENT": "codex",
            "SKILLS_EVAL_MODEL": "openai/openai/gpt-5-codex",
            "ANTHROPIC_BASE_URL": "https://inference-api.nvidia.com/v1",
            "ANTHROPIC_API_KEY": "secret",
        }
    )

    assert config.model == "openai/openai/gpt-5-codex"


def test_nemoclaw_default_preserves_existing_route() -> None:
    config = model_config.resolve_model_config(
        {
            "EVAL_AGENT": "nemoclaw",
            "NEMOCLAW_PROVIDER": "custom",
            "NEMOCLAW_MODEL": "nvidia/model",
            "NEMOCLAW_ENDPOINT_URL": "https://models.example.test/v1",
            "COMPATIBLE_API_KEY": "secret",
        }
    )

    assert config.nemoclaw_provider == "custom"
    assert config.model == "nvidia/model"
    assert config.endpoint_url == "https://models.example.test/v1"


def test_nemoclaw_nvidia_inference_maps_to_custom() -> None:
    config = model_config.resolve_model_config(
        {
            "EVAL_AGENT": "nemoclaw",
            "SKILLS_EVAL_PROVIDER": "nvidia-inference",
            "SKILLS_EVAL_MODEL": "nvidia/model",
            "SKILLS_EVAL_ENDPOINT_URL": "https://inference-api.nvidia.com/v1",
            "SKILLS_EVAL_API_KEY": "secret",
        }
    )

    assert config.nemoclaw_provider == "custom"
    assert config.api_key == "secret"


def test_explicit_nvidia_inference_requires_model() -> None:
    with pytest.raises(
        ValueError,
        match="SKILLS_EVAL_MODEL is required for provider=nvidia-inference",
    ):
        model_config.resolve_model_config(
            {
                "EVAL_AGENT": "nemoclaw",
                "SKILLS_EVAL_PROVIDER": "nvidia-inference",
                "NEMOCLAW_MODEL": "runner/default-model",
                "NEMOCLAW_ENDPOINT_URL": "https://inference-api.nvidia.com/v1",
                "COMPATIBLE_API_KEY": "secret",
            }
        )


def test_nemoclaw_nvidia_build_uses_build_contract() -> None:
    config = model_config.resolve_model_config(
        {
            "EVAL_AGENT": "nemoclaw",
            "SKILLS_EVAL_PROVIDER": "nvidia-build",
            "SKILLS_EVAL_MODEL": "nvidia/model",
            "NVIDIA_API_KEY": "secret",
        }
    )

    assert config.nemoclaw_provider == "build"
    assert config.endpoint_url == ""


@pytest.mark.parametrize("provider", ["nvidia-build", "build"])
def test_managed_build_provider_rejects_explicit_endpoint(provider: str) -> None:
    environment = {
        "EVAL_AGENT": "nemoclaw",
        "SKILLS_EVAL_PROVIDER": "nvidia-build",
        "SKILLS_EVAL_MODEL": "nvidia/model",
        "SKILLS_EVAL_ENDPOINT_URL": "https://models.example.test/v1",
        "NVIDIA_API_KEY": "secret",
    }
    if provider == "build":
        environment["SKILLS_EVAL_PROVIDER"] = "default"
        environment["NEMOCLAW_PROVIDER"] = "build"

    with pytest.raises(ValueError, match="manages its own endpoint"):
        model_config.resolve_model_config(environment)


@pytest.mark.parametrize("provider", ["install-vllm", "ollama", "nim-local"])
def test_local_provider_rejects_explicit_endpoint(provider: str) -> None:
    with pytest.raises(ValueError, match="manages its own endpoint"):
        model_config.resolve_model_config(
            {
                "EVAL_AGENT": "nemoclaw",
                "SKILLS_EVAL_PROVIDER": "default",
                "NEMOCLAW_PROVIDER": provider,
                "SKILLS_EVAL_MODEL": "local/model",
                "SKILLS_EVAL_ENDPOINT_URL": "https://models.example.test/v1",
            }
        )


def test_custom_requires_explicit_endpoint() -> None:
    with pytest.raises(ValueError, match="SKILLS_EVAL_ENDPOINT_URL is required"):
        model_config.resolve_model_config(
            {
                "EVAL_AGENT": "nemoclaw",
                "SKILLS_EVAL_PROVIDER": "custom",
                "SKILLS_EVAL_MODEL": "nvidia/model",
                "ANTHROPIC_BASE_URL": "https://configured.example.test/v1",
                "ANTHROPIC_API_KEY": "secret",
            }
        )


def test_endpoint_requires_matching_explicit_model() -> None:
    with pytest.raises(ValueError, match="when SKILLS_EVAL_ENDPOINT_URL is set"):
        model_config.resolve_model_config(
            {
                "EVAL_AGENT": "claude-code",
                "SKILLS_EVAL_ENDPOINT_URL": "https://models.example.test/v1",
                "ANTHROPIC_MODEL": "configured/model",
                "ANTHROPIC_API_KEY": "secret",
            }
        )


@pytest.mark.parametrize("endpoint", ["not-a-url", "https://"])
def test_malformed_endpoint_is_rejected(endpoint: str) -> None:
    with pytest.raises(ValueError, match="HTTP\\(S\\) URL with a host"):
        model_config.resolve_model_config(
            {
                "EVAL_AGENT": "claude-code",
                "SKILLS_EVAL_PROVIDER": "custom",
                "SKILLS_EVAL_MODEL": "custom/claude",
                "SKILLS_EVAL_ENDPOINT_URL": endpoint,
                "ANTHROPIC_API_KEY": "secret",
            }
        )


def test_missing_key_names_expected_runner_variable() -> None:
    with pytest.raises(ValueError, match="NVIDIA_API_KEY"):
        model_config.resolve_model_config(
            {
                "EVAL_AGENT": "nemoclaw",
                "SKILLS_EVAL_PROVIDER": "nvidia-build",
                "SKILLS_EVAL_MODEL": "nvidia/model",
            }
        )
