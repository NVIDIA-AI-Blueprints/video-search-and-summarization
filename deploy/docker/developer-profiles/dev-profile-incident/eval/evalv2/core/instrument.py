# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Instruments every call carries (dimensions F and G): token usage and timing.

Usage is what the provider reported, kept raw beside the normalised counts; nothing is estimated here.
Timing separates the attempt's own processing time from any waiting (pacing, rate-limit cooldown), which is
never counted as model time, and flags an attempt that spanned a host suspend, as V1 does.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

HOST_SUSPEND_MIN_S = 30.0  # V1's threshold (eval_vlm_client.HOST_SUSPEND_MIN_S)


def _int_or_none(value: Any) -> int | None:
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


@dataclass(frozen=True)
class Usage:
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    reasoning_tokens: int | None = None
    total_tokens: int | None = None
    provider_reported: bool = False
    raw: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_openai(cls, usage: Mapping[str, Any] | None) -> Usage:
        """Normalise an OpenAI-compatible ``usage`` block; ``None`` or empty means nothing was reported."""
        if not usage:
            return cls()
        details = usage.get("completion_tokens_details") or {}
        prompt = _int_or_none(usage.get("prompt_tokens"))
        completion = _int_or_none(usage.get("completion_tokens"))
        total = _int_or_none(usage.get("total_tokens"))
        if total is None and prompt is not None and completion is not None:
            total = prompt + completion
        return cls(prompt, completion, _int_or_none(details.get("reasoning_tokens")), total, True, dict(usage))

    def to_dict(self) -> dict[str, Any]:
        return {"prompt_tokens": self.prompt_tokens, "completion_tokens": self.completion_tokens,
                "reasoning_tokens": self.reasoning_tokens, "total_tokens": self.total_tokens,
                "provider_reported": self.provider_reported, "raw": dict(self.raw)}


@dataclass(frozen=True)
class Timing:
    started_at: str
    wall_ms: float
    waited_ms: float = 0.0
    server_ms: float | None = None
    host_suspended: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {"started_at": self.started_at, "wall_ms": self.wall_ms, "waited_ms": self.waited_ms,
                "server_ms": self.server_ms, "host_suspended": self.host_suspended}


class Stopwatch:
    """Time one attempt: monotonic for the duration, wall clock to detect a host suspend."""

    def __init__(self, *, wall: Callable[[], float] = time.time, monotonic: Callable[[], float] = time.monotonic,
                 waited_ms: float = 0.0) -> None:
        self._wall, self._monotonic, self._waited_ms = wall, monotonic, waited_ms
        self.timing: Timing | None = None

    def __enter__(self) -> Stopwatch:
        self._started_at = datetime.now(UTC).isoformat()
        self._wall_start, self._mono_start = self._wall(), self._monotonic()
        return self

    def __exit__(self, *exc: object) -> None:
        elapsed = self._monotonic() - self._mono_start
        suspended = (self._wall() - self._wall_start) - elapsed >= HOST_SUSPEND_MIN_S
        self.timing = Timing(self._started_at, round(elapsed * 1000.0, 3), self._waited_ms, None, suspended)
