#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Deterministic GitHub CI runner for the NemoClaw VSS skill smoke eval.

This path intentionally does not ask the outer Claude meta-agent to decide what
to run.  It generates one bounded Harbor dataset, locks one existing
``vss-eval-*`` worker, runs Harbor once in the foreground, and exits with a
clear verdict.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import selectors
import signal
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, NamedTuple


REPO_ROOT = Path(__file__).resolve().parents[3]
SKILL_EVAL_ROOT = REPO_ROOT / ".github" / "skill-eval"
DEFAULT_DATASET_ROOT = Path("/tmp/skill-eval/datasets/vss-deploy-profile")
DEFAULT_RESULTS_ROOT = Path("/tmp/skill-eval/results")

PLATFORM_TASK = {
    "RTXPRO6000BW": "rtxpro6000bw",
    "L40S": "l40s",
    "H100": "h100",
    "DGX-SPARK": "spark",
    "IGX-THOR": "thor",
}

PLATFORM_NAME_HINTS = {
    "RTXPRO6000BW": ("rtx",),
    "L40S": ("l40s",),
    "H100": ("h100",),
    "DGX-SPARK": ("spark",),
    "IGX-THOR": ("thor",),
}

PLATFORM_GPU_HINTS = {
    "RTXPRO6000BW": ("RTX", "PRO", "6000"),
    "L40S": ("L40S",),
    "H100": ("H100",),
    "DGX-SPARK": ("GB10", "SPARK"),
    "IGX-THOR": ("THOR",),
}


class CommandResult(NamedTuple):
    returncode: int
    stdout: str
    stderr: str


class InfrastructureBlocked(RuntimeError):
    """Raised when CI infra capacity prevents the smoke from running."""


def _run(cmd: list[str], *, timeout: int = 60, env: dict[str, str] | None = None) -> CommandResult:
    proc = subprocess.run(
        cmd,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
    )
    return CommandResult(proc.returncode, proc.stdout, proc.stderr)


def _parse_brev_json(raw: str) -> list[dict[str, Any]]:
    """Parse Brev JSON output while tolerating trailing CLI walkthrough text."""
    bracket = raw.rfind("]")
    if bracket < 0:
        return []
    try:
        parsed = json.loads(raw[: bracket + 1])
    except json.JSONDecodeError:
        return []
    return parsed if isinstance(parsed, list) else []


def _status_ready(status: str) -> bool:
    upper = status.upper()
    return "RUNNING" in upper or "READY" in upper


def _loose_tokens_match(want: tuple[str, ...], have: str) -> bool:
    upper = have.upper().replace("-", " ")
    return all(token.upper() in upper for token in want)


def _instance_candidates(
    instances: list[dict[str, Any]],
    *,
    platform: str,
    gpu_count: int,
) -> list[str]:
    name_hints = PLATFORM_NAME_HINTS.get(platform, ())
    gpu_hints = PLATFORM_GPU_HINTS.get(platform, ())
    candidates: list[tuple[int, str]] = []
    for inst in instances:
        name = str(inst.get("name") or "")
        if not name.startswith("vss-eval-"):
            continue
        status_text = " ".join(str(inst.get(key) or "") for key in ("status", "state"))
        if status_text and not _status_ready(status_text):
            continue
        lowered = name.lower()
        gpu_text = " ".join(
            str(inst.get(key) or "") for key in ("gpu", "instance_type", "type")
        )
        name_match = any(hint in lowered for hint in name_hints)
        gpu_match = _loose_tokens_match(gpu_hints, gpu_text) if gpu_hints else True
        if not (name_match or gpu_match):
            continue

        score = 0
        if gpu_count == 1 and "-1g" in lowered:
            score -= 10
        elif gpu_count >= 2 and "-2g" in lowered:
            score -= 10
        score += len(name)
        candidates.append((score, name))
    return [name for _, name in sorted(candidates)]


def _summarize_instances(instances: list[dict[str, Any]]) -> str:
    rows: list[str] = []
    for inst in instances:
        name = str(inst.get("name") or "")
        if not name.startswith("vss-eval-"):
            continue
        status = " ".join(str(inst.get(key) or "") for key in ("status", "state")).strip()
        gpu = str(inst.get("gpu") or "").strip()
        instance_type = str(inst.get("instance_type") or inst.get("type") or "").strip()
        details = ", ".join(part for part in (status, gpu, instance_type) if part)
        rows.append(f"{name} ({details or 'no metadata'})")
    return "; ".join(rows) if rows else "<no vss-eval-* workers visible>"


