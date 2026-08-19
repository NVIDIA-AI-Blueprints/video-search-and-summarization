#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Run one skills-eval leg under a process-held Brev box lock.

This wrapper owns BOTH fleet selection and the per-instance flock: it
reads the task's hardware requirements from the dataset's task.toml,
snapshots `brev ls --json`, and walks the eligible `vss-eval-*`
candidates with NON-BLOCKING lock attempts — claiming the first box it
can actually lock. The lock file descriptor stays open while Harbor
runs, so the mutex is a real kernel lock instead of a shell-FD
convention spread across multiple agent tool calls.

Why selection lives here and not in the agent: two legs that snapshot
the fleet at the same moment both see the same "best" lock-free box
(neither has acquired yet — check-then-act TOCTOU) and converge on it,
serialising for hours while other eligible boxes idle (observed:
run 29373239241, both lvs legs picked vss-eval-rtx-1g-2 and the second
waited 16 min with rtx-1g-3 free). Try-lock-in-order makes the pick and
the reservation one atomic step.

`--instance` remains as an explicit operator override (pinned box).
"""
from __future__ import annotations

import argparse
import contextlib
import dataclasses
import errno
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse

# Self-contained instrumentation with its own module state. Split out because
# this file is long enough that a reader looking for the lock or the Harbor
# command should not have to scroll past it. Read the phase label through
# leg_timing.current_phase(); importing the global copies it once.
import leg_timing
from leg_timing import HEARTBEAT_SEC, leg_log, phase

REPO_ROOT = Path(__file__).resolve().parents[2]
SKILL_EVAL_PYTHON_VERSION = (3, 12)
HARBOR_REQUIREMENT = "harbor==0.20.0"
# Harbor's uvx env is isolated from SKILL_EVAL_VENV. The generic verifier
# imports claude-agent-sdk (and cannot `pip install` into a uvx runtime).
CLAUDE_AGENT_SDK_REQUIREMENT = "claude-agent-sdk==0.2.128"
STEP_COUNT_RE = re.compile(r"^\s*step_count\s*=\s*(\d+)\s*$", re.MULTILINE)
SAFE_PART_RE = re.compile(r"[^A-Za-z0-9_-]+")
RTX4090_PREFIX = "vss-eval-geforce-rtx4090-"
# RTX 4090 capability-routing is opt-in at the spec level via gpu_type.
# These tables are intentionally empty: a test runs on RTX 4090 only when
# its spec metadata declares gpu_type that matches GEFORCE RTX 4090.
RTX4090_ALL_TESTS: frozenset[str] = frozenset()
RTX4090_TESTS: dict[str, frozenset[str]] = {}
# Shared root served by the coordinator's persistent `harbor-view.service`
# (AGENTS.md § Harbor viewer). Fixed path — the viewer is started once for
# the host, not per leg, so every leg publishes its trials in here.
VIEWER_ROOT = Path("/tmp/skill-eval/results/_viewer")


# Harbor phase budgets. Adapters set the task's base agent timeout to the same
# 600-second base used by Harbor for environment build and verification; these
# multipliers are also passed on the command line below. Keep the outer process
# backstop strictly beyond every phase plus recovery time so Harbor gets a
# chance to record the real phase outcome, download logs, and stop the Brev
# environment before this wrapper intervenes.
HARBOR_BASE_PHASE_TIMEOUT_SEC = 600
HARBOR_ENVIRONMENT_BUILD_TIMEOUT_MULTIPLIER = 3.0
NEMOCLAW_ENVIRONMENT_BUILD_TIMEOUT_MULTIPLIER = 10.0
HARBOR_AGENT_TIMEOUT_MULTIPLIER = 6.0
HARBOR_VERIFIER_TIMEOUT_MULTIPLIER = 3.0
HARBOR_ENVIRONMENT_BUILD_BUDGET_SEC = int(
    HARBOR_BASE_PHASE_TIMEOUT_SEC
    * HARBOR_ENVIRONMENT_BUILD_TIMEOUT_MULTIPLIER
)
# Harbor applies a separate six-minute ceiling while installing/configuring
# the selected agent, before the task's [agent] timeout begins.
HARBOR_AGENT_SETUP_BUDGET_SEC = 360
HARBOR_AGENT_BUDGET_SEC = int(
    HARBOR_BASE_PHASE_TIMEOUT_SEC * HARBOR_AGENT_TIMEOUT_MULTIPLIER
)
HARBOR_VERIFIER_BUDGET_SEC = int(
    HARBOR_BASE_PHASE_TIMEOUT_SEC * HARBOR_VERIFIER_TIMEOUT_MULTIPLIER
)
HARBOR_PHASE_BUDGET_SEC = (
    HARBOR_ENVIRONMENT_BUILD_BUDGET_SEC
    + HARBOR_AGENT_SETUP_BUDGET_SEC
    + HARBOR_AGENT_BUDGET_SEC
    + HARBOR_VERIFIER_BUDGET_SEC
)
# brev_env.py caps each upload/download API, including active work and process
# reaping, to this duration. These VSS tasks serially perform no more than four
# transfer windows around post-agent output/recovery, so reserve all four before
# the outer backstop.
HARBOR_TRANSFER_OPERATION_BUDGET_SEC = 630
HARBOR_RECOVERY_TRANSFER_OPERATION_COUNT = 4
HARBOR_CLEANUP_RECOVERY_HEADROOM_SEC = (
    HARBOR_TRANSFER_OPERATION_BUDGET_SEC
    * HARBOR_RECOVERY_TRANSFER_OPERATION_COUNT
)
MIN_HARBOR_BACKSTOP_SEC = (
    HARBOR_PHASE_BUDGET_SEC + HARBOR_CLEANUP_RECOVERY_HEADROOM_SEC
)
# Stay strictly above the minimum rather than making the validation boundary
# itself the default.  The round 200-minute backstop leaves another 32 minutes
# for scheduling jitter and bounded teardown that does not transfer files.
DEFAULT_HARBOR_TIMEOUT_SEC = 12_000

# A single remote agent command must not be killed by Brev before Harbor's own
# agent deadline can fire and drive normal artifact/environment cleanup.
MIN_BREV_EXEC_TIMEOUT_SEC = (
    HARBOR_AGENT_BUDGET_SEC + HARBOR_TRANSFER_OPERATION_BUDGET_SEC
)

# Emergency-only escalation after the outer backstop. SIGINT gives Harbor's
# asyncio runner enough time to complete Harbor 0.20's two serialized recovery
# pulls (agent logs, then task artifacts) before TERM/KILL. This preserves the
# primary timeout record instead of interrupting artifact recovery halfway.
HARBOR_SIGINT_GRACE_SEC = (
    2 * HARBOR_TRANSFER_OPERATION_BUDGET_SEC + 120
)
HARBOR_SIGTERM_GRACE_SEC = 30
HARBOR_SIGKILL_GRACE_SEC = 10
PROCESS_GROUP_POLL_INTERVAL_SEC = 0.1
HARBOR_SHUTDOWN_GRACE_SEC = (
    HARBOR_SIGINT_GRACE_SEC
    + HARBOR_SIGTERM_GRACE_SEC
    + HARBOR_SIGKILL_GRACE_SEC
)
HARBOR_POSTPROCESS_HEADROOM_SEC = 60
AGENT_VERDICT_RESERVE_SEC = 30 * 60
DEFAULT_WHOLE_LEG_BUDGET_SEC = 12 * 60 * 60 - AGENT_VERDICT_RESERVE_SEC
WORK_DEADLINE_ENV = "SKILL_EVAL_HARBOR_DEADLINE_MONOTONIC"
SDK_DEADLINE_ENV = "SKILL_EVAL_WORK_DEADLINE_MONOTONIC"
TRANSPORT_PGID_REGISTRY_ENV = "BREV_TRANSPORT_PGID_FILE"


@dataclasses.dataclass(frozen=True)
class HarborInvocation:
    """One concrete `uvx harbor run` invocation."""

    harbor_root: Path
    include_task_name: str
    chain_key: str
    step_index: int | None = None
    step_count: int | None = None


class LockTimeoutError(RuntimeError):
    pass


class LegDeadlineError(RuntimeError):
    pass


class _RunCommandInterrupted(BaseException):
    def __init__(self, signum: int):
        self.signum = signum
        super().__init__(f"run_leg received signal {signum}")


def _read_step_count(task_toml: Path) -> int | None:
    match = STEP_COUNT_RE.search(task_toml.read_text())
    return int(match.group(1)) if match else None


def _max_step_number(platform_dir: Path) -> int:
    max_step = 0
    for child in platform_dir.iterdir():
        if not child.is_dir():
            continue
        match = re.fullmatch(r"step-(\d+)", child.name)
        if match:
            max_step = max(max_step, int(match.group(1)))
    return max_step


def _chain_key(dataset_root: Path, harbor_root: Path) -> str:
    try:
        rel = harbor_root.relative_to(dataset_root)
    except ValueError:
        rel = harbor_root
    return SAFE_PART_RE.sub("_", rel.as_posix()).strip("_") or harbor_root.name


def discover_invocations(dataset_root: Path) -> list[HarborInvocation]:
    """Discover single-step tasks or ordered multi-step task chains."""
    dataset_root = dataset_root.resolve()
    step1_tomls = sorted(dataset_root.rglob("step-1/task.toml"))
    if step1_tomls:
        invocations: list[HarborInvocation] = []
        seen_roots: set[Path] = set()
        for step1_toml in step1_tomls:
            platform_dir = step1_toml.parent.parent
            if platform_dir in seen_roots:
                continue
            seen_roots.add(platform_dir)
            step_count = _read_step_count(step1_toml) or _max_step_number(platform_dir)
            if step_count < 1:
                raise ValueError(f"invalid step_count for {platform_dir}")
            key = _chain_key(dataset_root, platform_dir)
            for idx in range(1, step_count + 1):
                task_toml = platform_dir / f"step-{idx}" / "task.toml"
                if not task_toml.exists():
                    raise FileNotFoundError(
                        f"missing task.toml for step-{idx}: {task_toml}"
                    )
                invocations.append(
                    HarborInvocation(
                        harbor_root=platform_dir,
                        include_task_name=f"step-{idx}",
                        chain_key=key,
                        step_index=idx,
                        step_count=step_count,
                    )
                )
        return invocations

    task_tomls = sorted(dataset_root.rglob("task.toml"))
    if not task_tomls:
        raise FileNotFoundError(f"no task.toml found under {dataset_root}")

    invocations = []
    for task_toml in task_tomls:
        task_dir = task_toml.parent
        invocations.append(
            HarborInvocation(
                harbor_root=task_dir.parent,
                include_task_name=task_dir.name,
                chain_key=_chain_key(dataset_root, task_dir),
            )
        )
    return invocations


def _api_base_v1(base_url: str) -> str:
    stripped = base_url.rstrip("/")
    if stripped.endswith("/v1"):
        return stripped
    return f"{stripped}/v1"


def validate_harbor_timeout_sec(timeout_sec: int) -> int:
    """Require the outer backstop to leave every Harbor phase recovery room."""
    if timeout_sec <= MIN_HARBOR_BACKSTOP_SEC:
        raise ValueError(
            "harbor timeout must be greater than "
            f"{MIN_HARBOR_BACKSTOP_SEC}s: environment "
            f"{HARBOR_ENVIRONMENT_BUILD_BUDGET_SEC}s + agent setup "
            f"{HARBOR_AGENT_SETUP_BUDGET_SEC}s + agent "
            f"{HARBOR_AGENT_BUDGET_SEC}s + verifier "
            f"{HARBOR_VERIFIER_BUDGET_SEC}s + cleanup/recovery "
            f"{HARBOR_CLEANUP_RECOVERY_HEADROOM_SEC}s"
        )
    return timeout_sec


def resolve_work_deadline() -> float:
    """Return the Harbor deadline, preserving time for the agent's verdict."""
    raw_deadline = os.environ.get(WORK_DEADLINE_ENV)
    if raw_deadline is None and SDK_DEADLINE_ENV in os.environ:
        try:
            deadline = float(os.environ[SDK_DEADLINE_ENV])
        except ValueError as exc:
            raise ValueError(
                f"{SDK_DEADLINE_ENV} must be a monotonic timestamp"
            ) from exc
        raw_deadline = str(deadline - AGENT_VERDICT_RESERVE_SEC)
    if raw_deadline is None:
        return time.monotonic() + DEFAULT_WHOLE_LEG_BUDGET_SEC
    try:
        deadline = float(raw_deadline)
    except ValueError as exc:
        raise ValueError(f"{WORK_DEADLINE_ENV} must be a monotonic timestamp") from exc
    if deadline <= time.monotonic():
        raise LegDeadlineError("skill-eval work deadline has already expired")
    return deadline


