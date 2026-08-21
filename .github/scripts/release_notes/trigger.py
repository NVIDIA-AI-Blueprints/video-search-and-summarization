#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Trigger the downstream release-notes pipeline for a tag push.

Runs GitHub-side in release-notes.yml. Fires the same GitLab
trigger-token API as trigger-downstream-pipeline.sh, but for tag refs
and without polling: it passes RELEASE_NOTES=true and TAG_NAME so the
downstream pipeline runs only the notes job, then exits.
"""

from __future__ import annotations

import json
import os
import sys
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen


def require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(f"{name} must be set")
    return value


def add_mask(value: str) -> None:
    if value:
        print(f"::add-mask::{value}")


def gitlab_json(url: str, token: str, data: bytes | None = None) -> dict:
    request = Request(url, data=data, headers={"PRIVATE-TOKEN": token})
    with urlopen(request) as resp:
        return json.loads(resp.read())


def main() -> int:
    raw_url = require_env("DOWNSTREAM_CI_URL").rstrip("/")
    token = require_env("DOWNSTREAM_CI_TOKEN")
    project_path = require_env("DOWNSTREAM_PROJECT_PATH")
    ref = os.environ.get("DOWNSTREAM_REF", "main")
    tag = require_env("GITHUB_REF_NAME")
    commit_sha = require_env("GITHUB_SHA")

    base_url = raw_url if raw_url.endswith("/api/v4") else f"{raw_url}/api/v4"
    for value in (raw_url, base_url, token, project_path):
        add_mask(value)
    for segment in project_path.split("/"):
        add_mask(segment)

    project_id = int(gitlab_json(f"{base_url}/projects/{quote(project_path, safe='')}", token)["id"])
    variables = {
        "RELEASE_NOTES": "true",
        "TAG_NAME": tag,
        "VSS_SUBMODULE_HASH": commit_sha,
    }
    payload_pairs: list[tuple[str, str]] = [("ref", ref)]
    for key, value in variables.items():
        payload_pairs.extend([("variables[][key]", key), ("variables[][value]", value)])
    pipeline = gitlab_json(
        f"{base_url}/projects/{project_id}/pipeline",
        token,
        data=urlencode(payload_pairs).encode(),
    )

    pipeline_url = str(pipeline.get("web_url") or "")
    add_mask(pipeline_url)
    print(f"Triggered release-notes pipeline for {tag} (pipeline id {pipeline.get('id')})")
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY", "").strip()
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as handle:
            handle.write(f"Release-notes pipeline triggered for `{tag}`.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
