#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Launch a NemoClaw/OpenClaw scenario from a Harbor trial.

Harbor remains the result owner. The Harbor agent only invokes this script;
this script sends the real prompt to the OpenClaw hooks endpoint so the VSS
skills run inside NemoClaw/OpenClaw with the VSS Orchestrator MCP available.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import shlex
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path, PurePosixPath
from typing import Any

OPENCLAW_RUN_DIR = "/tmp/vss-skill-eval-openclaw"
OPENCLAW_STATE_PREFIX = "NEMOCLAW_OPENCLAW_STATE:"
OPENCLAW_RC_PREFIX = "NEMOCLAW_OPENCLAW_RC:"
OPENCLAW_LOG_BEGIN = "NEMOCLAW_OPENCLAW_LOG_BEGIN"
OPENCLAW_SESSION_DIR = PurePosixPath(
    "/sandbox/.openclaw/agents/main/sessions"
)
OPENCLAW_SESSION_MAX_BYTES = 50 * 1024 * 1024
RUNTIME_REDACTION_KEYS = (
    "RTSP_SAMPLE_URL",
    "NGC_CLI_API_KEY",
    "NGC_API_KEY",
    "NVIDIA_API_KEY",
    "HF_TOKEN",
    "ANTHROPIC_API_KEY",
    "COMPATIBLE_API_KEY",
    "OPENAI_API_KEY",
)


def _load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or not line.startswith("export ") or "=" not in line:
            continue
        key, value = line[len("export ") :].split("=", 1)
        os.environ.setdefault(key, shlex.split(value)[0] if value else "")


def _redact_runtime_text(raw: str) -> str:
    """Redact runtime credentials from logs and trajectory evidence."""
    replacements: list[tuple[str, str]] = []
    for key in RUNTIME_REDACTION_KEYS:
        value = os.environ.get(key, "")
        if not value:
            continue
        if key != "RTSP_SAMPLE_URL" and len(value) < 8:
            continue
        placeholder = (
            "<redacted:RTSP_SAMPLE_URL;match=exact-runtime-value>"
            if key == "RTSP_SAMPLE_URL"
            else f"<redacted:{key}>"
        )
        replacements.append((value, placeholder))
        escaped = json.dumps(value, ensure_ascii=False)[1:-1]
        if escaped != value:
            replacements.append((escaped, placeholder))
    redacted = raw
    for value, placeholder in sorted(
        set(replacements),
        key=lambda item: len(item[0]),
        reverse=True,
    ):
        redacted = redacted.replace(value, placeholder)
    return redacted


def _read_hooks_token() -> str:
    token = os.environ.get("OPENCLAW_HOOKS_TOKEN", "")
    if token:
        return token

    token_file = os.environ.get("NEMOCLAW_HOOKS_TOKEN_FILE", "")
    if not token_file:
        return ""
    try:
        return Path(token_file).read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _run(cmd: list[str], *, timeout: int = 30) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def _deadline_timeout(deadline: float | None, cap_s: int, phase: str) -> int:
    """Return a bounded subprocess timeout without extending an outer deadline."""
    if deadline is None:
        return cap_s
    remaining_s = int(deadline - time.monotonic())
    if remaining_s <= 0:
        raise TimeoutError(f"NemoClaw agent deadline exceeded during {phase}")
    return min(cap_s, remaining_s)


def _sleep_before_deadline(deadline: float | None, seconds: int) -> None:
    """Sleep for at most the time left in an outer deadline."""
    if deadline is None:
        time.sleep(seconds)
        return
    remaining_s = deadline - time.monotonic()
    if remaining_s <= 0:
        raise TimeoutError("NemoClaw agent deadline exceeded while waiting")
    time.sleep(min(seconds, remaining_s))


def _sandbox_exec(sandbox_name: str, script: str, *, timeout: int) -> subprocess.CompletedProcess[str]:
    encoded_script = base64.b64encode(script.encode("utf-8")).decode("ascii")
    wrapper = f"printf %s {shlex.quote(encoded_script)} | base64 -d | sh"
    if shutil_which("openshell"):
        return _run(
            ["openshell", "sandbox", "exec", "-n", sandbox_name, "--", "sh", "-lc", wrapper],
            timeout=timeout,
        )
    return _run(
        ["nemoclaw", sandbox_name, "exec", "--no-tty", "--", "sh", "-lc", wrapper],
        timeout=timeout,
    )


def _forward_running(port: str, sandbox_name: str) -> bool:
    result = _run(["openshell", "forward", "list"], timeout=30)
    combined = f"{result.stdout}\n{result.stderr}"
    for raw in combined.splitlines():
        parts = raw.split()
        if len(parts) >= 5 and parts[0] == sandbox_name and parts[2] == port and parts[-1].lower() == "running":
            return True
    result = _run(["ps", "-eo", "args="], timeout=10)
    if result.returncode != 0:
        return False
    needles = (
        f"openshell forward start {port} {sandbox_name}",
        f"openshell forward start --background {port} {sandbox_name}",
    )
    return any(any(needle in line for needle in needles) for line in result.stdout.splitlines())


def _dashboard_healthy(port: str) -> bool:
    result = _run(["curl", "-fsS", f"http://127.0.0.1:{port}/health"], timeout=10)
    return result.returncode == 0


