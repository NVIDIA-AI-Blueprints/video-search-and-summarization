#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Initialize frozen records in phases, then write verified Markdown projections."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from memory_access.upsert import (
    PersistedMemory,
    load_unified_memory_record,
    memory_service,
    persist_records,
    validate_unique_job_ids,
    verify_records,
    video_entries,
)


def write_job_markdown(persisted: PersistedMemory, markdown_root: Path) -> None:
    record = persisted.record
    lines = [
        "---",
        f"job_id: {json.dumps(persisted.job_id)}",
        f"authoritative_record_id: {json.dumps(persisted.record_id)}",
        "videos:",
    ]
    for video in video_entries(record):
        lines.extend(
            (
                f"  - video_id: {json.dumps(video['video_id'])}",
                f"    vios_sensor: {json.dumps(video['vios_sensor'])}",
            )
        )
    summary = record.output.answer.strip() if record.output and record.output.answer else ""
    lines.extend(("---", "", summary, ""))
    markdown_root.mkdir(parents=True, exist_ok=True)
    (markdown_root / f"{persisted.job_id}.md").write_text("\n".join(lines), encoding="utf-8")


def initialize_memories(source_dir: Path, markdown_root: Path, es_endpoint: str, memory_index: str) -> None:
    sources = tuple(sorted(source_dir.glob("*.json")))
    if not sources:
        raise ValueError(f"no frozen memories found in {source_dir}")
    records = tuple(load_unified_memory_record(source) for source in sources)
    validate_unique_job_ids(records)
    service = memory_service(es_endpoint, memory_index)
    persisted_by_job = persist_records(service, records)
    verified_by_job = verify_records(service, persisted_by_job)
    for job_id in sorted(verified_by_job):
        write_job_markdown(verified_by_job[job_id], markdown_root)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", required=True, type=Path)
    parser.add_argument("--markdown-root", required=True, type=Path)
    parser.add_argument("--es-endpoint", required=True)
    parser.add_argument("--memory-index", required=True)
    args = parser.parse_args()
    initialize_memories(args.source_dir, args.markdown_root, args.es_endpoint, args.memory_index)


if __name__ == "__main__":
    main()
