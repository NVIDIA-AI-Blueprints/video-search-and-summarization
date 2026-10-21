# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Time Video Analytics API HTTP queries and write result.json."""

from __future__ import annotations

import json
import os
import statistics
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Mapping

from kpis import evaluate_kpis, load_kpis
from safe_fs import safe_write_text

ENDPOINT_PATHS = {
    "livez": "/livez",
    "frames": "/frames",
    "behavior": "/behavior",
    "events": "/events/tripwire",
    "incidents": "/incidents",
}


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


def request_once(url: str, timeout: float = 30.0) -> tuple[int, float]:
    started = time.perf_counter()
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            resp.read()
            status = resp.status
    except urllib.error.HTTPError as error:
        status = error.code
    except urllib.error.URLError:
        status = 0
    elapsed = time.perf_counter() - started
    return status, elapsed


def endpoint_url(base: str, name: str, params: Mapping[str, str]) -> str:
    path = ENDPOINT_PATHS[name]
    if name == "livez":
        return urllib.parse.urljoin(base.rstrip("/") + "/", path.lstrip("/"))
    query = urllib.parse.urlencode(params)
    return f"{base.rstrip('/')}{path}?{query}"


def measure_endpoints(
    base_url: str,
    names: list[str],
    params: Mapping[str, str],
    *,
    request_count: int,
    warmup_requests: int,
) -> dict[str, Any]:
    results: dict[str, Any] = {}
    for name in names:
        url = endpoint_url(base_url, name, params)
        latencies: list[float] = []
        ok_count = 0
        total = 0
        rounds = max(request_count, 1) + max(warmup_requests, 0)
        for index in range(rounds):
            status, elapsed = request_once(url)
            if index < warmup_requests:
                continue
            total += 1
            if status == 200:
                ok_count += 1
                latencies.append(elapsed)
        results[name] = summarize(latencies, ok_count, total)
        results[name]["url"] = url
    return results


def build_result(
    endpoints: Mapping[str, Any],
    *,
    artifact_ref: str,
    scenario: str,
    config_id: str,
    request_count: int,
    warmup_requests: int,
    apply_kpis: bool = True,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": "0.1",
        "service": "video-analytics-api",
        "benchmark_type": "microservice",
        "artifact_ref": artifact_ref,
        "scenario": scenario,
        "config_id": config_id,
        "request_count": request_count,
        "warmup_requests": warmup_requests,
        "endpoints": dict(endpoints),
        "success": True,
    }
    if apply_kpis:
        kpis = evaluate_kpis(payload, load_kpis())
        payload["kpis"] = kpis
        payload["success"] = bool(kpis.get("passed"))
        if kpis.get("error"):
            payload["error"] = kpis["error"]
    return payload


def write_result(dest: Path, payload: Mapping[str, Any]) -> Path:
    return safe_write_text(dest, json.dumps(payload, indent=2) + "\n")


def main() -> int:
    from argparse import ArgumentParser

    parser = ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=os.environ.get("VA_API_URL", "http://127.0.0.1:18081"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--artifact-ref", default=os.environ.get("ARTIFACT_REF", ""))
    parser.add_argument("--scenario", default=os.environ.get("PERF_SCENARIO", "quick_test"))
    parser.add_argument("--config-id", default=os.environ.get("PERF_CONFIG_ID", "warehouse-es-dump"))
    parser.add_argument("--request-count", type=int, default=int(os.environ.get("PERF_REQUEST_COUNT", "20")))
    parser.add_argument("--warmup-requests", type=int, default=int(os.environ.get("PERF_WARMUP_REQUESTS", "2")))
    parser.add_argument("--sensor-id", default=os.environ.get("PERF_SENSOR_ID", "Camera"))
    parser.add_argument("--from-ts", default=os.environ.get("PERF_FROM_TS", "2026-02-14T10:16:00.000Z"))
    parser.add_argument("--to-ts", default=os.environ.get("PERF_TO_TS", "2026-02-14T10:17:00.000Z"))
    parser.add_argument(
        "--endpoints",
        default=os.environ.get("PERF_ENDPOINTS", "livez frames behavior events incidents"),
    )
    args = parser.parse_args()
    names = [item for item in args.endpoints.split() if item]
    params = {
        "sensorId": args.sensor_id,
        "fromTimestamp": args.from_ts,
        "toTimestamp": args.to_ts,
    }
    measured = measure_endpoints(
        args.base_url,
        names,
        params,
        request_count=args.request_count,
        warmup_requests=args.warmup_requests,
    )
    payload = build_result(
        measured,
        artifact_ref=args.artifact_ref,
        scenario=args.scenario,
        config_id=args.config_id,
        request_count=args.request_count,
        warmup_requests=args.warmup_requests,
    )
    write_result(args.output, payload)
    print(json.dumps({"result_file": str(args.output), "status": "PASS" if payload["success"] else "FAIL"}))
    return 0 if payload["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
