#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Local vision-pipeline trials using Claude Code and the existing VSS judge.

Separate working directories, not an OS sandbox. Never invokes Brev, resets
Docker, or publishes results. --prepare-only requires no model credentials.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile

HARNESS = Path(__file__).resolve().parents[1]
REPO = HARNESS.parents[1]
SKILL = REPO / "skills/vss-build-vision-pipeline"
sys.path.insert(0, str(HARNESS))
from redact_secrets import redact_obj, redact_text



def configure_credentials(env):
    """Map an inherited NVIDIA Hub key for both agent and judge, in memory only."""
    if not env.get("ANTHROPIC_API_KEY") and env.get("NVIDIA_API_KEY"):
        env["ANTHROPIC_API_KEY"] = env["NVIDIA_API_KEY"]
        env.setdefault("ANTHROPIC_BASE_URL", "https://inference-api.nvidia.com")
        env.setdefault("ANTHROPIC_MODEL", "aws/anthropic/bedrock-claude-sonnet-4-6")
    if "inference-api.nvidia.com" in env.get("ANTHROPIC_BASE_URL", ""):
        env.setdefault("CLAUDE_CODE_DISABLE_THINKING", "1")


def save(path, value):
    path.write_text(json.dumps(redact_obj(value), indent=2) + "\n")


def substitute(value, workspace, hardware):
    if isinstance(value, str):
        return (value.replace("{{repo_root}}/_builds/", str(workspace / "_builds") + "/")
                .replace("{{repo_root}}", str(REPO)).replace("{{platform}}", hardware))
    if isinstance(value, list):
        return [substitute(v, workspace, hardware) for v in value]
    if isinstance(value, dict):
        return {k: substitute(v, workspace, hardware) for k, v in value.items()}
    return value


def prepare(spec_path, run_dir, hardware):
    original = json.loads(spec_path.read_text())
    if original.get("skills") != [SKILL.name] or len(original.get("expects", [])) != 1:
        raise ValueError("Local runner expects this skill's single-step scenarios")
    workspace = run_dir / "workspace"
    workspace.mkdir(parents=True)
    shutil.copytree(SKILL, workspace / ".claude/skills" / SKILL.name)
    spec = substitute(original, workspace, hardware)
    # Preserve the CI contract verbatim; local hardware is an explicit override
    # recorded alongside it, not a claim to satisfy the L40S platform declaration.
    save(run_dir / "original-spec.json", original)
    save(run_dir / "spec.json", spec)
    save(run_dir / "manifest.json", {
        "mode": "local-host", "ci_platforms": original["resources"]["platforms"],
        "local_hardware_override": hardware, "spec": str(spec_path),
        "spec_sha256": hashlib.sha256(spec_path.read_bytes()).hexdigest(),
        "skill_sha256": hashlib.sha256((SKILL / "SKILL.md").read_bytes()).hexdigest(),
        "workspace": str(workspace), "repository": str(REPO),
        "status": "prepared", "ci_equivalent": False,
    })
    # Runtime boundaries are workspace configuration, not part of the test query.
    (workspace / "CLAUDE.md").write_text(
        "This is a shared host. Work within this trial workspace. "
        "Preserve the source checkout, host configuration and unrelated Docker resources. "
        "Do not run global Docker cleanup. Label trial containers vss.local-eval="
        + run_dir.name + ". Clean up only trial-owned resources by exact ID.\n"
    )
    prompt = spec["expects"][0]["query"]
    (run_dir / "instruction.md").write_text(prompt)
    return spec, workspace, prompt


def normalize_trace(events):
    steps = []
    for event in events:
        if event.get("type") not in ("assistant", "user"):
            continue
        message = event.get("message", {})
        content = message.get("content", [])
        if not isinstance(content, list):
            continue
        calls = []
        for item in content:
            if item.get("type") == "tool_use":
                calls.append({"tool_call_id": item["id"], "function_name": item["name"],
                              "arguments": item.get("input", {})})
        steps.append({"step_id": len(steps) + 1,
                      "source": "agent" if event["type"] == "assistant" else "user",
                      "message": "\n".join(c.get("text", "") for c in content if c.get("type") == "text"),
                      "tool_calls": calls, "observation": content})
    return {"schema_version": "local-vss-1", "steps": steps}


