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

"""Separate strict GPU decode evidence: host RGB copies, no inference verdict."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import Field, model_validator

from .gpu_resources import GPUIdentity, GPUIdleCheck, GPUTrace
from .media import MediaManifest
from .resources import ResourceTrace
from .results import Environment, Evidence
from .video_results import (
    DecodePeriod,
    VideoConfiguration,
    WarmupDiagnostic,
    balanced_orders,
)


class GPUConfiguration(VideoConfiguration):
    offline_e2e: Literal[False] = False
    decoder: Literal["pynv"] = "pynv"
    output_memory: Literal["owned_host_rgb_copies"] = "owned_host_rgb_copies"


class ArtifactReference(Evidence):
    name: str = Field(
        pattern=r"^(?:diagnostics/(?:parent|[0-9]{1,12})/[a-f0-9]{32}\.json|traces/[a-f0-9]{32}\.jsonl\.(?:gz|partial))$"
    )
    bytes: int = Field(ge=0, le=32 * 1024**2)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


def validate_gpu_metrics(period: DecodePeriod, media: MediaManifest):
    metrics = period.metrics
    n = len(media.pts_ns)
    exact = {
        "processing.selected_frames": n,
        "processing.native.produced_frames": n,
        "processing.native.delivered_frames": n + 1,
        "processing.native.flushes": 1,
        "processing.copied_bytes": n * media.width * media.height * 3,
        "processing.sessions": 1,
        "processing.native.sessions": 1,
        "processing.select": 1,
        "processing.native.discarded_frames": 0,
    }
    if not metrics or any(m.unsuccessful or m.unavailable for m in metrics.values()):
        raise ValueError("GPU metrics incomplete")
    if any(
        name not in metrics or metrics[name].sum != value
        for name, value in exact.items()
    ):
        raise ValueError("GPU prefetch/flush conservation mismatch")
    if (
        "processing.native.discarded_frames" in metrics
        and metrics["processing.native.discarded_frames"].sum
    ):
        raise ValueError("full GPU decode discarded output")
    for name in [
        "processing.open",
        "processing.close",
        "processing.decode",
        "processing.decode.lifetime",
        "processing.consumer_wait",
    ]:
        if name not in metrics or metrics[name].count != 1:
            raise ValueError("GPU decode lifecycle stage mismatch")
    reads = metrics.get("processing.native.read")
    if reads is None:
        raise ValueError("GPU native reads missing")
    for name in (
        "decode",
        "copy",
        "produced_frames",
        "delivered_frames",
        "pending_frames",
        "flushes",
    ):
        if (
            metrics.get("processing.native." + name) is None
            or metrics["processing.native." + name].count != reads.count
        ):
            raise ValueError("GPU read-role counts inconsistent")


class GPUTrial(Evidence):
    status: Literal["ok", "error", "unsupported"]
    failure: str | None = Field(default=None, max_length=64, pattern=r"^[a-z_]+$")
    process_id: int = Field(gt=0)
    order: Literal["disabled_first", "enabled_first"]
    configuration: GPUConfiguration
    environment: Environment
    native_builds: dict[str, str]
    software_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    lockfile_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    gpu: GPUIdentity | None = None
    media: MediaManifest | None = None
    periods: tuple[DecodePeriod, ...] = Field(default=(), max_length=2)
    resources: ResourceTrace | None = None
    gpu_trace: GPUTrace | None = None
    warmup_diagnostic: WarmupDiagnostic | None = None

    @model_validator(mode="after")
    def coherent(self):
        if self.warmup_diagnostic is not None and (
            self.status != "error" or self.failure != "warmup"
        ):
            raise ValueError("GPU warmup diagnostics only on failed warmup")
        if self.status != "ok":
            if self.failure is None:
                raise ValueError("failed GPU trial needs failure")
            return self
        if (
            self.failure is not None
            or self.media is None
            or self.gpu is None
            or len(self.periods) != 2
        ):
            raise ValueError("GPU pair missing required evidence")
        builds = {
            "opencv",
            "opencv_build_sha256",
            "psutil",
            "ffmpeg_build_sha256",
            "ffprobe_build_sha256",
            "pynv_binary_sha256",
        }
        if set(self.native_builds) != builds or any(
            not self.native_builds[k] for k in builds
        ):
            raise ValueError("GPU native build inventory incomplete")
        if any(
            re.fullmatch(r"[a-f0-9]{64}", self.native_builds[k]) is None
            for k in builds
            if k.endswith("_sha256")
        ):
            raise ValueError("GPU native build digest invalid")
        packages = {
            "pynvvideocodec",
            "nvidia-ml-py",
            "opencv-python-headless",
            "psutil",
        }
        if any(
            not self.environment.dependencies.get(k)
            or self.environment.dependencies[k] == "unknown"
            for k in packages
        ):
            raise ValueError("GPU actual package identity incomplete")
        order = (
            ["disabled", "enabled"]
            if self.order == "disabled_first"
            else ["enabled", "disabled"]
        )
        if [p.mode for p in self.periods] != order:
            raise ValueError("GPU pair order mismatch")
        for p in self.periods:
            if (
                p.status != "ok"
                or p.failure is not None
                or not p.correctness
                or p.frames != len(self.media.pts_ns)
                or p.pts_checks != p.frames
                or p.pixel_checks != len(self.media.oracle_indices)
                or p.maximum_rgb_mae > 3
            ):
                raise ValueError("GPU correctness incomplete")
            if p.mode == "enabled":
                if not p.collection_complete or p.trace is None:
                    raise ValueError("GPU scalar evidence incomplete")
                validate_gpu_metrics(p, self.media)
            elif p.metrics or p.trace or p.collection_complete is not None:
                raise ValueError("disabled GPU mode has metrics")
        if (
            self.resources is None
            or not self.resources.complete
            or self.resources.interval_s != 0.05
            or self.gpu_trace is None
            or not self.gpu_trace.complete
        ):
            raise ValueError("GPU resource evidence incomplete")
        return self


def identity(trial: GPUTrial):
    return (
        trial.configuration,
        trial.environment,
        trial.native_builds,
        trial.software_sha256,
        trial.lockfile_sha256,
        trial.gpu,
        trial.media,
    )


class GPUResult(Evidence):
    schema_version: Literal["1.0"] = "1.0"
    profile: Literal["video.gpu.paired.v1"] = "video.gpu.paired.v1"
    configuration: GPUConfiguration
    status: Literal["ok", "incomplete", "unsupported"]
    failure: str | None = Field(default=None, max_length=64, pattern=r"^[a-z_]+$")
    trials: tuple[GPUTrial, ...] = Field(default=(), max_length=100)
    artifacts: tuple[ArtifactReference, ...] = Field(default=(), max_length=1000)
    idle_checks: tuple[GPUIdleCheck, ...] = Field(default=(), max_length=200)
    acceptance_eligible: Literal[False] = False
    model_performance_measured: Literal[False] = False

    @model_validator(mode="after")
    def coherent(self):
        names = [f.name for f in self.artifacts]
        if (
            len(names) != len(set(names))
            or sum(f.bytes for f in self.artifacts) > 256 * 1024**2
        ):
            raise ValueError("GPU inventory budget/duplicates")
        attached = {p.trace.name for t in self.trials for p in t.periods if p.trace}
        traces = {n for n in names if n.startswith("traces/")}
        if not attached <= traces or (self.status == "ok" and attached != traces):
            raise ValueError("GPU trace inventory mismatch")
        if self.status == "ok":
            if (
                self.failure is not None
                or len(self.trials) != self.configuration.repetitions
                or any(
                    t.status != "ok" or t.configuration != self.configuration
                    for t in self.trials
                )
                or len({t.process_id for t in self.trials}) != len(self.trials)
                or [t.order for t in self.trials]
                != balanced_orders(
                    self.configuration.repetitions, self.configuration.seed
                )
                or any(identity(t) != identity(self.trials[0]) for t in self.trials)
                or [(c.pair, c.phase) for c in self.idle_checks]
                != [
                    (i, phase)
                    for i in range(len(self.trials))
                    for phase in ("before", "after")
                ]
                or any(
                    not c.quiescent or c.gpu != self.trials[0].gpu
                    for c in self.idle_checks
                )
            ):
                raise ValueError("complete matched fixed GPU pairs required")
        elif self.failure is None:
            raise ValueError("incomplete GPU result needs failure")
        return self
