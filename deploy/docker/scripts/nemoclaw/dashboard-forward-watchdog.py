#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Watchdog: repair a host dashboard forward that holds its port but carries no HTTP.

    browser / vss-agent-ui -> :18790 relay -> 127.0.0.1:18789 forward -> sandbox dashboard

The OpenShell `forward service <sandbox>` process can keep the loopback port while
no longer carrying traffic. `nemoclaw <sandbox> recover` cannot see that: it judges
an owned forward by a TCP connect, which a wedged forward still accepts, so it
leaves it in place. This watchdog probes `/health` over HTTP and, after
`--threshold` consecutive failures, terminates the forward - only when every
listener on the port is proved to be this sandbox's forward, by executable and
argv - and hands the rest to `nemoclaw <sandbox> recover`.

It never restarts the gateway, recreates the sandbox or reads the gateway token,
so a repair keeps the current `/#token=` link. The relay stays up; it connects
upstream per client and reaches the restored forward on the next connection.

deploy_nemoclaw.ipynb section 3.5 runs one repair pass with these functions and
starts this script detached next to the relay; it attributes a running watchdog
to its sandbox through `--sandbox` on the command line.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import time
from typing import NamedTuple

_TAG = "[dashboard-forward-watchdog]"
FORWARD_HOST = "127.0.0.1"
# Proof that the forward carries HTTP. 401/403 are auth answers from the dashboard
# behind it, which is transport enough; NemoClaw's own probes accept 401 too.
ANSWERING = frozenset({200, 401, 403})
RECOVER_TIMEOUT = 180.0
_TOKEN = re.compile(r"(token=)[^\s&#\"']+", re.IGNORECASE)


def redact(text: str) -> str:
    """Drop any `token=` value: recover can print the dashboard's tokenized URL."""
    return _TOKEN.sub(r"\1<redacted>", text)


def _log(message: str) -> None:
    stamp = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    sys.stderr.write(f"{stamp} {_TAG} {redact(message)}\n")
    sys.stderr.flush()


def listener_pids(port: int) -> list[str]:
    """PIDs listening on *port* (any address); empty when lsof is unavailable."""
    try:
        listing = subprocess.run(["lsof", "-t", f"-i:{port}", "-sTCP:LISTEN"], capture_output=True, text=True)
    except FileNotFoundError:
        return []
    return listing.stdout.split()


def pid_args(pid: str) -> str:
    """Command line of *pid*, untruncated; empty when it is already gone."""
    return subprocess.run(["ps", "-ww", "-p", pid, "-o", "args="], capture_output=True, text=True).stdout.strip()


def is_sandbox_forward(args: str, sandbox: str, port: int) -> bool:
    """True when a command line is this sandbox's dashboard forward: the `openshell`
    binary running onboard's gRPC `forward service <sandbox>`, bound to the port."""
    argv = args.split()
    return (
        bool(argv)
        and os.path.basename(argv[0]) == "openshell"
        and f"--local {FORWARD_HOST}:{port}" in args
        and bool(re.search(rf"\bforward\s+service\s+{re.escape(sandbox)}(\s|$)", args))
    )


def forward_owned(sandbox: str, port: int) -> bool:
    """True when every listener on the port is this sandbox's forward. False when
    nothing listens and when lsof cannot say: an unproved port is never trusted."""
    pids = listener_pids(port)
    return bool(pids) and all(is_sandbox_forward(pid_args(pid), sandbox, port) for pid in pids)


def forward_answers(port: int, timeout: float = 5.0) -> bool:
    """True when `/health` on the forward answers HTTP 200, 401 or 403."""
    probe = subprocess.run(
        [
            "curl", "--noproxy", "*", "-sS", "-o", "/dev/null", "-w", "%{http_code}",
            "--max-time", str(timeout), f"http://{FORWARD_HOST}:{port}/health",
        ],
        capture_output=True,
        text=True,
    )
    code = probe.stdout.strip()
    # A status line followed by a stalled body still times out: that is not answering.
    return probe.returncode == 0 and code.isdigit() and int(code) in ANSWERING