def invocation_reserve_sec(harbor_timeout_sec: int) -> int:
    """Wall-clock room required before safely starting one Harbor child."""
    return (
        harbor_timeout_sec
        + HARBOR_SHUTDOWN_GRACE_SEC
        + HARBOR_POSTPROCESS_HEADROOM_SEC
    )


def build_harbor_command(
    invocation: HarborInvocation,
    results_root: Path,
    model: str,
    anthropic_base_url: str,
    agent: str = "claude-code",
) -> list[str]:
    environment_import_path = "envs.brev_env:BrevEnvironment"
    environment_build_timeout_multiplier = (
        HARBOR_ENVIRONMENT_BUILD_TIMEOUT_MULTIPLIER
    )
    if agent == "codex":
        # Custom NvCodex subclass (agents/nv_codex.py) keeps the full
        # provider-prefixed model id — harbor's stock codex strips it to the
        # last path segment, which the NVIDIA gateway 401s on. Endpoint via
        # `--ak api_base`; OPENAI_API_KEY is read from the environment (same as
        # claude-code reads ANTHROPIC_API_KEY), so it never lands on the CLI.
        agent_flags = [
            "-a", "agents.nv_codex:NvCodex",
            "--model", model,
            "--ak", f"api_base={_api_base_v1(anthropic_base_url)}",
        ]
    elif agent == "claude-code":
        agent_flags = [
            "-a", "claude-code",
            "--model", model,
            "--ak", f"api_base={_api_base_v1(anthropic_base_url)}",
            "--ae", "CLAUDE_CODE_DISABLE_THINKING=1",
        ]
    elif agent == "nemoclaw":
        environment_import_path = (
            "envs.nemoclaw_brev_env:NemoClawBrevEnvironment"
        )
        environment_build_timeout_multiplier = (
            NEMOCLAW_ENVIRONMENT_BUILD_TIMEOUT_MULTIPLIER
        )
        agent_flags = [
            "-a", "agents.nemoclaw:NemoClaw",
            "--model", model,
        ]
    else:
        raise ValueError(
            f"unsupported agent {agent!r} "
            "(expected claude-code | codex | nemoclaw)"
        )
    return [
        "uvx",
        "--python",
        sys.executable,
        "--from",
        HARBOR_REQUIREMENT,
        "--with",
        CLAUDE_AGENT_SDK_REQUIREMENT,
        "harbor",
        "run",
        "--environment-import-path",
        environment_import_path,
        "-p",
        str(invocation.harbor_root),
        "--include-task-name",
        invocation.include_task_name,
        *agent_flags,
        "--environment-build-timeout-multiplier",
        str(environment_build_timeout_multiplier),
        "--agent-timeout-multiplier",
        str(HARBOR_AGENT_TIMEOUT_MULTIPLIER),
        "--verifier-timeout-multiplier",
        str(HARBOR_VERIFIER_TIMEOUT_MULTIPLIER),
        "--max-retries",
        "0",
        "-n",
        "1",
        "--yes",
        "-o",
        str(results_root),
    ]