def _generate_dataset(profile: str, platform: str, dataset_root: Path) -> None:
    shutil.rmtree(dataset_root, ignore_errors=True)
    env = os.environ.copy()
    env["SKILLS_EVAL_RUNNER"] = "nemoclaw"
    cmd = [
        sys.executable,
        ".github/skill-eval/adapters/vss-deploy-profile/generate.py",
        "--output-dir",
        str(dataset_root),
        "--skill-dir",
        "skills/vss-deploy-profile",
        "--profile",
        profile,
        "--platform",
        platform,
    ]
    print("[nemoclaw-ci] generating dataset:", " ".join(cmd), flush=True)
    result = _run(cmd, timeout=120, env=env)
    print(result.stdout, end="", flush=True)
    if result.returncode != 0:
        print(result.stderr, file=sys.stderr, flush=True)
        raise RuntimeError(f"dataset generation failed with exit {result.returncode}")


def _list_instances() -> list[dict[str, Any]]:
    try:
        result = _run(["brev", "ls", "--json"], timeout=45)
    except subprocess.TimeoutExpired as exc:
        raise InfrastructureBlocked(
            f"brev ls --json timed out after {exc.timeout}s"
        ) from exc
    if result.returncode != 0:
        raise InfrastructureBlocked(f"brev ls --json failed: {result.stderr[-500:]}")
    instances = _parse_brev_json(result.stdout)
    if not instances:
        raise InfrastructureBlocked("brev ls --json returned no parseable instances")
    return instances


def _cleanup_results(results_root: Path, run_id: str) -> None:
    """Drop stale run results so workflow artifacts only include this run."""
    results_root.mkdir(parents=True, exist_ok=True)
    for child in results_root.iterdir():
        if child.name in (run_id, "_viewer"):
            continue
        if child.is_dir():
            shutil.rmtree(child, ignore_errors=True)
        else:
            child.unlink(missing_ok=True)
    (results_root / run_id).mkdir(parents=True, exist_ok=True)


def _reachable(instance: str) -> bool:
    try:
        result = _run(["brev", "exec", instance, "echo harbor-ready"], timeout=45)
    except subprocess.TimeoutExpired:
        print(
            f"[nemoclaw-ci] candidate {instance} reachability check timed out",
            flush=True,
        )
        return False
    return result.returncode == 0 and "harbor-ready" in result.stdout


def _try_acquire_lock(instance: str) -> tuple[int, Any] | None:
    lock_dir = Path("/tmp/brev")
    lock_dir.mkdir(parents=True, exist_ok=True)
    fd = os.open(lock_dir / f"{instance}.lock", os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return fd, os.fdopen(fd, "w")
    except BlockingIOError:
        os.close(fd)
        return None


def _select_and_lock_instance(
    platform: str,
    gpu_count: int,
    explicit: str | None,
    timeout_s: int,
) -> tuple[str, int, Any]:
    deadline = time.time() + timeout_s
    while True:
        if explicit:
            candidates = [explicit]
        else:
            instances = _list_instances()
            candidates = _instance_candidates(instances, platform=platform, gpu_count=gpu_count)
            inventory = _summarize_instances(instances)
        print(
            "[nemoclaw-ci] candidate workers:",
            ", ".join(candidates) if candidates else "<none>",
            flush=True,
        )
        if not candidates:
            raise InfrastructureBlocked(
                f"no running vss-eval-* candidate for {platform}; visible workers: {inventory}"
            )

        for candidate in candidates:
            if not _reachable(candidate):
                print(f"[nemoclaw-ci] skipping unreachable candidate {candidate}", flush=True)
                continue
            lock = _try_acquire_lock(candidate)
            if lock is not None:
                fd, handle = lock
                return candidate, fd, handle
            print(f"[nemoclaw-ci] skipping locked candidate {candidate}", flush=True)

        if time.time() >= deadline:
            raise InfrastructureBlocked(
                "lock timeout: no reachable unlocked worker for "
                f"{platform} after {timeout_s}s"
            )
        if explicit:
            print(f"[nemoclaw-ci] waiting for explicit worker lock: {explicit}", flush=True)
        else:
            print("[nemoclaw-ci] all candidates busy; retrying worker selection", flush=True)
        time.sleep(10)


def _stream_command(
    cmd: list[str],
    *,
    timeout_s: int,
    env: dict[str, str],
    log_path: Path,
) -> int:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding="utf-8") as log:
        proc = subprocess.Popen(
            cmd,
            cwd=REPO_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env=env,
            bufsize=1,
            start_new_session=True,
        )
        started = time.time()
        last_heartbeat = started
        assert proc.stdout is not None
        selector = selectors.DefaultSelector()
        selector.register(proc.stdout, selectors.EVENT_READ)
        try:
            while True:
                for key, _ in selector.select(timeout=1):
                    line = key.fileobj.readline()
                    if line:
                        print(line, end="", flush=True)
                        log.write(line)
                        log.flush()
                now = time.time()
                if now - last_heartbeat >= 60:
                    elapsed = int(now - started)
                    heartbeat = f"[nemoclaw-ci] Harbor still running ({elapsed}s elapsed)\n"
                    print(heartbeat, end="", flush=True)
                    log.write(heartbeat)
                    log.flush()
                    last_heartbeat = now
                if proc.poll() is not None:
                    for rest in proc.stdout:
                        print(rest, end="", flush=True)
                        log.write(rest)
                    return proc.returncode or 0
                if time.time() - started > timeout_s:
                    _kill_process_group(proc, signal.SIGTERM)
                    try:
                        proc.wait(timeout=120)
                    except subprocess.TimeoutExpired:
                        _kill_process_group(proc, signal.SIGKILL)
                    return 124
        finally:
            selector.close()
            if proc.poll() is None:
                _kill_process_group(proc, signal.SIGKILL)


