# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Opt-in synthetic scale receipt for the CLI-to-artifact selection contract."""

import json
import os
import resource
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from flows.artifacts import QueryRecord
from flows.normalize import normalize_results
from flows.query import CliQueryBackend


@pytest.mark.skipif(
    os.environ.get("VSS_SOURCE_SELECTION_STRESS") != "1",
    reason="opt-in 100,000-hit artifact scale test",
)
def test_full_pool_is_serialized_once_at_candidate_limit(monkeypatch):
    # The default benchmark test suite stays independent of the core install.
    from vss_core.search_core._internal.source_selection import (
        SourceSelectionContext,
        select_sources,
        source_selection_context,
    )
    from vss_core.search_core.models.search import SearchOutput, SearchResult

    count, returned = 100000, 10
    baseline_rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    started = time.perf_counter()
    original = [
        SearchResult(
            sensor_id=f"source-{i // 1000:03d}",
            source_id=f"source-{i // 1000:03d}",
            video_name=f"source-{i // 1000:03d}",
            description="synthetic",
            start_time="",
            end_time="",
            screenshot_url="",
            similarity=0.5,
            object_ids=[str(i)],
        )
        for i in range(count)
    ]
    context = SourceSelectionContext(
        max_candidates=count,
        expected_scans=1,
        scans=[
            {
                "exhaustive": True,
                "snapshot_verified": True,
                "budget": count,
                "eligible_documents": count,
                "scored_documents": count,
                "snapshot_digest": "1" * 64,
                "snapshot_scope_digest": "2" * 64,
                "snapshot_indices": [{"name": "synthetic", "uuid": "synthetic-index"}],
            }
        ],
    )
    token = source_selection_context.set(context)
    try:
        selection = select_sources(
            original, cap=1, requested_top_k=returned, candidate_limit=count
        )
    finally:
        source_selection_context.reset(token)
    output = SearchOutput(
        data=[original[i] for i in selection.selected_indices],
        source_selection=selection,
    )
    wire = output.model_dump_json()
    monkeypatch.setattr(
        "flows.query.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout=wire, stderr=""),
    )
    backend = CliQueryBackend(
        ["fixture-vss"],
        search_path="attribute",
        attributes=["hat"],
        candidate_limit=count,
        retrieval_pool_size=returned,
        max_hits_per_source=1,
    )
    hits, latency = backend.search("person in a hat", query_id="scale")
    metadata = backend.metadata_by_query["scale"]
    normalized = [
        {key: value for key, value in hit.items() if key != "_raw"}
        for hit in normalize_results(hits)
    ]
    selected = [
        {key: value for key, value in hit.items() if key != "_raw"}
        for hit in normalize_results(metadata.pop("_selected_results"))
    ]
    record = QueryRecord(
        index=0,
        query="person in a hat",
        status="answered",
        hits=normalized,
        selected_hits=selected,
        selection=metadata.pop("selection"),
        search_latency_s=latency,
        evidence=metadata or None,
    )
    serialized = json.dumps(record.to_dict())
    assert len(record.hits) == count and len(record.selected_hits) == returned
    assert record.selection["selected_indices"] == list(range(0, 10000, 1000))
    # Only the full original pool and ten selected rows may serialize hit IDs;
    # no original_pool list may survive inside evidence or selection metadata.
    assert serialized.count('"sensor_id":') == count + returned
    assert '"original_pool": [' not in serialized
    assert QueryRecord.from_dict(json.loads(serialized)).selection == record.selection
    Path("/tmp/vss-source-selection-stress.json").write_text(
        json.dumps(
            {
                "scope": "synthetic complete-pool model -> CLI parser -> artifact; no deployed inference",
                "candidates": count,
                "selected": returned,
                "cli_json_bytes": len(wire.encode()),
                "artifact_json_bytes": len(serialized.encode()),
                "elapsed_s": time.perf_counter() - started,
                "rss_baseline": baseline_rss,
                "rss_peak": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                "rss_units": "KiB on Linux; bytes on macOS",
                "platform": sys.platform,
            },
            indent=2,
        )
        + "\n"
    )
