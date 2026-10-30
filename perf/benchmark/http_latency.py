# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Time Video Analytics API HTTP queries against the live warehouse stack."""

from __future__ import annotations

import json
import statistics
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Sequence

LIVE_ENDPOINTS: tuple[dict[str, Any], ...] = (
    {"name": "livez", "path": "/livez", "params": ()},
    {"name": "frames", "path": "/frames", "params": ("sensorId", "fromTimestamp", "toTimestamp")},
    {
        "name": "frames_enhanced",
        "path": "/frames/enhanced",
        "params": ("sensorId", "fromTimestamp", "toTimestamp"),
    },
    {
        "name": "frames_bev",
        "path": "/frames/bev",
        "params": ("sensorId", "fromTimestamp", "toTimestamp"),
    },
    {
        "name": "frames_proximity_detection",
        "path": "/frames/proximity-detection",
        "params": ("sensorId", "fromTimestamp", "toTimestamp"),
    },
    {
        "name": "behavior",
        "path": "/behavior",
        "params": ("sensorId", "fromTimestamp", "toTimestamp"),
    },
    {
        "name": "events",
        "path": "/events/tripwire",
        "params": ("sensorId", "fromTimestamp", "toTimestamp"),
    },
    {
        "name": "incidents",
        "path": "/incidents",
        "params": ("sensorId", "fromTimestamp", "toTimestamp"),
    },
    {
        "name": "incidents_severe",
        "path": "/incidents/severe",
        "params": ("sensorId", "fromTimestamp", "toTimestamp"),
    },
    {
        "name": "alerts",
        "path": "/alerts",
        "params": ("sensorId", "fromTimestamp", "toTimestamp"),
    },
    {
        "name": "alerts_severe",
        "path": "/alerts/severe",
        "params": ("sensorId", "fromTimestamp", "toTimestamp"),
    },
    {
        "name": "metrics_tripwire_counts",
        "path": "/metrics/tripwire/counts",
        "params": ("sensorId", "fromTimestamp", "toTimestamp"),
    },
    {
        "name": "metrics_occupancy_roi",
        "path": "/metrics/occupancy/roi",
        "params": ("sensorId", "fromTimestamp", "toTimestamp"),
    },
    {
        "name": "metrics_occupancy_fov",
        "path": "/metrics/occupancy/fov",
        "params": ("sensorId", "fromTimestamp", "toTimestamp"),
    },
    {
        "name": "metrics_last_processed_timestamp",
        "path": "/metrics/last-processed-timestamp",
        "params": ("sensorId",),
    },
    {"name": "config_calibration", "path": "/config/calibration", "params": ("sensorId",)},
)


def percentile(values: list[float], pct: int) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (pct / 100.0) * (len(ordered) - 1)
    low = int(rank)
    high = min(low + 1, len(ordered) - 1)
    weight = rank - low
    return ordered[low] * (1.0 - weight) + ordered[high] * weight


def summarize(latencies: list[float], ok_count: int, total: int) -> dict[str, Any]:
    present = total > 0 and ok_count > 0
    payload: dict[str, Any] = {
        "present": present,
        "count": total,
        "ok_count": ok_count,
        "ok_ratio": (ok_count / total) if total else 0.0,
    }
    if latencies:
        payload.update(
            {
                "min_s": min(latencies),
                "max_s": max(latencies),
                "avg_s": statistics.fmean(latencies),
                "p50_s": percentile(latencies, 50),
                "p95_s": percentile(latencies, 95),
                "p99_s": percentile(latencies, 99),
            }
        )
    return payload


def request_once(url: str, timeout: float = 30.0) -> tuple[int, float, bytes]:
    started = time.perf_counter()
    req = urllib.request.Request(url, method="GET")
    body = b""
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
            status = resp.status
    except urllib.error.HTTPError as error:
        status = error.code
        try:
            body = error.read()
        except OSError:
            body = b""
    except urllib.error.URLError:
        status = 0
    elapsed = time.perf_counter() - started
    return status, elapsed, body


