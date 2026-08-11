# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Brev environment that adds the opt-in NemoClaw notebook setup."""
from __future__ import annotations

import base64
import logging
import os
import shlex
from pathlib import Path

from envs.brev_env import BrevEnvironment, _run_brev_exec

logger = logging.getLogger(__name__)

_SETUP_KEYS = (
    "NGC_CLI_API_KEY",
    "NGC_API_KEY",
    "NVIDIA_API_KEY",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_BASE_URL",
    "ANTHROPIC_MODEL",
    "OPENAI_API_KEY",
    "COMPATIBLE_API_KEY",
    "LLM_REMOTE_URL",
    "LLM_REMOTE_MODEL",
    "VLM_REMOTE_URL",
    "VLM_REMOTE_MODEL",
    "PR_HEAD_SHA",
    "PR_REPO",
    "GITHUB_RUN_ID",
    "NEMOCLAW_INSTALL_REF",
    "NEMOCLAW_SANDBOX_NAME",
    "NEMOCLAW_GATEWAY_PORT",
    "HARDWARE_PROFILE",
    "HOST_INTERNAL_ALIAS",
    "VSS_ORCHESTRATOR_MCP_PORT",
    "VSS_ORCHESTRATOR_MCP_URL",
    "NEMOCLAW_AGENT_TIMEOUT_SEC",
)


def _bounded_setup_timeout() -> int:
    value = int(os.environ.get("NEMOCLAW_SETUP_TIMEOUT_SEC", "5400"))
    if not 300 <= value <= 7200:
        raise ValueError("NEMOCLAW_SETUP_TIMEOUT_SEC must be 300..7200")
    return value


def _forwarded_nemoclaw_env() -> str:
    values: list[tuple[str, str]] = []
    for key in _SETUP_KEYS:
        value = os.environ.get(key)
        if value is not None:
            values.append((key, value))
    # These empty values are intentional on the one-GPU representative task.
    values.extend(
        [
            ("AGENT_RUNTIME", "openclaw"),
            ("ORCHESTRATOR_ENABLE_HTTPS", "false"),
            ("LLM_DEVICE_ID", ""),
            ("VLM_DEVICE_ID", ""),
        ]
    )
    return "\n".join(
        f"export {key}={shlex.quote(value)}" for key, value in values
    )


def _setup_command(timeout: int) -> str:
    # Reserve 10 minutes for the venv and 10 for readiness, plus five
    # minutes of command/transport headroom inside the total setup budget.
    adapter_timeout = max(300, timeout - 1500)
    return f"""
set -eu
. "$HOME/.eval_env"
cd "$HOME/video-search-and-summarization"
scratch=/tmp/skill-eval/nemoclaw
venv="$scratch/notebook-venv"
rm -rf "$venv"
mkdir -p "$scratch"
python3 -m venv "$venv"
timeout --signal=TERM --kill-after=30 600s \
  "$venv/bin/python" -m pip install --quiet nbformat nbclient ipykernel
"$venv/bin/python" -m ipykernel install --user \
  --name nemoclaw-skill-eval --display-name "NemoClaw skill eval"
export NEMOCLAW_CI_KERNEL=nemoclaw-skill-eval
export NEMOCLAW_SETUP_CELL_TIMEOUT_SEC={adapter_timeout}
timeout --signal=TERM --kill-after=120 {adapter_timeout}s \
  "$venv/bin/python" \
  .github/skill-eval/nemoclaw/notebook_setup_adapter.py \
  --execute \
  --env-out "$scratch/nemoclaw.env" \
  --timeout "$NEMOCLAW_SETUP_CELL_TIMEOUT_SEC"
timeout --signal=TERM --kill-after=30 600s \
  "$venv/bin/python" \
  .github/skill-eval/nemoclaw/readiness.py \
  --env-file "$scratch/nemoclaw.env"
""".strip()


