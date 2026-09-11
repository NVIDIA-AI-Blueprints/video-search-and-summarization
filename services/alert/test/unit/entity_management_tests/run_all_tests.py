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

"""
Test runner for the entity management tests.

Delegates to pytest over this directory so collection follows the files that
are actually here. The previous version enumerated modules and test functions
by hand, which drifted: it named two modules that no longer exist, missed two
that do, and called a test that had been removed.
"""
import subprocess
import sys
import time
from pathlib import Path

TEST_DIR = Path(__file__).parent
# conftest.py at the service root puts src/ on sys.path.
SERVICE_ROOT = TEST_DIR.parents[2]


def main() -> None:
    """Run the entity management tests and report duration."""
    argv = sys.argv[1:] or ["-v"]
    start = time.time()
    result = subprocess.run(
        [sys.executable, "-m", "pytest", str(TEST_DIR), *argv],
        cwd=SERVICE_ROOT,
    )
    print(f"\nDuration: {time.time() - start:.2f}s")
    sys.exit(result.returncode)


if __name__ == "__main__":
    main()
