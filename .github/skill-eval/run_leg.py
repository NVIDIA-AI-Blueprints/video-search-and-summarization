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


class BoxUnreachableError(RuntimeError):
    """The box-side lock couldn't be reached (`brev exec` kept failing), so the
    box is unreachable/degraded. Fail fast and let the agent rescore to a
    different box instead of burning the whole lock budget waiting on a dead box
    (e.g. an instance that shows RUNNING in `brev ls` but is SSH-unreachable)."""
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


# ---------------------------------------------------------------------------
# Box-side mutex.
#
# The host-local flock below only serializes legs *on the same runner host*.
# When the vss-skill-eval-runner label spans multiple hosts (it now does),
# two legs on different hosts each flock their own local file, both see it
# free, and both drive the *same* brev box -> docker/port collision + mutual
# start() wipes (the "flaky media pipeline" root cause). To coordinate across
# hosts we ALSO take a claim marker *on the box itself* via `brev exec`, so
# every runner competing for that box contends on one real lock.
#
# The marker is a directory (mkdir is atomic on the box fs -> race-free claim),
# tagged with an owner id + unix timestamp. A crashed holder's marker is reaped
# only after BOX_LOCK_STALE_SEC, which is set WELL above the max per-leg trial
# time so a *live* long trial is never reclaimed out from under itself. No
# connection is held open (unlike a `brev exec flock`), so an SSH blip can't
# silently drop the lock mid-trial. Set SKILL_EVAL_DISABLE_BOX_LOCK=1 to fall
# back to host-local-flock-only (escape hatch).
#
# Known limitation: the stale-reap (rm + mkdir) is not itself serialized, so two
# legs that BOTH observe an already-stale marker and race to reclaim it could
# both win. That needs a dead holder whose marker sat stale for the full
# BOX_LOCK_STALE_SEC (~8h) AND two claimers reaping within the same few ms — very
# rare, and the fresh-claim path (mkdir) is fully atomic. A `flock`-serialized
# critical section would close it; deferred to avoid brittle nested quoting
# through `brev exec` until it can be validated on a live box.
BOX_LOCK_DIR = "/tmp/skill-eval-boxlock.d"
BOX_LOCK_STALE_SEC = 28800  # 8h > max per-leg trial (~6h) so a live holder is never reaped
BOX_EXEC_TIMEOUT = 45
# Consecutive `brev exec` claim failures (box unreachable) before we give up on
# this box and fail fast so the agent rescores elsewhere. Tolerates a transient
# blip (~a couple minutes of retries) but not a persistently dead box.
BOX_CLAIM_MAX_FAILS = 6


def _box_lock_enabled(instance: str) -> bool:
    """Box-side lock applies to the vss-eval-* brev pool (L40S + RTX)."""
    if os.environ.get("SKILL_EVAL_DISABLE_BOX_LOCK"):
        return False
    return instance.startswith("vss-eval-")


def _brev_exec(instance: str, command: str, timeout: int = BOX_EXEC_TIMEOUT) -> tuple[int, str]:
    """Run `brev exec <instance> <command>` synchronously -> (rc, stdout+stderr)."""
    try:
        proc = subprocess.run(
            ["brev", "exec", instance, command],
            input=b"\n", capture_output=True, timeout=timeout,
        )
        out = (proc.stdout or b"").decode(errors="replace")
        err = (proc.stderr or b"").decode(errors="replace")
        return proc.returncode, (out + err).strip()
    except subprocess.TimeoutExpired:
        return 124, "brev exec timed out"
    except FileNotFoundError:
        return 127, "brev CLI not found"


def _box_claim_script(my_id: str, stale_sec: int) -> str:
    # my_id and BOX_LOCK_DIR are sanitized/constant, so direct interpolation is
    # injection-safe and matches brev_env.py's reset-script style (double quotes
    # + $(), no single-quote nesting that could break through `brev exec`).
    # No `set -e`: the exit code must reflect brev-exec reachability, NOT an
    # intermediate command's rc (a corrupt/non-numeric owner ts must not make a
    # healthy box look unreachable). The trailing echo drives the caller's logic.
    d = BOX_LOCK_DIR
    return (
        f'D="{d}"; O="$D/owner"; MY="{my_id}"; NOW=$(date +%s); '
        # reap a stale marker (crashed/dead holder) before claiming; force TS numeric
        f'if [ -d "$D" ]; then TS=$(cut -d" " -f2 "$O" 2>/dev/null); '
        f'case "$TS" in ""|*[!0-9]*) TS=0;; esac; '
        f'[ $(( NOW - TS )) -ge {stale_sec} ] && rm -rf "$D" 2>/dev/null; fi; '
        # atomic claim: mkdir is the race gate. If the owner-write fails (e.g. disk
        # full) undo the mkdir so we never leave an orphan marker, and report
        # WRITEFAIL (the caller treats non-CLAIMED/non-BUSY as fail-fast).
        f'if mkdir "$D" 2>/dev/null; then '
        f'if printf "%s %s" "$MY" "$NOW" > "$O" 2>/dev/null; then echo CLAIMED; '
        f'else rm -rf "$D" 2>/dev/null; echo WRITEFAIL; fi; '
        f'else echo "BUSY $(cat "$O" 2>/dev/null)"; fi'
    )


