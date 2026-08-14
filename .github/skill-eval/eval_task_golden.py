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
"""Pin the Harbor task tree each adapter generates, so drift is visible.

A spec is not what the agent is graded on -- the *generated* task tree is. The
adapters under `adapters/*/generate.py` turn a spec into that tree, and they are
several thousand lines of logic. A change there alters instructions, verifier
wiring or step chaining without touching any spec, which moves scores while
every spec-level check still looks identical.

This regenerates each spec's tree into a temp dir and records one hash per spec.
Comparing that hash catches adapter drift; the spec files alone cannot.

    eval_task_golden.py            # verify against the committed golden
    eval_task_golden.py --write    # regenerate it after an intended change
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from eval_parity_scope import REPO_ROOT, all_skills  # noqa: E402
from plan_matrix import spec_platforms, specs_for_skill  # noqa: E402

ADAPTERS = Path(__file__).resolve().parent / "adapters"
GOLDEN = Path(__file__).resolve().parent / "task_tree_golden.json"


def tree_hash(root: Path) -> tuple[str, int]:
    """One hash over every generated file, path-sorted so it is order-stable."""
    digest = hashlib.sha256()
    count = 0
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(path.read_bytes())
        count += 1
    return digest.hexdigest(), count


def _adapter_flags(adapter: Path) -> set[str]:
    """Flags an adapter declares, read from its source rather than assumed."""
    import re

    return set(re.findall(r'add_argument\(\s*"(--[a-z-]+)"', adapter.read_text(encoding="utf-8")))


def generate(skill: str, spec_rel: str, platform: str, out_dir: Path) -> str | None:
    """Run one adapter for one declared platform. Error string, or None on success.

    Paths are passed REPO-RELATIVE and the adapter runs with cwd at the repo
    root. Several adapters serialize the spec path they were given into the
    generated `tests/*.json`, so an absolute path would bake this checkout's
    location into the hash and make the golden differ per machine.
    """
    adapter = ADAPTERS / skill / "generate.py"
    if not adapter.is_file():
        return "no adapter"
    flags = _adapter_flags(adapter)
    cmd = [sys.executable, str(adapter), "--output-dir", str(out_dir)]
    if (REPO_ROOT / "skills" / skill).is_dir():
        cmd += ["--skill-dir", f"skills/{skill}"]
    # Most adapters select a spec with --spec. vss-deploy-profile instead selects
    # with --profile, where the profile name is the spec stem; passing --spec to
    # it is an argparse error, which would silently leave 6 specs unpinned.
    if "--spec" in flags:
        cmd += ["--spec", spec_rel]
    elif "--profile" in flags:
        cmd += ["--profile", Path(spec_rel).stem]
    else:
        return "adapter selects neither --spec nor --profile"
    # Without --platform an adapter falls back to its own default, which for at
    # least one spec is a platform that spec does not declare -- pinning a tree
    # CI never generates.
    if "--platform" in flags:
        cmd += ["--platform", platform]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180,
                          cwd=str(REPO_ROOT))
    if proc.returncode != 0:
        return (proc.stderr.strip().splitlines() or ["exit %d" % proc.returncode])[-1][:160]
    return None


def build() -> dict:
    """Regenerate every (spec, platform) task tree and hash it.

    Keyed per platform because that is CI's unit: one matrix leg per declared
    platform, and the generated tree differs between them.
    """
    specs, errors = {}, {}
    for skill in all_skills():
        for spec_rel, _eval_dir, stem in specs_for_skill(skill):
            for platform in spec_platforms(spec_rel) or [""]:
                key = f"{skill}/{stem}@{platform}" if platform else f"{skill}/{stem}"
                with tempfile.TemporaryDirectory() as tmp:
                    out = Path(tmp) / "out"
                    err = generate(skill, spec_rel, platform, out)
                    if err is not None:
                        errors[key] = err
                        continue
                    sha, files = tree_hash(out)
                specs[key] = {"files": files, "tree_sha256": sha}
    return {"version": 2, "specs": dict(sorted(specs.items())),
            "ungenerated": dict(sorted(errors.items()))}


def diff(golden: dict, current: dict) -> tuple[list[str], list[str]]:
    """Returns (drift, noted).

    Only CHANGED is drift. Adding or removing a spec is deliberate work that is
    already visible in the same PR's diff, so failing on it would tax every
    contributor who adds an eval without catching anything a reviewer cannot
    already see. A CHANGED tree is the invisible case -- the generated tasks
    moved while the spec did not -- which is what this exists to catch.
    """
    old, new = golden.get("specs", {}), current.get("specs", {})
    noted = [f"ADDED    {k}" for k in sorted(set(new) - set(old))]
    noted += [f"REMOVED  {k}" for k in sorted(set(old) - set(new))]
    drift = [
        f"CHANGED  {k}  files {old[k]['files']} -> {new[k]['files']}"
        for k in sorted(set(old) & set(new))
        if old[k]["tree_sha256"] != new[k]["tree_sha256"]
    ]
    return drift, noted


def main() -> int:
    ap = argparse.ArgumentParser(description="Pin the generated Harbor task tree.")
    ap.add_argument("--write", action="store_true", help="rewrite the golden")
    args = ap.parse_args()

    current = build()

    if args.write:
        GOLDEN.write_text(json.dumps(current, indent=2, sort_keys=True) + "\n",
                          encoding="utf-8")
        print(f"wrote {GOLDEN.name}: {len(current['specs'])} specs, "
              f"{len(current['ungenerated'])} ungenerated")
        return 0

    if not GOLDEN.is_file():
        print(f"FATAL: {GOLDEN} missing; run with --write", file=sys.stderr)
        return 2

    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    drift, noted = diff(golden, current)

    if noted:
        print("Spec set changed (not a failure — re-run with --write to record):")
        print("\n".join(noted) + "\n")

    if drift:
        print("An existing spec's generated task tree changed while the spec "
              "itself did not:\n")
        print("\n".join(drift))
        print("\nThat moves what the agent is graded on. If it is intended, "
              "re-run with --write and commit the golden in the same PR so the "
              "score-bearing diff is reviewable.")
        return 1

    print(f"task tree golden OK: {len(golden['specs'])} entries, no drift")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
