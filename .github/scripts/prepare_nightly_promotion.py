#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Select the latest green develop release set for GitLab promotion."""
from __future__ import annotations

import argparse
import base64
import json
import os
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


def promotion_variables(
    release_set: dict[str, Any],
    *,
    requested_tag: str = "",
    agent_ui_config: str = "",
    alert_config: str = "",
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
    names = {str(image.get("name") or "") for image in built}
    if names.intersection({"vss-agent", "vss-agent-ui"}) and not agent_ui_config:
        raise ValueError("agent/UI artifacts-promotion config path is required")
    if "vss-alert-ms" in names and not alert_config:
        raise ValueError("alert artifacts-promotion config path is required")

    encoded = base64.b64encode(
        (json.dumps(release_set, separators=(",", ":")) + "\n").encode()
    ).decode()
    variables = {
        "BUILD_TYPE": "ghcr-promotion",
        "VSS_RELEASE_SET_B64": encoded,
        "VSS_RELEASE_SET_ID": release_set["release_set_id"],
        "VSS_PROMOTION_TAG": tag,
    }
    if agent_ui_config:
        variables["AGENT_UI_ARTIFACTS_PROMOTION_CONFIG_PATH"] = agent_ui_config
    if alert_config:
        variables["ALERT_ARTIFACTS_PROMOTION_CONFIG_PATH"] = alert_config
    return tag, variables


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", default=os.environ.get("GITHUB_REPOSITORY", ""))
    parser.add_argument("--requested-sha", default="")
    parser.add_argument("--requested-tag", default="")
    parser.add_argument("--agent-ui-config", default="")
    parser.add_argument("--alert-config", default="")
    args = parser.parse_args()

    token = os.environ.get("GITHUB_TOKEN", "").strip()
    github_env = os.environ.get("GITHUB_ENV", "").strip()
    github_output = os.environ.get("GITHUB_OUTPUT", "").strip()
    if not token or not args.repository or not github_env or not github_output:
        raise SystemExit(
            "GITHUB_TOKEN, repository, GITHUB_ENV, and GITHUB_OUTPUT are required"
        )
    api = GitHubApi(token)
    query = {"branch": "develop", "status": "success", "per_page": 50}
    if args.requested_sha:
        query["head_sha"] = args.requested_sha
    payload = api.request(
        "GET",
        f"/repos/{args.repository}/actions/workflows/build-dev-images.yml/runs?"
        + urllib.parse.urlencode(query),
    )
    run = select_build_run(payload.get("workflow_runs", []), args.requested_sha)
    if run is None:
        raise RuntimeError("no successful develop GHCR build run matched")
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

    release_set = download_release_set_artifact(
        api, args.repository, int(run["id"])
    )
    if release_set.get("source", {}).get("commit") != source_sha:
        raise RuntimeError("release-set source commit does not match selected run")
    problems = validate_release_set(release_set, load_inventory(Path.cwd()))
    if problems:
        raise RuntimeError("invalid release set: " + "; ".join(problems))
    tag, variables = promotion_variables(
        release_set,
        requested_tag=args.requested_tag,
        agent_ui_config=args.agent_ui_config,
        alert_config=args.alert_config,
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
    raise SystemExit(main())
