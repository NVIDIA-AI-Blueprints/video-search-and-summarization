# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Cross-component contract checks for the archive-search skill and CLI."""

from __future__ import annotations

import json
from pathlib import Path

from lib.cli.search import _parse_args

REPOSITORY_ROOT = Path(__file__).resolve().parents[5]
SKILL_ROOT = REPOSITORY_ROOT / "skills" / "vss-search-archive"
REMOVED_FLAGS = (
    "--use-critic",
    "--no-use-critic",
    "--vlm-media-mode",
    "--vst-clip-enable-audio",
    "--search-max-iterations",
)


def test_skill_and_eval_do_not_require_removed_cli_contract() -> None:
    contract_files = [SKILL_ROOT / "SKILL.md", *sorted((SKILL_ROOT / "references").glob("*.md"))]
    contract_files.extend(sorted((SKILL_ROOT / "evals").glob("*.json")))

    for path in contract_files:
        text = path.read_text(encoding="utf-8")
        for flag in REMOVED_FLAGS:
            assert flag not in text, f"{path.relative_to(REPOSITORY_ROOT)} still requires removed flag {flag}"


def test_eval_is_valid_json() -> None:
    for path in sorted((SKILL_ROOT / "evals").glob("*.json")):
        json.loads(path.read_text(encoding="utf-8"))


def test_documented_run_flags_are_accepted_by_run_parser() -> None:
    args = _parse_args(
        [
            "--deployment",
            "docker",
            "--profile",
            "search",
            "--query",
            "person in a white jacket climbing a ladder",
            "--attribute",
            "white jacket",
            "--search-mode",
            "fusion",
            "--video-source",
            "sample-warehouse-ladder",
            "--top-k",
            "10",
        ],
        operation="run",
    )
    assert args.query
    assert args.attributes == ["white jacket"]
