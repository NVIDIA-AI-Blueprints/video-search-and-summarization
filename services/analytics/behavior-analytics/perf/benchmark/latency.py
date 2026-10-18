# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Parse latency-monitor tables and subtract raw-topic latency."""

from __future__ import annotations

import re
from typing import Mapping

from kpis import evaluate_kpis, load_kpis

OUTPUT_TOPICS = ("frames", "behavior", "events", "incidents")
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
    """Aggregate sensor rows from the last cumulative latency-monitor dump.

    The monitor reprints a growing table every few seconds. Earlier dumps
    are prefixes of the last one, so merging every dump double-counts.
    Empty dumps in the warm-up window are skipped. Within the last dump,
    count is summed across sensors and average / percentile columns are
    count-weighted.
    """
    last: dict[str, dict[str, float]] = {}
    batch: dict[str, dict[str, float]] = {}
    topic: str | None = None
    for raw_line in text.splitlines():
        title = raw_line.strip()
        if title.startswith(_REPORT_HEADER):
            if batch:
                last = batch
            batch = {}
            topic = None
            continue
        if title in TOPIC_TITLES:
            topic = TOPIC_TITLES[title]
            continue
        if topic is None or not raw_line.startswith("|"):
            continue
        row = _parse_row(raw_line)
        if row is None:
            continue
        batch[topic] = _merge(batch.get(topic), row)
    if batch:
        last = batch
    return last


def correct_topics(topics: Mapping[str, Mapping[str, float]]) -> dict[str, object]:
    """Subtract the raw-topic summary from each Behavior Analytics output topic.

    Percentile correction uses the printed percentile columns. It is the
    summary subtraction Ganapathy and Yun agreed to, not a per-message
    recomputation.
    """
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


def build_result(
    report_text: str,
    *,
    artifact_ref: str,
    scenario: str,
    config_id: str,
    duration_seconds: int,
    warmup_seconds: int,
    apply_kpis: bool = True,
    kpis: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Build the benchmark result from a latency-monitor report.

    When apply_kpis is true (the Jenkins path), configs/kpis.yaml is loaded
    and a missing required topic or a threshold miss fails the run.
    """
    topics = parse_latency_report(report_text)
    correction = correct_topics(topics)
    payload: dict[str, object] = {
        "schema_version": "0.1",
        "benchmark_type": "microservice",
        "service": "behavior-analytics",
        "scenario": scenario,
        "config_id": config_id,
        "artifact_ref": artifact_ref,
        "duration_seconds": duration_seconds,
        "warmup_seconds": warmup_seconds,
        "setup": "warehouse_2d_kafka_playback",
        "correction": "topic_summary_minus_raw",
        "success": correction["success"],
        "error": correction["error"],
        "raw": correction["raw"],
        "topics": correction["topics"],
    }
    if apply_kpis:
        kpi_result = evaluate_kpis(payload, kpis if kpis is not None else load_kpis())
        payload["kpis"] = kpi_result
        if not kpi_result["passed"]:
            payload["success"] = False
            previous = payload.get("error")
            payload["error"] = (
                f"{previous}; {kpi_result['error']}" if previous else kpi_result["error"]
            )
    return payload


def _parse_row(line: str) -> dict[str, float] | None:
    cells = [cell.strip() for cell in line.split("|")]
    cells = [cell for cell in cells if cell]
    if len(cells) < 6 or not _NUMBER.match(cells[1]):
        return None
    numbers = [float(cell) for cell in cells[1:] if _NUMBER.match(cell)]
    if len(numbers) < 5:
        return None
    row = {
        "count": numbers[0],
        "min_s": numbers[1],
        "max_s": numbers[2],
        "avg_s": numbers[3],
        "size_kb": numbers[4],
    }
    for field, value in zip(("p50_s", "p95_s", "p99_s"), numbers[5:8]):
        row[field] = value
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
    for field in ("p50_s", "p95_s", "p99_s"):
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
