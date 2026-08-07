#!/usr/bin/env python3
"""Run generated Harbor tasks on the current machine."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean


REPO_ROOT = Path(__file__).resolve().parents[4]
JUDGE = REPO_ROOT / ".github/skill-eval/verifiers/generic_judge.py"
LOCAL_JUDGE = Path(__file__).with_name("local_judge.py")
TOKEN_FIELDS = (
    "input_tokens",
    "cache_read_input_tokens",
    "cache_creation_input_tokens",
    "output_tokens",
)
ARMS = ("direct", "cold", "cold_true", "warm")
DEFAULT_COMPARE_ARMS = ("direct", "cold", "warm")


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return parsed


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
            raise ValueError(
                "--cache-home is required for cold, cold_true, and warm modes"
            )
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
        raise ValueError(
            "--cache-home is required for cold, cold_true, and warm modes"
        )
    populated = cache_home.is_dir() and any(cache_home.iterdir())
    memories = cache_home / "memories"
    reusable = memories.is_dir() and any(
        path.is_dir() and (path / "procedure.md").is_file()
        for path in memories.iterdir()
    )
    if mode in {"cold", "cold_true"} and populated:
        raise ValueError(f"{mode} cache is not empty: {cache_home}")
    if mode == "warm" and not reusable:
        raise ValueError(f"warm cache has no reusable procedures: {cache_home}")


def merge_procedure_cache(source: Path, destination: Path) -> list[str]:
    """Collect independently learned procedures for a later warm arm.

    If isolated tasks produce the same action key, the later task's version
    wins. This mirrors the final-value behavior of a sequential cold cache
    without exposing an earlier task's procedure to a later cold_true task.
    """
    source_memories = source / "memories"
    if not source_memories.is_dir():
        return []
    destination_memories = destination / "memories"
    destination_memories.mkdir(parents=True, exist_ok=True)
    merged = []
    for memory in sorted(source_memories.iterdir()):
        if not memory.is_dir() or not (memory / "procedure.md").is_file():
            continue
        shutil.copytree(
            memory,
            destination_memories / memory.name,
            dirs_exist_ok=True,
        )
        merged.append(memory.name)
    return merged


def run_agent(
    task: Path,
    instruction: str,
    mode: str,
    cache_home: Path | None,
    output: Path,
    model: str | None,
    timeout: int,
) -> tuple[int, float, dict]:
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


def print_task_report(report: dict) -> None:
    reward = (
        f"{report['reward']:.3f}"
        if report["reward"] is not None else "unavailable"
    )
    cost = (
        f"${report['cost_usd']:.4f}"
        if report["cost_usd"] is not None else "unavailable"
    )
    total_seconds = report["agent_seconds"] + report["verifier_seconds"]
    print(f"\n--- {report['mode']} {Path(report['task']).name} ---")
    print("Input:")
    print(report["input"].strip() or "(empty)")
    print("Output:")
    print(report["agent_output"].strip() or "(no agent output)")
    print(
        f"Result: {'PASS' if report['passed'] else 'FAIL'} | "
        f"reward {reward} | {report['total_tokens']:,} tokens | {cost}"
    )
    print(
        f"Latency: agent {report['agent_seconds']:.1f}s | "
        f"verifier {report['verifier_seconds']:.1f}s | "
        f"total {total_seconds:.1f}s"
    )
    print(f"Artifacts: {report['output']}", flush=True)


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
    instruction = (task / "instruction.md").read_text(encoding="utf-8")
    output.mkdir(parents=True, exist_ok=False)
    print(f"Running {mode}: {task}", flush=True)
    agent_rc, agent_seconds, result = run_agent(
        task, instruction, mode, cache_home, output, model, agent_timeout,
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
        "cache_home": str(cache_home) if cache_home else None,
        "step": step,
        "reward": reward,
        "passed": agent_succeeded and verifier_rc == 0 and reward == 1.0,
        "agent_returncode": agent_rc,
        "agent_result_found": bool(result),
        "verifier_returncode": verifier_rc,
        "agent_seconds": round(agent_seconds, 2),
        "verifier_seconds": round(verifier_seconds, 2),
        "input": instruction,
        "agent_output": str(result.get("result") or ""),
        **usage(result),
        "output": str(output),
    }
    (output / "result.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print_task_report(report)
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


def mean_or_none(values: list[float | int | None]) -> float | None:
    return (
        mean(value for value in values if value is not None)
        if values and all(value is not None for value in values) else None
    )


def average_items(items: list[dict], reward_field: str) -> dict:
    return {
        "passed": bool(items) and all(item["passed"] for item in items),
        reward_field: mean_or_none([item[reward_field] for item in items]),
        "total_tokens": mean([item["total_tokens"] for item in items]),
        "cost_usd": mean_or_none([item["cost_usd"] for item in items]),
        "agent_seconds": mean([item["agent_seconds"] for item in items]),
        "verifier_seconds": mean(
            [item["verifier_seconds"] for item in items]
        ),
    }


def average_runs(runs: list[dict], modes: tuple[str, ...]) -> dict:
    arms = {}
    for mode in modes:
        run_arms = [run["arms"][mode] for run in runs]
        arm = {"mode": mode, **average_items(run_arms, "reward_mean")}
        arm["tasks"] = []
        for index in range(len(run_arms[0]["tasks"])):
            task_runs = [run_arm["tasks"][index] for run_arm in run_arms]
            task = average_items(task_runs, "reward")
            task["task"] = task_runs[0].get("task")
            task["mode"] = mode
            arm["tasks"].append(task)
        arms[mode] = arm
    return {
        "passed": bool(runs) and all(run["passed"] for run in runs),
        "arms": arms,
    }


def normalize_arms(values: list[str] | None) -> tuple[str, ...]:
    if not values:
        return DEFAULT_COMPARE_ARMS
    requested = []
    for value in values:
        requested.extend(
            part.strip() for part in value.split(",") if part.strip()
        )
    invalid = sorted(set(requested) - set(ARMS))
    if invalid:
        raise ValueError(f"unknown arm(s): {', '.join(invalid)}")
    if not requested:
        raise ValueError("--arms requires at least one arm")
    requested_set = set(requested)
    return tuple(arm for arm in ARMS if arm in requested_set)


def display_arm(mode: str) -> str:
    return {
        "direct": "Direct",
        "cold": "Cold series",
        "cold_true": "True cold",
        "warm": "Warm",
    }[mode]


def percent_change(value: float | int | None,
                   baseline: float | int | None) -> str:
    if value is None or baseline in (None, 0):
        return "unavailable"
    return f"{((value / baseline) - 1) * 100:+.1f}%"


def result_cell(item: dict, reward_field: str) -> str:
    reward = item[reward_field]
    formatted = f"{reward:.3f}" if reward is not None else "unavailable"
    return f"{'PASS' if item['passed'] else 'FAIL'} {formatted}"


def comparison_table(report: dict, labels: list[str]) -> str:
    lines = [
        "| Workload | Arm / result | Tokens | Cost | Latency | "
        "Change vs Direct |",
        "|---|---|---:|---:|---:|---|",
    ]
    task_word = "task" if len(labels) == 1 else "tasks"
    workloads = [(f"All {len(labels)} {task_word}", None)]
    workloads.extend((label, index) for index, label in enumerate(labels))
    direct = report["arms"].get("direct")
    for label, task_index in workloads:
        direct_item = (
            None if direct is None else
            direct if task_index is None else direct["tasks"][task_index]
        )
        reward_field = "reward_mean" if task_index is None else "reward"
        for mode in ARMS:
            arm = report["arms"].get(mode)
            if arm is None:
                continue
            item = arm if task_index is None else arm["tasks"][task_index]
            tokens = item["total_tokens"]
            cost = item["cost_usd"]
            latency = item["agent_seconds"]
            if mode == "direct":
                change = "-"
            elif direct_item is None:
                change = "unavailable (Direct not run)"
            else:
                direct_latency = direct_item["agent_seconds"]
                change = (
                    f"T {percent_change(tokens, direct_item['total_tokens'])} "
                    f"\u007c $ {percent_change(cost, direct_item['cost_usd'])} "
                    f"\u007c L {percent_change(latency, direct_latency)}"
                )
            formatted_cost = (
                f"${cost:.4f}" if cost is not None else "unavailable"
            )
            lines.append(
                f"| {label.replace('|', '&#124;')} | {display_arm(mode)} - "
                f"{result_cell(item, reward_field)} | {tokens:,.0f} | "
                f"{formatted_cost} | {latency:.1f}s | "
                f"{change.replace('|', '&#124;')} |"
            )
    return "\n".join(lines)


def summary_markdown(runs: list[dict], average: dict,
                     labels: list[str]) -> str:
    sections = []
    for index, run in enumerate(runs, 1):
        sections += [f"## Run {index}", "", comparison_table(run, labels), ""]
    sections += [
        f"## Average across {len(runs)} runs", "",
        comparison_table(average, labels), "",
    ]
    return "\n".join(sections)


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
    run_dir = REPO_ROOT / "local_eval" / stamp
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", type=Path, action="append", required=True,
                        help="generated Harbor task directory")
    parser.add_argument("--mode", choices=(*ARMS, "compare"),
                        default="direct")
    parser.add_argument(
        "--arms", nargs="+",
        help="arms to run with --mode compare; defaults to direct cold warm; "
             "use cold_true instead of cold to isolate each task's cache",
    )
    parser.add_argument(
        "--runs", "--repeat", dest="runs", type=positive_int, default=1,
        help="number of independent runs; defaults to 1",
    )
    parser.add_argument(
        "--task-label", action="append",
        help="display label for a --task, in the same order; repeat for each "
             "task",
    )
    parser.add_argument(
        "--cache-home", type=Path,
        default=run_dir / "cache",
        help="collected cache for cold/cold_true and warm; defaults to a new "
             "timestamped directory",
    )
    parser.add_argument("--spec", type=Path,
                        help="eval JSON; valid only with one --task")
    parser.add_argument(
        "--reset-script", type=Path,
        help="executable that restores external state before each arm",
    )
    parser.add_argument("--model", default=os.environ.get("ANTHROPIC_MODEL"),
                        help="Claude model; defaults to ANTHROPIC_MODEL")
    parser.add_argument("--output", type=Path, default=run_dir / "results")
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
        if args.arms and args.mode != "compare":
            raise ValueError("--arms is valid only with --mode compare")
        requested = normalize_arms(args.arms)
        modes = (
            requested
            if args.mode == "compare" else (args.mode,)
        )
        if "cold" in modes and "cold_true" in modes:
            raise ValueError(
                "cold and cold_true cannot run together because Warm would "
                "not have a single unambiguous source cache"
            )
        if args.task_label and len(args.task_label) != len(tasks):
            raise ValueError(
                "--task-label must be repeated once for every --task"
            )
        labels = args.task_label or [task.name for task in tasks]
        if args.mode == "compare" and not args.reset_script:
            raise ValueError("compare mode requires --reset-script")
        reset_script = (
            args.reset_script.expanduser().resolve() if args.reset_script else None
        )
        if reset_script and not reset_script.is_file():
            raise FileNotFoundError(f"reset script not found: {reset_script}")
        output.mkdir(parents=True, exist_ok=False)
        runs = []
        for run_index in range(1, args.runs + 1):
            run_output = (
                output if args.runs == 1 else output / f"run-{run_index:02d}"
            )
            if args.runs > 1:
                run_output.mkdir()
            run_cache = (
                cache_home if args.runs == 1 or cache_home is None else
                cache_home / f"run-{run_index:02d}"
            )
            if "cold" in modes:
                check_cache("cold", run_cache)
            elif "cold_true" in modes:
                check_cache("cold_true", run_cache)
            elif "warm" in modes:
                check_cache("warm", run_cache)
            summaries = {}
            for mode in modes:
                arm_output = run_output / mode
                arm_output.mkdir()
                if reset_script:
                    run_reset(reset_script, arm_output, args.reset_timeout)
                if mode == "warm":
                    check_cache("warm", run_cache)
                reports = []
                for index, task in enumerate(tasks, 1):
                    task_cache = run_cache
                    if mode == "cold_true":
                        task_cache = (
                            arm_output / "isolated-caches" /
                            f"{index:02d}-{task.name}"
                        )
                        check_cache("cold_true", task_cache)
                    report = evaluate_task(
                        task, mode, task_cache,
                        arm_output / f"{index:02d}-{task.name}",
                        args.model, args.agent_timeout, args.verifier_timeout,
                        args.spec,
                    )
                    reports.append(report)
                    if mode == "cold_true":
                        merge_procedure_cache(task_cache, run_cache)
                summaries[mode] = summarize(mode, reports)
            runs.append({
                "run": run_index,
                "passed": len(summaries) == len(modes) and all(
                    value["passed"] for value in summaries.values()
                ),
                "arms": summaries,
                "cache_home": str(run_cache) if run_cache else None,
                "output": str(run_output),
            })
        average = average_runs(runs, modes)
        report = {
            "passed": average["passed"],
            "run_count": args.runs,
            "runs": runs,
            "average": average,
            "arms": average["arms"],
            "cache_home": str(cache_home) if cache_home else None,
            "output": str(output),
        }
        (output / "result.json").write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        markdown = summary_markdown(runs, average, labels)
        (output / "summary.md").write_text(markdown, encoding="utf-8")
        print(f"\n{markdown}")
        print(f"\nFull result: {output / 'result.json'}")
        print(f"Markdown summary: {output / 'summary.md'}")
        return 0 if report["passed"] else 1
    except (FileNotFoundError, RuntimeError, ValueError, OSError,
            subprocess.SubprocessError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
