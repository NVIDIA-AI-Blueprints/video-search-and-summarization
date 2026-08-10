#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Fail-closed deterministic verifier for NemoClaw deploy-profile tasks."""
from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path
from typing import Any

LOG_PATH = Path("/logs/artifacts/nemoclaw/openclaw-agent.log")
HOOKS_REPORT_PATH = Path(
    "/logs/artifacts/nemoclaw/nemoclaw_hooks_response.json"
)
OUT_DIR = Path("/logs/verifier")


def _execution_gate() -> tuple[bool, str]:
    """Attest that the agent and post-agent readiness wait both succeeded."""
    try:
        report = json.loads(HOOKS_REPORT_PATH.read_text(encoding="utf-8"))
    except OSError:
        return False, "NemoClaw execution report is missing"
    except json.JSONDecodeError:
        return False, "NemoClaw execution report is malformed"
    if not isinstance(report, dict):
        return False, "NemoClaw execution report is not an object"

    response = report.get("response")
    if not isinstance(response, dict):
        return False, "NemoClaw response record is missing"
    try:
        status = int(response.get("status", 0))
    except (TypeError, ValueError):
        status = 0
    if not 200 <= status < 300:
        return False, "NemoClaw response status is not successful"

    body = response.get("body")
    if not isinstance(body, dict) or body.get("ok") is not True:
        return False, "NemoClaw agent response did not report success"
    if body.get("returncode") != 0:
        return False, "NemoClaw agent process returned nonzero"

    wait = report.get("wait")
    if not isinstance(wait, dict) or wait.get("waited") is not True:
        return False, "NemoClaw deployment readiness wait did not run"
    if wait.get("ok") is not True:
        return False, "NemoClaw deployment readiness wait failed"
    return True, "agent response and deployment readiness wait passed"


def _run_shell(command: str) -> tuple[bool, str]:
    result = subprocess.run(
        ["bash", "-lc", command],
        capture_output=True,
        text=True,
        timeout=30,
    )
    evidence = "\n".join(
        part
        for part in (
            f"exit={result.returncode}",
            result.stdout,
            result.stderr,
        )
        if part
    )
    return result.returncode == 0, evidence[-1000:]


def _command_from_check(check: str) -> str | None:
    # These are repository-owned executable probes, not runtime/user input.
    # Shell syntax is intentional: current checks require pipelines, negation,
    # quoting, and environment expansion. Harbor already executes the checked
    # out PR's Python and test scripts on the same isolated worker.
    match = re.search(r"`([^`]+)`\s+returns exit 0", check)
    return match.group(1) if match else None


def _service_from_check(check: str) -> str | None:
    match = re.search(r"grep -qx ([^` ]+)", check)
    if not match:
        return None
    return match.group(1).strip("'\"")


