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

"""Internal frozen ingestion planning types."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .contracts import RequestSource


class ChunkWindow(BaseModel):
    """Internal deterministic chunk window."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1.0"] = "1.0"
    chunk_idx: int = Field(ge=0)
    start_s: float = Field(ge=0)
    end_s: float = Field(gt=0)
    sample_timestamps_s: tuple[float, ...] = ()

    @model_validator(mode="after")
    def validate_window(self) -> ChunkWindow:
        if self.end_s <= self.start_s:
            raise ValueError("end_s must be greater than start_s")
        if any(
            timestamp < self.start_s or timestamp > self.end_s
            for timestamp in self.sample_timestamps_s
        ):
            raise ValueError("sample timestamps must be inside the window")
        return self


class IngestionPlan(BaseModel):
    """Internal immutable plan frozen before backend inference."""

    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        extra="forbid",
        frozen=True,
    )

    schema_version: Literal["1.0"] = "1.0"
    source: RequestSource
    prompt: str = Field(min_length=1)
    model: str = Field(min_length=1)
    chunk_duration: int = Field(default=0, ge=0, le=3600)
    chunk_overlap_duration: int = Field(default=0, ge=-3600, le=3600)
    windows: tuple[ChunkWindow, ...] = ()
