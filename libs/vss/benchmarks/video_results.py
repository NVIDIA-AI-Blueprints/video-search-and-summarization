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

"""Strict evidence for real-video paired decode and controlled offline E2E."""

from __future__ import annotations

import bisect
import random
from typing import Literal

from pydantic import Field, model_validator

from .artifacts import MetricAggregate, TraceReference
from .media import MediaManifest
from .resources import ResourceTrace
from .results import Environment, Evidence


class VideoConfiguration(Evidence):
    repetitions: int = Field(default=10, ge=1, le=100)
    warmups: int = Field(default=2, ge=0, le=10)
    seed: int = Field(default=1729, ge=0, le=2**32 - 1)
    timeout_s: float = Field(default=180, gt=0, le=3600)
    batch_size: Literal[4] = 4
    sample_interval_s: Literal[0.05] = 0.05
    offline_e2e: bool = True
    mock_delay_ms: int = Field(default=0, ge=0, le=1000)
    chunk_duration_s: Literal[30] = 30
    method: Literal["warm_cache_paired_decode.v1"] = "warm_cache_paired_decode.v1"
    recipe: Literal["local", "tiny", "720p", "1080p", "timeline30m"]


class DecodePeriod(Evidence):
    mode: Literal["disabled", "enabled"]
    status: Literal["ok", "error"]
    failure: Literal["decode", "correctness", "collection", "artifact"] | None = None
    wall_s: float = Field(gt=0)
    cpu_s: float = Field(ge=0)
    frames: int = Field(ge=0, le=12000)
    pts_checks: int = Field(ge=0, le=12000)
    pixel_checks: int = Field(ge=0, le=8)
    maximum_rgb_mae: float = Field(ge=0)
    correctness: bool
    collection_complete: bool | None
    metrics: dict[str, MetricAggregate]
    trace: TraceReference | None = None


class WorkflowEvidence(Evidence):
    status: Literal["ok", "error"]
    wall_s: float = Field(gt=0)
    chunks: int = Field(ge=0, le=61)
    events: int = Field(ge=0, le=61)
    stored_events: int = Field(ge=0, le=61)
    store_summary_wall_s: float = Field(ge=0)
    selected_frames: int = Field(ge=0, le=488)
    jpeg_checks: int = Field(ge=0, le=488)
    maximum_jpeg_mae: float = Field(ge=0)
    http_requests: int = Field(ge=0, le=61)
    request_bytes_max: int = Field(ge=0, le=128 * 1024**2)
    client_closed: bool
    collection_complete: bool
    metrics: dict[str, MetricAggregate]
    trace: TraceReference
    provider_mode: Literal["controlled_offline_http"] = "controlled_offline_http"
    wall_scope: Literal["offline_workflow_including_http_image_checks"] = (
        "offline_workflow_including_http_image_checks"
    )

    @model_validator(mode="after")
    def required_workflow(self) -> WorkflowEvidence:
        if self.status != "ok":
            return self
        if (
            self.jpeg_checks != self.selected_frames
            or self.maximum_jpeg_mae >= 10
            or self.request_bytes_max > 32 * 1024**2
        ):
            raise ValueError("encoded HTTP image evidence is incomplete")
        exact = {
            "processing.probe": 1,
            "ingestion.vlm.capacity_wait": 1,
            "ingestion.vlm.total": 1,
            "processing.sample": 1,
            "processing.sample.active": 1,
            "processing.decode": 1,
            "processing.decode.lifetime": 1,
            "processing.sampled_frames": 1,
            "ingestion.vlm.prepare": self.chunks,
            "ingestion.vlm.http": self.chunks,
            "ingestion.vlm.infer": self.chunks,
            "ingestion.vlm.input_tokens": self.chunks,
            "ingestion.vlm.output_tokens": self.chunks,
            "processing.encode": self.selected_frames,
            "processing.encoded_bytes": self.selected_frames,
            "summarization.aggregate": 2,
            "summarization.llm": 2,
            "summarization.total": 2,
            "summarization.requests": 2,
            "summarization.chunks": 2,
            "summarization.events": 2,
            "summarization.batches": 2,
            "summarization.input_tokens": 2,
            "summarization.output_tokens": 2,
            "summarization.store": 1,
        }
        if any(
            name not in self.metrics or self.metrics[name].count != count
            for name, count in exact.items()
        ) or any(m.unsuccessful for m in self.metrics.values()):
            raise ValueError("required workflow stages are missing or unsuccessful")
        counters = {
            "processing.sampled_frames": self.selected_frames,
            "processing.selected_frames": self.selected_frames,
            "summarization.chunks": 2 * self.chunks,
            "summarization.events": 2 * self.events,
            "summarization.requests": 2,
            "summarization.batches": 2,
            "ingestion.vlm.input_tokens": 0,
            "ingestion.vlm.output_tokens": 0,
        }
        if any(
            name not in self.metrics
            or self.metrics[name].sum != value
            or self.metrics[name].unavailable
            for name, value in counters.items()
        ):
            raise ValueError("workflow counters contradict outputs")
        if not self.metrics["processing.encoded_bytes"].sum:
            raise ValueError("encoded byte evidence missing")
        for name in ("summarization.input_tokens", "summarization.output_tokens"):
            metric = self.metrics[name]
            if metric.sum is not None or metric.unavailable != 2:
                raise ValueError("offline summary fixture usage must remain unknown")
        if self.stored_events != self.events or self.store_summary_wall_s <= 0:
            raise ValueError("in-memory store summary evidence incomplete")
        return self


