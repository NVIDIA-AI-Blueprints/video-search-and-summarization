#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Run one skills-eval leg under a process-held Brev box lock.

The LLM selects a vss-eval-* instance, but this wrapper owns the
per-instance flock and keeps the lock file descriptor open while Harbor
runs. That makes the mutex a real kernel lock instead of a shell-FD
convention spread across multiple agent tool calls.
"""
from __future__ import annotations

import argparse
import contextlib
import dataclasses
import errno
import fcntl
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
STEP_COUNT_RE = re.compile(r"^\s*step_count\s*=\s*(\d+)\s*$", re.MULTILINE)
SAFE_PART_RE = re.compile(r"[^A-Za-z0-9_-]+")


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
) -> list[str]:
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
        "-a",
        "claude-code",
        "--model",
        model,
        "--ak",
        f"api_base={_api_base_v1(anthropic_base_url)}",
        "--ae",
        "CLAUDE_CODE_DISABLE_THINKING=1",
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


def harbor_env(instance: str) -> dict[str, str]:
    env = os.environ.copy()
    workspace = env.get("GITHUB_WORKSPACE") or str(REPO_ROOT)
    skill_eval_path = str(Path(workspace) / ".github" / "skill-eval")
    pythonpath = env.get("PYTHONPATH", "")
    if skill_eval_path not in pythonpath.split(":"):
        pythonpath = f"{skill_eval_path}:{pythonpath}" if pythonpath else skill_eval_path
    env["PYTHONPATH"] = pythonpath
    env["PATH"] = f"{Path.home() / '.local' / 'bin'}:{env.get('PATH', '')}"
    env["BREV_INSTANCE"] = instance
    env["CLAUDE_CODE_DISABLE_THINKING"] = "1"
    return env


@contextlib.contextmanager
def hold_box_lock(lock_dir: Path, instance: str, timeout_sec: int):
    if "/" in instance or instance in {"", ".", ".."}:
        raise ValueError(f"invalid Brev instance name for lock file: {instance!r}")
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_path = lock_dir / f"{instance}.lock"
    deadline = time.monotonic() + timeout_sec
    with lock_path.open("a+") as fp:
        while True:
            try:
                fcntl.flock(fp.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                print(f"[run-leg] lock acquired: {lock_path}", flush=True)
                break
            except OSError as exc:
                if exc.errno not in (errno.EACCES, errno.EAGAIN):
                    raise
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise LockTimeoutError(f"lock timeout on {instance}") from exc
                print(
                    f"[run-leg] waiting for lock {lock_path} "
                    f"({int(remaining)}s remaining)",
                    flush=True,
                )
                time.sleep(min(60, remaining))
        try:
            yield lock_path
        finally:
            fcntl.flock(fp.fileno(), fcntl.LOCK_UN)
            print(f"[run-leg] lock released: {lock_path}", flush=True)


def run_command(cmd: list[str], env: dict[str, str], timeout_sec: int) -> int:
    print(f"[run-leg] exec: {' '.join(cmd)}", flush=True)
    proc = subprocess.Popen(cmd, cwd=str(REPO_ROOT), env=env, start_new_session=True)
    try:
        return proc.wait(timeout=timeout_sec)
    except subprocess.TimeoutExpired:
        print(f"[run-leg] timeout after {timeout_sec}s; terminating harbor", flush=True)
        try:
            os.killpg(proc.pid, signal.SIGTERM)
            proc.wait(timeout=30)
        except (ProcessLookupError, subprocess.TimeoutExpired):
            with contextlib.suppress(ProcessLookupError):
                os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()
        return 124


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
) -> int:
    env = harbor_env(instance)
    model = os.environ.get("ANTHROPIC_MODEL", "")
    base_url = os.environ.get("ANTHROPIC_BASE_URL", "")
    if not model:
        print("FATAL: ANTHROPIC_MODEL not set", file=sys.stderr)
        return 1
    if not base_url:
        print("FATAL: ANTHROPIC_BASE_URL not set", file=sys.stderr)
        return 1

    results_root.mkdir(parents=True, exist_ok=True)
    skipped_after: dict[str, int] = {}
    overall_rc = 0

    for invocation in invocations:
        if (
            invocation.step_index is not None
            and invocation.chain_key in skipped_after
            and invocation.step_index > skipped_after[invocation.chain_key]
        ):
            continue

        cmd = build_harbor_command(invocation, results_root, model, base_url)
        started_at = time.time() - 1.0
        rc = run_command(cmd, env, harbor_timeout_sec)
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
    parser.add_argument("--instance", required=True, help="Selected vss-eval-* Brev instance")
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
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    try:
        invocations = discover_invocations(args.dataset_root)
        print(f"[run-leg] discovered {len(invocations)} harbor invocation(s)", flush=True)
        for invocation in invocations:
            print(
                f"[run-leg] target: -p {invocation.harbor_root} "
                f"--include-task-name {invocation.include_task_name}",
                flush=True,
            )
        with hold_box_lock(args.lock_dir, args.instance, args.lock_timeout_sec):
            return run_invocations(
                invocations,
                args.instance,
                args.results_root,
                args.scratch,
                args.spec_stem,
                args.platform,
                args.harbor_timeout_sec,
            )
    except LockTimeoutError:
        print(f"BLOCKED: lock timeout on {args.instance}", flush=True)
        return 75
    except Exception as exc:  # noqa: BLE001
        print(f"FATAL: run_leg failed: {exc!r}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