def ensure_forward(port: str, sandbox_name: str) -> None:
    if _dashboard_healthy(port):
        return
    _run(["openshell", "forward", "stop", port, sandbox_name], timeout=30)
    if shutil_which("setsid"):
        subprocess.run(
            ["setsid", "-f", "openshell", "forward", "start", port, sandbox_name],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    else:
        _run(["openshell", "forward", "start", "--background", port, sandbox_name], timeout=60)
    for _ in range(30):
        if _dashboard_healthy(port):
            return
        time.sleep(2)
    raise RuntimeError(f"OpenClaw forward {port} for sandbox {sandbox_name} is not healthy")


def shutil_which(name: str) -> str | None:
    from shutil import which

    return which(name)


def post_hook(url: str, token: str, payload: dict[str, Any], timeout: int) -> dict[str, Any]:
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8", errors="replace")
            try:
                parsed: Any = json.loads(body) if body else {}
            except json.JSONDecodeError:
                parsed = body
            return {"status": response.status, "body": parsed}
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        return {"status": exc.code, "body": body, "error": str(exc)}
    except urllib.error.URLError as exc:
        return {"status": 0, "body": "", "error": str(exc)}


def _gateway_reachable(sandbox_name: str, deadline: float | None = None) -> bool:
    try:
        result = _sandbox_exec(
            sandbox_name,
            "curl -fsS http://127.0.0.1:18789/health >/dev/null",
            timeout=_deadline_timeout(deadline, 20, "OpenClaw gateway probe"),
        )
    except subprocess.TimeoutExpired:
        return False
    return result.returncode == 0


def ensure_openclaw_gateway(
    sandbox_name: str,
    log_dir: Path,
    deadline: float | None = None,
) -> None:
    attempts: list[str] = []
    try:
        if _gateway_reachable(sandbox_name, deadline):
            return

        try:
            restart = _run(
                ["nemoclaw", "sandbox", "gateway", "restart", sandbox_name],
                timeout=_deadline_timeout(
                    deadline,
                    120,
                    "managed OpenClaw gateway restart",
                ),
            )
        except subprocess.TimeoutExpired as exc:
            restart = None
            attempts.append(f"managed restart timed out: {exc}")
        else:
            attempts.append(
                "managed restart\n"
                f"returncode={restart.returncode}\n"
                f"stdout:\n{restart.stdout or ''}\n"
                f"stderr:\n{restart.stderr or ''}"
            )

        if restart is not None and restart.returncode == 0:
            for _ in range(10):
                if _gateway_reachable(sandbox_name, deadline):
                    (log_dir / "openclaw_gateway_recover.log").write_text(
                        "\n\n".join(attempts) + "\n",
                        encoding="utf-8",
                    )
                    return
                _sleep_before_deadline(deadline, 3)

        try:
            recover = _run(
                ["nemoclaw", "sandbox", "recover", sandbox_name],
                timeout=_deadline_timeout(
                    deadline,
                    300,
                    "NemoClaw sandbox recovery",
                ),
            )
        except subprocess.TimeoutExpired as exc:
            attempts.append(f"sandbox recover timed out: {exc}")
        else:
            attempts.append(
                "sandbox recover\n"
                f"returncode={recover.returncode}\n"
                f"stdout:\n{recover.stdout or ''}\n"
                f"stderr:\n{recover.stderr or ''}"
            )

        for _ in range(20):
            if _gateway_reachable(sandbox_name, deadline):
                (log_dir / "openclaw_gateway_recover.log").write_text(
                    "\n\n".join(attempts) + "\n",
                    encoding="utf-8",
                )
                return
            _sleep_before_deadline(deadline, 3)
    except TimeoutError as exc:
        attempts.append(str(exc))
        (log_dir / "openclaw_gateway_recover.log").write_text(
            "\n\n".join(attempts) + "\n",
            encoding="utf-8",
        )
        raise

    (log_dir / "openclaw_gateway_recover.log").write_text(
        "\n\n".join(attempts) + "\n",
        encoding="utf-8",
    )
    raise RuntimeError(f"OpenClaw gateway in sandbox {sandbox_name} is not reachable")


def _openclaw_cli_command(prompt: str, timeout_s: int) -> str:
    run_id = os.environ.get("GITHUB_RUN_ID", "ci").strip() or "ci"
    session_id = f"{run_id}-{uuid.uuid4().hex}"
    no_proxy = "localhost,127.0.0.1,::1,10.200.0.1"
    ca_path = "/etc/openshell-tls/ca-bundle.pem"
    return (
        "unset BREV_INSTANCE NEMOCLAW_BREV_INSTANCE; "
        f"export NO_PROXY={shlex.quote(no_proxy)}; "
        f"export no_proxy={shlex.quote(no_proxy)}; "
        f"export NODE_EXTRA_CA_CERTS={shlex.quote(ca_path)}; "
        "export OPENCLAW_DISABLE_STREAMING_TOOL_CALLS=1; "
        "openclaw agent --agent main --thinking off "
        "--json "
        f"--timeout {int(timeout_s)} "
        f"--session-id {shlex.quote(session_id)} "
        f"--message {shlex.quote(prompt)}"
    )


def collect_openclaw_cli_log(
    sandbox_name: str,
    log_dir: Path,
    deadline: float | None = None,
) -> None:
    result = _sandbox_exec(
        sandbox_name,
        f"cat {OPENCLAW_RUN_DIR}/openclaw-agent.log",
        timeout=_deadline_timeout(deadline, 30, "OpenClaw log collection"),
    )
    if result.returncode != 0:
        detail = (
            result.stderr
            or result.stdout
            or f"exit {result.returncode}"
        )[-500:]
        raise RuntimeError(
            "Could not collect the current OpenClaw log: "
            f"{_redact_runtime_text(detail)}"
        )
    (log_dir / "openclaw-agent.log").write_text(
        _redact_runtime_text(result.stdout or ""),
        encoding="utf-8",
    )


def _agent_json_documents(raw: str) -> list[Any]:
    """Decode OpenClaw's plain JSON or log-prefixed JSON output."""
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, list) else [parsed]
    except json.JSONDecodeError:
        pass

    decoder = json.JSONDecoder()
    documents: list[Any] = []
    index = 0
    while index < len(raw):
        if raw[index] not in "[{":
            index += 1
            continue
        try:
            parsed, end = decoder.raw_decode(raw, index)
        except json.JSONDecodeError:
            index += 1
            continue
        documents.extend(parsed if isinstance(parsed, list) else [parsed])
        index = end
    return documents


def _openclaw_result_envelope(raw: str) -> dict[str, Any] | None:
    """Return the final OpenClaw result envelope from warning-prefixed output."""
    documents = _agent_json_documents(raw)
    if not documents or not isinstance(documents[-1], dict):
        return None
    document = documents[-1]
    nested = document.get("result")
    return nested if isinstance(nested, dict) else document


