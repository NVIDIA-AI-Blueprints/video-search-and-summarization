#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Sample the eval box's GPUs for the life of one leg.

WHY THIS EXISTS. Nothing anywhere records what the GPUs do during a skill-eval
leg. Measured 2026-08-01: `nvidia-smi` accounting mode is `Disabled` on all 13
`vss-eval-*` boxes, so there is no retrospective data even in principle; the
boxes carry no DCGM exporter; and Horde's `gpu_utilization` API is an allocation
report, not a utilisation one. A snapshot of the idle fleet found 21 of 23 GPUs
at 0% holding 210.8 GiB resident, with 11 of 13 boxes still running a full VSS
stack hours after their last leg. That is suggestive, not attributable: it says
nothing about which spec caused it. This module makes it attributable.

WHERE IT HOOKS. `run_leg.py` holds the per-box `flock` and knows the run, spec,
platform and chosen instance at the same moment. That is the only place in the
pipeline where GPU identity and job identity coexist, so joining them here is
free; anywhere else it needs a pipeline change.

DESIGN CONSTRAINTS, each paid for by a previous incident:

* **Default on, never fatal.** Every entry point swallows its exceptions and the
  context manager always yields. A telemetry sampler that fails a deployment
  eval is worse than no telemetry, so this cannot fail a leg even if the box is
  unreachable, `nvidia-smi` is missing, or the fetch times out.
* **Hard-bounded lifetime.** The remote sampler runs under `timeout`, so it dies
  on its own even if this process is SIGKILLed and never calls stop. An earlier
  hand-run sampler leaked and ran 213 minutes across unrelated trials; with no
  bound and no timestamps the trace was unusable and was discarded.
* **Timestamps in every row.** Same incident: a trace that cannot be segmented
  afterwards is worthless.
* **Allowlist, not redaction.** The remote command emits exactly the seven
  `--query-gpu` fields below. It does not read process environments, command
  lines, container state or file contents. Redaction fails open; an allowlist
  fails closed. PR #516 leaked `NGC_CLI_API_KEY` through artifact collection,
  which is why the per-trial `agent/` trajectory is still excluded from the
  uploaded tarball, and why this module must not become a second such channel.
* **Rides the existing artifact.** Output lands under `results_root`, which
  `skills-eval.yml` and `skills-eval-daily.yml` already tar and upload with
  28-day retention. No new network path, and no credential on the GPU box.
  `_reset_docker_runtime` wipes the box between trials, so nothing may be left
  there.

The declared-versus-observed comparison is the point: `declared_gpu_count` comes
from the spec and travels in the sidecar next to what the GPUs actually did.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import signal
import subprocess
import time
from pathlib import Path


def _int_env(name: str, default: int) -> int:
    """Never let a malformed knob take down the leg.

    These are module-level constants, so a bare int() would raise at IMPORT
    time, and run_leg.py's guard only catches ImportError. `EVAL_GPU_TRACE_INTERVAL=x`
    would then fail every leg before main() even runs -- telemetry config
    breaking the thing it observes.
    """
    try:
        value = int(os.environ.get(name, "") or default)
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default

# Fixed field list. This IS the allowlist -- adding to it is a deliberate act,
# and `nounits` keeps the CSV numeric so the collector needs no unit parsing.
QUERY_FIELDS = (
    "timestamp,index,utilization.gpu,utilization.memory,"
    "memory.used,memory.total,power.draw"
)
CSV_HEADER = (
    "timestamp,gpu_index,util_gpu_pct,util_mem_pct,"
    "mem_used_mib,mem_total_mib,power_w"
)

# 10s matched the interval that established medians in the original one-box
# trace. Do not raise the rate because you can: a 2h leg already yields ~1440
# rows for a 2-GPU box, and the collector aggregates them anyway.
INTERVAL_SEC = _int_env("EVAL_GPU_TRACE_INTERVAL", 10)

