# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Immutable benchmark domain models."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol, TypeAlias


class GroupType(StrEnum):
    RELEVANCE = "relevance"
    LOGIC = "logic"


@dataclass(frozen=True, slots=True)
class VideoReference:
    video_id: str
    url: str


@dataclass(frozen=True, slots=True)
class Choice:
    label: str
    text: str


@dataclass(frozen=True, slots=True)
class MultipleChoiceTask:
    case_id: str
    question: str
    choices: tuple[Choice, ...]
    attributes: dict[str, str] = field(default_factory=dict)


Task: TypeAlias = MultipleChoiceTask


@dataclass(frozen=True, slots=True)
class ChoiceGroundTruth:
    label: str


GroundTruthValue: TypeAlias = ChoiceGroundTruth


@dataclass(frozen=True, slots=True)
class BenchmarkCase:
    task: Task
    ground_truth: GroundTruthValue


@dataclass(frozen=True, slots=True)
class BenchmarkGroup:
    group_id: str
    video: VideoReference
    group_type: GroupType
    group_structure: str
    cases: tuple[BenchmarkCase, ...]


@dataclass(frozen=True, slots=True)
class BenchmarkDataset:
    groups: tuple[BenchmarkGroup, ...]


@dataclass(frozen=True, slots=True)
class ChoicePrediction:
    case_id: str
    label: str


Prediction: TypeAlias = ChoicePrediction


@dataclass(frozen=True, slots=True)
class CaseGrade:
    case_id: str
    correct: bool


@dataclass(frozen=True, slots=True)
class GroupScore:
    group_id: str
    group_type: GroupType
    score: float
    grades: tuple[CaseGrade, ...]


@dataclass(frozen=True, slots=True)
class MetricResult:
    name: str
    value: float


@dataclass(frozen=True, slots=True)
class EvaluationResult:
    metrics: tuple[MetricResult, ...]
    group_scores: tuple[GroupScore, ...]


class BenchmarkEvaluator(Protocol):
    def evaluate(
        self,
        dataset: BenchmarkDataset,
        predictions: tuple[Prediction, ...],
    ) -> EvaluationResult: ...

