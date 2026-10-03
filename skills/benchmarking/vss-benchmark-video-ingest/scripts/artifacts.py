# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Normalized CSV artifacts.

Four CSVs are the canonical output of a run. Charts and summaries are generated
from them, never from ad hoc parsing of raw logs.

===========================  ==============================================
File                         Purpose
===========================  ==============================================
``ingest_corpus.csv``        one row per measured video
``ingest_requests.csv``      one row per upload
``ingest_summary.csv``       one row per sweep point
``ingest_errors.csv``        error taxonomy by HTTP status and observed phase
===========================  ==============================================

Two levels of metric live here, and mixing them is the easiest way to misread a
run: per-upload rows in ``ingest_requests.csv``, per-sweep-point rows in
``ingest_summary.csv``.
"""

from __future__ import annotations

import csv
from datetime import datetime
import math
from pathlib import Path
from typing import Any
from typing import Iterable
from typing import Sequence

from corpus import VideoItem
from upload import UploadRecord

CORPUS_FIELDS = (
    "video_id",
    "video_class",
    "source_path",
    "bytes",
    "duration_sec",
    "fps",
    "width",
    "height",
    "resolution",
    "codec",
    "container",
    "content_type",
    "probe_status",
)

#: ``ingest_summary.csv`` column order. Harness column names are reused so a
#: Phase 1 curve and a ``vss_ingest_perf`` curve line up without renaming.
#: ``rt_set`` and ``ba_enabled`` stay blank: Phase 1 does not control the
#: deployment shape, so it cannot label it.
SUMMARY_FIELDS = (
    "video_class",
    "concurrency",
    "rt_set",
    "ba_enabled",
    "upload_count",
    "success_count",
    "failure_count",
    "success_rate_pct",
    # Validity of the throughput columns below. A throughput number only means
    # something if every upload finished AND RT-CV actually processed the frames
    # behind it -- headline fps computed over failed streams reads as clean
    # scaling. Carry the verdict next to the number.
    "result_valid",
    "result_invalid_reason",
    "min_raw_completion_ratio",
    "total_video_duration_min",
    "total_video_size_gb",
    "wall_clock_min",
    "video_min_per_sec",
    "success_window_sec",
    "aggregate_mb_per_sec",
    "p50_latency_sec",
    "p95_latency_sec",
    "max_latency_sec",
    "api_failure_statuses",
    "cli_exit_statuses",
)

ERROR_FIELDS = (
    "video_class",
    "concurrency",
    "phase",
    "http_status",
    "cli_exit_code",
    "outcome",
    "count",
    "example_detail",
)

#: Below this, the slowest stream did not really finish and the point's
#: throughput must not be plotted. Matches ``result_validity`` in the harness.
MIN_RAW_COMPLETION_RATIO = 0.95

#: Where in the upload an outcome was observed. Phase 1 can name the phase the
#: *client* was in; it never names an internal pipeline stage.
PHASES = {
    "failed": "ingest_request",
    "timed_out": "ingest_request",
    "unconfirmed": "readiness_check",
}


def percentile(values: Sequence[float], pct: float) -> float:
    """Linear-interpolation percentile, identical to the harness helper."""
    clean = sorted(float(v) for v in values if v is not None and math.isfinite(float(v)))
    if not clean:
        return 0.0
    if len(clean) == 1:
        return clean[0]
    position = (len(clean) - 1) * pct
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return clean[lower]
    fraction = position - lower
    return clean[lower] + (clean[upper] - clean[lower]) * fraction


def _write_csv(path: Path, fields: Sequence[str], rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(fields))
        writer.writeheader()
        for row in rows:
            writer.writerow({name: row.get(name, "") for name in fields})


def write_corpus_csv(path: Path, items: Sequence[VideoItem]) -> None:
    _write_csv(path, CORPUS_FIELDS, (item.as_row() for item in items))


def write_requests_csv(path: Path, records: Sequence[UploadRecord]) -> None:
    _write_csv(path, UploadRecord.CSV_FIELDS, (record.as_row() for record in records))


def api_failure_statuses(records: Sequence[UploadRecord]) -> str:
    """HTTP status histogram across every upload in the point, e.g. ``200:21``.

    Transport-level give-ups have no status; they are counted under ``none`` so
    the histogram still sums to ``upload_count``.
    """
    counts: dict[str, int] = {}
    for record in records:
        key = record.http_status or "none"
        counts[key] = counts.get(key, 0) + 1
    return "; ".join(f"{key}:{counts[key]}" for key in sorted(counts))


def _upload_window_sec(records: Sequence[UploadRecord]) -> float | None:
    """Success window: max(completed) - min(started), with validated clocks.

    Under ``es_readiness`` each record's ``ingest_confirmed_at`` is
    max(ingested_at) across its indexed documents. It must be strictly later
    than that upload's client timestamp. A plausible aggregate window can hide
    an individual upload with clock skew, so validate every pair first.

    Return ``None`` for no successes; raise ``ValueError`` when a success lacks
    usable timestamps. An invalid window must never become a tiny denominator
    or silently fall back to a different throughput definition.
    """
    if not records:
        return None
    starts: list[datetime] = []
    ends: list[datetime] = []
    for record in records:
        started = _parse_ts(record.request_sent_at)
        completed = _parse_ts(record.ingest_confirmed_at)
        label = f"confirmed upload {record.upload_sequence}"
        if started is None or completed is None:
            invalid_fields = [
                name
                for name, value in (
                    ("request_sent_at", started),
                    ("ingest_confirmed_at", completed),
                )
                if value is None
            ]
            raise ValueError(
                f"{label} has missing, malformed, or timezone-naive "
                f"{', '.join(invalid_fields)}; throughput window is unavailable"
            )
        if completed <= started:
            raise ValueError(
                f"{label} has a nonpositive timestamp window "
                "(ingest_confirmed_at <= request_sent_at); check client and "
                "Elasticsearch clock synchronization"
            )
        starts.append(started)
        ends.append(completed)
    return (max(ends) - min(starts)).total_seconds()


def _parse_ts(value: str) -> datetime | None:
    """Parse a timezone-aware timestamp without assuming the client's zone."""
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.utcoffset() is not None else None