# A row is accepted only if it has exactly these 7 fields, the first parses as
# an nvidia-smi timestamp and the rest are numeric (or the literal [N/A] that
# nvidia-smi prints for unsupported fields). "at least 6 commas" is NOT an
# allowlist: it publishes whatever the remote `cat` returned, and the remote
# file is a predictable same-user path in /tmp that could have been replaced.
ROW_RE = re.compile(
    r"^\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2}\.\d+"
    r"(?:,\s*(?:-?\d+(?:\.\d+)?|\[N/A\])){6}$"
)
# The recorded PID is remote-supplied text that gets interpolated into a shell
# command. Anything but a bare positive integer is refused outright.
PID_RE = re.compile(r"^[1-9][0-9]*$")
# The remote directory comes back from `mktemp -d` and is then interpolated
# into a shell command, so it is pinned to exactly the shape mktemp produces.
DIR_RE = re.compile(r"^/tmp/vss-gputrace\.[A-Za-z0-9]{8}$")

# Cap what a single fetch may return. Without a bound, a replaced or endless
# source could exhaust the runner's memory before any exception handler helps.
MAX_FETCH_BYTES = _int_env("EVAL_GPU_TRACE_MAX_BYTES", 8 * 1024 * 1024)

# Default ON. Set EVAL_GPU_TRACE=0 to suppress. The failure mode is a missing
# trace, never a failed leg, so opt-out rather than opt-in is the right posture:
# opt-in telemetry that nobody enables collects nothing.
ENABLED = os.environ.get("EVAL_GPU_TRACE", "1") not in ("0", "false", "False", "")

# Round-trip budget for the two `brev exec` calls. Measured 2026-08-01: a warm
# `brev exec` round trip is ~2s, so 120s is ~60x headroom and still bounded.
EXEC_TIMEOUT_SEC = _int_env("EVAL_GPU_TRACE_EXEC_TIMEOUT", 120)

# Slack over the harbor timeout before the remote `timeout` fires. The sampler
# must outlive a normal leg but must not outlive a SIGKILLed one for long.
HARD_STOP_SLACK_SEC = 600


def _read_capped(proc: subprocess.Popen, deadline_sec: float) -> str:
    """Read at most MAX_FETCH_BYTES, enforcing the bound WHILE reading.

    `communicate()` buffers the whole stream and only then can it be sliced,
    so the cap did nothing: a replaced or endless remote source grew the
    runner's RSS without limit. Measured on a 32 MiB emit, maxrss grew 125 MB
    against an 8 MiB "cap". An OOM-killed runner fails the leg, which is
    exactly what this module promises never to do.
    """
    import select

    chunks: list[str] = []
    total = 0
    end = time.monotonic() + deadline_sec
    assert proc.stdout is not None
    while True:
        left = end - time.monotonic()
        if left <= 0:
            raise subprocess.TimeoutExpired(proc.args, deadline_sec)
        # select(), not a bare read(). `read()` blocks, so a deadline checked
        # at the top of the loop never fires on a hung remote and the "total
        # deadline" is not a deadline at all.
        ready, _, _ = select.select([proc.stdout], [], [], min(left, 1.0))
        if not ready:
            continue
        chunk = proc.stdout.read1(65536) if hasattr(proc.stdout, "read1") \
            else os.read(proc.stdout.fileno(), 65536).decode("utf-8", "replace")
        if not chunk:
            break
        total += len(chunk)
        if total > MAX_FETCH_BYTES:
            chunks.append(chunk[: MAX_FETCH_BYTES - (total - len(chunk))])
            print(f"[gpu-trace] remote output exceeded {MAX_FETCH_BYTES} bytes; truncated",
                  flush=True)
            with contextlib.suppress(ProcessLookupError, OSError):
                os.killpg(proc.pid, signal.SIGKILL)
            break
        chunks.append(chunk)
    with contextlib.suppress(Exception):
        proc.wait(timeout=max(1.0, end - time.monotonic()))
    return "".join(chunks)


