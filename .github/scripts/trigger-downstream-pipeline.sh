#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import json
import os
import socket
import ssl
import sys
from typing import Any
from urllib.error import ContentTooShortError
from urllib.error import HTTPError
from urllib.error import URLError
from urllib.parse import quote
from urllib.parse import urlencode
from urllib.request import Request
from urllib.request import urlopen


def emit_error(message: str) -> None:
    print(f"::error::{message}", file=sys.stderr)


def add_mask(value: str) -> None:
    if value:
        print(f"::add-mask::{value}")


def require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        emit_error(f"Missing {name}")
        raise SystemExit(1)
    return value


def api_base_url(raw_url: str) -> str:
    base = raw_url.rstrip("/")
    if not base.endswith("/api/v4"):
        base = f"{base}/api/v4"
    return base


def connection_error_detail(exc: URLError | ContentTooShortError) -> str:
    """Return a safe, non-secret hint about the connection failure."""
    if isinstance(exc, ContentTooShortError):
        return "truncated response body"

    reason = exc.reason

    if isinstance(reason, (TimeoutError, socket.timeout)):
        return "timeout"
    if isinstance(reason, socket.gaierror):
        errno = getattr(reason, "errno", None)
        suffix = f", errno {errno}" if errno is not None else ""
        return f"DNS resolution error ({reason.__class__.__name__}{suffix})"
    if isinstance(reason, ssl.SSLCertVerificationError):
        return "TLS certificate verification failed"
    if isinstance(reason, ssl.SSLError):
        return f"TLS error ({reason.__class__.__name__})"
    if isinstance(reason, ConnectionRefusedError):
        return "connection refused"
    if isinstance(reason, OSError):
        errno = getattr(reason, "errno", None)
        suffix = f", errno {errno}" if errno is not None else ""
        return f"network error ({reason.__class__.__name__}{suffix})"
    if isinstance(reason, str):
        lowered = reason.lower()
        if "timed out" in lowered:
            return "timeout"
        if "tunnel connection failed" in lowered or "proxy" in lowered:
            return "proxy error"
        if "unknown url type" in lowered or "no host given" in lowered:
            return "invalid URL configuration"
        return "network error (string reason)"

    return f"network error ({reason.__class__.__name__})"


