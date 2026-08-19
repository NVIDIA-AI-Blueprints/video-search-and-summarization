#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Resolve the model used by the agent under evaluation."""

from __future__ import annotations

import os
from collections.abc import MutableMapping
from dataclasses import dataclass, field

RUNTIMES = ("claude-code", "codex", "nemoclaw")
PROVIDERS = (
    "nvidia-inference",
    "nvidia-build",
)

_COMPATIBLE_PROVIDERS = {
    "claude-code": {"nvidia-inference"},
    "codex": {"nvidia-inference"},
    "nemoclaw": {"nvidia-inference", "nvidia-build"},
}


def _first(*values: object) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


@dataclass(frozen=True)
class SkillEvalModelConfig:
    runtime: str
    provider: str
    model: str
    endpoint_url: str
    api_key: str = field(repr=False)

    @property
    def notebook_provider(self) -> str:
        return "build" if self.provider == "nvidia-build" else "custom"


def resolve_model_config(
    environment: MutableMapping[str, str] | None = None,
) -> SkillEvalModelConfig:
    """Resolve standard inputs while retaining the current CI defaults."""

    env = environment if environment is not None else os.environ
    runtime = _first(env.get("EVAL_AGENT"), "claude-code")
    provider = _first(env.get("SKILLS_EVAL_PROVIDER"), "nvidia-inference")

    if runtime not in RUNTIMES:
        expected = " | ".join(RUNTIMES)
        raise ValueError(f"unsupported EVAL_AGENT {runtime!r}; expected {expected}")
    if provider not in PROVIDERS:
        expected = " | ".join(PROVIDERS)
        raise ValueError(
            f"unsupported SKILLS_EVAL_PROVIDER {provider!r}; expected {expected}"
        )
    if provider not in _COMPATIBLE_PROVIDERS[runtime]:
        raise ValueError(
            f"SKILLS_EVAL_PROVIDER={provider!r} is not compatible with "
            f"EVAL_AGENT={runtime!r}"
        )

    model = _first(env.get("SKILLS_EVAL_MODEL"))
    if not model and provider == "nvidia-inference":
        if runtime == "codex":
            model = _first(env.get("CODEX_MODEL"))
        elif runtime == "nemoclaw":
            model = _first(
                env.get("NEMOCLAW_MODEL"),
                env.get("ANTHROPIC_MODEL"),
                env.get("LLM_REMOTE_MODEL"),
            )
        else:
            model = _first(env.get("ANTHROPIC_MODEL"))
    if not model:
        raise ValueError(
            "SKILLS_EVAL_MODEL is required unless nvidia-inference can use "
            "the runtime's existing model"
        )

    if provider == "nvidia-inference":
        endpoint_url = _first(
            env.get("SKILLS_EVAL_ENDPOINT_URL"),
            env.get("NEMOCLAW_ENDPOINT_URL") if runtime == "nemoclaw" else "",
            env.get("ANTHROPIC_BASE_URL"),
            env.get("LLM_REMOTE_URL") if runtime == "nemoclaw" else "",
        )
        api_key = _first(
            env.get("SKILLS_EVAL_API_KEY"),
            env.get("COMPATIBLE_API_KEY") if runtime == "nemoclaw" else "",
            env.get("ANTHROPIC_API_KEY"),
            env.get("OPENAI_API_KEY") if runtime == "nemoclaw" else "",
            env.get("NVIDIA_API_KEY") if runtime == "nemoclaw" else "",
        )
    elif provider == "nvidia-build":
        endpoint_url = ""
        api_key = _first(
            env.get("SKILLS_EVAL_API_KEY"),
            env.get("NVIDIA_API_KEY"),
        )
    if provider != "nvidia-build" and not endpoint_url:
        raise ValueError(f"SKILLS_EVAL_ENDPOINT_URL is required for {provider}")
    if not api_key:
        raise ValueError(f"no API key is configured for {provider}")

    return SkillEvalModelConfig(
        runtime=runtime,
        provider=provider,
        model=model,
        endpoint_url=endpoint_url.rstrip("/"),
        api_key=api_key,
    )


def apply_model_config(
    environment: MutableMapping[str, str] | None = None,
) -> SkillEvalModelConfig:
    """Resolve and export the standardized agent-model variables."""

    env = environment if environment is not None else os.environ
    config = resolve_model_config(env)
    env["SKILLS_EVAL_PROVIDER"] = config.provider
    env["SKILLS_EVAL_MODEL"] = config.model
    env["SKILLS_EVAL_ENDPOINT_URL"] = config.endpoint_url
    env["SKILLS_EVAL_API_KEY"] = config.api_key
    return config


def main() -> int:
    config = resolve_model_config(os.environ)
    print(
        f"skill-eval model: runtime={config.runtime} "
        f"provider={config.provider} model={config.model}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
