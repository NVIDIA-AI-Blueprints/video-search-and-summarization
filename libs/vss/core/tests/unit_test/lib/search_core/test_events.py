# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for the streaming event models."""

from __future__ import annotations

from vss_core.search_core.events import ErrorEvent
from vss_core.search_core.events import FinalResultEvent
from vss_core.search_core.events import PartialResultEvent
from vss_core.search_core.events import StatusEvent
from vss_core.search_core.models.search import SearchOutput
from vss_core.search_core.models.search import SearchResult


def _make_result() -> SearchResult:
    return SearchResult(
        video_name="cam1",
        description="a red car",
        start_time="2025-01-01T00:00:00Z",
        end_time="2025-01-01T00:00:05Z",
        sensor_id="s1",
        screenshot_url="http://example/1.jpg",
        similarity=0.9,
    )


def test_status_event_round_trip():
    ev = StatusEvent(stage="embed_search", message="starting")
    assert ev.type == "status"
    restored = StatusEvent.model_validate_json(ev.model_dump_json())
    assert restored == ev


def test_error_event_round_trip():
    ev = ErrorEvent(error_code="BackendUnreachableError", message="es: down")
    assert ev.type == "error"
    restored = ErrorEvent.model_validate_json(ev.model_dump_json())
    assert restored == ev


def test_partial_result_event_round_trip():
    ev = PartialResultEvent(results=[_make_result()])
    assert ev.type == "partial"
    restored = PartialResultEvent.model_validate_json(ev.model_dump_json())
    assert restored == ev
    assert restored.results[0].video_name == "cam1"


def test_final_result_event_round_trip():
    ev = FinalResultEvent(output=SearchOutput(data=[_make_result()], search_messages=["done"]))
    assert ev.type == "final"
    restored = FinalResultEvent.model_validate_json(ev.model_dump_json())
    assert restored == ev
    assert restored.output.data[0].sensor_id == "s1"


def test_forward_refs_resolved_partial():
    # The module-level model_rebuild must have resolved the SearchResult forward ref.
    json_text = PartialResultEvent(results=[_make_result()]).model_dump_json()
    assert "cam1" in json_text


def test_forward_refs_resolved_final():
    json_text = FinalResultEvent(output=SearchOutput(data=[_make_result()])).model_dump_json()
    assert "cam1" in json_text
