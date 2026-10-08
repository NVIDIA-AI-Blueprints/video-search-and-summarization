#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Run one Harbor prompt through the Build Vision AI-provisioned sandbox."""

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
    dashboard_port = int(
        os.environ.get("NEMOCLAW_DASHBOARD_PORT", "18789") or "18789"
    )
    result = _sandbox_exec(
        sandbox,
        (
            "code=$(curl --noproxy '*' -sS --connect-timeout 3 --max-time 5 "
            f"-o /dev/null -w '%{{http_code}}' "
            f"http://127.0.0.1:{dashboard_port}/health) "
            '&& { [ "$code" = 200 ] || [ "$code" = 401 ] || '
            '[ "$code" = 403 ]; }'
        ),
        timeout=20,
    )
    return result.returncode == 0


def _ensure_gateway(sandbox: str) -> None:
    """Restore the sandbox's managed gateway if it stopped between tasks."""
    if _gateway_healthy(sandbox):
        return
    restarted = subprocess.run(
        ["nemoclaw", sandbox, "gateway", "restart"],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=360,
        check=False,
    )
    # The managed restart performs its own sustained health check and forward
    # recovery. Match the deployment notebook: a zero exit is authoritative.
    if restarted.returncode == 0:
        return
    recovered = subprocess.run(
        ["nemoclaw", sandbox, "recover"],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=360,
        check=False,
    )
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        if _gateway_healthy(sandbox):
            return
        time.sleep(3)
    restart_detail = (
        restarted.stderr or restarted.stdout or "no restart output"
    ).strip()[-500:]
    recover_detail = (
        recovered.stderr or recovered.stdout or "no recover output"
    ).strip()[-500:]
    raise RuntimeError(
        "OpenClaw gateway is not healthy after bounded NemoClaw recovery "
        f"(restart exit {restarted.returncode}; recover exit "
        f"{recovered.returncode}): restart={restart_detail}; "
        f"recover={recover_detail}"
    )


def _wait_sandbox_ready(sandbox: str, row: dict[str, Any]) -> None:
    deadline = time.monotonic() + 180
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RuntimeError("sandbox_phase deadline exceeded")
        result = subprocess.run(
            ["openshell", "sandbox", "get", sandbox, "-o", "json"],
            stdin=subprocess.DEVNULL, capture_output=True, text=True,
            timeout=min(30, remaining), check=False,
        )
        row["attempts"] = row.get("attempts", 0) + 1
        row["exit_code"] = result.returncode
        if result.returncode != 0:
            raise RuntimeError("sandbox_phase lookup failed")
        phase = json.loads(result.stdout).get("phase")
        if phase == "Ready":
            row["phase"] = phase
            return
        if phase in ("Error", "Failed", "Terminated", "Deleted"):
            row["phase"] = phase
            raise RuntimeError(f"sandbox_phase is {phase}")
        if not isinstance(phase, str) or not phase:
            raise RuntimeError("sandbox_phase response is invalid")
        time.sleep(min(3, max(0, deadline - time.monotonic())))


def _probe_local_inference(sandbox: str, row: dict[str, Any]) -> None:
    expected = os.environ["NEMOCLAW_MODEL"]
    envelope, _ = _run_openclaw(
        sandbox, "Reply with OK only. Do not use tools or change any files.", 120,
    )
    meta = envelope.get("meta", {})
    agent_meta = meta.get("agentMeta", {})
    if meta.get("aborted") is not False or agent_meta.get("model") != expected:
        raise RuntimeError("sandbox inference did not complete through the selected local model")
    if not any(payload.get("text") for payload in envelope.get("payloads", []) if isinstance(payload, dict)):
        raise RuntimeError("sandbox inference returned no assistant response")
    row["model"] = expected
    row["provider"] = agent_meta.get("provider")
    row["usage"] = {key: _int(agent_meta.get("usage", {}).get(key)) for key in ("input", "cacheRead", "output")}


