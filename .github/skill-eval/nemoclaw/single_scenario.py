#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Run the selected VSS skill-eval plan serially through NemoClaw and Harbor."""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import site
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, NamedTuple

import tomllib

REPO_ROOT = Path(__file__).resolve().parents[3]
SKILL_EVAL_ROOT = REPO_ROOT / ".github/skill-eval"
ADAPTERS_ROOT = SKILL_EVAL_ROOT / "adapters"
SKILLS_ROOT = REPO_ROOT / "skills"
if str(SKILL_EVAL_ROOT) not in sys.path:
    sys.path.insert(0, str(SKILL_EVAL_ROOT))

from run_leg import HARBOR_REQUIREMENT, run_command

DEFAULT_DATASET_ROOT = Path("/tmp/skill-eval/datasets/nemoclaw")
DEFAULT_RESULTS_ROOT = Path("/tmp/skill-eval/results/nemoclaw")
DEFAULT_SCRATCH_ROOT = Path("/tmp/skill-eval")
INSTANCE_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
SLUG_PATTERN = re.compile(r"[A-Za-z0-9_-]+")
ENV_BUILD_BASE_SECONDS = 600
ENV_BUILD_MULTIPLIER = 10
VERIFIER_BUDGET_SECONDS = 1800
CLEANUP_BUDGET_SECONDS = 600
DEFAULT_HARBOR_TIMEOUT_SECONDS = 12600


class MatrixRow(NamedTuple):
    skill: str
    spec: str
    spec_file: str
    platform: str
    kind: str
    slug: str

    @property
    def spec_path(self) -> Path:
        return (
            REPO_ROOT / self.spec_file if self.spec_file else SKILLS_ROOT / self.skill
        )

    @property
    def report_path(self) -> str:
        return self.spec_file or f"skills/{self.skill}"


class Scenario(NamedTuple):
    row: MatrixRow
    task_dir: Path
    gpu_count: int

    @property
    def slug(self) -> str:
        return _safe_slug(f"{self.row.slug}-{self.task_dir.name}")


def _safe_slug(value: str) -> str:
    slug = "".join(
        character if character.isalnum() else "-" for character in value.lower()
    )
    return slug.strip("-") or "scenario"


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
        str(item.get("name") or "").lower() for item in _parse_json_list(nodes.stdout)
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


def _rows_from_plan(document: object) -> list[MatrixRow]:
    if not isinstance(document, dict):
        raise TypeError("shared eval plan must be an object")
    planned = document.get("include")
    if not isinstance(planned, list) or not planned:
        raise ValueError("shared eval plan must contain a non-empty include list")
    rows: list[MatrixRow] = []
    seen: set[str] = set()
    for index, item in enumerate(planned):
        if not isinstance(item, dict):
            raise TypeError(f"shared eval plan row {index} must be an object")
        kind = str(item.get("kind") or "")
        skill = str(item.get("skill") or "")
        spec = str(item.get("spec_stem") or "")
        spec_file = str(item.get("spec_path") or "")
        platform = str(item.get("platform") or "")
        slug = str(item.get("slug") or "")
        if kind not in {"eval", "missing_adapter"}:
            raise ValueError(f"unsupported skill-eval plan kind: {kind!r}")
        if not skill or not spec or not slug or SLUG_PATTERN.fullmatch(slug) is None:
            raise ValueError(f"invalid skill-eval plan row: {item!r}")
        if kind == "eval" and not spec_file:
            raise ValueError(f"incomplete skill-eval plan row: {item!r}")
        if slug in seen:
            raise ValueError(f"duplicate skill-eval plan slug: {slug!r}")
        seen.add(slug)
        rows.append(MatrixRow(skill, spec, spec_file, platform, kind, slug))
    return rows


def _load_plan_file(path: Path) -> list[MatrixRow]:
    return _rows_from_plan(json.loads(path.read_text(encoding="utf-8")))


def _matrix_json(rows: list[MatrixRow]) -> str:
    return json.dumps(
        [
            {
                "skill": row.skill,
                "spec": row.spec,
                "spec_path": row.spec_file,
                "platform": row.platform,
                "kind": row.kind,
                "slug": row.slug,
            }
            for row in rows
        ],
        indent=2,
        sort_keys=True,
    )


