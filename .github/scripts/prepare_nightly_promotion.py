#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Select the latest green develop release set for GitLab promotion."""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import urllib.parse
from pathlib import Path
from typing import Any

from release_set import load_inventory, validate_release_set
from update_pr_ghcr_candidates import (
    GitHubApi,
    download_release_set_artifact,
)


def select_build_run(
    runs: list[dict[str, Any]], requested_sha: str = ""
) -> dict[str, Any] | None:
    for run in runs:
        if (
            run.get("head_branch") == "develop"
            and run.get("conclusion") == "success"
            and (not requested_sha or run.get("head_sha") == requested_sha)
        ):
            return run
    return None


def build_entries(release_set: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        image
        for image in release_set.get("images", [])
        if image.get("strategy") == "build"
        and str(image.get("image", "")).startswith("ghcr.io/")
    ]


def select_build_release_set(
    api: GitHubApi,
    repository: str,
    *,
    requested_sha: str = "",
    per_page: int = 100,
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """Find the latest successful develop run that produced GHCR build entries."""
    page = 1
    while True:
        query: dict[str, str | int] = {
            "branch": "develop",
            "status": "success",
            "per_page": per_page,
            "page": page,
        }
        if requested_sha:
            query["head_sha"] = requested_sha
        payload = api.request(
            "GET",
            f"/repos/{repository}/actions/workflows/build-dev-images.yml/runs?"
            + urllib.parse.urlencode(query),
        )
        runs = payload.get("workflow_runs", [])
        for run in runs:
            if select_build_run([run], requested_sha) is None:
                continue
            try:
                release_set = download_release_set_artifact(
                    api,
                    repository,
                    int(run["id"]),
                )
            except RuntimeError:
                continue
            if build_entries(release_set):
                return run, release_set
        if len(runs) < per_page:
            return None
        page += 1


def promotion_variables(
    release_set: dict[str, Any],
    *,
    requested_tag: str = "",
) -> tuple[str, dict[str, str]]:
    built = build_entries(release_set)
    if not built:
        raise ValueError("release set has no GHCR build entries to promote")
    tags = {str(image.get("tag") or "") for image in built}
    if len(tags) != 1 or "" in tags:
        raise ValueError(f"release set has inconsistent build tags: {sorted(tags)}")
    tag = next(iter(tags))
    if requested_tag and requested_tag != tag:
        raise ValueError(
            f"requested tag {requested_tag!r} does not match release-set tag {tag!r}"
        )
    encoded = base64.b64encode(
        (json.dumps(release_set, separators=(",", ":")) + "\n").encode()
    ).decode()
    variables = {
        "BUILD_TYPE": "ghcr-nightly",
        "VSS_ACCEPTANCE_REGISTRY": "ghcr",
        "VSS_RELEASE_SET_B64": encoded,
        "VSS_RELEASE_SET_ID": release_set["release_set_id"],
        "VSS_PROMOTION_TAG": tag,
    }
    return tag, variables


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", default=os.environ.get("GITHUB_REPOSITORY", ""))
    parser.add_argument("--requested-sha", default="")
    parser.add_argument("--requested-tag", default="")
    parser.add_argument("--release-set", type=Path)
    parser.add_argument(
        "--release-set-output",
        type=Path,
        default=Path(".promotion/release-set.json"),
    )
    args = parser.parse_args()

    token = os.environ.get("GITHUB_TOKEN", "").strip()
    github_env = os.environ.get("GITHUB_ENV", "").strip()
    github_output = os.environ.get("GITHUB_OUTPUT", "").strip()
    if not github_env or not github_output:
        raise SystemExit(
            "GITHUB_ENV and GITHUB_OUTPUT are required"
        )
    if args.release_set:
        release_set = json.loads(args.release_set.read_text())
        source_sha = str(release_set.get("source", {}).get("commit") or "")
    else:
        if not token or not args.repository:
            raise SystemExit(
                "GITHUB_TOKEN and repository are required without --release-set"
            )
        api = GitHubApi(token)
        selected = select_build_release_set(
            api,
            args.repository,
            requested_sha=args.requested_sha,
        )
        if selected is None:
            raise RuntimeError(
                "no successful develop GHCR build run with candidate images matched"
            )
        run, release_set = selected
        source_sha = str(run["head_sha"])

        ci_query = urllib.parse.urlencode(
            {"head_sha": source_sha, "status": "success", "per_page": 20}
        )
        ci_runs = api.request(
            "GET",
            f"/repos/{args.repository}/actions/workflows/ci.yml/runs?{ci_query}",
        ).get("workflow_runs", [])
        if not any(item.get("conclusion") == "success" for item in ci_runs):
            raise RuntimeError(
                f"commit {source_sha} has no successful GitHub CI/downstream run"
            )

    if release_set.get("source", {}).get("commit") != source_sha:
        raise RuntimeError("release-set source commit does not match selected run")
    problems = validate_release_set(release_set, load_inventory(Path.cwd()))
    if problems:
        raise RuntimeError("invalid release set: " + "; ".join(problems))
    tag, variables = promotion_variables(
        release_set,
        requested_tag=args.requested_tag,
    )
    args.release_set_output.parent.mkdir(parents=True, exist_ok=True)
    args.release_set_output.write_text(
        json.dumps(release_set, indent=2, sort_keys=True) + "\n"
    )

    with Path(github_env).open("a") as output:
        output.write(f"DOWNSTREAM_COMMIT_SHA={source_sha}\n")
        output.write("DOWNSTREAM_EXTRA_VARIABLES_JSON<<EOF\n")
        output.write(json.dumps(variables, separators=(",", ":")) + "\n")
        output.write("EOF\n")
    with Path(github_output).open("a") as output:
        output.write(f"source_sha={source_sha}\n")
        output.write(f"promotion_tag={tag}\n")
        output.write(f"release_set_id={release_set['release_set_id']}\n")
    print(
        f"Selected {release_set['release_set_id']} at {source_sha[:12]} "
        f"for immutable tag {tag}."
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(
            f"[nightly-release-set] ERROR {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        raise