def _check_readiness(sandbox: str, evidence: Path, *, setup: bool = False) -> None:
    """Prove sandbox access, authenticated gateway and CLI before a prompt."""
    stages: list[dict[str, Any]] = []
    artifact = evidence.parent.parent / "artifacts" / "nemoclaw" / evidence.name
    artifact.parent.mkdir(parents=True, exist_ok=True)
    probes = [
        ("sandbox_access", "true"),
        ("gateway_health", None),
        ("gateway_authentication", "openclaw gateway call health --json"),
        ("vss_configuration", "vss configure check"),
    ]
    if setup:
        probes.insert(0, ("sandbox_phase", None))
        if os.environ.get("SKILL_EVAL_LOCAL_NIM_API_KEY"):
            probes.append(("local_inference", None))
    for stage, command in probes:
        row: dict[str, Any] = {"stage": stage, "status": "failed"}
        stages.append(row)
        try:
            if stage == "sandbox_phase":
                _wait_sandbox_ready(sandbox, row)
            elif stage == "local_inference":
                _probe_local_inference(sandbox, row)
            elif command is None:
                if setup:
                    if not _gateway_healthy(sandbox):
                        raise RuntimeError("gateway_health probe failed")
                else:
                    _ensure_gateway(sandbox)
            else:
                # Canonical device scope approval can settle asynchronously.
                # Wait only for that explicit state; bad credentials and other
                # failures are not disguised as slow gateway startup.
                deadline = time.monotonic() + 90
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        row["reason"] = "pairing_deadline"
                        break
                    timeout = min(30 if stage == "gateway_authentication" else 90, remaining)
                    row["attempts"] = row.get("attempts", 0) + 1
                    try:
                        result = _sandbox_exec(sandbox, command, timeout=timeout)
                    except subprocess.TimeoutExpired:
                        if row.get("reason") != "pairing_pending":
                            raise
                        # The final short probe must not erase the last
                        # authenticated client's explicit pairing failure.
                        row["reason"] = "pairing_deadline"
                        break
                    pending = stage == "gateway_authentication" and result.returncode != 0 and any(
                        marker in ((result.stderr or "") + (result.stdout or "")).lower()
                        for marker in ("scope upgrade pending approval", "pairing required")
                    )
                    if stage == "gateway_authentication" and result.returncode != 0:
                        row["reason"] = "pairing_pending" if pending else "command_failed"
                    elif stage == "gateway_authentication":
                        row.pop("reason", None)
                    remaining = deadline - time.monotonic()
                    if not pending or remaining <= 0:
                        break
                    time.sleep(min(3, remaining))
                row["exit_code"] = result.returncode
                if result.returncode != 0:
                    raise RuntimeError(f"NemoClaw readiness failed at {stage} (exit {result.returncode})")
                if stage == "gateway_authentication" and _json_object(result.stdout).get("ok") is not True:
                    row["reason"] = "invalid_health_response"
                    raise RuntimeError("NemoClaw readiness failed at gateway_authentication: health did not report ok")
            row["status"] = "passed"
        except Exception as exc:
            row["exception_type"] = type(exc).__name__
            if setup:
                raise RuntimeError(f"NemoClaw readiness failed at {stage}: {type(exc).__name__}") from exc
            raise
        finally:
            # Metadata only: never store gateway tokens, config or raw output.
            report = json.dumps({
                "sandbox": sandbox,
                "gateway_port": os.environ.get("NEMOCLAW_GATEWAY_PORT", "8080"),
                "stages": stages,
            }, indent=2) + "\n"
            evidence.write_text(report)
            artifact.write_text(report)


def _nemoclaw_exec(
    sandbox: str,
    script: str,
    *,
    timeout: int,
) -> subprocess.CompletedProcess[str]:
    """Run with the trusted runtime env generated by NemoClaw PID 1."""
    runtime_env = "/tmp/nemoclaw-proxy-env.sh"
    wrapped = (
        f"[ -r {shlex.quote(runtime_env)} ] || "
        "{ echo 'NemoClaw runtime env is unavailable' >&2; exit 1; }; "
        f". {shlex.quote(runtime_env)} || exit $?; "
        "unset OPENCLAW_GATEWAY_TOKEN; " + script
    )
    return _sandbox_exec(sandbox, wrapped, timeout=timeout)


def _json_object(raw: str) -> dict[str, Any]:
    try:
        parsed = json.loads(raw)
        documents = parsed if isinstance(parsed, list) else [parsed]
    except json.JSONDecodeError:
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
    if not documents or not isinstance(documents[-1], dict):
        raise TypeError("OpenClaw CLI did not return a JSON object")
    document = documents[-1]
    nested = document.get("result")
    return nested if isinstance(nested, dict) else document


