#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Resolve the independent coding and operational skill-eval routes."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field

RUNTIMES = ("claude-code", "codex", "nemoclaw")
CODING_RUNTIMES = ("claude-code", "codex")
NVIDIA_INFERENCE_PROVIDER = "hosted-nvidia-inference"
NVIDIA_INFERENCE_SOURCE_URL = "https://inference.nvidia.com/"
NVIDIA_INFERENCE_API_BASE_URL = "https://inference-api.nvidia.com/v1"
LOCAL_NIM_PROVIDER = "local-nim"
SWITCHYARD_ROUTE = "switchyard/stage"
SWITCHYARD_FRONTIER_MODEL = "azure/anthropic/claude-opus-5"
ROLES = ("coding", "operational")


def _first(*values: object) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def switchyard_enabled(env: Mapping[str, str]) -> bool:
    value = _first(env.get("SKILLS_EVAL_SWITCHYARD"), "false").lower()
    if value not in {"true", "false"}:
        raise ValueError("SKILLS_EVAL_SWITCHYARD must be true or false")
    return value == "true"


@dataclass(frozen=True)
class SkillEvalModelConfig:
    role: str
    runtime: str
    provider: str
    model: str
    endpoint_url: str
    api_key: str = field(repr=False)

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
    requested_model = _first(env.get(f"{prefix}_MODEL"))
    deployment = _first(env.get(f"{prefix}_DEPLOYMENT"), NVIDIA_INFERENCE_PROVIDER)
    if deployment == "nvidia-inference":
        # Preserve older direct run_leg callers after the workflow choice rename.
        deployment = NVIDIA_INFERENCE_PROVIDER
    if deployment not in {NVIDIA_INFERENCE_PROVIDER, LOCAL_NIM_PROVIDER}:
        raise ValueError(f"unsupported {role} deployment {deployment!r}")
    route_api_key = _first(env.get(f"{prefix}_API_KEY"))

    allowed_runtimes = CODING_RUNTIMES if role == "coding" else RUNTIMES
    if runtime not in allowed_runtimes:
        raise ValueError(
            f"unsupported {role} harness {runtime!r}; "
            f"expected {' | '.join(allowed_runtimes)}"
        )
    route_switchyard = role == "operational" and switchyard_enabled(env)
    if route_switchyard:
        if runtime != "nemoclaw":
            raise ValueError("Switchyard requires the NemoClaw operational harness")
        if not requested_model:
            raise ValueError("Switchyard requires SKILLS_EVAL_OPERATIONAL_MODEL")
        frontier_model = _first(env.get("SKILLS_EVAL_SWITCHYARD_FRONTIER_MODEL"), SWITCHYARD_FRONTIER_MODEL)
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:/-]*", frontier_model):
            raise ValueError("Switchyard frontier model must be a valid NVIDIA Inference model ID")
        if not _first(env.get("SKILLS_EVAL_SWITCHYARD_FRONTIER_API_KEY"), route_api_key, env.get("ANTHROPIC_API_KEY")):
            raise ValueError("Switchyard requires a hosted frontier API key")
    if runtime in {"claude-code", "codex"}:
        if runtime == "codex":
            model = requested_model or _first(env.get("CODEX_MODEL"))
            if not model:
                raise ValueError(f"{prefix}_MODEL or CODEX_MODEL is required for codex")
        else:
            model = requested_model or _first(env.get("ANTHROPIC_MODEL"))
        endpoint_url = NVIDIA_INFERENCE_API_BASE_URL
        api_key = route_api_key or _first(env.get("ANTHROPIC_API_KEY"))
        credential_name = f"{prefix}_API_KEY or ANTHROPIC_API_KEY"
    else:
        model = requested_model or _first(
            env.get("NEMOCLAW_MODEL"),
            env.get("ANTHROPIC_MODEL"),
            env.get("LLM_REMOTE_MODEL"),
        )
        endpoint_url = NVIDIA_INFERENCE_API_BASE_URL
        api_key = route_api_key or _first(
            env.get("COMPATIBLE_API_KEY"),
            env.get("ANTHROPIC_API_KEY"),
        )
        credential_name = (
            f"{prefix}_API_KEY, COMPATIBLE_API_KEY, or ANTHROPIC_API_KEY"
        )

    if not model:
        raise ValueError(
            f"{prefix}_MODEL is required because the selected harness has "
            "no configured default model"
        )
    if deployment == LOCAL_NIM_PROVIDER:
        from local_nim import validate_model_id
        try:
            validate_model_id(model)
        except ValueError as exc:
            raise ValueError(f"{prefix}_MODEL: {exc}") from exc
        if not _first(env.get("NGC_CLI_API_KEY"), env.get("NGC_API_KEY")):
            raise ValueError("local-nim requires NGC_CLI_API_KEY or NGC_API_KEY")
        # Filled with a per-leg credential by run_leg; never send a hosted key.
        endpoint_url = "http://127.0.0.1:18400/v1"
        api_key = "local-nim"
    if not api_key:
        raise ValueError(
            f"no API key is configured; set {credential_name} on the runner"
        )

    return SkillEvalModelConfig(
        role=role,
        runtime=runtime,
        provider=deployment,
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
        print(
            f"skill-eval {config.role}: runtime={config.runtime} "
            f"provider={config.provider} model={config.model} endpoint=fixed"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