class WarmupDiagnostic(Evidence):
    mode: Literal["disabled", "enabled"]
    failure: Literal["decode", "correctness", "collection", "artifact"]
    frames: int = Field(ge=0, le=12000)
    pts_checks: int = Field(ge=0, le=12000)
    pixel_checks: int = Field(ge=0, le=8)
    maximum_rgb_mae: float = Field(ge=0)
    correctness: bool
    collection_complete: bool | None


class VideoTrial(Evidence):
    process_id: int = Field(gt=0)
    order: Literal["disabled_first", "enabled_first"]
    schedule_seed: int = Field(ge=0, le=2**32 - 1)
    warmups: int = Field(ge=0, le=10)
    status: Literal["ok", "error", "unsupported"]
    failure: str | None = Field(default=None, max_length=64, pattern=r"^[a-z0-9_]+$")
    environment: Environment
    native_builds: dict[str, str]
    software_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    lockfile_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    import_origin: Literal["checkout", "installed"]
    observation_schema: Literal["1.1"] = "1.1"
    catalog_version: Literal["1.1", "1.2"] = "1.2"
    media: MediaManifest | None = None
    periods: tuple[DecodePeriod, ...] = Field(max_length=2)
    workflow: WorkflowEvidence | None = None
    resources: ResourceTrace | None = None
    warmup_diagnostic: WarmupDiagnostic | None = None
    resources_scope: Literal["paired_decode_including_trace_retention"] = (
        "paired_decode_including_trace_retention"
    )

    @model_validator(mode="after")
    def coherent(self) -> VideoTrial:
        if self.warmup_diagnostic is not None and (
            self.status != "error" or self.failure != "warmup"
        ):
            raise ValueError("warmup diagnostic belongs only to failed warmup")
        if self.status != "ok":
            if self.failure is None:
                raise ValueError("failed trial needs typed failure")
            return self
        if self.failure is not None or self.media is None or len(self.periods) != 2:
            raise ValueError("successful pair needs both arms and media")
        expected_order = (
            ["disabled", "enabled"]
            if self.order == "disabled_first"
            else ["enabled", "disabled"]
        )
        if [p.mode for p in self.periods] != expected_order:
            raise ValueError("pair order mismatch")
        for period in self.periods:
            if (
                period.status != "ok"
                or period.failure is not None
                or not period.correctness
                or period.frames != len(self.media.pts_ns)
                or period.pts_checks != period.frames
                or period.pixel_checks != len(self.media.oracle_indices)
                or period.maximum_rgb_mae > 3
            ):
                raise ValueError("decode correctness evidence is missing")
            if period.mode == "disabled":
                if (
                    period.metrics
                    or period.trace
                    or period.collection_complete is not None
                ):
                    raise ValueError("disabled mode emitted collection evidence")
            else:
                if not period.collection_complete or period.trace is None:
                    raise ValueError("enabled mode incomplete")
                validate_decode_metrics(period.metrics, self.media)
        if self.resources is None or not self.resources.complete:
            raise ValueError("paired resource trace incomplete")
        if self.workflow is not None:
            flow = self.workflow
            expected = selected_chunks(self.media)
            if (
                flow.status != "ok"
                or not flow.client_closed
                or not flow.collection_complete
                or flow.chunks < 1
                or flow.chunks != flow.events
                or flow.http_requests != flow.chunks
                or flow.selected_frames < flow.chunks
                or flow.chunks != len(expected)
                or flow.selected_frames
                != sum(len(indices) for indices in expected.values())
            ):
                raise ValueError("offline workflow incomplete")
        return self


