# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Profile identity and completed-cycle contracts; fixtures are not production results."""
import asyncio
import copy
from datetime import datetime, timedelta

import pytest

from backend.app import _validate_result
from backend.profiles import BALANCED_SHA256, BALANCED_VIOS_20260922_SHA256, ORIGINAL_SHA256, profile_for_identity
from backend.tools import answer_question, create_mcp_server


def test_profiles_require_reviewed_identity_and_allow_verified_remux():
    assert profile_for_identity(ORIGINAL_SHA256).analysis_kind == "within-shot-heights"
    assert profile_for_identity(BALANCED_SHA256).analysis_kind == "bottle-cycles"
    assert profile_for_identity(BALANCED_VIOS_20260922_SHA256).analysis_kind == "bottle-cycles"
    assert profile_for_identity("f" * 64) is None
    proof = {"version": "video-identity-v2", "verified": True, "candidate_sha256": "f" * 64,
             "reference_sha256": BALANCED_SHA256, "time_alignment": "exact-zero-offset", "offset_seconds": 0,
             "method": "all-decoded-frames-sha256"}
    assert profile_for_identity("f" * 64, proof).analysis_kind == "bottle-cycles"
    for bad in ({"offset_seconds": 1}, {"reference_sha256": "1" * 64}, {"method": "filename"}, {"verified": False}):
        assert profile_for_identity("f" * 64, {**proof, **bad}) is None


@pytest.fixture
def cycle_result():
    cycles = []
    for i, (level, status) in enumerate([(0.84, "normal"), (0.6, "underfill"), (0.91, "overflow"), (None, "uncertain")]):
        cycles.append({"id": f"cycle-{i+1}", "label": f"Bottle {i+1}", "start_time": i * 10,
                       "end_time": i * 10 + 9, "fill_start_time": i * 10 + 2, "fill_end_time": i * 10 + 6,
                       "measurement_time": i * 10 + 7, "final_level": level, "status": status, "confidence": 0.8,
                       "reason": "Synthetic protocol fixture", "evidence_start": i * 10 + 5, "evidence_end": i * 10 + 9,
                       "reference_level": 0.82, "tolerance": 0.03, "overflow_time": i * 10 + 6 if status == "overflow" else None})
    cycles[-1].update(measurement_time=None, fill_start_time=None, fill_end_time=None)
    return {"version": "test-only", "source_sha256": BALANCED_SHA256, "algorithm": "synthetic-fixture",
            "sample_fps": 2, "runtime_seconds": 0.1, "samples": [], "events": [],
            "bottles": [{"id": c["id"]} for c in cycles], "quality": {"fixture": True},
            "analysis_kind": "bottle-cycles", "cycles": cycles,
            "summary": {"total": 4, "normal": 1, "underfill": 1, "overflow": 1, "uncertain": 1},
            "provenance": {"stream_id": "631962f2-5e96-4394-9eba-9fc0febf7306", "actual_start_time": "2026-09-22T00:00:00Z"}}


def test_cycle_summary_and_queries_use_final_measurements_not_mid_fill(cycle_result):
    _validate_result(cycle_result, {"sha256": BALANCED_SHA256, "duration": 40})
    summary = answer_question(cycle_result, "summarize the bottles")
    assert summary["summary"]["uncertain"] == 1
    assert len(summary["evidence"]) == 3  # No made-up completion timestamp for the uncertain cycle.
    low = answer_question(cycle_result, "which bottles finished below reference?", 0.8)
    assert [c["id"] for c in low["cycles"]] == ["cycle-2"]
    assert low["evidence"][0]["t"] == 17
    assert low["evidence"][0]["recorded_at"] == "2026-09-22T00:00:17+00:00"
    under = answer_question(cycle_result, "show underfills")
    assert under["cycles"][0]["id"] == "cycle-2"
    biggest = answer_question(cycle_result, "Which bottle had the largest shortfall?")
    assert biggest["cycles"][0]["shortfall_percentage_points"] == pytest.approx(22)
    over = answer_question(cycle_result, "show overflows")
    assert over["evidence"][0]["t"] == 26
    assert over["cycles"][0]["id"] == "cycle-3"
    assert answer_question(cycle_result, "what caused the overflow pressure?")["supported"] is False


@pytest.mark.parametrize("bad", ["summary", "time", "identity", "nan", "missing-measurement", "overflow-time"])
def test_cycle_validation_rejects_invalid_results(cycle_result, bad):
    result = copy.deepcopy(cycle_result)
    if bad == "summary":
        result["summary"]["underfill"] = 2
    elif bad == "time":
        result["cycles"][0]["measurement_time"] = 400
    elif bad == "identity":
        result["cycles"][0]["id"] = "not-in-inventory"
    elif bad == "nan":
        result["cycles"][0]["final_level"] = float("nan")
    elif bad == "missing-measurement":
        result["cycles"][0]["measurement_time"] = None
    else:
        result["cycles"][2]["overflow_time"] = 1
    with pytest.raises(ValueError):
        _validate_result(result, {"sha256": BALANCED_SHA256, "duration": 40})


def test_largest_shortfall_preserves_equal_measurements(cycle_result):
    cycle_result["cycles"][0]["final_level"] = 0.6
    answer = answer_question(cycle_result, "Which bottle had the largest shortfall?")
    assert [c["id"] for c in answer["cycles"]] == ["cycle-1", "cycle-2"]
    assert "tie" in answer["answer"]


