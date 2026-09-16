#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""The sizing platform of the GPU this host actually has.

A count-only OpenShell leg carries no SKU: placement is fleet labels plus a
GPU count (`plan_matrix.openshell_job_labels`), so the guest that claims the
job owns its own hardware. Sizing cannot be taken from the spec, because
`hw-<profile>.env` values are measured per card — `hw-H200-shared.env` sets
NIM_KVCACHE_PERCENT=0.5, the value measured to leave 1682 MiB free on an RTX
PRO 6000. A spec that declares `RTXPRO6000BW` and lands on an H200 guest must
not deploy RTX PRO 6000 sizing; it must size for the H200 it got.

So the profile is read off the live card, and an unrecognised card blocks
rather than deploying sizing that was never measured for it.

Used by the eval leg (`skills-eval-daily.yml`, via the CLI below) and by
`adapters/vss-deploy-test-openshell/generate.py`, which imports it so both
answer from one token table.

CLI:
    python3 .github/skill-eval/live_platform.py

    Prints the platform key on stdout and exits 0, or prints why it cannot
    size on stderr and exits 3.
"""
from __future__ import annotations

import subprocess
import sys

# `nvidia-smi --query-gpu=name` substring -> `resources.platforms` key. Order
# matters: "RTX PRO 6000" must be tested before the bare SKU families so a
# Blackwell server card is not read as an older one.
LIVE_GPU_TOKENS: tuple[tuple[str, str], ...] = (
    ("H200", "H200"),
    ("RTX PRO 6000", "RTXPRO6000BW"),
    ("RTX PRO SERVER 6000", "RTXPRO6000BW"),
    ("H100", "H100"),
    ("L40S", "L40S"),
    ("A40", "A40"),
    ("A16", "A16"),
    ("GB10", "DGX-SPARK"),
    ("THOR", "IGX-THOR"),
)

_NO_GPU_ERROR = (
    "cannot read this host's GPU: `nvidia-smi --query-gpu=name` returned "
    "nothing. A count-only OpenShell leg sizes NIM from the live card, so "
    "there is no safe profile to generate for. Pass --platform explicitly "
    "to override."
)


def live_gpu_names() -> list[str]:
    """Names `nvidia-smi` reports for this host, or [] when unreadable."""
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if result.returncode != 0:
        return []
    return [
        line.strip() for line in (result.stdout or "").splitlines() if line.strip()
    ]


def detect_platform(names: list[str]) -> str | None:
    """Platform key for these GPU names, or None when unrecognised."""
    blob = " | ".join(names).upper()
    if not blob:
        return None
    for token, platform in LIVE_GPU_TOKENS:
        if token in blob:
            return platform
    return None


def resolve_from_names(
    requested: str | None, names: list[str]
) -> tuple[str | None, str | None]:
    """Sizing platform for these GPU names, or a reason it cannot size.

    `requested` is an operator override and wins, for local runs on a box
    whose card the tokens above do not cover. A count-only leg passes none —
    the planner deliberately emits none — so the live card decides, and an
    unknown card is a blocker, not a fallback.
    """
    if requested:
        return requested, None
    platform = detect_platform(names)
    if platform:
        return platform, None
    if not names:
        return None, _NO_GPU_ERROR
    return None, (
        "unrecognised GPU on this host: "
        + ", ".join(sorted(set(names)))
        + ". No hw-<profile>.env sizing has been measured for it; add it to "
        "LIVE_GPU_TOKENS and ship the profile files, or pass --platform "
        "explicitly to override."
    )


def resolve_sizing_platform(
    requested: str | None = None,
) -> tuple[str | None, str | None]:
    """`resolve_from_names` against this host's live `nvidia-smi` output."""
    if requested:
        return requested, None
    return resolve_from_names(requested, live_gpu_names())


def main() -> int:
    platform, error = resolve_sizing_platform(None)
    if error or not platform:
        print(f"ERROR: {error}", file=sys.stderr)
        return 3
    print(platform)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