def validate_decode_metrics(
    metrics: dict[str, MetricAggregate], media: MediaManifest
) -> None:
    if not metrics or any(m.unsuccessful or m.unavailable for m in metrics.values()):
        raise ValueError("decode metrics unsuccessful or unavailable")
    n = len(media.pts_ns)
    exact = {
        "processing.selected_frames": n,
        "processing.native.produced_frames": n,
        "processing.native.delivered_frames": n,
        "processing.native.pending_frames": 0,
        "processing.copied_bytes": n * media.width * media.height * 3,
        "processing.sessions": 1,
        "processing.native.sessions": 1,
        "processing.select": 1,
    }
    if any(
        name not in metrics or metrics[name].sum != value
        for name, value in exact.items()
    ):
        raise ValueError("decode counters contradict admitted video")
    for name in (
        "processing.open",
        "processing.close",
        "processing.decode",
        "processing.decode.lifetime",
        "processing.consumer_wait",
    ):
        if name not in metrics or metrics[name].count != 1:
            raise ValueError("decode stage missing or repeated")
    if any(
        metrics[name].count != 1
        for name in (
            "processing.sessions",
            "processing.native.sessions",
            "processing.select",
            "processing.selected_frames",
        )
    ):
        raise ValueError("session/selection counters must describe one decode")
    reads = metrics.get("processing.native.read")
    if reads is None or reads.count != (n + 3) // 4 + 1:
        raise ValueError("native reads missing")
    for name in (
        "processing.native.decode",
        "processing.native.copy",
        "processing.native.produced_frames",
        "processing.native.delivered_frames",
        "processing.native.pending_frames",
    ):
        if name not in metrics or metrics[name].count != reads.count:
            raise ValueError("native read aggregates missing")


class VideoResult(Evidence):
    schema_version: Literal["1.0"] = "1.0"
    profile: Literal["video.cpu.paired.v1"] = "video.cpu.paired.v1"
    status: Literal["ok", "incomplete", "unsupported"]
    acceptance_eligible: Literal[False] = False
    configuration: VideoConfiguration
    trials: tuple[VideoTrial, ...] = Field(max_length=100)
    failure: str | None = Field(default=None, max_length=64, pattern=r"^[a-z0-9_]+$")

    @model_validator(mode="after")
    def coherent(self) -> VideoResult:
        if self.status == "ok":
            if (
                self.failure is not None
                or len(self.trials) != self.configuration.repetitions
                or any(t.status != "ok" for t in self.trials)
            ):
                raise ValueError("successful run needs every complete pair")
            if [t.order for t in self.trials] != balanced_orders(
                self.configuration.repetitions, self.configuration.seed
            ):
                raise ValueError("trial order differs from prespecified seed")
            if any(
                t.schedule_seed != self.configuration.seed
                or t.warmups != self.configuration.warmups
                for t in self.trials
            ):
                raise ValueError(
                    "worker schedule/warmup settings differ from configuration"
                )
            first = identity(self.trials[0])
            if any(identity(t) != first for t in self.trials):
                raise ValueError("trial identity mismatch")
            if any(
                t.software_sha256 != self.trials[0].software_sha256 for t in self.trials
            ):
                raise ValueError("software changed between independent workers")
            if any(
                (t.workflow is not None) != self.configuration.offline_e2e
                for t in self.trials
            ):
                raise ValueError("offline workflow configuration mismatch")
            if any(
                t.resources is None
                or t.resources.interval_s != self.configuration.sample_interval_s
                for t in self.trials
            ):
                raise ValueError("resource sampling configuration mismatch")
        elif self.failure is None:
            raise ValueError("incomplete run needs failure")
        if len({t.process_id for t in self.trials}) != len(self.trials):
            raise ValueError("trials must use independent workers")
        return self


def identity(trial: VideoTrial) -> tuple[object, ...]:
    return (
        trial.media,
        trial.environment,
        trial.native_builds,
        trial.lockfile_sha256,
        trial.import_origin,
        trial.observation_schema,
        trial.catalog_version,
    )


def selected_chunks(media: MediaManifest) -> dict[int, tuple[int, ...]]:
    """Independent uniform targets resolved through the FFprobe PTS sequence."""
    groups: dict[int, list[int]] = {}
    duration_ns = round(media.duration_s * 1e9)
    for start in range(0, duration_ns, 30_000_000_000):
        width = min(duration_ns - start, 30_000_000_000)
        for j in range(8):
            target = start + round(j * width / 8)
            index = bisect.bisect_left(media.pts_ns, target)
            if index == len(media.pts_ns):
                continue
            chunk = media.pts_ns[index] // 30_000_000_000
            group = groups.setdefault(chunk, [])
            if index not in group:
                group.append(index)
    return {key: tuple(indices) for key, indices in groups.items()}


def balanced_orders(n: int, seed: int) -> list[str]:
    orders = ["disabled_first", "enabled_first"] * (n // 2)
    rng = random.Random(seed)
    if n % 2:
        orders.append(rng.choice(["disabled_first", "enabled_first"]))
    rng.shuffle(orders)
    return orders
