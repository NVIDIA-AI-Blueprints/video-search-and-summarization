# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Task-local, bounded HTTP evidence for VLM consumers, captured before parsing."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
from typing import Any

_MAX_BODY_BYTES = 64 * 1024
_responses: ContextVar[list[dict[str, Any]] | None] = ContextVar("vlm_http_responses", default=None)


@contextmanager
def capture_http_responses():
    responses: list[dict[str, Any]] = []
    token = _responses.set(responses)
    try:
        yield responses
    finally:
        _responses.reset(token)


def record_http_response(response: Any) -> None:
    """Record status and bounded raw body; diagnostic failures never affect inference."""
    responses = _responses.get()
    if responses is None:
        return
    try:
        body = response.content
        responses.append(
            {
                "http_status": response.status_code,
                "raw_http_response": body[:_MAX_BODY_BYTES].decode("utf-8", errors="replace"),
                "body_bytes": len(body),
                "body_truncated": len(body) > _MAX_BODY_BYTES,
                "body_sha256": hashlib.sha256(body).hexdigest(),
            }
        )
    except Exception:
        # Inference remains authoritative even when a custom response object
        # cannot supply diagnostics. Do not retain request headers or secrets.
        return
