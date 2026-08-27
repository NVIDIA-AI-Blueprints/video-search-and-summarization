# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for strict introspection contracts and the text-only judge."""

from __future__ import annotations

from datetime import UTC
from datetime import datetime
import json

import httpx
from pydantic import ValidationError
import pytest

from vss_core.introspection.judge import InvalidJudgeResponseError
from vss_core.introspection.judge import OpenAIIntrospectionClient
from vss_core.introspection.models import GroundedGap
from vss_core.introspection.models import IntrospectionSettings
from vss_core.introspection.models import SufficiencyDecision
from vss_core.introspection.models import VLMEvidence
from vss_core.introspection.protocols import AnswerSynthesizer
from vss_core.introspection.protocols import SufficiencyJudge
from vss_core.memory.models import JobInfo
from vss_core.memory.models import MemoryInput
from vss_core.memory.models import SensorInfo
from vss_core.memory.models import TimestampPoint
from vss_core.memory.models import TimeWindow
from vss_core.memory.models import UnifiedMemoryRecord


def _record(
    *,
    record_id: str = "event-1",
    sensor: str = "camera-east",
    legacy_name: str = "legacy-east",
    start_time: datetime = datetime(2026, 8, 26, 12, 0, tzinfo=UTC),
    end_time: datetime = datetime(2026, 8, 26, 12, 1, tzinfo=UTC),
) -> UnifiedMemoryRecord:
    return UnifiedMemoryRecord(
        job=JobInfo(
            job_id="search-1",
            record_id=record_id,
            record_type="search_hit",
            group="search",
            status="completed",
            created_at=start_time,
        ),
        input=MemoryInput(
            sensors=[SensorInfo(id=sensor, info={"name": legacy_name})],
            window=TimeWindow(
                start=TimestampPoint(timestamp=start_time),
                end=TimestampPoint(timestamp=end_time),
            ),
        ),
    )


def _gap(**updates: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "question": "Was the person carrying a package?",
        "sensor": "camera-east",
        "start_time": "2026-08-26T12:00:10Z",
        "end_time": "2026-08-26T12:00:20Z",
    }
    payload.update(updates)
    return payload


def _decision(**updates: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "sufficient": False,
        "reason": "The event needs a closer visual check.",
        "evidence_record_ids": ["event-1"],
        "gaps": [_gap()],
    }
    payload.update(updates)
    return payload


def test_settings_defaults_and_strict_types() -> None:
    assert IntrospectionSettings().model_dump() == {
        "max_memory_records": 10,
        "max_vlm_queries": 3,
        "max_clip_duration_seconds": 60,
        "timeout_seconds": 180,
        "sufficiency_threshold": 0.7,
    }
    with pytest.raises(ValidationError):
        IntrospectionSettings(max_memory_records="10")  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "payload",
    (
        _decision(reason=" "),
        _decision(sufficient=True),
        _decision(evidence_record_ids=["event-1", "event-1"]),
        _decision(evidence_record_ids=[" "]),
        _decision(gaps=[_gap(question=" ")]),
        _decision(gaps=[_gap(sensor=" ")]),
        _decision(gaps=[_gap(start_time="2026-08-26 12:00:10")]),
        _decision(gaps=[_gap(end_time="2026-08-26T12:00:20")]),
        _decision(gaps=[_gap(start_time="2026-08-26T12:00:20Z", end_time="2026-08-26T12:00:10Z")]),
    ),
)
def test_decision_schema_validation(payload: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        SufficiencyDecision.model_validate(payload)


def test_gap_requires_start_time_and_end_time_field_names() -> None:
    with pytest.raises(ValidationError):
        GroundedGap.model_validate(
            {
                "question": "Was the person carrying a package?",
                "sensor": "camera-east",
                "start": "2026-08-26T12:00:10Z",
                "end": "2026-08-26T12:00:20Z",
            }
        )

    gap = GroundedGap.model_validate(_gap())
    assert gap.model_dump() == _gap()


def test_decision_does_not_fill_missing_approved_fields() -> None:
    with pytest.raises(ValidationError):
        SufficiencyDecision.model_validate(
            {
                "sufficient": True,
                "reason": "Enough evidence",
                "evidence_record_ids": ["event-1"],
            }
        )


@pytest.mark.parametrize(
    "updates",
    (
        {"start": "2026-08-26T12:00:10Z", "end": "2026-08-26T12:00:20Z"},
        {"start_time": "2026-08-26T12:00:20Z", "end_time": "2026-08-26T12:00:10Z"},
        {"start_time": "2026-08-26T12:00:10"},
        {"content": " "},
        {"intent": " "},
    ),
)
def test_vlm_evidence_validation(updates: dict[str, object]) -> None:
    payload: dict[str, object] = {
        "job_id": "vlm-1",
        "persisted": True,
        "sensor": "camera-east",
        "start_time": "2026-08-26T12:00:10Z",
        "end_time": "2026-08-26T12:00:20Z",
        "answer": "A person carried a small box.",
    }
    payload.update(updates)
    with pytest.raises(ValidationError):
        VLMEvidence.model_validate(payload)


def test_grounding_accepts_canonical_and_legacy_sensor_names() -> None:
    records = [_record()]
    canonical = SufficiencyDecision.model_validate(_decision())
    legacy = SufficiencyDecision.model_validate(
        _decision(
            gaps=[
                _gap(
                    sensor="legacy-east",
                    start_time="2026-08-26T12:00:10+00:00",
                    end_time="2026-08-26T12:00:20+00:00",
                )
            ]
        )
    )

    assert canonical.validate_grounding(records) is canonical
    assert legacy.validate_grounding(records) is legacy


@pytest.mark.parametrize(
    ("payload", "message"),
    (
        (_decision(evidence_record_ids=["invented"]), "unknown evidence_record_ids"),
        (_decision(gaps=[_gap(sensor="invented-camera")]), "not present"),
        (
            _decision(gaps=[_gap(start_time="2026-08-26T13:00:10Z", end_time="2026-08-26T13:00:20Z")]),
            "does not overlap",
        ),
    ),
)
def test_grounding_rejects_invented_ids_sensors_and_windows(
    payload: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        SufficiencyDecision.model_validate(payload).validate_grounding([_record()])


def test_window_must_overlap_record_for_the_same_sensor() -> None:
    other = _record(
        record_id="event-2",
        sensor="camera-west",
        legacy_name="legacy-west",
        start_time=datetime(2026, 8, 26, 13, 0, tzinfo=UTC),
        end_time=datetime(2026, 8, 26, 13, 1, tzinfo=UTC),
    )
    payload = _decision(gaps=[_gap(start_time="2026-08-26T13:00:10Z", end_time="2026-08-26T13:00:20Z")])

    with pytest.raises(ValueError, match="does not overlap"):
        SufficiencyDecision.model_validate(payload).validate_grounding([_record(), other])


def test_client_satisfies_judge_and_synthesizer_protocols() -> None:
    client = OpenAIIntrospectionClient(base_url="https://rt-vlm.example/v1", model="first-rt-vlm-model")

    assert isinstance(client, SufficiencyJudge)
    assert isinstance(client, AnswerSynthesizer)


@pytest.mark.asyncio
async def test_judge_retries_once_after_invalid_json_then_accepts_fenced_json() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        body = json.loads(request.content)
        assert body["temperature"] == 0
        assert body["response_format"] == {"type": "json_object"}
        prompt = body["messages"][0]["content"]
        assert "threshold of 0.70" in prompt
        assert "start_time and end_time" in prompt
        content = "not-json" if calls == 1 else f"```json\n{json.dumps(_decision())}\n```"
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]}, request=request)

    client = OpenAIIntrospectionClient(
        base_url="https://rt-vlm.example",
        model="first-rt-vlm-model",
        transport=httpx.MockTransport(handler),
    )
    try:
        result = await client.judge(query="What happened?", records=[_record()])
    finally:
        await client.aclose()

    assert calls == 2
    assert result.evidence_record_ids == ["event-1"]
    assert result.gaps[0].start_time == "2026-08-26T12:00:10Z"
    assert result.gaps[0].end_time == "2026-08-26T12:00:20Z"


