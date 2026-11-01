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

"""Sync shared incident contract files to vss-agent vendored directory.

`contracts/` is the single source of truth for the incident contract.
The agent keeps a vendored copy in `services/agent/src/vss_agents/incident_contract/`
because it is deployed from `services/agent`.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

CONTRACTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = Path(__file__).resolve().parents[5]
AGENT_CONTRACT_DIR = REPO_ROOT / "services/agent/src/vss_agents/incident_contract"

CONTRACT_FILES: list[str] = [
    "incident_report.schema.json",
    "incident_extraction_prompt.txt",
    "report_generation_prompt.txt",
    "VERSION",
    "inference.json",
]


def agent_prompt_files(source_dir: Path = CONTRACTS_DIR) -> list[str]:
    """The prompt registry and every registered prompt the agent uses (``used_by`` names it), as relative paths."""
    registry = json.loads((source_dir / "prompts" / "registry.json").read_text(encoding="utf-8"))
    files = [f"prompts/{entry['file']}" for entry in registry["prompts"] if "agent" in entry["used_by"]]
    return ["prompts/registry.json", *sorted(files)]


VENDORED_FILES: list[str] = CONTRACT_FILES + agent_prompt_files()


def check_file(name: str, source_dir: Path = CONTRACTS_DIR, target_dir: Path = AGENT_CONTRACT_DIR) -> bool:
    """Return True if target_dir / name exists and matches source_dir / name byte-for-byte."""
    src = source_dir / name
    dst = target_dir / name
    if not src.exists() or not dst.exists():
        return False
    return src.read_bytes() == dst.read_bytes()


def check_drift(source_dir: Path = CONTRACTS_DIR, target_dir: Path = AGENT_CONTRACT_DIR) -> list[str]:
    """Return the vendored files that differ between source_dir and target_dir (missing ones included)."""
    names = CONTRACT_FILES + agent_prompt_files(source_dir)
    return [name for name in names if not check_file(name, source_dir, target_dir)]


def sync(source_dir: Path = CONTRACTS_DIR, target_dir: Path = AGENT_CONTRACT_DIR) -> list[str]:
    """Copy all VENDORED_FILES from source_dir to target_dir. Return copied filenames."""
    target_dir.mkdir(parents=True, exist_ok=True)
    synced: list[str] = []
    for name in CONTRACT_FILES + agent_prompt_files(source_dir):
        src = source_dir / name
        dst = target_dir / name
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(src.read_bytes())
        synced.append(name)
    return synced


def main() -> int:
    parser = argparse.ArgumentParser(description="Sync shared incident contract files to the vendored agent directory.")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Check for drift without modifying files. Exits non-zero and prints differing files if drift is detected.",
    )
    parser.add_argument(
        "--source",
        type=Path,
        default=CONTRACTS_DIR,
        help=f"Source contract directory (default: {CONTRACTS_DIR})",
    )
    parser.add_argument(
        "--target",
        type=Path,
        default=AGENT_CONTRACT_DIR,
        help=f"Target agent contract directory (default: {AGENT_CONTRACT_DIR})",
    )
    args = parser.parse_args()

    if args.check:
        differing = check_drift(args.source, args.target)
        if differing:
            for name in differing:
                print(name)
            return 1
        return 0

    synced = sync(args.source, args.target)
    print(f"Synced {len(synced)} contract files from {args.source} to {args.target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