class NemoClawBrevEnvironment(BrevEnvironment):
    """Run normal Brev preparation, then the notebook-derived setup once."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._nemoclaw_ready = False

    async def start(self, force_build: bool) -> None:
        if self._nemoclaw_ready:
            return
        await super().start(force_build)
        if self._instance_name is None:
            raise RuntimeError("NemoClaw setup requires an explicit Brev instance")

        metadata = self._read_task_metadata()
        if metadata.get("runner") != "nemoclaw":
            raise RuntimeError(
                "NemoClawBrevEnvironment requires metadata.runner='nemoclaw'"
            )

        env_block = _forwarded_nemoclaw_env()
        append = (
            "cat >> \"$HOME/.eval_env\" <<'__NEMOCLAW_ENV__'\n"
            f"{env_block}\n"
            "__NEMOCLAW_ENV__"
        )
        written = await _run_brev_exec(
            self._instance_name, append, timeout=30
        )
        if written.return_code != 0:
            raise RuntimeError("Could not forward NemoClaw setup environment")

        timeout = _bounded_setup_timeout()
        logger.info(
            "Running notebook-derived NemoClaw setup on %s (timeout=%ss)",
            self._instance_name,
            timeout,
        )
        result = await _run_brev_exec(
            self._instance_name,
            _setup_command(timeout),
            timeout=timeout + 60,
        )
        if result.return_code != 0:
            detail = (result.stderr or result.stdout or "")[-2000:]
            raise RuntimeError(
                "NemoClaw notebook setup/readiness failed "
                f"(exit {result.return_code}):\n{detail}"
            )
        self._nemoclaw_ready = True
        logger.info("NemoClaw is ready on %s", self._instance_name)

    async def exec(
        self,
        command: str,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        timeout_sec: int | None = None,
        user: str | int | None = None,
    ):
        metadata = self._read_task_metadata()
        is_nemoclaw = metadata.get("runner") == "nemoclaw"
        is_claude_agent = (
            "claude --verbose --output-format=stream-json" in command
        )
        if not is_nemoclaw or not is_claude_agent:
            return await super().exec(
                command,
                cwd=cwd,
                env=env,
                timeout_sec=timeout_sec,
                user=user,
            )

        instructions = [
            value
            for key, value in (env or {}).items()
            if key.startswith("HARBOR_CLAUDE_CODE_INSTRUCTION_")
        ]
        if (
            "headless_runner.py" not in command
            and not any("headless_runner.py" in value for value in instructions)
        ):
            raise RuntimeError(
                "NemoClaw task is missing the expected Harbor launcher instruction"
            )
        prompt_path = (
            Path(self.environment_dir).parent
            / "tests"
            / "nemoclaw_prompt.md"
        )
        if not prompt_path.is_file():
            raise RuntimeError(
                "NemoClaw prompt is unavailable; refusing to run outer Claude"
            )
        prompt_b64 = base64.b64encode(prompt_path.read_bytes()).decode("ascii")
        agent_timeout = int(
            os.environ.get("NEMOCLAW_AGENT_TIMEOUT_SEC", "3300")
        )
        launcher = f"""set -euo pipefail
cd "$HOME/video-search-and-summarization"
mkdir -p /tmp/skill-eval/nemoclaw /logs/agent /logs/artifacts/nemoclaw
printf %s {shlex.quote(prompt_b64)} | base64 -d > /tmp/skill-eval/nemoclaw/current_prompt.md
cat > /logs/agent/claude-code.txt <<'__NEMOCLAW__'
Harbor intentionally bypassed outer Claude for this opt-in NemoClaw task.
The task ran through OpenClaw with repository skills and VSS Orchestrator MCP.
__NEMOCLAW__
python3 .github/skill-eval/nemoclaw/headless_runner.py \
  --prompt-file /tmp/skill-eval/nemoclaw/current_prompt.md \
  --log-dir /logs/artifacts/nemoclaw \
  --agent-log-dir /logs/agent \
  --timeout {agent_timeout}
"""
        clean_env = {
            key: value
            for key, value in (env or {}).items()
            if not key.startswith("HARBOR_CLAUDE_CODE_INSTRUCTION_")
        }
        return await super().exec(
            launcher,
            cwd=cwd,
            env=clean_env,
            timeout_sec=max(timeout_sec or 0, agent_timeout + 180),
            user=user,
        )
