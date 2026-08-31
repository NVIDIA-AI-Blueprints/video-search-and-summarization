# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Immutable benchmark domain models and persisted evaluation contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from math import isclose
from typing import Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, model_validator

ChoiceLabel: TypeAlias = Literal["A", "B", "C", "D", "E", "F", "G", "H"]


class GroupType(StrEnum):
    RELEVANCE = "relevance"
    LOGIC = "logic"


class FrozenModel(BaseModel):
    """Strict immutable base for values that cross process/file boundaries."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class ChoiceAnswer(FrozenModel):
    """One selected multiple-choice answer, for gold and predicted values."""

    label: ChoiceLabel


class CaseAnswer(FrozenModel):
    """Associate a canonical answer with its benchmark case."""

    case_id: str = Field(min_length=1)
    answer: ChoiceAnswer


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
class BenchmarkCase:
    task: Task
    ground_truth: ChoiceAnswer


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


class GroupEvaluationSpec(FrozenModel):
    """Typed contract generated for one Harbor group verifier."""

    group_id: str = Field(min_length=1)
    group_type: GroupType
    group_structure: str
    expected_answers: tuple[CaseAnswer, ...]
    minimum: float = Field(ge=0.0, le=1.0)
    final_group: bool
    expected_group_ids: tuple[str, ...]

    @model_validator(mode="after")
    def validate_group_contract(self) -> GroupEvaluationSpec:
        case_ids = [item.case_id for item in self.expected_answers]
        if len(case_ids) != 4 or len(set(case_ids)) != 4:
            raise ValueError("a benchmark group must define four unique case IDs")
        if len(self.expected_group_ids) != len(set(self.expected_group_ids)):
            raise ValueError("expected_group_ids must be unique")
        if self.group_id not in self.expected_group_ids:
            raise ValueError("group_id must be included in expected_group_ids")
        return self


class CaseGrade(FrozenModel):
    case_id: str = Field(min_length=1)
    expected: ChoiceAnswer
    predicted: ChoiceAnswer
    correct: bool

    @model_validator(mode="after")
    def validate_correctness(self) -> CaseGrade:
        if self.correct != (self.predicted == self.expected):
            raise ValueError("correct does not match expected and predicted answers")
        return self


class GroupScore(FrozenModel):
    group_id: str = Field(min_length=1)
    group_type: GroupType
    score: float = Field(ge=0.0, le=1.0)
    question_accuracy: float = Field(ge=0.0, le=1.0)
    grades: tuple[CaseGrade, ...]

    @model_validator(mode="after")
    def validate_grades(self) -> GroupScore:
        case_ids = [grade.case_id for grade in self.grades]
        if len(case_ids) != 4 or len(set(case_ids)) != 4:
            raise ValueError("a group score must define four unique case grades")
        expected_accuracy = sum(grade.correct for grade in self.grades) / len(
            self.grades
        )
        if not isclose(self.question_accuracy, expected_accuracy):
            raise ValueError("question_accuracy does not match case grades")
        return self


class BenchmarkReport(FrozenModel):
    overall_group_score: float = Field(ge=0.0, le=1.0)
    minimum: float = Field(ge=0.0, le=1.0)
    passed: bool
    groups: tuple[GroupScore, ...]

    @model_validator(mode="after")
    def validate_report(self) -> BenchmarkReport:
        if not self.groups:
            raise ValueError("a benchmark report must contain at least one group")
        group_ids = [group.group_id for group in self.groups]
        if len(group_ids) != len(set(group_ids)):
            raise ValueError("benchmark report group IDs must be unique")
        expected_overall = sum(group.score for group in self.groups) / len(self.groups)
        if not isclose(self.overall_group_score, expected_overall):
            raise ValueError("overall_group_score does not match group scores")
        if self.passed != (self.overall_group_score >= self.minimum):
            raise ValueError("passed does not match overall score and minimum")
        return self