def request_json(
    action: str,
    url: str,
    token: str,
    data: bytes | None = None,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    if headers is None:
        headers = {
            "PRIVATE-TOKEN": token,
            "Accept": "application/json",
        }
        if data is not None:
            headers["Content-Type"] = "application/x-www-form-urlencoded"

    request = Request(url, data=data, headers=headers)
    try:
        with urlopen(request) as response:
            payload = response.read().decode("utf-8")
    except HTTPError as exc:
        # Extract just the "message" / "error" field from the JSON body
        # (GitLab convention). We do NOT include the raw body because it
        # sometimes echoes the full request URL, which is a secret. The
        # message field itself is safe - typically "Reference not found",
        # "Missing CI config file", "insufficient_scope", etc.
        reason = ""
        try:
            body = exc.read().decode("utf-8", errors="replace")
            body_json = json.loads(body) if body else {}
            if isinstance(body_json, dict):
                msg = body_json.get("message") or body_json.get("error")
                if isinstance(msg, str):
                    reason = msg
                elif isinstance(msg, dict):
                    # GitLab sometimes returns a dict of field: [errors]
                    reason = ", ".join(f"{k}: {v}" for k, v in msg.items())
        except (UnicodeDecodeError, json.JSONDecodeError):
            pass
        suffix = f": {reason}" if reason else ""
        emit_error(f"{action} failed with status {exc.code}{suffix}")
        raise SystemExit(1) from exc
    except (URLError, ContentTooShortError) as exc:
        emit_error(f"{action} failed due to a connection error: {connection_error_detail(exc)}")
        raise SystemExit(1) from exc

    try:
        parsed = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        _ = exc
        emit_error(f"{action} returned an unexpected response")
        raise SystemExit(1) from exc

    if not isinstance(parsed, dict):
        emit_error(f"{action} returned an unexpected response")
        raise SystemExit(1)

    return parsed


def fetch_project_id(base_url: str, token: str, project_path: str) -> int:
    encoded_project_path = quote(project_path, safe="")
    response = request_json("Project lookup", f"{base_url}/projects/{encoded_project_path}", token)
    return int(response["id"])


def trigger_pipeline(
    base_url: str,
    token: str,
    project_id: int,
    ref: str,
    variable_name: str,
    commit_sha: str,
) -> dict[str, Any]:
    payload_pairs: list[tuple[str, str]] = [
        ("ref", ref),
        ("variables[][key]", variable_name),
        ("variables[][value]", commit_sha),
    ]
    for branch_var in ("VSS_COMPARE_BRANCH", "VSS_TARGET_BRANCH"):
        payload_pairs += [("variables[][key]", branch_var), ("variables[][value]", os.environ.get(branch_var, ""))]
    payload = urlencode(payload_pairs).encode("utf-8")
    return request_json("Pipeline trigger", f"{base_url}/projects/{project_id}/pipeline", token, data=payload)


def write_summary(message: str) -> None:
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY", "").strip()
    if not summary_path:
        return
    with open(summary_path, "a", encoding="utf-8") as summary_file:
        summary_file.write(f"{message}\n")


def write_output(key: str, value: str) -> None:
    output_path = os.environ.get("GITHUB_OUTPUT", "").strip()
    if not output_path or not value:
        return
    with open(output_path, "a", encoding="utf-8") as output_file:
        output_file.write(f"{key}={value}\n")


def main() -> int:
    try:
        raw_url = require_env("DOWNSTREAM_CI_URL")
        base_url = api_base_url(raw_url)
        token = require_env("DOWNSTREAM_CI_TOKEN")
        project_path = require_env("DOWNSTREAM_PROJECT_PATH")
        commit_sha = require_env("GITHUB_SHA")
        ref = os.environ.get("DOWNSTREAM_REF", "main")
        variable_name = os.environ.get("DOWNSTREAM_SUBMODULE_HASH_VARIABLE", "VSS_SUBMODULE_HASH")

        # Mask the raw URL (e.g. "https://gitlab.example.com"), the API
        # base URL (with "/api/v4" appended), and every path component of
        # the project so no combination of them can leak into the log.
        for value in (raw_url, base_url, token, project_path, ref, variable_name):
            add_mask(value)
        for segment in project_path.split("/"):
            add_mask(segment)

        project_id = fetch_project_id(base_url, token, project_path)
        pipeline = trigger_pipeline(base_url, token, project_id, ref, variable_name, commit_sha)

        pipeline_iid = str(pipeline.get("iid") or pipeline.get("id") or "")
        pipeline_id = str(pipeline.get("id") or "")
        pipeline_sha = str(pipeline.get("sha") or "")
        pipeline_url = str(pipeline.get("web_url") or "")
        pipeline_created_at = str(pipeline.get("created_at") or "")

        # The pipeline URL includes the downstream host and project path,
        # both of which are treated as secrets.
        if pipeline_url:
            add_mask(pipeline_url)

        # Log identifiers only - no URL, no project path.
        print(f"Triggered downstream pipeline #{pipeline_iid} (id={pipeline_id}, sha={pipeline_sha})")

        sha_short = pipeline_sha[:8] if pipeline_sha else ""
        summary_lines = ["### Downstream pipeline triggered", ""]
        if pipeline_iid:
            summary_lines.append(f"- **Pipeline:** #{pipeline_iid}")
        if pipeline_id:
            summary_lines.append(f"- **Global ID:** `{pipeline_id}`")
        if pipeline_sha:
            summary_lines.append(f"- **Commit SHA:** `{sha_short}` (`{pipeline_sha}`)")
        if pipeline_created_at:
            summary_lines.append(f"- **Created at:** {pipeline_created_at}")
        write_summary("\n".join(summary_lines))

        # Expose identifiers to the poll step in the same job. Do NOT
        # write the pipeline URL here - it is a secret and would appear
        # in any caller that echoes the output.
        write_output("pipeline_iid", pipeline_iid)
        write_output("pipeline_id", pipeline_id)
        write_output("pipeline_sha", pipeline_sha)
        write_output("pipeline_created_at", pipeline_created_at)
        write_output("project_id", str(project_id))
        return 0
    except SystemExit:
        raise
    except Exception as exc:
        _ = exc
        emit_error("Unexpected failure while triggering the downstream pipeline")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
