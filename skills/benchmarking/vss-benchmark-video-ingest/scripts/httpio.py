# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Public Elasticsearch JSON reads, authentication, and URL redaction."""

from __future__ import annotations

from dataclasses import dataclass
from http.client import IncompleteRead
import json
import os
import time
from typing import Any
import urllib.error
import urllib.parse
import urllib.request

AUTH_ENV_VAR = "VSS_AUTH_TOKEN"


def auth_headers() -> dict[str, str]:
    """Return the Authorization header from the environment, or an empty dict.

    The token value is never returned to callers that log, and never written to
    an artifact. Only presence is ever reported.
    """
    token = os.environ.get(AUTH_ENV_VAR, "").strip()
    return {"Authorization": f"Bearer {token}"} if token else {}


def auth_configured() -> bool:
    return bool(os.environ.get(AUTH_ENV_VAR, "").strip())


def redact_url(url: str) -> str:
    """Strip userinfo and any query value that looks like a credential."""
    try:
        parsed = urllib.parse.urlsplit(url)
    except ValueError:
        return "<unparseable-url>"
    netloc = parsed.hostname or ""
    if parsed.port:
        netloc = f"{netloc}:{parsed.port}"
    if parsed.username:
        netloc = f"***@{netloc}"
    query_pairs = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    safe_query = urllib.parse.urlencode(
        [
            (key, "***" if key.lower() in {"token", "key", "apikey", "api_key", "password"} else value)
            for key, value in query_pairs
        ]
    )
    return urllib.parse.urlunsplit((parsed.scheme, netloc, parsed.path, safe_query, ""))


def parse_endpoint(url: str, *, label: str) -> urllib.parse.SplitResult:
    """Validate that ``url`` is a usable http(s) endpoint."""
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError(f"{label} must be an http:// or https:// URL, got {parsed.scheme or 'no scheme'!r}")
    if not parsed.hostname:
        raise ValueError(f"{label} has no host")
    if parsed.netloc.endswith(":"):
        raise ValueError(f"{label} has a trailing colon with no port")
    return parsed


@dataclass
class JsonResponse:
    status: int
    body: Any
    text: str
    elapsed_sec: float


def request_json(
    url: str,
    *,
    method: str = "GET",
    payload: dict | None = None,
    timeout_sec: float = 30.0,
    extra_headers: dict[str, str] | None = None,
) -> JsonResponse:
    """Issue one JSON request and return status plus parsed body.

    HTTP failures and transport errors are returned as observations. Status 0
    means no complete HTTP response was received. This function never retries;
    the readiness poller may make its next read within its existing deadline.
    """
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    headers.update(auth_headers())
    if extra_headers:
        headers.update(extra_headers)

    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    started = time.monotonic()
    try:
        try:
            with urllib.request.urlopen(request, timeout=timeout_sec) as response:
                raw = response.read().decode("utf-8", errors="replace")
                status = response.status
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            status = exc.code
    except (OSError, IncompleteRead) as exc:
        return JsonResponse(
            status=0, body=None, text=f"ES transport error: {type(exc).__name__}",
            elapsed_sec=time.monotonic() - started,
        )
    elapsed = time.monotonic() - started

    parsed: Any = None
    if raw:
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = None
    return JsonResponse(status=status, body=parsed, text=raw, elapsed_sec=elapsed)
