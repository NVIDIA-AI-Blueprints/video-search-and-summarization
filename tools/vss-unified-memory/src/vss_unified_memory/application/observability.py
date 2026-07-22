# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Shared helpers for operation telemetry and optional JSONL logging."""

from __future__ import annotations

import json
import logging
import math
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def utc_timestamp() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def sha256_hex(text: str) -> str:
    return sha256(text.encode()).hexdigest()


def estimate_tokens_from_chars(char_count: int) -> int:
    if char_count <= 0:
        return 0
    return math.ceil(char_count / 4)


def safe_preview(text: str, *, max_length: int = 80) -> str:
    normalized = " ".join(text.split())
    if len(normalized) <= max_length:
        return normalized
    return f"{normalized[: max_length - 3]}..."


def query_text_observability_fields(
    query_text: str | None,
    *,
    include_preview: bool,
    preview_max_length: int = 80,
) -> dict[str, str | int]:
    if not query_text:
        return {}
    fields: dict[str, str | int] = {
        "query_text_chars": len(query_text),
        "query_text_hash": sha256_hex(query_text),
    }
    if include_preview:
        fields["query_text_preview"] = safe_preview(query_text, max_length=preview_max_length)
    return fields


@dataclass
class OperationTelemetry:
    latency_ms: dict[str, float] = field(default_factory=dict)
    candidate_summary_ids: tuple[str, ...] = ()

    def record_latency(self, key: str, milliseconds: float) -> None:
        self.latency_ms[key] = milliseconds

    @contextmanager
    def measure(self, key: str) -> Iterator[None]:
        start = time.perf_counter()
        try:
            yield
        finally:
            self.record_latency(key, (time.perf_counter() - start) * 1000.0)

    def get_latency(self, key: str) -> float | None:
        return self.latency_ms.get(key)


def append_observability_log(
    path: Path | None,
    *,
    tool_name: str,
    status: str,
    summary_id: str | None = None,
    record_id: str | None = None,
    observability: Mapping[str, Any] | None = None,
) -> None:
    if path is None:
        return
    record: dict[str, Any] = {
        "timestamp": utc_timestamp(),
        "tool_name": tool_name,
        "status": status,
    }
    if summary_id is not None:
        record["summary_id"] = summary_id
    if record_id is not None:
        record["record_id"] = record_id
    if observability is not None:
        record["observability"] = observability
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, separators=(",", ":"), default=str) + "\n")
    except OSError:
        logger.exception("failed to append observability log to %s", path)