def harbor_env(instance: str) -> dict[str, str]:
    env = os.environ.copy()
    if env.get("SKILL_EVAL_LOCAL_GPU_INSTANCE"):
        for key in list(env):
            if (
                key in {
                    "GH_TOKEN",
                    "GITHUB_TOKEN",
                    "SYSTEM_ACCESSTOKEN",
                }
                or key.startswith("ACTIONS_")
                or (
                    key.startswith("RUNNER_")
                    and key != "RUNNER_TRACKING_ID"
                )
                or (
                    key.startswith("GITHUB_")
                    and key not in {"GITHUB_RUN_ID", "GITHUB_WORKSPACE"}
                )
            ):
                env.pop(key, None)
        env.pop("SSH_AGENT_PID", None)
        env.pop("SSH_AUTH_SOCK", None)
    workspace = env.get("GITHUB_WORKSPACE") or str(REPO_ROOT)
    skill_eval_path = str(Path(workspace) / ".github" / "skill-eval")
    pythonpath = env.get("PYTHONPATH", "")
    if skill_eval_path not in pythonpath.split(":"):
        pythonpath = f"{skill_eval_path}:{pythonpath}" if pythonpath else skill_eval_path
    env["PYTHONPATH"] = pythonpath
    env["PATH"] = f"{Path.home() / '.local' / 'bin'}:{env.get('PATH', '')}"
    env["BREV_INSTANCE"] = instance
    env["CLAUDE_CODE_DISABLE_THINKING"] = "1"
    try:
        configured_brev_timeout = int(env.get("BREV_EXEC_TIMEOUT", "0"))
    except ValueError as exc:
        raise ValueError(
            "BREV_EXEC_TIMEOUT must be an integer number of seconds"
        ) from exc
    env["BREV_EXEC_TIMEOUT"] = str(
        max(configured_brev_timeout, MIN_BREV_EXEC_TIMEOUT_SEC)
    )
    # The outer backstop's recovery budget is derived from this exact per-call
    # cap. Do not let a larger inherited runner value invalidate that bound.
    env["BREV_TRANSFER_TOTAL_TIMEOUT_SEC"] = str(
        HARBOR_TRANSFER_OPERATION_BUDGET_SEC
    )
    return env


def _read_dataset_metadata(dataset_root: Path) -> dict:
    """[metadata] of the first task.toml under the dataset (all steps of a
    leg share one platform, so any task.toml carries the leg's hardware
    requirements)."""
    try:
        import tomllib
    except ModuleNotFoundError:  # Python < 3.11 on the coordinator
        import tomli as tomllib  # type: ignore[no-redef]

    task_toml = next(iter(sorted(dataset_root.rglob("task.toml"))), None)
    if task_toml is None:
        return {}
    return tomllib.loads(task_toml.read_text()).get("metadata", {}) or {}


def _parse_brev_json(raw: str | None) -> list[dict]:
    """Strip trailing walkthrough text and parse JSON from brev CLI.

    Handles both the legacy bare-array format (``[{...}, ...]``) and the
    newer wrapped format (``{"workspaces": [{...}, ...]}``) introduced in
    recent brev CLI versions.
    """
    import json

    if not raw:
        return []
    # Try full parse first (handles both formats without bracket heuristics)
    stripped = raw.strip()
    try:
        parsed = json.loads(stripped)
        if isinstance(parsed, list):
            return parsed
        if isinstance(parsed, dict) and "workspaces" in parsed:
            return parsed["workspaces"]
        return []
    except json.JSONDecodeError:
        pass
    # Fallback: strip trailing walkthrough text after last `]`
    bracket = raw.rfind("]")
    if bracket < 0:
        return []
    try:
        parsed = json.loads(raw[: bracket + 1])
        if isinstance(parsed, list):
            return parsed
        if isinstance(parsed, dict) and "workspaces" in parsed:
            return parsed["workspaces"]
        return []
    except json.JSONDecodeError:
        pass
    # Last resort: extract the inner array from {"workspaces": [...]}
    start = raw.find("[")
    if start >= 0 and bracket > start:
        try:
            return json.loads(raw[start: bracket + 1])
        except json.JSONDecodeError:
            pass
    return []


def _list_brev_instances() -> list[dict]:
    """Snapshot `brev ls --json` with retries for transient RPC flakes.
    An org with zero managed instances prints `null` — authoritative-empty."""
    for attempt in range(4):
        try:
            proc = subprocess.run(
                ["brev", "ls", "--json"],
                capture_output=True, text=True, timeout=60,
            )
        except (subprocess.TimeoutExpired, OSError) as exc:
            print(f"[run-leg] brev ls failed (attempt {attempt + 1}): {exc}", flush=True)
            time.sleep(5)
            continue
        raw = (proc.stdout or "").strip()
        if raw.startswith("null"):
            return []
        if raw and raw.rfind("]") >= 0:
            return _parse_brev_json(raw)
        print(f"[run-leg] brev ls returned empty stdout (attempt {attempt + 1})", flush=True)
        time.sleep(5)
    return []


def _list_registered_nodes() -> list[dict]:
    """Snapshot registered external nodes from ``brev ls nodes --json``.

    The CLI intentionally keeps registered nodes out of ``brev ls --json``.
    Treat a well-formed empty array (or ``null``) as authoritative, but retry
    empty/malformed output because transient auth/RPC failures otherwise make
    the external pool disappear for the whole lock wait.
    """
    for attempt in range(4):
        try:
            proc = subprocess.run(
                ["brev", "ls", "nodes", "--json"],
                capture_output=True, text=True, timeout=60,
            )
        except (subprocess.TimeoutExpired, OSError) as exc:
            print(
                f"[run-leg] brev ls nodes failed (attempt {attempt + 1}): {exc}",
                flush=True,
            )
            time.sleep(5)
            continue
        raw = (proc.stdout or "").strip()
        if raw.startswith("null"):
            return []
        if raw and raw.rfind("]") >= 0:
            return _parse_brev_json(raw)
        print(
            f"[run-leg] brev ls nodes returned empty stdout "
            f"(attempt {attempt + 1})",
            flush=True,
        )
        time.sleep(5)
    return []


def _registered_gpu_hint(name: str) -> str:
    """Infer hardware only for operator-controlled ``vss-eval-*`` names.

    ``brev ls nodes --json`` currently reports name/status but no GPU model.
    The pool's documented prefixes are therefore the only available hardware
    contract. Unknown prefixes return empty so GPU-requiring legs fail closed.
    """
    normalized = name.lower()
    if normalized.startswith(RTX4090_PREFIX):
        return "GEFORCE RTX 4090"
    # Use a more specific prefix to avoid matching RTX 4090 nodes whose names
    # begin with "vss-eval-rtx" (e.g. "vss-eval-rtx4090-*") as RTX PRO 6000.
    if normalized.startswith("vss-eval-rtx-"):
        return "RTX PRO 6000"
    if normalized.startswith("vss-eval-l40s"):
        return "L40S"
    if normalized.startswith("vss-eval-h100"):
        return "H100"
    return ""


def _parse_pool_names(raw: str) -> set[str]:
    return {
        name.lower()
        for name in re.split(r"[\s,]+", raw.strip())
        if name
    }


