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

"""Development CPU correctness evidence, separate from the offline profile."""

from __future__ import annotations

from collections import Counter
from typing import Literal

from pydantic import Field, model_validator

from vss.observability import Observation

from .resources import ResourceTrace
from .results import Environment, Evidence


class ProcessingConfiguration(Evidence):
    warmups: int = Field(default=2, ge=0, le=20)
    repetitions: int = Field(default=10, ge=1, le=100)
    timeout_s: float = Field(default=60, gt=0, le=3600)
    sample_interval_s: float = Field(default=0.01, ge=0.001, le=1)


class FixtureManifest(Evidence):
    recipe: Literal["cfr", "vfr", "nonzero_origin"]
    recipe_version: Literal["h264-160x96-5fps-10frames-v1"] = (
        "h264-160x96-5fps-10frames-v1"
    )
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    oracle_rgb_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    raw_pts_ns: tuple[int, ...] = Field(min_length=10, max_length=10)
    width: Literal[160] = 160
    height: Literal[96] = 96

    @model_validator(mode="after")
    def timing(self) -> FixtureManifest:
        if tuple(sorted(self.raw_pts_ns)) != self.raw_pts_ns:
            raise ValueError("fixture PTS must be ordered")
        deltas = {
            b - a for a, b in zip(self.raw_pts_ns, self.raw_pts_ns[1:], strict=False)
        }
        if 0 in deltas or (self.recipe == "vfr" and len(deltas) < 2):
            raise ValueError("fixture cadence does not match recipe")
        if self.recipe == "nonzero_origin" and self.raw_pts_ns[0] < 5_000_000_000:
            raise ValueError("nonzero-origin recipe lost its origin")
        return self


