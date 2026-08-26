#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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
"""Inventory the skill-eval corpus: how many specs, cells and checks exist.

The corpus size is quoted from memory in several places and the numbers
disagree. This makes it a measurement with one definition.

The spec/cell/check counts are exact. The scope buckets are NOT: they match on
mentions, not requirements, so "no docker compose is executed" counts as HOST
while a live container-state assertion with no command in it counts as PROSE.
Treat them as a triage hint for finding checks worth reading, never as a
measurement of what needs the deploy host -- that needs per-check metadata,
which this does not attempt.

Counts also cannot detect a check MOVING between cells, and reward is per cell
(``passed / len(checks)``, verifiers/generic_judge.py), so a move changes scores
with every total unchanged. Pinning ordered identities is the follow-up change;
until it lands this is scaffolding, not a parity baseline.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from benchmark.spec import spec_kind  # noqa: E402
from plan_matrix import EXCLUDED_SPEC_NAMES, REPO_ROOT, specs_for_skill  # noqa: E402

# Scope taxonomy, most-specific first. First match wins, so ordering is part of
# the definition. HOST precedes FILE precedes NET: a check that execs into a
# container to stat a file is bounded by the host requirement, not the file one.
HOST_PATTERNS = (
    r"\bdocker\s+(ps|compose|inspect|logs|exec|images|volume|cp|run|pull|stop|kill|rm|container)\b",
    r"\bnvidia-smi\b",
    r"\b(systemctl|journalctl)\b",
    r"\bon\s+the\s+(deploy\s+)?host\b",
)
FILE_PATTERNS = (
    r"\b(cat|ls|stat|find|file)\s+[/~$]",
    r"\btest\s+-[efd]\s+[/~$]",
    r"\bfile\s+exists\b",
    r"\b(under|at)\s+[/~]\S+",
)
NET_PATTERNS = (
    r"\bcurl\b",
    r"https?://",
    r"\b(GET|POST|PUT|DELETE|PATCH)\s+/",
    r"\bHTTP\s+\d{3}\b",
)

_HOST_RE = re.compile("|".join(HOST_PATTERNS), re.IGNORECASE)
_FILE_RE = re.compile("|".join(FILE_PATTERNS), re.IGNORECASE)
_NET_RE = re.compile("|".join(NET_PATTERNS), re.IGNORECASE)

SCOPES = ("HOST", "FILE", "NET", "PROSE")


class SpecError(ValueError):
    """A spec is malformed. Never silently degrade — a bad corpus must be loud,
    because every downstream gate sources its numbers from this count."""


def classify_check(text: str) -> str:
    """Primary execution scope a single check needs."""
    if _HOST_RE.search(text):
        return "HOST"
    if _FILE_RE.search(text):
        return "FILE"
    if _NET_RE.search(text):
        return "NET"
    return "PROSE"


def all_skills() -> list[str]:
    """Every skill directory that ships eval specs.

    Enumerated from the filesystem, NOT from SKILL.md: manual `*` dispatch
    enumerates directories directly, so a spec in a directory without a
    SKILL.md is dispatchable. Deriving from SKILL.md would let such a spec run
    while being absent from this baseline.
    """
    root = REPO_ROOT / "skills"
    if not root.is_dir():
        raise SpecError(f"no skills directory at {root}")
    return sorted({
        directory.parent.name
        for directory in root.rglob("*")
        if directory.is_dir() and directory.name in {"evals", "eval"}
    })


def scan_spec(rel_path: str) -> dict:
    """Inventory one spec: per-cell check counts and scope buckets."""
    try:
        data = json.loads((REPO_ROOT / rel_path).read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SpecError(f"{rel_path}: invalid JSON: {exc}") from exc
    except (OSError, UnicodeError) as exc:
        # Same loud path as bad JSON, else "never silently degrade" holds for
        # exactly one failure mode.
        raise SpecError(f"{rel_path}: unreadable: {exc}") from exc
    if not isinstance(data, dict):
        raise SpecError(f"{rel_path}: top level must be an object")

    try:
        kind = spec_kind(data)
    except ValueError as exc:
        raise SpecError(f"{rel_path}: {exc}") from exc
    if kind == "dataset":
        return {
            "spec": rel_path,
            "kind": "dataset",
            "cells": 0,
            "checks": 0,
            "scopes": dict.fromkeys(SCOPES, 0),
            "cells_with_host": 0,
            "cell_detail": [],
        }

    expects = data.get("expects")
    if not isinstance(expects, list):
        raise SpecError(f"{rel_path}: 'expects' must be a list")

    per_scope = dict.fromkeys(SCOPES, 0)
    cells: list[dict] = []

    for idx, step in enumerate(expects):
        if not isinstance(step, dict):
            raise SpecError(f"{rel_path}[{idx}]: cell must be an object")
        checks = step.get("checks")
        if not isinstance(checks, list):
            raise SpecError(f"{rel_path}[{idx}]: 'checks' must be a list")

        n, has_host = 0, False
        for cidx, check in enumerate(checks):
            if not isinstance(check, str):
                raise SpecError(f"{rel_path}[{idx}].checks[{cidx}]: must be a string")
            scope = classify_check(check)
            per_scope[scope] += 1
            has_host = has_host or scope == "HOST"
            n += 1

        cells.append({"index": idx, "check_count": n, "has_host": has_host})

    return {
        "spec": rel_path,
        "kind": "expects",
        "cells": len(cells),
        "checks": sum(c["check_count"] for c in cells),
        "scopes": per_scope,
        "cells_with_host": sum(1 for c in cells if c["has_host"]),
        "cell_detail": cells,
    }


def scan() -> dict:
    """Inventory the whole corpus."""
    rows = [
        scan_spec(rel)
        for skill in all_skills()
        for rel, _eval_dir, _stem in specs_for_skill(skill)
    ]

    totals = dict.fromkeys(SCOPES, 0)
    for row in rows:
        for scope in SCOPES:
            totals[scope] += row["scopes"][scope]

    return {
        "excluded_spec_names": sorted(EXCLUDED_SPEC_NAMES),
        "specs": len(rows),
        "cells": sum(r["cells"] for r in rows),
        "checks": sum(r["checks"] for r in rows),
        "scopes": totals,
        "cells_with_host": sum(r["cells_with_host"] for r in rows),
        "by_spec": rows,
    }


def format_summary(result: dict) -> str:
    hint = " ".join(f"{s}={result['scopes'][s]}" for s in SCOPES)
    return (
        f"specs={result['specs']} cells={result['cells']} checks={result['checks']}\n"
        f"lexical scope hint (mentions, NOT an execution-scope measurement): {hint}"
        f" / cells mentioning HOST: {result['cells_with_host']}"
    )


def main() -> int:
    ap = argparse.ArgumentParser(description="Inventory the skill-eval corpus.")
    ap.add_argument("--json", action="store_true", help="aggregate counts as JSON")
    args = ap.parse_args()

    try:
        result = scan()
    except SpecError as exc:
        print(f"FATAL: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps({k: v for k, v in result.items() if k != "by_spec"},
                         indent=2, sort_keys=True))
    else:
        print(format_summary(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
