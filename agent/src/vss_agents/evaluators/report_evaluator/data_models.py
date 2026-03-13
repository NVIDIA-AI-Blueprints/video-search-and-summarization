# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

from typing import Any
from typing import Optional

from pydantic import BaseModel
from pydantic import Field


class EvaluationScore(BaseModel):
    section_score: float | None = Field(
        None, ge=0.0, le=1.0, description="Score between 0.0 and 1.0, or None if failed to score. "
    )
    method: str = Field(..., description="Evaluation method used (e.g., exact_match, llm_judge, skipped)")
    actual_value: Any | None = Field(None, description="Actual generated value")
    reference_value: Any | None = Field(None, description="Reference value")
    error: str | None = Field(default=None, description="Error message if evaluation failed")
    field_scores: dict[str, Optional["EvaluationScore"]] = Field(
        default_factory=dict, description="Field scores within this section. "
    )

    @classmethod
    def from_error(
        cls,
        error_message: str,
        method: str = "unknown",
        actual_value: Any = None,
        reference_value: Any = None,
        field_scores: dict[str, Optional["EvaluationScore"]] | None = None,
    ) -> "EvaluationScore":
        """Create an error score (section_score=None) with error message."""
        return cls(
            section_score=None,
            method=method,
            actual_value=actual_value,
            reference_value=reference_value,
            error=error_message,
            field_scores=field_scores or {},
        )


EvaluationScore.model_rebuild()