def _task_dir_sort_key(task_dir: Path) -> tuple[str, int, str]:
    if task_dir.name.startswith("step-"):
        try:
            return (
                str(task_dir.parent),
                int(task_dir.name.split("-", 1)[1]),
                task_dir.name,
            )
        except ValueError:
            pass
    return (str(task_dir.parent), 0, task_dir.name)


def _adapter_help(adapter: Path) -> str:
    result = _run([sys.executable, str(adapter), "--help"], timeout=45)
    if result.returncode != 0:
        raise RuntimeError(f"{adapter}: --help exited {result.returncode}")
    return f"{result.stdout}\n{result.stderr}"


def _generate_dataset(row: MatrixRow, dataset_root: Path) -> list[Path]:
    adapter = ADAPTERS_ROOT / row.skill / "generate.py"
    if row.kind != "eval":
        raise RuntimeError(f"missing Harbor adapter for {row.skill}")
    if not row.platform:
        raise RuntimeError(f"{row.report_path}: spec declares no platforms")
    if not adapter.is_file():
        raise RuntimeError(f"missing Harbor adapter for {row.skill}")
    if not row.spec_path.is_file():
        raise RuntimeError(f"missing eval spec {row.spec_path.relative_to(REPO_ROOT)}")

    shutil.rmtree(dataset_root, ignore_errors=True)
    dataset_root.mkdir(parents=True, exist_ok=True)
    help_text = _adapter_help(adapter)
    command = [
        sys.executable,
        str(adapter.relative_to(REPO_ROOT)),
        "--output-dir",
        str(dataset_root),
        "--skill-dir",
        str((SKILLS_ROOT / row.skill).relative_to(REPO_ROOT)),
    ]
    missing_options = [
        option
        for option in ("--skill-dir", "--spec", "--platform")
        if option not in help_text
    ]
    if missing_options:
        raise RuntimeError(
            f"{row.skill}: adapter is missing common option(s): "
            + ", ".join(missing_options)
        )
    command.extend(
        [
            "--spec",
            str(row.spec_path.relative_to(REPO_ROOT)),
            "--platform",
            row.platform,
        ]
    )

    env = os.environ.copy()
    env["SKILLS_EVAL_RUNNER"] = "nemoclaw"
    print("[nemoclaw-ci] generating dataset:", " ".join(command), flush=True)
    result = _run(command, timeout=180, env=env)
    if result.stdout:
        print(result.stdout, end="", flush=True)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "")[-1000:]
        raise RuntimeError(
            f"{row.skill}/{row.spec}: adapter exited {result.returncode}: {detail}"
        )

    task_dirs = sorted(
        (path.parent for path in dataset_root.rglob("task.toml")),
        key=_task_dir_sort_key,
    )
    if not task_dirs:
        raise RuntimeError(f"{row.skill}/{row.spec}: adapter generated no tasks")
    return task_dirs


def _toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(item) for item in value) + "]"
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
    additions = [f"{key} = {_toml_value(value)}" for key, value in updates.items()]
    task_toml.write_text(
        "\n".join(lines[: start + 1] + kept + additions + lines[end:]).rstrip() + "\n",
        encoding="utf-8",
    )


