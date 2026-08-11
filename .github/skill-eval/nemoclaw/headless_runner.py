#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Run one Harbor prompt synchronously inside NemoClaw/OpenClaw."""
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

_SESSION_PATH = re.compile(
    r"/sandbox/\.openclaw/agents/main/sessions/[A-Za-z0-9._-]+\.jsonl"
)


def _load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line.startswith("export ") or "=" not in line:
            continue
        key, value = line[7:].split("=", 1)
        parsed = shlex.split(value) if value else [""]
        os.environ.setdefault(key, parsed[0] if parsed else "")


def _gateway_name() -> str:
    raw = os.environ.get("NEMOCLAW_GATEWAY_PORT", "8080").strip()
    if not raw.isdigit() or not 1024 <= int(raw) <= 65535:
        raise ValueError("invalid NEMOCLAW_GATEWAY_PORT")
    return "nemoclaw" if int(raw) == 8080 else f"nemoclaw-{int(raw)}"


def _sandbox_exec(
    sandbox: str,
    script: str,
    *,
    timeout: int,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "openshell",
            "sandbox",
            "exec",
            "--name",
            sandbox,
            "-g",
            _gateway_name(),
            "--",
            "sh",
            "-lc",
            script,
        ],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def _gateway_healthy(sandbox: str) -> bool:
    result = _sandbox_exec(
        sandbox,
        (
            "code=$(curl --noproxy '*' -sS --connect-timeout 3 --max-time 10 "
            "-o /dev/null -w '%{http_code}' http://127.0.0.1:18789/health) "
            "&& { [ \"$code\" = 200 ] || [ \"$code\" = 401 ]; }"
        ),
        timeout=30,
    )
    return result.returncode == 0


def _ensure_gateway(sandbox: str) -> None:
    if _gateway_healthy(sandbox):
        return
    recovery = subprocess.run(
        ["nemoclaw", "sandbox", "recover", sandbox],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=360,
        check=False,
    )
    if recovery.returncode != 0 or not _gateway_healthy(sandbox):
        raise RuntimeError("OpenClaw gateway is not healthy after managed recovery")


def _json_object(raw: str) -> dict[str, Any]:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        value = None
        for line in reversed(raw.splitlines()):
            try:
                candidate = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(candidate, dict):
                value = candidate
                break
    if not isinstance(value, dict):
        raise TypeError("OpenClaw CLI did not return a JSON object")
    return value


