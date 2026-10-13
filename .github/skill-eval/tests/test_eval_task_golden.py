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
"""Tests for eval_task_golden."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import eval_task_golden as tg  # noqa: E402


def _tree(root: Path, files: dict[str, str]) -> Path:
    for rel, body in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
    return root


def test_tree_hash_is_stable_and_counts_files(tmp_path):
    a = _tree(tmp_path / "a", {"x/1.txt": "one", "y/2.txt": "two"})
    b = _tree(tmp_path / "b", {"y/2.txt": "two", "x/1.txt": "one"})
    assert tg.tree_hash(a) == tg.tree_hash(b)
    assert tg.tree_hash(a)[1] == 2


def test_tree_hash_changes_on_content_and_on_path(tmp_path):
    base = tg.tree_hash(_tree(tmp_path / "a", {"x/1.txt": "one"}))[0]
    body = tg.tree_hash(_tree(tmp_path / "b", {"x/1.txt": "ONE"}))[0]
    moved = tg.tree_hash(_tree(tmp_path / "c", {"z/1.txt": "one"}))[0]
    # A renamed file with identical bytes must not hash the same: adapters can
    # move a step's files without changing them, and that changes the task tree.
    assert base != body and base != moved


def test_only_a_changed_tree_counts_as_drift():
    """Adding or removing a spec is deliberate work already visible in the PR
    diff, so it must not fail the check -- otherwise every contributor adding an
    eval pays a tax. A CHANGED tree is the invisible case and is the failure."""
    old = {"specs": {"a/x": {"files": 1, "tree_sha256": "1"},
                     "a/gone": {"files": 1, "tree_sha256": "9"}}}
    new = {"specs": {"a/x": {"files": 2, "tree_sha256": "2"},
                     "a/added": {"files": 1, "tree_sha256": "3"}}}
    drift, noted = tg.diff(old, new)
    assert any(s.startswith("CHANGED  a/x") for s in drift)
    assert any(s.startswith("ADDED    a/added") for s in noted)
    assert any(s.startswith("REMOVED  a/gone") for s in noted)

    # A pure addition is not drift.
    only_added = {"specs": dict(old["specs"], **{"a/new": {"files": 1, "tree_sha256": "7"}})}
    assert tg.diff(old, only_added)[0] == []
    assert tg.diff(old, old) == ([], [])


def test_adapter_flags_are_read_from_source(tmp_path):
    p = tmp_path / "generate.py"
    p.write_text('parser.add_argument("--output-dir")\nparser.add_argument("--profile")\n',
                 encoding="utf-8")
    assert tg._adapter_flags(p) == {"--output-dir", "--profile"}


def test_committed_golden_covers_every_declared_spec_and_platform():
    """Two failures this guards. A spec missing from the golden is unpinned, so
    adapter drift on it is invisible. And a spec pinned under the wrong platform
    pins a tree CI never generates -- worse than not pinning it, because it
    looks covered."""
    golden = json.loads(tg.GOLDEN.read_text(encoding="utf-8"))
    assert golden["ungenerated"] == {}, "every spec must generate"
    from eval_parity_scope import all_skills
    from plan_matrix import spec_platforms, specs_for_skill

    expected = {
        f"{s}/{stem}@{p}" if p else f"{s}/{stem}"
        for s in all_skills()
        for rel, _d, stem in specs_for_skill(s)
        for p in (spec_platforms(rel) or [""])
    }
    assert set(golden["specs"]) == expected


def test_golden_carries_no_checkout_specific_paths():
    """Several adapters serialize the spec path they are given into the generated
    tests. An absolute path would bake this checkout's location into the hash, so
    the golden would differ per machine with no score-bearing change."""
    assert "/home/" not in tg.GOLDEN.read_text(encoding="utf-8")