def summarize_point(
    *,
    video_class: str,
    concurrency: int,
    records: Sequence[UploadRecord],
    wall_clock_sec: float,
) -> dict[str, Any]:
    """Build one ``ingest_summary.csv`` row from the point's upload records.

    Counting rules applied here:

    * ``upload_count`` is concurrency times the file count, not concurrency.
    * ``success_count`` counts confirmed uploads only.
    * ``total_video_duration_min`` counts confirmed video-minutes and already
      includes the concurrency multiplier, because every worker uploaded the
      whole class.
    * ``total_video_size_gb`` counts confirmed source bytes, matching the
      harness column of the same name.
    * ``aggregate_mb_per_sec`` counts every byte the client put on the wire,
      confirmed or not, because the uplink carried all of them. Its
      denominator stays the point wall clock -- the uplink carried the failed
      bytes too, so the success window would overstate the transfer rate.
    * ``video_min_per_sec`` divides confirmed video-minutes by the success
      upload window, the harness denominator, not by the point wall clock.
    * Percentiles are taken across confirmed uploads inside this one point, so
      they measure contention rather than differences between videos.
    """
    confirmed = [r for r in records if r.outcome == "confirmed"]
    failures = [r for r in records if r.outcome != "confirmed"]
    latencies = [r.latency_sec for r in confirmed]

    # ---- result validity, mirroring the harness ------------------------
    ratios = [
        float(r.readiness_metrics["raw_completion_ratio"])
        for r in confirmed
        if isinstance(r.readiness_metrics.get("raw_completion_ratio"), (int, float))
    ]
    min_completion = min(ratios) if ratios else None
    invalid_reasons: list[str] = []
    if failures:
        invalid_reasons.append(f"{len(failures)}/{len(records)} uploads failed")
    if min_completion is not None and min_completion < MIN_RAW_COMPLETION_RATIO:
        invalid_reasons.append(
            f"slowest stream indexed {min_completion:.1%} of its frames "
            f"(< {MIN_RAW_COMPLETION_RATIO:.0%})"
        )
    if not confirmed:
        invalid_reasons.append("no successful uploads")
    wall = max(float(wall_clock_sec), 1e-9)
    transmitted = sum(r.transmitted_bytes for r in records)
    confirmed_video_min = sum(r.duration_sec for r in confirmed) / 60.0
    # Preserve the harness success-window denominator for usable timestamps.
    # Invalid confirmed timing cannot support throughput, even when another
    # upload would make the aggregate window positive. Keep no-success rows at
    # zero throughput using the point wall clock, as before.
    try:
        success_wall = _upload_window_sec(confirmed) if confirmed else wall
    except ValueError as exc:
        success_wall = None
        invalid_reasons.append(str(exc))

    return {
        "video_class": video_class,
        "concurrency": concurrency,
        "rt_set": "",
        "ba_enabled": "",
        "upload_count": len(records),
        "success_count": len(confirmed),
        "failure_count": len(records) - len(confirmed),
        "success_rate_pct": round(100.0 * len(confirmed) / len(records), 1) if records else "",
        "result_valid": not invalid_reasons,
        "result_invalid_reason": "; ".join(invalid_reasons),
        "min_raw_completion_ratio": (round(min_completion, 4) if min_completion is not None else ""),
        "total_video_duration_min": round(confirmed_video_min, 3),
        "total_video_size_gb": round(sum(r.bytes for r in confirmed) / 1_000_000_000, 3),
        "wall_clock_min": round(wall / 60.0, 3),
        "video_min_per_sec": (round(confirmed_video_min / success_wall, 3) if success_wall is not None else ""),
        "success_window_sec": round(success_wall, 3) if success_wall is not None else "",
        "aggregate_mb_per_sec": round(transmitted / 1_000_000 / wall, 3),
        "p50_latency_sec": round(percentile(latencies, 0.50), 1),
        "p95_latency_sec": round(percentile(latencies, 0.95), 1),
        "max_latency_sec": round(max(latencies), 1) if latencies else 0.0,
        "api_failure_statuses": api_failure_statuses(records),
        "cli_exit_statuses": "; ".join(
            f"{code}:{sum(str(r.cli_exit_code) == code for r in records)}"
            for code in sorted({str(r.cli_exit_code) for r in records})
        ),
    }


