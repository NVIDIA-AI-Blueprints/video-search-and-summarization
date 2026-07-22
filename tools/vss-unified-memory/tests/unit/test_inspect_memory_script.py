# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import json
from io import StringIO
from unittest.mock import MagicMock

import pytest

from scripts import inspect_memory


def test_build_snapshot_aggregates_summaries_and_events() -> None:
    client = MagicMock()
    client.count.side_effect = [
        {"count": 1},
        {"count": 2},
    ]
    client.search.side_effect = [
        {
            "hits": {
                "hits": [
                    {
                        "_source": {
                            "summary_id": "summary:1",
                            "description": "Near miss",
                            "start_seconds": 1.0,
                            "end_seconds": 2.0,
                            "event_chunks": [{"chunk_id": "event:1:chunk:0000"}],
                        },
                        "sort": ["summary:1", 1, "event:1"],
                    },
                    {
                        "_source": {
                            "summary_id": "summary:1",
                            "description": "Stop",
                            "start_seconds": 3.0,
                            "end_seconds": 4.0,
                            "event_chunks": [],
                        },
                        "sort": ["summary:1", 2, "event:2"],
                    },
                ]
            }
        },
        {"hits": {"hits": []}},
        {
            "hits": {
                "hits": [
                    {
                        "_source": {
                            "summary_id": "summary:1",
                            "video_id": "video-1",
                            "media_name": "warehouse.mp4",
                            "created_at": "2026-01-01T00:00:00Z",
                            "event_count": 2,
                            "description": "Warehouse safety summary",
                            "start_seconds": 0.0,
                            "end_seconds": 10.0,
                            "summary_chunks": [{"chunk_id": "summary:1:chunk:0000"}],
                        },
                        "sort": ["2026-01-01T00:00:00Z", "summary:1"],
                    }
                ]
            }
        },
        {"hits": {"hits": []}},
    ]

    snapshot = inspect_memory.build_snapshot(client, "vss-unified-memory")

    assert snapshot["summary_count"] == 1
    assert snapshot["event_count"] == 2
    assert snapshot["total_documents"] == 3
    assert snapshot["summaries"][0]["summary_id"] == "summary:1"
    assert snapshot["summaries"][0]["summary_chars"] == len("Warehouse safety summary")
    assert snapshot["summaries"][0]["event_chars_total"] == len("Near miss") + len("Stop")
    assert snapshot["summaries"][0]["has_summary_chunks"] is True
    assert snapshot["summaries"][0]["has_event_chunks"] is True
    assert snapshot["summaries"][0]["chunk_count_estimate"] == 2


def test_run_cli_writes_json_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
    snapshot = {
        "index": "vss-unified-memory",
        "total_documents": 0,
        "summary_count": 0,
        "event_count": 0,
        "summaries": [],
    }
    monkeypatch.setattr(inspect_memory, "build_snapshot", lambda client, index: snapshot)

    class FakeSettings:
        elasticsearch_endpoint = "http://localhost:9200"
        elasticsearch_index = "vss-unified-memory"
        request_timeout_seconds = 30.0
        observability_log = None

    stdout = StringIO()
    exit_code = inspect_memory.run_cli(stdout, settings=FakeSettings())  # type: ignore[arg-type]
    assert exit_code == 0
    assert json.loads(stdout.getvalue()) == snapshot
