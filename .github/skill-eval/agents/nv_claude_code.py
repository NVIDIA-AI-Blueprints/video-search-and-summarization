# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Keep Harbor's per-agent Claude endpoint separate from the verifier route.

Harbor 0.20.0 reads ANTHROPIC_API_KEY through ``_get_env`` (which honors
``--ae``), but reads ANTHROPIC_BASE_URL directly from ``os.environ``. Scope
the selected agent route to ``run`` so the verifier still uses the
coordinator's own endpoint after the agent exits.
"""

import os

from harbor.agents.installed.claude_code import ClaudeCode


class NvClaudeCode(ClaudeCode):
    async def run(self, instruction, environment, context):
        route_base = self._get_env("ANTHROPIC_BASE_URL")
        previous = os.environ.get("ANTHROPIC_BASE_URL")
        if route_base:
            os.environ["ANTHROPIC_BASE_URL"] = route_base
        try:
            return await super().run(instruction, environment, context)
        finally:
            if previous is None:
                os.environ.pop("ANTHROPIC_BASE_URL", None)
            else:
                os.environ["ANTHROPIC_BASE_URL"] = previous
