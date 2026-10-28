# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Time Video Analytics API HTTP queries and write result.json."""

from __future__ import annotations

import json
import mimetypes
import os
import statistics
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Mapping, Sequence

from kpis import evaluate_kpis, load_kpis
from openapi_matrix import dump_backed_endpoints, query_notes, query_params_for, query_window_s
from safe_fs import safe_write_text

SPEC_GETS_TOKEN = "spec_gets"


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


def encode_multipart(fields: Sequence[tuple[str, Path]]) -> tuple[bytes, str]:
    """Build a multipart/form-data body for P0 file uploads."""
    boundary = "----VaApiPerf" + uuid.uuid4().hex
    chunks: list[bytes] = []
    for name, path in fields:
        payload = path.read_bytes()
        ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        chunks.append(f"--{boundary}\r\n".encode("ascii"))
        chunks.append(
            (
                f'Content-Disposition: form-data; name="{name}"; '
                f'filename="{path.name}"\r\n'
            ).encode("ascii")
        )
        chunks.append(f"Content-Type: {ctype}\r\n\r\n".encode("ascii"))
        chunks.append(payload)
        chunks.append(b"\r\n")
    chunks.append(f"--{boundary}--\r\n".encode("ascii"))
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


def request_once(
    url: str,
    timeout: float = 30.0,
    *,
    method: str = "GET",
    body: bytes | None = None,
    content_type: str | None = None,
) -> tuple[int, float]:
    started = time.perf_counter()
    req = urllib.request.Request(url, data=body, method=method)
    if content_type:
        req.add_header("Content-Type", content_type)
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


def warehouse_context(
    *,
    sensor_id: str,
    place: str,
    from_ts: str,
    to_ts: str,
    lookup_x: str = "0",
    lookup_y: str = "0",
) -> dict[str, str]:
    return {
        "sensorId": sensor_id,
        "sensorIds": sensor_id,
        "place": place,
        "fromTimestamp": from_ts,
        "toTimestamp": to_ts,
        "timestamp": to_ts,
        "emptyIfNotFound": "true",
        "view": "plan-view",
        "x": lookup_x,
        "y": lookup_y,
    }


def resolve_endpoint_rows(names: list[str]) -> list[dict[str, Any]]:
    catalog = dump_backed_endpoints()
    if names == [SPEC_GETS_TOKEN]:
        return catalog
    by_name = {row["name"]: row for row in catalog}
    missing = [name for name in names if name not in by_name]
    if missing:
        raise ValueError(f"unknown endpoint names: {', '.join(missing)}")
    return [by_name[name] for name in names]


def endpoint_url(base: str, path: str, params: Mapping[str, str]) -> str:
    joined = urllib.parse.urljoin(base.rstrip("/") + "/", path.lstrip("/"))
    if not params:
        return joined
    return f"{joined}?{urllib.parse.urlencode(params)}"


def measure_endpoints(
    base_url: str,
    names: list[str],
    context: Mapping[str, str],
    *,
    request_count: int,
    warmup_requests: int,
) -> dict[str, Any]:
    results: dict[str, Any] = {}
    for row in resolve_endpoint_rows(names):
        params = query_params_for(row, context)
        url = endpoint_url(base_url, str(row["path"]), params)
        method = str(row.get("method") or "GET")
        ok_statuses = tuple(row.get("ok_statuses") or (200,))
        body: bytes | None = None
        content_type: str | None = None
        multipart = row.get("multipart")
        if multipart:
            fields = [(str(name), Path(path)) for name, path in multipart]
            body, content_type = encode_multipart(fields)
        latencies: list[float] = []
        ok_count = 0
        total = 0
        rounds = max(request_count, 1) + max(warmup_requests, 0)
        for index in range(rounds):
            status, elapsed = request_once(
                url,
                method=method,
                body=body,
                content_type=content_type,
            )
            if index < warmup_requests:
                continue
            total += 1
            if status in ok_statuses:
                ok_count += 1
                latencies.append(elapsed)
        results[row["name"]] = summarize(latencies, ok_count, total)
        results[row["name"]]["url"] = url
        results[row["name"]]["path"] = row["path"]
        results[row["name"]]["method"] = method
        results[row["name"]]["query"] = query_notes(row, params)
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
    from_ts: str | None = None,
    to_ts: str | None = None,
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
        "query_window_s": query_window_s(
            {"fromTimestamp": from_ts or "", "toTimestamp": to_ts or ""}
        ),
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
    parser.add_argument(
        "--place",
        default=os.environ.get("PERF_PLACE", "building=Warehouse/room=Room-1"),
    )
    parser.add_argument("--from-ts", default=os.environ.get("PERF_FROM_TS", "2026-02-14T10:16:00.000Z"))
    parser.add_argument("--to-ts", default=os.environ.get("PERF_TO_TS", "2026-02-14T10:17:00.000Z"))
    parser.add_argument("--lookup-x", default=os.environ.get("PERF_LOOKUP_X", "0"))
    parser.add_argument("--lookup-y", default=os.environ.get("PERF_LOOKUP_Y", "0"))
    parser.add_argument(
        "--endpoints",
        default=os.environ.get("PERF_ENDPOINTS", SPEC_GETS_TOKEN),
    )
    args = parser.parse_args()
    names = [item for item in args.endpoints.split() if item]
    context = warehouse_context(
        sensor_id=args.sensor_id,
        place=args.place,
        from_ts=args.from_ts,
        to_ts=args.to_ts,
        lookup_x=args.lookup_x,
        lookup_y=args.lookup_y,
    )
    measured = measure_endpoints(
        args.base_url,
        names,
        context,
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
        from_ts=args.from_ts,
        to_ts=args.to_ts,
    )
    write_result(args.output, payload)
    print(json.dumps({"result_file": str(args.output), "status": "PASS" if payload["success"] else "FAIL"}))
    return 0 if payload["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
