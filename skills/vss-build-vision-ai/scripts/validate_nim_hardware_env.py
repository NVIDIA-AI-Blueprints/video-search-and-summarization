#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Validate tuning files for the local NIM profiles selected by a build."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


PROFILE_PREFIXES = (
    ("llm_local_shared_", "-shared"),
    ("vlm_local_shared_", "-shared"),
    ("llm_local_", ""),
    ("vlm_local_", ""),
)


def required_tuning_files(
    repo_root: Path, profiles: str, hardware_profile: str
) -> list[Path]:
    required: list[Path] = []
    for profile in filter(None, (item.strip() for item in profiles.split(","))):
        for prefix, suffix in PROFILE_PREFIXES:
            if profile.startswith(prefix):
                slug = profile.removeprefix(prefix)
                required.append(
                    repo_root
                    / "deploy/docker/services/nim"
                    / slug
                    / f"hw-{hardware_profile}{suffix}.env"
                )
                break
    return required


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", required=True, type=Path)
    parser.add_argument("--profiles", required=True)
    parser.add_argument("--hardware-profile", required=True)
    args = parser.parse_args()

    # An empty value means the caller's extraction failed, not that a build
    # selected nothing: every profile layer assigns COMPOSE_PROFILES and
    # HARDWARE_PROFILE. Refuse it, because an empty profile list requires no
    # tuning files and would report a pass for a build this never inspected.
    for flag, value in (
        ("--profiles", args.profiles),
        ("--hardware-profile", args.hardware_profile),
    ):
        if not value.strip():
            print(
                f"ERROR: {flag} is empty — the effective environment was not "
                "read; nothing was validated",
                file=sys.stderr,
            )
            raise SystemExit(1)

    missing = [
        path
        for path in required_tuning_files(
            args.repo_root, args.profiles, args.hardware_profile
        )
        if not path.is_file()
    ]
    if missing:
        for path in missing:
            print(
                "ERROR: selected local NIM has no hardware tuning file: "
                f"{path.relative_to(args.repo_root)}",
                file=sys.stderr,
            )
        raise SystemExit(2)

    print("Validated selected local NIM hardware tuning files")


if __name__ == "__main__":
    main()
