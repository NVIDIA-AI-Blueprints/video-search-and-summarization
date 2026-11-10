# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Bounded, read-only worker diagnostics collected before canary teardown.

The result is stored under the run-id-scoped coordinator results tree. It is
not copied into the Harbor viewer; the workflow's secret scan must run before
the results artifact can be published.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
from typing import Callable


_REMOTE_SCRIPT = r'''
import json
import subprocess
import time

NAME = "vss-rtvi-cv"
LIMIT = 65536

def run(*args, timeout=30):
    try:
        result = subprocess.run(args, capture_output=True, timeout=timeout)
        return result.returncode, result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        return 124, b"", b"command timed out"
    except OSError as exc:
        return 127, b"", str(exc).encode()

def bounded(data):
    return data[-LIMIT:].decode("utf-8", "replace")

report = {"schema": 1, "collected_at_unix": time.time(), "container": NAME}
rc, out, err = run("nvidia-smi", "--query-gpu=name,driver_version", "--format=csv,noheader")
report["gpu"] = {"exit_code": rc, "output": bounded(out), "error": bounded(err)}
rc, out, err = run("docker", "inspect", NAME)
report["inspection_exit_code"] = rc
if rc == 0:
    try:
        info = json.loads(out)[0]
        state = info.get("State") or {}
        image = info.get("Config") or {}
        report["state"] = {
            "image": image.get("Image"), "image_id": info.get("Image"),
            "restart_count": info.get("RestartCount"),
            "status": state.get("Status"), "running": state.get("Running"),
            "exit_code": state.get("ExitCode"), "oom_killed": state.get("OOMKilled"),
            "error": state.get("Error"), "started_at": state.get("StartedAt"),
            "finished_at": state.get("FinishedAt"),
            "health": (state.get("Health") or {}).get("Status"),
            "cuda_metadata": [value for value in image.get("Env") or []
                              if value.startswith(("CUDA_VERSION=", "NVIDIA_REQUIRE_CUDA=", "TENSORRT_VERSION="))],
        }
    except (ValueError, IndexError, TypeError, AttributeError):
        report["inspection_error"] = "docker inspect returned invalid JSON"
else:
    report["inspection_error"] = bounded(err)
rc, out, err = run("docker", "logs", "--timestamps", "--tail", "300", NAME, timeout=60)
report["logs"] = {"exit_code": rc, "stdout_tail": bounded(out), "stderr_tail": bounded(err)}
print("WORKER_DIAGNOSTICS=" + json.dumps(report, separators=(",", ":")))
'''

_RECEIPT_PREFIX = "WORKER_DIAGNOSTICS="


def capture_worker_diagnostics(
    instance: str,
    results_root: Path,
    *,
    repo_root: Path,
    harbor_requirement: str,
    harbor_env: Callable[[str], dict[str, str]],
    timeout_sec: int = 120,
) -> None:
    """Fetch one CV container snapshot without printing its potentially secret logs."""
    # The same authenticated Brev transport used by worker cleanup. The
    # remote program emits one JSON object, avoiding shell quoting of logs.
    command = [
        "uvx", "--python", sys.executable, "--from", harbor_requirement,
        "python", "-c",
        "import asyncio,sys; from envs.brev_env import _run_brev_exec_retry; "
        "r=asyncio.run(_run_brev_exec_retry(sys.argv[1],sys.argv[2],timeout=int(sys.argv[3]))); "
        "print(r.stdout or ''); print(r.stderr or '',file=sys.stderr); sys.exit(r.return_code)",
        instance,
        "python3 -c " + shlex.quote(_REMOTE_SCRIPT),
        str(max(1, timeout_sec - 30)),
    ]
    result = subprocess.run(
        command, cwd=repo_root, env=harbor_env(instance),
        capture_output=True, text=True, timeout=timeout_sec, check=False,
    )
    if result.returncode:
        # Never put transport stderr in CI logs: Brev/SSH may echo commands or
        # agent output containing credentials.
        raise RuntimeError(f"worker diagnostic transport exited {result.returncode}")
    # brev exec may append the instance name (or another transport message)
    # after the command's stdout. Parse only our unique one-line receipt.
    receipts = [line.removeprefix(_RECEIPT_PREFIX)
                for line in result.stdout.splitlines()
                if line.startswith(_RECEIPT_PREFIX)]
    if len(receipts) != 1:
        raise RuntimeError("worker diagnostics missing or ambiguous receipt")
    try:
        report = json.loads(receipts[0])
    except ValueError as exc:
        raise RuntimeError("worker diagnostics did not return JSON") from exc
    if not isinstance(report, dict) or report.get("schema") != 1:
        raise RuntimeError("worker diagnostics returned an unexpected schema")

    target = results_root / "worker-diagnostics"
    target.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".cv-", dir=target)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(report, stream, indent=2)
            stream.write("\n")
        os.replace(name, target / "vss-rtvi-cv.json")
    finally:
        Path(name).unlink(missing_ok=True)
