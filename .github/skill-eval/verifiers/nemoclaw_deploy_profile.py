#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Deterministic verifier for NemoClaw deploy-profile Harbor tasks."""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any

LOG_PATH = Path("/logs/artifacts/nemoclaw/openclaw-agent.log")
OUT_DIR = Path("/logs/verifier")


def _iter_json_objects(text: str):
    decoder = json.JSONDecoder()
    index = 0
    while index < len(text):
        start = text.find("{", index)
        if start < 0:
            break
        try:
            parsed, offset = decoder.raw_decode(text[start:])
        except json.JSONDecodeError:
            index = start + 1
            continue
        if isinstance(parsed, dict):
            yield parsed
        index = start + max(offset, 1)


def _find_value(obj: Any, key: str) -> str:
    if isinstance(obj, dict):
        value = obj.get(key)
        if isinstance(value, str) and value:
            return value
        for child in obj.values():
            found = _find_value(child, key)
            if found:
                return found
    elif isinstance(obj, list):
        for child in obj:
            found = _find_value(child, key)
            if found:
                return found
    return ""


def _openclaw_text() -> tuple[str, str]:
    try:
        raw = LOG_PATH.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return "", ""
    final = ""
    for event in _iter_json_objects(raw):
        final = _find_value(event, "finalAssistantVisibleText") or final
    return raw, final


def _run_shell(command: str) -> tuple[bool, str]:
    result = subprocess.run(
        ["bash", "-lc", command],
        capture_output=True,
        text=True,
        timeout=30,
    )
    evidence = "\n".join(
        part for part in (f"exit={result.returncode}", result.stdout, result.stderr) if part
    )
    return result.returncode == 0, evidence[-1000:]


def _command_from_check(check: str) -> str | None:
    match = re.search(r"`([^`]+)`\s+returns exit 0", check)
    return match.group(1) if match else None


def _service_from_check(check: str) -> str | None:
    match = re.search(r"grep -qx ([^` ]+)", check)
    if not match:
        return None
    return match.group(1).strip("'\"")


def _service_aliases(service: str) -> list[str]:
    aliases = [service]
    if service == "phoenix":
        aliases.extend(["vss-haproxy-ingress", "brevlab.com", "secure link"])
    return aliases


def _log_has_service(service: str, final_text: str, raw_log: str) -> bool:
    haystacks = [final_text.lower(), raw_log.lower()]
    return any(alias.lower() in haystack for alias in _service_aliases(service) for haystack in haystacks)


def _fallback_pass(check: str, final_text: str, raw_log: str) -> tuple[bool, str]:
    lowered_final = final_text.lower()
    if "localhost:8000/health" in check:
        ok = "vss-agent" in lowered_final and ("200 ok" in lowered_final or "health" in lowered_final)
        return ok, "OpenClaw final text reports vss-agent health" if ok else "no API health evidence"
    if "localhost:3000/" in check:
        ok = "vss-agent-ui" in lowered_final or "brevlab.com" in lowered_final
        return ok, "OpenClaw final text reports UI or secure-link access" if ok else "no UI evidence"
    service = _service_from_check(check)
    if service:
        negated = check.lstrip().startswith("`! ")
        present = _log_has_service(service, final_text, raw_log)
        if negated:
            return not present, f"OpenClaw final text service-present={present}"
        return present, f"OpenClaw final text/log contains service alias for {service}: {present}"
    return False, "unsupported check shape for deterministic NemoClaw verifier"


def _evaluate_check(check: str, final_text: str, raw_log: str) -> dict[str, Any]:
    command = _command_from_check(check)
    if command:
        ok, evidence = _run_shell(command)
        if ok:
            return {"pass": True, "matched": evidence, "rationale": "live probe passed", "check": check}
    else:
        evidence = "no live command"
    fallback_ok, fallback_evidence = _fallback_pass(check, final_text, raw_log)
    return {
        "pass": fallback_ok,
        "matched": fallback_evidence if fallback_ok else evidence,
        "rationale": fallback_evidence,
        "check": check,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", required=True)
    parser.add_argument("--step", type=int, default=1)
    args = parser.parse_args()

    spec = json.loads(Path(args.spec).read_text(encoding="utf-8"))
    step = spec["expects"][args.step - 1]
    checks = [str(check) for check in step.get("checks", [])]
    raw_log, final_text = _openclaw_text()

    results = [_evaluate_check(check, final_text, raw_log) for check in checks]
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
                "trajectory_path": str(LOG_PATH),
                "trajectory_found": bool(raw_log),
                "checks": results,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    for item in results:
        status = "PASS" if item["pass"] else "FAIL"
        print(f"{status}: {item['check']}\n  {item['rationale']}")
    print(f"\n=== Results: {passed} passed, {total - passed} failed (of {total}) ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
