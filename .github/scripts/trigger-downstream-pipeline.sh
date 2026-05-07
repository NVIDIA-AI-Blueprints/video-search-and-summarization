#!/usr/bin/env python3

import json
import os
import re
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


def env_prefix() -> str:
    if len(sys.argv) > 2:
        emit_error(f"Usage: {sys.argv[0]} [ENV_PREFIX]")
        raise SystemExit(1)

    if len(sys.argv) == 1:
        return "DOWNSTREAM"

    prefix = sys.argv[1].strip().upper().replace("-", "_").rstrip("_")
    if not prefix:
        emit_error("ENV_PREFIX must not be empty")
        raise SystemExit(1)

    return prefix


def prefixed_env(prefix: str, suffix: str) -> str:
    return f"{prefix}_{suffix}"


def parse_extra_variables(prefix: str) -> list[tuple[str, str]]:
    raw = os.environ.get(prefixed_env(prefix, "VARIABLES"), "")
    variables: list[tuple[str, str]] = []
    for line_number, raw_line in enumerate(raw.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            emit_error(f"{prefixed_env(prefix, 'VARIABLES')} line {line_number} must be KEY=value")
            raise SystemExit(1)
        key, value = line.split("=", 1)
        key = key.strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            emit_error(f"{prefixed_env(prefix, 'VARIABLES')} line {line_number} has invalid key {key!r}")
            raise SystemExit(1)
        variables.append((key, value))
    return variables


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
    target_branch: str,
    compare_branch: str,
    extra_variables: list[tuple[str, str]],
) -> dict[str, Any]:
    payload_pairs: list[tuple[str, str]] = [
        ("ref", ref),
        ("variables[][key]", variable_name),
        ("variables[][value]", commit_sha),
        ("variables[][key]", "VSS_TARGET_BRANCH"),
        ("variables[][value]", target_branch),
        ("variables[][key]", "VSS_COMPARE_BRANCH"),
        ("variables[][value]", compare_branch),
    ]
    for key, value in extra_variables:
        payload_pairs.extend(
            [
                ("variables[][key]", key),
                ("variables[][value]", value),
            ]
        )
    payload = urlencode(payload_pairs).encode("utf-8")
    return request_json("Pipeline trigger", f"{base_url}/projects/{project_id}/pipeline", token, data=payload)


def fetch_pr_base_ref(repo: str, pr_number: int, token: str) -> str:
    """Fetch a PR's base ref from the GitHub REST API.

    Uses the workflow GITHUB_TOKEN. Returns an empty string on any failure -
    callers should fall back to a sane default rather than aborting the
    pipeline trigger.
    """
    if not repo or pr_number <= 0:
        return ""
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "vss-trigger-downstream-pipeline",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = Request(f"https://api.github.com/repos/{repo}/pulls/{pr_number}", headers=headers)
    try:
        with urlopen(request) as response:
            payload = response.read().decode("utf-8")
    except (HTTPError, URLError, ContentTooShortError):
        return ""
    try:
        data = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return ""
    if not isinstance(data, dict):
        return ""
    base = data.get("base")
    if isinstance(base, dict):
        ref = base.get("ref")
        if isinstance(ref, str):
            return ref
    return ""