def execute_agent(prompt, workspace, run_dir, timeout):
    command = ["claude", "-p", "--verbose", "--output-format", "stream-json",
               "--permission-mode", "acceptEdits",
               "--allowedTools", "Bash,Read,Write,Edit,Glob,Grep,WebFetch,WebSearch,Skill",
               "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
               "--settings", '{"disableAllHooks":true}',
               "--model", os.environ["ANTHROPIC_MODEL"]]
    # Do not persist unsanitized raw streams on disk or include secrets in argv.
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True, cwd=workspace,
                               start_new_session=True)
    timed_out = False
    try:
        stdout, stderr = process.communicate(prompt, timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        os.killpg(process.pid, signal.SIGTERM)
        try:
            stdout, stderr = process.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            stdout, stderr = process.communicate()
    (run_dir / "agent.jsonl").write_text(redact_text(stdout))
    (run_dir / "agent-stderr.txt").write_text(redact_text(stderr))
    events = []
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            events.append(event)
    save(run_dir / "trajectory.json", normalize_trace(events))
    result = next((e for e in reversed(events) if e.get("type") == "result"), None)
    success = (not timed_out and process.returncode == 0 and result is not None
               and not result.get("is_error") and result.get("subtype") == "success")
    return success


def judge(spec, run_dir, timeout):
    module_spec = importlib.util.spec_from_file_location("local_generic_judge", HARNESS / "verifiers/generic_judge.py")
    module = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(module)
    # Judge from the trial workspace and direct it to actual local artifacts.
    module._JUDGE_SYSTEM_PROMPT += (
        f"\nLocal evaluation: artifacts are in {run_dir}; use the per-check trajectory path. "
        "There is no Harbor /logs or /tests directory in this run. "
        "Only inspect this trial's artifacts and resources; do not modify the host. "
        "The manifest records a local GPU override; do not claim CI hardware parity."
    )
    previous = Path.cwd()
    try:
        os.chdir(run_dir / "workspace")
        results = module._run_checks(spec["expects"][0]["checks"], str(run_dir / "trajectory.json"), timeout)
    finally:
        os.chdir(previous)
    passed = sum(r.get("pass") is True for r in results)
    reward = passed / len(results)
    save(run_dir / "judge.json", {"checks": results, "passed": passed,
                                  "total": len(results), "reward": reward})
    (run_dir / "reward.txt").write_text(str(reward) + "\n")
    return passed == len(results)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", choices=[p.stem for p in sorted((SKILL / "evals").glob("*.json"))], required=True)
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--results-root", type=Path, default=Path('/tmp/vss-local-eval/results'))
    parser.add_argument("--agent-timeout", type=int, default=3600)
    parser.add_argument("--judge-timeout", type=int, default=600)
    args = parser.parse_args()
    os.umask(0o077)
    configure_credentials(os.environ)
    for executable in ("docker", "nvidia-smi", "claude"):
        if not shutil.which(executable):
            parser.error(f"Missing executable: {executable}")
    if not args.prepare_only:
        missing = [n for n in ("ANTHROPIC_API_KEY", "ANTHROPIC_MODEL") if not os.environ.get(n)]
        if missing:
            parser.error("Set " + ", ".join(missing) + " in the invoking environment; do not place secrets in arguments")
    hardware = subprocess.check_output(
        ['nvidia-smi', '--query-gpu=name,driver_version,memory.total', '--format=csv,noheader'], text=True).strip()
    subprocess.run(['docker', 'info', '--format', '{{.ServerVersion}}'], check=True, capture_output=True)
    args.results_root.mkdir(parents=True, exist_ok=True)
    # Only serializes runs from this launcher; no claim to lock other GPU users.
    with (Path(tempfile.gettempdir()) / 'vss-local-eval.lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        run_dir = Path(tempfile.mkdtemp(prefix=args.spec + '-', dir=args.results_root)).resolve()
        spec, workspace, prompt = prepare(SKILL / 'evals' / (args.spec + '.json'), run_dir, hardware)
        print(f"Artifacts: {run_dir}", flush=True)
        if args.prepare_only:
            return 0
        if not execute_agent(prompt, workspace, run_dir, args.agent_timeout):
            save(run_dir / 'status.json', {'state': 'agent-error', 'judge_run': False})
            return 2
        passed = judge(spec, run_dir, args.judge_timeout)
        save(run_dir / 'status.json', {'state': 'passed' if passed else 'failed', 'judge_run': True})
        return 0 if passed else 1


if __name__ == '__main__':
    raise SystemExit(main())
