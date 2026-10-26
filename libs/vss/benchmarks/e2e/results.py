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

"""Validate worker evidence before admitting a sample to any statistics."""

from typing import Literal

from pydantic import Field, model_validator

from ..artifacts import MetricAggregate
from ..resources import ResourceTrace
from ..results import Evidence


class WorkerResult(Evidence):
    status: Literal["ok", "failed"]
    failure: (
        Literal[
            "setup",
            "source_changed",
            "work_timeout",
            "quality",
            "cancelled",
            "provider_or_storage",
            "cleanup",
            "incomplete_observation",
            "unsupported_contract",
        ]
        | None
    ) = None
    stages_s: dict[str, float] = Field(default_factory=dict, max_length=8)
    result_ready_s: float | None = Field(default=None, gt=0)
    lifecycle_s: float | None = Field(default=None, gt=0)
    captions_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    summary_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    chunks: int | None = Field(default=None, ge=1, le=64)
    frames: int | None = Field(default=None, ge=1, le=6400000)
    events: int | None = Field(default=None, ge=1, le=10000)
    witness: dict | None = None
    providers: dict[str, list[dict]] = Field(default_factory=dict, max_length=2)
    provider_inputs: dict[str, list[dict]] = Field(default_factory=dict, max_length=2)
    resolved_endpoint_sha256: dict[str, str | None] = Field(
        default_factory=dict, max_length=3
    )
    resources: ResourceTrace | None = None
    metrics: dict[str, MetricAggregate] = Field(default_factory=dict, max_length=256)
    collection_complete: bool = False
    collection_dropped_count: int = Field(default=0, ge=0)
    caption_windows: list[dict] = Field(default_factory=list, max_length=64)

    @model_validator(mode="after")
    def coherent(self):
        import math

        if any(not math.isfinite(v) or v < 0 for v in self.stages_s.values()):
            raise ValueError("invalid stage timing")
        if self.status == "ok" and (
            self.failure is not None
            or not self.collection_complete
            or self.collection_dropped_count
            or self.resources is None
            or not self.resources.complete
            or self.chunks is None
            or self.frames is None
            or self.events is None
            or self.captions_sha256 is None
            or self.summary_sha256 is None
            or self.result_ready_s is None
            or self.lifecycle_s is None
            or self.lifecycle_s < self.result_ready_s
            or set(self.stages_s)
            != {
                "captions",
                "store_write",
                "store_read",
                "aggregate_and_summary",
                "cleanup",
            }
            or not self.metrics
            or not self.providers.get("llm")
        ):
            raise ValueError("incomplete successful worker evidence")
        if self.status == "failed" and self.failure is None:
            raise ValueError("failed worker requires reason")
        return self
