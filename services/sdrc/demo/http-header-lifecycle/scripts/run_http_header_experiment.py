#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Collect SDRC HTTP-header lifecycle experiment artifacts.

The script drives the checked-in HTTP-header lifecycle facade and writes CSV/JSON
evidence for placement latency, route availability, and SDRC state snapshots.
It intentionally uses only Python standard-library modules so it can run from a
developer laptop or a cluster jump host without installing benchmark tooling.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def join_url(base_url: str, path: str) -> str:
    return base_url.rstrip("/") + "/" + path.lstrip("/")


def percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * (pct / 100.0)
    lower = int(rank)
    upper = min(lower + 1, len(ordered) - 1)
    weight = rank - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def build_payload(stream_id: str, change: str, camera_url_template: str) -> dict[str, Any]:
    return {
        "alert_type": "camera_status_change",
        "created_at": utc_now(),
        "event": {
            "camera_id": stream_id,
            "camera_name": stream_id,
            "camera_url": camera_url_template.format(stream_id=stream_id),
            "change": change,
            "metadata": {"experiment": "sdrc-http-header"},
        },
        "source": "vst",
    }


def http_request(
    *,
    method: str,
    url: str,
    headers: dict[str, str] | None = None,
    payload: dict[str, Any] | None = None,
    timeout: float,
    action: str,
    stream_id: str = "",
) -> dict[str, Any]:
    request_headers = dict(headers or {})
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        request_headers.setdefault("Content-Type", "application/json")

    started_at = utc_now()
    started = time.perf_counter()
    status_code = 0
    body = ""
    error = ""

    try:
        request = Request(url, data=data, headers=request_headers, method=method)
        with urlopen(request, timeout=timeout) as response:
            status_code = int(response.status)
            body = response.read().decode("utf-8", errors="replace")
    except HTTPError as exc:
        status_code = int(exc.code)
        body = exc.read().decode("utf-8", errors="replace")
        error = str(exc)
    except URLError as exc:
        error = str(exc)
    except Exception as exc:  # noqa: BLE001 - capture benchmark failures in CSV.
        error = repr(exc)

    elapsed_ms = (time.perf_counter() - started) * 1000.0
    return {
        "action": action,
        "stream_id": stream_id,
        "method": method,
        "url": url,
        "started_at": started_at,
        "elapsed_ms": round(elapsed_ms, 3),
        "status_code": status_code,
        "ok": 200 <= status_code < 300 and error == "",
        "error": error,
        "response_body": body[:2048],
    }


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fieldnames = [
        "action",
        "stream_id",
        "method",
        "url",
        "started_at",
        "elapsed_ms",
        "status_code",
        "ok",
        "error",
        "response_body",
    ]
    with path.open("w", newline="", encoding="utf-8") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fieldnames})


