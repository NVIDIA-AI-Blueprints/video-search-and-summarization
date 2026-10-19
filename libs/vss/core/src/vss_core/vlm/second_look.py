# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""One optional, explicitly scoped reread of a recorded video interval."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from math import isfinite
from typing import TYPE_CHECKING
from typing import Literal

import httpx

from vss_core._foundation.errors import BackendUnreachableError
from vss_core._foundation.errors import ConfigurationError

if TYPE_CHECKING:
    from .protocols import VLMAnalyzer


@dataclass(frozen=True)
class SecondLookResult:
    """Both observations; ``model_calls`` counts logical calls made by this helper."""

    sensor_id: str
    start_timestamp: str
    end_timestamp: str
    second_start_timestamp: str
    second_end_timestamp: str
    time_format: Literal["iso", "offset"]
    first_answer: str
    second_answer: str | None
    answer: str
    fallback_reason: str | None
    model_calls: int


def _validate_windows(timestamps: tuple[str, str, str, str], time_format: Literal["iso", "offset"]) -> None:
    if time_format == "offset":
        offsets = tuple(float(value) for value in timestamps)
        if not all(isfinite(value) and value >= 0 for value in offsets):
            raise ValueError("offsets must be finite, non-negative source-relative seconds")
        start, end, second_start, second_end = offsets
        valid_original = start < end
        valid_second = start <= second_start < second_end <= end and (start, end) != (second_start, second_end)
    elif time_format == "iso":
        instants = tuple(datetime.fromisoformat(value.replace("Z", "+00:00")) for value in timestamps)
        if any(value.utcoffset() is None for value in instants):
            raise ValueError("ISO timestamps must include a timezone")
        first, last, second_first, second_last = instants
        valid_original = first < last
        valid_second = first <= second_first < second_last <= last and (first, last) != (second_first, second_last)
    else:
        raise ValueError("time_format must be 'iso' or 'offset'")
    if not valid_original:
        raise ValueError("the original window must have a positive duration")
    if not valid_second:
        raise ValueError("the second window must be strictly narrower than and contained in the original window")


def _window_prompt(prompt: str, start: str, end: str, time_format: Literal["iso", "offset"]) -> str:
    coordinates = "source-relative seconds" if time_format == "offset" else "absolute ISO 8601 times"
    return (
        f"{prompt}\n\nThis clip covers {coordinates} {start} to {end}. "
        "Clip-local time zero corresponds to the start of this interval. Answer using this clip."
    )


async def second_look(
    analyzer: VLMAnalyzer,
    *,
    sensor_id: str,
    start_timestamp: str,
    end_timestamp: str,
    second_start_timestamp: str,
    second_end_timestamp: str,
    prompt: str,
    time_format: Literal["iso", "offset"] = "iso",
    first_answer: str | None = None,
    second_analyzer: VLMAnalyzer | None = None,
) -> SecondLookResult:
    """Prefer one fresh targeted answer, retaining the first on operational failure.

    The caller must supply an original window already validated against one recorded
    segment, and select the target in the same source coordinates. This helper checks
    containment, not recording availability. A reused answer must belong to this
    source, original window and question. ISO bounds require explicit timezones;
    offsets are seconds from the source origin, not from the original window.

    A separately configured ``second_analyzer`` may request denser sampling. No
    analyzer settings are mutated. There are at most two logical analysis calls
    (one when reusing an answer); existing adapter transport retries still apply.
    """
    if not sensor_id.strip() or not prompt.strip():
        raise ValueError("sensor_id and prompt must be non-empty")
    if first_answer is not None and not first_answer.strip():
        raise ValueError("a reused first_answer must be non-empty")
    _validate_windows((start_timestamp, end_timestamp, second_start_timestamp, second_end_timestamp), time_format)

    model_calls = 1 if first_answer is not None else 2
    if first_answer is None:
        first_answer = await analyzer.analyze(
            sensor_id=sensor_id,
            start_timestamp=start_timestamp,
            end_timestamp=end_timestamp,
            prompt=_window_prompt(prompt, start_timestamp, end_timestamp, time_format),
            time_format=time_format,
        )
        if not first_answer.strip():
            raise BackendUnreachableError("vlm", "First read returned no answer")

    target_analyzer = analyzer if second_analyzer is None else second_analyzer
    second_answer = None
    fallback_reason = None
    try:
        second_answer = await target_analyzer.analyze(
            sensor_id=sensor_id,
            start_timestamp=second_start_timestamp,
            end_timestamp=second_end_timestamp,
            prompt=_window_prompt(prompt, second_start_timestamp, second_end_timestamp, time_format),
            time_format=time_format,
        )
        if not second_answer.strip():
            second_answer = None
            fallback_reason = "Second read returned no answer; retained first answer."
    except (BackendUnreachableError, ConfigurationError, httpx.HTTPError, TimeoutError, ConnectionError) as exc:
        # Exception messages may contain credentials or signed media URLs.
        fallback_reason = f"Second read failed ({type(exc).__name__}); retained first answer."

    return SecondLookResult(
        sensor_id=sensor_id,
        start_timestamp=start_timestamp,
        end_timestamp=end_timestamp,
        second_start_timestamp=second_start_timestamp,
        second_end_timestamp=second_end_timestamp,
        time_format=time_format,
        first_answer=first_answer,
        second_answer=second_answer,
        answer=first_answer if second_answer is None else second_answer,
        fallback_reason=fallback_reason,
        model_calls=model_calls,
    )
