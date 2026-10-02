#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Temporary read-only progress probe; emit allowlisted metadata, never raw logs."""
import argparse
from collections import Counter
import json
from pathlib import Path
import re
import shlex
import subprocess
import sys


def tool_kind(command):
    for marker, kind in (
        ("run_setup_notebook.py", "setup_notebook"),
        ("vss-build-vision-ai/SKILL.md", "build_skill_reference"),
        ("docker compose", "compose"),
        ("openshell", "openshell"),
        ("nemoclaw", "nemoclaw"),
        ("vss configure", "vss_configuration"),
        ("ngc ", "ngc"),
        ("uv ", "python_environment"),
        ("docker ", "docker"),
    ):
        if marker in command:
            return kind
    return "other"


def summarize_log(path):
    kinds, errors = Counter(), Counter()
    recent = []
    started, completed = 0, 0
    signals = {
        "scope upgrade pending approval": "gateway_scope_approval",
        "pairing required": "gateway_pairing",
        "origin not allowed": "gateway_origin",
        "SSRF preflight": "inference_preflight",
        "sandbox not found": "sandbox_missing",
        "apply_patch: command not found": "patch_command_missing",
        "Connection refused": "connection_refused",
        "timed out": "timeout",
    }
    with path.open() as stream:
        for line in stream:
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if not isinstance(event, dict):
                continue
            item = event.get("item")
            if not isinstance(item, dict) or item.get("type") != "command_execution":
                continue
            command = item.get("command")
            if not isinstance(command, str):
                continue
            kind = tool_kind(command)
            if event.get("type") == "item.started":
                started += 1
            if event.get("type") == "item.completed":
                completed += 1
                kinds[kind] += 1
                rc = item.get("exit_code")
                rc = rc if type(rc) is int and -255 <= rc <= 255 else None
                recent.append({"kind": kind, "exit_code": rc})
                if rc not in (0, None):
                    output = item.get("aggregated_output", "")
                    if isinstance(output, str):
                        for needle, signal in signals.items():
                            if needle in output:
                                errors[signal] += 1
    return {
        "bytes": path.stat().st_size,
        "modified_at": path.stat().st_mtime,
        "tools_started": started,
        "tools_completed": completed,
        "completed_tool_kinds": dict(kinds),
        "failed_tool_signals": dict(errors),
        "recent_completed_tools": recent[-12:],
    }


def worker(run_id):
    matches = []
    for process in Path("/proc").iterdir():
        if not process.name.isdigit():
            continue
        try:
            entries = (process / "environ").read_bytes().split(b"\0")
            if f"GITHUB_RUN_ID={run_id}".encode() not in entries:
                continue
            executable = (process / "exe").resolve().name
            if executable in {"node", "python3", "python3.13", "codex", "bash", "sh"}:
                matches.append(executable)
        except OSError:
            continue
    report = {"run_id": run_id, "matching_process_kinds": dict(Counter(matches))}
    if matches:
        path = Path("/logs/agent/codex.txt")
        if path.is_file():
            report["coding_trace_metadata"] = summarize_log(path)
        result = subprocess.run(
            ["docker", "ps", "--format", "{{json .}}"],
            capture_output=True, text=True, timeout=10,
        )
        states = Counter()
        for line in result.stdout.splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            name = row.get("Names", "")
            group = "vss" if name.startswith("vss-") else "nim" if name.startswith("skill-eval-nim-") else "other"
            state = "healthy" if "(healthy)" in row.get("Status", "") else "running"
            states[f"{group}_{state}"] += 1
        report["running_containers"] = dict(states)
    return report


def coordinator(run_id):
    trials = []
    for path in Path("/tmp/skill-eval/results").glob(f"*/{run_id}/*/step-*__*/result.json"):
        step = path.parent.name.split("__")[0]
        if not re.fullmatch(r"step-[0-9]+", step):
            continue
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        info = data.get("exception_info") or {}
        exception = info.get("exception_type") if isinstance(info, dict) else None
        trials.append({
            "step": step,
            "finished": bool(data.get("finished_at")),
            "exception": exception if isinstance(exception, str) and re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,100}", exception) else None,
        })
    command = shlex.join(["python3", "-", "--worker", run_id])
    result = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=2", "spark-ba-wifi", command],
        input=Path(__file__).read_text(), capture_output=True, text=True, timeout=45,
    )
    report = {"run_id": run_id, "completed_trial_metadata": trials, "worker_probe_exit_code": result.returncode}
    if result.returncode == 0:
        report["worker"] = json.loads(result.stdout)
    # Never print transport errors or raw remote output.
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("run_id")
    args = parser.parse_args()
    if not re.fullmatch(r"[0-9]{1,20}", args.run_id):
        parser.error("run_id must contain only digits")
    print(json.dumps(worker(args.run_id) if args.worker else coordinator(args.run_id), indent=2))
