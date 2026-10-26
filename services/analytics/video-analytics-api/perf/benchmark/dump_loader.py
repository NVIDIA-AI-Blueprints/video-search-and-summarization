#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Bulk-load warehouse Elasticsearch dumps without requiring host Node."""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

LFS_PREFIX = "version https://git-lfs.github.com/spec/"
BULK_CHUNK = 500


def is_lfs_pointer(path: Path) -> bool:
    head = path.read_bytes()[:200]
    return head.startswith(LFS_PREFIX.encode("utf-8"))


def _request(url: str, method: str, body: bytes | None = None, content_type: str = "application/json") -> bytes:
    headers = {}
    if body is not None:
        headers["Content-Type"] = content_type
        headers["Content-Length"] = str(len(body))
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return resp.read()
    except urllib.error.HTTPError as error:
        detail = error.read()[:300]
        raise RuntimeError(f"{method} {url} -> {error.code}: {detail!r}") from error


def post_bulk(es_url: str, lines: list[str]) -> None:
    payload = ("\n".join(lines) + "\n").encode("utf-8")
    raw = _request(f"{es_url.rstrip('/')}/_bulk", "POST", payload, "application/x-ndjson")
    try:
        decoded = json.loads(raw.decode("utf-8") or "{}")
    except json.JSONDecodeError as error:
        raise RuntimeError("Elasticsearch _bulk did not return JSON") from error
    if not isinstance(decoded, dict) or not decoded.get("errors"):
        return
    reasons: list[str] = []
    for item in decoded.get("items") or []:
        if not isinstance(item, dict):
            continue
        action = next(iter(item.values()), {})
        if not isinstance(action, dict):
            continue
        status = int(action.get("status") or 0)
        if action.get("error") or status >= 400:
            reasons.append(str(action.get("error") or status))
        if len(reasons) >= 3:
            break
    raise RuntimeError(
        "Elasticsearch _bulk rejected documents: "
        + ("; ".join(reasons) if reasons else "errors=true")
    )


def load_file(es_url: str, path: Path) -> int:
    if is_lfs_pointer(path):
        raise RuntimeError(
            f"{path} is a Git LFS pointer, not the dump. Run git lfs pull "
            "(or enable GitLFSPull in the CI checkout)."
        )
    bulk: list[str] = []
    count = 0
    with path.open() as handle:
        for raw in handle:
            stripped = raw.strip()
            if not stripped:
                continue
            try:
                hit = json.loads(stripped)
            except json.JSONDecodeError:
                continue
            index = hit.get("_index")
            source = hit.get("_source")
            if not index or source is None:
                continue
            action = {"index": {"_index": index}}
            if hit.get("_id"):
                action["index"]["_id"] = hit["_id"]
            bulk.append(json.dumps(action))
            bulk.append(json.dumps(source))
            count += 1
            if len(bulk) / 2 >= BULK_CHUNK:
                post_bulk(es_url, bulk)
                bulk = []
    if bulk:
        post_bulk(es_url, bulk)
    return count


def load_dump_dir(es_url: str, dump_dir: Path) -> int:
    if not dump_dir.is_dir():
        raise RuntimeError(f"dump directory not found: {dump_dir}")
    files = sorted(dump_dir.rglob("*.json"))
    if not files:
        raise RuntimeError(f"no JSON dumps under {dump_dir}")
    total = 0
    for path in files:
        loaded = load_file(es_url, path)
        print(f"  Loaded {loaded} docs from {path.name}", flush=True)
        total += loaded
    if total <= 0:
        raise RuntimeError(f"dump directory {dump_dir} contained files but no documents")
    print(f"Elasticsearch data dump loaded: {total} documents", flush=True)
    return total


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--es-url", default="http://127.0.0.1:19200")
    parser.add_argument("--dump-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        load_dump_dir(args.es_url, args.dump_dir)
    except Exception as error:  # noqa: BLE001 — CLI boundary
        print(f"dump load failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
