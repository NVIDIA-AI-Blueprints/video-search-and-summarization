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

"""Strict receipts for the initial balanced native campaign."""

from typing import Literal

from pydantic import Field, model_validator

from benchmarks.artifacts import MetricAggregate, TraceReference
from benchmarks.gpu_resources import GPUIdentity, GPUTrace
from benchmarks.media import MediaManifest
from benchmarks.resources import ResourceTrace
from benchmarks.results import Environment, Evidence

Backend = Literal["opencv", "pynv"]
Task = Literal["full", "selected", "prepared"]
Policy = Literal["minimal", "collection", "resources", "combined"]
POLICIES: tuple[Policy, ...] = ("minimal", "collection", "resources", "combined")


class Settings(Evidence):
    backends: tuple[Backend, ...] = ("opencv", "pynv")
    tasks: tuple[Task, ...] = ("full", "selected", "prepared")
    blocks: int = Field(default=10, ge=2, le=20)
    warmups: int = Field(default=1, ge=0, le=3)
    seed: int = Field(default=1729, ge=0, le=2**32 - 1)
    trial_timeout_s: float = Field(default=180, ge=1, le=600)
    wall_limit_s: float = Field(default=3600, ge=1, le=3600)

    @model_validator(mode="after")
    def finite_balanced(self):
        if (
            not self.backends
            or len(set(self.backends)) != len(self.backends)
            or not self.tasks
            or len(set(self.tasks)) != len(self.tasks)
            or self.blocks % 2
        ):
            raise ValueError("nonempty unique selections and even block count required")
        return self


class Slot(Evidence):
    index: int = Field(ge=0, lt=480)
    block: int = Field(ge=0, lt=20)
    backend: Backend
    task: Task
    policy: Policy


class NativeTrial(Evidence):
    schema_version: Literal["native_trial.1"] = "native_trial.1"
    slot: Slot
    settings_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    process_id: int = Field(gt=0)
    media: MediaManifest
    software_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    lock_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    environment: Environment
    native_builds: dict[str, str]
    import_origins: dict[str, str]
    gpu: GPUIdentity | None = None
    operation_wall_s: float = Field(gt=0)
    policy_wall_s: float = Field(gt=0)
    policy_cpu_s: float = Field(ge=0)
    frames: int = Field(ge=2, le=12000)
    pixel_checks: int = Field(ge=2, le=8)
    maximum_rgb_mae: float = Field(ge=0, le=3)
    output_metadata_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    encoded_base64_bytes: int = Field(ge=0)
    jpeg_checks: int = Field(ge=0, le=8)
    maximum_jpeg_mae: float | None = Field(default=None, ge=0, lt=10)
    collection_complete: bool | None
    metrics: dict[str, MetricAggregate]
    trace: TraceReference | None = None
    resources: ResourceTrace | None = None
    gpu_trace: GPUTrace | None = None
    correctness: Literal[True] = True
    timing_scope: Literal["owned_host_rgb_with_bounded_consumer_bookkeeping"] = (
        "owned_host_rgb_with_bounded_consumer_bookkeeping"
    )

    @model_validator(mode="after")
    def coherent(self):
        collecting = self.slot.policy in {"collection", "combined"}
        sampling = self.slot.policy in {"resources", "combined"}
        expected = (
            len(self.media.pts_ns)
            if self.slot.task == "full"
            else len(self.media.oracle_indices)
        )
        if (
            self.policy_wall_s < self.operation_wall_s
            or self.frames != expected
            or self.pixel_checks != len(self.media.oracle_indices)
            or not self.native_builds
            or not self._valid_origins()
            or (self.slot.backend == "pynv" and self.gpu is None)
            or (self.slot.task == "prepared") != (self.encoded_base64_bytes > 0)
            or self.jpeg_checks != (self.frames if self.slot.task == "prepared" else 0)
            or (self.slot.task == "prepared") != (self.maximum_jpeg_mae is not None)
        ):
            raise ValueError("native trial work/identity contradiction")
        if collecting:
            if (
                self.collection_complete is not True
                or self.trace is None
                or not self.metrics
                or any(m.unsuccessful for m in self.metrics.values())
            ):
                raise ValueError("incomplete native collection")
            for name in ("processing.open", "processing.close", "processing.decode"):
                if name not in self.metrics or self.metrics[name].count != 1:
                    raise ValueError("missing decode lifecycle metrics")
            selected = self.metrics.get("processing.selected_frames")
            if selected is None or selected.sum != self.frames:
                raise ValueError("selected frame counter mismatch")
        elif self.collection_complete is not None or self.trace or self.metrics:
            raise ValueError("unrequested collection")
        if sampling:
            if self.resources is None or not self.resources.complete:
                raise ValueError("incomplete resource sampling")
            if self.gpu is not None and (
                self.gpu_trace is None or not self.gpu_trace.complete
            ):
                raise ValueError("incomplete GPU sampling")
        elif self.resources is not None or self.gpu_trace is not None:
            raise ValueError("unrequested sampling")
        return self

    def _valid_origins(self):
        if self.environment.source_identity_version == "legacy_monolith.v1":
            return set(self.import_origins) == {"vss", "benchmark"}
        required = {
            "vss.video_processing",
            "vss.storage",
            "vss.observability",
            "mode",
            "benchmark",
        }
        return required <= set(self.import_origins) <= required | {
            "vss.ingestion",
            "vss.summarization",
        } and self.import_origins["mode"] in {"checkout", "installed"}