def _remote(instance: str, command: str, timeout: int = EXEC_TIMEOUT_SEC,
            attempts: int = 2) -> str | None:
    """Run `command` on the box. Returns stdout, or None on any failure.

    Tries `brev exec` first (managed instances), then `ssh <alias>` (registered
    nodes, which `brev exec` cannot reach). `brev shell` writes a lowercased
    `Host` entry into ~/.brev/ssh_config, so the alias is the lowercased name --
    the same convention `envs/brev_env.py::_ssh_alias_for` uses.

    `timeout` is a TOTAL deadline across both attempts, not per attempt: two
    120s attempts would be a 240s stall on a leg whose median is 132s.

    Each attempt runs in its own session and is killed by process GROUP on
    timeout. `subprocess.run`'s own timeout reaps only the immediate child, so
    an ssh that forked would survive; `envs/brev_env.py::_kill_proc_group`
    already fixes this exact problem the same way for harbor.
    """
    candidates = (
        ["brev", "exec", instance, command],
        ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15",
         "-o", "StrictHostKeyChecking=no", instance.lower(), command],
    )[:max(1, attempts)]
    deadline = time.monotonic() + timeout
    for cmd in candidates:
        remaining = deadline - time.monotonic()
        if remaining <= 1:
            break
        try:
            proc = subprocess.Popen(
                cmd, stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                text=True, start_new_session=True,
            )
        except OSError:
            continue
        try:
            out = _read_capped(proc, remaining)
        except subprocess.TimeoutExpired:
            with contextlib.suppress(ProcessLookupError, OSError):
                os.killpg(proc.pid, signal.SIGKILL)
            with contextlib.suppress(Exception):
                proc.communicate(timeout=10)
            continue
        except (OSError, ValueError):
            continue
        if proc.returncode == 0:
            # `brev exec` echoes the instance name on its own line after the
            # command output; drop it so callers get only the payload.
            lines = [ln for ln in (out or "").splitlines()
                     if ln.strip() != instance]
            return "\n".join(lines)
    return None


def _token(run_id: str, spec_stem: str, platform: str, step: int,
           chain: str = "") -> str:
    """Filename-safe, collision-free per-invocation marker.

    Includes the step index because a multi-step spec makes several harbor
    invocations against the same box under one lock, and each needs its own
    trace rather than appending to the previous one.
    """
    def clean(s: str) -> str:
        return "".join(c if c.isalnum() or c in "-_" else "-" for c in str(s))

    # BOTH discriminators are appended AFTER truncation, never before. Truncating
    # a joined string lets a long spec name push the suffix off the end, so two
    # invocations collide and the second silently overwrites the first.
    # `chain` is load-bearing: discover_invocations() supports several chains
    # per leg, and two chains sharing a step index produced the same token, so
    # the second silently overwrote the first. Verified: four remote calls, one
    # surviving file.
    # `chain` used to sit INSIDE the truncated head, which reintroduced exactly
    # that collision for any spec_stem long enough to push it past 100 chars:
    # `_token(r, "x"*100, p, 1, "chainA") == _token(r, "x"*100, p, 1, "chainB")`.
    # Step was already fixed this way; chain was not.
    head = clean(f"{run_id}-{spec_stem}-{platform}")[:100]
    tail = f"-{clean(chain)}" if chain else ""
    return f"{head}{tail}-step{clean(step)}"


