#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Which GPU fleet a skill-eval spec may run on.

Shared by the Brev planner (``.github/skill-eval/plan_matrix.py``) and the
OpenShell planner (``.github/skill-eval/openshell/plan_matrix.py``). A spec
opts in; the skill directory name does not.

``infrastructure`` is ``"brev"``, ``"openshell"``, or a list of those.
When it is absent, a top-level ``openshell`` object means OpenShell only
(the placement contract: ``openshell.gpu_count``). Every other spec stays
on Brev.
"""
from __future__ import annotations

import json
from pathlib import Path

FLEETS = frozenset({"brev", "openshell"})


def infrastructures_for_spec(spec: object) -> frozenset[str]:
    """Fleets named by one parsed spec object."""
    if not isinstance(spec, dict):
        return frozenset({"brev"})
    raw = spec.get("infrastructure")
    if isinstance(raw, str):
        chosen = {raw}
    elif isinstance(raw, list):
        chosen = {item for item in raw if isinstance(item, str)}
    else:
        chosen = set()
    chosen &= FLEETS
    if chosen:
        return frozenset(chosen)
    if isinstance(spec.get("openshell"), dict):
        return frozenset({"openshell"})
    return frozenset({"brev"})


def infrastructures_for_path(spec_path: Path) -> frozenset[str]:
    """Fleets for a spec file. Unreadable or missing files stay on Brev."""
    try:
        spec = json.loads(spec_path.read_text())
    except (OSError, json.JSONDecodeError):
        return frozenset({"brev"})
    return infrastructures_for_spec(spec)
