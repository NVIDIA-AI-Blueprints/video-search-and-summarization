# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Custom Harbor codex agent that sends the FULL model id on the wire.

Harbor's stock codex agent (`harbor.agents.installed.codex.Codex`) sends
`self.model_name.split("/")[-1]` to the model endpoint — i.e. it drops the
provider prefix and sends only the last path segment (`gpt-5-codex`). The
NVIDIA inference gateway is a LiteLLM proxy that registers codex models under
their FULL id (`openai/openai/gpt-5-codex`) and rejects the bare leaf with
HTTP 401 ("key can only access models=['default-models']. Tried to access
gpt-5-codex"). Verified out-of-band:

    curl .../v1/responses -d '{"model":"openai/openai/gpt-5-codex",...}'  -> 200
    curl .../v1/responses -d '{"model":"gpt-5-codex",...}'                -> 401

This subclass keeps the model id whole by wrapping `model_name` in a `str`
subclass whose `split("/")` returns the value unsplit, so harbor's
`model_name.split("/")[-1]` yields the full id again. Everything else
(install, auth.json / OPENAI_API_KEY, OPENAI_BASE_URL -> config.toml,
trajectory parsing) is inherited unchanged — this is intentionally the
smallest possible override so it does not depend on harbor's large `run()`
body staying byte-for-byte stable across versions.

Wired in by run_leg.py via Harbor's custom-agent mechanism:
    uvx harbor run \
      --agent-import-path agents.codex_full_model:FullModelCodex \
      --model openai/openai/gpt-5-codex
(`.github/skill-eval` is already on PYTHONPATH, same as envs.brev_env.)

If a future harbor changes the stripping mechanism (e.g. to
`rsplit("/", 1)` or `PurePosixPath(...).name`), revisit `_WholeModelName`.
"""
from __future__ import annotations

from harbor.agents.installed.codex import Codex


class _WholeModelName(str):
    """A `str` that refuses to be split on '/', so an 'a/b/c' id stays whole.

    Harbor strips the provider prefix with `model_name.split("/")[-1]`; for
    `sep == "/"` we return the whole string as the sole element so its `[-1]`
    is the full id. Every other separator behaves like a normal `str`, and in
    all other contexts the value is just its full string.
    """

    def split(self, sep=None, maxsplit=-1):  # type: ignore[override]
        if sep == "/":
            return [str(self)]
        return str.split(self, sep, maxsplit)

    def rsplit(self, sep=None, maxsplit=-1):  # type: ignore[override]
        if sep == "/":
            return [str(self)]
        return str.rsplit(self, sep, maxsplit)


class FullModelCodex(Codex):
    """Harbor codex agent that preserves the full provider-prefixed model id."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # `model_name` is a plain attribute on the base agent; re-wrap it so
        # harbor's internal `.split("/")[-1]` keeps the full id on the wire.
        if getattr(self, "model_name", None):
            self.model_name = _WholeModelName(self.model_name)
