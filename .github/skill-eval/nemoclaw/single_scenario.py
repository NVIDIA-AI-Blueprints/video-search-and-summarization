#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Generate and run one representative NemoClaw Harbor skill evaluation."""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]
SKILL_EVAL_ROOT = REPO_ROOT / ".github/skill-eval"
if str(SKILL_EVAL_ROOT) not in sys.path:
    sys.path.insert(0, str(SKILL_EVAL_ROOT))

from run_leg import HARBOR_REQUIREMENT, run_command  # noqa: E402

DEFAULT_DATASET_ROOT = Path("/tmp/skill-eval/datasets/nemoclaw")
DEFAULT_RESULTS_ROOT = Path("/tmp/skill-eval/results/nemoclaw")
PROFILE = "base"
PLATFORM = "RTXPRO6000BW"
TASK_NAME = "rtxpro6000bw"
INSTANCE_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
ENV_BUILD_BASE_SECONDS = 600
ENV_BUILD_MULTIPLIER = 10
VERIFIER_BUDGET_SECONDS = 1800
CLEANUP_BUDGET_SECONDS = 600
DEFAULT_HARBOR_TIMEOUT_SECONDS = 12600


def _run(
    cmd: list[str],
    *,
    timeout: int,
    env: dict[str, str] | None = None,
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=REPO_ROOT,
        stdin=subprocess.DEVNULL if input_text is None else None,
        input=input_text,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
        check=False,
    )


def _parse_json_list(raw: str) -> list[dict[str, Any]]:
    start = raw.find("[")
    end = raw.rfind("]")
    if start < 0 or end < start:
        return []
    try:
        value = json.loads(raw[start : end + 1])
    except json.JSONDecodeError:
        return []
    return value if isinstance(value, list) else []


def _validate_instance(instance: str) -> None:
    if INSTANCE_PATTERN.fullmatch(instance) is None:
        raise ValueError("nemoclaw instance name contains unsupported characters")

    nodes = _run(["brev", "ls", "nodes", "--json"], timeout=45)
    registered = {
        str(item.get("name") or "").lower()
        for item in _parse_json_list(nodes.stdout)
    }
    if instance.lower() in registered:
        probe = _run(
            [
                "ssh",
                "-o",
                "BatchMode=yes",
                "-o",
                "ConnectTimeout=15",
                "-o",
                "StrictHostKeyChecking=no",
                instance.lower(),
                "echo harbor-ready",
            ],
            timeout=45,
        )
    else:
        probe = _run(
            ["brev", "exec", instance, "echo harbor-ready"],
            timeout=60,
            input_text="\n",
        )
    if probe.returncode != 0 or "harbor-ready" not in probe.stdout:
        raise RuntimeError(f"cannot reach explicit NemoClaw worker {instance!r}")


def _generate_dataset(dataset_root: Path) -> Path:
    shutil.rmtree(dataset_root, ignore_errors=True)
    command = [
        sys.executable,
        ".github/skill-eval/adapters/vss-deploy-profile/generate.py",
        "--output-dir",
        str(dataset_root),
        "--skill-dir",
        "skills/vss-deploy-profile",
        "--profile",
        PROFILE,
        "--platform",
        PLATFORM,
    ]
    result = _run(command, timeout=120, env=os.environ.copy())
    if result.returncode != 0:
        raise RuntimeError(
            "dataset generation failed: "
            + (result.stderr or result.stdout)[-1000:]
        )
    task_dir = dataset_root / PROFILE / TASK_NAME
    if not (task_dir / "task.toml").is_file():
        raise RuntimeError("dataset generator did not produce the expected task")
    return task_dir


def _toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list):
        return "[" + ", ".join(json.dumps(str(item)) for item in value) + "]"
    return json.dumps(str(value))