def test_default_cycle_question_uses_each_calibrated_reference_without_rounding(cycle_result):
    question = "Which bottles finished below the reference level? Show their evidence."
    cycle_result["cycles"][0].update(final_level=0.8789, reference_level=0.8789)
    # Each calibrated reference is respected rather than guessed from the first cycle.
    cycle_result["cycles"][2].update(final_level=0.79, reference_level=0.78)
    answer = answer_question(cycle_result, question)
    assert answer["supported"] is True
    assert [c["id"] for c in answer["cycles"]] == ["cycle-2"]
    assert answer["reference_criterion"]["reference_source"] == "calibrated-per-cycle"
    assert answer["reference_criterion"]["references_by_cycle"]["cycle-1"] == 0.8789
    assert "calibrated demo reference" in answer["answer"]
    explicit = answer_question(cycle_result, question, reference_level=0.88)
    assert [c["id"] for c in explicit["cycles"]] == ["cycle-1", "cycle-2", "cycle-3"]
    assert explicit["reference_criterion"]["reference_source"] == "supplied"
    assert explicit["reference_criterion"]["reference_level"] == 0.88


def test_cycle_query_mcp_fetches_measured_cycle_evidence_interval(cycle_result):
    stream_id = cycle_result["provenance"]["stream_id"]
    clock = cycle_result["provenance"]["actual_start_time"]
    source = {"id": "test-source", "sha256": BALANCED_SHA256, "stream_id": stream_id,
              "actual_start_time": clock, "duration": 40}
    calls = []

    async def evidence(start, end):
        calls.append((start, end))
        origin = datetime.fromisoformat(clock.replace("Z", "+00:00"))
        return {"available": True, "source_id": source["id"], "stream_id": stream_id, "source_clock_origin": clock,
                "source_offsets": {"start": start, "end": end},
                "requested": {"streamId": stream_id, "startTime": (origin + timedelta(seconds=start)).isoformat(),
                              "endTime": (origin + timedelta(seconds=end)).isoformat()},
                "actual": {"streamId": stream_id}, "public_url": "https://test.example/actual.mp4"}

    server = create_mcp_server(lambda: source, lambda: cycle_result, measurement_evidence_provider=evidence)
    tool = server._tool_manager.get_tool("query_measurements")
    answer = asyncio.run(tool.run({"question": "Show underfills"}))
    assert calls == [(15.0, 19.0)]
    assert answer["video_evidence"]["available"] is True
    assert "https://test.example/actual.mp4" in answer["answer"]


@pytest.mark.parametrize("failure", [None, "unavailable", "wrong-interval", "exception"])
def test_cycle_mcp_returns_distinct_bottle_links_and_preserves_partial_failures(cycle_result, failure):
    cycle_result["cycles"][3].update(status="underfill", final_level=0.7, measurement_time=37, fill_start_time=32, fill_end_time=36)
    cycle_result["summary"].update(underfill=2, uncertain=0)
    stream_id = cycle_result["provenance"]["stream_id"]
    clock = cycle_result["provenance"]["actual_start_time"]
    origin = datetime.fromisoformat(clock.replace("Z", "+00:00"))
    source = {"id": "test-source", "sha256": BALANCED_SHA256, "stream_id": stream_id,
              "actual_start_time": clock, "duration": 40}
    active, peak, calls = 0, 0, []

    async def evidence(start, end):
        nonlocal active, peak
        calls.append((start, end))
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.01)
        active -= 1
        if start == 35 and failure:
            if failure == "unavailable":
                return {"available": False, "reason": "Fixture clip unavailable"}
            if failure == "exception":
                raise RuntimeError("Fixture failure")
            start, end = 15, 19  # Another bottle's interval must be rejected.
        return {"available": True, "source_id": source["id"], "stream_id": stream_id, "source_clock_origin": clock,
                "source_offsets": {"start": start, "end": end},
                "requested": {"streamId": stream_id, "startTime": (origin + timedelta(seconds=start)).isoformat(),
                              "endTime": (origin + timedelta(seconds=end)).isoformat()},
                "actual": {"streamId": stream_id}, "public_url": f"https://test.example/clip-{start}.mp4"}

    server = create_mcp_server(lambda: source, lambda: cycle_result, measurement_evidence_provider=evidence)
    tool = server._tool_manager.get_tool("query_measurements")
    answer = asyncio.run(tool.run({"question": "Show underfills"}))
    assert sorted(calls) == [(15.0, 19.0), (35.0, 39.0)]
    assert 2 <= peak <= 3
    clips = answer["video_evidences"]
    assert [clip["bottle_id"] for clip in clips] == ["cycle-2", "cycle-4"]
    assert clips[0]["available"] is True
    assert clips[0]["public_url"] == "https://test.example/clip-15.0.mp4"
    assert answer["video_evidence"]["public_url"] == clips[0]["public_url"]
    assert answer["cycles"][0]["video_evidence"] == clips[0]
    assert answer["evidence"][1]["video_evidence"] == clips[1]
    if failure:
        assert clips[1]["available"] is False
        assert "public_url" not in clips[1] and "local_clip_url" not in clips[1]
        assert "Bottle 4: evidence unavailable" in answer["answer"]
    else:
        assert clips[1]["public_url"] == "https://test.example/clip-35.0.mp4"
        assert clips[1]["public_url"] != clips[0]["public_url"]
        assert "[Bottle 4 clip](https://test.example/clip-35.0.mp4)" in answer["answer"]
