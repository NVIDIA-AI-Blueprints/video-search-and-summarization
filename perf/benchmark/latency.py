# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Parse latency-monitor tables and subtract raw-topic latency."""

from __future__ import annotations

import re
from typing import Mapping

OUTPUT_TOPICS = ("frames", "behavior", "events", "incidents")
COMPLETE_TOPICS = ("raw",) + OUTPUT_TOPICS
CORRECTED_FIELDS = ("min_s", "max_s", "avg_s", "p50_s", "p95_s", "p99_s")
TOPIC_TITLES = {
    "RAW MESSAGES": "raw",
    "FRAMES MESSAGES": "frames",
    "BEHAVIOR MESSAGES": "behavior",
    "EVENTS MESSAGES": "events",
    "INCIDENTS MESSAGES": "incidents",
}
_NUMBER = re.compile(r"^-?\d+(?:\.\d+)?$")
_REPORT_HEADER = "Statistics Report"


def parse_latency_report(text: str) -> dict[str, dict[str, float]]:
    """Aggregate sensor rows from the last cumulative latency-monitor dump."""
    last: dict[str, dict[str, float]] = {}
    batch: dict[str, dict[str, float]] = {}
    topic: str | None = None
    fields: tuple[str, ...] | None = None
    for raw_line in text.splitlines():
        title = raw_line.strip()
        if title.startswith(_REPORT_HEADER):
            last = _keep_report(last, batch)
            batch = {}
            topic = None
            fields = None
            continue
        if title in TOPIC_TITLES:
            topic = TOPIC_TITLES[title]
            fields = None
            continue
        if topic is None or not raw_line.startswith("|"):
            continue
        header = _header_fields(raw_line)
        if header is not None:
            fields = header
            continue
        row = _parse_row(raw_line, fields)
        if row is None:
            continue
        batch[topic] = _merge(batch.get(topic), row)
    return _keep_report(last, batch)


def _report_complete(batch: Mapping[str, Mapping[str, float]]) -> bool:
    return all(name in batch for name in COMPLETE_TOPICS)


def _keep_report(
    last: dict[str, dict[str, float]],
    batch: dict[str, dict[str, float]],
) -> dict[str, dict[str, float]]:
    if not batch:
        return last
    if _report_complete(batch) or not last:
        return batch
    return last


def correct_topics(topics: Mapping[str, Mapping[str, float]]) -> dict[str, object]:
    """Subtract the raw-topic summary from each Behavior Analytics output topic."""
    raw = topics.get("raw")
    if not raw or raw.get("count", 0) <= 0:
        return {
            "success": False,
            "error": "raw topic has no messages",
            "raw": dict(raw) if raw else None,
            "topics": {},
        }

    corrected: dict[str, dict[str, object]] = {}
    negatives: list[str] = []
    for name in OUTPUT_TOPICS:
        measured = topics.get(name)
        if not measured or measured.get("count", 0) <= 0:
            corrected[name] = {"present": False}
            continue
        entry: dict[str, object] = {"present": True, "total": dict(measured)}
        values: dict[str, float | None] = {}
        for field in CORRECTED_FIELDS:
            if field not in measured or field not in raw:
                values[field] = None
                continue
            value = round(float(measured[field]) - float(raw[field]), 6)
            values[field] = value
            if value < 0:
                negatives.append(f"{name}.{field}")
        entry["corrected"] = values
        corrected[name] = entry

    return {
        "success": not negatives and any(item.get("present") for item in corrected.values()),
        "error": f"negative corrected latency: {', '.join(negatives)}" if negatives else None,
        "raw": dict(raw),
        "topics": corrected,
    }


_DEFAULT_FIELDS = (
    "count",
    "min_s",
    "max_s",
    "avg_s",
    "size_kb",
    "p50_s",
    "p95_s",
    "p99_s",
)
_HEADER_FIELDS = {
    "count": "count",
    "min(s)": "min_s",
    "max(s)": "max_s",
    "avg(s)": "avg_s",
    "size(kb)": "size_kb",
}


def _cells(line: str) -> list[str]:
    return [cell.strip() for cell in line.split("|") if cell.strip()]


def _header_fields(line: str) -> tuple[str, ...] | None:
    cells = _cells(line)
    if not cells or cells[0].lower() != "sensor":
        return None
    fields: list[str] = []
    for cell in cells[1:]:
        name = _column_field(cell)
        if name is None:
            return None
        fields.append(name)
    if "count" not in fields:
        return None
    return tuple(fields)


def _column_field(cell: str) -> str | None:
    key = cell.strip().lower().replace(" ", "")
    if key in _HEADER_FIELDS:
        return _HEADER_FIELDS[key]
    match = re.fullmatch(r"p(\d+)\(s\)", key)
    if match:
        return f"p{match.group(1)}_s"
    return None


def _parse_row(line: str, fields: tuple[str, ...] | None = None) -> dict[str, float] | None:
    cells = _cells(line)
    if len(cells) < 6 or not _NUMBER.match(cells[1]):
        return None
    numbers = [float(cell) for cell in cells[1:] if _NUMBER.match(cell)]
    labels = fields or _DEFAULT_FIELDS
    if len(numbers) < 5:
        return None
    row: dict[str, float] = {}
    for field, value in zip(labels, numbers):
        row[field] = value
    if "count" not in row:
        return None
    return row


def _merge(existing: dict[str, float] | None, row: Mapping[str, float]) -> dict[str, float]:
    if existing is None:
        return dict(row)
    count = existing["count"] + row["count"]
    merged = {
        "count": count,
        "min_s": min(existing["min_s"], row["min_s"]),
        "max_s": max(existing["max_s"], row["max_s"]),
        "size_kb": _weighted(existing, row, "size_kb", count),
        "avg_s": _weighted(existing, row, "avg_s", count),
    }
    percentile_fields = {
        field
        for field in set(existing) | set(row)
        if field.startswith("p") and field.endswith("_s")
    }
    for field in sorted(percentile_fields):
        if field in existing and field in row:
            merged[field] = _weighted(existing, row, field, count)
    return merged


def _weighted(
    existing: Mapping[str, float],
    row: Mapping[str, float],
    field: str,
    count: float,
) -> float:
    return (existing[field] * existing["count"] + row[field] * row["count"]) / count
