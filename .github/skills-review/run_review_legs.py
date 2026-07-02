#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Run every skills-review leg in-process with bounded concurrency.

Replaces the old GitHub Actions review matrix. The per-(skill x paradigm) legs
are pure LLM API calls (no GPU/box lock) that each write an independent
review-<skill>__<paradigm>.json into REVIEW_OUT_DIR, so there is nothing to gain
from GitHub-level leg isolation — running them here as N bounded subprocesses
keeps the whole workflow to a SINGLE advisory check instead of
2 + (changed_skills x 6). Concurrency here replaces the matrix `max-parallel`.

Each leg runs review_agent.py with EVAL_SKILL / EVAL_PARADIGM set for that leg
and every other variable inherited (PR_BASE, REVIEW_OUT_DIR, ANTHROPIC_*, GH_*).
review_agent.py's git access is read-only (`git diff base...HEAD`), so parallel
legs share one checkout safely. A leg that fails or times out logs a GitHub
`::warning::` and is skipped; this driver always exits 0 (advisory — a review
leg must never fail the merge, matching the old matrix's fail-fast: false).

Env:
    MATRIX_JSON              leg list from plan_review_matrix.py, shape
                             {"include": [{"skill","paradigm","slug","name"}, ...]}
    REVIEW_FANOUT            max concurrent legs (default 12)
    REVIEW_LEG_TIMEOUT_SEC   per-leg wall-clock cap in seconds (default 1200)
    (plus everything review_agent.py reads, inherited unchanged)
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

HERE = Path(__file__).resolve().parent
AGENT = HERE / "review_agent.py"


def _leg_name(leg: dict) -> str:
    return leg.get("name") or f"{leg.get('skill')} · {leg.get('paradigm')}"


def run_leg(leg: dict) -> tuple[dict, int, str]:
    """Run one review_agent.py leg; return (leg, returncode, tail of combined output).

    stdout and stderr are merged into one stream so a failing leg's diagnostics
    are surfaced in full — an unhandled traceback that review_agent.py (or a C
    extension it imports) writes to stdout would otherwise be dropped, leaving
    the ``::warning::`` annotation empty. Legs transfer their results via JSON
    files in REVIEW_OUT_DIR, not stdout, so nothing useful is lost by merging.
    """
    env = {
        **os.environ,
        "EVAL_SKILL": leg["skill"],
        "EVAL_PARADIGM": leg["paradigm"],
    }
    timeout = int(os.environ.get("REVIEW_LEG_TIMEOUT_SEC", "1200"))
    try:
        proc = subprocess.run(
            [sys.executable, str(AGENT)],
            env=env,
            timeout=timeout,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        return leg, proc.returncode, (proc.stdout or "")[-2000:]
    except subprocess.TimeoutExpired as e:
        tail = (e.stdout or "").strip()[-2000:] if e.stdout else ""
        msg = f"timed out after {timeout}s"
        return leg, 124, f"{msg}\n{tail}" if tail else msg


def main() -> int:
    raw = os.environ.get("MATRIX_JSON", "").strip()
    if not raw:
        print("MATRIX_JSON is empty — no legs to run", file=sys.stderr)
        return 0
    include = json.loads(raw).get("include", [])
    if not include:
        print("no legs to run", file=sys.stderr)
        return 0

    fanout = max(1, int(os.environ.get("REVIEW_FANOUT", "12")))
    print(f"running {len(include)} legs, up to {fanout} concurrent", file=sys.stderr)

    failures = 0
    with ThreadPoolExecutor(max_workers=fanout) as pool:
        futures = {pool.submit(run_leg, leg): leg for leg in include}
        for fut in as_completed(futures):
            leg, rc, err = fut.result()
            name = _leg_name(leg)
            if rc == 0:
                print(f"[ok]   {name}", file=sys.stderr)
            else:
                failures += 1
                # Advisory: surface the failure, never fail the job.
                # err is the tail of the leg's output; take the LAST 300 chars
                # so the annotation shows the failure, not startup chatter.
                print(
                    f"::warning title=skills-review::leg failed (rc={rc}): "
                    f"{name} — {err.strip()[-300:]}"
                )
                print(f"[fail] {name} (rc={rc})", file=sys.stderr)

    print(
        f"legs done: {len(include) - failures} ok, {failures} failed/timed out",
        file=sys.stderr,
    )
    return 0  # advisory — a review leg error must not fail the merge


if __name__ == "__main__":
    sys.exit(main())