def _task_metadata(task_dir: Path) -> dict[str, Any]:
    try:
        value = tomllib.loads((task_dir / "task.toml").read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return {}
    metadata = value.get("metadata")
    return metadata if isinstance(metadata, dict) else {}


def _gpu_resource_guidance(gpu_count: int) -> str:
    if gpu_count <= 0:
        return (
            "This trial reserves no GPUs. Use the configured remote model endpoints "
            "and never request a local GPU device."
        )
    if gpu_count == 1:
        return (
            "This trial reserves exactly 1 GPU; the only valid device ID is 0. "
            "Leave GPU device-ID overrides unset so profile defaults use shared "
            "placement on GPU 0. Never request GPU 1 or another device."
        )
    return (
        f"This trial reserves exactly {gpu_count} GPUs; valid device IDs are 0 "
        f"through {gpu_count - 1}. Never request an out-of-range device."
    )


def _nemoclaw_prompt(
    original_instruction: str,
    gpu_count: int,
) -> str:
    return (
        "You are running inside automated GitHub VSS skill evaluation with "
        "NemoClaw/OpenClaw.\n\n"
        "Run autonomously without asking for confirmation. Follow the original "
        "eval request, its checked-in skill instructions, and the available "
        "tools. The VSS Orchestrator MCP server is available when the eval "
        "request requires host-side operations.\n\n"
        "## GPU resource boundary\n\n"
        f"{_gpu_resource_guidance(gpu_count)}\n\n"
        "## Original eval request\n\n"
        f"{original_instruction.strip()}\n"
    )


def _wrap_task(task_dir: Path, row: MatrixRow, agent_timeout: int) -> Scenario:
    metadata = _task_metadata(task_dir)
    try:
        gpu_count = int(metadata.get("gpu_count", 1))
    except (TypeError, ValueError):
        gpu_count = 1
    instruction_path = task_dir / "instruction.md"
    original_instruction = instruction_path.read_text(encoding="utf-8")
    tests_dir = task_dir / "tests"
    tests_dir.mkdir(exist_ok=True)
    (tests_dir / "nemoclaw_prompt.md").write_text(
        _nemoclaw_prompt(original_instruction, gpu_count),
        encoding="utf-8",
    )
    instruction_path.write_text(
        "This Harbor task is an opt-in NemoClaw launcher. The environment must "
        "bypass outer Claude and run the checked-in headless launcher. Expected "
        "command: python3 .github/skill-eval/nemoclaw/headless_runner.py "
        "--prompt-file /tests/nemoclaw_prompt.md "
        f"--timeout {agent_timeout}.\n",
        encoding="utf-8",
    )
    _upsert_metadata(task_dir / "task.toml", {"runner": "nemoclaw"})
    return Scenario(row=row, task_dir=task_dir, gpu_count=gpu_count)


def _uvx() -> str:
    candidates = (
        shutil.which("uvx"),
        str(Path(sys.executable).parent / "uvx"),
        str(Path(site.getuserbase()) / "bin" / "uvx"),
    )
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return candidate
    install = _run(
        [sys.executable, "-m", "pip", "install", "--user", "--quiet", "uv"],
        timeout=180,
    )
    if install.returncode == 0:
        for candidate in (
            shutil.which("uvx"),
            str(Path(sys.executable).parent / "uvx"),
            str(Path(site.getuserbase()) / "bin" / "uvx"),
        ):
            if candidate and Path(candidate).is_file():
                return candidate
    detail = (install.stderr or install.stdout or "")[-500:]
    raise RuntimeError(f"uvx is unavailable after installation: {detail}")


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
        setup_timeout + agent_timeout + VERIFIER_BUDGET_SECONDS + CLEANUP_BUDGET_SECONDS
    )
    if harbor_timeout <= required_harbor:
        raise ValueError(
            "Harbor timeout must exceed setup, OpenClaw, verifier, and cleanup"
        )


def _harbor_command(scenario: Scenario, output_root: Path) -> list[str]:
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
        str(scenario.task_dir.parent),
        "--include-task-name",
        scenario.task_dir.name,
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
        str(output_root),
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
            if (path.parent / "trial.log").is_file()
            or (path.parent / "exception.txt").is_file()
            or (path.parent / "verifier/reward.txt").is_file()
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
        value = float((trial / "verifier/reward.txt").read_text().strip())
    except (OSError, ValueError):
        return None
    return value if math.isfinite(value) and 0.0 <= value <= 1.0 else None


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
    return {}


def _trial_succeeded(result: dict[str, Any]) -> bool:
    return (
        bool(result) and "exception_info" in result and result["exception_info"] is None
    )


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
    if any(
        isinstance(value, bool) or not isinstance(value, (int, float))
        for value in values
    ):
        return False
    return (
        values[0] > 0
        and values[1] >= 0
        and values[2] >= 0
        and values[1] + values[2] > 0
    )


