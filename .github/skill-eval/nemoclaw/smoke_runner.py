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
import datetime as dt
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
from urllib.parse import quote


REPO_ROOT = Path(__file__).resolve().parents[3]
SKILL_EVAL_ROOT = REPO_ROOT / ".github" / "skill-eval"
DEFAULT_DATASET_ROOT = Path("/tmp/skill-eval/datasets/vss-deploy-profile")
DEFAULT_RESULTS_ROOT = Path("/tmp/skill-eval/results")
DEFAULT_PROFILE = "base"
DEFAULT_PLATFORM = "RTXPRO6000BW"
SCRATCH_ROOT = Path("/tmp/skill-eval")

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
        # A 1-GPU profile can safely run on a larger 2-GPU warm worker when
        # the 1-GPU pool is stopped; prefer exact partitions below, but do not
        # reject the larger worker as a fallback.
        if gpu_count >= 2 and "-1g" in lowered:
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


def _gpu_count_from_spec(profile: str, platform: str) -> int:
    spec_path = REPO_ROOT / "skills" / "vss-deploy-profile" / "evals" / f"{profile}.json"
    if not spec_path.exists():
        raise RuntimeError(f"missing vss-deploy-profile eval spec: {spec_path}")
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    platform_spec = (
        spec.get("resources", {})
        .get("platforms", {})
        .get(platform, {})
    )
    try:
        return int(platform_spec["gpu_count"])
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError(
            f"missing gpu_count for profile={profile!r}, platform={platform!r} in {spec_path}"
        ) from exc


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
            try:
                instances = _list_instances()
            except InfrastructureBlocked as exc:
                reason = str(exc)
                if time.time() >= deadline:
                    raise InfrastructureBlocked(
                        "worker inventory unavailable for "
                        f"{platform} after {timeout_s}s: {reason}"
                    ) from exc
                print(
                    f"[nemoclaw-ci] worker inventory unavailable: {reason}; "
                    "retrying worker selection",
                    flush=True,
                )
                time.sleep(10)
                continue
            candidates = _instance_candidates(instances, platform=platform, gpu_count=gpu_count)
            inventory = _summarize_instances(instances)
        print(
            "[nemoclaw-ci] candidate workers:",
            ", ".join(candidates) if candidates else "<none>",
            flush=True,
        )
        if not candidates:
            reason = (
                f"no running vss-eval-* candidate for {platform}; "
                f"visible workers: {inventory}"
            )
            if time.time() >= deadline:
                raise InfrastructureBlocked(reason)
            print(f"[nemoclaw-ci] {reason}; retrying worker selection", flush=True)
            time.sleep(10)
            continue

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


def _read_json(path: Path) -> dict[str, Any]:
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _latest_trial(results_root: Path, run_id: str) -> tuple[Path | None, dict[str, Any]]:
    run_root = results_root / run_id
    results = [
        path
        for path in run_root.rglob("result.json")
        if (path.parent / "verifier" / "reward.txt").exists()
        or (path.parent / "trial.log").exists()
    ]
    results = sorted(results, key=lambda p: p.stat().st_mtime)
    if not results:
        return None, {}
    result_path = results[-1]
    return result_path.parent, _read_json(result_path)