def _service_state(service: str) -> tuple[bool, str]:
    """Require a service to be running and healthy when it has a healthcheck."""
    try:
        result = subprocess.run(
            ["docker", "inspect", "--format", "{{json .State}}", service],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"docker inspect failed: {type(exc).__name__}"
    if result.returncode != 0:
        return False, f"docker inspect exit={result.returncode}"
    try:
        state = json.loads(result.stdout)
    except json.JSONDecodeError:
        return False, "docker inspect returned malformed state"
    if not isinstance(state, dict) or state.get("Running") is not True:
        return False, "container is not running"
    health = state.get("Health")
    if isinstance(health, dict) and health.get("Status") != "healthy":
        return False, f"container health is {health.get('Status', 'unknown')}"
    return True, "container is running and its healthcheck passed"


def _evaluate_check(check: str) -> dict[str, Any]:
    command = _command_from_check(check)
    if not command:
        return {
            "pass": False,
            "matched": "no live command",
            "rationale": "unsupported check shape for deterministic verifier",
            "check": check,
        }

    ok, evidence = _run_shell(command)
    rationale = "live probe passed" if ok else "live probe failed"
    service = _service_from_check(check)
    if ok and service and not command.lstrip().startswith("!"):
        ok, state_evidence = _service_state(service)
        evidence = f"{evidence}\n{state_evidence}"[-1000:]
        rationale = (
            "live probe and container state passed"
            if ok
            else "container state failed"
        )
    return {
        "pass": ok,
        "matched": evidence,
        "rationale": rationale,
        "check": check,
    }


def _load_step(spec_path: Path, step_number: int) -> dict[str, Any]:
    """Load one verifier step or raise a concise configuration error."""
    try:
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"could not read eval spec: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"eval spec is malformed JSON: {exc}") from exc
    if not isinstance(spec, dict):
        raise ValueError("eval spec must be a JSON object")
    expects = spec.get("expects")
    if not isinstance(expects, list) or not expects:
        raise ValueError("eval spec expects must be a non-empty list")
    if step_number < 1 or step_number > len(expects):
        raise ValueError(
            f"step {step_number} is outside the valid range 1..{len(expects)}"
        )
    step = expects[step_number - 1]
    if not isinstance(step, dict):
        raise ValueError(f"eval spec step {step_number} must be an object")
    checks = step.get("checks")
    if (
        not isinstance(checks, list)
        or not checks
        or any(not isinstance(check, str) or not check.strip() for check in checks)
    ):
        raise ValueError(
            f"eval spec step {step_number} checks must be a non-empty list of strings"
        )
    return step


def _write_configuration_failure(
    *, spec_path: Path, step_number: int, error: str
) -> None:
    """Always leave Harbor a zero reward and a diagnostic for bad specs."""
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "reward.txt").write_text("0.0\n", encoding="utf-8")
    (OUT_DIR / "judge.json").write_text(
        json.dumps(
            {
                "spec": str(spec_path),
                "step": step_number,
                "query": None,
                "total": 0,
                "passed": 0,
                "reward": 0.0,
                "configuration_error": error,
                "execution_gate": {
                    "pass": False,
                    "rationale": "not evaluated because the eval spec is invalid",
                    "report_path": str(HOOKS_REPORT_PATH),
                },
                "trajectory_path": str(LOG_PATH),
                "trajectory_found": LOG_PATH.is_file(),
                "checks": [],
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", required=True)
    parser.add_argument("--step", type=int, default=1)
    args = parser.parse_args(argv)

    spec_path = Path(args.spec)
    try:
        step = _load_step(spec_path, args.step)
    except ValueError as exc:
        error = str(exc)
        _write_configuration_failure(
            spec_path=spec_path,
            step_number=args.step,
            error=error,
        )
        print(f"FAIL: invalid NemoClaw eval spec: {error}")
        return 1
    checks = step["checks"]
    gate_ok, gate_evidence = _execution_gate()

    if gate_ok:
        results = [_evaluate_check(check) for check in checks]
    else:
        results = [
            {
                "pass": False,
                "matched": gate_evidence,
                "rationale": "NemoClaw execution gate failed",
                "check": check,
            }
            for check in checks
        ]
    passed = sum(1 for item in results if item["pass"])
    total = len(results)
    reward = (passed / total) if total else 0.0

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "reward.txt").write_text(f"{reward}\n", encoding="utf-8")
    (OUT_DIR / "judge.json").write_text(
        json.dumps(
            {
                "spec": args.spec,
                "step": args.step,
                "query": step.get("query"),
                "total": total,
                "passed": passed,
                "reward": reward,
                "execution_gate": {
                    "pass": gate_ok,
                    "rationale": gate_evidence,
                    "report_path": str(HOOKS_REPORT_PATH),
                },
                "trajectory_path": str(LOG_PATH),
                "trajectory_found": LOG_PATH.is_file(),
                "checks": results,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    for item in results:
        status = "PASS" if item["pass"] else "FAIL"
        print(f"{status}: {item['check']}\n  {item['rationale']}")
    print(
        f"\n=== Results: {passed} passed, "
        f"{total - passed} failed (of {total}) ==="
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