def _int(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    return "".join(
        str(part.get("text") or "")
        for part in content
        if isinstance(part, dict) and part.get("type") == "text"
    )


def _tool_calls(content: Any) -> list[dict[str, Any]]:
    if not isinstance(content, list):
        return []
    calls: list[dict[str, Any]] = []
    for index, part in enumerate(content, 1):
        if not isinstance(part, dict) or part.get("type") != "toolCall":
            continue
        name = str(part.get("name") or "")
        raw_arguments = part.get("arguments")
        if isinstance(raw_arguments, str):
            try:
                arguments = json.loads(raw_arguments) if raw_arguments else {}
            except json.JSONDecodeError:
                arguments = {"raw": raw_arguments}
        elif isinstance(raw_arguments, dict):
            arguments = dict(raw_arguments)
        else:
            arguments = {}
        # Generic Harbor judges use Claude's canonical Bash tool name and
        # command field. OpenClaw emits the equivalent operation as exec.
        function_name = "Bash" if name == "exec" else name
        if (
            name == "exec"
            and "command" not in arguments
            and isinstance(arguments.get("cmd"), str)
        ):
            arguments["command"] = arguments.pop("cmd")
        calls.append(
            {
                "tool_call_id": str(
                    part.get("id") or f"openclaw-tool-{index:06d}"
                ),
                "function_name": function_name,
                "arguments": arguments,
            }
        )
    return calls


def _session_to_atif(
    session_jsonl: str,
    envelope: dict[str, Any],
    prompt: str,
) -> tuple[dict[str, Any], dict[str, int]]:
    rows: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for raw in session_jsonl.splitlines():
        try:
            record = json.loads(raw)
        except json.JSONDecodeError:
            continue
        message = record.get("message") if isinstance(record, dict) else None
        if (
            isinstance(record, dict)
            and record.get("type") == "message"
            and isinstance(message, dict)
        ):
            rows.append((record, message))

    steps: list[dict[str, Any]] = []
    prompt_tokens = cached_tokens = completion_tokens = 0
    first_user = True
    index = 0
    while index < len(rows):
        record, message = rows[index]
        role = message.get("role")
        timestamp = record.get("timestamp")
        if role == "user":
            step: dict[str, Any] = {
                "step_id": len(steps) + 1,
                "source": "user",
                "message": prompt if first_user else (_text(message.get("content")) or "(empty)"),
            }
            first_user = False
            if isinstance(timestamp, str):
                step["timestamp"] = timestamp
            steps.append(step)
            index += 1
            continue
        if role != "assistant":
            index += 1
            continue

        calls = _tool_calls(message.get("content"))
        observations: list[dict[str, Any]] = []
        next_index = index + 1
        call_ids = {call["tool_call_id"] for call in calls}
        while next_index < len(rows) and rows[next_index][1].get("role") == "toolResult":
            result_message = rows[next_index][1]
            source_id = str(result_message.get("toolCallId") or "")
            if call_ids and source_id not in call_ids:
                break
            details = result_message.get("details")
            body = (
                str(details.get("aggregated") or "")
                if isinstance(details, dict)
                else ""
            ) or _text(result_message.get("content"))
            observations.append(
                {"source_call_id": source_id or None, "content": body or None}
            )
            next_index += 1

        usage = message.get("usage")
        step_metrics: dict[str, int] = {}
        if isinstance(usage, dict):
            cache_read = _int(usage.get("cacheRead"))
            prompt_count = _int(usage.get("input")) + cache_read
            output_count = _int(usage.get("output"))
            prompt_tokens += prompt_count
            cached_tokens += cache_read
            completion_tokens += output_count
            step_metrics = {
                "prompt_tokens": prompt_count,
                "cached_tokens": cache_read,
                "completion_tokens": output_count,
            }
        step = {
            "step_id": len(steps) + 1,
            "source": "agent",
            "message": _text(message.get("content")) or "(no assistant text)",
        }
        if isinstance(timestamp, str):
            step["timestamp"] = timestamp
        if calls:
            step["tool_calls"] = calls
        if observations:
            step["observation"] = {"results": observations}
        if step_metrics:
            step["metrics"] = step_metrics
        steps.append(step)
        index = next_index

    meta = envelope.get("meta")
    agent_meta = meta.get("agentMeta") if isinstance(meta, dict) else {}
    final_usage = agent_meta.get("usage") if isinstance(agent_meta, dict) else {}
    if isinstance(final_usage, dict):
        final_cache = _int(final_usage.get("cacheRead"))
        prompt_tokens = max(
            prompt_tokens, _int(final_usage.get("input")) + final_cache
        )
        cached_tokens = max(cached_tokens, final_cache)
        completion_tokens = max(
            completion_tokens, _int(final_usage.get("output"))
        )
    turns = sum(step.get("source") == "agent" for step in steps)
    metrics = {
        "turns": turns,
        # Existing skill-eval reports split uncached prompt from cached input.
        "prompt_tokens": max(0, prompt_tokens - cached_tokens),
        "cached_tokens": cached_tokens,
        "completion_tokens": completion_tokens,
    }
    trajectory = {
        "schema_version": "ATIF-v1.7",
        "session_id": str(
            agent_meta.get("sessionId")
            if isinstance(agent_meta, dict)
            else ""
        ),
        "agent": {
            "name": "openclaw",
            "version": os.environ.get("NEMOCLAW_INSTALL_REF", "unknown"),
            "model_name": str(
                agent_meta.get("model", "unknown")
                if isinstance(agent_meta, dict)
                else "unknown"
            ),
        },
        "steps": steps,
        "final_metrics": {
            "total_prompt_tokens": prompt_tokens,
            "total_completion_tokens": completion_tokens,
            "total_cached_tokens": cached_tokens,
            "total_steps": len(steps),
        },
    }
    return trajectory, metrics


def _session_file(envelope: dict[str, Any]) -> str:
    meta = envelope.get("meta")
    agent_meta = meta.get("agentMeta") if isinstance(meta, dict) else None
    value = agent_meta.get("sessionFile") if isinstance(agent_meta, dict) else None
    if not isinstance(value, str) or _SESSION_PATH.fullmatch(value) is None:
        raise RuntimeError("OpenClaw result did not provide a trusted session file")
    return value


def _run_openclaw(
    sandbox: str,
    prompt: str,
    timeout: int,
) -> tuple[dict[str, Any], dict[str, int], dict[str, Any]]:
    session_id = (
        f"{os.environ.get('GITHUB_RUN_ID', 'local')}-"
        f"{uuid.uuid4().hex}"
    )
    no_proxy = "localhost,127.0.0.1,::1,10.200.0.1"
    command = (
        "unset BREV_INSTANCE NEMOCLAW_BREV_INSTANCE; "
        f"export NO_PROXY={shlex.quote(no_proxy)}; "
        f"export no_proxy={shlex.quote(no_proxy)}; "
        "export NODE_EXTRA_CA_CERTS=/etc/openshell-tls/ca-bundle.pem; "
        "export OPENCLAW_DISABLE_STREAMING_TOOL_CALLS=1; "
        "openclaw agent --agent main --thinking off --json "
        f"--timeout {int(timeout)} "
        f"--session-id {shlex.quote(session_id)} "
        f"--message {shlex.quote(prompt)}"
    )
    result = _sandbox_exec(sandbox, command, timeout=timeout + 120)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "")[-1000:]
        raise RuntimeError(
            f"OpenClaw agent exited {result.returncode}: {detail}"
        )
    envelope = _json_object(result.stdout)
    session_file = _session_file(envelope)
    session = _sandbox_exec(
        sandbox,
        f"cat -- {shlex.quote(session_file)}",
        timeout=60,
    )
    if session.returncode != 0:
        raise RuntimeError("OpenClaw session metrics could not be collected")
    trajectory, metrics = _session_to_atif(session.stdout, envelope, prompt)
    if metrics["turns"] < 1:
        raise RuntimeError("OpenClaw session contained no assistant turns")
    if metrics["prompt_tokens"] + metrics["cached_tokens"] < 1:
        raise RuntimeError("OpenClaw session contained no native token usage")
    return envelope, metrics, trajectory


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prompt-file", required=True)
    parser.add_argument(
        "--env-file",
        default="/tmp/skill-eval/nemoclaw/nemoclaw.env",
    )
    parser.add_argument("--log-dir", default="/logs/artifacts/nemoclaw")
    parser.add_argument("--agent-log-dir", default="/logs/agent")
    parser.add_argument(
        "--timeout",
        type=int,
        default=int(os.environ.get("NEMOCLAW_AGENT_TIMEOUT_SEC", "3300")),
    )
    args = parser.parse_args(argv)

    _load_env_file(Path(args.env_file))
    log_dir = Path(args.log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    agent_log_dir = Path(args.agent_log_dir)
    agent_log_dir.mkdir(parents=True, exist_ok=True)
    sandbox = os.environ.get(
        "NEMOCLAW_SANDBOX_NAME", "skill-eval-nemoclaw"
    )
    prompt = Path(args.prompt_file).read_text(encoding="utf-8")
    started = time.monotonic()

    try:
        _ensure_gateway(sandbox)
        envelope, metrics, trajectory = _run_openclaw(
            sandbox, prompt, args.timeout
        )
        meta = envelope.get("meta")
        agent_meta = meta.get("agentMeta") if isinstance(meta, dict) else {}
        report = {
            "ok": True,
            "sandbox": sandbox,
            "elapsed_s": round(time.monotonic() - started, 3),
            "session_id": (
                str(agent_meta.get("sessionId"))
                if isinstance(agent_meta, dict)
                and agent_meta.get("sessionId")
                else ""
            ),
        }
        (log_dir / "metrics.json").write_text(
            json.dumps(metrics, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        (agent_log_dir / "trajectory.json").write_text(
            json.dumps(trajectory, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        (log_dir / "run.json").write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        (log_dir / "agent.log").write_text(
            "NemoClaw/OpenClaw headless run completed\n"
            f"sandbox={sandbox}\n"
            f"elapsed_s={report['elapsed_s']}\n",
            encoding="utf-8",
        )
        print(json.dumps({**report, "metrics": metrics}, indent=2))
        return 0
    except Exception as exc:  # noqa: BLE001
        # The exception is printed for GitHub's normal secret masking. No
        # failure transcript or environment snapshot is written to artifacts.
        print(
            f"NemoClaw/OpenClaw headless run failed: "
            f"{type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
