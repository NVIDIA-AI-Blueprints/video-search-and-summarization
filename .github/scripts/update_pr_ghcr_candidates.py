#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Publish immutable GHCR candidate coordinates after downstream CI passes."""
from __future__ import annotations

import argparse
import base64
import io
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from typing import Any

MARKER = "<!-- vss-ghcr-candidates -->"
API_ROOT = "https://api.github.com"


def pr_number(ref_name: str) -> int | None:
    match = re.fullmatch(r"pull-request/(\d+)", ref_name)
    return int(match.group(1)) if match else None


def candidate_entries(release_set: dict[str, Any]) -> list[dict[str, Any]]:
    return sorted(
        (
            entry
            for entry in release_set.get("images", [])
            if entry.get("strategy") == "build"
            and str(entry.get("image", "")).startswith("ghcr.io/")
        ),
        key=lambda entry: str(entry.get("name", "")),
    )


def moving_alias(tag: str) -> str:
    if re.fullmatch(r"develop-[0-9a-f]{7,40}", tag):
        return "develop-latest"
    match = re.fullmatch(r"pr-(\d+)-[0-9a-f]{7,40}", tag)
    return f"pr-{match.group(1)}-latest" if match else ""


def update_container_defaults(text: str, registry: str, tag: str) -> str:
    registry_pattern = re.compile(
        r'^VSS_CONTAINER_REGISTRY="\$\{VSS_CONTAINER_REGISTRY:-[^}]*\}"$',
        re.MULTILINE,
    )
    tag_pattern = re.compile(
        r'^VSS_CONTAINER_TAG="\$\{VSS_CONTAINER_TAG:-[^}]*\}"$',
        re.MULTILINE,
    )
    updated, registry_count = registry_pattern.subn(
        f'VSS_CONTAINER_REGISTRY="${{VSS_CONTAINER_REGISTRY:-{registry}}}"',
        text,
    )
    updated, tag_count = tag_pattern.subn(
        f'VSS_CONTAINER_TAG="${{VSS_CONTAINER_TAG:-{tag}}}"',
        updated,
    )
    if registry_count != 1 or tag_count != 1:
        raise ValueError(
            "containers.env must contain exactly one shared registry and tag default"
        )
    return updated


def render_comment(release_set: dict[str, Any], sha: str) -> str:
    entries = candidate_entries(release_set)
    lines = [
        MARKER,
        "## GHCR candidates validated downstream",
        "",
        f"Downstream validation passed for commit `{sha}`.",
        f"Release set: `{release_set.get('release_set_id', 'unknown')}`",
        "",
    ]
    if not entries:
        lines.append("No GHCR image was rebuilt for this commit.")
    else:
        lines.append("Immutable candidates:")
        for entry in entries:
            lines.append(
                f"- `{entry['name']}`: "
                f"`{entry['image']}:{entry['tag']}@{entry['digest']}`"
            )
            alias = moving_alias(str(entry["tag"]))
            if alias:
                lines.append(f"  - developer alias: `{entry['image']}:{alias}`")
    lines.extend(
        [
            "",
            "These tags are immutable. Promotion copies the same manifest digests to NGC; it does not rebuild them.",
        ]
    )
    return "\n".join(lines)


class GitHubApi:
    def __init__(self, token: str):
        self.headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "User-Agent": "vss-ghcr-candidate-reporter",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def request(
        self, method: str, path_or_url: str, payload: dict[str, Any] | None = None
    ) -> Any:
        url = (
            path_or_url
            if path_or_url.startswith("https://")
            else f"{API_ROOT}{path_or_url}"
        )
        data = json.dumps(payload).encode() if payload is not None else None
        headers = dict(self.headers)
        if data is not None:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                body = response.read()
        except urllib.error.HTTPError as exc:
            raise RuntimeError(
                f"GitHub API {method} failed with status {exc.code}"
            ) from exc
        content_type = response.headers.get_content_type()
        return json.loads(body) if content_type == "application/json" else body


def select_release_set_run(
    runs: list[dict[str, Any]], sha: str, ref_name: str
) -> dict[str, Any] | None:
    for run in runs:
        if (
            run.get("head_sha") == sha
            and run.get("head_branch") == ref_name
            and run.get("conclusion") == "success"
        ):
            return run
    return None


def download_release_set(
    api: GitHubApi,
    repository: str,
    sha: str,
    ref_name: str,
    attempts: int,
    interval_seconds: int,
) -> dict[str, Any]:
    query = urllib.parse.urlencode(
        {"head_sha": sha, "status": "success", "per_page": 20}
    )
    run: dict[str, Any] | None = None
    for attempt in range(1, attempts + 1):
        payload = api.request(
            "GET",
            f"/repos/{repository}/actions/workflows/build-dev-images.yml/runs?{query}",
        )
        run = select_release_set_run(payload.get("workflow_runs", []), sha, ref_name)
        if run is not None:
            break
        if attempt < attempts:
            print(
                f"GHCR build run for {sha[:12]} is not ready; "
                f"retrying in {interval_seconds}s ({attempt}/{attempts})",
                flush=True,
            )
            time.sleep(interval_seconds)
    if run is None:
        raise RuntimeError(f"no successful GHCR build run found for {sha}")

    return download_release_set_artifact(api, repository, int(run["id"]))


