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

"""Run manifests: what a run was, pinned by hashes, so it can be audited and re-scored later.

A manifest records the config (and its SHA-256), every input it depends on (dataset versions and hashes, prompt
ids and hashes, the contract version), the code (git SHA and whether the tree was dirty) and the V2 version.
``input_drift`` compares a recorded manifest with the inputs as they are now; a run is never resumed or
re-scored across drift without saying so.
"""

from __future__ import annotations

import subprocess
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from evalv2 import EVALV2_VERSION
from evalv2.core.hashing import sha256_json

MANIFEST_VERSION = "evalv2-run-manifest-v1"


def git_info(path: Path) -> dict[str, Any]:
    """The checkout's commit and whether it has uncommitted changes; ``None`` values outside a git checkout."""
    def run(*args: str) -> str | None:
        try:
            done = subprocess.run(["git", *args], cwd=path, capture_output=True, text=True, check=False, timeout=30)
        except (OSError, subprocess.TimeoutExpired):
            return None
        return done.stdout.strip() if done.returncode == 0 else None

    sha = run("rev-parse", "HEAD")
    status = run("status", "--porcelain", "--untracked-files=no")
    return {"git_sha": sha, "dirty": None if status is None else bool(status)}


def build_run_manifest(*, run_id: str, config: Mapping[str, Any], inputs: Mapping[str, str], code_dir: Path,
                       extra: Mapping[str, Any] | None = None) -> dict[str, Any]:
    return {
        "manifest_version": MANIFEST_VERSION,
        "run_id": run_id,
        "created_at": datetime.now(UTC).isoformat(),
        "config_sha256": sha256_json(dict(config)),
        "config": dict(config),
        "inputs": dict(sorted(inputs.items())),
        "code": {**git_info(code_dir), "evalv2_version": EVALV2_VERSION},
        **(dict(extra) if extra else {}),
    }


def input_drift(recorded: Mapping[str, str], current: Mapping[str, str]) -> list[str]:
    """Differences between the inputs a manifest recorded and the inputs as they are now."""
    problems = []
    for name in sorted(set(recorded) | set(current)):
        if name not in current:
            problems.append(f"input {name!r} recorded but no longer present")
        elif name not in recorded:
            problems.append(f"input {name!r} is new since the manifest was written")
        elif recorded[name] != current[name]:
            problems.append(f"input {name!r} changed: {recorded[name][:12]}... -> {current[name][:12]}...")
    return problems