def endpoint_url(base: str, path: str, params: Mapping[str, str]) -> str:
    joined = urllib.parse.urljoin(base.rstrip("/") + "/", path.lstrip("/"))
    if not params:
        return joined
    return f"{joined}?{urllib.parse.urlencode(params)}"


def parse_timestamp(value: Any) -> datetime | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def last_processed_timestamp(payload: Any) -> datetime | None:
    """Read `/metrics/last-processed-timestamp` including `{latestTimestamp:{timestamp}}`."""
    if not isinstance(payload, Mapping):
        return None
    nested = payload.get("latestTimestamp")
    if isinstance(nested, Mapping):
        parsed = parse_timestamp(
            nested.get("timestamp") or nested.get("lastProcessedTimestamp")
        )
        if parsed is not None:
            return parsed
    elif nested not in (None, ""):
        parsed = parse_timestamp(nested)
        if parsed is not None:
            return parsed
    for key in ("lastProcessedTimestamp", "timestamp", "toTimestamp"):
        parsed = parse_timestamp(payload.get(key))
        if parsed is not None:
            return parsed
    data = payload.get("data")
    if data is not payload and isinstance(data, Mapping):
        return last_processed_timestamp(data)
    return None


def discover_window(base_url: str, sensor_id: str, window_seconds: int = 60) -> dict[str, str]:
    """Query window ending at last-processed-timestamp. Fail if that value is missing."""
    url = endpoint_url(
        base_url,
        "/metrics/last-processed-timestamp",
        {"sensorId": sensor_id},
    )
    status, _, body = request_once(url)
    parsed = None
    if status == 200 and body:
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            payload = None
        parsed = last_processed_timestamp(payload)
    if parsed is None:
        raise RuntimeError(
            "Video Analytics API last-processed timestamp is missing; "
            "refusing to query a host-now window"
        )
    from_ts = parsed - timedelta(seconds=window_seconds)
    stamp = parsed.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    return {
        "fromTimestamp": from_ts.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        "toTimestamp": stamp,
        "timestamp": stamp,
    }


def resolve_rows(names: Sequence[str]) -> list[dict[str, Any]]:
    catalog = {row["name"]: row for row in LIVE_ENDPOINTS}
    if list(names) == ["spec_gets"]:
        return [dict(row) for row in LIVE_ENDPOINTS]
    missing = [name for name in names if name not in catalog]
    if missing:
        raise ValueError(f"unknown endpoint names: {', '.join(missing)}")
    return [dict(catalog[name]) for name in names]


def measure_endpoints(
    base_url: str,
    names: Sequence[str],
    context: Mapping[str, str],
    *,
    request_count: int,
    warmup_requests: int,
) -> dict[str, Any]:
    endpoints: dict[str, Any] = {}
    for row in resolve_rows(names):
        params = {key: context[key] for key in row["params"] if key in context}
        url = endpoint_url(base_url, row["path"], params)
        for _ in range(warmup_requests):
            request_once(url)
        latencies: list[float] = []
        ok_count = 0
        statuses: list[int] = []
        for _ in range(request_count):
            status, elapsed, _body = request_once(url)
            statuses.append(status)
            if status == 200:
                ok_count += 1
                latencies.append(elapsed)
        summary = summarize(latencies, ok_count, request_count)
        summary.update(
            {
                "path": row["path"],
                "url": url,
                "statuses": statuses,
            }
        )
        endpoints[row["name"]] = summary
    return endpoints


def build_api_result(
    *,
    base_url: str,
    names: Sequence[str],
    sensor_id: str,
    place: str,
    request_count: int,
    warmup_requests: int,
    window_seconds: int = 60,
) -> dict[str, Any]:
    window = discover_window(base_url, sensor_id, window_seconds=window_seconds)
    context = {
        "sensorId": sensor_id,
        "place": place,
        **window,
    }
    endpoints = measure_endpoints(
        base_url,
        names,
        context,
        request_count=request_count,
        warmup_requests=warmup_requests,
    )
    return {
        "base_url": base_url,
        "sensor_id": sensor_id,
        "place": place,
        "query_window": window,
        "request_count": request_count,
        "warmup_requests": warmup_requests,
        "endpoints": endpoints,
    }
