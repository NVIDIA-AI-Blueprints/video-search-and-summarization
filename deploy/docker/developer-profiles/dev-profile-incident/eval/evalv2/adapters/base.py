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

"""The adapter protocol: provider specifics only, never scoring.

An adapter sends one canonical request to one model (or one deployed pipeline) and returns what came back, with
usage, timing, every attempt, and references to the stored request and response artifacts. It charges every
HTTP attempt to the budget before sending it and applies the run's retry policy. It never judges the content:
validation and scoring belong to tasks.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from evalv2.core.artifacts import ArtifactRef, ArtifactStore
from evalv2.core.budget import BudgetLedger
from evalv2.core.instrument import Timing, Usage
from evalv2.core.retry import Failure, RetryPolicy

CAPABILITY_NAMES = ("video_input", "image_input", "text_input", "json_schema", "bbox")


class AdapterConfigError(RuntimeError):
    """The adapter is not configured (for example a missing endpoint or key); nothing was sent."""


@dataclass(frozen=True)
class Capabilities:
    video_input: bool = False
    image_input: bool = False
    text_input: bool = True
    json_schema: bool = False
    bbox: bool = False
    frame_policy: str = "unknown"
    verified: bool = False
    notes: str = ""

    def missing(self, requires: Iterable[str]) -> list[str]:
        """Required capabilities this subject lacks; an empty list means the task is supported."""
        unknown = sorted(set(requires) - set(CAPABILITY_NAMES))
        if unknown:
            raise ValueError(f"unknown capabilities {unknown}")
        return sorted(name for name in requires if not getattr(self, name))


@dataclass
class CallContext:
    store: ArtifactStore
    ledger: BudgetLedger
    policy: RetryPolicy
    subject: str
    task: str
    budget_ledger: str = "inference"
    tokens_bound: int = 20000  # pessimistic tokens per attempt, charged until usage is measured
    sleep: Callable[[float], None] = time.sleep


@dataclass(frozen=True)
class CallResult:
    request_ref: ArtifactRef
    response_ref: ArtifactRef | None
    text: str | None
    finish_reason: str | None
    usage: Usage
    timing: Timing | None
    failure: Failure | None
    attempts: tuple[Mapping[str, Any], ...] = field(default_factory=tuple)

    @property
    def ok(self) -> bool:
        return self.failure is None

    def to_dict(self) -> dict[str, Any]:
        return {"request_ref": str(self.request_ref),
                "response_ref": str(self.response_ref) if self.response_ref else None,
                "finish_reason": self.finish_reason, "usage": self.usage.to_dict(),
                "timing": self.timing.to_dict() if self.timing else None,
                "failure": self.failure.to_dict() if self.failure else None, "attempts": list(self.attempts)}


@runtime_checkable
class ModelAdapter(Protocol):
    id: str
    model: str
    capabilities: Capabilities

    def complete(self, request: Mapping[str, Any], ctx: CallContext) -> CallResult: ...
