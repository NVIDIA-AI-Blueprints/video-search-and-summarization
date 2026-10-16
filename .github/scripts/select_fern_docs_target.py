#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Route persistent Fern docs to develop or the highest stable release branch."""

from __future__ import annotations

import argparse
import re
from collections.abc import Iterable
from pathlib import Path

RELEASE_BRANCH = re.compile(r"release/(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)")


def select_target(branch: str, branches: Iterable[str]) -> dict[str, str]:
    if branch == "develop":
        return {
            "publish": "true",
            "instance": "nvidia-vss-develop.docs.buildwithfern.com/vss",
        }

    releases = {
        name: tuple(int(part) for part in match.groups())
        for name in branches
        if (match := RELEASE_BRANCH.fullmatch(name))
    }
    latest = max(releases, key=releases.__getitem__, default=None)
    if branch != latest or latest is None:
        return {"publish": "false"}

    return {
        "publish": "true",
        "instance": "nvidia-vss-staging.docs.buildwithfern.com/vss",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--branch", required=True)
    parser.add_argument("--branches-file", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    target = select_target(args.branch, args.branches_file.read_text().splitlines())
    with args.output.open("a") as output:
        for name, value in target.items():
            print(f"{name}={value}", file=output)
    if target["publish"] == "true":
        print(f"Publishing {args.branch} to {target['instance']}")
    else:
        print(f"Skipping {args.branch}: only develop and the latest release/X.Y.Z publish docs")


if __name__ == "__main__":
    main()