@contextlib.contextmanager
def trace(
    instance: str,
    results_root: Path,
    *,
    spec_stem: str = "",
    platform: str = "",
    step: int = 1,
    chain: str = "",
    declared_gpu_count: int | None = None,
    skill: str = "",
    harbor_timeout_sec: int = 7800,
):
    """Sample `instance`'s GPUs for the duration of the block.

    Never raises and never re-raises: the body runs whether or not tracing
    worked. On exit the trace is written to
    ``results_root/gputrace/<token>.csv`` with a ``.json`` sidecar carrying the
    job identity and the declared GPU count.
    """
    if not ENABLED or not instance:
        yield
        return

    run_id = os.environ.get("GITHUB_RUN_ID", "local")
    token = _token(run_id, spec_stem, platform, step, chain)
    started_at = time.time()
    pid: str | None = None
    remote_dir: str | None = None
    remote_csv: str | None = None

    # The remote `timeout` is the load-bearing guarantee: if this process dies
    # without running the finally block, the sampler still exits on its own.
    hard_stop = max(harbor_timeout_sec + HARD_STOP_SLACK_SEC, 900)
    # GRAMMAR IS LOAD-BEARING, and the obvious form is wrong. `mkdir && nohup
    # ... &` backgrounds the whole AND-list, so `$!` is a forked subshell rather
    # than `timeout`, and killing it leaves the sampler running. Worse, that
    # subshell inherits the stdout pipe, so `communicate()` blocks until the
    # SAMPLER exits -- measured 2026-08-01: the start call took 3.03s under bash
    # and 3.04s under sh for a 3s target, versus 0.01s under zsh. Brev execs via
    # bash, so this would have stalled every start until the 120s timeout, then
    # retried over ssh and started a SECOND sampler. Developing on a zsh laptop
    # hid it completely.
    #
    # So: `mkdir` runs as its own statement, exactly ONE command is backgrounded,
    # and its stdout AND stderr are redirected so no descriptor keeps the pipe
    # open. `$!` is then genuinely `timeout`'s pid, and `timeout` forwards
    # SIGTERM to nvidia-smi.
    #
    # The output directory is created with `mktemp -d`, NOT at a path derived
    # from the job identity. A predictable name under a world-writable /tmp is
    # a file-clobbering primitive: shell `>` follows symlinks, so a pre-placed
    # `<token>.csv -> ~/.ssh/authorized_keys` gets truncated. Verified in real
    # bash: a victim file holding "KEEP-ME" was overwritten through exactly that
    # redirect. `mktemp -d` also makes each invocation's directory unique, which
    # removes the separate bug where two chains of one leg sharing a step index
    # overwrote each other's trace.
    #
    # `set -C` (noclobber) is belt-and-braces inside that fresh directory: it
    # refuses to write through an existing name at all.
    start_cmd = (
        # Reap orphans first. Nothing else on the box does: _reset_docker_runtime
        # touches only containers and volumes, the stray-agent reaper matches only
        # claude, and the /logs wipe does not reach /tmp. Every abrupt-death path
        # leaves a directory behind, and these boxes stay up for weeks. 180 min is
        # safely past the hard stop, so this can never hit a live sampler.
        f"find /tmp -maxdepth 1 -name 'vss-gputrace.*' -mmin +180 "
        f"-exec rm -rf {{}} + 2>/dev/null; "
        f"set -C; d=$(mktemp -d /tmp/vss-gputrace.XXXXXXXX) || exit 0; "
        f"echo DIR=$d; "
        # `-f FILE` rather than `> FILE`. A redirected stdout is block-buffered
        # and SIGTERM does not flush it, so up to a full buffer of samples is
        # discarded at exactly the moment cleanup runs -- on a 2-GPU box at 10s
        # that is minutes of data, and a short leg would return nothing at all,
        # logged indistinguishably from an unreachable box. Letting nvidia-smi
        # own the file also puts the nonce directory into its argv, which is
        # what makes the identity check below exact rather than a guess.
        f"nohup timeout -k 10 {hard_stop} nvidia-smi "
        f"--query-gpu={QUERY_FIELDS} --format=csv,noheader,nounits "
        f"-l {INTERVAL_SEC} -f \"$d/trace.csv\" >/dev/null 2>&1 </dev/null & "
        f"echo PID=$!"
    )
    try:
        # Single attempt. `start_cmd` is NOT idempotent: a second run creates a
        # second mktemp directory and a second sampler whose pid we never see,
        # so nothing can kill it and it lives to the 140-minute hard stop. The
        # fetch below is safely re-runnable and keeps both attempts.
        out = _remote(instance, start_cmd, attempts=1)
        # Only two prefixes are read, and both are validated. There is
        # deliberately no free-form field: an earlier revision assigned every
        # other line to `gpu_name` and published it in the sidecar, so anything
        # the remote emitted in that position went straight into an uploaded
        # artifact. ROW_RE guarded the CSV and nothing guarded the sidecar.
        if out:
            for line in out.splitlines():
                if line.startswith("PID="):
                    # About to be interpolated into a shell command, so a bare
                    # positive integer or nothing: `PID=1; rm -rf ...` must not
                    # become a cleanup command.
                    candidate = line.split("=", 1)[1].strip()
                    pid = candidate if PID_RE.match(candidate) else None
                elif line.startswith("DIR="):
                    candidate = line.split("=", 1)[1].strip()
                    remote_dir = candidate if DIR_RE.match(candidate) else None
        if pid and remote_dir:
            remote_csv = f"{remote_dir}/trace.csv"
            print(f"[gpu-trace] sampling {instance} every {INTERVAL_SEC}s "
                  f"(pid {pid}, hard stop {hard_stop}s)", flush=True)
        else:
            print(f"[gpu-trace] could not start on {instance}; leg continues",
                  flush=True)
    except Exception as exc:  # noqa: BLE001 - telemetry must never fail a leg
        print(f"[gpu-trace] start failed ({exc!r}); leg continues", flush=True)

    try:
        yield
    finally:
        # Runs on success, failure, harbor timeout and KeyboardInterrupt. A
        # SIGKILL skips it, which is what the remote `timeout` covers.
        with contextlib.suppress(Exception):
            _finish(instance, remote_csv, remote_dir, pid, results_root, token,
                    run_id=run_id, skill=skill, spec_stem=spec_stem,
                    platform=platform, step=step, chain=chain,
                    declared_gpu_count=declared_gpu_count,
                    started_at=started_at)