def download_release_set_artifact(
    api: GitHubApi, repository: str, run_id: int
) -> dict[str, Any]:
    artifacts = api.request(
        "GET",
        f"/repos/{repository}/actions/runs/{run_id}/artifacts?per_page=100",
    ).get("artifacts", [])
    artifact = next(
        (
            item
            for item in artifacts
            if item.get("name") == "release-set" and not item.get("expired")
        ),
        None,
    )
    if artifact is None:
        raise RuntimeError(f"release-set artifact missing from workflow run {run_id}")

    archive = api.request("GET", artifact["archive_download_url"])
    with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
        matches = [name for name in bundle.namelist() if name.endswith("release-set.json")]
        if len(matches) != 1:
            raise RuntimeError("release-set artifact has an unexpected shape")
        return json.loads(bundle.read(matches[0]))


def upsert_comment(
    api: GitHubApi, repository: str, number: int, body: str
) -> None:
    comments = api.request(
        "GET", f"/repos/{repository}/issues/{number}/comments?per_page=100"
    )
    existing = next(
        (comment for comment in comments if MARKER in str(comment.get("body", ""))),
        None,
    )
    if existing:
        api.request(
            "PATCH",
            f"/repos/{repository}/issues/comments/{existing['id']}",
            {"body": body},
        )
        print(f"Updated GHCR candidate comment on PR #{number}.")
    else:
        api.request(
            "POST",
            f"/repos/{repository}/issues/{number}/comments",
            {"body": body},
        )
        print(f"Created GHCR candidate comment on PR #{number}.")


def commit_tested_coordinates(
    api: GitHubApi,
    repository: str,
    number: int,
    release_set: dict[str, Any],
) -> None:
    entries = candidate_entries(release_set)
    if not entries:
        print("No GHCR image was rebuilt; no coordinate commit needed.")
        return
    registries = {str(entry["image"]).rsplit("/", 1)[0] for entry in entries}
    tags = {str(entry["tag"]) for entry in entries}
    if len(registries) != 1 or len(tags) != 1:
        raise RuntimeError("built candidates do not share one registry and tag")
    registry = next(iter(registries))
    tag = next(iter(tags))

    pull = api.request("GET", f"/repos/{repository}/pulls/{number}")
    head = pull.get("head") or {}
    head_repository = (head.get("repo") or {}).get("full_name")
    branch = str(head.get("ref") or "")
    if head_repository != repository or not branch:
        raise RuntimeError(
            "automatic coordinate commits require a branch in the upstream repository"
        )

    path = "deploy/docker/containers.env"
    encoded_path = urllib.parse.quote(path, safe="/")
    query = urllib.parse.urlencode({"ref": branch})
    current = api.request(
        "GET", f"/repos/{repository}/contents/{encoded_path}?{query}"
    )
    original = base64.b64decode(current["content"]).decode()
    updated = update_container_defaults(original, registry, tag)
    if updated == original:
        print(f"{path} already points at {registry}:{tag}; no commit needed.")
        return
    api.request(
        "PUT",
        f"/repos/{repository}/contents/{encoded_path}",
        {
            "message": (
                f"ci: pin tested GHCR tag {tag}\n\n"
                "This coordinate-only commit reuses the already-built and "
                "downstream-tested image digests."
            ),
            "content": base64.b64encode(updated.encode()).decode(),
            "sha": current["sha"],
            "branch": branch,
        },
    )
    print(f"Committed tested coordinates {registry}:{tag} to {branch}.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", default=os.environ.get("GITHUB_REPOSITORY", ""))
    parser.add_argument("--sha", default=os.environ.get("GITHUB_SHA", ""))
    parser.add_argument("--ref-name", default=os.environ.get("GITHUB_REF_NAME", ""))
    parser.add_argument("--attempts", type=int, default=20)
    parser.add_argument("--interval-seconds", type=int, default=15)
    args = parser.parse_args()

    number = pr_number(args.ref_name)
    if number is None:
        print(f"{args.ref_name!r} is not a synthetic PR ref; nothing to update.")
        return 0
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    tag_bump_token = os.environ.get("TAG_BUMP_TOKEN", "").strip()
    if not token or not tag_bump_token or not args.repository or not args.sha:
        raise SystemExit(
            "GITHUB_TOKEN, TAG_BUMP_TOKEN, repository, and SHA are required"
        )

    api = GitHubApi(token)
    release_set = download_release_set(
        api,
        args.repository,
        args.sha,
        args.ref_name,
        args.attempts,
        args.interval_seconds,
    )
    if release_set.get("source", {}).get("commit") != args.sha:
        raise RuntimeError("release-set source commit does not match downstream SHA")
    upsert_comment(api, args.repository, number, render_comment(release_set, args.sha))
    commit_tested_coordinates(
        GitHubApi(tag_bump_token), args.repository, number, release_set
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
