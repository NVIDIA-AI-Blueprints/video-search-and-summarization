# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Versioned schema for canonical VSS performance results."""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, model_validator

DEFAULT_SCHEMA_VERSION = "0.1"
DEFAULT_GPU_MODEL_UNKNOWN = "Unknown"


class BenchmarkStatus(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    PARTIAL = "PARTIAL"


class TriggerSource(str, Enum):
    CI_PIPELINE = "ci_pipeline"
    MANUAL = "manual"
    SCHEDULED = "scheduled"
    ARTIFACT_PROMOTION = "artifact_promotion"
    SQA = "sqa"
    PERF_LAB = "perf-lab"


DEFAULT_TRIGGERED_BY = TriggerSource.MANUAL.value
DEFAULT_PLATFORM_PLACEHOLDER: dict[str, Any] = {
    "gpu": {"model": DEFAULT_GPU_MODEL_UNKNOWN, "count": 0}
}


class RunInfo(BaseModel):
    run_id: str = Field(..., description="Unique run id")
    timestamp: str = Field(..., description="ISO 8601 timestamp (UTC)")
    duration_seconds: float | None = Field(None, description="Scenario duration (s)")
    triggered_by: str = Field(DEFAULT_TRIGGERED_BY)
    pipeline_url: str = Field("")
    release: str = Field("")


class GPUInfo(BaseModel):
    model: str = Field(DEFAULT_GPU_MODEL_UNKNOWN)
    full_name: str = Field("")
    count: int = Field(0, ge=0)
    memory_gb: float | None = Field(None, ge=0)
    driver_version: str = Field("")
    cuda_version: str = Field("")


class CPUInfo(BaseModel):
    model: str = Field("")
    cores: int = Field(0, ge=0)


class PlatformInfo(BaseModel):
    gpu: GPUInfo = Field(
        default_factory=lambda: GPUInfo(model=DEFAULT_GPU_MODEL_UNKNOWN, count=0)
    )
    cpu: CPUInfo | None = None
    memory_gb: float | None = Field(None, ge=0)
    os: str | None = None


class BenchmarkMetadata(BaseModel):
    service: str
    run_info: RunInfo
    platform: PlatformInfo = Field(default_factory=PlatformInfo)
    config: dict[str, Any] = Field(default_factory=dict)
    benchmark_name: str = ""
    benchmark_mode: str = ""


class BenchmarkSummary(BaseModel):
    total_tests: int = Field(0, ge=0)
    passed: int = Field(0, ge=0)
    failed: int = Field(0, ge=0)
    pass_rate: float = Field(0.0, ge=0, le=100)
    overall_status: str = "PASS"

    @model_validator(mode="after")
    def validate_totals(self) -> "BenchmarkSummary":
        if self.total_tests and self.passed + self.failed != self.total_tests:
            raise ValueError(
                f"passed ({self.passed}) + failed ({self.failed}) "
                f"must equal total_tests ({self.total_tests})"
            )
        return self


class VSSBenchmarkResult(BaseModel):
    schema_version: str = DEFAULT_SCHEMA_VERSION
    metadata: BenchmarkMetadata
    summary: BenchmarkSummary
    test_cases: list[dict[str, Any]] = Field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "VSSBenchmarkResult":
        return cls.model_validate(data)

    @classmethod
    def load_from_file(cls, path: str | Path) -> "VSSBenchmarkResult":
        import json

        with Path(path).open(encoding="utf-8") as stream:
            return cls.model_validate(json.load(stream))