def write_summary_csv(path: Path, rows: Sequence[dict[str, Any]]) -> None:
    _write_csv(path, SUMMARY_FIELDS, rows)


def build_error_rows(records: Sequence[UploadRecord]) -> list[dict[str, Any]]:
    """Group non-confirmed uploads by (class, concurrency, phase, status)."""
    grouped: dict[tuple, dict[str, Any]] = {}
    for record in records:
        if record.outcome == "confirmed":
            continue
        key = (
            record.video_class,
            record.concurrency,
            PHASES.get(record.outcome, "unknown"),
            record.http_status or "none",
            record.outcome,
            record.cli_exit_code,
        )
        entry = grouped.setdefault(
            key,
            {
                "video_class": key[0],
                "concurrency": key[1],
                "phase": key[2],
                "http_status": key[3],
                "outcome": key[4],
                "cli_exit_code": key[5],
                "count": 0,
                "example_detail": "",
            },
        )
        entry["count"] += 1
        if not entry["example_detail"]:
            drop = str(record.readiness_metrics.get("drop_reason") or "")
            detail = f"[{drop}] {record.error_detail}" if drop else record.error_detail
            if detail:
                entry["example_detail"] = detail[:300]
    return [grouped[key] for key in sorted(grouped, key=lambda k: (str(k[0]), k[1], str(k[2]), str(k[3])))]


def write_errors_csv(path: Path, records: Sequence[UploadRecord]) -> None:
    _write_csv(path, ERROR_FIELDS, build_error_rows(records))