def _kill_process_group(proc: subprocess.Popen[str], sig: int) -> None:
    try:
        os.killpg(proc.pid, sig)
    except ProcessLookupError:
        return


def _latest_reward(results_root: Path, run_id: str) -> tuple[float | None, Path | None]:
    run_root = results_root / run_id
    rewards = sorted(run_root.glob("*/*/verifier/reward.txt"), key=lambda p: p.stat().st_mtime)
    if not rewards:
        return None, None
    path = rewards[-1]
    try:
        return float(path.read_text(encoding="utf-8").strip()), path
    except ValueError:
        return None, path


def _append_summary(
    *,
    profile: str,
    platform: str,
    instance: str,
    reward: float | None,
    reward_path: Path | None,
    harbor_rc: int,
    log_path: Path,
) -> None:
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return
    status = "PASS" if reward is not None and reward >= 1.0 and harbor_rc == 0 else "FAIL"
    body = [
        "## NemoClaw VSS Skill Eval",
        "",
        f"- Status: `{status}`",
        f"- Scenario: `vss-deploy-profile / {profile} / {platform}`",
        f"- Worker: `{instance}`",
        f"- Harbor exit code: `{harbor_rc}`",
        f"- Reward: `{reward if reward is not None else 'missing'}`",
        f"- Reward path: `{reward_path if reward_path else 'missing'}`",
        f"- Harbor log: `{log_path}`",
        "",
    ]
    with Path(summary_path).open("a", encoding="utf-8") as handle:
        handle.write("\n".join(body))


def _append_blocked_summary(*, reason: str, profile: str, platform: str) -> None:
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return
    body = [
        "## NemoClaw VSS Skill Eval",
        "",
        "- Status: `BLOCKED`",
        f"- Scenario: `vss-deploy-profile / {profile} / {platform}`",
        f"- Reason: `{reason}`",
        "",
        "This is an infrastructure/capacity blocker, not a skill regression.",
        "",
    ]
    with Path(summary_path).open("a", encoding="utf-8") as handle:
        handle.write("\n".join(body))


def _harbor_command(dataset_root: Path, profile: str, task_name: str, results_root: Path, run_id: str) -> list[str]:
    uvx = _ensure_uvx()
    model = os.environ.get("ANTHROPIC_MODEL", "")
    base_url = os.environ.get("ANTHROPIC_BASE_URL", "").rstrip("/")
    if not model:
        raise RuntimeError("ANTHROPIC_MODEL is required")
    if not base_url:
        raise RuntimeError("ANTHROPIC_BASE_URL is required")
    api_base = base_url if base_url.endswith("/v1") else f"{base_url}/v1"
    return [
        uvx,
        "harbor",
        "run",
        "--environment-import-path",
        "envs.brev_env:BrevEnvironment",
        "-p",
        str(dataset_root / profile),
        "--include-task-name",
        task_name,
        "-a",
        "claude-code",
        "--model",
        model,
        "--ak",
        f"api_base={api_base}",
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
        str(results_root / run_id),
    ]