def _rtx4090_supports(skill: str | None, spec_stem: str | None) -> bool:
    """Whether resource data supports this exact test on a 24 GB RTX 4090."""
    if not skill or not spec_stem:
        return False
    return (
        skill in RTX4090_ALL_TESTS
        or spec_stem in RTX4090_TESTS.get(skill, ())
    )


def _registered_pool_allowlist(
    skill: str | None = None,
    spec_stem: str | None = None,
) -> set[str]:
    """Registered nodes approved for this test.

    ``BREV_REGISTERED_POOL`` contains full-capability workers. The separate
    RTX 4090 pool is intentionally capability-routed because those 24 GB
    cards cannot safely satisfy every RTX PRO 6000 task.
    """
    names = _parse_pool_names(os.environ.get("BREV_REGISTERED_POOL", ""))
    if _rtx4090_supports(skill, spec_stem):
        names.update(_parse_pool_names(os.environ.get("BREV_RTX4090_POOL", "")))
    return names


def _list_pool_instances(
    skill: str | None = None,
    spec_stem: str | None = None,
) -> list[dict]:
    """Return managed instances plus connected registered pool nodes."""
    instances = list(_list_brev_instances())
    seen = {(inst.get("name") or "").lower() for inst in instances}
    registered_allowlist = _registered_pool_allowlist(skill, spec_stem)
    rtx4090_allowlist = _parse_pool_names(
        os.environ.get("BREV_RTX4090_POOL", "")
    )
    if not registered_allowlist:
        return instances
    for node in _list_registered_nodes():
        name = (node.get("name") or "").strip()
        if (
            not name
            or name.lower() in seen
            or name.lower() not in registered_allowlist
        ):
            continue
        status = (node.get("status") or "").upper()
        instances.append({
            **node,
            "name": name,
            # Managed instances say RUNNING; registered nodes say Connected.
            "status": "RUNNING" if status == "CONNECTED" else status,
            "gpu": _registered_gpu_hint(name),
            "instance_type": "registered-external-node",
            "_registered": True,
            "_rtx4090_capability_routed": (
                name.lower() in rtx4090_allowlist
                and name.lower().startswith(RTX4090_PREFIX)
            ),
        })
        seen.add(name.lower())
    return instances


def _loose_gpu_match(want: str, have: str) -> bool:
    """`RTX PRO 6000` ⊆ `RTX PRO SERVER 6000` — all tokens of `want` must
    appear in `have` (substring fallback for dashed variants). Mirrors
    envs.brev_env._check_instance_matches."""
    want_tokens = set(want.replace("-", " ").split())
    have_tokens = set(have.replace("-", " ").split())
    return want_tokens.issubset(have_tokens) or want in have


def _name_gpu_count_hint(name: str) -> int | None:
    """Fleet-naming gpu_count hint: `*-1g*` → 1, `*-2g*` → 2 (AGENTS.md
    pool convention). None when the name encodes nothing."""
    if name.lower().startswith(RTX4090_PREFIX):
        return 1
    match = re.search(r"-(\d)g(?:-|$)", name)
    return int(match.group(1)) if match else None


def pool_candidates(
    metadata: dict,
    spec_stem: str | None = None,
) -> list[str]:
    """Eligible `vss-eval-*` boxes for this leg, best-first.

    Hardware-hard, software-free (AGENTS.md § 5a): RUNNING + gpu_type
    token match. Dedicated registered nodes sort before managed cloud
    instances; exact name-hinted gpu_count matches sort first within each
    tier. Over-provisioned boxes remain valid — brev_env validates the final
    pick with live nvidia-smi and the box is reset either way.
    gpu_count == 0 (remote-all / GPU-independent) accepts any RUNNING box.
    """
    required_type = (metadata.get("gpu_type") or "").upper()
    required_count = int(metadata.get("gpu_count", 1) or 0)
    skill = metadata.get("skill") or os.environ.get("EVAL_SKILL") or None
    spec_stem = (
        spec_stem
        or metadata.get("spec_stem")
        or os.environ.get("EVAL_SPEC_STEM")
        or None
    )

    candidates: list[tuple[str, bool]] = []
    for inst in _list_pool_instances(skill, spec_stem):
        name = inst.get("name") or ""
        if not name.startswith("vss-eval-"):
            continue
        if (inst.get("status") or "").upper() != "RUNNING":
            continue
        if inst.get("_registered") and required_count > 0:
            count_hint = _name_gpu_count_hint(name)
            if count_hint is not None and count_hint < required_count:
                continue
        if required_count > 0 and required_type:
            gpu = (inst.get("gpu") or "").upper()
            itype = (inst.get("instance_type") or "").upper()
            capability_routed = (
                bool(inst.get("_rtx4090_capability_routed"))
                and _rtx4090_supports(skill, spec_stem)
            )
            # Accept via instance_type when `gpu` is a transient "-"/"" flake
            # (brev catalog refresh) — same soft-fail brev_env applies.
            if not (_loose_gpu_match(required_type, gpu)
                    or _loose_gpu_match(required_type, itype)
                    or capability_routed):
                continue
        candidates.append((name, bool(inst.get("_registered"))))

    def sort_key(candidate: tuple[str, bool]) -> tuple[int, int, str]:
        name, registered = candidate
        hint = _name_gpu_count_hint(name)
        exact = 0 if (required_count > 0 and hint == required_count) else 1
        # Use the dedicated registered pool before consuming managed cloud
        # capacity. Within each pool tier, preserve exact-count partitioning.
        # BrevEnvironment validates the chosen node with live nvidia-smi.
        return (0 if registered else 1, exact, name.lower())

    return [name for name, _ in sorted(candidates, key=sort_key)]


