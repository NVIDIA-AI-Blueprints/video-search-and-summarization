#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Delete GHCR package versions containing only tags for a closed VSS PR."""
from __future__ import annotations

import argparse
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


def deletable_version_ids(
    versions: list[dict[str, Any]], tag_prefix: str
) -> list[int]:
    selected: list[int] = []
    for version in versions:
        tags = version.get("metadata", {}).get("container", {}).get("tags", [])
        if tags and all(str(tag).startswith(tag_prefix) for tag in tags):
            selected.append(int(version["id"]))
    return selected


class GitHubPackages:
    def __init__(self, token: str, owner: str):
        self.owner = owner
        self.headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "User-Agent": "vss-ghcr-pr-gc",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def request(self, method: str, path: str) -> Any:
        request = urllib.request.Request(
            f"https://api.github.com{path}", headers=self.headers, method=method
        )
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                body = response.read()
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return []
            raise RuntimeError(
                f"GitHub Packages API {method} failed with status {exc.code}"
            ) from exc
        return json.loads(body) if body else None

    def versions(self, package: str) -> list[dict[str, Any]]:
        encoded = urllib.parse.quote(package, safe="")
        path = (
            f"/orgs/{self.owner}/packages/container/{encoded}/versions"
            "?per_page=100"
        )
        payload = self.request("GET", path)
        return payload if isinstance(payload, list) else []

    def delete(self, package: str, version_id: int) -> None:
        encoded = urllib.parse.quote(package, safe="")
        self.request(
            "DELETE",
            f"/orgs/{self.owner}/packages/container/{encoded}/versions/{version_id}",
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pr-number", type=int, required=True)
    parser.add_argument(
        "--inventory",
        type=Path,
        default=Path("deploy/docker/container-inventory.json"),
    )
    parser.add_argument(
        "--owner", default=os.environ.get("GITHUB_REPOSITORY_OWNER", "")
    )
    args = parser.parse_args()
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if not token or not args.owner:
        raise SystemExit("GITHUB_TOKEN and repository owner are required")

    inventory = json.loads(args.inventory.read_text())
    images = [
        entry["name"]
        for entry in inventory["images"]
        if entry.get("strategy") == "build" and entry.get("ghcr_build")
    ]
    packages = GitHubPackages(token, args.owner)
    prefix = f"pr-{args.pr_number}-"
    deleted = 0
    for image in images:
        package = f"vss/{image}"
        versions = packages.versions(package)
        for version_id in deletable_version_ids(versions, prefix):
            packages.delete(package, version_id)
            deleted += 1
            print(f"Deleted {package} version {version_id} ({prefix}*)")
    print(f"Deleted {deleted} PR-only GHCR package version(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