class ProcessingTrial(Evidence):
    process_id: int = Field(gt=0)
    status: Literal["ok", "error", "unsupported"]
    failure: Literal["prerequisite", "fixture", "workload", "evidence"] | None = None
    # Captured by this worker, not inferred from a launcher environment.
    environment: Environment
    native_builds: dict[str, str]
    software_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    lockfile_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    import_origin: Literal["checkout", "installed"]
    fixtures: tuple[FixtureManifest, ...] = Field(max_length=3)
    latency_s: float | None = Field(default=None, gt=0)
    latency_scope: Literal["workload_and_correctness_checks"] = (
        "workload_and_correctness_checks"
    )
    resources: ResourceTrace | None = None
    collection_complete: bool = False
    observations: tuple[Observation, ...] = Field(default=(), max_length=8192)
    decoded_frames: int = Field(default=0, ge=0)
    checks_passed: bool = False

    @model_validator(mode="after")
    def evidence(self) -> ProcessingTrial:
        if self.status != "ok":
            if self.failure is None:
                raise ValueError("unsuccessful worker needs a typed failure")
            return self
        names = Counter(o.name for o in self.observations)
        # Exactly one invocation per fixture of each public workflow stage.
        exact = {
            "processing.probe": 3,
            "processing.extract": 3,
            "processing.sample": 3,
            "processing.encode": 3,
            "processing.materialize": 3,
            "processing.decode.lifetime": 9,
        }
        required = {
            "processing.native.read",
            "processing.native.decode",
            "processing.native.copy",
            "processing.native.produced_frames",
            "processing.native.delivered_frames",
            "processing.native.pending_frames",
            "processing.native.sessions",
            "processing.sessions",
            "processing.copy",
            "processing.copied_bytes",
            "processing.select",
            "processing.close",
        }
        if (
            self.failure is not None
            or not self.checks_passed
            or self.latency_s is None
            or self.decoded_frames != 30
            or {f.recipe for f in self.fixtures} != {"cfr", "vfr", "nonzero_origin"}
            or len(self.fixtures) != 3
            or not self.collection_complete
            or self.resources is None
            or not self.resources.complete
            or any(names[name] != count for name, count in exact.items())
            or not required.issubset(names)
            or len({o.run_id for o in self.observations}) != 1
            or len({o.request_id for o in self.observations}) != 1
            or len({(o.schema_version, o.catalog_version) for o in self.observations})
            != 1
            or len({o.source_id for o in self.observations}) != 3
            or any(
                o.status != "ok" or o.availability != "measured"
                for o in self.observations
            )
        ):
            raise ValueError("processing correctness or evidence is incomplete")
        selections = [o for o in self.observations if o.name == "processing.select"]
        if any(
            o.device != "cpu" or o.selection_reason != "explicit" for o in selections
        ):
            raise ValueError("CPU profile must select the explicit CPU decoder")
        if not {
            "opencv",
            "opencv_build_sha256",
            "psutil",
            "ffmpeg_build_sha256",
            "ffprobe_build_sha256",
        }.issubset(self.native_builds):
            raise ValueError("native build identity is missing")
        sources = {o.source_id for o in self.observations}
        for source in sources:
            counts = Counter(o.name for o in self.observations if o.source_id == source)
            if any(counts[name] != total // 3 for name, total in exact.items()):
                raise ValueError("per-fixture stage evidence is missing")
            source_events = [o for o in self.observations if o.source_id == source]
            if not required.issubset(counts):
                raise ValueError("per-fixture native evidence is missing")

            totals: Counter[str] = Counter()
            for event in source_events:
                totals[event.name] += event.value or 0

            if (
                # Two full ten-frame scans plus three extraction outputs.
                not 23 <= totals["processing.native.produced_frames"] <= 30
                or totals["processing.native.produced_frames"]
                != totals["processing.native.delivered_frames"]
                or totals["processing.native.pending_frames"] != 0
                or any(
                    o.value is None or o.value > 2
                    for o in source_events
                    if o.name
                    in {
                        "processing.native.produced_frames",
                        "processing.native.delivered_frames",
                    }
                )
                or totals["processing.copied_bytes"]
                != totals["processing.native.produced_frames"] * 160 * 96 * 3
                or totals["processing.encoded_bytes"] <= 0
                or any(
                    counts[name] != counts["processing.native.read"]
                    for name in (
                        "processing.native.decode",
                        "processing.native.copy",
                        "processing.native.produced_frames",
                        "processing.native.delivered_frames",
                        "processing.native.pending_frames",
                    )
                )
                or any(
                    totals[name] != 4
                    for name in (
                        "processing.sessions",
                        "processing.native.sessions",
                        "processing.select",
                    )
                )
                or any(
                    counts[name] != 4
                    for name in (
                        "processing.sessions",
                        "processing.native.sessions",
                        "processing.select",
                        "processing.close",
                    )
                )
            ):
                raise ValueError(
                    "native production/delivery/session evidence contradicts workload"
                )
        sums = {
            name: sum(o.value or 0 for o in self.observations if o.name == name)
            for name in ("processing.selected_frames", "processing.sampled_frames")
        }
        if sums != {"processing.selected_frames": 69, "processing.sampled_frames": 30}:
            raise ValueError("measured counts disagree with workload")
        return self


class ProcessingResult(Evidence):
    schema_version: Literal["1.0"] = "1.0"
    profile: Literal["processing.cpu.correctness.v1"] = "processing.cpu.correctness.v1"
    status: Literal["ok", "incomplete", "unsupported"]
    acceptance_eligible: Literal[False] = False
    configuration: ProcessingConfiguration
    trials: tuple[ProcessingTrial, ...]
    failure: (
        Literal[
            "timeout",
            "cancelled",
            "child_failure",
            "invalid_child_evidence",
            "worker_failure",
            "identity_mismatch",
            "platform",
            "cleanup_failure",
        ]
        | None
    ) = None
    unavailable: tuple[str, ...] = (
        "exact_process_tree_peaks",
        "process_tree_io",
        "gpu_resources",
        "live_model_throughput",
        "approved_thresholds",
        "shared_decode_requires_M7",
    )

    @model_validator(mode="after")
    def evidence(self) -> ProcessingResult:
        if self.status == "ok":
            if (
                self.failure is not None
                or len(self.trials) != self.configuration.repetitions
            ):
                raise ValueError("successful run requires all trials")
            if any(t.status != "ok" for t in self.trials):
                raise ValueError("successful run contains failed trial")
            first = self.trials[0]
            for trial in self.trials:
                if identity(trial) != identity(first):
                    raise ValueError(
                        "workers disagree about workload/environment identity"
                    )
        elif self.failure is None:
            raise ValueError("incomplete run needs a typed failure")
        if len({t.process_id for t in self.trials}) != len(self.trials):
            raise ValueError("each trial must use an independent worker")
        if any(
            t.resources is not None
            and t.resources.interval_s != self.configuration.sample_interval_s
            for t in self.trials
        ):
            raise ValueError("resource trace interval differs from configuration")
        return self


def identity(trial: ProcessingTrial) -> tuple[object, ...]:
    return (
        trial.environment,
        trial.native_builds,
        trial.software_sha256,
        trial.lockfile_sha256,
        trial.import_origin,
        trial.fixtures,
        tuple(
            sorted({(o.schema_version, o.catalog_version) for o in trial.observations})
        ),
    )