@pytest.mark.asyncio
async def test_judge_fails_after_second_invalid_response() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": '{"sufficient": false}'}}]},
            request=request,
        )

    client = OpenAIIntrospectionClient(
        base_url="https://rt-vlm.example/v1",
        model="first-rt-vlm-model",
        transport=httpx.MockTransport(handler),
    )
    try:
        with pytest.raises(InvalidJudgeResponseError):
            await client.judge(query="What happened?", records=[_record()])
    finally:
        await client.aclose()

    assert calls == 2


@pytest.mark.asyncio
async def test_ungrounded_gap_is_rejected_after_one_retry() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        payload = _decision(gaps=[_gap(sensor="invented-camera")])
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": json.dumps(payload)}}]},
            request=request,
        )

    client = OpenAIIntrospectionClient(
        base_url="https://rt-vlm.example/v1",
        model="first-rt-vlm-model",
        transport=httpx.MockTransport(handler),
    )
    try:
        with pytest.raises(InvalidJudgeResponseError, match="not present"):
            await client.judge(query="What happened?", records=[_record()])
    finally:
        await client.aclose()

    assert calls == 2


@pytest.mark.asyncio
async def test_synthesize_uses_supplied_evidence_only() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert "response_format" not in body
        prompt = body["messages"][0]["content"]
        assert "camera-east" in prompt
        assert "A person carried a small box." in prompt
        assert "An unresolved gap is unknown" in prompt
        assert "never turn missing evidence into a positive or negative claim" in prompt
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": " A person carried a small box. "}}]},
            request=request,
        )

    client = OpenAIIntrospectionClient(
        base_url="https://rt-vlm.example/v1",
        model="first-rt-vlm-model",
        transport=httpx.MockTransport(handler),
    )
    evidence = VLMEvidence.model_validate(
        {
            "job_id": "vlm-1",
            "persisted": True,
            "sensor": "camera-east",
            "start_time": "2026-08-26T12:00:10Z",
            "end_time": "2026-08-26T12:00:20Z",
            "answer": "A person carried a small box.",
        }
    )
    try:
        answer = await client.synthesize(
            query="What happened?",
            memory_evidence=[_record()],
            vlm_evidence=[evidence],
            unresolved_gaps=["package color remains unknown"],
        )
    finally:
        await client.aclose()

    assert answer == "A person carried a small box."


@pytest.mark.asyncio
async def test_http_failure_is_not_retried() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(503, request=request)

    client = OpenAIIntrospectionClient(
        base_url="https://rt-vlm.example/v1",
        model="first-rt-vlm-model",
        transport=httpx.MockTransport(handler),
    )
    try:
        with pytest.raises(httpx.HTTPStatusError):
            await client.judge(query="What happened?", records=[_record()])
    finally:
        await client.aclose()

    assert calls == 1