def _upsert_metadata(task_toml: Path, updates: dict[str, Any]) -> None:
    lines = task_toml.read_text(encoding="utf-8").splitlines()
    try:
        start = lines.index("[metadata]")
    except ValueError as exc:
        raise RuntimeError("generated task has no metadata table") from exc
    end = len(lines)
    for index in range(start + 1, len(lines)):
        if lines[index].startswith("[") and lines[index].endswith("]"):
            end = index
            break
    update_keys = set(updates)
    kept = [
        line
        for line in lines[start + 1 : end]
        if line.split("=", 1)[0].strip() not in update_keys
    ]
    additions = [
        f"{key} = {_toml_value(value)}" for key, value in updates.items()
    ]
    task_toml.write_text(
        "\n".join(lines[: start + 1] + kept + additions + lines[end:]).rstrip()
        + "\n",
        encoding="utf-8",
    )


def _wrap_task(task_dir: Path, agent_timeout: int) -> None:
    instruction = (task_dir / "instruction.md").read_text(encoding="utf-8")
    prompt = (
        "You are running the representative VSS skill evaluation inside "
        "NemoClaw/OpenClaw.\n\n"
        "Use the /vss-deploy-profile skill as the primary workflow. "
        "Deploy the VSS base profile on RTXPRO6000BW autonomously. Use the "
        "VSS Orchestrator MCP tools for prerequisites, compose generation, "
        "deployment, and status polling. Do not deploy with raw docker compose "
        "or dev-profile.sh commands. Poll docker_status until the operation is "
        "terminal, then summarize the final deployment state.\n\n"
        "This trial reserves exactly one GPU. Leave LLM_DEVICE_ID and "
        "VLM_DEVICE_ID unset so the profile uses its supported shared "
        "placement on GPU 0.\n\n"
        "Original eval request:\n\n"
        f"{instruction.strip()}\n"
    )
    tests_dir = task_dir / "tests"
    tests_dir.mkdir(exist_ok=True)
    (tests_dir / "nemoclaw_prompt.md").write_text(prompt, encoding="utf-8")
    (task_dir / "instruction.md").write_text(
        "This Harbor task is an opt-in NemoClaw launcher. The environment "
        "must bypass outer Claude and run the checked-in headless launcher. "
        "Expected command: python3 .github/skill-eval/nemoclaw/"
        "headless_runner.py --prompt-file /tests/nemoclaw_prompt.md "
        f"--timeout {agent_timeout}.\n",
        encoding="utf-8",
    )
    _upsert_metadata(
        task_dir / "task.toml",
        {
            "runner": "nemoclaw",
            "requires_nemoclaw": True,
            "requires_mcp": True,
            "expected_skill": "vss-deploy-profile",
            "deployment_profile": PROFILE,
            "required_mcp_tools": [
                "vss_orchestrator__prereqs",
                "vss_orchestrator__docker_generate",
                "vss_orchestrator__docker_up",
                "vss_orchestrator__docker_status",
            ],
        },
    )


def _uvx() -> str:
    found = shutil.which("uvx")
    if found:
        return found
    install = _run(
        [sys.executable, "-m", "pip", "install", "--user", "--quiet", "uv"],
        timeout=180,
    )
    if install.returncode != 0 or not (found := shutil.which("uvx")):
        raise RuntimeError("uvx is unavailable")
    return found


def _validate_timeouts(
    *,
    setup_timeout: int,
    agent_timeout: int,
    harbor_timeout: int,
) -> None:
    environment_budget = ENV_BUILD_BASE_SECONDS * ENV_BUILD_MULTIPLIER
    if setup_timeout >= environment_budget:
        raise ValueError("environment-build budget must exceed setup timeout")
    required_harbor = (
        setup_timeout
        + agent_timeout
        + VERIFIER_BUDGET_SECONDS
        + CLEANUP_BUDGET_SECONDS
    )
    if harbor_timeout <= required_harbor:
        raise ValueError(
            "Harbor timeout must exceed setup, OpenClaw, verifier, and cleanup"
        )


