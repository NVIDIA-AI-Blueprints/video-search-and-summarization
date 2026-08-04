# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Cross-post release notes to JIRA and NVBugs (idempotently).

Every posted comment starts with a per-(issue, tag) marker so pipeline
retries and hotfix re-runs never double-post: existing comments are
listed first and the post is skipped when the marker is already there.

Both trackers are reached via REST with bearer tokens: JIRA at
jirasw.nvidia.com, NVBugs at prod.api.nvidia.com/int/nvbugs (the same
NVAuth JWT used by nvbugs-cli; direct REST avoids installing internal
tooling into the CI image).
"""

from __future__ import annotations

import json
import os
import ssl
from urllib.request import Request, urlopen

JIRA_BASE = os.environ.get("JIRA_BASE_URL", "https://jirasw.nvidia.com")
NVBUGS_BASE = os.environ.get("NVBUGS_BASE_URL", "https://prod.api.nvidia.com/int/nvbugs")


def marker(tag: str) -> str:
    return f"[vss-release-notes:{tag}]"


# --- JIRA -------------------------------------------------------------------


def _jira_request(method: str, path: str, body: dict | None = None) -> dict | list | None:
    token = os.environ.get("JIRA_TOKEN", "").strip()
    if not token:
        raise SystemExit("JIRA_TOKEN must be set")
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    request = Request(f"{JIRA_BASE}{path}", data=data, headers=headers, method=method)
    with urlopen(request) as resp:
        payload = resp.read()
        return json.loads(payload) if payload.strip() else None


def jira_already_posted(issue: str, tag: str) -> bool:
    start = 0
    while True:
        page = _jira_request(
            "GET", f"/rest/api/2/issue/{issue}/comment?startAt={start}&maxResults=100"
        )
        comments = (page or {}).get("comments", [])
        if any(marker(tag) in (comment.get("body") or "") for comment in comments):
            return True
        start += len(comments)
        if start >= (page or {}).get("total", 0) or not comments:
            return False


def post_jira_comment(issue: str, body: str, tag: str, dry_run: bool) -> str:
    if dry_run:
        return f"DRY-RUN jira {issue}"
    if jira_already_posted(issue, tag):
        return f"skip jira {issue} (already posted for {tag})"
    _jira_request(
        "POST", f"/rest/api/2/issue/{issue}/comment", {"body": f"{marker(tag)}\n{body}"}
    )
    return f"posted jira {issue}"


# --- NVBugs -----------------------------------------------------------------


def _nvbugs_request(method: str, path: str, body: dict | None = None) -> dict | None:
    token = os.environ.get("NVBUGS_TOKEN", "").strip()
    if not token:
        raise SystemExit("NVBUGS_TOKEN must be set")
    data = json.dumps(body).encode() if body is not None else None
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    # Internal service behind an internal CA (same verify=False the canonical
    # Hinton connector uses).
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    request = Request(f"{NVBUGS_BASE}{path}", data=data, headers=headers, method=method)
    with urlopen(request, context=context, timeout=60) as resp:
        payload = resp.read()
        return json.loads(payload) if payload.strip() else None


def nvbug_already_posted(bug_id: str, tag: str) -> bool:
    bug = _nvbugs_request("GET", f"/api/Bug/GetBug/{bug_id}")
    return marker(tag) in json.dumps(bug or {})


def post_nvbug_comment(bug_id: str, body: str, tag: str, dry_run: bool) -> str:
    if dry_run:
        return f"DRY-RUN nvbug {bug_id}"
    if nvbug_already_posted(bug_id, tag):
        return f"skip nvbug {bug_id} (already posted for {tag})"
    _nvbugs_request(
        "POST",
        f"/api/Bug/Comments/{bug_id}",
        {"Text": f"{marker(tag)}\n{body}", "Notification": True},
    )
    return f"posted nvbug {bug_id}"
