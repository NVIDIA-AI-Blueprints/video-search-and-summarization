#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Elapsed-time heartbeat and per-phase wall time for one skill-eval leg.

A leg's log lines carry no clock. `skills_eval_agent` runs `run_leg.py` through
a Bash tool that surfaces captured output only once the tool returns, so a
132-minute leg reaches the Actions log as a single undated block: run
30733390364 spent 130.3 of 132 minutes inside one gap, and 103.5 of 108 in
another leg, with no way to tell a lock wait from a slow deploy.

Two cheap answers, both here. A heartbeat stamps elapsed time and the current
phase into the same stream, so every neighbouring line can be placed to within
HEARTBEAT_SEC even though the whole block arrives at once. Each phase's wall
time is written next to the results, where the workflow's existing collect step
already uploads it, so the split can be aggregated across legs instead of read
by eye.

TIMING MUST NEVER CHANGE A VERDICT. Every write here is best effort and a
failure to record is logged and swallowed. `leg_log` swallows its own
exceptions because one of these calls sits in `run_leg.main`'s finally, where
raising would replace Harbor's exit code with a logging failure.

WHAT THIS DELIBERATELY DOES NOT DO is sample the network. Harbor runs the
workload on a remote Brev box via `envs.brev_env:BrevEnvironment` while the
wrapper runs on the coordinator, so any counter read here describes the wrong
machine: a box downloading model weights for ten minutes would show up as a
quiet link. Measuring the transfer needs remote sampling of the kind
`gpu_trace.py` does, and is a follow-up once these timings show whether that
time is where we think it is.

