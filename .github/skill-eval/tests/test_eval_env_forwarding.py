# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Anything the workflow sets must also be forwarded to the instance.

A leg's deploy does not run on the runner. It runs on a Brev box, where the
environment is `~/.eval_env`, and only names in `brev_env.py`'s allowlist are
written there. So a variable added to the workflow's `env:` block alone reaches
the runner and stops: the deploy keeps its default, the workflow looks correct,
and nothing reports an error. That failure is silent by construction, which is
why it gets a test rather than a comment.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
# Both workflows, because they are separate files with separate env blocks.
# The nightly sweep is where the GPU-heavy specs actually run, so wiring only
# the PR workflow leaves the population that matters untouched.
WORKFLOWS = (ROOT / ".github" / "workflows" / "skills-eval.yml",
             ROOT / ".github" / "workflows" / "skills-eval-daily.yml")
BREV_ENV = ROOT / ".github" / "skill-eval" / "envs" / "brev_env.py"


def _workflow_env_keys(path: Path) -> set[str]:
    """Keys of one workflow's top-level `env:` mapping.

    Scanned rather than parsed with PyYAML: this runs in the harness-contracts
    step, whose uvx environment carries pytest and nothing else. The block is a
    flat two-space-indented mapping, so a scanner is sufficient and keeps the
    test runnable anywhere.
    """
    keys: set[str] = set()
    inside = False
    for line in path.read_text().splitlines():
        if not inside:
            inside = line.rstrip() == "env:"
            continue
        if line.strip() and not line.startswith("  "):
            break                                   # dedent ends the block
        m = re.match(r"^  ([A-Za-z_][A-Za-z0-9_]*):", line)
        if m:
            keys.add(m.group(1))
    assert keys, f"no top-level env: block found in {path.name}"
    return keys


def _forwarded_keys() -> set[str]:
    """Every string literal iterated by the forwarding loop in brev_env.py.

    Read from the source rather than by importing: brev_env imports harbor,
    which is not installed in the unit-test environment.
    """
    tree = ast.parse(BREV_ENV.read_text())
    best: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.For) or not isinstance(node.iter, ast.Tuple):
            continue
        names = {e.value for e in node.iter.elts
                 if isinstance(e, ast.Constant) and isinstance(e.value, str)}
        # The forwarding loop is the one carrying the known-forwarded names.
        if "PR_HEAD_SHA" in names:
            best = names
    assert best, "could not locate the env-forwarding allowlist in brev_env.py"
    return best


def test_vss_workflow_env_is_forwarded_to_the_instance():
    """A VSS_* knob set in a workflow must reach the box that deploys."""
    forwarded = _forwarded_keys()
    for path in WORKFLOWS:
        missing = sorted(k for k in _workflow_env_keys(path)
                         if k.startswith("VSS_") and k not in forwarded)
        assert not missing, (
            f"set in {path.name} but not forwarded by brev_env.py, so the deploy "
            f"will silently keep its default: {missing}"
        )


def test_every_workflow_declares_the_same_vss_knobs():
    """A knob in one workflow and not the other silently skips a population.

    The PR sweep and the nightly sweep are separate files. Wiring only the PR
    one left the nightly, where the GPU-heavy specs run, on the old path.
    """
    per_file = {p.name: {k for k in _workflow_env_keys(p) if k.startswith("VSS_")}
                for p in WORKFLOWS}
    assert len(set(map(frozenset, per_file.values()))) == 1, (
        f"VSS_* env differs between the workflows: {per_file}"
    )


def test_prebake_flag_is_wired_end_to_end():
    """The specific knob this test file was added for, asserted on every hop."""
    for path in WORKFLOWS:
        assert "VSS_VIOS_PREBAKE_PACKAGES" in _workflow_env_keys(path), path.name
    assert "VSS_VIOS_PREBAKE_PACKAGES" in _forwarded_keys()
