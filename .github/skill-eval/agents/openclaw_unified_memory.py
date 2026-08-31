# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""OpenClaw driver that runs a benchmark group as one isolated conversation."""

from __future__ import annotations

import json
import re
import shlex
from typing import Any, ClassVar, override

from harbor.agents.installed.openclaw import OpenClaw, _nvm22
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext

GROUP_PREFIX = "<!-- unified-memory-group\n"
GROUP_SUFFIX = "\n-->"
RESET_OPENCLAW_RUNTIME_CLI = (
    "rm -f ~/.openclaw/openclaw.json && rm -rf ~/.openclaw/state"
)
PREDICTION_JQ_FILTER = r"""
(.payloads[0].text | select(type == "string")) as $response
|
{
  case_id: $case_id,
  answer: (try ($response | fromjson) catch null)
}
"""


def _group_envelope(instruction: str) -> dict[str, Any] | None:
    start = instruction.find(GROUP_PREFIX)
    if start < 0:
        return None
    start += len(GROUP_PREFIX)
    end = instruction.find(GROUP_SUFFIX, start)
    if end < 0:
        raise ValueError("unterminated unified-memory group envelope")
    envelope = json.loads(instruction[start:end])
    if envelope.get("kind") != "unified-memory-group":
        raise ValueError("invalid unified-memory group envelope")
    turns = envelope.get("turns")
    if not isinstance(turns, list) or len(turns) != 4:
        raise ValueError("a benchmark group must contain exactly four turns")
    return envelope


def _prediction_extractor_command(case_id: str) -> str:
    return (
        "jq -ce "
        f"--arg case_id {shlex.quote(case_id)} "
        f"{shlex.quote(PREDICTION_JQ_FILTER)}"
    )


def _openclaw_setup_commands(setup_cli: str) -> tuple[str, str]:
    """Return a clean, version-compatible setup sequence for a warm worker."""
    return RESET_OPENCLAW_RUNTIME_CLI, _nvm22(setup_cli)


class UnifiedMemoryOpenClaw(OpenClaw):
    """Minimal extension: four prompts share one unique ``--session-key``."""

    _DEFAULT_CONFIG: ClassVar[dict[str, Any]] = {
        "agents": {"defaults": {"workspace": "~/.openclaw/workspace"}}
    }

    def _build_register_skills_command(self) -> str | None:
        command = super()._build_register_skills_command()
        if not command:
            return None
        owned = (
            "benchmark-unified-memory",
            "vss-deploy-profile",
            "vss-manage-video-io-storage",
            "vss-ask-video",
        )
        cleanup = " ".join(f"~/.openclaw/skills/{shlex.quote(name)}" for name in owned)
        return f"rm -rf {cleanup} && {command}"

    async def _prepare(
        self, environment: BaseEnvironment, instruction: str
    ) -> dict[str, str]:
        if not self.model_name or "/" not in self.model_name:
            raise ValueError("Model name must be in the format provider/model_name")
        provider, _ = self.model_name.split("/", 1)
        self._validate_provider(provider)
        env = {
            key: value
            for key in self._provider_env_keys(provider)
            if (value := self._get_env(key))
        }
        upload_path = self.logs_dir / self._UPLOAD_CONFIG_FILENAME
        upload_path.write_text(
            json.dumps(self._build_full_openclaw_config(), indent=2) + "\n",
            encoding="utf-8",
        )
        (self.logs_dir / "instruction.txt").write_text(instruction, encoding="utf-8")
        await environment.upload_file(
            upload_path,
            f"{self._CONTAINER_LOGS_AGENT}/{self._UPLOAD_CONFIG_FILENAME}",
        )
        for command in _openclaw_setup_commands(self._SETUP_CLI):
            await self.exec_as_agent(environment, command=command, env=env)
        await self.exec_as_agent(
            environment,
            command=(
                "mkdir -p ~/.openclaw && cp "
                f"{shlex.quote(f'{self._CONTAINER_LOGS_AGENT}/{self._UPLOAD_CONFIG_FILENAME}')} "
                "~/.openclaw/openclaw.json"
            ),
            env=env,
        )
        if skills_command := self._build_register_skills_command():
            await self.exec_as_agent(environment, command=skills_command, env=env)
        return env

    @override
    async def run(
        self, instruction: str, environment: BaseEnvironment, context: AgentContext
    ) -> None:
        group = _group_envelope(instruction)
        logged_instruction = str(group["turns"][0]["prompt"]) if group else instruction
        env = await self._prepare(environment, logged_instruction)
        if not self.session_id:
            raise RuntimeError("Harbor did not assign the OpenClaw trial session_id")
        group_id = str(group["group_id"]) if group else "single-turn"
        safe_session = re.sub(
            r"[^A-Za-z0-9_-]+", "-", f"{self.session_id}-{group_id}"
        ).strip("-")
        cli_flags = self.build_cli_flags()
        turns = (
            group["turns"] if group else [{"case_id": "setup", "prompt": instruction}]
        )
        for index, turn in enumerate(turns, 1):
            output_name = (
                "openclaw.txt" if index == len(turns) else f"openclaw-turn-{index}.txt"
            )
            command = (
                "set -o pipefail; "
                ". ~/.nvm/nvm.sh && nvm use 22 >/dev/null && "
                f"openclaw agent --local --json {cli_flags + ' ' if cli_flags else ''}"
                f"--session-key {shlex.quote(safe_session)} "
                f"--model {shlex.quote(self.model_name)} "
                f"--message {shlex.quote(str(turn['prompt']))} "
                f"</dev/null "
                f"| stdbuf -oL tee /logs/agent/{output_name}"
            )
            if group:
                temporary_path = f'"${{TMPDIR:?}}/prediction-{index}.json.tmp"'
                prediction_path = f"/logs/artifacts/prediction-{index}.json"
                command += (
                    f" | {_prediction_extractor_command(str(turn['case_id']))} "
                    f"> {temporary_path} "
                    f"&& mv {temporary_path} {shlex.quote(prediction_path)}"
                )
            if index == len(turns):
                command += (
                    f" && ({self._shell_copy_openclaw_session_to_logs()} || true)"
                )
            await self.exec_as_agent(environment, command=command, env=env)
