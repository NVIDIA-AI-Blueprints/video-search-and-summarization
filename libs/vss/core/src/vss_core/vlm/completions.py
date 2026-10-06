# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Strict completion extraction with per-operation telemetry."""

from dataclasses import asdict
from dataclasses import dataclass
from typing import Any

from vss_core.openai import extract_chat_content


@dataclass(frozen=True)
class TokenUsage:
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None


@dataclass(frozen=True)
class ChatCompletion:
    text: str
    requested_model: str
    reported_model: str | None
    completion_id: str | None
    finish_reason: str | None
    truncated: bool
    usage: TokenUsage | None
    attempts: int
    latency_s: float
    reasoning_content: str | None = None

    def metadata(self) -> dict[str, Any]:
        values = asdict(self)
        values.pop("text")
        if self.reasoning_content is None:
            values.pop("reasoning_content")
        return values


def extract_completion(
    payload: Any, model: str, attempts: int, latency_s: float, *, allow_text_parts: bool = True
) -> ChatCompletion:
    text = extract_chat_content(payload)
    if not allow_text_parts and not isinstance(payload["choices"][0]["message"]["content"], str):
        raise ValueError("completion content must be a string")
    choice = payload["choices"][0]
    message = choice["message"]
    fields = (payload.get("model"), payload.get("id"), choice.get("finish_reason"), message.get("reasoning_content"))
    if any(v is not None and not isinstance(v, str) for v in fields):
        raise ValueError("invalid completion metadata")
    usage = payload.get("usage")
    if usage is not None:
        if not isinstance(usage, dict):
            raise ValueError("invalid usage")
        counters = {
            name: usage[name] for name in ("prompt_tokens", "completion_tokens", "total_tokens") if name in usage
        }
        if any(type(v) is not int or v < 0 for v in counters.values()):
            raise ValueError("invalid usage counters")
        usage = TokenUsage(**counters)
    return ChatCompletion(
        text, model, fields[0], fields[1], fields[2], fields[2] == "length", usage, attempts, latency_s, fields[3]
    )
