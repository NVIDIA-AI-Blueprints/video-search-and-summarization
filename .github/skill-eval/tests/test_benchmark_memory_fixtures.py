# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from collections import Counter
import json
from pathlib import Path


MEMORY_DIR = (
    Path(__file__).parents[3]
    / "skills"
    / "benchmarking"
    / "benchmark-unified-memory"
    / "datasets"
    / "physical-ai-video-mme-v2"
    / "memory"
)


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_frozen_memories_use_parent_child_records() -> None:
    summaries = [_load(path) for path in sorted(MEMORY_DIR.glob("*_summary.json"))]
    event_paths = sorted(MEMORY_DIR.glob("*_event-*.json"))
    events = [_load(path) for path in event_paths]

    assert len(summaries) == 2
    assert len(events) == 20
    assert all("events" not in record["output"]["ext"] for record in summaries)

    expected_counts = {
        record["job"]["job_id"]: record["output"]["ext"]["event_count"]
        for record in summaries
    }
    actual_counts = Counter(record["job"]["job_id"] for record in events)
    assert actual_counts == expected_counts

    identities = {
        (record["job"]["job_id"], record["job"]["record_id"])
        for record in events
    }
    assert len(identities) == len(events)
    assert all(record["job"]["record_type"] == "event" for record in events)

    for path, record in zip(event_paths, events, strict=True):
        ordinal = path.stem.rsplit("-", 1)[1]
        assert record["job"]["record_id"].endswith(f":{ordinal}")
        assert record["output"]["answer"]
        assert record["output"]["ext"]["event_type"]
        assert record["output"]["ext"]["source_event_id"] is not None
