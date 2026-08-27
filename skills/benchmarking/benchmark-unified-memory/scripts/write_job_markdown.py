#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Write one verified parent memory record as an OpenClaw Markdown projection."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import tempfile

from vss_core.memory import UnifiedMemoryRecord


def _safe_projection_job_id(record: UnifiedMemoryRecord) -> str:
    ext = record.output.ext if record.output is not None else None
    value = ext.get("job_id") if ext is not None else None
    job_id = str(value or record.job.job_id)
    if not job_id or job_id in {".", ".."} or Path(job_id).name != job_id:
        raise ValueError("projection job ID must be a safe filename")
    return job_id


def _video_entries(record: UnifiedMemoryRecord) -> tuple[tuple[str, str], ...]:
    ext = record.output.ext if record.output is not None else None
    raw = ext.get("videos") if ext is not None else None
    if not isinstance(raw, list) or not raw:
        raise ValueError("parent record must contain output.ext.videos")

    entries: list[tuple[str, str]] = []
    for item in raw:
        if not isinstance(item, dict) or not item.get("video_id") or not item.get("vios_sensor"):
            raise ValueError("every video must contain video_id and vios_sensor")
        entries.append((str(item["video_id"]), str(item["vios_sensor"])))
    if len({video_id for video_id, _ in entries}) != len(entries):
        raise ValueError("video_id mappings must be unique")
    return tuple(entries)


def render_job_markdown(record: UnifiedMemoryRecord) -> tuple[str, str]:
    """Return ``(projection_job_id, markdown)`` for one parent record."""
    if record.job.is_child:
        raise ValueError("Markdown projections can only be written for parent records")

    projection_job_id = _safe_projection_job_id(record)
    lines = [
        "---",
        f"job_id: {json.dumps(projection_job_id)}",
        f"authoritative_record_id: {json.dumps(record.job.job_id)}",
        "videos:",
    ]
    for video_id, vios_sensor in _video_entries(record):
        lines.extend(
            (
                f"  - video_id: {json.dumps(video_id)}",
                f"    vios_sensor: {json.dumps(vios_sensor)}",
            )
        )
    summary = record.output.answer.strip() if record.output and record.output.answer else ""
    lines.extend(("---", "", summary, ""))
    return projection_job_id, "\n".join(lines)


def write_job_markdown(input_path: Path, markdown_root: Path) -> Path:
    record = UnifiedMemoryRecord.model_validate_json(input_path.read_text(encoding="utf-8"))
    projection_job_id, markdown = render_job_markdown(record)
    markdown_root.mkdir(parents=True, exist_ok=True)
    destination = markdown_root / f"{projection_job_id}.md"

    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=markdown_root,
            prefix=f".{projection_job_id}.",
            suffix=".tmp",
            delete=False,
        ) as stream:
            stream.write(markdown)
            temporary = Path(stream.name)
        os.replace(temporary, destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return destination


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--markdown-root", required=True, type=Path)
    args = parser.parse_args()
    write_job_markdown(args.input, args.markdown_root)


if __name__ == "__main__":
    main()
