#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Exercise Elasticsearch with the NVBug 6661431 high-scale workload shape."""

import argparse
import concurrent.futures
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request


def request(base_url, method, path, body=None, timeout=180):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(
        f"{base_url.rstrip('/')}/{path.lstrip('/')}",
        data=data,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            payload = response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")
        raise RuntimeError(f"{method} {path} returned {exc.code}: {detail}") from exc
    return json.loads(payload) if payload else {}


def write_rejections(stats):
    total = 0
    for node in stats.get("nodes", {}).values():
        pools = node.get("thread_pool", {})
        for pool_name in ("write", "write_coordination"):
            total += pools.get(pool_name, {}).get("rejected", 0)
    return total


def max_heap_percent(stats):
    return max(
        (
            node.get("jvm", {}).get("mem", {}).get("heap_used_percent", 0)
            for node in stats.get("nodes", {}).values()
        ),
        default=0,
    )


def bulk_payload(index_name, documents):
    lines = []
    for doc_id in range(documents):
        lines.append(json.dumps({"index": {"_index": index_name, "_id": doc_id}}))
        lines.append(
            json.dumps(
                {
                    "text": f"Synthetic raw event {doc_id} for {index_name}",
                    "metadata": {
                        "content_metadata": {
                            "uuid": index_name,
                            "doc_type": "raw_events",
                            "chunkIdx": doc_id,
                        }
                    },
                }
            )
        )
    return ("\n".join(lines) + "\n").encode()


def write_stream(base_url, index_name, documents, timeout):
    req = urllib.request.Request(
        f"{base_url.rstrip('/')}/_bulk?wait_for_active_shards=1",
        data=bulk_payload(index_name, documents),
        method="POST",
        headers={"Content-Type": "application/x-ndjson"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            result = json.loads(response.read())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")
        raise RuntimeError(
            f"bulk write for {index_name} returned {exc.code}: {detail}"
        ) from exc
    if result.get("errors"):
        failures = [
            item
            for item in result.get("items", [])
            if next(iter(item.values())).get("status", 500) >= 300
        ]
        raise RuntimeError(
            f"bulk write for {index_name} had {len(failures)} failed items"
        )


def wait_for_cluster(base_url, nodes, timeout):
    query = urllib.parse.urlencode(
        {
            "wait_for_nodes": f">={nodes}",
            "wait_for_status": "yellow",
            "wait_for_no_initializing_shards": "true",
            "wait_for_no_relocating_shards": "true",
            "timeout": f"{timeout}s",
        }
    )
    health = request(base_url, "GET", f"_cluster/health?{query}", timeout=timeout + 10)
    if health.get("timed_out"):
        raise RuntimeError(f"cluster did not settle: {health}")
    if health.get("number_of_nodes", 0) < nodes:
        raise RuntimeError(f"expected at least {nodes} nodes: {health}")
    return health


def monitor_cluster(base_url, stop, sample):
    while not stop.wait(0.5):
        try:
            stats = request(base_url, "GET", "_nodes/stats/thread_pool,jvm", timeout=10)
            sample["max_heap_percent"] = max(
                sample["max_heap_percent"], max_heap_percent(stats)
            )
            sample["max_write_rejections"] = max(
                sample["max_write_rejections"], write_rejections(stats)
            )
        except Exception as exc:  # preserve monitoring failures for the main thread
            sample["errors"].append(str(exc))


def run_wave(args, prefix, wave):
    pattern = f"{prefix}-w{wave}-*"
    indices = [f"{prefix}-w{wave}-s{stream:04d}" for stream in range(args.streams)]
    before = request(args.url, "GET", "_nodes/stats/thread_pool,jvm")
    rejected_before = write_rejections(before)
    sample = {
        "max_heap_percent": max_heap_percent(before),
        "max_write_rejections": rejected_before,
        "errors": [],
    }
    monitor_stop = threading.Event()
    monitor = threading.Thread(
        target=monitor_cluster,
        args=(args.url, monitor_stop, sample),
        daemon=True,
    )

    failures = []
    started = time.monotonic()
    monitor.start()
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.concurrency) as pool:
            futures = {
                pool.submit(
                    write_stream,
                    args.url,
                    index_name,
                    args.documents,
                    args.timeout,
                ): index_name
                for index_name in indices
            }
            for completed, future in enumerate(
                concurrent.futures.as_completed(futures), 1
            ):
                try:
                    future.result()
                except Exception as exc:  # collect all failures before reporting
                    failures.append({"index": futures[future], "error": str(exc)})
                if completed % 128 == 0 or completed == args.streams:
                    print(
                        f"wave {wave}: {completed}/{args.streams} streams written",
                        flush=True,
                    )
    finally:
        monitor_stop.set()
        monitor.join(timeout=15)
    if monitor.is_alive():
        raise RuntimeError(f"wave {wave}: cluster monitor did not stop")
    if sample["errors"]:
        raise RuntimeError(
            f"wave {wave}: cluster monitoring failed: {sample['errors'][:10]}"
        )
    if failures:
        raise RuntimeError(
            f"wave {wave} had failed writes: {json.dumps(failures[:10])}"
        )

    health = wait_for_cluster(args.url, args.nodes, args.timeout)
    encoded_pattern = urllib.parse.quote(pattern, safe="*-_")
    request(args.url, "POST", f"{encoded_pattern}/_refresh")
    count = request(args.url, "GET", f"{encoded_pattern}/_count").get("count", -1)
    expected = args.streams * args.documents
    if count != expected:
        raise RuntimeError(f"wave {wave}: expected {expected} documents, found {count}")

    shards = request(
        args.url,
        "GET",
        f"_cat/shards/{encoded_pattern}?format=json&h=index,prirep,state,node",
    )
    primaries = [shard for shard in shards if shard.get("prirep") == "p"]
    if len(primaries) != args.streams:
        raise RuntimeError(
            f"wave {wave}: expected {args.streams} primary shards, found {len(primaries)}"
        )
    not_started = [shard for shard in primaries if shard.get("state") != "STARTED"]
    if not_started:
        raise RuntimeError(
            f"wave {wave}: {len(not_started)} primary shards are not STARTED"
        )
    shard_nodes = {shard.get("node") for shard in primaries if shard.get("node")}
    if len(shard_nodes) < args.nodes:
        raise RuntimeError(f"wave {wave}: shards use only {len(shard_nodes)} nodes")

    after = request(args.url, "GET", "_nodes/stats/thread_pool,jvm")
    rejected_delta = max(
        write_rejections(after), sample["max_write_rejections"]
    ) - rejected_before
    heap = max(max_heap_percent(after), sample["max_heap_percent"])
    if rejected_delta:
        raise RuntimeError(
            f"wave {wave}: Elasticsearch rejected {rejected_delta} writes"
        )
    if heap >= args.max_heap_percent:
        raise RuntimeError(
            f"wave {wave}: heap reached {heap}%, limit is <{args.max_heap_percent}%"
        )

    result = {
        "wave": wave,
        "streams": args.streams,
        "documents": count,
        "duration_seconds": round(time.monotonic() - started, 3),
        "nodes": health.get("number_of_nodes"),
        "shard_nodes": sorted(shard_nodes),
        "write_rejections": rejected_delta,
        "max_heap_percent": heap,
    }
    print(json.dumps(result, sort_keys=True), flush=True)
    return result, indices


def delete_indices(base_url, indices):
    # Elasticsearch disables wildcard deletes in the shipped configuration.
    # Use bounded batches of exact test-created names to keep request URLs
    # below common proxy limits without weakening that safety setting.
    for offset in range(0, len(indices), 50):
        encoded = urllib.parse.quote(",".join(indices[offset : offset + 50]), safe=",-_")
        request(base_url, "DELETE", f"{encoded}?ignore_unavailable=true")


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:9200")
    parser.add_argument("--nodes", type=int, default=4)
    parser.add_argument("--waves", type=int, default=2)
    parser.add_argument("--streams", type=int, default=1280)
    parser.add_argument("--documents", type=int, default=80)
    parser.add_argument("--concurrency", type=int, default=1280)
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--max-heap-percent", type=int, default=80)
    return parser.parse_args()


def main():
    args = parse_args()
    if min(args.nodes, args.waves, args.streams, args.documents, args.concurrency) < 1:
        raise SystemExit(
            "nodes, waves, streams, documents, and concurrency must be positive"
        )

    unique = f"{int(time.time())}-{os.getpid()}"
    prefix = f"default_nvbug6661431_{unique}"
    if not re.fullmatch(r"[a-z0-9_-]+", prefix):
        raise SystemExit(f"unsafe test index prefix: {prefix}")
    template_name = f"nvbug6661431-e2e-{unique}"
    pattern = f"{prefix}-*"
    results = []
    created_indices = []

    wait_for_cluster(args.url, args.nodes, args.timeout)
    request(
        args.url,
        "PUT",
        f"_index_template/{template_name}",
        {
            "index_patterns": [pattern],
            "priority": 500,
            "template": {
                "settings": {"number_of_shards": 1, "number_of_replicas": 0}
            },
        },
    )
    try:
        for wave in range(1, args.waves + 1):
            wave_indices = [
                f"{prefix}-w{wave}-s{stream:04d}" for stream in range(args.streams)
            ]
            created_indices.extend(wave_indices)
            result, _ = run_wave(args, prefix, wave)
            results.append(result)
            delete_indices(args.url, wave_indices)
            wait_for_cluster(args.url, args.nodes, args.timeout)
    finally:
        if created_indices:
            try:
                delete_indices(args.url, created_indices)
            except RuntimeError as exc:
                print(f"cleanup warning: {exc}", file=sys.stderr)
        try:
            request(args.url, "DELETE", f"_index_template/{template_name}")
        except RuntimeError as exc:
            print(f"cleanup warning: {exc}", file=sys.stderr)

    print(json.dumps({"status": "passed", "waves": results}, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