def _openclaw_session_file(raw: str) -> tuple[dict[str, Any], str]:
    """Return a trusted current-session path from the final CLI envelope."""
    envelope = _openclaw_result_envelope(raw)
    if envelope is None:
        raise RuntimeError("OpenClaw output did not end with a JSON result envelope")
    meta = envelope.get("meta")
    agent_meta = meta.get("agentMeta") if isinstance(meta, dict) else None
    session_file = (
        agent_meta.get("sessionFile")
        if isinstance(agent_meta, dict)
        else None
    )
    if not isinstance(session_file, str) or not session_file.strip():
        raise RuntimeError(
            "OpenClaw result envelope did not provide meta.agentMeta.sessionFile"
        )
    if any(char in session_file for char in ("\0", "\r", "\n")):
        raise RuntimeError("OpenClaw session path contains control characters")

    candidate = PurePosixPath(session_file)
    if (
        not candidate.is_absolute()
        or candidate.parent != OPENCLAW_SESSION_DIR
        or candidate.suffix != ".jsonl"
        or candidate.name in {"", ".jsonl"}
    ):
        raise RuntimeError(
            "OpenClaw session path is outside the managed main-agent session directory"
        )
    return envelope, str(candidate)


def _json_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _openclaw_session_jsonl_to_atif(
    session_jsonl: str,
    *,
    instruction: str,
    envelope: dict[str, Any],
) -> dict[str, Any] | None:
    """Map an OpenClaw session transcript to Harbor's normalized ATIF shape."""

    def text_from_content(content: Any) -> str:
        if isinstance(content, str):
            return content
        if not isinstance(content, list):
            return ""
        return "".join(
            part["text"]
            for part in content
            if isinstance(part, dict)
            and part.get("type") == "text"
            and isinstance(part.get("text"), str)
        )

    tool_sequence = 0

    def assistant_parts(
        content: Any,
    ) -> tuple[str, list[dict[str, Any]]]:
        nonlocal tool_sequence
        if not isinstance(content, list):
            return "", []
        text_parts: list[str] = []
        tool_calls: list[dict[str, Any]] = []
        for part in content:
            if not isinstance(part, dict):
                continue
            if (
                part.get("type") == "text"
                and isinstance(part.get("text"), str)
            ):
                text_parts.append(part["text"])
                continue
            if (
                part.get("type") != "toolCall"
                or not isinstance(part.get("name"), str)
            ):
                continue
            raw_arguments = part.get("arguments", "")
            if isinstance(raw_arguments, str):
                try:
                    arguments = (
                        json.loads(raw_arguments)
                        if raw_arguments.strip()
                        else {}
                    )
                except json.JSONDecodeError:
                    arguments = {"raw": raw_arguments}
                if not isinstance(arguments, dict):
                    arguments = {"value": arguments}
            elif isinstance(raw_arguments, dict):
                arguments = raw_arguments
            else:
                arguments = {}
            tool_sequence += 1
            raw_id = part.get("id")
            tool_call_id = (
                str(raw_id)
                if raw_id is not None and str(raw_id)
                else f"openclaw-tool-{tool_sequence:06d}"
            )
            tool_calls.append(
                {
                    "tool_call_id": tool_call_id,
                    "function_name": part["name"],
                    "arguments": arguments,
                }
            )
        return "".join(text_parts), tool_calls

    def usage_metrics(usage: Any) -> dict[str, Any] | None:
        if not isinstance(usage, dict):
            return None
        input_tokens = _json_int(usage.get("input"))
        output_tokens = _json_int(usage.get("output"))
        cache_read = _json_int(usage.get("cacheRead"))
        cache_write = _json_int(usage.get("cacheWrite"))
        if not (input_tokens or output_tokens or cache_read):
            return None
        metrics: dict[str, Any] = {
            "prompt_tokens": input_tokens + cache_read or None,
            "completion_tokens": output_tokens or None,
            "cached_tokens": cache_read or None,
        }
        if cache_write:
            metrics["extra"] = {"cache_write_tokens": cache_write}
        return metrics

    rows: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for raw_line in session_jsonl.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(record, dict) or record.get("type") != "message":
            continue
        message = record.get("message")
        if not isinstance(message, dict):
            continue
        if message.get("role") in {"user", "assistant", "toolResult"}:
            rows.append((record, message))
    if not rows:
        return None

    meta = envelope.get("meta")
    agent_meta = meta.get("agentMeta") if isinstance(meta, dict) else None
    model_name = (
        str(agent_meta.get("model"))
        if isinstance(agent_meta, dict) and agent_meta.get("model")
        else "unknown"
    )
    steps: list[dict[str, Any]] = []
    first_user = True
    row_index = 0
    while row_index < len(rows):
        record, message = rows[row_index]
        timestamp = (
            record.get("timestamp")
            if isinstance(record.get("timestamp"), str)
            else None
        )
        role = message.get("role")
        if role == "user":
            body = text_from_content(message.get("content"))
            user_message = (
                instruction.strip()
                if first_user and instruction.strip()
                else body
            )
            first_user = False
            step: dict[str, Any] = {
                "step_id": len(steps) + 1,
                "source": "user",
                "message": user_message or "(empty user message)",
            }
            if timestamp:
                step["timestamp"] = timestamp
            steps.append(step)
            row_index += 1
            continue

        if role == "assistant":
            assistant_text, tool_calls = assistant_parts(message.get("content"))
            error_message = message.get("errorMessage")
            if assistant_text.strip():
                agent_message = assistant_text.strip()
            elif isinstance(error_message, str) and error_message.strip():
                agent_message = f"(error) {error_message.strip()}"
            else:
                agent_message = "(no assistant text)"

            next_index = row_index + 1
            pending = {
                call["tool_call_id"]
                for call in tool_calls
                if call["tool_call_id"]
            }
            observations: list[dict[str, Any]] = []
            while (
                next_index < len(rows)
                and rows[next_index][1].get("role") == "toolResult"
            ):
                tool_result = rows[next_index][1]
                source_call_id = str(tool_result.get("toolCallId") or "")
                if source_call_id not in pending:
                    break
                details = tool_result.get("details")
                observation_text = ""
                if isinstance(details, dict):
                    aggregated = details.get("aggregated")
                    if isinstance(aggregated, str) and aggregated.strip():
                        observation_text = aggregated
                if not observation_text:
                    observation_text = text_from_content(
                        tool_result.get("content")
                    )
                observations.append(
                    {
                        "source_call_id": source_call_id or None,
                        "content": observation_text or None,
                    }
                )
                pending.discard(source_call_id)
                next_index += 1
                if not pending:
                    break

            agent_step: dict[str, Any] = {
                "step_id": len(steps) + 1,
                "source": "agent",
                "message": agent_message,
                "model_name": model_name,
            }
            if timestamp:
                agent_step["timestamp"] = timestamp
            if tool_calls:
                agent_step["tool_calls"] = tool_calls
            if observations:
                agent_step["observation"] = {"results": observations}
            metrics = usage_metrics(message.get("usage"))
            if metrics:
                agent_step["metrics"] = metrics
            steps.append(agent_step)
            row_index = next_index
            continue

        row_index += 1

    if len(steps) < 2:
        return None

    session_id = (
        str(agent_meta.get("sessionId"))
        if isinstance(agent_meta, dict) and agent_meta.get("sessionId")
        else "unknown"
    )
    final_usage = (
        agent_meta.get("usage")
        if isinstance(agent_meta, dict)
        and isinstance(agent_meta.get("usage"), dict)
        else {}
    )
    input_tokens = _json_int(final_usage.get("input"))
    output_tokens = _json_int(final_usage.get("output"))
    cache_read = _json_int(final_usage.get("cacheRead"))
    return {
        "schema_version": "ATIF-v1.7",
        "session_id": session_id,
        "agent": {
            "name": "openclaw",
            "version": os.environ.get("NEMOCLAW_INSTALL_REF", "").strip()
            or "unknown",
            "model_name": model_name,
        },
        "steps": steps,
        "final_metrics": {
            "total_prompt_tokens": input_tokens + cache_read or None,
            "total_completion_tokens": output_tokens or None,
            "total_cached_tokens": cache_read or None,
            "total_steps": len(steps),
        },
    }