def _trace_link(run_id: str) -> str:
    repo = os.environ.get("GITHUB_REPOSITORY") or os.environ.get("PR_REPO")
    if repo and run_id.isdigit():
        return f"[workflow](https://github.com/{repo}/actions/runs/{run_id})"
    return "n/a"


def _append_report(report: str, benchmark: Path) -> None:
    if summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        with Path(summary).open("a", encoding="utf-8") as handle:
            handle.write(report.rstrip() + "\n\n")
    else:
        print(report, flush=True)
    with benchmark.open("a", encoding="utf-8") as handle:
        handle.write(report.rstrip() + "\n\n---\n\n")


def _scenario_report(
    *,
    scenario: Scenario,
    instance: str,
    run_id: str,
    output_root: Path,
    harbor_rc: int,
    elapsed: float,
    benchmark: Path,
) -> dict[str, Any]:
    trial, result = _latest_trial(output_root)
    reward = _reward(trial)
    metrics = _inner_metrics(trial)
    metrics_ok = _native_metrics_valid(metrics)
    trial_ok = _trial_succeeded(result)
    complete = harbor_rc == 0 and trial_ok and reward is not None and metrics_ok
    status = "PASS" if complete and reward >= 1.0 else "FAIL" if complete else "ERROR"
    reward_text = f"{reward:.3g}" if reward is not None else "missing"
    duration = f"{int(elapsed // 60)}m {int(elapsed % 60)}s"
    report = "\n".join(
        [
            f"## Harbor Eval - `{scenario.row.report_path}`",
            "",
            (
                f"Skill `{scenario.row.skill}` - task `{scenario.task_dir.name}` - "
                f"platform `{scenario.row.platform}` - instance `{instance}` - "
                "runtime `NemoClaw/OpenClaw`"
            ),
            "",
            "| Platform | Result | Reward | Duration | Turns | Prompt tok | Cached tok | Trace |",
            "|---|---|---|---|---|---|---|---|",
            (
                f"| {scenario.row.platform} | {status} | {reward_text} | {duration} | "
                f"{_format_number(metrics.get('turns'))} | "
                f"{_format_number(metrics.get('prompt_tokens'))} | "
                f"{_format_number(metrics.get('cached_tokens'))} | "
                f"{_trace_link(run_id)} |"
            ),
            "",
            "- Runtime path: Harbor -> NemoClaw/OpenClaw -> VSS Orchestrator MCP",
            f"- Harbor exit code: `{harbor_rc}`",
            f"- Harbor trial execution: `{'successful' if trial_ok else 'failed'}`",
            f"- Native OpenClaw metrics: `{'present' if metrics_ok else 'missing'}`",
            f"- Result: `{trial / 'result.json' if trial else 'missing'}`",
        ]
    )
    _append_report(report, benchmark)
    return {
        "skill": scenario.row.skill,
        "spec": scenario.row.spec,
        "platform": scenario.row.platform,
        "task": scenario.task_dir.name,
        "status": status,
        "harness_complete": complete,
        "harbor_exit_code": harbor_rc,
        "reward": reward,
        "turns": metrics.get("turns"),
        "prompt_tokens": metrics.get("prompt_tokens"),
        "cached_tokens": metrics.get("cached_tokens"),
        "duration_seconds": round(elapsed, 3),
        "result": str(trial / "result.json") if trial else None,
    }


def _error_report(
    *,
    row: MatrixRow,
    task: str,
    reason: str,
    instance: str,
    benchmark: Path,
) -> dict[str, Any]:
    report = "\n".join(
        [
            f"## Harbor Eval - `{row.report_path}`",
            "",
            f"Skill `{row.skill}` - task `{task}` - instance `{instance}` - runtime `NemoClaw/OpenClaw`",
            "",
            "| Platform | Result | Reward | Duration | Turns | Prompt tok | Cached tok | Trace |",
            "|---|---|---|---|---|---|---|---|",
            f"| {row.platform} | ERROR | missing | n/a | n/a | n/a | n/a | n/a |",
            "",
            f"- Controlled error: `{reason}`",
        ]
    )
    _append_report(report, benchmark)
    return {
        "skill": row.skill,
        "spec": row.spec,
        "platform": row.platform,
        "task": task,
        "status": "ERROR",
        "harness_complete": False,
        "reason": reason,
    }


