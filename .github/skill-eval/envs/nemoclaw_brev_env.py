# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Brev environment that runs the checked-in NemoClaw setup notebooks."""

from __future__ import annotations

import logging
import os
import shlex

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
    "NEMOCLAW_DASHBOARD_PORT",
    "NEMOCLAW_POLICY_MODE",
    "HARDWARE_PROFILE",
    "HOST_INTERNAL_ALIAS",
    "VSS_ORCHESTRATOR_MCP_PORT",
    "VSS_ORCHESTRATOR_MCP_URL",
    "NEMOCLAW_AGENT_TIMEOUT_SEC",
    "RTSP_SAMPLE_URL",
)

_NEMOCLAW_DEFAULTS = {
    "NEMOCLAW_INSTALL_REF": "v0.0.108",
    "NEMOCLAW_SANDBOX_NAME": "skill-eval",
    "NEMOCLAW_GATEWAY_PORT": "8990",
    "NEMOCLAW_POLICY_MODE": "skip",
}


def _bounded_setup_timeout() -> int:
    value = int(os.environ.get("NEMOCLAW_SETUP_TIMEOUT_SEC", "5400"))
    if not 300 <= value <= 7200:
        raise ValueError("NEMOCLAW_SETUP_TIMEOUT_SEC must be 300..7200")
    return value


def _forwarded_nemoclaw_env() -> str:
    defaults = dict(_NEMOCLAW_DEFAULTS)
    run_id = os.environ.get("GITHUB_RUN_ID", "0")
    if run_id.isdigit():
        defaults["NEMOCLAW_DASHBOARD_PORT"] = str(20000 + int(run_id) % 40000)
    platform = os.environ.get("EVAL_PLATFORM", "")
    if platform in {"L40S", "RTXPRO6000BW"}:
        defaults["HARDWARE_PROFILE"] = platform
    elif platform == "ANY":
        defaults["HARDWARE_PROFILE"] = "RTXPRO6000BW"

    values = []
    for key in _SETUP_KEYS:
        value = os.environ.get(key, defaults.get(key))
        if value is not None:
            values.append((key, value))
    values.extend(
        [
            ("AGENT_RUNTIME", "openclaw"),
            ("ORCHESTRATOR_ENABLE_HTTPS", "false"),
            ("LLM_DEVICE_ID", ""),
            ("VLM_DEVICE_ID", ""),
        ]
    )
    return "\n".join(f"export {key}={shlex.quote(value)}" for key, value in values)


def _destroy_sandbox_command(sandbox: str) -> str:
    quoted = shlex.quote(sandbox)
    return f"""
set -e
set +u
. "$HOME/.profile" 2>/dev/null || true
set -u
if command -v nemoclaw >/dev/null 2>&1 && \
   command -v openshell >/dev/null 2>&1 && \
   openshell sandbox get {quoted} >/dev/null 2>&1; then
  timeout --signal=TERM --kill-after=30 600s \
    nemoclaw {quoted} destroy --yes --cleanup-gateway
fi
""".strip()


def _setup_command(timeout: int) -> str:
    return f"""
set -e
set +u
. "$HOME/.profile" 2>/dev/null || true
set -u
. "$HOME/.eval_env"
cd "$HOME/video-search-and-summarization"
scratch=/tmp/skill-eval/nemoclaw
mkdir -p "$scratch"
export NEMOCLAW_SETUP_CELL_TIMEOUT_SEC={timeout}
timeout --signal=TERM --kill-after=120 {timeout}s \
  uv run --isolated --no-project --python 3.12 \
  --with nbformat --with nbclient --with ipykernel -- \
  python .github/skill-eval/nemoclaw/notebook_setup_adapter.py \
  --env-out "$scratch/nemoclaw.env" \
  --timeout "$NEMOCLAW_SETUP_CELL_TIMEOUT_SEC"
""".strip()


class NemoClawBrevEnvironment(BrevEnvironment):
    """Run normal Brev preparation, then the checked-in setup notebooks."""

    _preserved_docker_networks = ("openshell-docker",)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._nemoclaw_ready = False

    async def start(self, force_build: bool) -> None:
        if self._nemoclaw_ready:
            return

        # Use one stable CI sandbox name per locked worker. Destroying it
        # before the base provider resets Docker gives onboarding a clean
        # lifecycle without teaching this harness how to repair host state.
        instance = self._resolve_instance_name()
        sandbox = os.environ.get("NEMOCLAW_SANDBOX_NAME", "skill-eval")
        if instance:
            destroyed = await _run_brev_exec(
                instance,
                _destroy_sandbox_command(sandbox),
                timeout=660,
            )
            if destroyed.return_code != 0:
                detail = (destroyed.stderr or destroyed.stdout or "")[-2000:]
                raise RuntimeError(
                    f"Could not destroy existing NemoClaw sandbox {sandbox!r}:\n"
                    f"{detail}"
                )

        await super().start(force_build)
        if self._instance_name is None:
            raise RuntimeError("NemoClaw setup requires an explicit Brev instance")

        env_block = _forwarded_nemoclaw_env()
        append = (
            "cat >> \"$HOME/.eval_env\" <<'__NEMOCLAW_ENV__'\n"
            f"{env_block}\n"
            "__NEMOCLAW_ENV__"
        )
        written = await _run_brev_exec(self._instance_name, append, timeout=30)
        if written.return_code != 0:
            raise RuntimeError("Could not forward NemoClaw setup environment")

        timeout = _bounded_setup_timeout()
        logger.info(
            "Running NemoClaw setup notebooks on %s (timeout=%ss)",
            self._instance_name,
            timeout,
        )
        result = await _run_brev_exec(
            self._instance_name,
            _setup_command(timeout),
            timeout=timeout + 60,
        )
        if result.return_code != 0:
            detail = (result.stderr or result.stdout or "")[-12000:]
            raise RuntimeError(
                f"NemoClaw notebook setup failed (exit {result.return_code}):\n{detail}"
            )
        self._nemoclaw_ready = True
        logger.info("NemoClaw is ready on %s", self._instance_name)
