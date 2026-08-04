#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Publish immutable GHCR candidate coordinates after downstream CI passes."""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path
from typing import Any

MARKER = "<!-- vss-ghcr-candidates -->"
API_ROOT = "https://api.github.com"
COMMENT_PAGE_SIZE = 100
MAX_COMMENT_PAGES = 100
MAX_API_RESPONSE_BYTES = 64 * 1024 * 1024
MAX_RELEASE_SET_BYTES = 8 * 1024 * 1024


class SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Never forward the GitHub bearer token to artifact storage hosts."""

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> urllib.request.Request | None:
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected is None:
            return None
        source = urllib.parse.urlsplit(req.full_url)
        destination = urllib.parse.urlsplit(newurl)
        if (source.scheme, source.netloc) != (
            destination.scheme,
            destination.netloc,
        ):
            redirected.remove_header("Authorization")
        return redirected


_URL_OPENER = urllib.request.build_opener(SafeRedirectHandler())


def safe_urlopen(request: urllib.request.Request, timeout: int) -> Any:
    return _URL_OPENER.open(request, timeout=timeout)


def enforce_memory_ceiling() -> None:
    """Keep a broken CI helper from exhausting its runner or developer host."""
    try:
        import resource
    except ImportError:
        return
    raw_limit = os.environ.get("GHCR_CANDIDATE_MEMORY_LIMIT_GB", "10").strip()
    try:
        limit_gb = float(raw_limit)
    except ValueError as exc:
        raise ValueError(
            "GHCR_CANDIDATE_MEMORY_LIMIT_GB must be numeric"
        ) from exc
    if limit_gb <= 0:
        return
    requested = int(limit_gb * 1024**3)
    soft, hard = resource.getrlimit(resource.RLIMIT_AS)
    if soft != resource.RLIM_INFINITY:
        requested = min(requested, soft)
    if hard != resource.RLIM_INFINITY:
        requested = min(requested, hard)
    resource.setrlimit(resource.RLIMIT_AS, (requested, requested))


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
    def __init__(self, token: str, open_func: Any = None):
        self.open_func = open_func or safe_urlopen
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
        headers = (
            dict(self.headers)
            if urllib.parse.urlsplit(url).netloc == "api.github.com"
            else {"User-Agent": self.headers["User-Agent"]}
        )
        if data is not None:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with self.open_func(request, timeout=60) as response:
                body = response.read(MAX_API_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError as exc:
            raise RuntimeError(
                f"GitHub API {method} failed with status {exc.code}"
            ) from exc
        if len(body) > MAX_API_RESPONSE_BYTES:
            raise RuntimeError(
                f"GitHub API response exceeded {MAX_API_RESPONSE_BYTES} bytes"
            )
        content_type = response.headers.get_content_type()
        return json.loads(body) if content_type == "application/json" else body


#: Conclusions that mean the companion build will never produce a release set.
#: Anything else (None while queued or in progress, or a value we do not
#: recognise) is treated as "keep waiting", so an unfamiliar conclusion can only
#: cost time, never a false failure.
#: ``action_required`` is deliberately absent: approval can resume the same run.
TERMINAL_FAILURE_CONCLUSIONS = frozenset(
    {"failure", "cancelled", "timed_out", "startup_failure", "stale",
     "neutral", "skipped"}
)


def select_release_set_run(
    runs: list[dict[str, Any]], sha: str, ref_name: str
) -> dict[str, Any] | None:
    """Newest Build Dev Images run for this exact commit and ref, any conclusion.

    Deliberately not filtered to successes. The caller has to tell three states
    apart: not created yet, still running, and finished unsuccessfully. Filtering
    here collapsed the last two into "keep polling", so a failed companion build
    was indistinguishable from one that had not started and the caller waited out
    its whole budget against a run that would never appear.

    Newest wins so that a rerun supersedes the attempt it replaced.
    """
    matches = [
        run
        for run in runs
        if run.get("head_sha") == sha and run.get("head_branch") == ref_name
    ]
    if not matches:
        return None
    return max(
        matches,
        key=lambda run: (
            str(run.get("created_at") or ""),
            run.get("run_number") or 0,
            run.get("id") or 0,
        ),
    )


def download_release_set(
    api: GitHubApi,
    repository: str,
    sha: str,
    ref_name: str,
    attempts: int,
    interval_seconds: int,
) -> dict[str, Any]:
    query = urllib.parse.urlencode(
        {"head_sha": sha, "branch": ref_name, "per_page": 100}
    )
    run: dict[str, Any] | None = None
    for attempt in range(1, attempts + 1):
        payload = api.request(
            "GET",
            f"/repos/{repository}/actions/workflows/build-dev-images.yml/runs?{query}",
        )
        run = select_release_set_run(payload.get("workflow_runs", []), sha, ref_name)
        if run is not None:
            status = run.get("status")
            conclusion = run.get("conclusion")
            if status != "completed":
                # Queued, in progress, or a re-run in flight. A GitHub re-run
                # reuses the run id and resets status, so an earlier failure
                # must not abort the attempt that replaced it.
                conclusion = None
            if conclusion == "success":
                break
            if conclusion in TERMINAL_FAILURE_CONCLUSIONS:
                # Fail now rather than polling out the budget. The companion run
                # has finished and will never publish a release set.
                raise RuntimeError(
                    f"GHCR build run {run.get('id')} for {sha[:12]} on {ref_name} "
                    f"concluded {conclusion!r}; it will not produce a release set. "
                    f"See {run.get('html_url') or 'the Build Dev Images run'}."
                )
            # Present but not finished: keep waiting.
        if attempt < attempts:
            state = "not ready" if run is None else f"{run.get('status')}"
            print(
                f"GHCR build run for {sha[:12]} is {state}; "
                f"retrying in {interval_seconds}s ({attempt}/{attempts})",
                flush=True,
            )
            time.sleep(interval_seconds)
        run = None
    if run is None:
        raise RuntimeError(
            f"no GHCR build run for {sha} on {ref_name} reached a successful "
            f"conclusion after {attempts} polling attempts at "
            f"{interval_seconds}s intervals"
        )

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
        if bundle.getinfo(matches[0]).file_size > MAX_RELEASE_SET_BYTES:
            raise RuntimeError("release-set artifact is too large")
        return json.loads(bundle.read(matches[0]))


def upsert_comment(
    api: GitHubApi, repository: str, number: int, body: str
) -> None:
    existing: dict[str, Any] | None = None
    seen_comment_ids: set[Any] = set()
    for page in range(1, MAX_COMMENT_PAGES + 1):
        comments = api.request(
            "GET",
            f"/repos/{repository}/issues/{number}/comments"
            f"?per_page={COMMENT_PAGE_SIZE}&page={page}",
        )
        if not isinstance(comments, list):
            raise RuntimeError("GitHub comments response was not a list")
        page_ids = {
            comment.get("id")
            for comment in comments
            if isinstance(comment, dict) and comment.get("id") is not None
        }
        if page_ids and page_ids.issubset(seen_comment_ids):
            raise RuntimeError("GitHub comment pagination repeated a page")
        seen_comment_ids.update(page_ids)
        existing = next(
            (
                comment
                for comment in comments
                if MARKER in str(comment.get("body", ""))
            ),
            None,
        )
        if existing is not None or len(comments) < COMMENT_PAGE_SIZE:
            break
    else:
        raise RuntimeError(
            f"GitHub comment pagination exceeded {MAX_COMMENT_PAGES} pages"
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", default=os.environ.get("GITHUB_REPOSITORY", ""))
    parser.add_argument("--sha", default=os.environ.get("GITHUB_SHA", ""))
    parser.add_argument("--ref-name", default=os.environ.get("GITHUB_REF_NAME", ""))
    parser.add_argument("--attempts", type=int, default=20)
    parser.add_argument("--interval-seconds", type=int, default=15)
    parser.add_argument("--release-set", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    number = pr_number(args.ref_name)
    if number is None:
        print(f"{args.ref_name!r} is not a synthetic PR ref; nothing to update.")
        return 0
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    api = GitHubApi(token) if token else None
    if args.release_set:
        release_set = json.loads(args.release_set.read_text())
    else:
        if api is None or not args.repository or not args.sha:
            raise SystemExit("GITHUB_TOKEN, repository, and SHA are required")
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
    body = render_comment(release_set, args.sha)
    if args.dry_run:
        print("[ghcr-candidate-reporter] DRY RUN comment body:")
        print(body)
        return 0
    if api is None:
        raise SystemExit("GITHUB_TOKEN is required unless --dry-run is used")
    upsert_comment(api, args.repository, number, body)
    return 0


if __name__ == "__main__":
    try:
        enforce_memory_ceiling()
        raise SystemExit(main())
    except Exception as exc:
        print(
            f"[ghcr-candidate-reporter] ERROR {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        raise
