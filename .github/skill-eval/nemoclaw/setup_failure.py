#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Emit secret-safe, fixed-category NemoClaw setup failure diagnostics."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Sequence

SCHEMA_VERSION = 1
MAX_INPUT_BYTES = 4 * 1024 * 1024

_CATEGORY_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "trusted_lsof_unavailable",
        re.compile(
            r"(?:lsof is unavailable in the trusted system paths|"
            r"Cannot install trusted lsof for scoped gateway recovery)",
            re.IGNORECASE,
        ),
    ),
    (
        "gateway_release_failed",
        re.compile(
            r"(?:Scoped NemoClaw gateway release failed|"
            r"gateway port [0-9]+ is still busy after scoped release)",
            re.IGNORECASE,
        ),
    ),
    (
        "gateway_lifecycle_authority_refused",
        re.compile(
            r"lifecycle authority refused scoped release",
            re.IGNORECASE,
        ),
    ),
    (
        "legacy_state_conflict",
        re.compile(
            r"Cannot safely migrate legacy NemoClaw state for this gateway "
            r"port: onboard-session\.json conflicts with its sandbox registry row",
            re.IGNORECASE,
        ),
    ),
    (
        "state_owner_mismatch",
        re.compile(
            r"Refusing non-owner-only DGX Station Express resume directory",
            re.IGNORECASE,
        ),
    ),
    (
        "sandbox_container_restarting",
        re.compile(
            r"reason=ContainerRestarting Container is restarting after a failure",
            re.IGNORECASE,
        ),
    ),
    (
        "sandbox_not_ready",
        re.compile(
            r"Sandbox '[A-Za-z0-9._-]+' was created but did not become ready "
            r"within [0-9]+s\.",
            re.IGNORECASE,
        ),
    ),
    (
        "cli_version_mismatch",
        re.compile(
            r"expected nemoclaw v[0-9]+\.[0-9]+\.[0-9]+, "
            r"found nemoclaw v[0-9]+\.[0-9]+\.[0-9]+",
            re.IGNORECASE,
        ),
    ),
    (
        "nemoclaw_install_failed",
        re.compile(r"NemoClaw install failed", re.IGNORECASE),
    ),
    (
        "nemoclaw_onboard_failed",
        re.compile(r"nemoclaw onboard failed", re.IGNORECASE),
    ),
    (
        "notebook_execution_failed",
        re.compile(r"(?:CellExecutionError|Notebook execution)", re.IGNORECASE),
    ),
    (
        "readiness_failed",
        re.compile(
            r"(?:NemoClaw readiness failed|readiness probe failed)",
            re.IGNORECASE,
        ),
    ),
)


def classify_setup_failure(text: str, return_code: int) -> dict[str, object]:
    """Return only fixed categories; never copy raw setup text."""
    categories: list[str] = []
    if return_code in (124, 137):
        categories.append("setup_timeout")
    for category, pattern in _CATEGORY_PATTERNS:
        if pattern.search(text) and category not in categories:
            categories.append(category)
    if not categories:
        categories.append("unclassified_setup_failure")
    return {
        "schema_version": SCHEMA_VERSION,
        "return_code": int(return_code),
        "categories": categories,
    }


def format_setup_failure(diagnostic: dict[str, object]) -> str:
    """Format the validated category list for one exception line."""
    categories = diagnostic.get("categories")
    if not isinstance(categories, list) or not all(
        isinstance(category, str) and re.fullmatch(r"[a-z0-9_]+", category)
        for category in categories
    ):
        return "unclassified_setup_failure"
    return ",".join(categories) or "unclassified_setup_failure"


def _read_tail(path: Path, max_bytes: int = MAX_INPUT_BYTES) -> str:
    with path.open("rb") as handle:
        handle.seek(0, 2)
        size = handle.tell()
        handle.seek(max(0, size - max_bytes))
        return handle.read(max_bytes).decode("utf-8", errors="replace")


def _write_json(path: Path, value: dict[str, object]) -> None:
    if path.is_symlink():
        raise RuntimeError(f"refusing symlinked setup diagnostic output: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    path.chmod(0o600)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--return-code", type=int, required=True)
    args = parser.parse_args(argv)
    diagnostic = classify_setup_failure(
        _read_tail(args.input),
        args.return_code,
    )
    _write_json(args.output, diagnostic)
    print(format_setup_failure(diagnostic))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
