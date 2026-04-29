#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Authorize the actor that dispatched the Release Pipeline workflow.

Refuses to continue unless ``$ACTOR`` either:

* holds one of the repository roles listed in ``$REQUIRED_ROLES`` (a
  comma-separated list, default ``admin,maintain``), as reported by the
  GitHub REST endpoint ``GET /repos/{owner}/{repo}/collaborators/{login}/permission``;
  OR
* appears in the explicit allowlist ``$ALLOWED_ACTORS`` (newline- or
  comma-separated GitHub logins, case-insensitive).

The script intentionally fails closed: any API error, missing token, or
malformed response aborts the workflow with a clear message. It writes
the result to ``$GITHUB_STEP_SUMMARY`` so the Actions UI shows who
triggered the release and which gate let them through.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Iterable
from urllib.error import HTTPError
from urllib.error import URLError
from urllib.request import Request
from urllib.request import urlopen


def emit_error(message: str) -> None:
    print(f"::error::{message}", file=sys.stderr)


def require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        emit_error(f"Missing {name}")
        raise SystemExit(1)
    return value


def parse_list(raw: str) -> list[str]:
    """Split a string on commas / newlines / whitespace into trimmed tokens."""
    if not raw:
        return []
    tokens: list[str] = []
    for chunk in raw.replace(",", "\n").splitlines():
        token = chunk.strip()
        if token and not token.startswith("#"):
            tokens.append(token)
    return tokens


def fetch_role(repo: str, actor: str, token: str) -> str:
    url = f"https://api.github.com/repos/{repo}/collaborators/{actor}/permission"
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "Authorization": f"Bearer {token}",
        "User-Agent": "vss-release-pipeline-authz",
    }
    try:
        with urlopen(Request(url, headers=headers)) as response:
            payload = response.read().decode("utf-8")
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        emit_error(
            f"Permission lookup for @{actor} returned HTTP {exc.code}. "
            f"Either the GITHUB_TOKEN does not grant `read` on the repo "
            f"metadata, or @{actor} is not a collaborator. Body: {body[:200]!r}"
        )
        raise SystemExit(1) from exc
    except URLError as exc:
        emit_error(f"Permission lookup for @{actor} failed: {exc.reason}")
        raise SystemExit(1) from exc

    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        emit_error(f"Permission lookup for @{actor} returned non-JSON response")
        raise SystemExit(1) from exc

    if not isinstance(data, dict):
        emit_error(f"Permission lookup for @{actor} returned unexpected payload")
        raise SystemExit(1)

    role = data.get("role_name") or data.get("permission") or ""
    if not isinstance(role, str) or not role:
        emit_error(f"Permission lookup for @{actor} did not include a role")
        raise SystemExit(1)

    return role.lower()


def write_summary(lines: Iterable[str]) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY", "").strip()
    if not path:
        return
    with open(path, "a", encoding="utf-8") as summary_file:
        summary_file.write("\n".join(lines) + "\n")


def main() -> int:
    actor = require_env("ACTOR").lower()
    repo = require_env("REPO")
    token = require_env("GITHUB_TOKEN")

    required_roles = {role.lower() for role in parse_list(os.environ.get("REQUIRED_ROLES", "admin,maintain"))}
    if not required_roles:
        emit_error("REQUIRED_ROLES resolved to an empty set; refusing to proceed")
        return 1

    allowlist = {token.lower() for token in parse_list(os.environ.get("ALLOWED_ACTORS", ""))}

    if actor in allowlist:
        print(f"OK: @{actor} is on RELEASE_PIPELINE_ALLOWED_ACTORS allowlist.")
        write_summary(
            [
                "### Release pipeline authorization",
                "",
                f"- **Actor:** `@{actor}`",
                "- **Decision:** allowed (explicit allowlist)",
            ]
        )
        return 0

    role = fetch_role(repo, actor, token)
    if role in required_roles:
        print(f"OK: @{actor} has role `{role}` (required one of {sorted(required_roles)}).")
        write_summary(
            [
                "### Release pipeline authorization",
                "",
                f"- **Actor:** `@{actor}`",
                f"- **Role:** `{role}`",
                f"- **Decision:** allowed (role matches {sorted(required_roles)})",
            ]
        )
        return 0

    emit_error(
        f"@{actor} has role `{role}`, which is not in the required set "
        f"{sorted(required_roles)} and is not on the explicit allowlist. "
        "Ask a repository maintainer to either grant you `maintain`/`admin` "
        "or add your login to the `RELEASE_PIPELINE_ALLOWED_ACTORS` "
        "repository variable."
    )
    write_summary(
        [
            "### Release pipeline authorization",
            "",
            f"- **Actor:** `@{actor}`",
            f"- **Role:** `{role}`",
            f"- **Decision:** **denied** (role not in {sorted(required_roles)})",
        ]
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