STATE IS MODULE-LEVEL and single-writer by design. `run_leg.main` is the only
writer; the heartbeat thread only reads `_CURRENT_PHASE`, and it is joined
before `_PHASES` is serialised. Read the label through `current_phase()` rather
than importing the global, which would copy it once and never see a change.
"""

from __future__ import annotations

import contextlib
import json
import math
import os
import threading
import time
from pathlib import Path


MAX_HEARTBEAT_SEC = 3600.0


def _heartbeat_interval() -> float:
    """Seconds between heartbeats, clamped so a typo cannot spin or break a leg."""
    raw = os.environ.get("RUN_LEG_HEARTBEAT_SEC", "60")
    try:
        interval = float(raw)
    except ValueError:
        print(
            f"[run-leg] ignoring RUN_LEG_HEARTBEAT_SEC={raw!r}, using 60s",
            flush=True,
        )
        return 60.0
    # float() accepts "inf" and "1e999", and max(1.0, inf) is inf, which then
    # reaches Thread.join(timeout=...) in main()'s finally and raises
    # OverflowError: timestamp out of range for platform time_t. That would
    # replace the leg's verdict with an instrumentation traceback, which is the
    # one thing this file must never do. NaN is safe by luck (max returns 1.0),
    # which is not a reason to leave it to luck.
    if not math.isfinite(interval):
        print(
            f"[run-leg] ignoring non-finite RUN_LEG_HEARTBEAT_SEC={raw!r}, using 60s",
            flush=True,
        )
        return 60.0
    # Below a second it busy-loops, filling the log and the very disk this is
    # here to measure. Above an hour it is not a heartbeat.
    return min(max(interval, 1.0), MAX_HEARTBEAT_SEC)


HEARTBEAT_SEC = _heartbeat_interval()
PHASE_TIMINGS_NAME = "phase-timings.json"
_LEG_T0 = time.monotonic()
_PHASES: list[dict] = []
_CURRENT_PHASE = "startup"


def leg_log(message: str) -> None:
    """Instrumentation output, which must never become the leg's outcome.

    A print can raise: a closed or backpressured pipe gives BrokenPipeError,
    and one of these calls sits in main()'s finally, where an exception would
    replace Harbor's exit code with a logging failure.
    """
    with contextlib.suppress(Exception):
        print(f"[run-leg] {message}", flush=True)


def leg_elapsed() -> float:
    """Seconds since this leg started, the clock all timing lines share."""
    return time.monotonic() - _LEG_T0


def record_phase(name: str, started_s: float, ended_s: float) -> None:
    _PHASES.append(
        {
            "phase": name,
            "start_s": round(started_s, 1),
            "end_s": round(ended_s, 1),
            "seconds": round(ended_s - started_s, 1),
        }
    )


def set_phase(name: str) -> None:
    """Point the heartbeat at what the leg is doing right now.

    Needed where the phase's recorded NAME depends on how it ends, which
    `phase()` cannot express because it has to name the phase before the body
    runs. The lock wait is `lock-wait`, `lock-wait-timeout` or
    `lock-wait-failed` depending on its outcome, so it sets its own label.
    """
    global _CURRENT_PHASE
    _CURRENT_PHASE = name


def current_phase() -> str:
    """The label the heartbeat is reporting right now.

    An accessor rather than a bare global, because `from leg_timing import
    _CURRENT_PHASE` binds the value once and then never changes: a caller
    saving the label to restore it later would restore whatever it happened to
    be at import time, which is always "startup".
    """
    return _CURRENT_PHASE


@contextlib.contextmanager
def phase(name: str):
    """Time one named phase and mark its boundaries in the log."""
    global _CURRENT_PHASE
    previous = _CURRENT_PHASE
    started = leg_elapsed()
    # Label flips after the begin line and before the end line, so a tick
    # landing on a boundary cannot report a phase the log has not opened yet.
    leg_log(f"t+{started:.0f}s phase begin: {name}")
    _CURRENT_PHASE = name
    try:
        yield
    finally:
        ended = leg_elapsed()
        record_phase(name, started, ended)
        # Restore BEFORE the end line, mirroring the begin side. Logging first
        # left a window in which a heartbeat could claim the leg was still in a
        # phase the log had already closed.
        _CURRENT_PHASE = previous
        leg_log(f"t+{ended:.0f}s phase end: {name} ({ended - started:.0f}s)")


def _heartbeat(stop: threading.Event) -> None:
    """Emit elapsed time and the current phase until asked to stop.

    The first tick fires immediately rather than after a full interval. A leg
    that dies inside its first HEARTBEAT_SEC would otherwise produce no timing
    line at all, and "no output" is exactly the symptom this exists to remove.
    """
    while not stop.is_set():
        # Checked before every emission, not only after the wait. A leg short
        # enough to finish before this thread is first scheduled would
        # otherwise log "still in <phase>" about work that had already ended.
        leg_log(f"t+{leg_elapsed():.0f}s still in {_CURRENT_PHASE}")
        if stop.wait(HEARTBEAT_SEC):
            return


def start_heartbeat() -> tuple[threading.Thread, threading.Event]:
    """Start the elapsed-time heartbeat. Daemon, so it cannot hold exit."""
    stop = threading.Event()
    thread = threading.Thread(
        target=_heartbeat,
        args=(stop,),
        name="run-leg-heartbeat",
        daemon=True,
    )
    thread.start()
    return thread, stop


def write_phase_timings(results_root: Path) -> None:
    """Drop the phase split next to the results, and summarise it in the log.

    The workflow's existing "Collect results for workflow artifact" step
    uploads whatever is under results_root, so this needs no workflow change
    to reach the artifact.
    """
    if not _PHASES:
        return
    summary = " ".join(f"{entry['phase']}={entry['seconds']:.0f}s" for entry in _PHASES)
    leg_log(f"phase summary: {summary}")
    payload = {
        "run_id": os.environ.get("GITHUB_RUN_ID", ""),
        "slug": os.environ.get("EVAL_SLUG", ""),
        "total_s": round(leg_elapsed(), 1),
        "phases": _PHASES,
    }
    # Written to a sibling then renamed. This runs in main()'s finally, which
    # is exactly where a second signal can arrive during a cancellation, and a
    # write interrupted partway leaves truncated JSON at the real path. A
    # reader cannot tell that from a leg that genuinely recorded nothing, so it
    # is worse than the missing file. os.replace is atomic within a directory,
    # so the artifact is either the previous state or the complete new one.
    destination = results_root / PHASE_TIMINGS_NAME
    scratch_path = destination.with_name(f"{PHASE_TIMINGS_NAME}.partial")
    try:
        results_root.mkdir(parents=True, exist_ok=True)
        scratch_path.write_text(
            json.dumps(payload, indent=2) + "\n", encoding="utf-8"
        )
        os.replace(scratch_path, destination)
    except OSError as exc:
        leg_log(f"could not write {PHASE_TIMINGS_NAME}: {exc!r}")
        # Never leave the half-written sibling behind to be collected as an
        # artifact in its own right.
        with contextlib.suppress(OSError):
            scratch_path.unlink()
