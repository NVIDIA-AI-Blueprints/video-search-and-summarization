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
import re
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.parse
from pathlib import Path

from distributed_lock import (
    Lease,
    LeaseError,
    LeaseGuard,
    LeaseLostError,
    PostgresLeaseClient,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
STEP_COUNT_RE = re.compile(r"^\s*step_count\s*=\s*(\d+)\s*$", re.MULTILINE)
DISTRIBUTED_RUNNER_RE = re.compile(
    r"^vss-skill-validator-distributed-[1-8]-runner-[1-4]$"
)
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


class RunCancelledError(RuntimeError):
    pass


def _handle_termination(signum, _frame) -> None:
    """Turn SIGTERM into normal unwinding so Harbor and leases are cleaned up."""
    raise RunCancelledError(f"received signal {signum}")


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


def build_harbor_command(
    invocation: HarborInvocation,
    results_root: Path,
    model: str,
    anthropic_base_url: str,
    agent: str = "claude-code",
) -> list[str]:
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
    else:
        raise ValueError(f"unsupported agent {agent!r} (expected claude-code | codex)")
    return [
        "uvx",
        "harbor",
        "run",
        "--environment-import-path",
        "envs.brev_env:BrevEnvironment",
        "-p",
        str(invocation.harbor_root),
        "--include-task-name",
        invocation.include_task_name,
        *agent_flags,
        "--environment-build-timeout-multiplier",
        "3.0",
        "--agent-timeout-multiplier",
        "6.0",
        "--verifier-timeout-multiplier",
        "3.0",
        "--max-retries",
        "0",
        "-n",
        "1",
        "--yes",
        "-o",
        str(results_root),
    ]


def harbor_env(instance: str, lease: Lease | None = None) -> dict[str, str]:
    env = os.environ.copy()
    # Harbor and its on-box agent never need database credentials.  The only
    # cross-boundary capability is the short-lived lease token for the exact
    # worker/generation selected by this wrapper.
    env.pop("GPU_LEASE_DATABASE_URL", None)
    env.pop("GPU_FENCE_DATABASE_URL", None)
    env.pop("GPU_LEASE_ADMIN_DATABASE_URL", None)
    env.pop("CI_GPU_LEASE_DATABASE_URL", None)
    workspace = env.get("GITHUB_WORKSPACE") or str(REPO_ROOT)
    skill_eval_path = str(Path(workspace) / ".github" / "skill-eval")
    pythonpath = env.get("PYTHONPATH", "")
    if skill_eval_path not in pythonpath.split(":"):
        pythonpath = f"{skill_eval_path}:{pythonpath}" if pythonpath else skill_eval_path
    env["PYTHONPATH"] = pythonpath
    env["PATH"] = f"{Path.home() / '.local' / 'bin'}:{env.get('PATH', '')}"
    env["BREV_INSTANCE"] = instance
    env["CLAUDE_CODE_DISABLE_THINKING"] = "1"
    for key in (
        "GPU_LEASE_GPU_ID",
        "GPU_LEASE_TOKEN",
        "GPU_LEASE_GENERATION",
        "GPU_WORKER_FENCE_REQUIRED",
    ):
        env.pop(key, None)
    if lease is not None:
        if lease.gpu_id != instance:
            raise LeaseError(
                f"lease worker {lease.gpu_id!r} does not match Harbor "
                f"instance {instance!r}"
            )
        env["GPU_LEASE_GPU_ID"] = lease.gpu_id
        env["GPU_LEASE_TOKEN"] = str(lease.token)
        env["GPU_LEASE_GENERATION"] = str(lease.generation)
        env["GPU_WORKER_FENCE_REQUIRED"] = "1"
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


@contextlib.contextmanager
def hold_distributed_pool_lock(
    candidates_fn,
    lock_dir: Path,
    timeout_sec: int,
    client: PostgresLeaseClient,
    heartbeat_sec: int,
):
    """Acquire a PostgreSQL lease plus a host-local defense-in-depth flock."""
    lock_dir.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout_sec
    last_names: list[str] = []
    while True:
        last_names = list(candidates_fn())
        remaining = list(last_names)
        lease = None
        fp = None
        while remaining:
            lease = client.try_acquire(remaining)
            if lease is None:
                break
            name = lease.gpu_id
            if "/" in name or name in {"", ".", ".."}:
                with contextlib.suppress(LeaseError):
                    client.release(lease)
                raise ValueError(f"invalid Brev instance name for lock file: {name!r}")
            lock_path = lock_dir / f"{name}.lock"
            candidate_fp = lock_path.open("a+")
            try:
                fcntl.flock(
                    candidate_fp.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB
                )
            except OSError as exc:
                candidate_fp.close()
                with contextlib.suppress(LeaseError):
                    client.release(lease)
                if exc.errno not in (errno.EACCES, errno.EAGAIN):
                    raise
                # A legacy process on this coordinator holds the local lock.
                # Exclude this worker for this snapshot instead of reacquiring
                # and releasing the same PostgreSQL row in a tight loop.
                remaining = [item for item in remaining if item != name]
                lease = None
                continue
            fp = candidate_fp
            break

        if lease is not None and fp is not None:
            break
        remaining_sec = deadline - time.monotonic()
        if remaining_sec <= 0:
            raise LockTimeoutError(
                "no eligible distributed GPU lease became free before timeout "
                f"(last candidates: {', '.join(last_names) or 'none'})"
            )
        print(
            f"[run-leg] all distributed candidates busy or unavailable "
            f"({', '.join(last_names) or 'no RUNNING hardware match'}); "
            f"retrying in 60s ({int(remaining_sec)}s remaining)",
            flush=True,
        )
        time.sleep(min(60, remaining_sec))

    guard = None
    try:
        guard = LeaseGuard(client, lease, heartbeat_sec).start()
        print(
            f"[run-leg] selected instance: {lease.gpu_id} "
            f"(PostgreSQL lease generation={lease.generation}, local flock held)",
            flush=True,
        )
        yield lease.gpu_id, guard
    finally:
        active_error = sys.exc_info()[0] is not None
        cleanup_error: BaseException | None = None
        if guard is not None:
            try:
                guard.close()
            except BaseException as exc:
                cleanup_error = exc
        try:
            released = client.release(lease)
            if not released:
                print(
                    f"[run-leg] lease already expired or reassigned: {lease.gpu_id}",
                    flush=True,
                )
        except LeaseError as exc:
            print(f"[run-leg] lease release deferred to TTL: {exc}", file=sys.stderr)
            cleanup_error = cleanup_error or exc
        finally:
            fcntl.flock(fp.fileno(), fcntl.LOCK_UN)
            fp.close()
        print(f"[run-leg] distributed lock released: {lease.gpu_id}", flush=True)
        if cleanup_error is not None and not active_error:
            raise cleanup_error


def _terminate_process_group(proc: subprocess.Popen) -> None:
    try:
        os.killpg(proc.pid, signal.SIGTERM)
        proc.wait(timeout=20)
    except (ProcessLookupError, subprocess.TimeoutExpired):
        with contextlib.suppress(ProcessLookupError):
            os.killpg(proc.pid, signal.SIGKILL)
        proc.wait()


def run_command(
    cmd: list[str],
    env: dict[str, str],
    timeout_sec: int,
    health_check=None,
) -> int:
    print(f"[run-leg] exec: {' '.join(cmd)}", flush=True)
    proc = subprocess.Popen(cmd, cwd=str(REPO_ROOT), env=env, start_new_session=True)
    deadline = time.monotonic() + timeout_sec
    while True:
        try:
            if health_check is not None:
                health_check()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                print(
                    f"[run-leg] timeout after {timeout_sec}s; terminating harbor",
                    flush=True,
                )
                _terminate_process_group(proc)
                return 124
            return proc.wait(timeout=min(5, remaining))
        except subprocess.TimeoutExpired:
            continue
        except BaseException:
            print(
                "[run-leg] lease health check failed; terminating harbor",
                file=sys.stderr,
                flush=True,
            )
            _terminate_process_group(proc)
            raise


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


def run_invocations(
    invocations: list[HarborInvocation],
    instance: str,
    results_root: Path,
    scratch: Path,
    spec_stem: str,
    platform: str,
    harbor_timeout_sec: int,
    health_check=None,
    lease: Lease | None = None,
) -> int:
    env = harbor_env(instance, lease)
    agent = os.environ.get("EVAL_AGENT", "claude-code")
    # Reject unknown agents loudly — otherwise a typo (e.g. "Codex") would
    # silently fall through to the claude-code path and be indistinguishable
    # from a real claude-code run in the logs.
    if agent not in ("claude-code", "codex"):
        print(f"FATAL: unsupported EVAL_AGENT {agent!r} (expected claude-code | codex)",
              file=sys.stderr)
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
    skipped_after: dict[str, int] = {}
    overall_rc = 0

    for invocation in invocations:
        if (
            invocation.step_index is not None
            and invocation.chain_key in skipped_after
            and invocation.step_index > skipped_after[invocation.chain_key]
        ):
            continue

        cmd = build_harbor_command(invocation, results_root, model, base_url, agent)
        started_at = time.time() - 1.0
        rc = run_command(cmd, env, harbor_timeout_sec, health_check)
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
            if rc == 124 or reward_value < 1.0:
                write_skip_markers(
                    scratch,
                    spec_stem,
                    platform or invocation.chain_key,
                    invocation.step_index,
                    reward,
                    invocation.step_count,
                )
                skipped_after[invocation.chain_key] = invocation.step_index
                if rc == 124:
                    return 124

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
    parser.add_argument("--harbor-timeout-sec", default=7800, type=int)
    parser.add_argument(
        "--lock-mode",
        choices=("local", "postgres"),
        default=os.environ.get("GPU_LEASE_MODE", "local"),
        help="local preserves the single-coordinator flock; postgres is "
             "required before multiple coordinator hosts are activated",
    )
    parser.add_argument(
        "--lease-database-url",
        default=os.environ.get("GPU_LEASE_DATABASE_URL", ""),
        help="PostgreSQL DSN (prefer GPU_LEASE_DATABASE_URL in the protected env)",
    )
    parser.add_argument(
        "--coordinator-id",
        default=os.environ.get("COORDINATOR_ID", socket.gethostname()),
        help="Stable host/runner identity; run and PID are appended automatically",
    )
    parser.add_argument("--lease-ttl-sec", default=90, type=int)
    parser.add_argument("--lease-heartbeat-sec", default=20, type=int)
    return parser.parse_args(argv)


def validate_coordinator_lock_config(args: argparse.Namespace) -> None:
    """Distributed GitHub runners must never fall back to host-local locking."""
    runner_name = os.environ.get("RUNNER_NAME", "")
    if not DISTRIBUTED_RUNNER_RE.fullmatch(runner_name):
        return
    if args.lock_mode != "postgres":
        raise LeaseError(
            f"distributed runner {runner_name} requires GPU_LEASE_MODE=postgres"
        )
    if args.coordinator_id != runner_name:
        raise LeaseError(
            f"distributed runner identity mismatch: COORDINATOR_ID="
            f"{args.coordinator_id!r}, expected {runner_name!r}"
        )
    parsed = urllib.parse.urlsplit(args.lease_database_url)
    sslmode = urllib.parse.parse_qs(parsed.query).get("sslmode", [""])[0]
    if (
        parsed.scheme not in {"postgres", "postgresql"}
        or not parsed.hostname
        or not parsed.path.strip("/")
        or sslmode != "verify-full"
    ):
        raise LeaseError(
            "distributed runners require a managed PostgreSQL DSN with "
            "host, database, and sslmode=verify-full"
        )


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    try:
        validate_coordinator_lock_config(args)
        invocations = discover_invocations(args.dataset_root)
        print(f"[run-leg] discovered {len(invocations)} harbor invocation(s)", flush=True)
        for invocation in invocations:
            print(
                f"[run-leg] target: -p {invocation.harbor_root} "
                f"--include-task-name {invocation.include_task_name}",
                flush=True,
            )
        metadata = _read_dataset_metadata(args.dataset_root)
        # Pin precedence: CLI/--instance (incl. BREV_INSTANCE env default)
        # > task.toml brev_instance > pool selection.
        pinned = args.instance or metadata.get("brev_instance") or None
        if pinned:
            print(f"[run-leg] pinned instance: {pinned} (pool selection skipped)",
                  flush=True)
            candidates_fn = lambda: [pinned]  # noqa: E731
        else:
            candidates_fn = (  # noqa: E731
                lambda: pool_candidates(metadata, args.spec_stem)
            )
        if args.lock_mode == "local":
            with hold_pool_lock(
                candidates_fn, args.lock_dir, args.lock_timeout_sec
            ) as instance:
                return run_invocations(
                    invocations,
                    instance,
                    args.results_root,
                    args.scratch,
                    args.spec_stem,
                    args.platform,
                    args.harbor_timeout_sec,
                )

        if not args.lease_database_url:
            raise LeaseError(
                "GPU_LEASE_MODE=postgres requires GPU_LEASE_DATABASE_URL"
            )
        run_id = os.environ.get("GITHUB_RUN_ID", "local")
        owner_id = f"{args.coordinator_id}:{run_id}:{os.getpid()}"
        client = PostgresLeaseClient(
            args.lease_database_url,
            owner_id,
            ttl_sec=args.lease_ttl_sec,
        )
        with hold_distributed_pool_lock(
            candidates_fn,
            args.lock_dir,
            args.lock_timeout_sec,
            client,
            args.lease_heartbeat_sec,
        ) as (instance, guard):
            return run_invocations(
                invocations,
                instance,
                args.results_root,
                args.scratch,
                args.spec_stem,
                args.platform,
                args.harbor_timeout_sec,
                health_check=guard.raise_if_lost,
                lease=guard.lease,
            )
    except LockTimeoutError:
        target = args.instance or f"pool ({args.platform or 'platform'})"
        print(f"BLOCKED: lock timeout on {target}", flush=True)
        return 75
    except LeaseLostError as exc:
        print(f"BLOCKED: distributed lease lost: {exc}", flush=True)
        return 75
    except LeaseError as exc:
        print(f"BLOCKED: distributed lease unavailable: {exc}", flush=True)
        return 75
    except RunCancelledError as exc:
        print(f"CANCELLED: {exc}", flush=True)
        return 130
    except Exception as exc:  # noqa: BLE001
        print(f"FATAL: run_leg failed: {exc!r}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, _handle_termination)
    sys.exit(main())
