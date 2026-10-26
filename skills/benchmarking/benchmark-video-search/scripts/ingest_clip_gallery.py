# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Ingest a validated local clip gallery, or audit its configured search indexes."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import os
import subprocess
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

from flows import clip_metrics
from flows.ingest import AgentThreeStepIngest, probe_media
from flows.ingest_readiness import ExpectedVideo, IngestWatcher
from prepare_search_reproduction import command, digest, load_workspace, save


def validate_dataset(root):
    root = root.resolve(strict=True)
    gallery = json.loads((root / "additional_gt/clips_gt.json").read_text())["gallery"]
    queries = json.loads((root / "gt/queries_gt.json").read_text())["queries"]
    clips = []
    for item in gallery:
        relative = Path(item["video"])
        if (
            relative.is_absolute()
            or len(relative.parts) != 2
            or relative.parts[0] != "clips"
        ):
            raise ValueError("Gallery video must be a local file under clips/")
        path = root / relative
        path.resolve(strict=True)
        if not path.is_file():
            raise ValueError("Gallery video must be a local file under clips/")
        # The canonical manifest supports flat clips/ filenames.
        if path.parent != root / "clips":
            raise ValueError("Place gallery videos directly under clips/")
        media = probe_media(path)
        if (
            not media.get("duration_s")
            or not math.isfinite(media["duration_s"])
            or media["duration_s"] <= 0
        ):
            raise ValueError(f"Cannot determine a positive media duration: {path.name}")
        clips.append(
            {
                "dataset_id": item["chunk_id"],
                "filename": path.name,
                "sensor_id": path.stem,
                "duration_s": media["duration_s"],
                "sha256": digest(path),
            }
        )
    if not clips or len({Path(c["filename"]).stem for c in clips}) != len(clips):
        raise ValueError("Gallery names must be nonempty and unique")
    sensors = clip_metrics.gallery_sensors(clips)
    if not queries or len({q["query_id"] for q in queries}) != len(queries):
        raise ValueError("Query IDs must be nonempty and unique")
    for query in queries:
        if (
            not query["query_id"]
            or query["query_domain"] not in {"event", "pas"}
            or not query["query"].strip()
        ):
            raise ValueError("Invalid query identity, domain or text")
        clip_metrics.score_query(query, [], sensors)
    return root, clips, queries


def configured_origin(manifest):
    from vss_cli import config

    deployment = config.load()
    if deployment.base_url.rstrip("/") != manifest["origin"]:
        raise ValueError("Configure the branch CLI against this workspace origin first")
    return deployment


def refresh_after_ingestion(workspace, manifest):
    """New indexes must be discovered before the SDK enables frame scoring."""
    from vss_cli.search.group import _runtime_from

    with (workspace / "configure-after-ingestion.log").open("w") as log:
        subprocess.run(
            [
                str(Path(sys.executable).with_name("vss")),
                "configure",
                "--base-url",
                manifest["origin"],
            ],
            stdout=log,
            stderr=subprocess.STDOUT,
            check=True,
        )
    if _runtime_from(configured_origin(manifest)).frames_index is None:
        raise RuntimeError(
            "Ingestion completed without a discoverable raw-frame index; full search reproduction is unsupported"
        )


async def audit_indexes(manifest, clips=None):
    from vss_cli.search.group import _runtime_from
    from vss_core.search_core.clients.elastic import ElasticClient

    deployment = configured_origin(manifest)
    listing = json.loads(
        command(
            [
                Path(sys.executable).with_name("vss"),
                "vios",
                "list",
                "--type",
                "video",
                "--raw",
            ]
        )
    )["sensors"]
    listing = sorted(
        ({"sensor_id": item["sensor_id"], "name": item["name"]} for item in listing),
        key=lambda item: item["sensor_id"],
    )
    sensor_ids = {s["sensor_id"] for s in listing}
    if clips is not None and sensor_ids != {c["sensor_id"] for c in clips}:
        raise ValueError("Registered gallery differs from the ingestion manifest")
    client = ElasticClient.from_runtime_behavior(_runtime_from(deployment))
    report = {"sensors": listing, "indices": {}}
    try:
        inventory = await client.raw.indices.get(index="*", expand_wildcards="all")
        for index, info in inventory.items():
            entry = {"uuid": info["settings"]["index"]["uuid"]}
            if index.startswith(("mdx-embed-filtered-", "mdx-raw-", "mdx-behavior-")):
                revisions = []
                by_source = Counter()
                scroll = None
                try:
                    response = await client.raw.search(
                        index=index,
                        query={"match_all": {}},
                        source=["sensor", "sensorId", "sensor_id"],
                        seq_no_primary_term=True,
                        track_total_hits=True,
                        size=1000,
                        scroll="1m",
                        allow_partial_search_results=False,
                    )
                    while True:
                        if response.get("timed_out") or response.get("_shards", {}).get(
                            "failed"
                        ):
                            raise RuntimeError("Incomplete index audit")
                        scroll = response.get("_scroll_id", scroll)
                        hits = response["hits"]["hits"]
                        if not hits:
                            break
                        for hit in hits:
                            revisions.append(
                                (hit["_id"], hit["_seq_no"], hit["_primary_term"])
                            )
                            source = hit["_source"]
                            sid = (
                                source.get("sensorId")
                                or source.get("sensor_id")
                                or (source.get("sensor") or {}).get("id")
                            )
                            by_source[str(sid)] += 1
                        response = await client.raw.scroll(
                            scroll_id=scroll, scroll="1m"
                        )
                finally:
                    if scroll:
                        await client.raw.clear_scroll(scroll_id=scroll)
                entry.update(
                    documents=len(revisions),
                    by_source=dict(sorted(by_source.items())),
                    revisions_sha256=hashlib.sha256(
                        json.dumps(sorted(revisions)).encode()
                    ).hexdigest(),
                )
            report["indices"][index] = entry
    finally:
        await client.aclose()
    return report


