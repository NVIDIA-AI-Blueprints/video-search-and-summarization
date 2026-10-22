# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

"""Dependency-injection protocols for bounded memory introspection."""

from __future__ import annotations

from typing import TYPE_CHECKING
from typing import Protocol
from typing import runtime_checkable

if TYPE_CHECKING:
    from vss_core.introspection.models import SufficiencyDecision
    from vss_core.introspection.models import VLMEvidence
    from vss_core.memory.models import UnifiedMemoryRecord


@runtime_checkable
class SufficiencyJudge(Protocol):
    async def judge(self, *, query: str, records: list[UnifiedMemoryRecord]) -> SufficiencyDecision: ...


@runtime_checkable
class AnswerSynthesizer(Protocol):
    async def synthesize(
        self,
        *,
        query: str,
        memory_evidence: list[UnifiedMemoryRecord],
        vlm_evidence: list[VLMEvidence],
        unresolved_gaps: list[str],
    ) -> str: ...


@runtime_checkable
class IntrospectionVLMRunner(Protocol):
    async def run(
        self,
        *,
        sensor: str,
        start_time: str,
        end_time: str,
        prompt: str,
        intent: str,
        fps: float | None = None,
        timeout_seconds: float | None = None,
    ) -> VLMEvidence: ...


__all__ = ["AnswerSynthesizer", "IntrospectionVLMRunner", "SufficiencyJudge"]
