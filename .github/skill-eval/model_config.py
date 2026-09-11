#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Resolve the model route used by the agent under evaluation."""

from __future__ import annotations

import os
import urllib.parse
from collections.abc import Mapping
from dataclasses import dataclass, field

RUNTIMES = ("claude-code", "codex", "nemoclaw")
REQUESTED_PROVIDERS = ("default", "nvidia-inference", "nvidia-build", "custom")


def _first(*values: object) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _validate_endpoint_url(endpoint_url: str) -> None:
    if not endpoint_url:
        return
    parsed = urllib.parse.urlsplit(endpoint_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("SKILLS_EVAL_ENDPOINT_URL must be an HTTP(S) URL with a host")


@dataclass(frozen=True)
class SkillEvalModelConfig:
    runtime: str
    provider: str
    model: str
    endpoint_url: str
    api_key: str = field(repr=False)

    @property
    def nemoclaw_provider(self) -> str:
        """Translate the workflow vocabulary to Build Vision AI's contract."""
        if self.provider == "nvidia-build":
            return "build"
        if self.provider == "nvidia-inference":
            return "custom"
        return self.provider


def resolve_model_config(
    environment: Mapping[str, str] | None = None,
) -> SkillEvalModelConfig:
    """Resolve manual inputs while preserving the current CI defaults."""

    env = environment if environment is not None else os.environ
    runtime = _first(
        env.get("SKILLS_EVAL_HARNESS"),
        env.get("EVAL_AGENT"),
        "claude-code",
    )
    requested_provider = _first(env.get("SKILLS_EVAL_PROVIDER"), "default")
    requested_model = _first(env.get("SKILLS_EVAL_MODEL"))
    requested_endpoint = _first(env.get("SKILLS_EVAL_ENDPOINT_URL"))

    if runtime not in RUNTIMES:
        raise ValueError(
            f"unsupported evaluated-agent harness {runtime!r}; "
            f"expected {' | '.join(RUNTIMES)}"
        )
    if requested_provider not in REQUESTED_PROVIDERS:
        raise ValueError(
            f"unsupported SKILLS_EVAL_PROVIDER {requested_provider!r}; "
            f"expected {' | '.join(REQUESTED_PROVIDERS)}"
        )
    if requested_endpoint and not requested_model:
        raise ValueError(
            "SKILLS_EVAL_MODEL is required when SKILLS_EVAL_ENDPOINT_URL is set"
        )
    if requested_provider != "default" and not requested_model:
        raise ValueError(
            f"SKILLS_EVAL_MODEL is required for provider={requested_provider}"
        )

    if runtime in {"claude-code", "codex"}:
        if requested_provider == "nvidia-build":
            raise ValueError(
                "SKILLS_EVAL_PROVIDER=nvidia-build is only supported by nemoclaw"
            )
        if _first(env.get("SKILLS_EVAL_API_KEY")):
            raise ValueError(
                "SKILLS_EVAL_API_KEY is not supported by claude-code/codex; "
                "the evaluated agent uses the runner-managed ANTHROPIC_API_KEY"
            )
        provider = (
            "nvidia-inference"
            if requested_provider == "default"
            else requested_provider
        )
        model = requested_model or _first(
            env.get("CODEX_MODEL") if runtime == "codex" else "",
            env.get("ANTHROPIC_MODEL"),
        )
        endpoint_url = requested_endpoint or _first(env.get("ANTHROPIC_BASE_URL"))
        api_key = _first(env.get("ANTHROPIC_API_KEY"))
        if requested_provider == "custom" and not requested_endpoint:
            raise ValueError("SKILLS_EVAL_ENDPOINT_URL is required for provider=custom")
    else:
        inherited_provider = _first(env.get("NEMOCLAW_PROVIDER"), "custom")
        provider = (
            inherited_provider
            if requested_provider == "default"
            else requested_provider
        )
        model = requested_model or _first(
            env.get("NEMOCLAW_MODEL"),
            env.get("ANTHROPIC_MODEL"),
            env.get("LLM_REMOTE_MODEL"),
        )
        if provider == "nvidia-build" or provider == "build":
            endpoint_url = ""
            api_key = _first(env.get("NVIDIA_API_KEY"))
            credential_name = "NVIDIA_API_KEY"
        elif provider in {"install-vllm", "ollama", "nim-local"}:
            endpoint_url = ""
            api_key = "local-provider"
            credential_name = ""
        else:
            endpoint_url = requested_endpoint or _first(
                env.get("NEMOCLAW_ENDPOINT_URL"),
                env.get("ANTHROPIC_BASE_URL"),
                env.get("LLM_REMOTE_URL"),
            )
            api_key = _first(
                env.get("SKILLS_EVAL_API_KEY"),
                env.get("COMPATIBLE_API_KEY"),
                env.get("ANTHROPIC_API_KEY"),
            )
            credential_name = (
                "SKILLS_EVAL_API_KEY, COMPATIBLE_API_KEY, or ANTHROPIC_API_KEY"
            )
            if requested_provider == "custom" and not requested_endpoint:
                raise ValueError(
                    "SKILLS_EVAL_ENDPOINT_URL is required for provider=custom"
                )

    if not model:
        raise ValueError(
            "SKILLS_EVAL_MODEL is required because the selected harness has "
            "no configured default model"
        )
    endpoint_optional = {
        "nvidia-build",
        "build",
        "install-vllm",
        "ollama",
        "nim-local",
    }
    if provider not in endpoint_optional and not endpoint_url:
        raise ValueError(
            "SKILLS_EVAL_ENDPOINT_URL is required because the selected route "
            "has no configured default endpoint"
        )
    if not api_key:
        expected = credential_name if runtime == "nemoclaw" else "ANTHROPIC_API_KEY"
        raise ValueError(f"no API key is configured; set {expected} on the runner")

    _validate_endpoint_url(endpoint_url)

    return SkillEvalModelConfig(
        runtime=runtime,
        provider=provider,
        model=model,
        endpoint_url=endpoint_url.rstrip("/"),
        api_key=api_key,
    )


def main() -> int:
    config = resolve_model_config()
    endpoint = "configured" if config.endpoint_url else "provider-managed"
    print(
        f"skill-eval model: runtime={config.runtime} "
        f"provider={config.provider} model={config.model} endpoint={endpoint}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
