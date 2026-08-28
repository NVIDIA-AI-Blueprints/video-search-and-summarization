#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Cancel a downstream GitLab pipeline that this GitHub job already started.

Used when the GitHub Actions job is cancelled (PR closed, superseded
mirror push, or a human cancel). The poller is killed; GitLab would
otherwise keep running.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any
from urllib.error import ContentTooShortError
from urllib.error import HTTPError
from urllib.error import URLError
from urllib.request import Request
from urllib.request import urlopen

sys.path.insert(0, str(Path(__file__).resolve().parent))

from trigger_downstream_pipeline import add_mask  # noqa: E402
from trigger_downstream_pipeline import api_base_url  # noqa: E402
from trigger_downstream_pipeline import connection_error_detail  # noqa: E402
from trigger_downstream_pipeline import emit_error  # noqa: E402
from trigger_downstream_pipeline import require_env  # noqa: E402


def cancel_pipeline(
    base_url: str,
    token: str,
    project_id: int,
    pipeline_id: int,
    open_func: Any = urlopen,
) -> str:
    """POST GitLab's pipeline cancel. 404/409 mean it is already gone."""
    url = (
        f"{base_url}/projects/{int(project_id)}/pipelines/{int(pipeline_id)}/cancel"
    )
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


def main() -> int:
    raw_url = require_env("DOWNSTREAM_CI_URL")
    token = require_env("DOWNSTREAM_CI_TOKEN")
    project_id = int(require_env("DOWNSTREAM_PROJECT_ID"))
    pipeline_id = int(require_env("DOWNSTREAM_PIPELINE_ID"))
    base_url = api_base_url(raw_url)
    for value in (raw_url, base_url, token):
        add_mask(value)
    outcome = cancel_pipeline(base_url, token, project_id, pipeline_id)
    print(f"Downstream pipeline {pipeline_id}: {outcome}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