def _skipped_record(scenario: Scenario, reason: str) -> dict[str, Any]:
    return {
        "skill": scenario.row.skill,
        "spec": scenario.row.spec,
        "platform": scenario.row.platform,
        "task": scenario.task_dir.name,
        "status": "SKIPPED",
        "harness_complete": True,
        "reason": reason,
    }


def _blocks_dependent_scenarios(record: dict[str, Any]) -> bool:
    """Stop a dependent chain only when the harness did not produce a result."""
    return record.get("status") == "ERROR"


def _row_status(row_records: list[dict[str, Any]]) -> str:
    statuses = {record["status"] for record in row_records}
    if not statuses:
        return "MISSING"
    if "ERROR" in statuses:
        return "ERROR"
    if "FAIL" in statuses:
        return "FAIL"
    if "SKIPPED" in statuses:
        return "SKIPPED"
    return "PASS"


def _write_aggregate(
    *,
    rows: list[MatrixRow],
    records: list[dict[str, Any]],
    run_id: str,
    benchmark: Path,
    verdict: Path,
) -> None:
    row_results: list[dict[str, Any]] = []
    table = [
        "## NemoClaw skill-eval aggregate",
        "",
        "| Skill / spec / platform | Planned tasks | Executed | Result |",
        "|---|---:|---:|---|",
    ]
    for row in rows:
        row_records = [
            record
            for record in records
            if record["skill"] == row.skill
            and record["spec"] == row.spec
            and record["platform"] == row.platform
        ]
        status = _row_status(row_records)
        planned = sum(
            record["task"] not in {"dataset-generation", "worker-validation"}
            for record in row_records
        )
        executed = sum(
            record["status"] not in {"SKIPPED"}
            and record["task"] not in {"dataset-generation", "worker-validation"}
            for record in row_records
        )
        table.append(
            f"| `{row.skill}/{row.spec}/{row.platform or 'n/a'}` | "
            f"{planned} | {executed} | {status} |"
        )
        row_results.append(
            {
                "skill": row.skill,
                "spec": row.spec,
                "platform": row.platform,
                "planned_tasks": planned,
                "executed_tasks": executed,
                "status": status,
            }
        )
    counts = {
        status: sum(record["status"] == status for record in records)
        for status in ("PASS", "FAIL", "ERROR", "SKIPPED")
    }
    complete_results = counts["PASS"] + counts["FAIL"]
    table.extend(
        [
            "",
            f"- Planned spec/platform rows: `{len(rows)}`",
            f"- Planned Harbor tasks: `{sum(row['planned_tasks'] for row in row_results)}`",
            f"- Harbor results with native metrics: `{complete_results}`",
            f"- Semantic PASS / FAIL: `{counts['PASS']} / {counts['FAIL']}`",
            f"- Execution errors / dependent skips: `{counts['ERROR']} / {counts['SKIPPED']}`",
            f"- Workflow: `{_trace_link(run_id)}`",
        ]
    )
    aggregate = "\n".join(table)
    if summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        with Path(summary).open("a", encoding="utf-8") as handle:
            handle.write(aggregate + "\n")
    with benchmark.open("a", encoding="utf-8") as handle:
        handle.write(aggregate + "\n")
    verdict.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "runtime": "nemoclaw",
                "run_id": run_id,
                "rows": row_results,
                "counts": counts,
                "scenarios": records,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    print(aggregate, flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--instance", default=os.environ.get("NEMOCLAW_INSTANCE", ""))
    parser.add_argument("--plan-file", required=True)
    parser.add_argument("--dataset-root", default=str(DEFAULT_DATASET_ROOT))
    parser.add_argument("--results-root", default=str(DEFAULT_RESULTS_ROOT))
    parser.add_argument("--scratch-root", default=str(DEFAULT_SCRATCH_ROOT))
    parser.add_argument("--print-matrix", action="store_true")
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

    rows = _load_plan_file(Path(args.plan_file))
    if args.print_matrix:
        print(_matrix_json(rows))
        return 0
    if not args.instance:
        parser.error("--instance is required for execution")

    run_id = os.environ.get("GITHUB_RUN_ID", f"local-{int(time.time())}")
    dataset_run_root = Path(args.dataset_root) / run_id
    results_run_root = Path(args.results_root) / run_id
    scratch_run_root = Path(args.scratch_root) / run_id
    for path in (dataset_run_root, results_run_root, scratch_run_root):
        shutil.rmtree(path, ignore_errors=True)
        path.mkdir(parents=True, exist_ok=True)
    benchmark = scratch_run_root / "benchmark.md"
    benchmark.write_text(
        "# Skills Eval Benchmark - NemoClaw sweep\n\n",
        encoding="utf-8",
    )
    verdict = scratch_run_root / "verdict.json"

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
            "PYTHONPATH": f"{SKILL_EVAL_ROOT}:{os.environ.get('PYTHONPATH', '')}",
        }
    )

    records: list[dict[str, Any]] = []
    try:
        _validate_instance(args.instance)
    except Exception as exc:  # noqa: BLE001 - publish one row verdict per target.
        for row in rows:
            records.append(
                _error_report(
                    row=row,
                    task="worker-validation",
                    reason=str(exc),
                    instance=args.instance,
                    benchmark=benchmark,
                )
            )
        _write_aggregate(
            rows=rows,
            records=records,
            run_id=run_id,
            benchmark=benchmark,
            verdict=verdict,
        )
        return 1

    for row_index, row in enumerate(rows, start=1):
        print(
            f"[nemoclaw-ci] row {row_index}/{len(rows)}: "
            f"{row.skill}/{row.spec}/{row.platform or 'n/a'}",
            flush=True,
        )
        try:
            task_dirs = _generate_dataset(row, dataset_run_root / row.slug)
            scenarios = [
                _wrap_task(task_dir, row, args.agent_timeout) for task_dir in task_dirs
            ]
        except Exception as exc:  # noqa: BLE001 - continue the remaining rows.
            records.append(
                _error_report(
                    row=row,
                    task="dataset-generation",
                    reason=str(exc),
                    instance=args.instance,
                    benchmark=benchmark,
                )
            )
            continue

        stop_reason = ""
        for scenario_index, scenario in enumerate(scenarios, start=1):
            if stop_reason:
                records.append(_skipped_record(scenario, stop_reason))
                continue
            print(
                f"[nemoclaw-ci] scenario {scenario_index}/{len(scenarios)}: "
                f"{row.skill}/{row.spec}/{scenario.task_dir.name}",
                flush=True,
            )
            output_root = results_run_root / row.slug / scenario.task_dir.name
            output_root.mkdir(parents=True, exist_ok=True)
            started = time.monotonic()
            harbor_rc = 1
            try:
                command = _harbor_command(scenario, output_root)
                harbor_rc = run_command(
                    command,
                    os.environ.copy(),
                    args.harbor_timeout,
                )
                record = _scenario_report(
                    scenario=scenario,
                    instance=args.instance,
                    run_id=run_id,
                    output_root=output_root,
                    harbor_rc=harbor_rc,
                    elapsed=time.monotonic() - started,
                    benchmark=benchmark,
                )
            except Exception as exc:  # noqa: BLE001 - preserve aggregate coverage.
                record = _error_report(
                    row=row,
                    task=scenario.task_dir.name,
                    reason=str(exc),
                    instance=args.instance,
                    benchmark=benchmark,
                )
            records.append(record)
            if _blocks_dependent_scenarios(record):
                stop_reason = (
                    f"dependent task skipped after {scenario.task_dir.name} "
                    f"reported {record['status']}"
                )

    _write_aggregate(
        rows=rows,
        records=records,
        run_id=run_id,
        benchmark=benchmark,
        verdict=verdict,
    )
    return 1 if any(record["status"] == "ERROR" for record in records) else 0


if __name__ == "__main__":
    sys.exit(main())
