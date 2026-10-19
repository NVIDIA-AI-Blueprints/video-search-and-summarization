# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Bounds, provenance and failure behavior for explicit second looks."""

from __future__ import annotations

import asyncio

import httpx
import pytest

from vss_core._foundation.errors import BackendUnreachableError
from vss_core._foundation.errors import ConfigurationError
from vss_core.vlm import second_look


class _Analyzer:
    def __init__(self, *answers: str | BaseException) -> None:
        self.answers = iter(answers)
        self.calls: list[dict[str, str]] = []

    async def analyze(self, **kwargs: str) -> str:
        self.calls.append(kwargs)
        answer = next(self.answers)
        if isinstance(answer, BaseException):
            raise answer
        return answer


_REQUEST = {
    "sensor_id": "camera-1",
    "start_timestamp": "100",
    "end_timestamp": "200",
    "second_start_timestamp": "120",
    "second_end_timestamp": "123",
    "prompt": "What is on the table?",
    "time_format": "offset",
}


@pytest.mark.asyncio
async def test_fresh_reads_keep_provenance_and_do_not_carry_forward_first_answer() -> None:
    analyzer = _Analyzer("FIRST-ANSWER-ONLY", "A blue cup.")
    result = await second_look(analyzer, **_REQUEST)
    assert result.answer == result.second_answer == "A blue cup."
    assert result.first_answer == "FIRST-ANSWER-ONLY"
    assert result.model_calls == len(analyzer.calls) == 2
    assert result.fallback_reason is None
    assert (result.sensor_id, result.time_format) == ("camera-1", "offset")
    assert (result.start_timestamp, result.end_timestamp) == ("100", "200")
    assert (result.second_start_timestamp, result.second_end_timestamp) == ("120", "123")
    assert analyzer.calls[0]["prompt"].startswith(_REQUEST["prompt"])
    assert "source-relative seconds 100 to 200" in analyzer.calls[0]["prompt"]
    second = analyzer.calls[1]
    assert (second["start_timestamp"], second["end_timestamp"]) == ("120", "123")
    assert "source-relative seconds 120 to 123" in second["prompt"]
    assert "FIRST-ANSWER-ONLY" not in second["prompt"]


@pytest.mark.asyncio
async def test_reuses_first_answer_and_separately_configured_second_analyzer() -> None:
    first = _Analyzer()
    second = _Analyzer("targeted")
    result = await second_look(first, **_REQUEST, first_answer="existing", second_analyzer=second)
    assert result.model_calls == 1
    assert result.first_answer == "existing"
    assert result.answer == "targeted"
    assert not first.calls
    assert len(second.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes",
    [
        {"start_timestamp": "200"},
        {"second_end_timestamp": "120"},
        {"second_start_timestamp": "99"},
        {"second_end_timestamp": "201"},
        {"second_start_timestamp": "100", "second_end_timestamp": "200"},
        {"start_timestamp": "-1"},
        {"end_timestamp": "inf"},
        {"second_start_timestamp": "nan"},
        {"second_end_timestamp": "not-a-time"},
        {"time_format": "unknown"},
        {"sensor_id": " "},
        {"prompt": " "},
        {"first_answer": " "},
    ],
)
async def test_rejects_invalid_input_before_any_model_call(changes: dict[str, str]) -> None:
    analyzer = _Analyzer()
    with pytest.raises(ValueError):
        await second_look(analyzer, **(_REQUEST | changes))
    assert not analyzer.calls


@pytest.mark.asyncio
async def test_iso_comparisons_use_absolute_instants_and_allow_one_shared_boundary() -> None:
    result = await second_look(
        _Analyzer("first", "second"),
        sensor_id="camera-1",
        start_timestamp="2026-10-19T00:00:00Z",
        end_timestamp="2026-10-19T00:01:00Z",
        second_start_timestamp="2026-10-19T01:00:00+01:00",
        second_end_timestamp="2026-10-19T01:00:03+01:00",
        prompt="What happened?",
    )
    assert result.answer == "second"


@pytest.mark.asyncio
@pytest.mark.parametrize("target_end", ["2026-10-19T00:00:03", "2026-10-19T01:01:00+01:00"])
async def test_iso_rejects_naive_times_and_equivalent_whole_window(target_end: str) -> None:
    analyzer = _Analyzer()
    with pytest.raises(ValueError):
        await second_look(
            analyzer,
            sensor_id="camera-1",
            start_timestamp="2026-10-19T00:00:00Z",
            end_timestamp="2026-10-19T00:01:00Z",
            second_start_timestamp="2026-10-19T01:00:00+01:00",
            second_end_timestamp=target_end,
            prompt="What happened?",
        )
    assert not analyzer.calls


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    [
        BackendUnreachableError("vlm", "https://secret.example/?token=secret"),
        ConfigurationError("secret"),
        httpx.ReadTimeout("secret"),
        TimeoutError("secret"),
        ConnectionError("secret"),
    ],
)
async def test_optional_operational_failure_keeps_first_without_retry_or_sensitive_details(failure: Exception) -> None:
    analyzer = _Analyzer("first", failure)
    result = await second_look(analyzer, **_REQUEST)
    assert result.answer == "first"
    assert result.second_answer is None
    assert result.fallback_reason and "secret" not in result.fallback_reason
    assert result.model_calls == len(analyzer.calls) == 2


@pytest.mark.asyncio
async def test_baseline_failure_propagates_without_optional_read() -> None:
    analyzer = _Analyzer(BackendUnreachableError("vlm", "unavailable"))
    with pytest.raises(BackendUnreachableError):
        await second_look(analyzer, **_REQUEST)
    assert len(analyzer.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure", [asyncio.CancelledError(), RuntimeError("bug"), TypeError("bug"), ValueError("bug")]
)
async def test_cancellation_and_programming_errors_propagate(failure: BaseException) -> None:
    analyzer = _Analyzer("first", failure)
    with pytest.raises(type(failure)):
        await second_look(analyzer, **_REQUEST)
    assert len(analyzer.calls) == 2


@pytest.mark.asyncio
async def test_empty_answers_cannot_be_selected() -> None:
    with pytest.raises(BackendUnreachableError):
        await second_look(_Analyzer(" "), **_REQUEST)
    result = await second_look(_Analyzer("first", " "), **_REQUEST)
    assert result.answer == "first"
    assert result.second_answer is None
    assert result.fallback_reason