def _harbor_command(
    dataset_root: Path,
    results_root: Path,
    run_id: str,
) -> list[str]:
    model = os.environ.get("ANTHROPIC_MODEL", "").strip()
    if not model:
        raise RuntimeError("ANTHROPIC_MODEL is required")
    command = [
        _uvx(),
        "--python",
        sys.executable,
        "--from",
        HARBOR_REQUIREMENT,
        "harbor",
        "run",
        "--environment-import-path",
        "envs.nemoclaw_brev_env:NemoClawBrevEnvironment",
        "-p",
        str(dataset_root / PROFILE),
        "--include-task-name",
        TASK_NAME,
        "-a",
        "claude-code",
        "--model",
        model,
    ]
    base_url = os.environ.get("ANTHROPIC_BASE_URL", "").strip().rstrip("/")
    if base_url:
        api_base = base_url if base_url.endswith("/v1") else f"{base_url}/v1"
        command += ["--ak", f"api_base={api_base}"]
    command += [
        "--ae",
        "CLAUDE_CODE_DISABLE_THINKING=1",
        "--environment-build-timeout-multiplier",
        str(float(ENV_BUILD_MULTIPLIER)),
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
    return command


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _latest_trial(run_root: Path) -> tuple[Path | None, dict[str, Any]]:
    paths = sorted(
        (
            path
            for path in run_root.rglob("result.json")
            if (path.parent / "verifier/reward.txt").is_file()
        ),
        key=lambda path: path.stat().st_mtime,
    )
    if not paths:
        return None, {}
    return paths[-1].parent, _read_json(paths[-1])


def _reward(trial: Path | None) -> float | None:
    if trial is None:
        return None
    try:
        return float(
            (trial / "verifier/reward.txt").read_text(encoding="utf-8").strip()
        )
    except (OSError, ValueError):
        return None


def _inner_metrics(trial: Path | None) -> dict[str, Any]:
    if trial is None:
        return {}
    candidates = (
        trial / "artifacts/logs/artifacts/nemoclaw/metrics.json",
        trial / "artifacts/nemoclaw/metrics.json",
    )
    for path in candidates:
        value = _read_json(path)
        if value:
            return value
    trajectory = _read_json(trial / "agent/trajectory.json")
    final = trajectory.get("final_metrics")
    if isinstance(final, dict):
        total_prompt = final.get("total_prompt_tokens")
        cached = final.get("total_cached_tokens")
        uncached_prompt = (
            max(0, total_prompt - cached)
            if isinstance(total_prompt, (int, float))
            and not isinstance(total_prompt, bool)
            and isinstance(cached, (int, float))
            and not isinstance(cached, bool)
            else None
        )
        return {
            "turns": sum(
                step.get("source") == "agent"
                for step in trajectory.get("steps", [])
                if isinstance(step, dict)
            ),
            "prompt_tokens": uncached_prompt,
            "cached_tokens": cached,
        }
    return {}


def _format_number(value: Any) -> str:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return "n/a"
    if number >= 1_000_000:
        return f"{number / 1_000_000:.1f}M"
    if number >= 1_000:
        return f"{number / 1_000:.1f}k"
    return str(number)


def _native_metrics_valid(metrics: dict[str, Any]) -> bool:
    values = (
        metrics.get("turns"),
        metrics.get("prompt_tokens"),
        metrics.get("cached_tokens"),
    )
    if any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in values):
        return False
    return (
        values[0] > 0
        and values[1] >= 0
        and values[2] >= 0
        and values[1] + values[2] > 0
    )


