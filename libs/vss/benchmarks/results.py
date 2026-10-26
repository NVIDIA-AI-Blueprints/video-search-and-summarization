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

"""Strict versioned evidence for the first offline summarization profile."""

from __future__ import annotations

import statistics
from collections import Counter
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from vss.observability import Observation


class Evidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class Trial(Evidence):
    process_id: int = Field(gt=0)
    latency_s: float = Field(gt=0)
    cpu_time_s: float = Field(ge=0)
    rss_peak_bytes: int | None = Field(default=None, ge=0)
    rss_scope: Literal["process_lifetime", "unsupported"]
    chunks: int = Field(gt=0, le=1000)
    events: int = Field(gt=0, le=1000)
    collection_complete: bool
    observations: tuple[Observation, ...] = Field(max_length=256)

    @model_validator(mode="after")
    def correctness_and_evidence(self) -> Trial:
        required = {
            "summarization.total",
            "summarization.aggregate",
            "summarization.llm",
            "summarization.chunks",
            "summarization.events",
            "summarization.batches",
            "summarization.requests",
            "summarization.input_tokens",
            "summarization.output_tokens",
        }
        names = Counter(o.name for o in self.observations)
        totals = [o for o in self.observations if o.name == "summarization.total"]
        counts = {
            o.name: o.value
            for o in self.observations
            if o.name in {"summarization.chunks", "summarization.events"}
        }
        if (
            self.events != self.chunks
            or not self.collection_complete
            or len(totals) != 1
            or totals[0].status != "ok"
            or totals[0].value != self.latency_s
            or counts.get("summarization.chunks") != self.chunks
            or counts.get("summarization.events") != self.events
            or any(names[name] != 1 for name in required)
            or len({o.run_id for o in self.observations}) != 1
            or len({o.request_id for o in self.observations}) != 1
            or len({(o.schema_version, o.catalog_version) for o in self.observations})
            != 1
            or (self.rss_scope == "unsupported") != (self.rss_peak_bytes is None)
            or any(o.status != "ok" for o in self.observations)
        ):
            raise ValueError("trial correctness or measurement evidence is incomplete")
        return self


class Environment(Evidence):
    source_identity_version: Literal["legacy_monolith.v1", "namespace_workspace.v2"] = (
        "legacy_monolith.v1"
    )
    python: str
    system: str
    architecture: str
    os_release: str
    cpu_model_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    cpu_count: int | None
    dependencies: dict[str, str]


class Configuration(Evidence):
    chunks: int = Field(default=20, ge=1, le=1000)
    warmups: int = Field(default=2, ge=0, le=100)
    repetitions: int = Field(default=10, ge=1, le=1000)
    timeout_s: float = Field(default=30, gt=0, le=3600)


class Comparison(Evidence):
    status: Literal["descriptive", "incompatible", "invalid"]
    relative_latency_change: float | None = None
    reason: Literal[
        "unapproved_development_profile",
        "identity_mismatch",
        "incomplete_evidence",
        "hardware_identity_unavailable",
    ]


class RunResult(Evidence):
    schema_version: Literal["1.0"] = "1.0"
    profile: Literal["summarization.offline.v1"] = "summarization.offline.v1"
    status: Literal["ok", "incomplete", "unsupported"]
    acceptance_eligible: Literal[False] = False
    environment: Environment
    configuration: Configuration
    corpus_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    software_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    lockfile_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    import_origin: Literal["checkout", "installed"]
    trials: tuple[Trial, ...]
    comparison: Comparison | None = None
    failure: (
        Literal["child_failure", "timeout", "invalid_child_evidence", "cancelled"]
        | None
    ) = None
    unavailable: tuple[str, ...] = (
        "gpu_resources",
        "resource_time_series",
        "live_model_throughput",
        "approved_thresholds",
        "shared_decode_requires_M7",
    )

    @model_validator(mode="after")
    def validate_trials(self) -> RunResult:
        if self.status == "ok" and (
            len(self.trials) != self.configuration.repetitions
            or self.failure is not None
        ):
            raise ValueError("successful run requires every configured trial")
        if any(trial.chunks != self.configuration.chunks for trial in self.trials):
            raise ValueError("trial workload does not match configuration")
        return self

    @property
    def median_latency_s(self) -> float | None:
        return (
            statistics.median(t.latency_s for t in self.trials) if self.trials else None
        )


def compare(candidate: RunResult, baseline: RunResult) -> Comparison:
    """Descriptive comparison only; no implicit baseline promotion or approval."""
    if candidate.status != "ok" or baseline.status != "ok":
        return Comparison(status="invalid", reason="incomplete_evidence")
    if (
        candidate.environment.cpu_model_sha256 is None
        or baseline.environment.cpu_model_sha256 is None
    ):
        return Comparison(status="incompatible", reason="hardware_identity_unavailable")
    if (
        candidate.profile != baseline.profile
        or candidate.environment != baseline.environment
        or candidate.configuration != baseline.configuration
        or candidate.corpus_sha256 != baseline.corpus_sha256
        or candidate.lockfile_sha256 != baseline.lockfile_sha256
        or candidate.import_origin != baseline.import_origin
        or {
            (o.schema_version, o.catalog_version)
            for t in candidate.trials
            for o in t.observations
        }
        != {
            (o.schema_version, o.catalog_version)
            for t in baseline.trials
            for o in t.observations
        }
    ):
        return Comparison(status="incompatible", reason="identity_mismatch")
    current, previous = candidate.median_latency_s, baseline.median_latency_s
    assert current is not None and previous is not None
    return Comparison(
        status="descriptive",
        relative_latency_change=(current - previous) / previous,
        reason="unapproved_development_profile",
    )
