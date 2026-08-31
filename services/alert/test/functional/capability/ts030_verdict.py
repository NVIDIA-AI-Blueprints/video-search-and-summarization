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

"""Decide the rate-ramp verdict and print the line the suite reports.

Kept in a file rather than inlined into the runner: as a `python3 -c "..."`
argument inside a double-quoted shell string, every Python double quote —
including the ones in docstrings, comments and nested f-strings — silently
terminated the shell string, and the gate died at parse time before reading a
single measurement.

Usage:
  ts030_verdict.py RESULTS_DIR SIM_LIMIT PROCESSES RATES SMEAN MMEAN SCPU MCPU

The last five arguments are comma-separated lists, one entry per ramp point.
Exits 0 when the ramp supports the claim, 1 otherwise; either way the reason is
printed.
"""

import os
import sys


def _floats(raw):
    return [float(value) for value in raw.split(",") if value != ""]


def simulator_peaks(results_dir, processes, rate):
    """Peak CPU per simulator at one offered rate, across both variants."""
    worst = {}
    for variant in (1, processes):
        path = os.path.join(results_dir, f"sims_P{variant}R{rate}.txt")
        if not os.path.exists(path):
            continue
        with open(path) as handle:
            for line in handle:
                parts = line.split()
                if len(parts) < 3:
                    continue
                try:
                    peak = float(parts[2])
                except ValueError:
                    continue
                worst[parts[0]] = max(worst.get(parts[0], 0.0), peak)
    return worst


def _format_peaks(peaks):
    return ", ".join(f"{name} {value:.0f}%" for name, value in sorted(peaks.items()))


def main():
    results_dir = sys.argv[1]
    sim_limit = float(sys.argv[2])
    processes = int(sys.argv[3])
    rates = [int(float(value)) for value in sys.argv[4].split(",") if value != ""]
    s_mean, m_mean, s_cpu, m_cpu = (_floats(arg) for arg in sys.argv[5:9])

    s_base, m_base = s_mean[0], m_mean[0]

    # Where does one process first buckle?
    break_index = next((i for i, v in enumerate(s_mean) if v >= s_base * 1.5), None)
    if break_index is None:
        print(
            "single process never inflated: it is already saturated at the first "
            "rate, so there is no flat baseline to grow from. LOWER the first "
            "entry in RAMP_RATES, do not raise it."
        )
        return 1

    break_rate = rates[break_index]

    # Only the break-rate comparison is corrupted by a saturated simulator. At
    # higher rates a throttled simulator makes Alert Bridge use *less* CPU, so
    # the crosses-one-core half stays conservative and cannot pass falsely.
    at_break = simulator_peaks(results_dir, processes, break_rate)
    blocked = [
        f"{name} {peak:.1f}%" for name, peak in sorted(at_break.items()) if peak >= sim_limit
    ]
    if blocked:
        print(
            f"simulator saturated at the break rate ({break_rate}/s): "
            f"{', '.join(blocked)} of one core. The latency comparison there "
            "describes the harness, not Alert Bridge. Give the simulators more "
            "capacity, or start the ramp lower."
        )
        return 1

    ok = (
        max(s_cpu) >= 85.0                        # the one-core ceiling is real
        and m_mean[break_index] <= m_base * 1.3   # N processes unaffected where 1 broke
        and m_mean[break_index] < s_mean[break_index]   # ...and faster there
        and max(m_cpu) > 100.0                    # N processes do cross one core
    )

    print(
        f"break_rate={break_rate}/s "
        f"1p={s_mean[break_index]}s {s_cpu[break_index]}% "
        f"{processes}p={m_mean[break_index]}s {m_cpu[break_index]}% "
        f"| max cpu 1p={max(s_cpu)}% {processes}p={max(m_cpu)}% "
        f"| sims@top {_format_peaks(simulator_peaks(results_dir, processes, rates[-1]))}"
    )
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
