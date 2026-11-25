# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Versioned JSON contracts; intentionally separate from VSS nv.Frame protobuf."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Question(StrictModel):
    type: Literal["noul", "choice", "score"] = "noul"
    instructions: str = Field(min_length=1, max_length=4000)
    criteria: list[str] | dict[str, str] | None = None

    @model_validator(mode="after")
    def validate_criteria(self):
        if not self.instructions.strip():
            raise ValueError("instructions must not be blank")
        if self.type == "noul":
            if self.criteria is not None:
                raise ValueError("noul does not accept criteria")
        else:
            expected = list if self.type == "score" else dict
            if not isinstance(self.criteria, expected) or not 2 <= len(self.criteria) <= 10:
                raise ValueError("score needs 2–10 ordered labels; choice needs 2–10 keyed labels")
            labels = list(self.criteria.values()) if isinstance(self.criteria, dict) else self.criteria
            if any(not x.strip() or len(x) > 1000 for x in labels) or len(set(labels)) != len(labels):
                raise ValueError("labels must be nonempty and unique")
        return self


class StreamConfig(StrictModel):
    rtsp_url: str = Field(pattern=r"^rtsps?://", max_length=2048)
    vios_sensor: str | None = Field(default=None, min_length=1, max_length=256)
    question: Question
    interval_seconds: float = Field(default=2, ge=0.2, le=60)
    input_mode: Literal["image", "video"] = "image"
    video_frames: int = Field(default=8, ge=2, le=16)
    video_fps: float = Field(default=1, ge=0.5, le=5)
    enabled: bool = True


class Rule(StrictModel):
    stream_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,64}$")
    # Pin to the prompt revision: changing a question must not silently reuse its rules.
    config_revision: int = Field(ge=1)
    field: Literal["noul", "score", "confidence", "choice", "probability"]
    probability_key: str | None = None
    operator: Literal["gte", "lte", "eq"] = "gte"
    value: float | str
    consecutive_samples: int = Field(default=3, ge=1, le=100)
    max_gap_seconds: float = Field(default=10, gt=0, le=600)
    cooldown_seconds: float = Field(default=60, ge=0, le=86400)
    pre_seconds: float = Field(default=10, ge=0, le=120)
    post_seconds: float = Field(default=5, ge=0, le=120)
    review_prompt: str = Field(default="Describe the event visible in this clip, its temporal context, and any uncertainty. Treat the D1 trigger as a hypothesis, not an established fact.", min_length=1, max_length=4000)
    enabled: bool = True

    @model_validator(mode="after")
    def validate_comparison(self):
        if self.field == "confidence":
            raise ValueError("Confidence alone does not identify a condition for Cosmos to verify; use noul, score, choice or probability")
        if self.field == "probability" and self.probability_key is None:
            raise ValueError("probability_key is required")
        if self.field == "choice":
            if self.operator != "eq" or not isinstance(self.value, str):
                raise ValueError("choice requires eq and a string key")
        elif not isinstance(self.value, (int, float)):
            raise ValueError("numeric fields require a numeric threshold")
        if self.field in ("noul", "confidence", "probability") and not 0 <= self.value <= 1:
            raise ValueError("probability thresholds must lie in [0, 1]")
        if self.field == "noul" and not (
            self.operator == "gte" and self.value >= 0.5
            or self.operator == "lte" and self.value <= 0.5
        ):
            raise ValueError("Yes/no verification requires gte with a threshold >= 0.5 or lte with a threshold <= 0.5")
        if self.field == "score" and self.operator == "eq" and not float(self.value).is_integer():
            raise ValueError("An exact score trigger must identify an integer answer-label index")
        if self.pre_seconds + self.post_seconds <= 0:
            raise ValueError("clip duration must be positive")
        return self


def matches(rule: Rule, answer: dict) -> bool:
    value = answer.get("probabilities", {}).get(rule.probability_key) if rule.field == "probability" else answer.get(rule.field)
    if value is None:
        return False
    if rule.operator == "eq":
        return value == rule.value
    if not isinstance(value, (float, int)):
        return False
    return value >= rule.value if rule.operator == "gte" else value <= rule.value
