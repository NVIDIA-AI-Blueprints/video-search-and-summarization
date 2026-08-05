#!/usr/bin/env python3
"""Run generated Harbor tasks on the current machine."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[4]
JUDGE = REPO_ROOT / ".github/skill-eval/verifiers/generic_judge.py"
LOCAL_JUDGE = Path(__file__).with_name("local_judge.py")
TOKEN_FIELDS = (
    "input_tokens",
    "cache_read_input_tokens",
    "cache_creation_input_tokens",
    "output_tokens",
)
ARMS = ("direct", "cold", "warm")
def task_step(task: Path) -> int:
    text = (task / "task.toml").read_text(encoding="utf-8")
    match = re.search(r"(?m)^step_index\s*=\s*(\d+)\s*$", text)
    return int(match.group(1)) if match else 1


def task_spec(task: Path, explicit: Path | None) -> Path:
    if explicit:
        path = explicit.expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(f"spec not found: {path}")
        return path
    candidates = sorted((task / "tests").glob("*.json"))
    if len(candidates) != 1:
        raise ValueError(
            f"expected one JSON spec under {task / 'tests'}, found "
            f"{len(candidates)}; pass --spec"
        )
    return candidates[0]


def read_result(trajectory: Path) -> dict:
    result = None
    with trajectory.open(encoding="utf-8", errors="replace") as handle:
        for line in handle:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict) and event.get("type") == "result":
                result = event
    if result is None:
        raise ValueError("Claude produced no result event")
    return result


def usage(result: dict) -> dict:
    raw = result.get("usage") or {}
    values = {
        name: int(raw.get(name) or 0) if isinstance(raw, dict) else 0
        for name in TOKEN_FIELDS
    }
    cost = result.get("total_cost_usd")
    if not isinstance(cost, (int, float)) or isinstance(cost, bool):
        cost = result.get("cost_usd")
    if isinstance(cost, bool):
        cost = None
    return {
        **values,
        "total_tokens": sum(values.values()),
        "num_turns": int(result.get("num_turns") or 0),
        "cost_usd": float(cost) if isinstance(cost, (int, float)) else None,
    }


def claude_command(instruction: str, model: str | None) -> list[str]:
    command = [
        os.environ.get("CLAUDE_BIN", "claude"),
        "-p", instruction,
        "--output-format", "stream-json",
        "--verbose",
        "--permission-mode", "bypassPermissions",
        "--no-session-persistence",
    ]
    if model:
        command += ["--model", model]
    return command


def mode_environment(mode: str, cache_home: Path | None) -> dict[str, str]:
    env = os.environ.copy()
    env["PLAN_EXECUTE_CACHE"] = "0" if mode == "direct" else "1"
    env["CLAUDE_CODE_DISABLE_AUTO_MEMORY"] = "1"
    env.pop("CLAUDE_CODE_DISABLE_THINKING", None)
    if mode != "direct":
        if cache_home is None:
            raise ValueError("--cache-home is required for cold and warm modes")
        env["PLAN_EXECUTE_CACHE_HOME"] = str(cache_home)
    return env


def prepare_claude_config(task: Path, output: Path) -> Path:
    """Expose only the skills bundled with this generated Harbor task."""
    source_root = task / "skills"
    if not source_root.is_dir():
        raise FileNotFoundError(f"task has no bundled skills: {source_root}")
    config = output / "claude-config"
    skills = config / "skills"
    skills.mkdir(parents=True)
    for source in sorted(path for path in source_root.iterdir()
                         if path.is_dir()):
        (skills / source.name).symlink_to(source.resolve(), target_is_directory=True)
    return config


def check_cache(mode: str, cache_home: Path | None) -> None:
    if mode == "direct":
        return
    if cache_home is None:
        raise ValueError("--cache-home is required for cold and warm modes")
    populated = cache_home.is_dir() and any(cache_home.iterdir())
    memories = cache_home / "memories"
    reusable = memories.is_dir() and any(
        path.is_dir() and (path / "procedure.md").is_file()
        for path in memories.iterdir()
    )
    if mode == "cold" and populated:
        raise ValueError(f"cold cache is not empty: {cache_home}")
    if mode == "warm" and not reusable:
        raise ValueError(f"warm cache has no reusable procedures: {cache_home}")


def run_agent(
    task: Path,
    mode: str,
    cache_home: Path | None,
    output: Path,
    model: str | None,
    timeout: int,
) -> tuple[int, float, dict]:
    instruction = (task / "instruction.md").read_text(encoding="utf-8")
    trajectory = output / "trajectory.jsonl"
    stderr = output / "agent-stderr.log"
    env = mode_environment(mode, cache_home)
    env["CLAUDE_CONFIG_DIR"] = str(prepare_claude_config(task, output))
    started = time.monotonic()
    try:
        with trajectory.open("w", encoding="utf-8") as stdout_handle, \
                stderr.open("w", encoding="utf-8") as stderr_handle:
            completed = subprocess.run(
                claude_command(instruction, model),
                cwd=REPO_ROOT,
                env=env,
                stdout=stdout_handle,
                stderr=stderr_handle,
                text=True,
                timeout=timeout,
            )
        returncode = completed.returncode
    except subprocess.TimeoutExpired:
        returncode = 124
    elapsed = time.monotonic() - started
    try:
        result = read_result(trajectory)
    except ValueError:
        result = {}

    return returncode, elapsed, result


def run_verifier(
    spec: Path,
    step: int,
    trajectory: Path,
    output: Path,
    timeout: int,
) -> tuple[int, float]:
    started = time.monotonic()
    completed = subprocess.run(
        [
            sys.executable,
            str(LOCAL_JUDGE),
            "--trajectory", str(trajectory),
            "--spec", str(spec),
            "--step", str(step),
            "--reward-file", str(output / "reward.txt"),
            "--details-file", str(output / "judge.json"),
        ],
        cwd=REPO_ROOT,
        env=os.environ.copy(),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=timeout,
    )
    (output / "verifier.log").write_text(completed.stdout, encoding="utf-8")
    return completed.returncode, time.monotonic() - started


def evaluate_task(
    task: Path,
    mode: str,
    cache_home: Path | None,
    output: Path,
    model: str | None,
    agent_timeout: int,
    verifier_timeout: int,
    spec: Path | None = None,
) -> dict:
    if not (task / "instruction.md").is_file() \
            or not (task / "task.toml").is_file():
        raise FileNotFoundError(f"not a generated Harbor task: {task}")
    resolved_spec = task_spec(task, spec)
    step = task_step(task)
    output.mkdir(parents=True, exist_ok=False)
    print(f"Running {mode}: {task}", flush=True)
    agent_rc, agent_seconds, result = run_agent(
        task, mode, cache_home, output, model, agent_timeout,
    )
    print(f"Verifying {task.name}...", flush=True)
    verifier_rc, verifier_seconds = run_verifier(
        resolved_spec, step, output / "trajectory.jsonl", output,
        verifier_timeout,
    )
    reward_path = output / "reward.txt"
    reward = (
        float(reward_path.read_text(encoding="utf-8").strip())
        if reward_path.is_file() else None
    )
    agent_succeeded = (
        agent_rc == 0 and bool(result) and not result.get("is_error", False)
    )
    report = {
        "task": str(task),
        "mode": mode,
        "step": step,
        "reward": reward,
        "passed": agent_succeeded and verifier_rc == 0 and reward == 1.0,
        "agent_returncode": agent_rc,
        "agent_result_found": bool(result),
        "verifier_returncode": verifier_rc,
        "agent_seconds": round(agent_seconds, 2),
        "verifier_seconds": round(verifier_seconds, 2),
        **usage(result),
        "output": str(output),
    }
    (output / "result.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return report


def summarize(mode: str, reports: list[dict]) -> dict:
    costs = [report["cost_usd"] for report in reports]
    rewards = [report["reward"] for report in reports]
    return {
        "mode": mode,
        "passed": bool(reports) and all(report["passed"] for report in reports),
        "tasks": reports,
        "reward_mean": (
            sum(rewards) / len(rewards)
            if rewards and all(value is not None for value in rewards) else None
        ),
        "total_tokens": sum(report["total_tokens"] for report in reports),
        "cost_usd": (
            sum(costs) if costs and all(value is not None for value in costs)
            else None
        ),
        "agent_seconds": round(sum(r["agent_seconds"] for r in reports), 2),
        "verifier_seconds": round(
            sum(r["verifier_seconds"] for r in reports), 2
        ),
    }


def print_summary(report: dict) -> None:
    print("\n| Arm | Passed | Reward | Tokens | Cost | Agent latency |")
    print("|---|---:|---:|---:|---:|---:|")
    for mode in ARMS:
        arm = report["arms"].get(mode)
        if not arm:
            continue
        cost = (
            f"${arm['cost_usd']:.4f}"
            if arm["cost_usd"] is not None else "unavailable"
        )
        reward = (
            f"{arm['reward_mean']:.3f}"
            if arm["reward_mean"] is not None else "unavailable"
        )
        print(
            f"| {mode} | {'yes' if arm['passed'] else 'no'} | {reward} | "
            f"{arm['total_tokens']:,} | {cost} | {arm['agent_seconds']:.1f}s |"
        )


def run_reset(script: Path, output: Path, timeout: int) -> None:
    completed = subprocess.run(
        [str(script)], cwd=REPO_ROOT, env=os.environ.copy(),
        capture_output=True, text=True, timeout=timeout,
    )
    (output / "reset.log").write_text(
        completed.stdout + completed.stderr, encoding="utf-8",
    )
    if completed.returncode:
        raise RuntimeError(
            f"reset failed with exit {completed.returncode}; "
            f"inspect {output / 'reset.log'}"
        )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ").lower()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", type=Path, action="append", required=True,
                        help="generated Harbor task directory")
    parser.add_argument("--mode", choices=(*ARMS, "compare"),
                        default="direct")
    parser.add_argument(
        "--cache-home", type=Path,
        default=Path("/tmp/skill-eval-local-cache") / stamp,
        help="shared cache for cold and warm; defaults to a new timestamped "
             "directory",
    )
    parser.add_argument("--spec", type=Path,
                        help="eval JSON; valid only with one --task")
    parser.add_argument(
        "--reset-script", type=Path,
        help="executable that restores external state before each arm",
    )
    parser.add_argument("--model", default=os.environ.get("ANTHROPIC_MODEL"),
                        help="Claude model; defaults to ANTHROPIC_MODEL")
    parser.add_argument("--output", type=Path,
                        default=Path("/tmp/skill-eval-local") / stamp)
    parser.add_argument("--agent-timeout", type=int, default=3600)
    parser.add_argument("--verifier-timeout", type=int, default=1800)
    parser.add_argument("--reset-timeout", type=int, default=300)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    tasks = [task.expanduser().resolve() for task in args.task]
    output = args.output.expanduser().resolve()
    cache_home = args.cache_home.expanduser().resolve() if args.cache_home else None

    try:
        if args.spec and len(tasks) != 1:
            raise ValueError("--spec can only be used with one --task")
        for task in tasks:
            if not (task / "instruction.md").is_file() \
                    or not (task / "task.toml").is_file():
                raise FileNotFoundError(f"not a generated Harbor task: {task}")
            task_spec(task, args.spec)
        modes = ARMS if args.mode == "compare" else (args.mode,)
        if args.mode == "compare" and not args.reset_script:
            raise ValueError("compare mode requires --reset-script")
        if "cold" in modes:
            check_cache("cold", cache_home)
        elif "warm" in modes:
            check_cache("warm", cache_home)
        reset_script = (
            args.reset_script.expanduser().resolve() if args.reset_script else None
        )
        if reset_script and not reset_script.is_file():
            raise FileNotFoundError(f"reset script not found: {reset_script}")
        output.mkdir(parents=True, exist_ok=False)
        summaries = {}
        for mode in modes:
            arm_output = output / mode
            arm_output.mkdir()
            if reset_script:
                run_reset(reset_script, arm_output, args.reset_timeout)
            if mode == "warm":
                check_cache("warm", cache_home)
            reports = []
            for index, task in enumerate(tasks, 1):
                report = evaluate_task(
                    task, mode, cache_home,
                    arm_output / f"{index:02d}-{task.name}",
                    args.model, args.agent_timeout, args.verifier_timeout,
                    args.spec,
                )
                reports.append(report)
            summaries[mode] = summarize(mode, reports)
        report = {
            "passed": len(summaries) == len(modes)
                      and all(value["passed"] for value in summaries.values()),
            "arms": summaries,
            "cache_home": str(cache_home) if cache_home else None,
            "output": str(output),
        }
        (output / "result.json").write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print_summary(report)
        print(f"\nFull result: {output / 'result.json'}")
        return 0 if report["passed"] else 1
    except (FileNotFoundError, RuntimeError, ValueError, OSError,
            subprocess.SubprocessError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
