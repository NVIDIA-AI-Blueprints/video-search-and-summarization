# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Minimal stdlib GitHub REST client (token auth, pagination, retries)."""

from __future__ import annotations

import json
import os
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

API_ROOT = "https://api.github.com"
RETRYABLE_STATUS = {429, 500, 502, 503, 504}


def token() -> str:
    value = os.environ.get("RELEASE_NOTES_GITHUB_TOKEN") or os.environ.get("GITHUB_TOKEN") or ""
    if not value.strip():
        raise SystemExit("RELEASE_NOTES_GITHUB_TOKEN or GITHUB_TOKEN must be set")
    return value.strip()


def request(
    method: str, path: str, body: dict[str, Any] | None = None, retries: int = 3
) -> tuple[Any, dict[str, str]]:
    """Issue one API request; return (parsed JSON, response headers)."""
    url = path if path.startswith("http") else f"{API_ROOT}{path}"
    data = json.dumps(body).encode() if body is not None else None
    headers = {
        "Authorization": f"Bearer {token()}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        # GitHub rejects requests without an identifiable User-Agent.
        "User-Agent": "vss-release-notes",
    }
    if data is not None:
        headers["Content-Type"] = "application/json"
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            with urlopen(Request(url, data=data, headers=headers, method=method)) as resp:
                payload = resp.read()
                parsed = json.loads(payload) if payload.strip() else None
                return parsed, dict(resp.headers)
        except HTTPError as err:
            detail = ""
            try:
                detail = err.read().decode(errors="replace")[:300]
            except Exception:
                pass
            # Primary/secondary rate limits surface as 403 with a retry-after
            # or a rate-limit message — treat those as retryable too.
            rate_limited = err.code == 403 and (
                "rate limit" in detail.lower() or err.headers.get("Retry-After")
            )
            if (err.code in RETRYABLE_STATUS or rate_limited) and attempt < retries:
                wait = int(err.headers.get("Retry-After") or 2**attempt)
                print(f"retrying {method} {url} after HTTP {err.code} ({wait}s): {detail[:120]}")
                time.sleep(min(wait, 120))
                last_error = err
                continue
            print(f"ERROR: {method} {url} -> HTTP {err.code}: {detail}")
            raise
        except URLError as err:
            if attempt < retries:
                time.sleep(2**attempt)
                last_error = err
                continue
            raise
    raise RuntimeError(f"unreachable: {last_error}")


def get(path: str) -> Any:
    parsed, _ = request("GET", path)
    return parsed


def get_paginated(path: str, per_page: int = 100, max_pages: int = 20) -> list[Any]:
    """GET all pages of a list endpoint (follows the Link: rel=next header)."""
    sep = "&" if "?" in path else "?"
    url: str | None = f"{API_ROOT}{path}{sep}per_page={per_page}"
    items: list[Any] = []
    for _ in range(max_pages):
        if url is None:
            break
        parsed, headers = request("GET", url)
        items.extend(parsed or [])
        url = _next_link(headers.get("Link", ""))
    return items


def _next_link(link_header: str) -> str | None:
    for part in link_header.split(","):
        if 'rel="next"' in part:
            return part.split(";")[0].strip().strip("<>")
    return None
