#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Restore develop's committed managed-container defaults after a PR merge."""
from __future__ import annotations

import argparse
import base64
import os
import urllib.parse

from update_pr_ghcr_candidates import GitHubApi, update_container_defaults

DEFAULT_REGISTRY = "ghcr.io/nvidia-ai-blueprints/vss"
DEFAULT_TAG = "develop-latest"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", default=os.environ.get("GITHUB_REPOSITORY", ""))
    parser.add_argument("--branch", default="develop")
    args = parser.parse_args()
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if not token or not args.repository:
        raise SystemExit("GITHUB_TOKEN and repository are required")

    api = GitHubApi(token)
    path = "deploy/docker/containers.env"
    encoded_path = urllib.parse.quote(path, safe="/")
    query = urllib.parse.urlencode({"ref": args.branch})
    current = api.request(
        "GET", f"/repos/{args.repository}/contents/{encoded_path}?{query}"
    )
    original = base64.b64decode(current["content"]).decode()
    updated = update_container_defaults(
        original, DEFAULT_REGISTRY, DEFAULT_TAG
    )
    if updated == original:
        print(f"{args.branch} already uses {DEFAULT_REGISTRY}:{DEFAULT_TAG}.")
        return 0

    api.request(
        "PUT",
        f"/repos/{args.repository}/contents/{encoded_path}",
        {
            "message": (
                "ci: restore develop GHCR channel\n\n"
                "PRs pin their tested immutable tag; develop follows the "
                "continuous develop-latest alias."
            ),
            "content": base64.b64encode(updated.encode()).decode(),
            "sha": current["sha"],
            "branch": args.branch,
        },
    )
    print(f"Restored {args.branch} to {DEFAULT_REGISTRY}:{DEFAULT_TAG}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