def stop_verified_forward(sandbox: str, port: int, wait: float = 5.0) -> list[str]:
    """Terminate the forward when every listener on the port is proved to be this
    sandbox's; return the pids signalled. Anything unproved is left running."""
    pids = listener_pids(port)
    if not pids or not all(is_sandbox_forward(pid_args(pid), sandbox, port) for pid in pids):
        return []
    for pid in pids:
        subprocess.run(["kill", pid], capture_output=True)
    for _ in range(int(wait * 5)):
        if not set(pids) & set(listener_pids(port)):
            break
        time.sleep(0.2)
    for pid in sorted(set(pids) & set(listener_pids(port))):
        # Still holding the port after SIGTERM; it is proved ours, so finish it.
        subprocess.run(["kill", "-9", pid], capture_output=True)
    return pids


class Repair(NamedTuple):
    answering: bool
    stopped: list[str]
    recover: subprocess.CompletedProcess


def repair(sandbox: str, port: int, wait: float = 15.0, timeout: float = 5.0) -> Repair:
    """One repair pass: stop a proved-but-wedged forward, let `nemoclaw recover`
    re-create it, and wait up to *wait* seconds for `/health`. A forward that is not
    listening at all goes straight to recover, which applies its own ownership gates."""
    stopped = [] if forward_answers(port, timeout) else stop_verified_forward(sandbox, port)
    command = ["nemoclaw", sandbox, "recover"]
    try:
        recovered = subprocess.run(
            command, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=RECOVER_TIMEOUT
        )
    except subprocess.TimeoutExpired:
        # A stalled recover must not freeze the probe loop; count it as a failed repair.
        recovered = subprocess.CompletedProcess(command, 124, "", f"timed out after {RECOVER_TIMEOUT:.0f}s")
    # recover returns before the detached forward is listening.
    deadline = time.monotonic() + wait
    answering = forward_answers(port, timeout)
    for _ in range(int(wait)):
        if answering or time.monotonic() >= deadline:
            break
        time.sleep(1)
        answering = forward_answers(port, timeout)
    return Repair(answering, stopped, recovered)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--sandbox", required=True, help="sandbox whose forward this watches")
    parser.add_argument("--port", type=int, default=18789, help="the forward's loopback port")
    parser.add_argument("--interval", type=float, default=15.0, help="seconds between probes")
    parser.add_argument("--timeout", type=float, default=5.0, help="seconds per /health probe")
    parser.add_argument("--threshold", type=int, default=3, help="consecutive failures before a repair")
    parser.add_argument("--min-repair-gap", type=float, default=120.0, help="minimum seconds between repairs")
    parser.add_argument("--max-failed-repairs", type=int, default=3, help="failed repairs before backing off")
    parser.add_argument("--backoff", type=float, default=900.0, help="seconds between repairs once backed off")
    args = parser.parse_args(argv)
    if args.threshold < 1 or args.interval <= 0:
        parser.error("--threshold must be >= 1 and --interval > 0")
    return args


def run(args: argparse.Namespace, *, clock=time.monotonic, sleep=time.sleep, cycles: int | None = None) -> None:
    """Probe forever (or *cycles* times, for tests)."""
    failures = failed_repairs = 0
    last_repair = float("-inf")
    _log(f"watching http://{FORWARD_HOST}:{args.port}/health for sandbox {args.sandbox!r}")
    while cycles is None or cycles > 0:
        if cycles is not None:
            cycles -= 1
        if forward_answers(args.port, args.timeout):
            if failures or failed_repairs:
                _log("forward answering again")
            failures = failed_repairs = 0
        else:
            failures += 1
            gap = args.backoff if failed_repairs >= args.max_failed_repairs else args.min_repair_gap
            if failures >= args.threshold and clock() - last_repair >= gap:
                last_repair = clock()
                _log(f"{failures} consecutive /health failures; repairing")
                result = repair(args.sandbox, args.port, timeout=args.timeout)
                if result.stopped:
                    _log(f"terminated this sandbox's wedged forward: pid {', '.join(result.stopped)}")
                detail = (result.recover.stderr or result.recover.stdout or "").strip()
                _log(f"`nemoclaw {args.sandbox} recover` exit {result.recover.returncode}{': ' + detail if detail else ''}")
                if result.answering:
                    _log("repaired: forward answering")
                    failures = failed_repairs = 0
                else:
                    failed_repairs += 1
                    _log(f"repair {failed_repairs} failed")
                    if failed_repairs == args.max_failed_repairs:
                        _log(
                            f"backing off to one repair per {args.backoff:.0f}s; "
                            f"diagnose with `nemoclaw {args.sandbox} status`"
                        )
        sleep(args.interval)


def main(argv: list[str] | None = None) -> int:
    try:
        run(parse_args(argv))
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