def _int(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _normalize_session(session_jsonl: str) -> tuple[str, dict[str, int]]:
    """Map NemoClaw's exec wrapper to the Bash name existing judges expect."""
    lines: list[str] = []
    totals = {"input": 0, "cacheRead": 0, "output": 0, "turns": 0}
    for raw in session_jsonl.splitlines():
        try:
            record = json.loads(raw)
        except json.JSONDecodeError:
            lines.append(raw)
            continue
        message = record.get("message") if isinstance(record, dict) else None
        if isinstance(message, dict) and message.get("role") == "assistant":
            totals["turns"] += 1
            usage = message.get("usage")
            if isinstance(usage, dict):
                totals["input"] += _int(usage.get("input"))
                totals["cacheRead"] += _int(usage.get("cacheRead"))
                totals["output"] += _int(usage.get("output"))
            content = message.get("content")
            if isinstance(content, list):
                for part in content:
                    if not isinstance(part, dict) or part.get("type") != "toolCall":
                        continue
                    arguments = part.get("arguments")
                    if isinstance(arguments, str):
                        try:
                            arguments = json.loads(arguments)
                        except json.JSONDecodeError:
                            continue
                    if not isinstance(arguments, dict):
                        continue
                    name = part.get("name")
                    if (
                        name == "tool_call"
                        and arguments.get("id") == "openclaw:core:exec"
                        and isinstance(arguments.get("args"), dict)
                    ):
                        part["name"] = "Bash"
                        part["arguments"] = dict(arguments["args"])
                    elif name == "exec":
                        part["name"] = "Bash"
                    normalized = part.get("arguments")
                    if (
                        part.get("name") == "Bash"
                        and isinstance(normalized, dict)
                        and "command" not in normalized
                        and isinstance(normalized.get("cmd"), str)
                    ):
                        normalized["command"] = normalized.pop("cmd")
        lines.append(json.dumps(record, separators=(",", ":")))
    return "\n".join(lines) + "\n", totals


def _session_file(envelope: dict[str, Any]) -> str:
    meta = envelope.get("meta")
    agent_meta = meta.get("agentMeta") if isinstance(meta, dict) else None
    value = agent_meta.get("sessionFile") if isinstance(agent_meta, dict) else None
    if not isinstance(value, str) or _SESSION_PATH.fullmatch(value) is None:
        raise RuntimeError("OpenClaw result did not provide a trusted session file")
    return value


def _set_native_usage(envelope: dict[str, Any], totals: dict[str, int]) -> None:
    if totals["turns"] < 1:
        raise RuntimeError("OpenClaw session contained no assistant turns")
    if totals["input"] + totals["cacheRead"] < 1:
        raise RuntimeError("OpenClaw session contained no native token usage")
    meta = envelope.setdefault("meta", {})
    if not isinstance(meta, dict):
        raise RuntimeError("OpenClaw result metadata is invalid")
    agent_meta = meta.setdefault("agentMeta", {})
    if not isinstance(agent_meta, dict):
        raise RuntimeError("OpenClaw agent metadata is invalid")
    usage = agent_meta.setdefault("usage", {})
    if not isinstance(usage, dict):
        usage = {}
        agent_meta["usage"] = usage
    for key in ("input", "cacheRead", "output"):
        if _int(usage.get(key)) == 0:
            usage[key] = totals[key]


def _run_openclaw(
    sandbox: str,
    prompt: str,
    timeout: int,
) -> tuple[dict[str, Any], str]:
    session_id = f"{os.environ.get('GITHUB_RUN_ID', 'local')}-{uuid.uuid4().hex}"
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
    result = _nemoclaw_exec(sandbox, command, timeout=timeout + 120)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "")[-1000:]
        raise RuntimeError(f"OpenClaw agent exited {result.returncode}: {detail}")
    envelope = _json_object(result.stdout)
    session = _sandbox_exec(
        sandbox,
        f"cat -- {shlex.quote(_session_file(envelope))}",
        timeout=60,
    )
    if session.returncode != 0:
        raise RuntimeError("OpenClaw session could not be collected")
    normalized, totals = _normalize_session(session.stdout)
    _set_native_usage(envelope, totals)
    return envelope, normalized


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--prompt-file")
    mode.add_argument("--setup-check", action="store_true")
    parser.add_argument(
        "--env-file",
        default="/tmp/skill-eval/nemoclaw/nemoclaw.env",
    )
    parser.add_argument("--agent-log-dir", default="/logs/agent")
    parser.add_argument(
        "--timeout",
        type=int,
        default=int(os.environ.get("NEMOCLAW_AGENT_TIMEOUT_SEC", "3300")),
    )
    args = parser.parse_args(argv)

    _load_env_file(Path(args.env_file))
    agent_log_dir = Path(args.agent_log_dir)
    agent_log_dir.mkdir(parents=True, exist_ok=True)
    sandbox = os.environ.get("NEMOCLAW_SANDBOX_NAME", "skill-eval")
    try:
        if args.setup_check:
            _check_readiness(sandbox, agent_log_dir / "setup-readiness.json", setup=True)
            print("NemoClaw setup handoff passed")
            return 0
        prompt = Path(args.prompt_file).read_text(encoding="utf-8")
        _check_readiness(sandbox, agent_log_dir / "readiness.json")
        # Onboarding owns the provider binding. The job's local proxy needs
        # no credential refresh; the native agent turn verifies inference.
        envelope, session = _run_openclaw(sandbox, prompt, args.timeout)
        (agent_log_dir / "openclaw.txt").write_text(
            json.dumps(envelope, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        (agent_log_dir / "openclaw.session.jsonl").write_text(
            session,
            encoding="utf-8",
        )
        return 0
    except Exception as exc:  # noqa: BLE001
        failure = f"NemoClaw/OpenClaw headless run failed: {type(exc).__name__}: {exc}"
        # No session exists when gateway/inference fails before an answer.
        # Give Harbor and the judge this trial's failure evidence instead of
        # leaving them to discover a previous coding or operational raw log.
        (agent_log_dir / "agent.log").write_text(failure + "\n", encoding="utf-8")
        # Readiness failures name only the stage and exception type, so setup
        # never echoes a chained subprocess exception or raw gateway config.
        print(failure, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
