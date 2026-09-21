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
WORKFLOW = ROOT / ".github" / "workflows" / "skills-eval.yml"
BREV_ENV = ROOT / ".github" / "skill-eval" / "envs" / "brev_env.py"


def _workflow_env_keys() -> set[str]:
    """Keys of the workflow's top-level `env:` mapping.

    Scanned rather than parsed with PyYAML: this runs in the harness-contracts
    step, whose uvx environment carries pytest and nothing else. The block is a
    flat two-space-indented mapping, so a scanner is sufficient and keeps the
    test runnable anywhere.
    """
    keys: set[str] = set()
    inside = False
    for line in WORKFLOW.read_text().splitlines():
        if not inside:
            inside = line.rstrip() == "env:"
            continue
        if line.strip() and not line.startswith("  "):
            break                                   # dedent ends the block
        m = re.match(r"^  ([A-Za-z_][A-Za-z0-9_]*):", line)
        if m:
            keys.add(m.group(1))
    assert keys, "no top-level env: block found in skills-eval.yml"
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
    """A VSS_* knob set in the workflow must reach the box that deploys."""
    missing = sorted(
        k for k in _workflow_env_keys()
        if k.startswith("VSS_") and k not in _forwarded_keys()
    )
    assert not missing, (
        "set in skills-eval.yml but not forwarded by brev_env.py, so the deploy "
        f"will silently keep its default: {missing}"
    )


def test_prebake_flag_is_wired_end_to_end():
    """The specific knob this test file was added for, asserted both sides."""
    assert "VSS_VIOS_PREBAKE_PACKAGES" in _workflow_env_keys()
    assert "VSS_VIOS_PREBAKE_PACKAGES" in _forwarded_keys()