def _box_release_script(my_id: str) -> str:
    d = BOX_LOCK_DIR
    # only release if we still own it (guards against removing a marker that was
    # reaped-as-stale and re-claimed by another leg)
    return (
        f'D="{d}"; O="$D/owner"; MY="{my_id}"; '
        f'OWN=$(cut -d" " -f1 "$O" 2>/dev/null); '
        f'if [ "$OWN" = "$MY" ]; then rm -rf "$D" 2>/dev/null; echo RELEASED; '
        f'else echo "SKIP owner=$OWN"; fi'
    )


@contextlib.contextmanager
def hold_box_lock(lock_dir: Path, instance: str, timeout_sec: int):
    if "/" in instance or instance in {"", ".", ".."}:
        raise ValueError(f"invalid Brev instance name for lock file: {instance!r}")
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_path = lock_dir / f"{instance}.lock"
    deadline = time.monotonic() + timeout_sec
    box_lock = _box_lock_enabled(instance)
    # Owner id must be unique per leg-run-host so the release-ownership guard
    # can't misfire (same spec+platform on two hosts could share a pid). Sanitize
    # to a shell/owner-file-safe charset (no spaces, quotes, or $).
    my_id = re.sub(
        r"[^A-Za-z0-9_.:-]",
        "_",
        ":".join(
            [
                os.environ.get("EVAL_SLUG", "leg"),
                os.environ.get("GITHUB_RUN_ID", "0"),
                os.environ.get("RUNNER_NAME", "h"),
                str(os.getpid()),
            ]
        ),
    )
    with lock_path.open("a+") as fp:
        # 1) host-local flock — cheap same-host serialization
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
        # 2) box-side marker — cross-host serialization on the box itself
        box_held = False
        if box_lock:
            claim = _box_claim_script(my_id, BOX_LOCK_STALE_SEC)
            exec_fails = 0
            while True:
                rc, out = _brev_exec(instance, claim)
                if rc == 0 and "CLAIMED" in out:
                    box_held = True
                    print(f"[run-leg] box lock acquired on {instance}", flush=True)
                    break
                if rc == 0 and "BUSY" in out:
                    # Box is reachable but the marker is held by another leg ->
                    # legitimate contention: wait up to the lock budget.
                    exec_fails = 0
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        fcntl.flock(fp.fileno(), fcntl.LOCK_UN)
                        raise LockTimeoutError(f"box lock timeout on {instance}: {out[:120]}")
                    print(
                        f"[run-leg] waiting for box lock on {instance} "
                        f"(held-by: {out[:80]}; {int(remaining)}s remaining)",
                        flush=True,
                    )
                    time.sleep(min(60, remaining))
                    continue
                # `brev exec` itself failed (rc != 0 / unexpected output) -> the box
                # is unreachable or degraded. Retry a few times for a transient blip,
                # then FAIL FAST so the agent rescores to a different box instead of
                # burning the whole lock budget on a dead box (the vss-eval-rtx-2g
                # "RUNNING in brev ls but SSH-unreachable" case that stalled legs for
                # ~5.8 h). Do NOT treat this like BUSY.
                exec_fails += 1
                print(
                    f"[run-leg] box lock claim failed on {instance} "
                    f"(attempt {exec_fails}/{BOX_CLAIM_MAX_FAILS}; rc={rc}; {out[:80]})",
                    flush=True,
                )
                if exec_fails >= BOX_CLAIM_MAX_FAILS or deadline - time.monotonic() <= 0:
                    fcntl.flock(fp.fileno(), fcntl.LOCK_UN)
                    raise BoxUnreachableError(
                        f"box unreachable for box lock: {instance} (rc={rc}; {out[:120]})"
                    )
                time.sleep(min(20, max(1, deadline - time.monotonic())))
        try:
            yield lock_path
        finally:
            if box_held:
                rc, out = _brev_exec(instance, _box_release_script(my_id))
                print(f"[run-leg] box lock released on {instance} ({out[:60]})", flush=True)
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
            # Chain-abort policy: only a HARD failure aborts the rest of the
            # chain — a non-zero harbor rc means the agent/trial crashed or hit
            # the harbor timeout, so the step never ran to completion and
            # downstream steps that build on its setup can't be trusted. A SOFT
            # check-miss (rc==0 but reward<1.0 — the agent completed the step's
            # operations, but a grader check scored <1.0, often a grader
            # timeout/flake or a miss on an independent read) is recorded via
            # the step's own reward.txt but does NOT abort: later steps in a
            # chain like vios_ops (13 steps) / nvstreamer_ops are mostly
            # independent reads on step-1's shared sensor and should still run,
            # so one grader flake no longer wipes out the tail of the chain.
            # (Single-step specs — e.g. every vss-deploy-profile spec — never
            # reach this branch.)
            if rc != 0:
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
            elif reward_value < 1.0:
                print(
                    f"[run-leg] {invocation.chain_key}/{invocation.include_task_name} "
                    f"soft check-miss (reward={reward if reward is not None else 'missing'}); "
                    f"continuing chain — later independent steps still run",
                    flush=True,
                )

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
    except BoxUnreachableError as exc:
        # Fail fast: this box is unreachable/degraded. Signal the agent to
        # rescore to a different box rather than wait out the lock budget.
        print(f"BLOCKED: box unreachable on {args.instance} ({exc})", flush=True)
        return 75
    except LockTimeoutError:
        print(f"BLOCKED: lock timeout on {args.instance}", flush=True)
        return 75
    except Exception as exc:  # noqa: BLE001
        print(f"FATAL: run_leg failed: {exc!r}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
