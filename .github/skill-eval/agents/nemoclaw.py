# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Harbor agent adapter for OpenClaw running inside a NemoClaw sandbox."""

from __future__ import annotations

import base64
import os
import shlex

from harbor.agents.installed.base import with_prompt_template
from harbor.agents.installed.openclaw import OpenClaw
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext


def _agent_timeout() -> int:
    value = int(os.environ.get("NEMOCLAW_AGENT_TIMEOUT_SEC", "3300"))
    if not 60 <= value <= 3600:
        raise ValueError("NEMOCLAW_AGENT_TIMEOUT_SEC must be 60..3600")
    return value


class NemoClaw(OpenClaw):
    """Use Harbor's OpenClaw reporting with the notebook-managed runtime."""

    @staticmethod
    def name() -> str:
        return "nemoclaw"

    def version(self) -> str | None:
        return os.environ.get("NEMOCLAW_INSTALL_REF", "v0.0.109")

    async def setup(self, environment: BaseEnvironment) -> None:
        # NemoClaw and OpenClaw are installed and configured by the notebooks
        # in NemoClawBrevEnvironment.start().
        return None

    @with_prompt_template
    async def run(
        self,
        instruction: str,
        environment: BaseEnvironment,
        context: AgentContext,
    ) -> None:
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        (self.logs_dir / "instruction.txt").write_text(
            instruction,
            encoding="utf-8",
        )

        timeout = _agent_timeout()
        prompt = base64.b64encode(instruction.encode("utf-8")).decode("ascii")
        prompt_path = "/tmp/skill-eval/nemoclaw/current_prompt.md"
        command = f"""set -euo pipefail
host_home=$HOME
repo="$host_home/video-search-and-summarization"
export HOME="$host_home/.skill-eval/nemoclaw-home"
export PATH="$HOME/.local/bin:$PATH"
cd "$repo"
mkdir -p /tmp/skill-eval/nemoclaw /logs/agent
printf %s {shlex.quote(prompt)} | base64 -d > {shlex.quote(prompt_path)}
python3 .github/skill-eval/nemoclaw/headless_runner.py \
  --prompt-file {shlex.quote(prompt_path)} \
  --agent-log-dir /logs/agent \
  --timeout {timeout}
"""
        await self.exec_as_agent(
            environment,
            command,
            timeout_sec=timeout + 180,
        )