def _parse_iso(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed


def _format_duration(seconds: float | None) -> str:
    if seconds is None or seconds < 0:
        return "-"
    total = int(round(seconds))
    minutes, sec = divmod(total, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m {sec}s"


def _duration_from_result(result: dict[str, Any]) -> tuple[str, str, str]:
    started = (
        result.get("trial_started_at")
        or result.get("started_at")
        or result.get("start_time")
        or result.get("start")
    )
    finished = (
        result.get("trial_finished_at")
        or result.get("finished_at")
        or result.get("end_time")
        or result.get("end")
    )
    start_dt = _parse_iso(str(started)) if started else None
    finish_dt = _parse_iso(str(finished)) if finished else None
    duration = (
        (finish_dt - start_dt).total_seconds()
        if start_dt is not None and finish_dt is not None
        else None
    )
    return str(started or "-"), str(finished or "-"), _format_duration(duration)


def _format_number(value: int | float | None) -> str:
    if value is None:
        return "-"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "-"
    if number >= 1_000_000:
        return f"{number / 1_000_000:.1f}M"
    if number >= 1_000:
        return f"{number / 1_000:.1f}k"
    if number.is_integer():
        return str(int(number))
    return f"{number:.1f}"


def _load_trajectory_metrics(trial_dir: Path | None, result: dict[str, Any]) -> tuple[str, str, str]:
    agent_result = result.get("agent_result") if isinstance(result, dict) else None
    if isinstance(agent_result, dict):
        prompt = agent_result.get("n_input_tokens")
        cached = agent_result.get("n_cache_tokens")
        if prompt is not None or cached is not None:
            return "n/a", _format_number(prompt), _format_number(cached)

    if trial_dir is None:
        return "n/a", "n/a", "n/a"
    trajectory = trial_dir / "agent" / "trajectory.json"
    data = _read_json(trajectory)
    if not data:
        return "n/a", "n/a", "n/a"

    steps = data.get("steps")
    turns = 0
    prompt_tokens = 0
    cached_tokens = 0
    if isinstance(steps, list):
        for step in steps:
            if not isinstance(step, dict):
                continue
            message = step.get("message")
            if isinstance(message, str):
                try:
                    message = json.loads(message)
                except json.JSONDecodeError:
                    continue
            if not isinstance(message, dict) or message.get("type") != "assistant":
                continue
            turns += 1
            usage = (message.get("message") or {}).get("usage")
            if isinstance(usage, dict):
                prompt_tokens += int(usage.get("input_tokens") or 0)
                cached_tokens += int(usage.get("cache_read_input_tokens") or 0)
                cached_tokens += int(usage.get("cache_creation_input_tokens") or 0)

    final_metrics = data.get("final_metrics") or {}
    model_usage = final_metrics.get("modelUsage") if isinstance(final_metrics, dict) else None
    if isinstance(model_usage, dict):
        prompt_tokens = 0
        cached_tokens = 0
        for usage in model_usage.values():
            if not isinstance(usage, dict):
                continue
            prompt_tokens += int(usage.get("inputTokens") or 0)
            cached_tokens += int(usage.get("cacheReadInputTokens") or 0)
            cached_tokens += int(usage.get("cacheCreationInputTokens") or 0)

    return (
        str(turns) if turns else "n/a",
        _format_number(prompt_tokens) if prompt_tokens else "n/a",
        _format_number(cached_tokens) if cached_tokens else "n/a",
    )


def _judge_details(trial_dir: Path | None, reward: float | None) -> tuple[int | None, int | None, list[str]]:
    if trial_dir is None:
        return None, None, []
    details = _read_json(trial_dir / "verifier" / "judge.json")
    total = details.get("total")
    passed = details.get("passed")
    checks = details.get("checks")
    failures: list[str] = []
    if isinstance(checks, list):
        for idx, check in enumerate(checks, start=1):
            if not isinstance(check, dict) or bool(check.get("pass")):
                continue
            check_text = str(check.get("check") or f"Check {idx}")
            rationale = str(check.get("rationale") or check.get("matched") or "no rationale recorded")
            failures.append(f"**Check {idx}** ({check_text}) - {rationale}")
    if isinstance(total, int) and isinstance(passed, int):
        return passed, total, failures
    if reward is not None and isinstance(total, int):
        return int(round(reward * total)), total, failures
    return None, None, failures


def _md_cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ").strip()


def _shorten(value: str, limit: int = 500) -> str:
    text = " ".join(value.split())
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _copy_viewer_snapshot(
    *,
    results_root: Path,
    run_id: str,
    profile: str,
    platform: str,
    trial_dir: Path | None,
) -> str | None:
    brev_env_id = _coordinator_brev_env_id()
    if not brev_env_id or trial_dir is None:
        return None
    run_root = results_root / run_id
    source = trial_dir.parent if trial_dir.parent != run_root else trial_dir
    if not source.exists():
        return None
    viewer_name = (
        f"nemoclaw__vss-deploy-profile__{profile}__{platform}__"
        f"{run_id}__{source.name}"
    )
    viewer_dir = results_root / "_viewer" / viewer_name
    shutil.rmtree(viewer_dir, ignore_errors=True)
    shutil.copytree(source, viewer_dir)
    return f"https://harbor-{brev_env_id}.brevlab.com/jobs/{quote(viewer_name, safe='')}"


def _coordinator_brev_env_id() -> str:
    value = os.environ.get("BREV_ENV_ID", "").strip()
    if value:
        return value
    try:
        for line in Path("/etc/environment").read_text(encoding="utf-8").splitlines():
            if not line.startswith("BREV_ENV_ID="):
                continue
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        return ""
    return ""


def _github_run_url(run_id: str) -> str | None:
    repo = os.environ.get("PR_REPO") or os.environ.get("GITHUB_REPOSITORY")
    if not repo:
        return None
    return f"https://github.com/{repo}/actions/runs/{run_id}"


def _write_benchmark_input(run_id: str, profile: str, body: str) -> None:
    scratch = SCRATCH_ROOT / run_id
    scratch.mkdir(parents=True, exist_ok=True)
    (scratch / f"pr-nemoclaw-{profile}.md").write_text(body, encoding="utf-8")
    generated = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    (scratch / "benchmark.md").write_text(
        "# Skills Eval Benchmark - NemoClaw smoke\n\n"
        f"Generated: {generated}\n\n"
        "---\n\n"
        f"{body.rstrip()}\n",
        encoding="utf-8",
    )


def _append_harbor_report(
    *,
    profile: str,
    platform: str,
    instance: str,
    results_root: Path,
    run_id: str,
    reward: float | None,
    harbor_rc: int,
    log_path: Path,
) -> None:
    trial_dir, result = _latest_trial(results_root, run_id)
    started, finished, duration = _duration_from_result(result)
    if started == "-" and trial_dir is not None:
        started = dt.datetime.fromtimestamp(trial_dir.stat().st_mtime, dt.timezone.utc).isoformat()
    if finished == "-" and trial_dir is not None:
        finished = dt.datetime.fromtimestamp(trial_dir.stat().st_mtime, dt.timezone.utc).isoformat()
    turns, prompt_tokens, cached_tokens = _load_trajectory_metrics(trial_dir, result)
    passed, total, failures = _judge_details(trial_dir, reward)
    trace_url = _copy_viewer_snapshot(
        results_root=results_root,
        run_id=run_id,
        profile=profile,
        platform=platform,
        trial_dir=trial_dir,
    )
    if trace_url:
        trace_cell = f"[trace]({trace_url})"
    elif run_url := _github_run_url(run_id):
        trace_cell = f"[artifacts]({run_url})"
    else:
        trace_cell = "n/a"
    status_ok = reward is not None and reward >= 1.0 and harbor_rc == 0
    result_prefix = "PASS" if status_ok else "FAIL"
    reward_text = f"{reward:.3g}" if reward is not None else "missing"
    if passed is not None and total is not None:
        result_text = f"{result_prefix} {reward_text} ({passed}/{total})"
    else:
        result_text = f"{result_prefix} {reward_text}"

    head_sha = os.environ.get("PR_HEAD_SHA", "")
    head = head_sha[:8] if head_sha else "unknown"
    spec_path = f"skills/vss-deploy-profile/evals/{profile}.json"
    body = [
        f"## Harbor Eval - `{spec_path}`",
        "",
        f"Head: `{head}` - platform `{platform}` - instance `{instance}` - runtime `NemoClaw/OpenClaw`",
        f"First started: `{started}` - Last finished: `{finished}` - Total: `{duration}`",
        "",
        "| Platform | Result | Reward | Duration | Turns | Prompt tok | Cached tok | Trace |",
        "|---|---|---|---|---|---|---|---|",
        (
            f"| {_md_cell(platform)} | {_md_cell(result_text)} | {_md_cell(reward_text)} | "
            f"{_md_cell(duration)} | {_md_cell(turns)} | {_md_cell(prompt_tokens)} | "
            f"{_md_cell(cached_tokens)} | {trace_cell} |"
        ),
        "",
        "### NemoClaw runtime details",
        "",
        f"- Worker: `{instance}`",
        "- Runtime path: Harbor launcher -> NemoClaw/OpenClaw -> VSS Orchestrator MCP",
        f"- Harbor exit code: `{harbor_rc}`",
        f"- Harbor log: `{log_path}`",
    ]
    if trial_dir is not None:
        body.append(f"- Trial artifacts: `{trial_dir}`")
    if failures:
        body.extend(["", "### Failing checks", ""])
        body.extend(f"- {_shorten(item)}" for item in failures[:10])
        if len(failures) > 10:
            body.append(f"- ... {len(failures) - 10} additional failing checks omitted")
    body.append("")
    report = "\n".join(body)

    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with Path(summary_path).open("a", encoding="utf-8") as handle:
            handle.write(report)
            handle.write("\n")
    else:
        print(report, flush=True)
    _write_benchmark_input(run_id, profile, report)


def _append_blocked_summary(*, reason: str, profile: str, platform: str) -> None:
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
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
    report = "\n".join(body)
    if summary_path:
        with Path(summary_path).open("a", encoding="utf-8") as handle:
            handle.write(report)
    else:
        print(report, flush=True)
    _write_benchmark_input(os.environ.get("GITHUB_RUN_ID", "local"), profile, report)


def _harbor_command(dataset_root: Path, profile: str, task_name: str, results_root: Path, run_id: str) -> list[str]:
    uvx = _ensure_uvx()
    model = os.environ.get("ANTHROPIC_MODEL", "")
    base_url = os.environ.get("ANTHROPIC_BASE_URL", "").rstrip("/")
    env_build_timeout = os.environ.get(
        "NEMOCLAW_ENVIRONMENT_BUILD_TIMEOUT_MULTIPLIER",
        "6.0",
    )
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
        env_build_timeout,
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
    parser.add_argument("--profile", default=os.environ.get("NEMOCLAW_EVAL_PROFILE", DEFAULT_PROFILE))
    parser.add_argument("--platform", default=os.environ.get("NEMOCLAW_EVAL_PLATFORM", DEFAULT_PLATFORM))
    parser.add_argument("--gpu-count", type=int, default=None)
    parser.add_argument("--instance", default=os.environ.get("NEMOCLAW_BREV_INSTANCE"))
    parser.add_argument("--lock-timeout", type=int, default=int(os.environ.get("NEMOCLAW_LOCK_TIMEOUT_SEC", "600")))
    parser.add_argument("--harbor-timeout", type=int, default=int(os.environ.get("NEMOCLAW_HARBOR_TIMEOUT_SEC", "4500")))
    parser.add_argument("--dataset-root", default=str(DEFAULT_DATASET_ROOT))
    parser.add_argument("--results-root", default=str(DEFAULT_RESULTS_ROOT))
    args = parser.parse_args(argv)

    run_id = os.environ.get("GITHUB_RUN_ID", f"manual-{int(time.time())}")
    dataset_root = Path(args.dataset_root)
    results_root = Path(args.results_root)
    task_name = PLATFORM_TASK.get(args.platform)
    if not task_name:
        raise RuntimeError(f"unsupported platform {args.platform!r}")
    if args.gpu_count is None:
        args.gpu_count = (
            int(os.environ["NEMOCLAW_EVAL_GPU_COUNT"])
            if os.environ.get("NEMOCLAW_EVAL_GPU_COUNT")
            else _gpu_count_from_spec(args.profile, args.platform)
        )

    os.environ["SKILLS_EVAL_RUNNER"] = "nemoclaw"
    os.environ["PYTHONPATH"] = f"{SKILL_EVAL_ROOT}:{os.environ.get('PYTHONPATH', '')}"
    SCRATCH_ROOT.mkdir(parents=True, exist_ok=True)
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

        reward, _reward_path = _latest_reward(results_root, run_id)
        _append_harbor_report(
            profile=args.profile,
            platform=args.platform,
            instance=instance,
            results_root=results_root,
            run_id=run_id,
            reward=reward,
            harbor_rc=harbor_rc,
            log_path=log_path,
        )
        if harbor_rc == 0 and reward is not None and reward >= 1.0:
            print(
                f"DONE: NemoClaw vss-deploy-profile/{args.profile} smoke passed",
                flush=True,
            )
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
        body = (
            "## NemoClaw VSS Skill Eval\n\n"
            "- Status: `BLOCKED`\n"
            f"- Scenario: `vss-deploy-profile / {args.profile} / {args.platform}`\n"
            f"- Reason: `{exc}`\n"
        )
        if os.environ.get("GITHUB_STEP_SUMMARY"):
            with Path(os.environ["GITHUB_STEP_SUMMARY"]).open("a", encoding="utf-8") as handle:
                handle.write(body)
        else:
            print(body, flush=True)
        _write_benchmark_input(run_id, args.profile, body)
        return 1


if __name__ == "__main__":
    sys.exit(main())
