#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Apply configured stage deadlines to entire process groups, without retries."""

import os
import signal
import subprocess
import sys


def main():
    seconds, script, declarations = sys.argv[1:]
    command = declarations + '\nsource "$1"'
    process = subprocess.Popen(
        ["bash", "-c", command, "stage", script], start_new_session=True
    )
    try:
        return process.wait(timeout=float(seconds))
    except subprocess.TimeoutExpired:
        print(f"Stage {script} exceeded {seconds} seconds", file=sys.stderr)
        os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
        return 124


if __name__ == "__main__":
    sys.exit(main())
