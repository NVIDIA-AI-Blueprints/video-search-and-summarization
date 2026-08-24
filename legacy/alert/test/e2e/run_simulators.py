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

"""Utility to launch all local simulators used by E2E tests.

Usage:
    python -m test.e2e.run_simulators

The script starts Elastic, NIM, VST, and VSS simulators using the same
helpers as the pytest fixtures, then waits until you press Enter (or
Ctrl+C). When it exits, all child processes are terminated cleanly.
"""

from __future__ import annotations

import argparse
import sys
import textwrap
from contextlib import ExitStack

from .kafka_incident.fixtures import start_simulators


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Launch Elastic/NIM/VST/VSS simulators for manual testing",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent(
            """
            Examples:
              python -m test.e2e.run_simulators
              python test/e2e/run_simulators.py
            """
        ),
    )
    parser.add_argument(
        "--use-real-endpoints",
        action="store_true",
        help="Skip launching simulators (mirrors pytest flag)",
    )
    args = parser.parse_args(argv)

    with ExitStack() as stack:
        handles = stack.enter_context(start_simulators(args.use_real_endpoints))
        names = ", ".join(handle.name for handle in handles) or "(none)"
        print(f"Simulators running: {names}")
        print("Press Enter or Ctrl+C to stop...")
        try:
            input()
        except KeyboardInterrupt:
            print("\nStopping simulators...")

    return 0


if __name__ == "__main__":
    sys.exit(main())