def _report(
    *,
    instance: str,
    run_id: str,
    results_root: Path,
    harbor_rc: int,
    elapsed: float,
) -> tuple[float | None, Path | None, bool]:
    trial, _result = _latest_trial(results_root / run_id)
    reward = _reward(trial)
    metrics = _inner_metrics(trial)
    metrics_ok = _native_metrics_valid(metrics)
    passed = (
        harbor_rc == 0
        and reward is not None
        and reward >= 1.0
        and metrics_ok
    )
    reward_text = f"{reward:.3g}" if reward is not None else "missing"
    duration = f"{int(elapsed // 60)}m {int(elapsed % 60)}s"
    repo = os.environ.get("GITHUB_REPOSITORY") or os.environ.get("PR_REPO")
    trace = (
        f"[workflow](https://github.com/{repo}/actions/runs/{run_id})"
        if repo and run_id.isdigit()
        else "n/a"
    )
    report = "\n".join(
        [
            "## Harbor Eval - skills/vss-deploy-profile/evals/base.json",
            "",
            f"Platform RTXPRO6000BW - instance {instance} - runtime NemoClaw/OpenClaw",
            "",
            "| Platform | Result | Reward | Duration | Turns | Prompt tok | Cached tok | Trace |",
            "|---|---|---|---|---|---|---|---|",
            (
                f"| RTXPRO6000BW | {'PASS' if passed else 'FAIL'} | "
                f"{reward_text} | {duration} | "
                f"{_format_number(metrics.get('turns'))} | "
                f"{_format_number(metrics.get('prompt_tokens'))} | "
                f"{_format_number(metrics.get('cached_tokens'))} | {trace} |"
            ),
            "",
            "- Runtime path: Harbor -> NemoClaw/OpenClaw -> VSS Orchestrator MCP",
            f"- Harbor exit code: {harbor_rc}",
            f"- Native OpenClaw metrics: {'present' if metrics_ok else 'missing'}",
            f"- Result: {trial / 'result.json' if trial else 'missing'}",
            "",
        ]
    )
    if summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        with Path(summary).open("a", encoding="utf-8") as handle:
            handle.write(report + "\n")
    else:
        print(report)
    benchmark_dir = Path("/tmp/skill-eval") / run_id
    benchmark_dir.mkdir(parents=True, exist_ok=True)
    (benchmark_dir / "benchmark.md").write_text(
        "# Skills Eval Benchmark - NemoClaw\n\n" + report,
        encoding="utf-8",
    )
    return reward, trial, metrics_ok


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--instance", required=True)
    parser.add_argument("--dataset-root", default=str(DEFAULT_DATASET_ROOT))
    parser.add_argument("--results-root", default=str(DEFAULT_RESULTS_ROOT))
    parser.add_argument(
        "--agent-timeout",
        type=int,
        default=int(os.environ.get("NEMOCLAW_AGENT_TIMEOUT_SEC", "3300")),
    )
    parser.add_argument(
        "--harbor-timeout",
        type=int,
        default=int(
            os.environ.get(
                "NEMOCLAW_HARBOR_TIMEOUT_SEC",
                str(DEFAULT_HARBOR_TIMEOUT_SECONDS),
            )
        ),
    )
    args = parser.parse_args(argv)

    run_id = os.environ.get("GITHUB_RUN_ID", f"local-{int(time.time())}")
    dataset_root = Path(args.dataset_root)
    results_root = Path(args.results_root)
    setup_timeout = int(os.environ.get("NEMOCLAW_SETUP_TIMEOUT_SEC", "5400"))
    _validate_timeouts(
        setup_timeout=setup_timeout,
        agent_timeout=args.agent_timeout,
        harbor_timeout=args.harbor_timeout,
    )
    os.environ.update(
        {
            "BREV_INSTANCE": args.instance,
            "SKILLS_EVAL_RUNNER": "nemoclaw",
            "NEMOCLAW_AGENT_TIMEOUT_SEC": str(args.agent_timeout),
            "PYTHONPATH": (
                f"{SKILL_EVAL_ROOT}:{os.environ.get('PYTHONPATH', '')}"
            ),
        }
    )
    (results_root / run_id).mkdir(parents=True, exist_ok=True)

    _validate_instance(args.instance)
    task_dir = _generate_dataset(dataset_root)
    _wrap_task(task_dir, args.agent_timeout)
    command = _harbor_command(dataset_root, results_root, run_id)
    started = time.monotonic()
    # Reuse the existing harness process-tree contract. It registers detached
    # Brev/SSH transport groups and reaps them together with Harbor on timeout.
    harbor_rc = run_command(
        command,
        os.environ.copy(),
        args.harbor_timeout,
    )
    elapsed = time.monotonic() - started
    reward, trial, metrics_ok = _report(
        instance=args.instance,
        run_id=run_id,
        results_root=results_root,
        harbor_rc=harbor_rc,
        elapsed=elapsed,
    )
    if (
        harbor_rc == 0
        and reward is not None
        and trial is not None
        and metrics_ok
    ):
        print(
            f"NemoClaw Harbor eval produced result.json with reward={reward}",
            flush=True,
        )
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