def resolve_branches() -> tuple[str, str]:
    """Resolve (target_branch, compare_branch) for the downstream pipeline.

    On a push to a copy-pr-bot synthetic branch ``pull-request/<N>``:
        target  = the PR's base ref (e.g. ``release/3.2.0``)
        compare = ``pull-request/<N>`` (the synthetic branch under test)

    For pushes to regular branches (``main``, ``develop``, ...), both default
    to ``GITHUB_REF_NAME`` so downstream consumers always see something
    meaningful.
    """
    ref_name = os.environ.get("GITHUB_REF_NAME", "").strip()
    pr_match = re.fullmatch(r"pull-request/(\d+)", ref_name)
    if not pr_match:
        return ref_name, ref_name
    pr_number = int(pr_match.group(1))
    repo = os.environ.get("GITHUB_REPOSITORY", "").strip()
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    base_ref = fetch_pr_base_ref(repo, pr_number, token)
    if not base_ref:
        # Couldn't resolve via the API - keep the synthetic branch as the
        # target so we never silently send a wrong release branch downstream.
        print(
            f"::warning::Could not resolve base ref for PR #{pr_number}; "
            "falling back to GITHUB_REF_NAME for VSS_TARGET_BRANCH"
        )
        return ref_name, ref_name
    return base_ref, ref_name


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
        prefix = env_prefix()
        raw_url = require_env(prefixed_env(prefix, "CI_URL"))
        base_url = api_base_url(raw_url)
        token = require_env(prefixed_env(prefix, "CI_TOKEN"))
        project_path = require_env(prefixed_env(prefix, "PROJECT_PATH"))
        commit_sha = require_env("GITHUB_SHA")
        ref = os.environ.get(prefixed_env(prefix, "REF"), "main")
        variable_name = os.environ.get(prefixed_env(prefix, "SUBMODULE_HASH_VARIABLE"), "VSS_SUBMODULE_HASH")
        extra_variables = parse_extra_variables(prefix)

        # Mask the raw URL (e.g. "https://gitlab.example.com"), the API
        # base URL (with "/api/v4" appended), and every path component of
        # the project so no combination of them can leak into the log.
        for value in (raw_url, base_url, token, project_path, ref, variable_name):
            add_mask(value)
        for _, value in extra_variables:
            add_mask(value)
        for segment in project_path.split("/"):
            add_mask(segment)

        target_branch, compare_branch = resolve_branches()

        project_id = fetch_project_id(base_url, token, project_path)
        pipeline = trigger_pipeline(
            base_url,
            token,
            project_id,
            ref,
            variable_name,
            commit_sha,
            target_branch,
            compare_branch,
            extra_variables,
        )

        pipeline_iid = str(pipeline.get("iid") or pipeline.get("id") or "")
        pipeline_id = str(pipeline.get("id") or "")
        pipeline_sha = str(pipeline.get("sha") or "")
        pipeline_url = str(pipeline.get("web_url") or "")
        pipeline_created_at = str(pipeline.get("created_at") or "")

        # The pipeline URL includes the downstream host and project path,
        # both of which are treated as secrets.
        if pipeline_url:
            add_mask(pipeline_url)

        # Log identifiers only - no URL, no project path. Echo the
        # submodule SHA and resolved branches so it is obvious which
        # commit and branches the downstream pipeline is testing
        # (none of these are secrets - the SHA and branches all come
        # from the public GitHub event that triggered this workflow).
        print(f"Triggered downstream pipeline #{pipeline_iid} (id={pipeline_id}, sha={pipeline_sha})")
        print(f"  {variable_name}={commit_sha}")
        print(f"  VSS_TARGET_BRANCH={target_branch}")
        print(f"  VSS_COMPARE_BRANCH={compare_branch}")

        sha_short = pipeline_sha[:8] if pipeline_sha else ""
        commit_sha_short = commit_sha[:8] if commit_sha else ""
        summary_lines = ["### Downstream pipeline triggered", ""]
        if pipeline_iid:
            summary_lines.append(f"- **Pipeline:** #{pipeline_iid}")
        if pipeline_id:
            summary_lines.append(f"- **Global ID:** `{pipeline_id}`")
        if pipeline_sha:
            summary_lines.append(f"- **Downstream commit SHA:** `{sha_short}` (`{pipeline_sha}`)")
        if commit_sha:
            summary_lines.append(f"- **{variable_name}:** `{commit_sha_short}` (`{commit_sha}`)")
        if target_branch:
            summary_lines.append(f"- **VSS_TARGET_BRANCH:** `{target_branch}`")
        if compare_branch:
            summary_lines.append(f"- **VSS_COMPARE_BRANCH:** `{compare_branch}`")
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