def _ensure_uvx() -> str:
    """Return a usable uvx binary, installing uv into ~/.local/bin if needed."""
    user_bin = str(Path.home() / ".local" / "bin")
    os.environ["PATH"] = f"{user_bin}:{os.environ.get('PATH', '')}"
    found = shutil.which("uvx")
    if found:
        return found
    print("[nemoclaw-ci] uvx not found; installing uv with pip --user", flush=True)
    result = _run(
        [sys.executable, "-m", "pip", "install", "--user", "--quiet", "uv"],
        timeout=180,
        env=os.environ.copy(),
    )
    if result.returncode != 0:
        raise RuntimeError(f"failed to install uv: {result.stderr[-1000:]}")
    found = shutil.which("uvx")
    if not found:
        raise RuntimeError("uv install completed but uvx is still not on PATH")
    return found


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", default=os.environ.get("NEMOCLAW_EVAL_PROFILE", "base"))
    parser.add_argument("--platform", default=os.environ.get("NEMOCLAW_EVAL_PLATFORM", "RTXPRO6000BW"))
    parser.add_argument("--gpu-count", type=int, default=int(os.environ.get("NEMOCLAW_EVAL_GPU_COUNT", "1")))
    parser.add_argument("--instance", default=os.environ.get("NEMOCLAW_BREV_INSTANCE"))
    parser.add_argument("--lock-timeout", type=int, default=int(os.environ.get("NEMOCLAW_LOCK_TIMEOUT_SEC", "600")))
    parser.add_argument("--harbor-timeout", type=int, default=int(os.environ.get("NEMOCLAW_HARBOR_TIMEOUT_SEC", "3300")))
    parser.add_argument("--dataset-root", default=str(DEFAULT_DATASET_ROOT))
    parser.add_argument("--results-root", default=str(DEFAULT_RESULTS_ROOT))
    args = parser.parse_args(argv)

    run_id = os.environ.get("GITHUB_RUN_ID", f"manual-{int(time.time())}")
    dataset_root = Path(args.dataset_root)
    results_root = Path(args.results_root)
    task_name = PLATFORM_TASK.get(args.platform)
    if not task_name:
        raise RuntimeError(f"unsupported platform {args.platform!r}")

    os.environ["SKILLS_EVAL_RUNNER"] = "nemoclaw"
    os.environ["PYTHONPATH"] = f"{SKILL_EVAL_ROOT}:{os.environ.get('PYTHONPATH', '')}"
    Path("/tmp/skill-eval").mkdir(parents=True, exist_ok=True)
    _cleanup_results(results_root, run_id)

    try:
        _generate_dataset(args.profile, args.platform, dataset_root)
        instance, lock_fd, lock_handle = _select_and_lock_instance(
            args.platform,
            args.gpu_count,
            args.instance,
            args.lock_timeout,
        )
        print(f"[nemoclaw-ci] selected worker: {instance}", flush=True)
        try:
            os.environ["BREV_INSTANCE"] = instance
            harbor_env = os.environ.copy()
            harbor_env["BREV_INSTANCE"] = instance
            cmd = _harbor_command(dataset_root, args.profile, task_name, results_root, run_id)
            log_path = Path("/tmp/skill-eval") / f"nemoclaw-harbor-{run_id}.log"
            print("[nemoclaw-ci] running Harbor once:", " ".join(cmd), flush=True)
            harbor_rc = _stream_command(
                cmd,
                timeout_s=args.harbor_timeout,
                env=harbor_env,
                log_path=log_path,
            )
        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            lock_handle.close()

        reward, reward_path = _latest_reward(results_root, run_id)
        _append_summary(
            profile=args.profile,
            platform=args.platform,
            instance=instance,
            reward=reward,
            reward_path=reward_path,
            harbor_rc=harbor_rc,
            log_path=log_path,
        )
        if harbor_rc == 0 and reward is not None and reward >= 1.0:
            print("DONE: NemoClaw vss-deploy-profile/base smoke passed", flush=True)
            return 0
        print(
            "BLOCKED: NemoClaw smoke failed "
            f"(harbor_rc={harbor_rc}, reward={reward if reward is not None else 'missing'})",
            flush=True,
        )
        return 1
    except InfrastructureBlocked as exc:
        reason = str(exc)
        print(f"BLOCKED: NemoClaw smoke infra blocked: {reason}", file=sys.stderr, flush=True)
        _append_blocked_summary(reason=reason, profile=args.profile, platform=args.platform)
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"BLOCKED: NemoClaw smoke setup failed: {exc}", file=sys.stderr, flush=True)
        if os.environ.get("GITHUB_STEP_SUMMARY"):
            with Path(os.environ["GITHUB_STEP_SUMMARY"]).open("a", encoding="utf-8") as handle:
                handle.write(
                    "## NemoClaw VSS Skill Eval\n\n"
                    f"- Status: `BLOCKED`\n- Reason: `{exc}`\n"
                )
        return 1


if __name__ == "__main__":
    sys.exit(main())
