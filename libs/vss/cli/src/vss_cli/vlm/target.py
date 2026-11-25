# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Resolve already-configured runtime targets without exposing secrets in repr."""

from dataclasses import dataclass

from vss_cli import config as config_mod


@dataclass(frozen=True, repr=False)
class VlmTarget:
    endpoint: str
    model: str
    backend: str
    api_key: str | None


def default_model(deployment: config_mod.Deployment) -> str:
    """The one model the VLM endpoint reports serving, or a ConfigError.

    An endpoint listing several (Inference Hub lists its whole catalog) has no
    defensible default, so the caller chooses rather than getting the first.
    """
    service = deployment.services.get("rt_vlm")
    models = service.models if service else []
    choose = f"Pass --model, run `vss configure vlm --model <id>`, or export {config_mod.VLM_ENV['model']}."
    if len(models) == 1:
        return models[0]
    if models:
        shown = ", ".join(models[:10]) + (", ..." if len(models) > 10 else "")
        raise config_mod.ConfigError(
            f"the VLM endpoint at {deployment.base_url} lists {len(models)} models ({shown}). {choose}"
        )
    raise config_mod.ConfigError(
        f"deployment at {deployment.base_url} reports no VLM model, so --model cannot be defaulted. "
        f"{choose} Or re-run `vss configure --base-url {deployment.base_url}`."
    )


def resolve_vlm_target(
    deployment: config_mod.Deployment, model: str | None = None, policy: config_mod.VlmConfig | None = None
) -> VlmTarget:
    """Resolve a model input after CLI policy has already been applied."""
    policy = policy or config_mod.effective_vlm_config(deployment.vlm)
    return VlmTarget(
        deployment.endpoint("rt_vlm"),
        model or default_model(deployment),
        policy.backend if policy else "rt_vlm",
        config_mod.vlm_api_key(),
    )
