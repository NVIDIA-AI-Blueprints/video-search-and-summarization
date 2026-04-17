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
) -> dict[str, Any]:
    payload = urlencode(
        [
            ("ref", ref),
            ("variables[][key]", variable_name),
            ("variables[][value]", commit_sha),
        ]
    ).encode("utf-8")
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


# Context string used on GitHub commit statuses to track the downstream
# pipeline. The follow-up workflow looks for this exact context to discover
# pipelines that still need to be checked.
DOWNSTREAM_STATUS_CONTEXT = "ci/downstream-pipeline"


def post_github_status(
    repo: str,
    sha: str,
    github_token: str,
    state: str,
    target_url: str,
    description: str,
) -> None:
    url = f"https://api.github.com/repos/{repo}/statuses/{sha}"
    body = json.dumps(
        {
            "state": state,
            "target_url": target_url,
            "description": description[:140],
            "context": DOWNSTREAM_STATUS_CONTEXT,
        }
    ).encode("utf-8")
    headers = {
        "Authorization": f"Bearer {github_token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "Content-Type": "application/json",
        "User-Agent": "trigger-downstream-pipeline",
    }
    try:
        request_json("GitHub status update", url, token="", data=body, headers=headers)
    except SystemExit:
        # Failing to post a status should not fail the pipeline trigger itself;
        # the pipeline is already running on GitLab at this point.
        print("::warning::Failed to post GitHub commit status for downstream pipeline")


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
        # both of which are treated as secrets. Mask it before anything
        # else might echo it.
        if pipeline_url:
            add_mask(pipeline_url)

        # Log identifiers only (numbers + commit SHAs). No URL, no
        # project path - the link is carried on the GitHub commit status
        # via target_url, which is not part of the job log.
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
        summary_lines.append("")
        summary_lines.append("Follow the `ci/downstream-pipeline` commit status for the clickable link to the downstream pipeline.")
        write_summary("\n".join(summary_lines))

        write_output("pipeline_iid", pipeline_iid)
        write_output("pipeline_id", pipeline_id)
        write_output("pipeline_sha", pipeline_sha)
        # pipeline_url is intentionally masked above and exposed via
        # commit status target_url rather than a job output to reduce
        # the risk of a later step echoing it in plaintext.
        write_output("pipeline_created_at", pipeline_created_at)

        github_token = os.environ.get("GITHUB_TOKEN", "").strip()
        github_repo = os.environ.get("GITHUB_REPOSITORY", "").strip()
        if github_token and github_repo and pipeline_id and pipeline_url:
            # Machine-readable marker. The follow-up workflow parses
            # "pipeline_id=<N>" out of description to re-query GitLab.
            description = f"pipeline_id={pipeline_id} iid={pipeline_iid} (awaiting downstream CI)"
            post_github_status(
                repo=github_repo,
                sha=commit_sha,
                github_token=github_token,
                state="pending",
                target_url=pipeline_url,
                description=description,
            )
        return 0
    except SystemExit:
        raise
    except Exception as exc:
        _ = exc
        emit_error("Unexpected failure while triggering the downstream pipeline")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
