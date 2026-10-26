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

"""Strict evidence for normal VSS client comparisons; never full-stack approval."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from .media import MediaManifest
from .resources import ResourceTrace
from .results import Environment, Evidence
from .video_results import balanced_orders

type Arm = Literal["normal_vss", "library"]
type Fault = Literal[
    "none",
    "malformed",
    "missing_done",
    "duplicate",
    "wrong_model",
    "wrong_content",
    "delete_failure",
    "delete_conflict",
    "truncated",
]


class NormalConfiguration(Evidence):
    repetitions: int = Field(default=10, ge=1, le=100)
    warmups: int = Field(default=2, ge=0, le=10)
    seed: int = Field(default=1729, ge=0, le=2**32 - 1)
    timeout_s: float = Field(default=120, gt=0, le=600)
    chunk_duration: Literal[30] = 30
    frames_per_chunk: Literal[8] = 8
    max_tokens: Literal[64] = 64
    concurrency: Literal[1] = 1
    fixture_delay_ms: int = Field(default=0, ge=0, le=100)
    fault: Fault = "none"
    model: Literal["m8-controlled-fixture"] = "m8-controlled-fixture"
    prompt: Literal["Return one controlled event per chunk."] = (
        "Return one controlled event per chunk."
    )
    transport_policy: Literal["real_clients_no_proxy_no_auth_zero_retry.v1"] = (
        "real_clients_no_proxy_no_auth_zero_retry.v1"
    )
    logging_policy: Literal["upstream_null_logger_library_collection_disabled.v1"] = (
        "upstream_null_logger_library_collection_disabled.v1"
    )
    session_policy: Literal["warm_reused_sessions_closed_after_pair.v1"] = (
        "warm_reused_sessions_closed_after_pair.v1"
    )
    timing_scope: Literal["upload_caption_delete_job_settlement.v1"] = (
        "upload_caption_delete_job_settlement.v1"
    )


class SourceIdentity(Evidence):
    revision: str = Field(pattern=r"^[a-f0-9]{40}$")
    files: dict[str, str]

    @model_validator(mode="after")
    def bounded(self):
        expected = {
            "rtvi_vlm_client.py",
            "chunk_info.py",
            "vlm_pipeline/__init__.py",
            "vlm_pipeline/vlm_types.py",
            "via_logger.py",
        }
        if set(self.files) != expected or any(
            len(v) != 64 or any(c not in "0123456789abcdef" for c in v)
            for v in self.files.values()
        ):
            raise ValueError("upstream source inventory/hash mismatch")
        return self


class ChunkFingerprint(Evidence):
    index: int = Field(ge=0, le=63)
    start_ns: int = Field(ge=0)
    end_ns: int = Field(gt=0)
    frames: int = Field(gt=0, le=8)
    content_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    input_tokens: Literal[0] = 0
    output_tokens: Literal[0] = 0


class WireWitness(Evidence):
    upload_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    settings_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    bytes: int = Field(gt=0, le=65536)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    messages: int = Field(ge=0, le=64)
    done: bool
    sent_complete: bool
    valid: bool
    request_matched: bool
    upload_matched: bool
    deleted: bool
    residual_uploads: int = Field(ge=0, le=2)
    chunks: tuple[ChunkFingerprint, ...] = Field(max_length=64)


class NormalPeriod(Evidence):
    arm: Arm
    status: Literal["ok", "error"]
    failure: Literal["execution", "protocol", "correctness", "cleanup"] | None = None
    wall_s: float = Field(gt=0)
    cpu_s: float = Field(ge=0)
    stages: dict[Literal["upload", "captions", "delete"], float]
    chunks: tuple[ChunkFingerprint, ...] = Field(max_length=64)
    wire: WireWitness | None = None

    @model_validator(mode="after")
    def coherent(self):
        if any(v < 0 for v in self.stages.values()):
            raise ValueError("negative stage duration")
        if self.status == "ok":
            w = self.wire
            if (
                self.failure is not None
                or not self.chunks
                or set(self.stages) != {"upload", "captions", "delete"}
                or sum(self.stages.values()) > self.wall_s + 1e-6
                or w is None
                or not all(
                    [
                        w.valid,
                        w.done,
                        w.sent_complete,
                        w.request_matched,
                        w.upload_matched,
                        w.deleted,
                    ]
                )
                or w.residual_uploads
                or w.messages != len(self.chunks)
                or w.chunks != self.chunks
                or [c.index for c in self.chunks]
                != sorted({c.index for c in self.chunks})
            ):
                raise ValueError("successful client arm lacks complete evidence")
        elif self.failure is None:
            raise ValueError("failed arm needs typed failure")
        return self


class NormalTrial(Evidence):
    status: Literal["ok", "error", "unsupported"]
    failure: str | None = Field(default=None, max_length=64, pattern=r"^[a-z_]+$")
    process_id: int = Field(gt=0)
    order: Literal["disabled_first", "enabled_first"]
    configuration: NormalConfiguration
    environment: Environment
    upstream: SourceIdentity | None = None
    native_builds: dict[str, str] = Field(default_factory=dict)
    software_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    lockfile_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    media: MediaManifest | None = None
    expected: tuple[ChunkFingerprint, ...] = Field(default=(), max_length=64)
    periods: tuple[NormalPeriod, ...] = Field(default=(), max_length=2)
    resources: ResourceTrace | None = None
    clients_closed: bool = False
    server_closed: bool = False
    worker_scope: Literal["client_and_loopback_fixture_process"] = (
        "client_and_loopback_fixture_process"
    )

    @model_validator(mode="after")
    def coherent(self):
        if self.status != "ok":
            if self.failure is None:
                raise ValueError("unsuccessful trial needs failure")
            return self
        import json

        from .normal_fixture import (
            MAX_MEDIA,
            digest,
            expected_payloads,
            fingerprint,
            settings,
        )

        if (
            self.media is None
            or self.media.bytes > MAX_MEDIA
            or self.expected
            != tuple(fingerprint(p) for p in expected_payloads(self.media))
        ):
            raise ValueError("expected output does not match admitted media oracle")
        arms = (
            ["normal_vss", "library"]
            if self.order == "disabled_first"
            else ["library", "normal_vss"]
        )
        if (
            self.failure is not None
            or self.configuration.fault != "none"
            or self.upstream is None
            or set(self.native_builds)
            != {
                "opencv",
                "opencv_build_sha256",
                "psutil",
                "ffmpeg_build_sha256",
                "ffprobe_build_sha256",
            }
            or self.media is None
            or not self.expected
            or [p.arm for p in self.periods] != arms
            or any(p.status != "ok" or p.chunks != self.expected for p in self.periods)
            or any(
                p.wire is None
                or p.wire.upload_sha256 != self.media.sha256
                or p.wire.settings_sha256
                != digest(
                    json.dumps(
                        settings(self.configuration),
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode()
                )
                for p in self.periods
            )
            or self.resources is None
            or not self.resources.complete
            or self.resources.interval_s != 0.05
            or not self.clients_closed
            or not self.server_closed
        ):
            raise ValueError("successful pair lacks equivalent complete evidence")
        return self


class CoverageRow(Evidence):
    profile: Literal[
        "rtvi_client",
        "cpu_vlm",
        "gpu_vlm",
        "embedding",
        "stored_summary",
        "full_summary",
        "m7_shared",
    ]
    status: Literal["controlled_fixture", "unsupported"]
    reason: str = Field(pattern=r"^[a-z_]+$", max_length=80)


def coverage(*, completed: bool = False) -> tuple[CoverageRow, ...]:
    return tuple(
        CoverageRow(
            profile=p,
            status="controlled_fixture"
            if p == "rtvi_client" and completed
            else "unsupported",
            reason=r,
        )
        for p, r in [
            (
                "rtvi_client",
                "actual_upstream_client_loopback_only"
                if completed
                else "client_fixture_not_completed",
            ),
            ("cpu_vlm", "equivalent_normal_cpu_path_not_qualified"),
            ("gpu_vlm", "gpu_and_service_environment_unavailable"),
            ("embedding", "normal_embedding_adapter_not_qualified"),
            ("stored_summary", "persistent_store_and_equivalence_not_qualified"),
            ("full_summary", "lvs_ca_rag_equivalence_not_qualified"),
            ("m7_shared", "m_seven_runtime_unavailable"),
        ]
    )


class NormalResult(Evidence):
    schema_version: Literal["1.0"] = "1.0"
    profile: Literal["normal_vss.rtvi_client_fixture.v1"] = (
        "normal_vss.rtvi_client_fixture.v1"
    )
    status: Literal["ok", "incomplete", "unsupported"]
    configuration: NormalConfiguration
    failure: str | None = Field(default=None, max_length=64, pattern=r"^[a-z_]+$")
    trials: tuple[NormalTrial, ...] = Field(default=(), max_length=100)
    coverage: tuple[CoverageRow, ...] = Field(default_factory=coverage)
    acceptance_eligible: Literal[False] = False
    model_performance_measured: Literal[False] = False

    @model_validator(mode="after")
    def coherent(self):
        if self.coverage != coverage(completed=self.status == "ok"):
            raise ValueError("required coverage rows cannot be changed")
        if self.status == "ok":
            if (
                self.failure is not None
                or len(self.trials) != self.configuration.repetitions
                or any(
                    t.status != "ok" or t.configuration != self.configuration
                    for t in self.trials
                )
                or [t.order for t in self.trials]
                != balanced_orders(
                    self.configuration.repetitions, self.configuration.seed
                )
                or len({t.process_id for t in self.trials}) != len(self.trials)
                or any(
                    trial_identity(t) != trial_identity(self.trials[0])
                    for t in self.trials
                )
            ):
                raise ValueError("successful run needs all matched fixed pairs")
        elif self.failure is None:
            raise ValueError("incomplete result needs failure")
        return self


def trial_identity(t: NormalTrial) -> tuple[object, ...]:
    return (
        t.configuration,
        t.environment,
        t.upstream,
        t.native_builds,
        t.media,
        t.expected,
        t.software_sha256,
        t.lockfile_sha256,
    )
