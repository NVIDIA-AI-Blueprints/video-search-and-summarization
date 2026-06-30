# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Codex agent variant that keeps the full provider-prefixed model id.

Harbor's stock codex agent computes the wire model via
`self.model_name.split("/")[-1]`, which drops the provider prefix
(`openai/openai/gpt-5-codex` -> `gpt-5-codex`). The NVIDIA inference gateway
(LiteLLM) registers codex models under their full id and 401s on the bare
leaf, so we keep the full id on the wire.

Run via Harbor's `-a` flag, which accepts a `module:Class` import path
(`.github/skill-eval` is already on PYTHONPATH, same as envs.brev_env):

    uvx harbor run \
      --environment-import-path envs.brev_env:BrevEnvironment \
      -a agents.nv_codex:NvCodex \
      -m openai/openai/gpt-5-codex \
      --ak api_base=https://inference-api.nvidia.com/v1 \
      ...

harbor's codex agent authenticates via OPENAI_API_KEY (read from the env).
run_leg.py reuses the shared NVIDIA inference key — it injects
ANTHROPIC_API_KEY's value as OPENAI_API_KEY into the subprocess env (and the
endpoint as `--ak api_base=${ANTHROPIC_BASE_URL}`), so no separate
OPENAI_API_KEY / OPENAI_BASE_URL needs to be configured.
"""
from harbor.agents.installed.codex import Codex


class _WholeModel(str):
    """Refuse only the *unbounded* split on '/'. Codex computes --model via
    model_name.split('/')[-1] (maxsplit=-1); intercept just that so the full
    id survives. Bounded splits (BaseAgent's provider parse, split('/', 1))
    stay normal, or `provider, name = ...` would fail to unpack."""

    def split(self, sep=None, maxsplit=-1):
        if sep == "/" and maxsplit == -1:
            return [str(self)]
        return super().split(sep, maxsplit)


class NvCodex(Codex):
    @property
    def model_name(self):
        return self._nv_model_name

    @model_name.setter
    def model_name(self, value):
        self._nv_model_name = _WholeModel(value) if isinstance(value, str) else value
