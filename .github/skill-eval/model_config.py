#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Resolve the independent coding and operational skill-eval routes."""

from __future__ import annotations

import os
import urllib.parse
from collections.abc import Mapping
from dataclasses import dataclass, field

RUNTIMES = ("claude-code", "codex", "nemoclaw")
CODING_RUNTIMES = ("claude-code", "codex")
REQUESTED_PROVIDERS = ("default", "nvidia-inference", "nvidia-build", "custom")
ENDPOINT_MANAGED_PROVIDERS = {
    "nvidia-build",
    "build",
    "install-vllm",
    "ollama",
    "nim-local",
}
ROLES = ("coding", "operational")


def _first(*values: object) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _validate_endpoint_url(endpoint_url: str, variable: str) -> None:
    if not endpoint_url:
        return
    parsed = urllib.parse.urlsplit(endpoint_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError(f"{variable} must be an HTTP(S) URL with a host")


@dataclass(frozen=True)
class SkillEvalModelConfig:
    role: str
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


@dataclass(frozen=True)
class SkillEvalModelRoutes:
    coding: SkillEvalModelConfig
    operational: SkillEvalModelConfig


def resolve_model_config(
    environment: Mapping[str, str] | None = None,
    *,
    role: str = "operational",
) -> SkillEvalModelConfig:
    """Resolve one role without inheriting overrides from the other role."""

    if role not in ROLES:
        raise ValueError(f"unsupported skill-eval role {role!r}")
    env = environment if environment is not None else os.environ
    prefix = f"SKILLS_EVAL_{role.upper()}"
    runtime_default = (
        "claude-code"
        if role == "coding"
        else _first(env.get("EVAL_AGENT"), "claude-code")
    )
    runtime = _first(env.get(f"{prefix}_HARNESS"), runtime_default)
    requested_provider = _first(env.get(f"{prefix}_PROVIDER"), "default")
    requested_model = _first(env.get(f"{prefix}_MODEL"))
    requested_endpoint = _first(env.get(f"{prefix}_ENDPOINT_URL"))
    route_api_key = _first(env.get(f"{prefix}_API_KEY"))

    allowed_runtimes = CODING_RUNTIMES if role == "coding" else RUNTIMES
    if runtime not in allowed_runtimes:
        raise ValueError(
            f"unsupported {role} harness {runtime!r}; "
            f"expected {' | '.join(allowed_runtimes)}"
        )
    if requested_provider not in REQUESTED_PROVIDERS:
        raise ValueError(
            f"unsupported {prefix}_PROVIDER {requested_provider!r}; "
            f"expected {' | '.join(REQUESTED_PROVIDERS)}"
        )
    if requested_endpoint and not requested_model:
        raise ValueError(
            f"{prefix}_MODEL is required when {prefix}_ENDPOINT_URL is set"
        )
    if requested_provider != "default" and not requested_model:
        raise ValueError(
            f"{prefix}_MODEL is required for provider={requested_provider}"
        )

    if runtime in {"claude-code", "codex"}:
        if requested_provider == "nvidia-build":
            raise ValueError(
                f"{prefix}_PROVIDER=nvidia-build is only supported by nemoclaw"
            )
        provider = (
            "nvidia-inference"
            if requested_provider == "default"
            else requested_provider
        )
        if runtime == "codex":
            model = requested_model or _first(env.get("CODEX_MODEL"))
            if not model:
                raise ValueError(f"{prefix}_MODEL or CODEX_MODEL is required for codex")
        else:
            model = requested_model or _first(env.get("ANTHROPIC_MODEL"))
        endpoint_url = requested_endpoint or _first(env.get("ANTHROPIC_BASE_URL"))
        api_key = route_api_key or _first(env.get("ANTHROPIC_API_KEY"))
        credential_name = f"{prefix}_API_KEY or ANTHROPIC_API_KEY"
        if requested_provider == "custom" and not requested_endpoint:
            raise ValueError(f"{prefix}_ENDPOINT_URL is required for provider=custom")
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
        if requested_endpoint and provider in ENDPOINT_MANAGED_PROVIDERS:
            raise ValueError(
                f"{prefix}_ENDPOINT_URL is incompatible with "
                f"provider={provider}; that provider manages its own endpoint"
            )
        if provider in {"nvidia-build", "build"}:
            endpoint_url = ""
            api_key = route_api_key or _first(env.get("NVIDIA_API_KEY"))
            credential_name = f"{prefix}_API_KEY or NVIDIA_API_KEY"
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
            api_key = route_api_key or _first(
                env.get("COMPATIBLE_API_KEY"),
                env.get("ANTHROPIC_API_KEY"),
            )
            credential_name = (
                f"{prefix}_API_KEY, COMPATIBLE_API_KEY, or ANTHROPIC_API_KEY"
            )
            if requested_provider == "custom" and not requested_endpoint:
                raise ValueError(
                    f"{prefix}_ENDPOINT_URL is required for provider=custom"
                )

    if not model:
        raise ValueError(
            f"{prefix}_MODEL is required because the selected harness has "
            "no configured default model"
        )
    if provider not in ENDPOINT_MANAGED_PROVIDERS and not endpoint_url:
        raise ValueError(
            f"{prefix}_ENDPOINT_URL is required because the selected route "
            "has no configured default endpoint"
        )
    if not api_key:
        raise ValueError(
            f"no API key is configured; set {credential_name} on the runner"
        )

    _validate_endpoint_url(endpoint_url, f"{prefix}_ENDPOINT_URL")

    return SkillEvalModelConfig(
        role=role,
        runtime=runtime,
        provider=provider,
        model=model,
        endpoint_url=endpoint_url.rstrip("/"),
        api_key=api_key,
    )


def resolve_model_routes(
    environment: Mapping[str, str] | None = None,
) -> SkillEvalModelRoutes:
    """Resolve coding and operational routes from independent input prefixes."""

    env = environment if environment is not None else os.environ
    return SkillEvalModelRoutes(
        coding=resolve_model_config(env, role="coding"),
        operational=resolve_model_config(env, role="operational"),
    )


def main() -> int:
    routes = resolve_model_routes()
    for config in (routes.coding, routes.operational):
        endpoint = "configured" if config.endpoint_url else "provider-managed"
        print(
            f"skill-eval {config.role}: runtime={config.runtime} "
            f"provider={config.provider} model={config.model} endpoint={endpoint}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
