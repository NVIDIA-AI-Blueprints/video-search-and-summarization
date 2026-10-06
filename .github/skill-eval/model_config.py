#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Resolve the independent coding and operational skill-eval routes."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

RUNTIMES = ("claude-code", "codex", "nemoclaw")
CODING_RUNTIMES = ("claude-code", "codex")
NVIDIA_INFERENCE_PROVIDER = "hosted-nvidia-inference"
NVIDIA_INFERENCE_SOURCE_URL = "https://inference.nvidia.com/"
NVIDIA_INFERENCE_API_BASE_URL = "https://inference-api.nvidia.com/v1"
LOCAL_NIM_PROVIDER = "local-nim"
ROLES = ("coding", "operational")
VSS_SHARED_LOCAL_MODELS = {"nvidia/nemotron-3.5-lightning-30b-a3b": "nemotron-3.5-lightning-30b-a3b"}


def share_local_llm_with_vss(environment: Mapping[str, str]) -> bool:
    value = (environment.get("SKILLS_EVAL_SHARE_LOCAL_LLM_WITH_VSS") or "false").lower()
    if value not in {"true", "false"}:
        raise ValueError("SKILLS_EVAL_SHARE_LOCAL_LLM_WITH_VSS must be true or false")
    return value == "true"


def validate_shared_local_llm(routes: SkillEvalModelRoutes, environment: Mapping[str, str]) -> None:
    if not share_local_llm_with_vss(environment):
        return
    if routes.coding.provider != NVIDIA_INFERENCE_PROVIDER:
        raise ValueError("sharing VSS's LLM requires hosted coding; a local coding NIM starts before VSS")
    if routes.operational.runtime != "nemoclaw" or routes.operational.provider != LOCAL_NIM_PROVIDER:
        raise ValueError("sharing VSS's LLM requires NemoClaw with operational_deployment=local-nim")
    if routes.operational.model not in VSS_SHARED_LOCAL_MODELS:
        raise ValueError(
            f"VSS cannot deploy {routes.operational.model!r} as a local LLM; "
            f"supported: {', '.join(VSS_SHARED_LOCAL_MODELS)}"
        )
    spec_path = environment.get("EVAL_SPEC_PATH")
    if spec_path:
        repo = Path(__file__).resolve().parents[2]
        spec = json.loads((repo / spec_path).read_text())
        if not spec.get("shared_local_llm"):
            raise ValueError(
                f"{spec_path} does not support shared local VSS LLM; "
                "its deployment contract may require remote inference"
            )
DEFAULT_CODEX_MODEL = "azure/openai/gpt-6.1-sol"
DEFAULT_NEMOCLAW_MODEL = "aws/anthropic/bedrock-claude-opus-5-5"


def _first(*values: object) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


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
        "codex"
        if role == "coding"
        else _first(env.get("EVAL_AGENT"), "nemoclaw")
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
    if runtime in {"claude-code", "codex"}:
        if runtime == "codex":
            model = requested_model or _first(env.get("CODEX_MODEL"), DEFAULT_CODEX_MODEL)
        else:
            model = requested_model or _first(env.get("ANTHROPIC_MODEL"))
        endpoint_url = NVIDIA_INFERENCE_API_BASE_URL
        api_key = route_api_key or _first(env.get("ANTHROPIC_API_KEY"))
        credential_name = f"{prefix}_API_KEY or ANTHROPIC_API_KEY"
    else:
        model = requested_model or _first(
            env.get("NEMOCLAW_MODEL"),
            DEFAULT_NEMOCLAW_MODEL,
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
    routes = SkillEvalModelRoutes(
        coding=resolve_model_config(env, role="coding"),
        operational=resolve_model_config(env, role="operational"),
    )
    validate_shared_local_llm(routes, env)
    return routes


def main() -> int:
    routes = resolve_model_routes()
    if share_local_llm_with_vss(os.environ):
        print("skill-eval operational: share-local-llm-with-vss=true; VSS owns the NIM")
    for config in (routes.coding, routes.operational):
        print(
            f"skill-eval {config.role}: runtime={config.runtime} "
            f"provider={config.provider} model={config.model} endpoint=fixed"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
