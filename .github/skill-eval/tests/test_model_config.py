# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import model_config  # noqa: E402


def test_claude_uses_native_anthropic_configuration_by_default() -> None:
    env = {
        "CLAUDE_CODE_MODEL": "claude-opus-4-6",
        "ANTHROPIC_MODEL": "must-not-win",
        "ANTHROPIC_BASE_URL": "https://must-not-be-used.example.test",
        "ANTHROPIC_API_KEY": "anthropic-key",
    }

    config = model_config.apply_model_config(env)

    assert config.runtime == "claude-code"
    assert config.provider == "anthropic"
    assert config.model == "claude-opus-4-6"
    assert config.endpoint_url == ""
    assert config.api_key == "anthropic-key"


@pytest.mark.parametrize("provider", ("nvidia-inference", "nvidia-build", "custom"))
def test_claude_rejects_provider_selection(provider: str) -> None:
    env = {
        "EVAL_AGENT": "claude-code",
        "SKILLS_EVAL_PROVIDER": provider,
        "SKILLS_EVAL_MODEL": "claude-opus-4-6",
        "ANTHROPIC_API_KEY": "anthropic-key",
    }

    with pytest.raises(ValueError, match="not compatible"):
        model_config.resolve_model_config(env)


def test_claude_rejects_endpoint_selection() -> None:
    env = {
        "EVAL_AGENT": "claude-code",
        "SKILLS_EVAL_PROVIDER": "default",
        "SKILLS_EVAL_MODEL": "claude-opus-4-6",
        "SKILLS_EVAL_ENDPOINT_URL": "https://models.example.test",
        "ANTHROPIC_API_KEY": "anthropic-key",
    }

    with pytest.raises(ValueError, match="cannot be set for claude-code"):
        model_config.resolve_model_config(env)


def test_standardized_harness_overrides_legacy_eval_agent() -> None:
    env = {
        "SKILLS_EVAL_HARNESS": "nemoclaw",
        "EVAL_AGENT": "claude-code",
        "SKILLS_EVAL_PROVIDER": "nvidia-build",
        "SKILLS_EVAL_MODEL": "nvidia/model",
        "NVIDIA_API_KEY": "build-key",
    }

    config = model_config.apply_model_config(env)

    assert config.runtime == "nemoclaw"
    assert env["EVAL_AGENT"] == "nemoclaw"


def test_optional_model_overrides_only_the_evaluated_agent() -> None:
    env = {
        "EVAL_AGENT": "nemoclaw",
        "SKILLS_EVAL_PROVIDER": "nvidia-inference",
        "SKILLS_EVAL_MODEL": "selected-agent-model",
        "ANTHROPIC_MODEL": "existing-coordinator-and-judge-model",
        "ANTHROPIC_BASE_URL": "https://inference-api.nvidia.com/v1",
        "ANTHROPIC_API_KEY": "inference-key",
    }

    config = model_config.apply_model_config(env)

    assert config.model == "selected-agent-model"
    assert env["ANTHROPIC_MODEL"] == "existing-coordinator-and-judge-model"


def test_nemoclaw_supports_nvidia_build_model_selection() -> None:
    env = {
        "EVAL_AGENT": "nemoclaw",
        "SKILLS_EVAL_PROVIDER": "nvidia-build",
        "SKILLS_EVAL_MODEL": "nvidia/nemotron-3.5-lightning-30b-a3b",
        "NVIDIA_API_KEY": "build-key",
        "ANTHROPIC_MODEL": "existing-coordinator-and-judge-model",
    }

    config = model_config.apply_model_config(env)

    assert config.notebook_provider == "build"
    assert config.endpoint_url == ""
    assert config.api_key == "build-key"
    assert env["ANTHROPIC_MODEL"] == "existing-coordinator-and-judge-model"


def test_nvidia_build_is_rejected_for_claude_code() -> None:
    env = {
        "EVAL_AGENT": "claude-code",
        "SKILLS_EVAL_PROVIDER": "nvidia-build",
        "SKILLS_EVAL_MODEL": "nvidia/nemotron-3.5-lightning-30b-a3b",
        "NVIDIA_API_KEY": "build-key",
    }

    with pytest.raises(ValueError, match="not compatible"):
        model_config.resolve_model_config(env)


def test_nvidia_build_requires_an_explicit_model() -> None:
    env = {
        "EVAL_AGENT": "nemoclaw",
        "SKILLS_EVAL_PROVIDER": "nvidia-build",
        "NVIDIA_API_KEY": "build-key",
        "ANTHROPIC_MODEL": "must-not-be-reused",
    }

    with pytest.raises(ValueError, match="SKILLS_EVAL_MODEL is required"):
        model_config.resolve_model_config(env)


@pytest.mark.parametrize("runtime", ("codex", "nemoclaw"))
def test_custom_provider_uses_only_standardized_model_inputs(runtime: str) -> None:
    env = {
        "EVAL_AGENT": runtime,
        "SKILLS_EVAL_PROVIDER": "custom",
        "SKILLS_EVAL_MODEL": "custom-model",
        "SKILLS_EVAL_ENDPOINT_URL": "https://models.example.test/v1/",
        "SKILLS_EVAL_API_KEY": "custom-key",
        "ANTHROPIC_BASE_URL": "https://must-not-be-used.example.test",
        "ANTHROPIC_API_KEY": "must-not-be-used",
    }

    config = model_config.apply_model_config(env)

    assert config.provider == "custom"
    assert config.model == "custom-model"
    assert config.endpoint_url == "https://models.example.test/v1"
    assert config.api_key == "custom-key"


def test_custom_provider_requires_endpoint_and_key() -> None:
    env = {
        "EVAL_AGENT": "nemoclaw",
        "SKILLS_EVAL_PROVIDER": "custom",
        "SKILLS_EVAL_MODEL": "custom-model",
    }

    with pytest.raises(ValueError, match="SKILLS_EVAL_ENDPOINT_URL is required"):
        model_config.resolve_model_config(env)


def test_api_key_is_hidden_from_config_repr() -> None:
    env = {
        "SKILLS_EVAL_MODEL": "claude-opus-4-6",
        "ANTHROPIC_API_KEY": "do-not-render",
    }

    config = model_config.resolve_model_config(env)

    assert "do-not-render" not in repr(config)
