#!/usr/bin/env python3

import json
import os
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


def request_json(action: str, url: str, token: str, data: bytes | None = None) -> dict[str, Any]:
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
        _ = exc.read()
        emit_error(f"{action} failed with status {exc.code}")
        raise SystemExit(1) from exc
    except (URLError, ContentTooShortError) as exc:
        _ = exc
        emit_error(f"{action} failed due to a connection error")
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
) -> int:
    payload = urlencode(
        [
            ("ref", ref),
            ("variables[][key]", variable_name),
            ("variables[][value]", commit_sha),
        ]
    ).encode("utf-8")
    response = request_json("Pipeline trigger", f"{base_url}/projects/{project_id}/pipeline", token, data=payload)
    return int(response.get("iid") or response["id"])


def write_summary(message: str) -> None:
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY", "").strip()
    if not summary_path:
        return
    with open(summary_path, "a", encoding="utf-8") as summary_file:
        summary_file.write(f"{message}\n")


def main() -> int:
    try:
        base_url = api_base_url(require_env("DOWNSTREAM_CI_URL"))
        token = require_env("DOWNSTREAM_CI_TOKEN")
        project_path = require_env("DOWNSTREAM_PROJECT_PATH")
        commit_sha = require_env("GITHUB_SHA")
        ref = os.environ.get("DOWNSTREAM_REF", "main")
        variable_name = os.environ.get("DOWNSTREAM_SUBMODULE_HASH_VARIABLE", "VSS_SUBMODULE_HASH")

        for value in (base_url, token, project_path, ref, variable_name):
            add_mask(value)

        project_id = fetch_project_id(base_url, token, project_path)
        pipeline_number = trigger_pipeline(base_url, token, project_id, ref, variable_name, commit_sha)

        message = f"Triggered pipeline number {pipeline_number}"
        print(message)
        write_summary(message)
        return 0
    except SystemExit:
        raise
    except Exception as exc:
        _ = exc
        emit_error("Unexpected failure while triggering the downstream pipeline")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
