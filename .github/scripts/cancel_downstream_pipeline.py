#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Cancel a downstream GitLab pipeline that this GitHub job already started.

Used when the GitHub Actions job is cancelled (PR closed, superseded
mirror push, or a human cancel). The poller is killed; GitLab would
otherwise keep running.

GitHub only publishes a step's ``GITHUB_OUTPUT`` when that step finishes,
so this script also reads the fsynced handoff file the trigger writes
immediately after GitLab creates the pipeline.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any
from urllib.error import ContentTooShortError
from urllib.error import HTTPError
from urllib.error import URLError
from urllib.parse import quote
from urllib.request import Request
from urllib.request import urlopen

sys.path.insert(0, str(Path(__file__).resolve().parent))

from trigger_downstream_pipeline import add_mask  # noqa: E402
from trigger_downstream_pipeline import api_base_url  # noqa: E402
from trigger_downstream_pipeline import connection_error_detail  # noqa: E402
from trigger_downstream_pipeline import emit_error  # noqa: E402
from trigger_downstream_pipeline import handoff_path  # noqa: E402
from trigger_downstream_pipeline import require_env  # noqa: E402


def cancel_pipeline(
    base_url: str,
    token: str,
    pipeline_id: int,
    *,
    project_id: int | None = None,
    project_path: str = "",
    open_func: Any = urlopen,
) -> str:
    """POST GitLab's pipeline cancel. 404/409 mean it is already gone."""
    if project_id is not None:
        project = str(int(project_id))
    elif project_path:
        project = quote(project_path, safe="")
    else:
        emit_error("Need a GitLab project id or project path to cancel")
        raise SystemExit(1)
    url = f"{base_url}/projects/{project}/pipelines/{int(pipeline_id)}/cancel"
    request = Request(
        url,
        data=b"",
        method="POST",
        headers={
            "PRIVATE-TOKEN": token,
            "Accept": "application/json",
        },
    )
    try:
        with open_func(request) as response:
            response.read()
    except HTTPError as exc:
        if exc.code in {404, 409}:
            return f"already finished ({exc.code})"
        emit_error(f"Pipeline cancel failed with status {exc.code}")
        raise SystemExit(1) from exc
    except (URLError, ContentTooShortError) as exc:
        emit_error(
            "Pipeline cancel failed due to a connection error: "
            + connection_error_detail(exc)
        )
        raise SystemExit(1) from exc
    return "cancelled"


def resolve_pipeline_ids() -> tuple[str, str]:
    """Prefer step env; fall back to the trigger step's fsynced handoff file."""
    env_project = os.environ.get("DOWNSTREAM_PROJECT_ID", "").strip()
    env_pipeline = os.environ.get("DOWNSTREAM_PIPELINE_ID", "").strip()
    if env_project and env_pipeline:
        return env_project, env_pipeline
    path = Path(handoff_path())
    payload: dict[str, Any] = {}
    if path.is_file():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            loaded = {}
        if isinstance(loaded, dict):
            payload = loaded
    project_id = env_project or str(payload.get("project_id") or "").strip()
    pipeline_id = env_pipeline or str(payload.get("pipeline_id") or "").strip()
    return project_id, pipeline_id


def main() -> int:
    raw_url = require_env("DOWNSTREAM_CI_URL")
    token = require_env("DOWNSTREAM_CI_TOKEN")
    project_path = os.environ.get("DOWNSTREAM_PROJECT_PATH", "").strip()
    project_id, pipeline_id = resolve_pipeline_ids()
    if not pipeline_id:
        print("No downstream pipeline id; trigger never published one")
        return 0
    base_url = api_base_url(raw_url)
    for value in (raw_url, base_url, token, project_path):
        add_mask(value)
    outcome = cancel_pipeline(
        base_url,
        token,
        int(pipeline_id),
        project_id=int(project_id) if project_id.isdigit() else None,
        project_path=project_path,
    )
    print(f"Downstream pipeline {pipeline_id}: {outcome}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
