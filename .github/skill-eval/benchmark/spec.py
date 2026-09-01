# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Validation and loading for expects-backed and dataset-backed eval specs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class AgentSpec(StrictModel):
    name: Literal["openclaw"]


SetupInputName = Literal["dataset_video_ids", "summarization_config"]


class SetupStep(StrictModel):
    name: Annotated[str, Field(min_length=1)]
    query: Annotated[str, Field(min_length=1)]
    checks: Annotated[tuple[str, ...], Field(min_length=1)]
    inputs: tuple[SetupInputName, ...] = ()
    agent_timeout_sec: Annotated[int, Field(ge=60, le=3600)] = 600

    @model_validator(mode="after")
    def require_unique_inputs(self) -> SetupStep:
        if len(set(self.inputs)) != len(self.inputs):
            raise ValueError("setup inputs must be unique")
        return self


class PlatformResource(StrictModel):
    gpu_count: Annotated[int, Field(ge=0)] = 1


class ResourcesSpec(StrictModel):
    platforms: Annotated[dict[str, PlatformResource], Field(min_length=1)]


class DatasetSpec(StrictModel):
    path: str
    format: Literal["video-mme-v2"]


class SummarizationSpec(StrictModel):
    scenario: Annotated[str, Field(min_length=1)]
    events: Annotated[tuple[str, ...], Field(min_length=1)]
    creation_time: Annotated[str, Field(min_length=1)]


class ScoringSpec(StrictModel):
    metric: Literal["overall_group_score"]
    minimum: Annotated[float, Field(ge=0.0, le=1.0)]


class BenchmarkSpec(StrictModel):
    agent: AgentSpec
    skills: tuple[str, ...]
    resources: ResourcesSpec
    setup: tuple[SetupStep, ...]
    dataset: DatasetSpec
    summarization: SummarizationSpec
    scoring: ScoringSpec

    @model_validator(mode="after")
    def require_setup_contract(self) -> BenchmarkSpec:
        if len(self.setup) != 3:
            raise ValueError("dataset benchmarks require exactly three setup steps")
        if len(set(self.skills)) != len(self.skills):
            raise ValueError("skills must be unique")
        required = {"benchmark-unified-memory", "vss-ask-video"}
        if not required <= set(self.skills):
            raise ValueError(f"benchmark spec is missing required skills: {sorted(required - set(self.skills))}")
        return self


def load_benchmark_spec(path: Path) -> BenchmarkSpec:
    return BenchmarkSpec.model_validate(json.loads(path.read_text(encoding="utf-8")))


def spec_kind(data: dict) -> Literal["expects", "dataset"]:
    has_expects = "expects" in data
    has_dataset = "dataset" in data
    if has_expects == has_dataset:
        raise ValueError("spec must contain exactly one of 'expects' or 'dataset'")
    return "expects" if has_expects else "dataset"