def ingest(args):
    workspace, deployment = load_workspace(args.workspace)
    configured_origin(deployment)
    output = args.output.resolve()
    if output.exists():
        raise ValueError(
            "Manifest already exists; ambiguous or partial ingestion is never repeated automatically"
        )
    root, clips, queries = validate_dataset(args.dataset_root)
    # Protect datasets even when callers choose an unsafe location inside runtime.
    runtime = Path(deployment["runtime"]).resolve()
    if root.is_relative_to(runtime) or runtime.is_relative_to(root):
        raise ValueError("Dataset overlaps disposable runtime")
    if any(
        (root / "clips" / c["filename"]).resolve().is_relative_to(runtime)
        for c in clips
    ):
        raise ValueError("A dataset symlink references disposable runtime")
    date = datetime.fromisoformat(args.upload_timestamp).date().isoformat()
    if date != "2025-01-01":
        raise ValueError(
            "This profile's ingestion/readiness indexes use the 2025-01-01 anchor; choose a time on that date"
        )
    initial = asyncio.run(audit_indexes(deployment))
    save(workspace / "gallery-empty.json", initial)
    if initial["sensors"] or any(
        i.get("documents", 0) for i in initial["indices"].values()
    ):
        raise ValueError(
            "Ingestion requires empty file registration and searched indexes"
        )
    manifest = {
        "schema_version": 1,
        "flow": "agent-3step",
        "complete": False,
        "upload_timestamp": args.upload_timestamp,
        "dataset_root": str(root),
        "clips": [],
        "queries": len(queries),
        "gallery": len(clips),
        "input_sha256": {
            name: digest(root / name)
            for name in ("gt/queries_gt.json", "additional_gt/clips_gt.json")
        },
        "file_sha256": {c["filename"]: c["sha256"] for c in clips},
        "phase": "validated",
    }
    save(output, manifest)
    backend = AgentThreeStepIngest(
        deployment["origin"], upload_timestamp=args.upload_timestamp, complete_retries=1
    )
    watcher = IngestWatcher(
        deployment["origin"],
        require={"embed", "raw", "behavior"},
        absent_grace_s=None,
        poll_s=1,
        quiet_s=15,
        upload_timestamp=args.upload_timestamp,
    )
    watcher.start()
    try:
        for clip in sorted(clips, key=lambda c: c["filename"]):
            manifest.update(phase="uploading", active_filename=clip["filename"])
            save(output, manifest)
            row = backend.upload(root / "clips" / clip["filename"])
            row.update(
                dataset_id=clip["dataset_id"],
                filename=clip["filename"],
                sha256=clip["sha256"],
            )
            manifest["clips"].append(row)
            save(output, manifest)
            if (
                not row.get("success")
                or type(row.get("chunks_processed")) is not int
                or row["chunks_processed"] <= 0
            ):
                raise RuntimeError(
                    f"Ingestion unverified for {clip['filename']}; inspect the partial manifest before resetting"
                )
            watcher.add(
                ExpectedVideo(
                    name=Path(row["filename"]).stem,
                    sensor_id=row["sensor_id"],
                    duration_s=row["duration_s"],
                    fps=row.get("fps"),
                    upload_start_mono=row.get("upload_start_mono"),
                    upload_start_utc=row.get("upload_start_utc"),
                    video_name=row.get("video_name", ""),
                )
            )
            print(
                f"Ingested {len(manifest['clips'])}/{len(clips)}: {clip['filename']}",
                flush=True,
            )
        manifest["phase"] = "waiting-for-indexes"
        save(output, manifest)
        readiness = watcher.wait(deadline_s=args.deadline)
        save(workspace / "ingestion-readiness.json", readiness)
        if readiness["outcome"] != "confirmed" or readiness.get("absent"):
            raise RuntimeError("Strict embedding/CV readiness did not pass")
        if len({r["sensor_id"] for r in manifest["clips"]}) != len(clips):
            raise RuntimeError("Duplicate sensor identities")
        refresh_after_ingestion(workspace, deployment)
        manifest.update(complete=True, phase="complete")
        manifest.pop("active_filename", None)
        save(output, manifest)
        save(
            workspace / "gallery-before-benchmark.json",
            asyncio.run(audit_indexes(deployment, manifest["clips"])),
        )
    except Exception as error:
        manifest.update(
            complete=False, phase="failed", error=f"{type(error).__name__}: {error}"
        )
        save(output, manifest)
        raise
    finally:
        watcher.stop()
    print(f"Complete: {len(clips)} clips, {len(queries)} queries; manifest {output}")


def main():
    os.umask(0o077)
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--workspace", required=True, type=Path)
    p.add_argument("--dataset-root", type=Path)
    p.add_argument("--output", required=True, type=Path)
    p.add_argument("--upload-timestamp", default="2025-01-01T00:00:00")
    p.add_argument("--deadline", type=float, default=1800)
    p.add_argument("--audit-only", action="store_true")
    args = p.parse_args()
    if args.audit_only:
        workspace, manifest = load_workspace(args.workspace)
        report = asyncio.run(audit_indexes(manifest))
        save(args.output, report)
        before = json.loads((workspace / "gallery-before-benchmark.json").read_text())
        if report != before:
            raise RuntimeError(
                "Gallery/index inventory changed during benchmarking; measurement invalid"
            )
        print("Gallery and searched index revisions unchanged")
    else:
        if not args.dataset_root:
            p.error("--dataset-root is required for ingestion")
        ingest(args)


if __name__ == "__main__":
    main()
