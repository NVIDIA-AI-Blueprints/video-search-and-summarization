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

"""Versioned scalar measurements, independent of the capability libraries."""

from __future__ import annotations

from types import MappingProxyType
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Library = Literal["processing", "ingestion", "summarization"]
Availability = Literal[
    "measured", "not_reported", "not_applicable", "unsupported", "collection_error"
]
Status = Literal["ok", "error", "cancelled", "closed"]
Unit = Literal["seconds", "count", "bytes"]
_IDENTITY = r"^(unknown|not_applicable|opaque:[a-f0-9]{64})$"

# Names and units are fixed; arbitrary caller strings cannot create instruments.
METRIC_CATALOG = MappingProxyType(
    {
        "ingestion.queue.wait": "seconds",
        "ingestion.queue.execute": "seconds",
        "ingestion.queue.total": "seconds",
        "ingestion.queue.running": "count",
        "ingestion.queue.pending": "count",
        "ingestion.select": "count",
        "ingestion.infer": "seconds",
        "summarization.total": "seconds",
        "summarization.aggregate": "seconds",
        "summarization.llm": "seconds",
        "summarization.cleanup": "seconds",
        "summarization.store": "seconds",
        "summarization.chunks": "count",
        "summarization.events": "count",
        "summarization.batches": "count",
        "summarization.requests": "count",
        "summarization.input_tokens": "count",
        "summarization.output_tokens": "count",
        "processing.native.call": "seconds",
        "processing.probe": "seconds",
        "processing.open": "seconds",
        "processing.capacity_wait": "seconds",
        "processing.select": "count",
        "processing.sessions": "count",
        "processing.close": "seconds",
        "processing.decode": "seconds",
        "processing.decode.lifetime": "seconds",
        "processing.consumer_wait": "seconds",
        "processing.extract": "seconds",
        "processing.sample": "seconds",
        "processing.sample.active": "seconds",
        "processing.sample.consumer_wait": "seconds",
        "processing.sampled_frames": "count",
        "processing.encode": "seconds",
        "processing.materialize": "seconds",
        "processing.copy": "seconds",
        "processing.selected_frames": "count",
        "processing.copied_bytes": "bytes",
        "processing.encoded_bytes": "bytes",
        "processing.native.read": "seconds",
        "processing.native.lock_wait": "seconds",
        "processing.native.decode": "seconds",
        "processing.native.copy": "seconds",
        "processing.native.produced_frames": "count",
        "processing.native.delivered_frames": "count",
        "processing.native.pending_frames": "count",
        "processing.native.discarded_frames": "count",
        "processing.native.flushes": "count",
        "processing.native.resets": "count",
        "processing.native.sessions": "count",
        "ingestion.prepare": "seconds",
        "ingestion.upload": "seconds",
        "ingestion.model_resolution": "seconds",
        "ingestion.dispatch": "seconds",
        "ingestion.http": "seconds",
        "ingestion.first_result": "seconds",
        "ingestion.chunk": "count",
        "ingestion.chunks": "count",
        "ingestion.input_tokens": "count",
        "ingestion.output_tokens": "count",
        "ingestion.total_input_tokens": "count",
        "ingestion.total_output_tokens": "count",
        "ingestion.provider.queue": "seconds",
        "ingestion.provider.decode": "seconds",
        "ingestion.provider.infer": "seconds",
        "ingestion.provider.chunk": "seconds",
        "ingestion.provider.frames": "count",
        "ingestion.inference_retries": "count",
        "ingestion.cleanup_retries": "count",
        "ingestion.cleanup": "seconds",
        "ingestion.assemble": "seconds",
        "ingestion.backend": "seconds",
        "ingestion.release": "seconds",
        "ingestion.vlm.total": "seconds",
        "ingestion.vlm.capacity_wait": "seconds",
        "ingestion.vlm.prepare": "seconds",
        "ingestion.vlm.http": "seconds",
        "ingestion.vlm.infer": "seconds",
        "ingestion.vlm.input_tokens": "count",
        "ingestion.vlm.output_tokens": "count",
        "processing.vlm.total": "seconds",
        "processing.vlm.capacity_wait": "seconds",
        "processing.vlm.prepare": "seconds",
        "processing.vlm.http": "seconds",
        "processing.vlm.infer": "seconds",
        "processing.vlm.input_tokens": "count",
        "processing.vlm.output_tokens": "count",
    }
)


class Observation(BaseModel):
    """An immutable observation containing no media or arbitrary attributes."""

    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    schema_version: Literal["1.0", "1.1"] = "1.1"
    catalog_version: Literal["1.0", "1.1", "1.2"] = "1.2"
    run_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    request_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    span_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    parent_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")
    source_id: str = Field(pattern=_IDENTITY)
    pipeline: str = Field(pattern=_IDENTITY)
    pipeline_version: Literal["0.1.0"] = "0.1.0"
    configured_model: str = Field(pattern=_IDENTITY)
    model: str = Field(pattern=_IDENTITY)
    backend: str = Field(pattern=_IDENTITY)
    chunk_idx: int | None = Field(default=None, ge=0, le=2**53 - 1)
    device: Literal["cpu", "gpu", "unknown"] = "unknown"
    selection_reason: Literal[
        "unknown", "explicit", "environment", "alias", "auto", "availability_fallback"
    ] = "unknown"
    library: Library
    name: str = Field(max_length=80)
    unit: Unit
    value: float | None = Field(default=None, ge=0, le=2**53 - 1, strict=True)
    availability: Availability = "measured"
    origin: Literal["client", "provider"] = "client"
    status: Status = "ok"
    timestamp_ns: int = Field(ge=0)
    reason: Literal["none", "workload_error", "cancelled", "closed"] = "none"

    @model_validator(mode="after")
    def check_metric(self) -> Observation:
        if (
            METRIC_CATALOG.get(self.name) != self.unit
            or not self.name.startswith(self.library + ".")
            or (self.availability == "measured") != (self.value is not None)
            or (
                self.unit in {"count", "bytes"}
                and self.value is not None
                and not self.value.is_integer()
            )
        ):
            raise ValueError(
                "metric catalog, library, unit and availability must match"
            )
        return self
