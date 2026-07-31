#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Launch a NemoClaw/OpenClaw scenario from a Harbor trial.

Harbor remains the result owner. The Harbor agent only invokes this script;
this script sends the real prompt to OpenClaw so the VSS skills run inside
NemoClaw/OpenClaw with the VSS Orchestrator MCP available.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import shlex
import subprocess
import sys
import time
import urllib.error
import urllib.parse
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
OPENCLAW_SESSION_MAX_DEPTH = 8
OPENCLAW_LAUNCH_SESSION_METADATA = "openclaw-launch-session.json"
OPENCLAW_LLM_TIMEOUT_RETRY_MIN_SECONDS = 120
OPENCLAW_LLM_TIMEOUT_RETRY_MAX_SECONDS = 600
OPENCLAW_PROFILE_READINESS_RESERVE_SECONDS = 120
RTSP_TOOL_ENV_READY_SENTINEL = "RTSP_SAMPLE_URL is set"
RTSP_TOOL_ENV_PROBE_COMMAND = (
    'test -n "${RTSP_SAMPLE_URL:-}" && '
    "printf 'RTSP_SAMPLE_URL is set\\n'"
)
RTSP_TOOL_ID = "openclaw:core:exec"
OPENCLAW_LLM_TIMEOUT_TEXT = (
    "GatewayClientRequestError: FailoverError: LLM request timed out"
)
OPENCLAW_LLM_TIMEOUT_MARKER = re.compile(
    rf"^{re.escape(OPENCLAW_LLM_TIMEOUT_TEXT)}\.?$"
)
ANSI_COLOR_ESCAPE_PATTERN = re.compile(r"\x1b\[[0-9;]*m")
OPENCLAW_LLM_TIMEOUT_CONTINUATION_PROMPT = (
    "Resume the original evaluation task in this same session after the "
    "transient LLM request timeout. Do not repeat setup or work that the "
    "session already completed. Finish only the incomplete work, then perform "
    "all terminal deployment, endpoint, profile-readiness, and task-result "
    "verification required by the original task. Report the final result only "
    "after those original success criteria are satisfied."
)
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
RTSP_EXACT_REDACTION = (
    "<redacted:RTSP_SAMPLE_URL;match=exact-runtime-value>"
)
RTSP_URI_REDACTION = "<redacted:RTSP_URL>"
RTSP_URI_PATTERN = re.compile(
    r"\brtsps?:(?:(?:\\/|/)){2}(?:(?:\\.)|[^\s\"'<>\\])+",
    re.IGNORECASE,
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
    """Redact runtime credentials and derived RTSP endpoints."""
    replacements: dict[str, str] = {}
    labeled_credentials: dict[str, str] = {}

    def add(value: str, placeholder: str) -> None:
        if not value:
            return
        replacements.setdefault(value, placeholder)
        escaped = json.dumps(value, ensure_ascii=False)[1:-1]
        if escaped != value:
            replacements.setdefault(escaped, placeholder)

    def add_component(
        component: str,
        raw_value: str,
        minimum_length: int,
        *,
        credential: bool = False,
    ) -> None:
        placeholder = (
            "<redacted:RTSP_SAMPLE_URL;"
            f"component={component}>"
        )
        variants = {
            raw_value,
            urllib.parse.unquote(raw_value),
        }
        for component_value in variants:
            if not component_value:
                continue
            if credential:
                labeled_credentials.setdefault(
                    component_value,
                    placeholder,
                )
                escaped = json.dumps(
                    component_value,
                    ensure_ascii=False,
                )[1:-1]
                labeled_credentials.setdefault(escaped, placeholder)
            if len(component_value) >= minimum_length:
                add(component_value, placeholder)

    for key in RUNTIME_REDACTION_KEYS:
        value = os.environ.get(key, "")
        if not value:
            continue
        if key != "RTSP_SAMPLE_URL" and len(value) < 8:
            continue
        placeholder = (
            RTSP_EXACT_REDACTION
            if key == "RTSP_SAMPLE_URL"
            else f"<redacted:{key}>"
        )
        add(value, placeholder)
        if key != "RTSP_SAMPLE_URL":
            continue

        try:
            parsed = urllib.parse.urlsplit(value)
            hostname = parsed.hostname or ""
            port = parsed.port
        except ValueError:
            continue
        if parsed.scheme.lower() not in {"rtsp", "rtsps"}:
            continue

        add_component("authority", parsed.netloc, 4)
        raw_userinfo = (
            parsed.netloc.rpartition("@")[0]
            if "@" in parsed.netloc
            else ""
        )
        add_component("userinfo", raw_userinfo, 3)
        add_component("hostname", hostname, 4)
        add_component(
            "host-port",
            f"{hostname}:{port}" if hostname and port else "",
            4,
        )
        add_component(
            "username",
            parsed.username or "",
            12,
            credential=True,
        )
        add_component(
            "password",
            parsed.password or "",
            12,
            credential=True,
        )
        add_component("path", parsed.path, 8)
        add_component("query", parsed.query, 8)
        add_component("fragment", parsed.fragment, 8)
        for label in hostname.split("."):
            add_component("hostname-label", label, 8)
        for segment in parsed.path.split("/"):
            add_component("path-segment", segment, 8)
        for _, query_value in urllib.parse.parse_qsl(
            parsed.query,
            keep_blank_values=True,
        ):
            add_component("query-value", query_value, 8)
        for raw_query_item in parsed.query.split("&"):
            _, separator, raw_query_value = raw_query_item.partition("=")
            if separator:
                add_component("query-value", raw_query_value, 8)

    redacted = raw
    if replacements:
        ordered_values = sorted(
            replacements,
            key=len,
            reverse=True,
        )
        pattern = re.compile("|".join(map(re.escape, ordered_values)))
        redacted = pattern.sub(
            lambda match: replacements[match.group(0)],
            redacted,
        )
    for credential, placeholder in sorted(
        labeled_credentials.items(),
        key=lambda item: len(item[0]),
        reverse=True,
    ):
        labeled_pattern = re.compile(
            (
                r"((?i:\b(?:rtsp[\s_-]*)?"
                r"(?:user(?:name)?|password|passwd|pass|credential)\b)"
                r"[\"']?(?:\s+(?i:is|was)\s+|\s*[:=]\s*)[\"']?)"
                + re.escape(credential)
                + r"(?=$|[\s\"'`,;&)}\]])"
            )
        )
        redacted = labeled_pattern.sub(
            lambda match: match.group(1) + placeholder,
            redacted,
        )
    return RTSP_URI_PATTERN.sub(RTSP_URI_REDACTION, redacted)


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
    return subprocess.run(
        cmd,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _deadline_timeout(deadline: float | None, cap_s: int, phase: str) -> int:
    """Return a bounded subprocess timeout without extending an outer deadline."""
    if deadline is None:
        return cap_s
    remaining_s = int(deadline - time.monotonic())
    if remaining_s <= 0:
        raise TimeoutError(f"NemoClaw agent deadline exceeded during {phase}")
    return min(cap_s, remaining_s)


def _openshell_gateway_name() -> str:
    raw_port = os.environ.get("NEMOCLAW_GATEWAY_PORT", "8080").strip()
    if (
        not raw_port.isascii()
        or not raw_port.isdigit()
        or len(raw_port) > 5
    ):
        raise ValueError("NEMOCLAW_GATEWAY_PORT is invalid")
    port = int(raw_port)
    if port < 1024 or port > 65535:
        raise ValueError("NEMOCLAW_GATEWAY_PORT is invalid")
    return "nemoclaw" if port == 8080 else f"nemoclaw-{port}"


def _sleep_before_deadline(deadline: float | None, seconds: int) -> None:
    """Sleep for at most the time left in an outer deadline."""
    if deadline is None:
        time.sleep(seconds)
        return
    remaining_s = deadline - time.monotonic()
    if remaining_s <= 0:
        raise TimeoutError("NemoClaw agent deadline exceeded while waiting")
    time.sleep(min(seconds, remaining_s))


def _sandbox_exec(
    sandbox_name: str,
    script: str,
    *,
    timeout: int,
) -> subprocess.CompletedProcess[str]:
    encoded_script = base64.b64encode(script.encode("utf-8")).decode("ascii")
    wrapper = (
        f"_ci_script=$(printf %s {shlex.quote(encoded_script)} | base64 -d) "
        '&& exec sh -lc "$_ci_script"'
    )
    if shutil_which("openshell"):
        command = [
            "openshell",
            "sandbox",
            "exec",
            "-n",
            sandbox_name,
            "-g",
            _openshell_gateway_name(),
            "--",
            "sh",
            "-lc",
            wrapper,
        ]
        return _run(command, timeout=timeout)
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


def ensure_openclaw_gateway(
    sandbox_name: str,
    log_dir: Path,
    deadline: float | None = None,
) -> None:
    attempts: list[str] = []

    def write_attempts() -> None:
        (log_dir / "openclaw_gateway_recover.log").write_text(
            _redact_runtime_text("\n\n".join(attempts) + "\n"),
            encoding="utf-8",
        )

    try:
        try:
            recover = _run(
                ["nemoclaw", "sandbox", "recover", sandbox_name],
                timeout=_deadline_timeout(
                    deadline,
                    360,
                    "NemoClaw sandbox recovery",
                ),
            )
        except subprocess.TimeoutExpired as exc:
            attempts.append(f"sandbox recover timed out: {exc}")
            write_attempts()
            raise RuntimeError(
                f"OpenClaw gateway recovery timed out for sandbox {sandbox_name}"
            ) from exc
        else:
            attempts.append(
                "sandbox recover\n"
                f"returncode={recover.returncode}\n"
                f"stdout:\n{recover.stdout or ''}\n"
                f"stderr:\n{recover.stderr or ''}"
            )

        # `sandbox recover` is the supported idempotent health path. A zero
        # exit means NemoClaw verified or recovered gateway health and its
        # required routing/forwards. Do not override that result with an
        # ordinary sandbox-exec curl: OpenClaw can use a child network
        # namespace and onboarding can select a dashboard port other than
        # 18789.
        write_attempts()
        if recover.returncode == 0:
            return

        # Recovery can fail closed for registry, route, secret-boundary, MCP,
        # or forward-ownership checks. Do not bypass that refusal with a
        # separate force-restart path.
        raise RuntimeError(
            f"OpenClaw gateway recovery failed for sandbox {sandbox_name}"
        )
    except TimeoutError as exc:
        attempts.append(str(exc))
        write_attempts()
        raise


def _fresh_openclaw_session_id() -> str:
    run_id = os.environ.get("GITHUB_RUN_ID", "ci").strip() or "ci"
    return f"{run_id}-{uuid.uuid4().hex}"


def _openclaw_cli_command(
    prompt: str,
    timeout_s: int,
    session_id: str | None = None,
    *,
    local: bool = False,
) -> str:
    session_id = session_id or _fresh_openclaw_session_id()
    no_proxy = "localhost,127.0.0.1,::1,10.200.0.1"
    ca_path = "/etc/openshell-tls/ca-bundle.pem"
    local_arg = "--local " if local else ""
    return (
        "unset BREV_INSTANCE NEMOCLAW_BREV_INSTANCE; "
        f"export NO_PROXY={shlex.quote(no_proxy)}; "
        f"export no_proxy={shlex.quote(no_proxy)}; "
        f"export NODE_EXTRA_CA_CERTS={shlex.quote(ca_path)}; "
        "export OPENCLAW_DISABLE_STREAMING_TOOL_CALLS=1; "
        "openclaw agent --agent main --thinking off "
        f"{local_arg}--json "
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


def _assert_rtsp_tool_shell_visibility(trajectory_path: Path) -> None:
    """Require the exact successful OpenClaw exec-tool probe in ATIF."""
    try:
        trajectory = json.loads(trajectory_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            "RTSP runtime tool-shell trajectory is unavailable"
        ) from exc
    steps = trajectory.get("steps") if isinstance(trajectory, dict) else None
    if not isinstance(steps, list):
        raise RuntimeError("RTSP runtime tool-shell trajectory has no steps")

    for step in steps:
        if not isinstance(step, dict) or step.get("source") != "agent":
            continue
        calls = step.get("tool_calls")
        observation = step.get("observation")
        results = (
            observation.get("results")
            if isinstance(observation, dict)
            else None
        )
        if not isinstance(calls, list):
            continue
        result_by_call = {
            result["source_call_id"]: result["content"]
            for result in (results if isinstance(results, list) else ())
            if isinstance(result, dict)
            and isinstance(result.get("source_call_id"), str)
            and isinstance(result.get("content"), str)
        }
        for call in calls:
            if not isinstance(call, dict):
                continue
            function_name = call.get("function_name")
            arguments = call.get("arguments")
            if function_name == "exec" or (
                isinstance(function_name, str)
                and function_name.rsplit(":", 1)[-1] == "exec"
            ):
                raise RuntimeError(
                    "OpenClaw's first exec tool call bypassed the pinned "
                    "OpenClaw exec-tool wrapper"
                )
            if function_name != "tool_call" or not isinstance(
                arguments,
                dict,
            ):
                continue
            tool_id = arguments.get("id")
            if (
                isinstance(tool_id, str)
                and tool_id.rsplit(":", 1)[-1] == "exec"
                and tool_id != RTSP_TOOL_ID
            ):
                raise RuntimeError(
                    "OpenClaw's first exec tool call used an unpinned exec tool"
                )
            if (
                tool_id != RTSP_TOOL_ID
            ):
                continue
            # The eval contract requires this probe to be the first shell
            # action. Once the first pinned exec tool call is encountered,
            # no later call may repair a different or failed command.
            tool_args = arguments.get("args")
            if (
                not isinstance(tool_args, dict)
                or tool_args.get("command") != RTSP_TOOL_ENV_PROBE_COMMAND
            ):
                raise RuntimeError(
                    "OpenClaw's first exec tool call was not the canonical "
                    "RTSP_SAMPLE_URL probe"
                )
            call_id = call.get("tool_call_id")
            if not isinstance(call_id, str):
                raise RuntimeError(
                    "OpenClaw's first exec tool call had no result identity"
                )
            try:
                envelope = json.loads(result_by_call.get(call_id, ""))
            except json.JSONDecodeError as exc:
                raise RuntimeError(
                    "OpenClaw's first exec tool call result was not JSON"
                ) from exc
            if not isinstance(envelope, dict):
                raise RuntimeError(
                    "OpenClaw's first exec tool call result was not an object"
                )
            tool = envelope.get("tool")
            result = envelope.get("result")
            if (
                not isinstance(tool, dict)
                or tool.get("id") != RTSP_TOOL_ID
                or not isinstance(result, dict)
            ):
                raise RuntimeError(
                    "OpenClaw's first exec tool call result did not attest "
                    "the pinned exec tool"
                )
            content = result.get("content")
            details = result.get("details")
            if (
                isinstance(content, list)
                and len(content) == 1
                and isinstance(content[0], dict)
                and content[0].get("type") == "text"
                and content[0].get("text") == RTSP_TOOL_ENV_READY_SENTINEL
                and isinstance(details, dict)
                and details.get("status") == "completed"
                and type(details.get("exitCode")) is int
                and details.get("exitCode") == 0
                and details.get("aggregated")
                == RTSP_TOOL_ENV_READY_SENTINEL
            ):
                return
            raise RuntimeError(
                "OpenClaw's first exec tool call did not return the exact "
                "successful RTSP_SAMPLE_URL sentinel"
            )
    raise RuntimeError(
        "OpenClaw trajectory did not prove RTSP_SAMPLE_URL visibility "
        "inside an actual exec tool shell"
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


def _validate_openclaw_session_path(
    value: Any,
    *,
    context: str,
) -> str:
    """Validate one managed main-agent session path without resolving it."""
    if not isinstance(value, str) or not value.strip():
        raise RuntimeError(f"{context} did not provide an OpenClaw session path")
    if any(char in value for char in ("\0", "\r", "\n")):
        raise RuntimeError(f"{context} contains control characters")

    candidate = PurePosixPath(value)
    if (
        not candidate.is_absolute()
        or candidate.parent != OPENCLAW_SESSION_DIR
        or candidate.suffix != ".jsonl"
        or candidate.name in {"", ".jsonl"}
    ):
        raise RuntimeError(
            f"{context} is outside the managed main-agent session directory"
        )
    return str(candidate)


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
    return envelope, _validate_openclaw_session_path(
        session_file,
        context="OpenClaw result-envelope session path",
    )


def _openclaw_parent_session_file(session_jsonl: str) -> str | None:
    """Return the validated parent path from one session's JSONL header."""
    headers: list[dict[str, Any]] = []
    for line_number, raw_line in enumerate(session_jsonl.splitlines(), 1):
        line = raw_line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                "OpenClaw session transcript contains malformed JSON "
                f"at line {line_number}"
            ) from exc
        if not isinstance(record, dict):
            raise RuntimeError(
                "OpenClaw session transcript contains a non-object JSON "
                f"record at line {line_number}"
            )
        if record.get("type") == "session":
            headers.append(record)

    if len(headers) != 1:
        raise RuntimeError(
            "OpenClaw session transcript must contain exactly one session header"
        )
    parent = headers[0].get("parentSession")
    if parent is None:
        return None
    return _validate_openclaw_session_path(
        parent,
        context="OpenClaw parentSession path",
    )


def _merge_openclaw_session_chain(
    sessions: list[tuple[str, str]],
) -> str:
    """Merge root-to-leaf JSONL while deduplicating retained compaction rows."""

    def stable_record(record: dict[str, Any]) -> str:
        """Remove only fields OpenClaw rewrites during compaction rotation."""
        comparable = dict(record)
        comparable.pop("parentId", None)
        if comparable.get("type") == "compaction":
            # Successor rotation moves this boundary back to the preserved
            # assistant turn while retaining the compaction record's id.
            comparable.pop("firstKeptEntryId", None)
        if comparable.get("type") == "message":
            message = comparable.get("message")
            if (
                isinstance(message, dict)
                and message.get("role") == "assistant"
                and isinstance(message.get("content"), list)
            ):
                normalized_message = dict(message)
                normalized_content: list[Any] = []
                for raw_part in message["content"]:
                    if (
                        not isinstance(raw_part, dict)
                        or raw_part.get("type")
                        not in {"thinking", "redacted_thinking"}
                    ):
                        normalized_content.append(raw_part)
                        continue
                    part = dict(raw_part)
                    for signature_key in (
                        "thinkingSignature",
                        "signature",
                        "thought_signature",
                    ):
                        part.pop(signature_key, None)
                    if part.get("type") == "redacted_thinking":
                        part.pop("data", None)
                    normalized_content.append(part)
                normalized_message["content"] = normalized_content
                comparable["message"] = normalized_message
        return json.dumps(
            comparable,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    seen_records: dict[tuple[str, str], str] = {}
    merged: list[str] = []
    multiple_sessions = len(sessions) > 1

    for session_file, session_jsonl in sessions:
        for line_number, raw_line in enumerate(session_jsonl.splitlines(), 1):
            line = raw_line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise RuntimeError(
                    "OpenClaw session transcript contains malformed JSON in "
                    f"{session_file} at line {line_number}"
                ) from exc
            if not isinstance(record, dict):
                raise RuntimeError(
                    "OpenClaw session transcript contains a non-object JSON "
                    f"record in {session_file} at line {line_number}"
                )

            record_id = record.get("id")
            if (
                multiple_sessions
                and record.get("type") == "message"
                and (not isinstance(record_id, str) or not record_id)
            ):
                raise RuntimeError(
                    "OpenClaw compaction lineage contains a message without "
                    "a stable record id"
                )

            normalized = stable_record(record)
            if isinstance(record_id, str) and record_id:
                key = (str(record.get("type") or ""), record_id)
                prior = seen_records.get(key)
                if prior is not None:
                    if prior != normalized:
                        raise RuntimeError(
                            "OpenClaw compaction lineage reused record id "
                            f"{record_id!r} with different content"
                        )
                    continue
                seen_records[key] = normalized
            merged.append(
                json.dumps(
                    record,
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
            )

    return "\n".join(merged) + ("\n" if merged else "")


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
    if not rows or not any(
        message.get("role") == "user"
        for _, message in rows
    ):
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

    if len(steps) < 2 or steps[0].get("source") != "user":
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


def _launch_root_session_file(log_dir: Path) -> str:
    """Load the fresh launcher root bound to this trial."""
    metadata_path = log_dir / OPENCLAW_LAUNCH_SESSION_METADATA
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            "OpenClaw launch-session metadata is missing or invalid"
        ) from exc
    if not isinstance(metadata, dict):
        raise RuntimeError("OpenClaw launch-session metadata is not an object")

    root_session_id = metadata.get("root_session_id")
    if (
        not isinstance(root_session_id, str)
        or not root_session_id
        or any(char in root_session_id for char in ("/", "\0", "\r", "\n"))
    ):
        raise RuntimeError(
            "OpenClaw launch-session metadata has an invalid root session id"
        )
    expected = str(
        OPENCLAW_SESSION_DIR / f"{root_session_id}.jsonl"
    )
    recorded = _validate_openclaw_session_path(
        metadata.get("root_session_file"),
        context="OpenClaw launch root-session path",
    )
    if recorded != expected:
        raise RuntimeError(
            "OpenClaw launch-session metadata does not match its root session id"
        )
    return recorded


def _read_managed_openclaw_session(
    sandbox_name: str,
    session_file: str,
    max_bytes: int,
    deadline: float | None,
) -> str:
    """Read one exact, non-symlinked session within the managed directory."""
    if max_bytes <= 0:
        raise RuntimeError(
            "OpenClaw session lineage exceeded the 50 MiB aggregate limit"
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
        '[ "$resolved" = "$base_resolved/$(basename "$src")" ]; '
        'case "$(basename "$resolved")" in *.jsonl) ;; *) exit 65 ;; esac; '
        'size=$(wc -c < "$resolved"); '
        f'[ "$size" -le {int(max_bytes)} ] || exit 66; '
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
    session_jsonl = result.stdout or ""
    if not session_jsonl.strip():
        raise RuntimeError("The managed OpenClaw session transcript was empty")
    if len(session_jsonl.encode("utf-8")) > max_bytes:
        raise RuntimeError(
            "OpenClaw session lineage exceeded the 50 MiB aggregate limit"
        )
    return session_jsonl


def _publish_openclaw_failure_root_session(
    sandbox_name: str,
    log_dir: Path,
    root_session_file: str,
    deadline: float | None,
) -> dict[str, Any]:
    """Publish only this launch's exact root JSONL as failure evidence."""
    raw_session_jsonl = _read_managed_openclaw_session(
        sandbox_name,
        root_session_file,
        OPENCLAW_SESSION_MAX_BYTES,
        deadline,
    )
    if _openclaw_parent_session_file(raw_session_jsonl) is not None:
        raise RuntimeError(
            "Fresh OpenClaw failure root session unexpectedly has a parent"
    )
    session_jsonl = _redact_runtime_text(raw_session_jsonl)
    artifact_name = "openclaw.failure-session.jsonl"
    _atomic_write_text(log_dir / artifact_name, session_jsonl)

    report = {
        "root_session_file": root_session_file,
        "source_session_bytes": len(raw_session_jsonl.encode("utf-8")),
        "session_bytes": len(session_jsonl.encode("utf-8")),
        "artifact": str(log_dir / artifact_name),
        "reason": "missing_openclaw_result_envelope",
    }
    _atomic_write_text(
        log_dir / "openclaw_failure_session.json",
        json.dumps(report, indent=2) + "\n",
    )
    return report


def _validate_openclaw_retry_root_session(
    session_jsonl: str,
    *,
    expected_session_id: str,
    expected_prompt: str,
) -> None:
    """Bind a parentless retry root to this launch and its exact prompt."""
    if _openclaw_parent_session_file(session_jsonl) is not None:
        raise RuntimeError(
            "Fresh OpenClaw retry root session unexpectedly has a parent"
        )
    if not expected_prompt.strip():
        raise RuntimeError("OpenClaw retry original prompt was empty")

    exact_prompt_seen = False
    for raw_line in session_jsonl.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        # `_openclaw_parent_session_file` already validated every nonempty row.
        record = json.loads(line)
        if (
            record.get("type") == "session"
            and record.get("id") != expected_session_id
        ):
            raise RuntimeError(
                "Fresh OpenClaw retry root session id did not match its "
                "launch binding"
            )
        message = record.get("message")
        if (
            record.get("type") == "message"
            and isinstance(message, dict)
            and message.get("role") == "user"
        ):
            content = message.get("content")
            if isinstance(content, str):
                persisted_prompt = content
            elif isinstance(content, list):
                persisted_prompt = "".join(
                    part["text"]
                    for part in content
                    if isinstance(part, dict)
                    and part.get("type") == "text"
                    and isinstance(part.get("text"), str)
                )
            else:
                continue
            if persisted_prompt == expected_prompt:
                exact_prompt_seen = True
    if exact_prompt_seen:
        return
    raise RuntimeError(
        "Fresh OpenClaw retry root session did not contain the exact "
        "original user prompt"
    )


def collect_and_publish_openclaw_trajectory(
    sandbox_name: str,
    log_dir: Path,
    agent_log_dir: Path,
    instruction: str,
    deadline: float | None = None,
) -> dict[str, Any]:
    """Copy the current OpenClaw lineage and publish its normalized trajectory."""
    raw_log = (log_dir / "openclaw-agent.log").read_text(
        encoding="utf-8",
        errors="replace",
    )
    root_session_file = _launch_root_session_file(log_dir)
    _atomic_write_text(
        log_dir / "openclaw-agent.log",
        _redact_runtime_text(raw_log),
    )
    if _openclaw_result_envelope(raw_log) is None:
        try:
            _publish_openclaw_failure_root_session(
                sandbox_name,
                log_dir,
                root_session_file,
                deadline,
            )
        except (
            OSError,
            RuntimeError,
            subprocess.SubprocessError,
            ValueError,
        ) as exc:
            raise RuntimeError(
                "OpenClaw output did not end with a JSON result envelope; "
                "the exact fresh root failure session could not be published: "
                f"{_redact_runtime_text(str(exc))}"
            ) from exc
        raise RuntimeError(
            "OpenClaw output did not end with a JSON result envelope; "
            "the exact fresh root failure session was published"
        )
    envelope, leaf_session_file = _openclaw_session_file(raw_log)

    lineage_leaf_first: list[tuple[str, str]] = []
    seen_files: set[str] = set()
    source_session_bytes = 0
    current_session_file = leaf_session_file
    for _ in range(OPENCLAW_SESSION_MAX_DEPTH):
        if current_session_file in seen_files:
            raise RuntimeError(
                "OpenClaw session lineage contains a parent cycle"
            )
        seen_files.add(current_session_file)
        remaining_bytes = OPENCLAW_SESSION_MAX_BYTES - source_session_bytes
        raw_session_jsonl = _read_managed_openclaw_session(
            sandbox_name,
            current_session_file,
            remaining_bytes,
            deadline,
        )
        source_session_bytes += len(
            raw_session_jsonl.encode("utf-8")
        )
        lineage_leaf_first.append(
            (current_session_file, raw_session_jsonl)
        )
        parent_session_file = _openclaw_parent_session_file(
            raw_session_jsonl
        )

        if current_session_file == root_session_file:
            if parent_session_file is not None:
                raise RuntimeError(
                    "Fresh OpenClaw root session unexpectedly has a parent"
                )
            break
        if parent_session_file is None:
            raise RuntimeError(
                "OpenClaw session lineage ended before the fresh launcher root"
            )
        current_session_file = parent_session_file
    else:
        raise RuntimeError(
            "OpenClaw session lineage exceeded the maximum compaction depth"
        )

    if lineage_leaf_first[-1][0] != root_session_file:
        raise RuntimeError(
            "OpenClaw session lineage did not reach the fresh launcher root"
        )
    lineage_root_first = list(reversed(lineage_leaf_first))
    merged_session_jsonl = _merge_openclaw_session_chain(
        lineage_root_first
    )
    session_jsonl = _redact_runtime_text(merged_session_jsonl)

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
        "session_file": leaf_session_file,
        "root_session_file": root_session_file,
        "session_files": [
            session_file
            for session_file, _ in lineage_root_first
        ],
        "session_chain_depth": len(lineage_root_first),
        "source_session_bytes": source_session_bytes,
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


def _openclaw_transient_llm_timeout(raw: str) -> bool:
    """Match only a terminal provider timeout plus its observed sentinel."""
    nonempty_lines: list[str] = []
    for raw_line in raw.splitlines():
        line = ANSI_COLOR_ESCAPE_PATTERN.sub("", raw_line).strip()
        if line:
            nonempty_lines.append(line)
    if nonempty_lines and nonempty_lines[-1] == "Terminated":
        nonempty_lines.pop()
    return bool(
        nonempty_lines
        and OPENCLAW_LLM_TIMEOUT_MARKER.fullmatch(nonempty_lines[-1])
    )


def _openclaw_retryable_llm_timeout(
    *,
    state: str,
    cli_returncode: int | None,
    error_type: str,
    raw_log: str,
) -> bool:
    """Require the exact stopped/rc=1 failure observed from OpenClaw."""
    return (
        state == "stopped"
        and cli_returncode == 1
        and error_type == "OpenClawStopped"
        and _openclaw_transient_llm_timeout(raw_log)
    )


def _preserve_openclaw_attempt_logs(log_dir: Path, attempt: int) -> None:
    """Preserve redacted per-attempt evidence before the next launch rotates it."""
    agent_log = log_dir / "openclaw-agent.log"
    try:
        raw_agent_log = agent_log.read_text(
            encoding="utf-8",
            errors="replace",
        )
    except OSError as exc:
        raise RuntimeError(
            f"could not preserve OpenClaw attempt {attempt} log"
        ) from exc
    _atomic_write_text(
        log_dir / f"openclaw-agent-attempt-{attempt}.log",
        _redact_runtime_text(raw_agent_log),
    )

    launch_log = log_dir / "openclaw-launch.log"
    try:
        raw_launch_log = launch_log.read_text(
            encoding="utf-8",
            errors="replace",
        )
    except FileNotFoundError:
        return
    except OSError as exc:
        raise RuntimeError(
            f"could not preserve OpenClaw attempt {attempt} launch log"
        ) from exc
    _atomic_write_text(
        log_dir / f"openclaw-launch-attempt-{attempt}.log",
        _redact_runtime_text(raw_launch_log),
    )


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
    session_id: str | None = None,
) -> dict[str, Any]:
    ensure_openclaw_gateway(sandbox_name, log_dir, deadline)
    openclaw_timeout_s = _deadline_timeout(
        deadline,
        timeout_s,
        "OpenClaw agent launch",
    )
    root_session_id = session_id or _fresh_openclaw_session_id()
    if (
        not root_session_id
        or any(
            char in root_session_id
            for char in ("/", "\0", "\r", "\n")
        )
    ):
        raise RuntimeError("OpenClaw root session id is invalid")
    root_session_file = str(
        OPENCLAW_SESSION_DIR / f"{root_session_id}.jsonl"
    )
    _atomic_write_text(
        log_dir / OPENCLAW_LAUNCH_SESSION_METADATA,
        json.dumps(
            {
                "root_session_id": root_session_id,
                "root_session_file": root_session_file,
            },
            indent=2,
        )
        + "\n",
    )
    # A retry has already preserved attempt 1 under its attempt-specific
    # filename. Never let its local snapshot masquerade as attempt 2 if the
    # continuation reaches its deadline before a stopped-state snapshot.
    (log_dir / "openclaw-agent.log").unlink(missing_ok=True)
    inner = _openclaw_cli_command(
        prompt,
        openclaw_timeout_s,
        root_session_id,
    )
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
    launch_timeout = _deadline_timeout(
        deadline,
        60,
        "OpenClaw async launcher",
    )
    result = _sandbox_exec(
        sandbox_name,
        launcher,
        timeout=launch_timeout,
    )
    safe_stdout = _redact_runtime_text(result.stdout or "")
    safe_stderr = _redact_runtime_text(result.stderr or "")
    (log_dir / "openclaw-launch.log").write_text(
        f"returncode={result.returncode}\nstdout:\n{safe_stdout}\n"
        f"stderr:\n{safe_stderr}\n"
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
        "stdout_tail": safe_stdout[-4000:],
        "stderr_tail": safe_stderr[-4000:],
        "error": "",
        "error_type": "",
        "root_session_id": root_session_id,
        "root_session_file": root_session_file,
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
    poll_sec = max(5, int(os.environ.get("NEMOCLAW_OPENCLAW_POLL_SEC", "15")))
    attempt_prompt = prompt
    retry_session_id: str | None = None
    retry_report: dict[str, Any] | None = None
    retry_deadline_cap: float | None = None
    readiness_reserve_s = (
        OPENCLAW_PROFILE_READINESS_RESERVE_SECONDS
        if wait_profile
        else 0
    )
    attempt_2_launched = False
    response: dict[str, Any] | None = None

    for attempt in (1, 2):
        if attempt == 1:
            attempt_deadline = deadline
            attempt_timeout_s = timeout_s
        else:
            if (
                retry_deadline_cap is None
                or retry_report is None
                or response is None
            ):
                raise AssertionError(
                    "OpenClaw retry state was not initialized"
                )
            retry_now = time.monotonic()
            remaining_retry_s = retry_deadline_cap - retry_now
            if (
                remaining_retry_s
                < OPENCLAW_LLM_TIMEOUT_RETRY_MIN_SECONDS
            ):
                retry_report["attempted"] = False
                retry_report["skipped"] = (
                    "insufficient_shared_deadline"
                )
                retry_report["remaining_shared_deadline_s"] = max(
                    0,
                    round(deadline - retry_now, 3),
                )
                response["retry"] = retry_report
                return response
            attempt_timeout_s = min(
                OPENCLAW_LLM_TIMEOUT_RETRY_MAX_SECONDS,
                max(1, int(remaining_retry_s)),
            )
            attempt_deadline = min(
                retry_deadline_cap,
                retry_now + attempt_timeout_s,
            )

        start_kwargs: dict[str, Any] = {}
        if retry_session_id is not None:
            start_kwargs["session_id"] = retry_session_id
        start = _start_openclaw_cli_async(
            sandbox_name,
            attempt_prompt,
            attempt_timeout_s,
            log_dir,
            attempt_deadline,
            **start_kwargs,
        )
        if not _response_ok(start):
            body = start.get("body")
            if isinstance(body, dict):
                body["mode"] = "cli"
            start["attempts"] = 1 if attempt == 2 else attempt
            if retry_report is not None:
                if attempt == 2:
                    retry_report["attempted"] = False
                    retry_report["attempt_2_launched"] = False
                    retry_report["launch_failed"] = True
                start["retry"] = retry_report
            start["attempt_2_launched"] = False
            return start
        if attempt == 2:
            attempt_2_launched = True
            assert retry_report is not None
            retry_report["attempted"] = True
            retry_report["attempt_2_launched"] = True

        returncode = 124
        stdout = start.get("stdout_tail", "")
        stderr = start.get("stderr_tail", "")
        error = "OpenClaw final output was not emitted before timeout"
        error_type = "Timeout"
        completed = False
        state = "unknown"
        cli_returncode: int | None = None

        while time.monotonic() < attempt_deadline:
            try:
                state, cli_returncode = _openclaw_cli_snapshot(
                    sandbox_name,
                    log_dir,
                    attempt_deadline,
                )
            except TimeoutError:
                break
            except subprocess.TimeoutExpired:
                try:
                    _sleep_before_deadline(attempt_deadline, poll_sec)
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
                    error = (
                        "OpenClaw process exited successfully without "
                        "assistant payload text"
                    )
                    error_type = "OpenClawMissingOutput"
                elif cli_returncode is None:
                    returncode = 1
                    error = (
                        "OpenClaw process stopped without recording its "
                        "exit status"
                    )
                    error_type = "OpenClawMissingExitStatus"
                else:
                    returncode = cli_returncode
                    error = (
                        "OpenClaw process exited with status "
                        f"{cli_returncode}"
                    )
                    error_type = "OpenClawStopped"
                break
            if state == "missing":
                returncode = 1
                error = "OpenClaw process state files are missing"
                error_type = "OpenClawMissingState"
                break
            try:
                _sleep_before_deadline(attempt_deadline, poll_sec)
            except TimeoutError:
                break

        with (log_dir / "openclaw-launch.log").open(
            "a",
            encoding="utf-8",
        ) as handle:
            handle.write(
                f"mode=blocking-poll\nattempt={attempt}\n"
                f"returncode={returncode}\n"
                f"completed={str(completed).lower()}\n"
                f"last_state={state}\nerror_type={error_type}\n"
                f"error={error}\n"
            )
        response = {
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
            "attempts": attempt,
            "attempt_2_launched": attempt_2_launched,
            "root_session_file": start.get("root_session_file", ""),
        }
        if retry_report is not None:
            response["retry"] = retry_report

        if attempt == 2:
            try:
                _preserve_openclaw_attempt_logs(log_dir, attempt)
            except RuntimeError as exc:
                if _response_ok(response):
                    return {
                        "status": 500,
                        "body": {
                            "ok": False,
                            "mode": "cli",
                            "returncode": 1,
                        },
                        "stdout_tail": response["stdout_tail"],
                        "stderr_tail": response["stderr_tail"],
                        "error": _redact_runtime_text(str(exc)),
                        "error_type": "OpenClawRetryArtifactError",
                        "attempts": attempt,
                        "retry": retry_report,
                        "attempt_2_launched": attempt_2_launched,
                        "root_session_file": response[
                            "root_session_file"
                        ],
                    }
            return response

        if returncode == 0:
            return response
        try:
            raw_attempt_log = (
                log_dir / "openclaw-agent.log"
            ).read_text(encoding="utf-8", errors="replace")
        except OSError:
            return response
        if not _openclaw_retryable_llm_timeout(
            state=state,
            cli_returncode=cli_returncode,
            error_type=error_type,
            raw_log=raw_attempt_log,
        ):
            return response

        retry_deadline_cap = deadline - readiness_reserve_s
        retry_now = time.monotonic()
        remaining_s = deadline - retry_now
        remaining_retry_s = retry_deadline_cap - retry_now
        retry_report = {
            "attempted": False,
            "attempt_2_launched": False,
            "reason": "transient_llm_request_timeout",
            "readiness_reserve_s": readiness_reserve_s,
            "remaining_shared_deadline_s": max(
                0,
                round(remaining_s, 3),
            ),
        }
        if (
            remaining_retry_s
            < OPENCLAW_LLM_TIMEOUT_RETRY_MIN_SECONDS
        ):
            retry_report["skipped"] = "insufficient_shared_deadline"
            response["retry"] = retry_report
            return response

        candidate_session_id = start.get("root_session_id")
        candidate_session_file = start.get("root_session_file")
        if (
            not isinstance(candidate_session_id, str)
            or not candidate_session_id
            or any(
                char in candidate_session_id
                for char in ("/", "\0", "\r", "\n")
            )
        ):
            retry_report["skipped"] = "missing_trusted_root_session_id"
            response["retry"] = retry_report
            return response
        expected_session_file = str(
            OPENCLAW_SESSION_DIR / f"{candidate_session_id}.jsonl"
        )
        try:
            recorded_session_file = _launch_root_session_file(log_dir)
        except (OSError, RuntimeError, ValueError):
            retry_report["skipped"] = "untrusted_root_session_binding"
            response["retry"] = retry_report
            return response
        if (
            candidate_session_file != expected_session_file
            or recorded_session_file != expected_session_file
        ):
            retry_report["skipped"] = "untrusted_root_session_binding"
            response["retry"] = retry_report
            return response

        try:
            root_session_jsonl = _read_managed_openclaw_session(
                sandbox_name,
                expected_session_file,
                OPENCLAW_SESSION_MAX_BYTES,
                retry_deadline_cap,
            )
            _validate_openclaw_retry_root_session(
                root_session_jsonl,
                expected_session_id=candidate_session_id,
                expected_prompt=prompt,
            )
        except (
            OSError,
            RuntimeError,
            subprocess.SubprocessError,
            ValueError,
        ):
            retry_report["skipped"] = "invalid_retry_root_session"
            response["retry"] = retry_report
            return response

        try:
            _preserve_openclaw_attempt_logs(log_dir, attempt)
        except RuntimeError as exc:
            response["error"] = (
                f"{error}; retry not started because attempt evidence "
                f"could not be preserved: "
                f"{_redact_runtime_text(str(exc))}"
            )
            response["error_type"] = "OpenClawRetryArtifactError"
            retry_report["skipped"] = "attempt_log_preservation_failed"
            response["retry"] = retry_report
            return response

        retry_session_id = candidate_session_id
        attempt_prompt = OPENCLAW_LLM_TIMEOUT_CONTINUATION_PROMPT

    raise AssertionError("OpenClaw retry loop exceeded its fixed attempt count")


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


def _vss_alerts_ready(deadline: float | None = None) -> tuple[bool, str]:
    base_ok, base_message = _vss_base_ready(deadline)
    if not base_ok:
        return base_ok, base_message

    probe = [
        "curl",
        "-sf",
        "--max-time",
        "15",
        "http://localhost:8018/v1/health/ready",
    ]
    result = _run(
        probe,
        timeout=_deadline_timeout(deadline, 20, "VSS alerts readiness probe"),
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or f"exit {result.returncode}")[-300:]
        return False, f"{' '.join(probe)} failed: {detail}"

    result = _run(
        ["docker", "ps", "--format", "{{.Names}}"],
        timeout=_deadline_timeout(deadline, 20, "VSS alerts container probe"),
    )
    if result.returncode != 0:
        return False, f"docker ps failed: {(result.stderr or result.stdout)[-300:]}"
    names = set(result.stdout.splitlines())
    missing = sorted({"vss-rtvi-vlm", "kafka"} - names)
    if missing:
        return False, "missing containers: " + ", ".join(missing)
    return True, "VSS alerts readiness probes passed"


def _profile_ready(
    profile: str,
    deadline: float | None = None,
) -> tuple[bool, str]:
    if profile == "lvs":
        return _vss_lvs_ready(deadline)
    if profile == "alerts":
        return _vss_alerts_ready(deadline)
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
    expected_skill = args.expected_skill.strip()
    response: dict[str, Any] = {"status": 0, "body": "", "error": ""}
    wait_report = {"waited": False}
    try:
        prompt = Path(args.prompt_file).read_text(encoding="utf-8")
        (log_dir / "nemoclaw_prompt.md").write_text(prompt, encoding="utf-8")
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
                runtime_attestation_error = ""
                cli_log_collected = False
                try:
                    stop_openclaw_cli(sandbox_name, deadline)
                except Exception as exc:
                    stop_error = _redact_runtime_text(str(exc))
                    cleanup_errors.append(
                        f"stop: {type(exc).__name__}: {stop_error}"
                    )
                try:
                    collect_openclaw_cli_log(sandbox_name, log_dir, deadline)
                    cli_log_collected = True
                except Exception as exc:
                    cleanup_errors.append(
                        "collect: "
                        f"{type(exc).__name__}: "
                        f"{_redact_runtime_text(str(exc))}"
                    )
                if (
                    cli_log_collected
                    and response.get("attempt_2_launched") is True
                ):
                    try:
                        _preserve_openclaw_attempt_logs(log_dir, 2)
                    except RuntimeError as exc:
                        cleanup_errors.append(
                            "attempt-2-log: "
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
                if (
                    expected_skill == "vss-deploy-dense-captioning"
                    and not trajectory_error
                ):
                    try:
                        _assert_rtsp_tool_shell_visibility(
                            log_dir / "trajectory.json"
                        )
                    except Exception as exc:
                        runtime_attestation_error = _redact_runtime_text(
                            str(exc)
                        )
                        cleanup_errors.append(
                            "runtime-tool-shell: "
                            f"{type(exc).__name__}: "
                            f"{runtime_attestation_error}"
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
                elif runtime_attestation_error and _response_ok(response):
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
                            "OpenClaw completed but runtime visibility in its "
                            "actual exec tool shell was not proven: "
                            f"{runtime_attestation_error}"
                        ),
                        "error_type": "OpenClawRuntimeEnvAttestationError",
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