def snapshot_endpoint(
    *,
    management_base_url: str,
    endpoint: str,
    out_dir: Path,
    timeout: float,
) -> tuple[str, Any]:
    url = join_url(management_base_url, endpoint)
    result = http_request(
        method="GET",
        url=url,
        timeout=timeout,
        action="snapshot",
    )
    body = str(result["response_body"])
    name = endpoint.strip("/").replace("/", "_")

    if result["status_code"] != 200:
        error_path = out_dir / f"{name}.error.json"
        error_path.write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
        return name, None

    if endpoint == "/metrics":
        (out_dir / "metrics.prom").write_text(body, encoding="utf-8")
        return name, body

    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        (out_dir / f"{name}.txt").write_text(body, encoding="utf-8")
        return name, body

    (out_dir / f"{name}.json").write_text(
        json.dumps(parsed, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return name, parsed


def summarize_operation(rows: list[dict[str, Any]], action: str) -> dict[str, Any]:
    selected = [row for row in rows if row["action"] == action]
    latencies = [float(row["elapsed_ms"]) for row in selected if row["ok"]]
    return {
        "count": len(selected),
        "ok": sum(1 for row in selected if row["ok"]),
        "failed": sum(1 for row in selected if not row["ok"]),
        "status_counts": dict(Counter(str(row["status_code"]) for row in selected)),
        "latency_ms_p50": percentile(latencies, 50),
        "latency_ms_p95": percentile(latencies, 95),
        "latency_ms_p99": percentile(latencies, 99),
        "latency_ms_max": max(latencies) if latencies else None,
    }


def mapping_accuracy(mapping: Any, stream_ids: list[str]) -> dict[str, Any]:
    if not isinstance(mapping, dict):
        return {
            "expected_streams": len(stream_ids),
            "mapped_streams": None,
            "missing_streams": stream_ids,
            "accuracy_percent": None,
        }

    missing = [stream_id for stream_id in stream_ids if not mapping.get(stream_id)]
    mapped = len(stream_ids) - len(missing)
    accuracy = (mapped / len(stream_ids) * 100.0) if stream_ids else 100.0
    return {
        "expected_streams": len(stream_ids),
        "mapped_streams": mapped,
        "missing_streams": missing,
        "accuracy_percent": round(accuracy, 3),
        "unique_target_count": len({mapping[stream_id] for stream_id in stream_ids if mapping.get(stream_id)}),
    }


def pod_distribution(pod_list: Any) -> dict[str, int]:
    if not isinstance(pod_list, dict):
        return {}
    pods = pod_list.get("pods")
    if not isinstance(pods, list):
        return {}
    distribution: dict[str, int] = {}
    for pod in pods:
        if not isinstance(pod, dict):
            continue
        stream_ids = pod.get("stream_ids")
        if isinstance(stream_ids, list):
            distribution[str(pod.get("podName", ""))] = len(stream_ids)
    return distribution


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run SDRC HTTP-header placement/routing experiment.",
    )
    parser.add_argument("--base-url", default="http://localhost:10001")
    parser.add_argument(
        "--management-base-url",
        default=None,
        help="Defaults to <base-url>/sdrc. Use controller path such as http://localhost:5003/sdrc/vss-rtvi-cv when preferred.",
    )
    parser.add_argument("--streams", type=int, default=20)
    parser.add_argument("--concurrency", type=int, default=5)
    parser.add_argument("--header-name", default="streamid")
    parser.add_argument("--stream-prefix", default="camera")
    parser.add_argument("--add-path", default="/api/v1/stream/add")
    parser.add_argument("--delete-path", default="/api/v1/stream/remove")
    parser.add_argument("--route-path", default="/hello")
    parser.add_argument("--route-requests-per-stream", type=int, default=0)
    parser.add_argument("--camera-url-template", default="rtsp://vss-vios-streamprocessing:30554/webrtc/{stream_id}")
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--settle-seconds", type=float, default=1.0)
    parser.add_argument("--reset-before", action="store_true")
    parser.add_argument("--delete-after", action="store_true")
    parser.add_argument("--fail-on-errors", action="store_true")
    parser.add_argument("--out-dir", default=None)
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    if args.streams < 1:
        raise SystemExit("--streams must be >= 1")
    if args.concurrency < 1:
        raise SystemExit("--concurrency must be >= 1")

    started_wall = utc_now()
    run_name = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = Path(args.out_dir or f"results/{run_name}").resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    base_url = args.base_url.rstrip("/")
    management_base_url = (args.management_base_url or join_url(base_url, "/sdrc")).rstrip("/")
    stream_ids = [f"{args.stream_prefix}-{index:04d}" for index in range(1, args.streams + 1)]
    records: list[dict[str, Any]] = []

    if args.reset_before:
        records.append(
            http_request(
                method="GET",
                url=join_url(management_base_url, "/reset"),
                timeout=args.timeout,
                action="reset",
            )
        )

    add_started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=args.concurrency) as executor:
        futures = [
            executor.submit(
                http_request,
                method="POST",
                url=join_url(base_url, args.add_path),
                headers={args.header_name: stream_id},
                payload=build_payload(stream_id, "camera_streaming", args.camera_url_template),
                timeout=args.timeout,
                action="add",
                stream_id=stream_id,
            )
            for stream_id in stream_ids
        ]
        for future in as_completed(futures):
            records.append(future.result())
    add_wall_clock_s = time.perf_counter() - add_started

    if args.settle_seconds > 0:
        time.sleep(args.settle_seconds)

    snapshots: dict[str, Any] = {}
    for endpoint in (
        "/current_streamid_address_mapping",
        "/redis_cache_data",
        "/pod_list",
        "/get_wl_replica_data",
        "/metrics",
    ):
        name, payload = snapshot_endpoint(
            management_base_url=management_base_url,
            endpoint=endpoint,
            out_dir=out_dir,
            timeout=args.timeout,
        )
        snapshots[name] = payload

    if args.route_requests_per_stream > 0:
        with ThreadPoolExecutor(max_workers=args.concurrency) as executor:
            futures = []
            for stream_id in stream_ids:
                for _ in range(args.route_requests_per_stream):
                    futures.append(
                        executor.submit(
                            http_request,
                            method="GET",
                            url=join_url(base_url, args.route_path),
                            headers={args.header_name: stream_id},
                            timeout=args.timeout,
                            action="route",
                            stream_id=stream_id,
                        )
                    )
            for future in as_completed(futures):
                records.append(future.result())

    if args.delete_after:
        with ThreadPoolExecutor(max_workers=args.concurrency) as executor:
            futures = [
                executor.submit(
                    http_request,
                    method="POST",
                    url=join_url(base_url, args.delete_path),
                    headers={args.header_name: stream_id},
                    timeout=args.timeout,
                    action="delete",
                    stream_id=stream_id,
                )
                for stream_id in stream_ids
            ]
            for future in as_completed(futures):
                records.append(future.result())

    write_csv(out_dir / "requests.csv", records)
    summary = {
        "started_at": started_wall,
        "finished_at": utc_now(),
        "base_url": base_url,
        "management_base_url": management_base_url,
        "streams": args.streams,
        "concurrency": args.concurrency,
        "add_wall_clock_s": round(add_wall_clock_s, 3),
        "add_throughput_streams_per_s": round(args.streams / add_wall_clock_s, 3) if add_wall_clock_s > 0 else None,
        "operations": {
            "reset": summarize_operation(records, "reset"),
            "add": summarize_operation(records, "add"),
            "route": summarize_operation(records, "route"),
            "delete": summarize_operation(records, "delete"),
        },
        "mapping_accuracy": mapping_accuracy(
            snapshots.get("current_streamid_address_mapping"),
            stream_ids,
        ),
        "pod_distribution": pod_distribution(snapshots.get("pod_list")),
    }
    (out_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True),
        encoding="utf-8",
    )

    print(json.dumps(summary, indent=2, sort_keys=True))
    print(f"\nWrote experiment artifacts to: {out_dir}")

    failed = summary["operations"]["add"]["failed"]
    if args.fail_on_errors and failed:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
