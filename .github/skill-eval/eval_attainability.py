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
"""Report which eval cells can actually accumulate samples, from nightly results.

Steps chain: a step scoring below 1.0 aborts the rest of its spec, so a later
cell only produces a result on nights where every preceding step scored exactly
1.0. A cell behind a reliably imperfect step yields nothing -- not rarely, but
never. Any plan assuming "N samples per cell" has to be checked against what the
nightlies produce.

Reads downloaded `skills-eval-daily` artifacts and labels every cell as
EXECUTED (a judge verdict exists), SKIPPED_BY_CHAIN (an earlier step scored
< 1.0 that night) or ABSENT (the leg produced nothing at all).

    gh run download <run-id> -D nightly/          # repeat per run
    eval_attainability.py --artifacts nightly/ --min-samples 10
"""

from __future__ import annotations

import argparse
import json
import sys
import tarfile
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from eval_parity_scope import REPO_ROOT, all_skills, scan_spec  # noqa: E402
from plan_matrix import specs_for_skill  # noqa: E402

ARTIFACT_PREFIX = "skills-eval-daily-results-"

# Step number comes from judge.json, never the path: multi-step specs use
# `step-N__<hash>/` but single-step specs use a flat `<platform>__<hash>/`, so
# keying off the path drops every single-step verdict and misreports it ABSENT.
JUDGE_SUFFIX = "verifier/judge.json"

EXECUTED, SKIPPED, ABSENT = "EXECUTED", "SKIPPED_BY_CHAIN", "ABSENT"


def parse_artifact_name(filename: str) -> tuple[str, str, str] | None:
    """`...-<skill>__<stem>__<platform>-<run_id>.tar.gz` -> (skill, stem, platform)."""
    name = filename[:-7] if filename.endswith(".tar.gz") else filename
    if not name.startswith(ARTIFACT_PREFIX):
        return None
    body = name[len(ARTIFACT_PREFIX):].rsplit("-", 1)[0]  # drop the run id
    parts = body.split("__")
    if len(parts) != 3:
        return None
    return parts[0], parts[1], parts[2]


def read_leg(path: Path) -> dict[int, float]:
    """Step number -> reward, for one leg's artifact tarball."""
    out: dict[int, float] = {}
    with tarfile.open(path, "r:gz") as tf:
        for member in tf.getmembers():
            if not member.name.endswith(JUDGE_SUFFIX) or not member.isfile():
                continue
            fh = tf.extractfile(member)
            if fh is None:
                continue
            try:
                verdict = json.load(fh)
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            step = verdict.get("step")
            if step is None:
                continue
            out[int(step)] = float(verdict.get("reward", 0.0))
    return out


def classify_leg(total_cells: int, rewards: dict[int, float]) -> dict[int, str]:
    """Label every 1-indexed cell of one spec for one night.

    A cell is SKIPPED_BY_CHAIN rather than ABSENT when some earlier step ran and
    scored below 1.0 -- that is the chain abort, and it is the interesting case
    because it recurs every night the earlier step is imperfect.
    """
    labels: dict[int, str] = {}
    aborted = False
    for cell in range(1, total_cells + 1):
        if cell in rewards:
            labels[cell] = EXECUTED
            if rewards[cell] < 1.0:
                aborted = True
        else:
            labels[cell] = SKIPPED if aborted else ABSENT
    return labels


def spec_cell_counts() -> dict[tuple[str, str], int]:
    """(skill, stem) -> number of cells, from the committed specs."""
    counts = {}
    for skill in all_skills():
        for rel, _eval_dir, stem in specs_for_skill(skill):
            counts[(skill, stem)] = scan_spec(rel)["cells"]
    return counts


def analyse(artifact_dir: Path, min_samples: int) -> dict:
    counts = spec_cell_counts()
    samples: dict[tuple[str, str, int], int] = defaultdict(int)
    labels: dict[tuple[str, str, int], dict[str, int]] = defaultdict(
        lambda: defaultdict(int)
    )
    runs, unknown = set(), set()

    for tarball in sorted(artifact_dir.rglob("*.tar.gz")):
        parsed = parse_artifact_name(tarball.name)
        if not parsed:
            continue
        skill, stem, _platform = parsed
        total = counts.get((skill, stem))
        if total is None:
            unknown.add(f"{skill}/{stem}")
            continue
        runs.add(tarball.name.rsplit("-", 1)[1])
        for cell, label in classify_leg(total, read_leg(tarball)).items():
            key = (skill, stem, cell)
            labels[key][label] += 1
            if label == EXECUTED:
                samples[key] += 1

    cells = []
    for (skill, stem), total in sorted(counts.items()):
        for cell in range(1, total + 1):
            key = (skill, stem, cell)
            n = samples.get(key, 0)
            cells.append(
                {
                    "spec": f"{skill}/{stem}",
                    "cell": cell,
                    "samples": n,
                    "reachable": n >= min_samples,
                    "labels": dict(labels.get(key, {})),
                }
            )

    return {
        "runs_seen": len(runs),
        "min_samples": min_samples,
        "cells_total": len(cells),
        "cells_reachable": sum(1 for c in cells if c["reachable"]),
        "cells_starved": sum(1 for c in cells if c["samples"] == 0),
        "unknown_specs": sorted(unknown),
        "cells": cells,
    }


def format_report(result: dict) -> str:
    pct = 100.0 * result["cells_reachable"] / (result["cells_total"] or 1)
    lines = [
        f"nightly runs analysed: {result['runs_seen']}",
        f"cells: {result['cells_total']}",
        f"reachable at >={result['min_samples']} samples: {result['cells_reachable']} ({pct:.1f}%)",
        f"never sampled: {result['cells_starved']}",
    ]
    if result["unknown_specs"]:
        lines.append(f"artifacts with no matching spec: {', '.join(result['unknown_specs'])}")
    worst = [c for c in result["cells"] if c["samples"] == 0]
    if worst:
        lines += ["", "cells that never produced a sample (deterministic gates only):"]
        lines += [
            f"  {c['spec']} cell {c['cell']}"
            f"  ({max(c['labels'], key=c['labels'].get) if c['labels'] else 'no data'})"
            for c in worst
        ]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="Report per-cell sample attainability.")
    ap.add_argument("--artifacts", required=True, type=Path, help="dir of artifact tarballs")
    ap.add_argument("--min-samples", type=int, default=10, help="samples to count as reachable")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if not args.artifacts.is_dir():
        print(f"FATAL: no such directory: {args.artifacts}", file=sys.stderr)
        return 2

    result = analyse(args.artifacts, args.min_samples)
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(format_report(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