@contextlib.contextmanager
def hold_pool_lock(candidates_fn, lock_dir: Path, timeout_sec: int):
    """Claim the first candidate whose flock succeeds NON-BLOCKINGLY.

    Selection and reservation are one atomic step: a busy box fails the
    try-lock and we move to the next candidate, so concurrent legs fan
    out across the pool instead of herding onto one "best" box. When
    every candidate is held (or none is eligible), re-snapshot the fleet
    and retry every 60s until `timeout_sec` — the pool is operator-managed
    and a box may come online mid-run.

    Yields the claimed instance name; the lock FD stays open until exit.
    """
    lock_dir.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout_sec
    chosen: str | None = None
    fp = None
    while True:
        names = candidates_fn()
        for name in names:
            if "/" in name or name in {"", ".", ".."}:
                raise ValueError(f"invalid Brev instance name for lock file: {name!r}")
            lock_path = lock_dir / f"{name}.lock"
            candidate_fp = lock_path.open("a+")
            try:
                fcntl.flock(candidate_fp.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                candidate_fp.close()
                if exc.errno not in (errno.EACCES, errno.EAGAIN):
                    raise
                continue
            chosen, fp = name, candidate_fp
            print(f"[run-leg] selected instance: {name} (lock acquired: {lock_path})",
                  flush=True)
            break
        if chosen:
            break
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise LockTimeoutError(
                f"no eligible pool box became free before timeout "
                f"(last candidates: {', '.join(names) or 'none'})"
            )
        print(
            f"[run-leg] all candidates busy or none eligible "
            f"({', '.join(names) or 'no RUNNING hardware match'}); "
            f"retrying in 60s ({int(remaining)}s remaining)",
            flush=True,
        )
        time.sleep(min(60, remaining))
    try:
        yield chosen
    finally:
        fcntl.flock(fp.fileno(), fcntl.LOCK_UN)
        fp.close()
        print(f"[run-leg] lock released: {chosen}", flush=True)


def _process_group_exists(pgid: int) -> bool:
    """Return whether any process still belongs to *pgid*."""
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # We own the spawned group in normal operation. If permissions ever
        # change, fail safe by treating an unprobeable group as still alive.
        return True
    except OSError as exc:
        print(f"[run-leg] process-group probe failed for {pgid}: {exc!r}", flush=True)
        return True
    return True


def _process_start_ticks(pid: int) -> str | None:
    """Return Linux /proc start ticks, which disambiguate PID reuse."""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
    except (FileNotFoundError, ProcessLookupError, PermissionError, OSError):
        return None
    closing_paren = stat.rfind(")")
    if closing_paren < 0:
        return None
    fields_from_state = stat[closing_paren + 2 :].split()
    # /proc/<pid>/stat field 3 is the first token after the command; starttime
    # is field 22, therefore index 19 in this tail.
    return fields_from_state[19] if len(fields_from_state) > 19 else None


def _registry_environment_groups(registry_path: Path) -> set[int]:
    """Find every process group carrying this invocation's unique marker."""
    expected_env = (
        f"{TRANSPORT_PGID_REGISTRY_ENV}={registry_path}".encode()
    )
    try:
        proc_entries = Path("/proc").iterdir()
    except OSError:
        return set()
    groups: set[int] = set()
    for entry in proc_entries:
        if not entry.name.isdigit():
            continue
        try:
            stat = (entry / "stat").read_text()
            closing_paren = stat.rfind(")")
            fields_from_state = stat[closing_paren + 2 :].split()
            member_pgid = int(fields_from_state[2])
            environ = (entry / "environ").read_bytes().split(b"\0")
        except (
            FileNotFoundError,
            ProcessLookupError,
            PermissionError,
            OSError,
            ValueError,
            IndexError,
        ):
            continue
        if expected_env in environ and _process_group_exists(member_pgid):
            groups.add(member_pgid)
    return groups


def _registered_transport_groups(registry_path: Path | None) -> list[int]:
    """Return still-live, identity-matched detached Brev transport PGIDs."""
    if registry_path is None:
        return []
    try:
        lines = registry_path.read_text().splitlines()
    except (FileNotFoundError, PermissionError, OSError):
        return []

    # Scanning the unique inherited environment marker catches grandchildren
    # that create another session internally (for example a CLI spawning its
    # own setsid ssh), not only the direct PGIDs written by brev_env.py.
    groups = _registry_environment_groups(registry_path)
    for line in lines:
        try:
            pid_text, expected_start = line.split(maxsplit=1)
            pid = int(pid_text)
        except (TypeError, ValueError):
            continue
        if not _process_group_exists(pid):
            continue
        leader_matches = _process_start_ticks(pid) == expected_start
        if leader_matches:
            groups.add(pid)
    return sorted(groups)


def _signal_registered_transport_groups(
    registry_path: Path | None,
    sig: signal.Signals,
    exclude_pgid: int | None = None,
) -> None:
    for pgid in _registered_transport_groups(registry_path):
        if pgid == exclude_pgid:
            continue
        try:
            os.killpg(pgid, sig)
        except ProcessLookupError:
            pass
        except OSError as exc:
            print(
                f"[run-leg] {sig.name} delivery to transport PGID {pgid} "
                f"failed: {exc!r}",
                flush=True,
            )


def _wait_for_process_group_exit(
    proc: subprocess.Popen,
    pgid: int,
    grace_sec: float,
    registry_path: Path | None = None,
) -> bool:
    """Wait for Harbor's group and every registered transport group to exit."""
    deadline = time.monotonic() + max(grace_sec, 0)
    try:
        proc.wait(timeout=max(grace_sec, 0))
    except subprocess.TimeoutExpired:
        pass
    except OSError as exc:
        print(f"[run-leg] wait for Harbor leader failed: {exc!r}", flush=True)

    # The Harbor leader can exit while a `brev exec`/SSH descendant remains in
    # its original group. Do not report successful cancellation until PGID 0
    # probing says the whole group is gone.
    while (
        _process_group_exists(pgid)
        or _registered_transport_groups(registry_path)
    ):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        time.sleep(min(PROCESS_GROUP_POLL_INTERVAL_SEC, remaining))
    return True


def _signal_process_group_and_wait(
    proc: subprocess.Popen,
    pgid: int,
    sig: signal.Signals,
    grace_sec: float,
    registry_path: Path | None = None,
) -> bool:
    """Signal Harbor plus detached transports and wait a bounded time."""
    try:
        os.killpg(pgid, sig)
    except ProcessLookupError:
        # The Harbor leader/group won the race. Detached registered transport
        # sessions can still exist, so continue through the combined probe.
        with contextlib.suppress(OSError):
            proc.poll()
    except OSError as exc:
        print(f"[run-leg] {sig.name} delivery failed: {exc!r}", flush=True)
    _signal_registered_transport_groups(registry_path, sig, exclude_pgid=pgid)

    return _wait_for_process_group_exit(
        proc, pgid, grace_sec, registry_path
    )


def _cancel_process_tree(
    proc: subprocess.Popen,
    pgid: int,
    registry_path: Path,
) -> bool:
    """Escalate INT → TERM → KILL across Harbor and detached transports."""
    exited = _signal_process_group_and_wait(
        proc,
        pgid,
        signal.SIGINT,
        HARBOR_SIGINT_GRACE_SEC,
        registry_path,
    )
    if not exited:
        print(
            "[run-leg] Harbor tree did not exit after SIGINT; escalating to SIGTERM",
            flush=True,
        )
        exited = _signal_process_group_and_wait(
            proc,
            pgid,
            signal.SIGTERM,
            HARBOR_SIGTERM_GRACE_SEC,
            registry_path,
        )
    if not exited:
        print(
            "[run-leg] Harbor tree did not exit after SIGTERM; escalating to SIGKILL",
            flush=True,
        )
        exited = _signal_process_group_and_wait(
            proc,
            pgid,
            signal.SIGKILL,
            HARBOR_SIGKILL_GRACE_SEC,
            registry_path,
        )
    return exited


def run_command(cmd: list[str], env: dict[str, str], timeout_sec: int) -> int:
    print(f"[run-leg] exec: {' '.join(cmd)}", flush=True)
    registry_fd, registry_name = tempfile.mkstemp(
        prefix="skill-eval-transport-pgids-",
    )
    os.close(registry_fd)
    registry_path = Path(registry_name)
    child_env = env.copy()
    child_env[TRANSPORT_PGID_REGISTRY_ENV] = str(registry_path)
    proc: subprocess.Popen | None = None
    pgid: int | None = None
    pending_signal: int | None = None
    cleanup_started = False
    previous_handlers: dict[signal.Signals, object] = {}

    def forward_external_signal(signum, _frame):  # noqa: ANN001
        nonlocal cleanup_started, pending_signal
        # Install before Popen to close the parent-signal race. If a signal
        # lands while Popen is still constructing the child, remember it and
        # start teardown immediately after Popen returns a usable handle.
        if proc is None or pgid is None:
            pending_signal = signum
            return
        if cleanup_started:
            return
        cleanup_started = True
        raise _RunCommandInterrupted(signum)

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            previous_handlers[sig] = signal.getsignal(sig)
            signal.signal(sig, forward_external_signal)
        except ValueError:
            # Defensive for library callers that invoke run_command off the
            # main thread. The production wrapper always runs on MainThread.
            for installed_sig, previous in previous_handlers.items():
                with contextlib.suppress(ValueError):
                    signal.signal(installed_sig, previous)
            previous_handlers.clear()
            break

    try:
        try:
            proc = subprocess.Popen(
                cmd,
                cwd=str(REPO_ROOT),
                env=child_env,
                start_new_session=True,
            )
            # start_new_session=True makes the Harbor leader's PID its PGID.
            # Preserve it now so a leader exit cannot hide descendants.
            pgid = proc.pid
            if pending_signal is not None:
                cleanup_started = True
                raise _RunCommandInterrupted(pending_signal)

            try:
                rc = proc.wait(timeout=timeout_sec)
            except subprocess.TimeoutExpired:
                cleanup_started = True
                outcome = 124
                reason = f"outer timeout after {timeout_sec}s"
            else:
                if rc < 0:
                    cleanup_started = True
                    outcome = 128 + abs(rc)
                    try:
                        signal_name = signal.Signals(abs(rc)).name
                    except ValueError:
                        signal_name = str(abs(rc))
                    reason = f"Harbor exited from signal {signal_name}"
                else:
                    detached = _registered_transport_groups(registry_path)
                    if not detached:
                        cleanup_started = True
                        return rc
                    cleanup_started = True
                    outcome = 124
                    reason = (
                        "Harbor exited while detached transport groups remained: "
                        + ", ".join(map(str, detached))
                    )
        except _RunCommandInterrupted as exc:
            cleanup_started = True
            outcome = 128 + exc.signum
            reason = f"external {signal.Signals(exc.signum).name}"

        if proc is None or pgid is None:
            return outcome

        cleanup_started = True
        print(
            f"[run-leg] {reason}; "
            "requesting graceful Harbor cancellation with SIGINT",
            flush=True,
        )
        # Ignore repeated workflow signals while bounded cleanup owns the
        # process tree. A later SIGKILL remains the unavoidable hard ceiling.
        for sig in previous_handlers:
            signal.signal(sig, signal.SIG_IGN)
        exited = _cancel_process_tree(proc, pgid, registry_path)
        if not exited:
            print(
                "[run-leg] Harbor tree could not be reaped after SIGKILL; "
                "preserving primary outcome",
                flush=True,
            )
        return outcome
    finally:
        for sig, previous in previous_handlers.items():
            with contextlib.suppress(ValueError):
                signal.signal(sig, previous)
        registry_path.unlink(missing_ok=True)


def latest_reward(
    results_root: Path,
    include_task_name: str,
    started_at: float | None = None,
) -> str | None:
    matches = list(results_root.glob(f"*/{include_task_name}__*/verifier/reward.txt"))
    if started_at is not None:
        matches = [p for p in matches if p.stat().st_mtime >= started_at]
    if not matches:
        return None
    latest = max(matches, key=lambda p: p.stat().st_mtime)
    return latest.read_text().strip()


def _coordinator_env_id() -> str | None:
    """Brev env id of the COORDINATOR host — the box running `harbor view`.

    Never derive this from `brev ls`: that yields a per-trial instance id,
    and the resulting URL points at a subdomain with no viewer behind it.
    """
    env_id = os.environ.get("BREV_ENV_ID", "").strip()
    if env_id:
        return env_id
    try:
        for line in Path("/etc/environment").read_text().splitlines():
            key, _, value = line.partition("=")
            if key.strip() == "BREV_ENV_ID":
                return value.strip().strip('"').strip("'") or None
    except OSError:
        pass
    return None


def trace_url(result_json: Path, job_name: str) -> str | None:
    """Harbor viewer deep-link for one finished trial.

    Every segment is read from the trial's own result.json, so the link
    cannot drift from what the viewer indexes. `task_name` in particular is
    Harbor's fully-qualified name (`nvidia-vss/<dataset>-step-N`), NOT the
    `--include-task-name` filter (`step-N`) that selects the task here — the
    viewer resolves its task route by the former. A bare `step-N` matches
    nothing and renders a BLANK PAGE rather than a 404, because the viewer
    is a client-side SPA where every route returns the same HTTP 200 shell.
    That failure mode is indistinguishable from missing trace data, which is
    why the URL is built here instead of being assembled by hand.
    """
    env_id = _coordinator_env_id()
    if not env_id:
        return None
    try:
        data = json.loads(result_json.read_text())
    except (OSError, ValueError):
        return None
    agent_info = data.get("agent_info") or {}
    model_info = agent_info.get("model_info") or {}
    parts = [
        data.get("source"),
        agent_info.get("name"),
        model_info.get("provider"),
        model_info.get("name"),
        data.get("task_name"),
    ]
    if not all(parts):
        return None
    # safe="" so the slashes inside <model> and <task> encode as %2F — the
    # viewer expects them as single path segments, not extra path levels.
    encoded = "/".join(urllib.parse.quote(str(part), safe="") for part in parts)
    return f"https://harbor-{env_id}.brevlab.com/jobs/{job_name}/tasks/{encoded}"


def publish_trace(
    results_root: Path,
    invocation: HarborInvocation,
    started_at: float,
    leg_slug: str,
    run_id: str,
) -> str | None:
    """Copy a finished trial into the viewer root and record its trace URL.

    Returns None when the trial produced no result.json (errored or timed
    out before the verifier ran) — such a step has no trace to link.
    """
    matches = [
        path.parent
        for path in results_root.glob(
            f"*/{invocation.include_task_name}__*/result.json"
        )
        if path.stat().st_mtime >= started_at
    ]
    if not matches:
        return None
    trial_dir = max(matches, key=lambda path: path.stat().st_mtime)
    date_dir = trial_dir.parent
    job_name = f"{leg_slug}__{run_id}__{date_dir.name}"
    viewer_job = VIEWER_ROOT / job_name
    viewer_job.mkdir(parents=True, exist_ok=True)
    # Copy (never move) the date dir's *contents*: the workflow's "Collect
    # results" step runs after this and tars results_root for the artifact,
    # and copying the dir itself would nest a later trial under
    # <job>/<date>/ where the viewer cannot see it.
    shutil.copytree(date_dir, viewer_job, dirs_exist_ok=True)
    url = trace_url(trial_dir / "result.json", job_name)
    if url:
        with (results_root / "trace-urls.tsv").open("a") as handle:
            handle.write(
                f"{invocation.include_task_name}\t{trial_dir.name}\t{url}\n"
            )
        print(
            f"[run-leg] trace: {invocation.include_task_name} -> {url}",
            flush=True,
        )
    return url


def _reward_value(reward: str | None) -> float:
    if reward is None:
        return 0.0
    try:
        return float(reward)
    except ValueError:
        return 0.0


def _safe_part(value: str) -> str:
    return SAFE_PART_RE.sub("_", value).strip("_") or "unknown"


def write_skip_markers(
    scratch: Path,
    spec_stem: str,
    platform: str,
    failed_step: int,
    reward: str | None,
    step_count: int,
) -> None:
    scratch.mkdir(parents=True, exist_ok=True)
    stem = _safe_part(spec_stem or "spec")
    plat = _safe_part(platform or "platform")
    reward_text = reward if reward is not None else "missing"
    for step in range(failed_step + 1, step_count + 1):
        marker = scratch / f"skipped-{stem}-{plat}-step-{step}.txt"
        marker.write_text(
            f"skipped (prior-step fail, step={failed_step} reward={reward_text})\n"
        )
        print(f"[run-leg] wrote skip marker: {marker}", flush=True)


def record_machine(
    results_root: Path, instance: str, leg_slug: str, run_id: str
) -> None:
    """Persist the (machine, run, leg) triple the moment the box is claimed.

    This is the only point in the pipeline where all three coexist, and
    ``run_leg.py`` runs as a Bash *tool call* inside the outer agent session —
    so its stdout is swallowed by the SDK and never reaches the CI job log.
    Absent this, the box a leg ran on is unrecoverable after the run
    (``BREV_INSTANCE`` lives only in the Harbor child's env; ``result.json``,
    the saved trajectory, and the results artifact all omit it).

    Two best-effort writes, independent of stdout capture:
      * ``results_root/machine.txt`` — tarred into the per-leg results artifact
        (run/slug-keyed), and dashboard-ingestible.
      * ``$GITHUB_STEP_SUMMARY`` — surfaces in the Actions run UI + API, with
        far longer retention than the 7-day artifact.

    Never raises: a debug signal must not fail the leg.
    """
    def _say(msg: str) -> None:
        # Even the diagnostics must not raise: a broken or unencodable stdout
        # (e.g. the outer agent closed the pipe) would otherwise propagate out
        # of a sink's except block and fail the leg.
        try:
            print(msg, flush=True)
        except Exception:  # noqa: BLE001
            pass

    _say(f"[run-leg] machine: {instance} leg={leg_slug} run={run_id}")
    # Broad excepts + explicit utf-8: a non-UTF-8 runner locale would otherwise
    # raise UnicodeEncodeError (not an OSError) and fail the leg. Nothing here
    # may propagate.
    try:
        (results_root / "machine.txt").write_text(
            f"{instance}\t{leg_slug}\t{run_id}\n", encoding="utf-8"
        )
    except Exception as exc:  # noqa: BLE001
        _say(f"[run-leg] machine.txt write failed: {exc!r}")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        try:
            with open(summary, "a", encoding="utf-8") as handle:
                handle.write(
                    f"- leg `{leg_slug}` -> **{instance}** (run {run_id})\n"
                )
        except Exception as exc:  # noqa: BLE001
            _say(f"[run-leg] step-summary write failed: {exc!r}")


def run_invocations(
    invocations: list[HarborInvocation],
    instance: str,
    results_root: Path,
    scratch: Path,
    spec_stem: str,
    platform: str,
    harbor_timeout_sec: int,
    work_deadline: float | None = None,
) -> int:
    env = harbor_env(instance)
    agent = os.environ.get("EVAL_AGENT", "claude-code")
    # Reject unknown agents loudly — otherwise a typo (e.g. "Codex") would
    # silently fall through to the claude-code path and be indistinguishable
    # from a real claude-code run in the logs.
    if agent not in ("claude-code", "codex", "nemoclaw"):
        print(
            f"FATAL: unsupported EVAL_AGENT {agent!r} "
            "(expected claude-code | codex | nemoclaw)",
            file=sys.stderr,
        )
        return 1
    model = os.environ.get("ANTHROPIC_MODEL", "")
    base_url = os.environ.get("ANTHROPIC_BASE_URL", "")
    if not base_url:
        print("FATAL: ANTHROPIC_BASE_URL not set", file=sys.stderr)
        return 1
    if agent == "codex":
        model = os.environ.get("CODEX_MODEL", "")
        if not model:
            print("FATAL: CODEX_MODEL not set (required for EVAL_AGENT=codex)",
                  file=sys.stderr)
            return 1
        anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not anthropic_key:
            print("FATAL: ANTHROPIC_API_KEY not set (required for EVAL_AGENT=codex)",
                  file=sys.stderr)
            return 1
        env["OPENAI_API_KEY"] = anthropic_key
        env["OPENAI_BASE_URL"] = _api_base_v1(base_url)
    if not model:
        print("FATAL: ANTHROPIC_MODEL not set", file=sys.stderr)
        return 1

    results_root.mkdir(parents=True, exist_ok=True)
    # skills-eval.yml passes --results-root as <...>/results/<slug>/<run_id>;
    # the env vars are the authoritative source when the agent exports them.
    leg_slug = os.environ.get("EVAL_SLUG") or results_root.parent.name
    run_id = os.environ.get("GITHUB_RUN_ID") or results_root.name
    # Record which box this leg locked BEFORE the first Harbor invocation, so a
    # leg that dies inside BrevEnvironment.start() (e.g. a disk-full box) still
    # leaves a trail pointing at the machine to inspect.
    record_machine(results_root, instance, leg_slug, run_id)
    skipped_after: dict[str, int] = {}
    overall_rc = 0

    for invocation in invocations:
        if (
            invocation.step_index is not None
            and invocation.chain_key in skipped_after
            and invocation.step_index > skipped_after[invocation.chain_key]
        ):
            continue

        if work_deadline is not None:
            remaining = work_deadline - time.monotonic()
            required = invocation_reserve_sec(harbor_timeout_sec)
            if remaining < required:
                print(
                    "[run-leg] whole-leg deadline leaves only "
                    f"{max(0, int(remaining))}s; refusing to start "
                    f"{invocation.include_task_name}, which requires "
                    f"{required}s including teardown",
                    flush=True,
                )
                if (
                    invocation.step_index is not None
                    and invocation.step_count is not None
                ):
                    write_skip_markers(
                        scratch,
                        spec_stem,
                        platform or invocation.chain_key,
                        invocation.step_index - 1,
                        "whole-leg-deadline",
                        invocation.step_count,
                    )
                return 124

        cmd = build_harbor_command(invocation, results_root, model, base_url, agent)
        started_at = time.time() - 1.0
        with phase(f"harbor:{invocation.include_task_name}"):
            rc = run_command(cmd, env, harbor_timeout_sec)
        # Publish before the rc checks below: a timed-out (rc=124) trial
        # returns early, and its partial trace is exactly what needs reading.
        try:
            publish_trace(results_root, invocation, started_at, leg_slug, run_id)
        except Exception as exc:  # noqa: BLE001
            # A trace link is reporting convenience; the verdict comes from
            # reward.txt. Never let a viewer-publish error fail the leg.
            print(f"[run-leg] trace publish failed: {exc!r}", flush=True)
        if rc != 0 and overall_rc == 0:
            overall_rc = rc

        if invocation.step_index is not None and invocation.step_count is not None:
            reward = latest_reward(results_root, invocation.include_task_name, started_at)
            reward_value = _reward_value(reward)
            print(
                f"[run-leg] {invocation.chain_key}/{invocation.include_task_name} "
                f"rc={rc} reward={reward if reward is not None else 'missing'}",
                flush=True,
            )
            if rc == 124 or rc >= 128 or reward_value < 1.0:
                write_skip_markers(
                    scratch,
                    spec_stem,
                    platform or invocation.chain_key,
                    invocation.step_index,
                    reward,
                    invocation.step_count,
                )
                skipped_after[invocation.chain_key] = invocation.step_index

        # An outer Harbor timeout is terminal for the entire locked leg, not
        # only a multi-step chain. Continuing could wipe/reuse the same Brev
        # box while descendants from the timed-out process are still settling.
        # For chained tasks the block above writes every applicable skip marker
        # before this return.
        if rc == 124 or rc >= 128:
            return rc

    return overall_rc


def parse_args(argv: list[str]) -> argparse.Namespace:
    run_id = os.environ.get("GITHUB_RUN_ID", "local")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--instance",
        default=os.environ.get("BREV_INSTANCE") or None,
        help="Operator override: pin the leg to this Brev instance instead "
             "of pool selection (still lock-guarded; waits if held)",
    )
    parser.add_argument("--dataset-root", required=True, type=Path, help="Per-leg generated dataset root")
    parser.add_argument("--results-root", required=True, type=Path, help="Per-leg Harbor results root")
    parser.add_argument(
        "--scratch",
        default=Path(f"/tmp/skill-eval/{run_id}"),
        type=Path,
        help="Per-run scratch root for skip marker files",
    )
    parser.add_argument("--spec-stem", default=os.environ.get("EVAL_SPEC_STEM", ""))
    parser.add_argument("--platform", default=os.environ.get("EVAL_PLATFORM", ""))
    parser.add_argument("--lock-dir", default=Path("/tmp/brev"), type=Path)
    parser.add_argument("--lock-timeout-sec", default=21000, type=int)
    parser.add_argument(
        "--harbor-timeout-sec",
        default=DEFAULT_HARBOR_TIMEOUT_SEC,
        type=int,
    )
    args = parser.parse_args(argv)
    try:
        validate_harbor_timeout_sec(args.harbor_timeout_sec)
    except ValueError as exc:
        parser.error(str(exc))
    return args


