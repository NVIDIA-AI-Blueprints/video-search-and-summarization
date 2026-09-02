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
    "SKILLS_EVAL_HARNESS",
    "EVAL_AGENT",
    "SKILLS_EVAL_PROVIDER",
    "SKILLS_EVAL_MODEL",
    "SKILLS_EVAL_ENDPOINT_URL",
    "SKILLS_EVAL_API_KEY",
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
    "NEMOCLAW_AGENT_THINKING",
    "RTSP_SAMPLE_URL",
)

_NEMOCLAW_DEFAULTS = {
    "NEMOCLAW_INSTALL_REF": "v0.0.114",
    "NEMOCLAW_SANDBOX_NAME": "skill-eval",
    "NEMOCLAW_GATEWAY_PORT": "8991",
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


def _destroy_sandbox_command(sandbox: str, gateway_port: str) -> str:
    quoted = shlex.quote(sandbox)
    quoted_port = shlex.quote(gateway_port)
    return f"""
set -e
set +u
. "$HOME/.profile" 2>/dev/null || true
set -u
host_home=$HOME
export HOME="$host_home/.skill-eval/nemoclaw-home"
export NEMOCLAW_GATEWAY_PORT={quoted_port}
if command -v nemoclaw >/dev/null 2>&1 && \
   command -v openshell >/dev/null 2>&1; then
  if openshell sandbox get {quoted} >/dev/null 2>&1; then
    timeout --signal=TERM --kill-after=30 600s \
      nemoclaw {quoted} destroy --yes --cleanup-gateway
  else
    # Sandbox RECORD is gone, but an `openshell-gateway` process can outlive it
    # and keep the port bound, so every later leg on this box dies with
    # "gateway port N occupied" (run 33599330003: pid 4073256 holding 8991 on
    # vss-eval-l40s-5 with no sandbox container). `--cleanup-gateway` is the
    # sanctioned way to release it, so attempt it even with no record.
    #
    # Deliberately NOT force-killing whatever owns the port:
    # test_eval_harness_only_destroys_the_named_sandbox constrains this command
    # to the named sandbox's own CLI, so keep it that way.
    # Best-effort — a missing record is not itself an error.
    timeout --signal=TERM --kill-after=30 600s \
      nemoclaw {quoted} destroy --yes --cleanup-gateway >/dev/null 2>&1 || true
  fi
fi
""".strip()


def _setup_command(timeout: int) -> str:
    return f"""
set -e
set +u
. "$HOME/.profile" 2>/dev/null || true
set -u
. "$HOME/.eval_env"
host_home=$HOME
repo="$host_home/video-search-and-summarization"
export HOME="$host_home/.skill-eval/nemoclaw-home"
mkdir -p "$HOME"
cd "$repo"
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


def _bounded_predeploy_timeout() -> int:
    """Seconds allowed for the whole MCP deploy, including a cold NIM pull.

    A cold first deploy downloads model weights (~20 min observed); warm is
    ~1 min. The default leaves headroom for cold without letting a wedged
    `docker_up` eat the leg's 840-minute Actions budget.
    """
    value = int(os.environ.get("NEMOCLAW_PREDEPLOY_TIMEOUT_SEC", "3600"))
    if not 300 <= value <= 7200:
        raise ValueError("NEMOCLAW_PREDEPLOY_TIMEOUT_SEC must be 300..7200")
    return value


def _predeploy_command(profile: str, deploy_mode: str, timeout: int) -> str:
    """Drive the documented orchestrator-MCP deploy sequence on the box.

    Runs AFTER the setup notebooks because the MCP server it calls is what
    `deploy_vss_orchestrator.ipynb` starts. Uses the same reassigned HOME as
    `_setup_command` so `uv` resolves the same caches and venv the notebooks
    prepared, and pins VSS_REPO_DIR explicitly -- predeploy.py would otherwise
    derive it from the reassigned HOME and miss the checkout.

    Runs under `uv ... --python 3.12`, NOT the box's bare `python3` (3.10):
    predeploy.py imports `deploy/docker/scripts/orchestrator_mcp_helper.py`,
    which does `from enum import StrEnum` -- 3.11+. Run 33587758108 failed both
    legs on exactly that. Same interpreter contract as `_setup_command`; the
    helper's own inner `uv run nat mcp client` still resolves the agent
    project's venv, so this only pins the outer interpreter.
    """
    mode_arg = (
        f" --deploy-mode {shlex.quote(deploy_mode)}" if deploy_mode else ""
    )
    return f"""
set -e
set +u
. "$HOME/.profile" 2>/dev/null || true
set -u
. "$HOME/.eval_env"
host_home=$HOME
repo="$host_home/video-search-and-summarization"
export HOME="$host_home/.skill-eval/nemoclaw-home"
export PATH="$HOME/.local/bin:$host_home/.local/bin:$PATH"
export VSS_REPO_DIR="$repo"
cd "$repo"
timeout --signal=TERM --kill-after=120 {timeout}s \
  uv run --isolated --no-project --python 3.12 -- \
  python .github/skill-eval/nemoclaw/predeploy.py \
  --profile {shlex.quote(profile)}{mode_arg}
""".strip()


class NemoClawBrevEnvironment(BrevEnvironment):
    """Run normal Brev preparation, then the checked-in setup notebooks."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._nemoclaw_ready = False

    def _is_first_trial(self) -> bool:
        """True for a single-step spec or `step-1` of a multi-step chain.

        Mirrors the predicate BrevEnvironment.start() uses to gate the docker
        reset: `environment_dir.parent` is the task dir, named `step-N` for a
        multi-step spec and the platform for a single-step one.
        """
        task_dir_name = self.environment_dir.parent.name
        return not (task_dir_name.startswith("step-") and task_dir_name != "step-1")

    async def start(self, force_build: bool) -> None:
        if self._nemoclaw_ready:
            return

        # Onboard once per leg, not once per step. Each step is a separate
        # `harbor run` -> separate start(), and destroying the sandbox here
        # would re-run `nemoclaw onboard` plus both setup notebooks for every
        # step -- minutes of rebuild per step on a multi-step spec, to arrive
        # at the state step-1 already established. The base class gates its
        # docker reset on the same predicate, so step-2+ already keeps the
        # deployment; keeping the sandbox and the orchestrator MCP alongside it
        # is the matching behaviour, and step N's checks assume step N-1's
        # environment anyway (AGENTS.md "Multi-step specs").
        first_trial = self._is_first_trial()
        instance = self._resolve_instance_name()
        sandbox = os.environ.get("NEMOCLAW_SANDBOX_NAME", "skill-eval")
        gateway_port = os.environ.get("NEMOCLAW_GATEWAY_PORT", "8991")
        if instance and first_trial:
            destroyed = await _run_brev_exec(
                instance,
                _destroy_sandbox_command(sandbox, gateway_port),
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

        if not first_trial:
            logger.info(
                "Reusing the NemoClaw sandbox and orchestrator MCP established "
                "by step-1 on %s (task=%s); skipping onboard and notebook setup",
                self._instance_name,
                self.environment_dir.parent.name,
            )
            self._nemoclaw_ready = True
            return

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
        await self._predeploy_vss()
        self._nemoclaw_ready = True
        logger.info("NemoClaw is ready on %s", self._instance_name)

    async def _predeploy_vss(self) -> None:
        """Deploy this spec's VSS profile before the agent turn, if declared.

        OPT-IN by metadata presence: an adapter that emits `profile` into
        `task.toml [metadata]` gets its stack pre-deployed; one that does not is
        untouched and keeps today's behaviour. That is what keeps the deploy
        skills (`vss-deploy-*`, `vss-setup-*`) correct -- pre-deploying those
        would make the eval vacuous -- without any per-skill special-casing
        here.

        Reverses part of #819, which removed
        `BrevEnvironment._ensure_prerequisite_deployed()` so the deploy would be
        visible in the trial trajectory. The difference: that hook ran
        `/vss-deploy-profile` through a *sub-agent*, whereas this drives the
        documented MCP tool sequence directly, so it is reproducible rather than
        model-dependent. The trajectory argument is answered by the brev-exec
        output landing in the trial log instead.
        """
        metadata = self._read_task_metadata()
        profile = str(metadata.get("profile") or "").strip()
        if not profile:
            logger.info(
                "No `profile` in task.toml [metadata]; skipping VSS pre-deploy "
                "(the trial's own steps own deployment)"
            )
            return
        deploy_mode = str(metadata.get("deploy_mode") or "").strip()
        timeout = _bounded_predeploy_timeout()
        logger.info(
            "Pre-deploying VSS profile %r (mode=%s) on %s via the orchestrator "
            "MCP (timeout=%ss)",
            profile,
            deploy_mode or "-",
            self._instance_name,
            timeout,
        )
        result = await _run_brev_exec(
            self._instance_name,
            _predeploy_command(profile, deploy_mode, timeout),
            timeout=timeout + 60,
        )
        if result.return_code != 0:
            detail = (result.stderr or result.stdout or "")[-12000:]
            raise RuntimeError(
                f"VSS pre-deploy failed for profile {profile!r} "
                f"(exit {result.return_code}):\n{detail}"
            )
        logger.info("VSS profile %r is deployed on %s", profile, self._instance_name)