def _atomic_write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(value, encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def collect_and_publish_openclaw_trajectory(
    sandbox_name: str,
    log_dir: Path,
    agent_log_dir: Path,
    instruction: str,
    deadline: float | None = None,
) -> dict[str, Any]:
    """Copy the current OpenClaw session and publish its normalized trajectory."""
    raw_log = (log_dir / "openclaw-agent.log").read_text(
        encoding="utf-8",
        errors="replace",
    )
    envelope, session_file = _openclaw_session_file(raw_log)
    _atomic_write_text(
        log_dir / "openclaw-agent.log",
        _redact_runtime_text(raw_log),
    )
    quoted_session = shlex.quote(session_file)
    quoted_base = shlex.quote(str(OPENCLAW_SESSION_DIR))
    script = (
        "set -eu; "
        f"base={quoted_base}; src={quoted_session}; "
        'base_resolved=$(readlink -f -- "$base"); '
        'resolved=$(readlink -f -- "$src"); '
        '[ -f "$resolved" ]; '
        '[ "$(dirname "$resolved")" = "$base_resolved" ]; '
        'case "$(basename "$resolved")" in *.jsonl) ;; *) exit 65 ;; esac; '
        'size=$(wc -c < "$resolved"); '
        f'[ "$size" -le {OPENCLAW_SESSION_MAX_BYTES} ] || exit 66; '
        'cat -- "$resolved"'
    )
    result = _sandbox_exec(
        sandbox_name,
        script,
        timeout=_deadline_timeout(
            deadline,
            60,
            "OpenClaw session transcript collection",
        ),
    )
    if result.returncode != 0:
        detail = (
            result.stderr
            or result.stdout
            or f"exit {result.returncode}"
        )[-500:]
        raise RuntimeError(
            "Could not collect the managed OpenClaw session transcript: "
            f"{_redact_runtime_text(detail)}"
        )
    raw_session_jsonl = result.stdout or ""
    if not raw_session_jsonl.strip():
        raise RuntimeError("The managed OpenClaw session transcript was empty")
    if len(raw_session_jsonl.encode("utf-8")) > OPENCLAW_SESSION_MAX_BYTES:
        raise RuntimeError("The managed OpenClaw session transcript exceeded 50 MiB")
    session_jsonl = _redact_runtime_text(raw_session_jsonl)

    trajectory = _openclaw_session_jsonl_to_atif(
        session_jsonl,
        instruction=instruction,
        envelope=envelope,
    )
    if trajectory is None:
        raise RuntimeError(
            "The managed OpenClaw session transcript had no usable ATIF steps"
        )
    trajectory_json = json.dumps(
        trajectory,
        ensure_ascii=False,
        separators=(",", ":"),
    ) + "\n"

    _atomic_write_text(
        agent_log_dir / "openclaw.session.jsonl",
        session_jsonl,
    )
    _atomic_write_text(agent_log_dir / "trajectory.json", trajectory_json)
    _atomic_write_text(log_dir / "openclaw.session.jsonl", session_jsonl)
    _atomic_write_text(log_dir / "trajectory.json", trajectory_json)
    report = {
        "session_file": session_file,
        "session_bytes": len(session_jsonl.encode("utf-8")),
        "trajectory_steps": len(trajectory["steps"]),
        "agent_trajectory": str(agent_log_dir / "trajectory.json"),
    }
    _atomic_write_text(
        log_dir / "trajectory_publish.json",
        json.dumps(report, indent=2) + "\n",
    )
    return report


def _openclaw_visible_text(raw: str) -> list[str]:
    """Return user-visible assistant payload text from supported envelopes."""
    text: list[str] = []
    for document in _agent_json_documents(raw):
        if not isinstance(document, dict):
            continue
        status = document.get("status")
        if isinstance(status, str) and status.lower() in {"error", "failed", "failure"}:
            continue
        if document.get("error"):
            continue
        payloads = document.get("payloads")
        result = document.get("result")
        if payloads is None and isinstance(result, dict):
            result_status = result.get("status")
            if (
                isinstance(result_status, str)
                and result_status.lower() in {"error", "failed", "failure"}
            ):
                continue
            if result.get("error"):
                continue
            payloads = result.get("payloads")
        if not isinstance(payloads, list):
            continue
        for payload in payloads:
            if not isinstance(payload, dict):
                continue
            if payload.get("isError") is True or payload.get("error"):
                continue
            value = payload.get("text")
            if isinstance(value, str) and value.strip():
                text.append(value.strip())
    return text


def _openclaw_log_completed(log_dir: Path) -> bool:
    try:
        text = (log_dir / "openclaw-agent.log").read_text(encoding="utf-8")
    except OSError:
        return False
    return bool(_openclaw_visible_text(text))


def _openclaw_cli_snapshot(
    sandbox_name: str,
    log_dir: Path,
    deadline: float | None = None,
) -> tuple[str, int | None]:
    """Read process state and, when stopped, its status and log in one probe."""
    result = _sandbox_exec(
        sandbox_name,
        f"if [ ! -f {OPENCLAW_RUN_DIR}/openclaw-agent.pid ]; then "
        f"printf '{OPENCLAW_STATE_PREFIX}missing\\n'; "
        "else "
        f"pid=$(cat {OPENCLAW_RUN_DIR}/openclaw-agent.pid); "
        f"if [ -f {OPENCLAW_RUN_DIR}/openclaw-agent.rc ]; then "
        f"printf '{OPENCLAW_STATE_PREFIX}stopped\\n'; "
        f"rc=$(cat {OPENCLAW_RUN_DIR}/openclaw-agent.rc 2>/dev/null || true); "
        f"printf '{OPENCLAW_RC_PREFIX}%s\\n' \"$rc\"; "
        f"printf '{OPENCLAW_LOG_BEGIN}\\n'; "
        f"cat {OPENCLAW_RUN_DIR}/openclaw-agent.log 2>/dev/null || true; "
        "elif kill -0 \"$pid\" 2>/dev/null; then "
        f"printf '{OPENCLAW_STATE_PREFIX}running\\n'; "
        "else "
        f"printf '{OPENCLAW_STATE_PREFIX}stopped\\n'; "
        f"printf '{OPENCLAW_RC_PREFIX}\\n'; "
        f"printf '{OPENCLAW_LOG_BEGIN}\\n'; "
        f"cat {OPENCLAW_RUN_DIR}/openclaw-agent.log 2>/dev/null || true; "
        "fi; "
        "fi",
        timeout=_deadline_timeout(deadline, 30, "OpenClaw process snapshot"),
    )
    lines = (result.stdout or "").splitlines(keepends=True)
    state = "unknown"
    state_index = -1
    for index, line in enumerate(lines):
        marker = line.rstrip("\r\n")
        if not marker.startswith(OPENCLAW_STATE_PREFIX):
            continue
        candidate = marker.removeprefix(OPENCLAW_STATE_PREFIX)
        if candidate in {"missing", "running", "stopped"}:
            state = candidate
            state_index = index
            break

    returncode: int | None = None
    log_index = -1
    if state == "stopped":
        for index in range(state_index + 1, len(lines)):
            marker = lines[index].rstrip("\r\n")
            if marker.startswith(OPENCLAW_RC_PREFIX):
                raw_returncode = marker.removeprefix(OPENCLAW_RC_PREFIX)
                try:
                    returncode = int(raw_returncode)
                except ValueError:
                    returncode = None
            if marker == OPENCLAW_LOG_BEGIN:
                log_index = index
                break
        if log_index >= 0:
            (log_dir / "openclaw-agent.log").write_text(
                _redact_runtime_text("".join(lines[log_index + 1 :])),
                encoding="utf-8",
            )
    return state, returncode


def stop_openclaw_cli(
    sandbox_name: str,
    deadline: float | None = None,
) -> None:
    result = _sandbox_exec(
        sandbox_name,
        _openclaw_process_cleanup_script(),
        timeout=_deadline_timeout(deadline, 30, "OpenClaw process cleanup"),
    )
    if result.returncode != 0:
        detail = (
            result.stderr
            or result.stdout
            or f"exit {result.returncode}"
        )[-500:]
        raise RuntimeError(f"OpenClaw process cleanup failed: {detail}")


def _openclaw_process_cleanup_script(
    run_dir: str = OPENCLAW_RUN_DIR,
) -> str:
    """Return fail-closed shell that stops only the recorded OpenClaw group."""
    return "\n".join(
        [
            f"run_dir={shlex.quote(run_dir)}",
            'pid_file="$run_dir/openclaw-agent.pid"',
            'pgid_file="$run_dir/openclaw-agent.pgid"',
            'if [ -f "$pid_file" ]; then',
            '  pid=$(cat "$pid_file" 2>/dev/null || true)',
            '  pgid=$(cat "$pgid_file" 2>/dev/null || true)',
            '  case "$pid" in ""|*[!0-9]*)',
            '    echo "invalid recorded OpenClaw pid" >&2; exit 70 ;;',
            "  esac",
            '  case "$pgid" in ""|*[!0-9]*)',
            '    echo "invalid recorded OpenClaw process group" >&2; exit 70 ;;',
            "  esac",
            '  [ "$pid" = "$pgid" ] || {',
            '    echo "recorded OpenClaw pid is not its process-group leader" >&2',
            "    exit 70",
            "  }",
            "  group_has_live_members() {",
            "    members=$(ps -eo pgid=,stat=) || {",
            '      echo "could not inspect OpenClaw process group" >&2',
            "      exit 70",
            "    }",
            '    printf "%s\\n" "$members" | awk -v target="$pgid" '
            "'$1 == target && $2 !~ /^Z/ { found=1 } "
            "END { exit !found }'",
            "  }",
            "  validated=0",
            '  if kill -0 "$pid" 2>/dev/null; then',
            '    actual_pgid=$(ps -o pgid= -p "$pid" 2>/dev/null '
            '| tr -d "[:space:]")',
            '    if ! kill -0 "$pid" 2>/dev/null; then',
            "      :",
            '    elif [ "$actual_pgid" != "$pgid" ]; then',
            '      echo "live process does not match recorded OpenClaw group" >&2',
            "      exit 70",
            '    elif [ ! -r "/proc/$pid/cmdline" ]; then',
            '      if kill -0 "$pid" 2>/dev/null; then',
            '        echo "recorded OpenClaw process has no readable cmdline" >&2',
            "        exit 70",
            "      fi",
            "    else",
            '      cmdline=$(tr "\\000" " " < "/proc/$pid/cmdline" '
            "2>/dev/null || true)",
            '      if kill -0 "$pid" 2>/dev/null; then',
            '        case "$cmdline" in',
            '          *"$run_dir/openclaw-agent.rc.tmp"*) validated=1 ;;',
            "          *)",
            '            echo "refusing to stop an unrelated recorded pid" >&2',
            "            exit 70",
            "            ;;",
            "        esac",
            "      fi",
            "    fi",
            "  fi",
            '  if [ "$validated" != 1 ]; then',
            "    if group_has_live_members; then",
            '      echo "OpenClaw leader exited with live group members" >&2',
            "      exit 70",
            "    fi",
            "  else",
            '    kill -TERM -"$pgid" 2>/dev/null || true',
            "    for _attempt in 1 2 3 4 5; do",
            "      group_has_live_members || break",
            "      sleep 1",
            "    done",
            "    if group_has_live_members; then",
            '      kill -KILL -"$pgid" 2>/dev/null || true',
            "      for _attempt in 1 2 3 4 5; do",
            "        group_has_live_members || break",
            "        sleep 1",
            "      done",
            "      if group_has_live_members; then",
            '        echo "OpenClaw process group survived SIGKILL" >&2',
            "        exit 70",
            "      fi",
            "    fi",
            "  fi",
            "fi",
        ]
    )


def _start_openclaw_cli_async(
    sandbox_name: str,
    prompt: str,
    timeout_s: int,
    log_dir: Path,
    deadline: float | None = None,
) -> dict[str, Any]:
    ensure_openclaw_gateway(sandbox_name, log_dir, deadline)
    openclaw_timeout_s = _deadline_timeout(
        deadline,
        timeout_s,
        "OpenClaw agent launch",
    )
    inner = _openclaw_cli_command(prompt, openclaw_timeout_s)
    cleanup = _openclaw_process_cleanup_script()
    worker = (
        "set +e; "
        f"sh -lc {shlex.quote(inner)}; rc=$?; "
        f"printf '%s\\n' \"$rc\" > {OPENCLAW_RUN_DIR}/openclaw-agent.rc.tmp; "
        f"mv {OPENCLAW_RUN_DIR}/openclaw-agent.rc.tmp "
        f"{OPENCLAW_RUN_DIR}/openclaw-agent.rc; "
        "trap 'exit 0' TERM INT HUP; "
        "while :; do sleep 60; done"
    )
    launcher = (
        "set -eu; "
        f"mkdir -p {OPENCLAW_RUN_DIR}; "
        "command -v setsid >/dev/null 2>&1 || { "
        "echo 'setsid is required for scoped OpenClaw cleanup' >&2; exit 69; }; "
        f"{cleanup}; "
        f"rm -f {OPENCLAW_RUN_DIR}/openclaw-agent.log "
        f"{OPENCLAW_RUN_DIR}/openclaw-agent.pid "
        f"{OPENCLAW_RUN_DIR}/openclaw-agent.pgid "
        f"{OPENCLAW_RUN_DIR}/openclaw-agent.rc "
        f"{OPENCLAW_RUN_DIR}/openclaw-agent.rc.tmp; "
        f"setsid sh -lc {shlex.quote(worker)} "
        f"> {OPENCLAW_RUN_DIR}/openclaw-agent.log 2>&1 & "
        "pid=$!; "
        f"echo \"$pid\" > {OPENCLAW_RUN_DIR}/openclaw-agent.pid; "
        "pgid=''; "
        "for _attempt in 1 2 3 4 5 6 7 8 9 10; do "
        'pgid=$(ps -o pgid= -p "$pid" 2>/dev/null | tr -d "[:space:]"); '
        '[ "$pgid" = "$pid" ] && break; '
        'kill -0 "$pid" 2>/dev/null || break; '
        "sleep 1; "
        "done; "
        '[ "$pgid" = "$pid" ] || { '
        'kill "$pid" 2>/dev/null || true; '
        'echo "OpenClaw launcher did not create a private process group" >&2; '
        "exit 70; }; "
        f"echo \"$pgid\" > {OPENCLAW_RUN_DIR}/openclaw-agent.pgid; "
        "echo started"
    )
    result = _sandbox_exec(
        sandbox_name,
        launcher,
        timeout=_deadline_timeout(deadline, 60, "OpenClaw async launcher"),
    )
    (log_dir / "openclaw-launch.log").write_text(
        f"returncode={result.returncode}\nstdout:\n{result.stdout or ''}\nstderr:\n{result.stderr or ''}\n"
        "mode=async\n",
        encoding="utf-8",
    )
    return {
        "status": 200 if result.returncode == 0 else 500,
        "body": {
            "ok": result.returncode == 0,
            "mode": "cli-async",
            "returncode": result.returncode,
        },
        "stdout_tail": (result.stdout or "")[-4000:],
        "stderr_tail": (result.stderr or "")[-4000:],
        "error": "",
        "error_type": "",
    }


def run_openclaw_cli(
    sandbox_name: str,
    prompt: str,
    timeout_s: int,
    log_dir: Path,
    wait_profile: str = "",
    deadline: float | None = None,
) -> dict[str, Any]:
    # The caller may share one end-to-end budget across gateway recovery,
    # OpenClaw, and profile readiness.  Start the local deadline before
    # gateway recovery so a slow recovery cannot silently extend the agent
    # phase beyond that budget.
    deadline = deadline if deadline is not None else time.monotonic() + timeout_s
    start = _start_openclaw_cli_async(
        sandbox_name,
        prompt,
        timeout_s,
        log_dir,
        deadline,
    )
    if not _response_ok(start):
        start["body"]["mode"] = "cli"
        return start

    poll_sec = max(5, int(os.environ.get("NEMOCLAW_OPENCLAW_POLL_SEC", "15")))
    returncode = 124
    stdout = start.get("stdout_tail", "")
    stderr = start.get("stderr_tail", "")
    error = "OpenClaw final output was not emitted before timeout"
    error_type = "Timeout"
    completed = False
    state = "unknown"

    while time.monotonic() < deadline:
        try:
            state, cli_returncode = _openclaw_cli_snapshot(
                sandbox_name,
                log_dir,
                deadline,
            )
        except TimeoutError:
            break
        except subprocess.TimeoutExpired:
            try:
                _sleep_before_deadline(deadline, poll_sec)
            except TimeoutError:
                break
            continue
        if state == "stopped":
            if cli_returncode == 0 and _openclaw_log_completed(log_dir):
                returncode = 0
                error = ""
                error_type = ""
                completed = True
            elif cli_returncode == 0:
                returncode = 1
                error = "OpenClaw process exited successfully without assistant payload text"
                error_type = "OpenClawMissingOutput"
            elif cli_returncode is None:
                returncode = 1
                error = "OpenClaw process stopped without recording its exit status"
                error_type = "OpenClawMissingExitStatus"
            else:
                returncode = cli_returncode
                error = f"OpenClaw process exited with status {cli_returncode}"
                error_type = "OpenClawStopped"
            break
        if state == "missing":
            returncode = 1
            error = "OpenClaw process state files are missing"
            error_type = "OpenClawMissingState"
            break
        try:
            _sleep_before_deadline(deadline, poll_sec)
        except TimeoutError:
            break

    with (log_dir / "openclaw-launch.log").open("a", encoding="utf-8") as handle:
        handle.write(
            f"mode=blocking-poll\nreturncode={returncode}\ncompleted={str(completed).lower()}\n"
            f"last_state={state}\nerror_type={error_type}\nerror={error}\n"
        )
    return {
        "status": 200 if returncode == 0 else 500,
        "body": {
            "ok": returncode == 0,
            "mode": "cli",
            "returncode": returncode,
        },
        "stdout_tail": stdout[-4000:],
        "stderr_tail": stderr[-4000:],
        "error": error,
        "error_type": error_type,
    }


def _response_ok(response: dict[str, Any]) -> bool:
    body = response.get("body")
    return 200 <= int(response.get("status", 0)) < 300 and isinstance(body, dict) and bool(body.get("ok"))


def _vss_base_ready(deadline: float | None = None) -> tuple[bool, str]:
    probes = [
        ["curl", "-sf", "--max-time", "15", "http://localhost:8000/docs"],
        ["curl", "-sf", "--max-time", "15", "http://localhost:3000/"],
    ]
    for probe in probes:
        result = _run(
            probe,
            timeout=_deadline_timeout(deadline, 20, "VSS base readiness probe"),
        )
        if result.returncode != 0:
            return False, f"{' '.join(probe)} failed: {(result.stderr or result.stdout)[-300:]}"

    result = _run(
        ["docker", "ps", "--format", "{{.Names}}"],
        timeout=_deadline_timeout(deadline, 20, "VSS base container probe"),
    )
    if result.returncode != 0:
        return False, f"docker ps failed: {(result.stderr or result.stdout)[-300:]}"
    names = set(result.stdout.splitlines())
    missing = sorted({"vss-agent", "vss-agent-ui", "redis"} - names)
    if missing:
        return False, "missing containers: " + ", ".join(missing)
    return True, "VSS base readiness probes passed"


def _vss_lvs_ready(deadline: float | None = None) -> tuple[bool, str]:
    base_ok, base_message = _vss_base_ready(deadline)
    if not base_ok:
        return base_ok, base_message
    probes = [
        ["curl", "-sf", "--max-time", "15", "http://localhost:38111/v1/ready"],
    ]
    for probe in probes:
        result = _run(
            probe,
            timeout=_deadline_timeout(deadline, 20, "VSS LVS readiness probe"),
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or f"exit {result.returncode}")[-300:]
            return False, f"{' '.join(probe)} failed: {detail}"

    result = _run(
        ["docker", "ps", "--format", "{{.Names}}"],
        timeout=_deadline_timeout(deadline, 20, "VSS LVS container probe"),
    )
    if result.returncode != 0:
        return False, f"docker ps failed: {(result.stderr or result.stdout)[-300:]}"
    names = set(result.stdout.splitlines())
    if "vss-lvs" not in names:
        return False, "missing containers: vss-lvs"
    return True, "VSS lvs readiness probes passed"


def _profile_ready(
    profile: str,
    deadline: float | None = None,
) -> tuple[bool, str]:
    if profile == "lvs":
        return _vss_lvs_ready(deadline)
    return _vss_base_ready(deadline)


def wait_for_profile(
    profile: str,
    timeout_s: int,
    log_dir: Path,
    deadline: float | None = None,
) -> dict[str, Any]:
    if not profile:
        return {"waited": False, "reason": "no profile requested"}

    deadline = deadline if deadline is not None else time.monotonic() + timeout_s
    attempts: list[dict[str, Any]] = []
    while time.monotonic() < deadline:
        try:
            ok, message = _profile_ready(profile, deadline)
        except TimeoutError as exc:
            ok, message = False, str(exc)
        except subprocess.TimeoutExpired as exc:
            ok, message = False, f"VSS readiness probe timed out: {exc}"
        attempts.append({"t": round(time.time(), 3), "ok": ok, "message": message})
        (log_dir / "nemoclaw_wait.json").write_text(json.dumps(attempts, indent=2), encoding="utf-8")
        if ok:
            return {"waited": True, "ok": True, "profile": profile, "message": message}
        if isinstance(message, str) and "deadline exceeded" in message:
            break
        try:
            _sleep_before_deadline(deadline, 30)
        except TimeoutError:
            break
    return {
        "waited": True,
        "ok": False,
        "profile": profile,
        "message": attempts[-1]["message"] if attempts else "no readiness attempts ran",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prompt-file", required=True)
    parser.add_argument("--env-file", default="/tmp/skill-eval/nemoclaw/nemoclaw.env")
    parser.add_argument("--log-dir", default="/logs/agent")
    parser.add_argument(
        "--agent-log-dir",
        default="/logs/agent",
        help="Harbor agent-log directory where the current ATIF trajectory is published",
    )
    parser.add_argument("--name", default="NemoClaw Harbor skill evaluation")
    parser.add_argument("--dashboard-port", default=os.environ.get("NEMOCLAW_DASHBOARD_PORT", "18789"))
    parser.add_argument("--launch-mode", choices=("hook", "cli"), default="hook")
    parser.add_argument("--timeout", type=int, default=3600)
    parser.add_argument("--wait-profile", default="", help="Wait for the live VSS profile to become ready after hook launch")
    parser.add_argument("--expected-skill", default="", help="Fail fast if the prompt file does not reference this skill")
    args = parser.parse_args(argv)

    log_dir = Path(args.log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    agent_log_dir = Path(args.agent_log_dir)
    _load_env_file(Path(args.env_file))

    sandbox_name = os.environ.get("NEMOCLAW_SANDBOX_NAME", "demo")
    hooks_path = "/" + os.environ.get("OPENCLAW_HOOKS_PATH", "/hooks").strip("/")
    hook_url = f"http://127.0.0.1:{args.dashboard_port}{hooks_path}/agent"
    started = time.time()
    deadline = time.monotonic() + args.timeout
    cleanup_reserve_s = min(60, max(1, args.timeout // 10))
    agent_deadline = (
        deadline - cleanup_reserve_s
        if args.launch_mode == "cli" and args.timeout > cleanup_reserve_s
        else deadline
    )
    prompt = ""
    response: dict[str, Any] = {"status": 0, "body": "", "error": ""}
    wait_report = {"waited": False}
    try:
        prompt = Path(args.prompt_file).read_text(encoding="utf-8")
        (log_dir / "nemoclaw_prompt.md").write_text(prompt, encoding="utf-8")
        expected_skill = args.expected_skill.strip()
        if expected_skill and f"`/{expected_skill}`" not in prompt and f"/{expected_skill}" not in prompt:
            raise RuntimeError(
                f"prompt file {args.prompt_file} does not reference expected "
                f"skill /{expected_skill}; refusing to launch a stale NemoClaw task"
            )

        if args.launch_mode == "cli":
            try:
                response = run_openclaw_cli(
                    sandbox_name,
                    prompt,
                    args.timeout,
                    log_dir,
                    wait_profile=args.wait_profile,
                    deadline=agent_deadline,
                )
                if _response_ok(response):
                    wait_report = wait_for_profile(
                        args.wait_profile,
                        args.timeout,
                        log_dir,
                        deadline=agent_deadline,
                    )
            finally:
                cleanup_errors: list[str] = []
                stop_error = ""
                trajectory_error = ""
                try:
                    stop_openclaw_cli(sandbox_name, deadline)
                except Exception as exc:
                    stop_error = _redact_runtime_text(str(exc))
                    cleanup_errors.append(
                        f"stop: {type(exc).__name__}: {stop_error}"
                    )
                try:
                    collect_openclaw_cli_log(sandbox_name, log_dir, deadline)
                except Exception as exc:
                    cleanup_errors.append(
                        "collect: "
                        f"{type(exc).__name__}: "
                        f"{_redact_runtime_text(str(exc))}"
                    )
                try:
                    collect_and_publish_openclaw_trajectory(
                        sandbox_name,
                        log_dir,
                        agent_log_dir,
                        prompt,
                        deadline,
                    )
                except Exception as exc:
                    trajectory_error = _redact_runtime_text(str(exc))
                    cleanup_errors.append(
                        "trajectory: "
                        f"{type(exc).__name__}: {trajectory_error}"
                    )
                if stop_error and _response_ok(response):
                    response = {
                        "status": 500,
                        "body": {
                            "ok": False,
                            "mode": "cli",
                            "returncode": 1,
                        },
                        "stdout_tail": response.get("stdout_tail", ""),
                        "stderr_tail": response.get("stderr_tail", ""),
                        "error": (
                            "OpenClaw completed but its process group "
                            f"could not be cleaned up: {stop_error}"
                        ),
                        "error_type": "OpenClawCleanupError",
                    }
                elif trajectory_error and _response_ok(response):
                    response = {
                        "status": 500,
                        "body": {
                            "ok": False,
                            "mode": "cli",
                            "returncode": 1,
                        },
                        "stdout_tail": response.get("stdout_tail", ""),
                        "stderr_tail": response.get("stderr_tail", ""),
                        "error": (
                            "OpenClaw completed but its current trajectory "
                            f"could not be published: {trajectory_error}"
                        ),
                        "error_type": "OpenClawTrajectoryError",
                    }
                if cleanup_errors:
                    (log_dir / "openclaw-cleanup.log").write_text(
                        "\n".join(cleanup_errors) + "\n",
                        encoding="utf-8",
                    )
        else:
            hooks_token = _read_hooks_token()
            if not hooks_token:
                response["error"] = "OpenClaw hooks token is not available; run the notebook setup adapter first"
            else:
                payload = {"name": args.name, "message": prompt}
                ensure_forward(str(args.dashboard_port), sandbox_name)
                response = post_hook(hook_url, hooks_token, payload, timeout=60)
                if _response_ok(response):
                    wait_report = wait_for_profile(
                        args.wait_profile,
                        args.timeout,
                        log_dir,
                        deadline=deadline,
                    )
    except Exception as exc:  # Keep Harbor artifacts structured on setup failures.
        response = {
            "status": 0,
            "body": "",
            "error": _redact_runtime_text(str(exc)),
            "error_type": type(exc).__name__,
        }
    elapsed = time.time() - started

    report = {
        "hook_url": hook_url,
        "sandbox": sandbox_name,
        "launch_mode": args.launch_mode,
        "elapsed_s": round(elapsed, 3),
        "response": response,
        "wait": wait_report,
    }
    (log_dir / "nemoclaw_hooks_response.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    (log_dir / "agent.log").write_text(
        "NemoClaw/OpenClaw headless launch\n"
        f"sandbox: {sandbox_name}\n"
        f"hook_url: {hook_url}\n"
        f"response: {json.dumps(response, sort_keys=True)}\n"
        f"prompt:\n{prompt}\n",
        encoding="utf-8",
    )

    ok = _response_ok(response)
    if wait_report.get("waited"):
        ok = ok and bool(wait_report.get("ok"))
    print(json.dumps(report, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
