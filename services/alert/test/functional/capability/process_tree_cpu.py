#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

"""Sample aggregate CPU utilisation of every process matching a command line.

``ps %cpu`` reports a lifetime average, which hides the ramp the scaling tests
care about, so utime+stime deltas are read straight from /proc instead. 100%
means one fully busy core.

Interpreter startup and child construction burn CPU that has nothing to do with
steady-state throughput, so SKIP discards the first seconds of samples —
without it the peak reported at the *lowest* offered rate is a boot spike.

PATTERN may be a comma-separated list. With one pattern the output is a single
"<avg> <max> <samples>" line; with several, one "<pattern> <avg> <max>
<samples>" line each, so a run can watch the process under test and the
simulators it depends on in the same pass.

Usage: process_tree_cpu.py PATTERN[,PATTERN...] INTERVAL DURATION [SKIP]
"""

import os
import sys
import time

CLOCK_TICKS = os.sysconf("SC_CLK_TCK")


SELF = os.path.basename(__file__)


def matching_pids(pattern):
    """Pids whose command line contains ``pattern``, excluding this sampler.

    The sampler is invoked with the pattern as an argument, so its own command
    line matches it. Left in, it charged its own CPU to whatever it was
    measuring — small (about 0.8% of a core) but attributed to the wrong
    process.
    """
    pids = []
    own_pid = os.getpid()
    for entry in os.listdir("/proc"):
        if not entry.isdigit() or int(entry) == own_pid:
            continue
        try:
            with open(os.path.join("/proc", entry, "cmdline"), "rb") as handle:
                cmdline = handle.read().decode("utf-8", "replace").replace("\0", " ")
        except OSError:
            continue
        if pattern in cmdline and SELF not in cmdline:
            pids.append(int(entry))

    # Descendants count too, or this measures a supervisor and calls it the
    # workload. Children inherited the parent's argv under fork and matched
    # the pattern themselves; spawned children are re-exec'd as
    # "python -c from multiprocessing.spawn import spawn_main ..." and match
    # nothing, which reports a busy N-process instance as ~0% CPU.
    tree = _children_by_parent()
    seen = set(pids)
    queue = list(pids)
    while queue:
        for child in tree.get(queue.pop(), ()):
            if child not in seen:
                seen.add(child)
                queue.append(child)
    seen.discard(os.getpid())
    return sorted(seen)


def _children_by_parent():
    """ppid -> [pid] for every live process, read from /proc/<pid>/stat."""
    tree = {}
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        try:
            # The comm field can contain spaces and parentheses, so split on
            # the last ") " rather than tokenising the whole line.
            with open(f"/proc/{entry}/stat") as handle:
                fields = handle.read().rsplit(") ", 1)[1].split()
            ppid = int(fields[1])
        except (OSError, IndexError, ValueError):
            continue
        tree.setdefault(ppid, []).append(int(entry))
    return tree


def cpu_ticks(pid):
    try:
        with open(f"/proc/{pid}/stat") as handle:
            fields = handle.read().rsplit(") ", 1)[1].split()
        return int(fields[11]) + int(fields[12])
    except (OSError, IndexError, ValueError):
        return None


def snapshot(pattern):
    result = {}
    for pid in matching_pids(pattern):
        ticks = cpu_ticks(pid)
        if ticks is not None:
            result[pid] = ticks
    return result


def main():
    patterns = [p for p in sys.argv[1].split(",") if p]
    interval = float(sys.argv[2])
    duration = float(sys.argv[3])
    skip = float(sys.argv[4]) if len(sys.argv) > 4 else 0.0

    previous = {p: snapshot(p) for p in patterns}
    previous_at = time.monotonic()
    samples = {p: [] for p in patterns}
    started_at = previous_at
    end = previous_at + duration

    while time.monotonic() < end:
        time.sleep(interval)
        now = time.monotonic()
        elapsed = now - previous_at
        for pattern in patterns:
            current = snapshot(pattern)
            # A pid absent from the previous snapshot is a freshly restarted
            # child; counting its lifetime ticks as one interval's work would
            # spike the sample, so only carried-over pids contribute.
            delta = sum(ticks - previous[pattern][pid]
                        for pid, ticks in current.items() if pid in previous[pattern])
            if elapsed > 0 and now - started_at >= skip:
                samples[pattern].append(100.0 * delta / CLOCK_TICKS / elapsed)
            previous[pattern] = current
        previous_at = now

    def summarise(values):
        if not values:
            return "0.0 0.0 0"
        return f"{sum(values) / len(values):.1f} {max(values):.1f} {len(values)}"

    if len(patterns) == 1:
        print(summarise(samples[patterns[0]]))
        return
    for pattern in patterns:
        print(f"{pattern} {summarise(samples[pattern])}")


if __name__ == "__main__":
    main()