def _terminate(signum: int, _frame) -> None:
    """Turn SIGTERM into an unwinding exit so cleanup actually runs.

    Python converts only SIGINT into an exception; SIGTERM keeps SIG_DFL and
    terminates the interpreter without unwinding, so no `finally` and no
    context-manager exit fires. That matters here because `skills-eval.yml`
    sets `cancel-in-progress: true`: every push to a pull request SIGTERMs the
    in-flight legs, up to `max-parallel` of them at once. Without this, a
    cancelled leg never reaches the phase-timing write in main()'s finally, so
    it produces no artifact at all -- and a leg cancelled after an hour in the
    lock queue is one of the most informative things this feature can record.
    """
    raise SystemExit(128 + signum)


def main(argv: list[str] | None = None) -> int:
    actual_python = sys.version_info[:2]
    if actual_python != SKILL_EVAL_PYTHON_VERSION:
        expected = ".".join(map(str, SKILL_EVAL_PYTHON_VERSION))
        found = ".".join(map(str, actual_python))
        print(
            f"FATAL: run_leg requires Python {expected}.x; found {found}",
            file=sys.stderr,
        )
        return 1
    with contextlib.suppress(ValueError, OSError):
        # ValueError if not on the main thread; never fatal either way.
        signal.signal(signal.SIGTERM, _terminate)
    args = parse_args(argv or sys.argv[1:])
    # Instrumentation must not be able to fail the leg, and starting a thread
    # can: a runner at its thread limit raises RuntimeError here. Losing the
    # heartbeat costs visibility, while raising costs the whole leg, so this
    # degrades to no heartbeat and says so.
    heartbeat: threading.Thread | None = None
    stop_heartbeat: threading.Event | None = None
    try:
        heartbeat, stop_heartbeat = leg_timing.start_heartbeat()
    except Exception as exc:  # noqa: BLE001 - telemetry is never load-bearing
        leg_log(f"heartbeat unavailable ({exc!r}); leg continues without it")
    try:
        invocations = discover_invocations(args.dataset_root)
        print(f"[run-leg] discovered {len(invocations)} harbor invocation(s)", flush=True)
        for invocation in invocations:
            print(
                f"[run-leg] target: -p {invocation.harbor_root} "
                f"--include-task-name {invocation.include_task_name}",
                flush=True,
            )
        metadata = _read_dataset_metadata(args.dataset_root)
        work_deadline = resolve_work_deadline()
        required = invocation_reserve_sec(args.harbor_timeout_sec)
        max_lock_wait = int(work_deadline - time.monotonic() - required)
        if max_lock_wait <= 0:
            raise LegDeadlineError(
                "whole-leg deadline cannot fit one complete Harbor invocation"
            )
        effective_lock_timeout = min(args.lock_timeout_sec, max_lock_wait)
        # Pin precedence: CLI/--instance (incl. BREV_INSTANCE env default)
        # > SKILL_EVAL_LOCAL_GPU_INSTANCE (direct OpenShell runner)
        # > task.toml brev_instance > pool selection.
        local_pin = os.environ.get("SKILL_EVAL_LOCAL_GPU_INSTANCE", "").strip()
        pinned = args.instance or local_pin or metadata.get("brev_instance") or None
        if pinned:
            print(f"[run-leg] pinned instance: {pinned} (pool selection skipped)",
                  flush=True)
            candidates_fn = lambda: [pinned]  # noqa: E731
        else:
            candidates_fn = (  # noqa: E731
                lambda: pool_candidates(metadata, args.spec_stem)
            )
        # Timed either side of the lock: the wait for a free box is the leg's
        # least visible cost and the one the pickup analysis most needs split
        # out from the Harbor run itself.
        lock_wait_started = leg_timing.leg_elapsed()
        lock_acquired = False
        # The heartbeat's LABEL, not just the recorded interval. The lock wait
        # is the longest gap a leg can have -- 16 minutes in the run that
        # motivated this, and bounded only by lock_timeout_sec -- and it ran
        # entirely under the label "startup", so every tick during the one
        # phase the log most needed to name reported the wrong thing.
        outer_phase = leg_timing.current_phase()
        leg_timing.set_phase("lock-wait")
        try:
            with hold_pool_lock(
                candidates_fn, args.lock_dir, effective_lock_timeout
            ) as instance:
                lock_acquired = True
                leg_timing.record_phase(
                    "lock-wait", lock_wait_started, leg_timing.leg_elapsed()
                )
                # The wait is over the moment the lock is held; the Harbor
                # phases below set their own labels.
                leg_timing.set_phase(outer_phase)
                return run_invocations(
                    invocations,
                    instance,
                    args.results_root,
                    args.scratch,
                    args.spec_stem,
                    args.platform,
                    args.harbor_timeout_sec,
                    work_deadline,
                )
        except LockTimeoutError:
            leg_timing.record_phase(
                "lock-wait-timeout", lock_wait_started, leg_timing.leg_elapsed()
            )
            if effective_lock_timeout < args.lock_timeout_sec:
                raise LegDeadlineError(
                    "whole-leg deadline expired while reserving room for Harbor"
                )
            raise
        except BaseException:
            # Every exit from the lock gets a phase, including the ones nobody
            # expects: an invalid instance name raises ValueError, a bad lock
            # dir raises OSError. A leg that died selecting a box still spent
            # that time, and an artifact with no lock interval is
            # indistinguishable from one where the wait was zero.
            #
            # BaseException, not Exception, and the difference is the common
            # case rather than an exotic one. `skills-eval.yml` sets
            # cancel-in-progress, so a push cancels in-flight legs, and a
            # cancellation arriving during the lock wait raises
            # KeyboardInterrupt. Catching only Exception dropped the wait from
            # the artifact for precisely the legs that spent longest in it.
            if not lock_acquired:
                leg_timing.record_phase(
                    "lock-wait-failed", lock_wait_started, leg_timing.leg_elapsed()
                )
            raise
        finally:
            # Every exit restores the label, including the ones that raise past
            # the resets above. A stuck "lock-wait" would misreport the rest of
            # the leg for as long as it ran.
            leg_timing.set_phase(outer_phase)
    except LockTimeoutError:
        target = args.instance or f"pool ({args.platform or 'platform'})"
        print(f"BLOCKED: lock timeout on {target}", flush=True)
        return 75
    except LegDeadlineError as exc:
        print(f"FATAL: {exc}", file=sys.stderr)
        return 124
    except Exception as exc:  # noqa: BLE001
        print(f"FATAL: run_leg failed: {exc!r}", file=sys.stderr)
        return 1
    finally:
        if stop_heartbeat is not None:
            stop_heartbeat.set()
        # Joined, not just signalled. A daemon still writing to stdout while
        # the interpreter tears down its buffered streams is how a clean leg
        # turns into a fatal abort at exit.
        if heartbeat is not None:
            heartbeat.join(timeout=HEARTBEAT_SEC + 5)
        # This whole block runs on the return path of a leg that already has
        # its outcome. Nothing here may replace it.
        try:
            leg_timing.write_phase_timings(args.results_root)
        except Exception as exc:  # noqa: BLE001
            leg_log(f"phase timing write failed: {exc!r}")


if __name__ == "__main__":
    sys.exit(main())