def _finish(
    instance: str,
    remote_csv: str | None,
    remote_dir: str | None,
    pid: str | None,
    results_root: Path,
    token: str,
    **meta,
) -> None:
    """Stop the sampler, pull the rows back, write CSV + sidecar. Best effort."""
    # `timeout` forwards SIGTERM to nvidia-smi, so killing its pid is enough.
    # The `rm` matters: `_reset_docker_runtime` does not clear /tmp, and an
    # unbounded pile of traces on a box that has been up six weeks is litter.
    # The kill and the fetch were coupled to one guard, but only the fetch needs
    # the directory. A reply where PID= validated and DIR= did not left a running
    # sampler that nothing would ever kill, reported as a benign non-start.
    if pid and not remote_dir:
        # Identity still has to be established here, and this branch cannot use
        # the nonce because the nonce IS the directory that failed to validate.
        # A bare `kill <number>` is the exact defect the guard below documents:
        # the sampler can exit early, the OS reuses the pid, and cleanup then
        # signals whatever inherited it. Verified there with an unrelated
        # `sleep 60`; nothing about this branch makes that safer.
        # The literal `vss-gputrace` prefix is in the sampler's argv (it owns
        # the file via `-f "$d/trace.csv"`), so match on that: weaker than the
        # exact nonce -- it cannot tell this leg's sampler from another's --
        # but it is the difference between "some gputrace sampler" and "any
        # process on the box". Both samplers are bounded by their own timeout,
        # so the weaker match costs at most a slightly early stop.
        # `-ww` for the reason spelled out at the fetch-path guard below, and
        # this site is worse off: `vss-gputrace` occurs in the sampler's argv
        # only inside `-f "$d/trace.csv"`, which is the LAST thing on a 204-char
        # command line, so any width limit at all discards it.
        _remote(instance,
                f"ps -ww -o args= -p {pid} 2>/dev/null | grep -qF vss-gputrace "
                f"&& kill {pid} 2>/dev/null; true", attempts=1)
        print(f"[gpu-trace] tried to stop sampler {pid} on {instance} "
              f"but have no trace path", flush=True)
        return
    if not remote_dir or not remote_csv:
        print(f"[gpu-trace] no remote trace directory for {instance}", flush=True)
        return
    # `pid` is PID_RE-validated at capture so it cannot carry shell syntax, but
    # syntax is not identity: a sampler that exited early frees its pid, the OS
    # reuses it, and `kill <number>` then hits an unrelated process on a shared
    # box. Verified: handing back the pid of an unrelated `sleep 60` terminated
    # it. So the pid must still LOOK like our sampler before it is signalled.
    # Match this invocation's nonce directory, not merely "some nvidia-smi".
    # These boxes run nvidia-smi constantly -- deploy scripts, health checks,
    # containers -- so a recycled pid that happens to be any nvidia-smi would be
    # killed, possibly one belonging to another leg. The nonce is in argv now
    # that the sampler uses `-f`, which makes this exact.
    # -F, not a bare pattern: DIR_RE permits `.`, which is a BRE wildcard, so
    # `grep -q /tmp/vss-gputrace.ABCD1234` also matches `/tmp/vss-gputraceXABCD1234`.
    # A fixed-string match makes the nonce mean what it looks like it means.
    #
    # `-ww`, and it is load-bearing rather than tidy. `ps` truncates `args` to
    # the display width, and the sampler's command line is 204 characters with
    # the nonce starting at column 169. Any width under that returns a prefix
    # that CANNOT contain the nonce, so `grep` reports no match, `&&` skips the
    # kill, and the sampler runs to its 2h20m hard stop on a box that has already
    # been handed to the next leg -- a guard that is present, correct-looking and
    # inert. Two real ways to land under 169 columns, both measured on
    # procps-ng 3.3.17:
    #   * COLUMNS is set in the environment (COLUMNS=80 and =100 both discard it);
    #   * stdout is a pty whose window size was never set, which defaults to
    #     exactly 80x24 -- and `brev exec`/`ssh` allocate precisely that.
    # Note it is NOT simply "no tty": with no tty anywhere and COLUMNS unset,
    # procps prints unlimited width, which is why this can look green locally.
    # `-ww` removes the limit unconditionally and is immune to all of them.
    # A single `-w` is NOT enough and is the tempting half-fix: it widens to 132
    # columns, still short of 169, so the guard stays inert while looking
    # repaired. Measured at COLUMNS=80: bare=80 chars, -w=132, -ww=207.
    kill = (f"ps -ww -o args= -p {pid} 2>/dev/null | grep -qF -- {remote_dir} "
            f"&& kill {pid} 2>/dev/null; ") if pid else ""
    # `head -c` bounds the remote side; _read_capped bounds ours. Both are
    # needed: the remote cap cannot bound brev's own stdout.
    out = _remote(instance,
                  f"{kill}sleep 1; head -c {MAX_FETCH_BYTES} {remote_csv} 2>/dev/null; "
                  f"rm -rf {remote_dir}")
    # Structural validation, not a comma count. `ln.count(",") >= 6` publishes
    # whatever the remote `cat` returned into a public artifact, and the remote
    # path is a predictable same-user file in /tmp. A row now has to LOOK like
    # nvidia-smi output -- an nvidia-smi timestamp then exactly six numeric (or
    # [N/A]) fields -- or it is dropped. This is what makes the allowlist real
    # rather than nominal.
    rows = [ln for ln in (out or "").splitlines() if ROW_RE.match(ln.strip())]
    dropped = len([ln for ln in (out or "").splitlines() if ln.strip()]) - len(rows)
    if dropped > 0:
        # Loud, because a persistent nonzero here means the remote file is not
        # what we wrote and the fetch path needs looking at, not silencing.
        print(f"[gpu-trace] dropped {dropped} malformed row(s) from {instance}", flush=True)
    if not rows:
        print(f"[gpu-trace] no samples returned from {instance}", flush=True)
        return

    dest = results_root / "gputrace"
    dest.mkdir(parents=True, exist_ok=True)
    (dest / f"{token}.csv").write_text(
        CSV_HEADER + "\n" + "\n".join(rows) + "\n", encoding="utf-8"
    )

    # Everything the collector needs to answer "declared versus observed"
    # without re-deriving it from paths. `instance` is the only fleet-side
    # identifier emitted; no IP, no hostname, no credentials.
    sidecar = {
        "schema": 1,
        "instance": instance,

        "declared_gpu_count": meta.get("declared_gpu_count"),
        "run_id": meta.get("run_id"),
        "skill": meta.get("skill"),
        "spec_stem": meta.get("spec_stem"),
        "platform": meta.get("platform"),
        "step": meta.get("step"),
        "interval_sec": INTERVAL_SEC,
        "started_at": meta.get("started_at"),
        "finished_at": time.time(),
        "samples": len(rows),
    }
    (dest / f"{token}.json").write_text(
        json.dumps(sidecar, indent=2) + "\n", encoding="utf-8"
    )
    span = sidecar["finished_at"] - (meta.get("started_at") or sidecar["finished_at"])
    print(f"[gpu-trace] {len(rows)} samples over {span / 60:.1f} min "
          f"-> {dest / (token + '.csv')}", flush=True)
