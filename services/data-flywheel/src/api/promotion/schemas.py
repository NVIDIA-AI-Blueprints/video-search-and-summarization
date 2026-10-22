# SPDX-FileCopyrightText: Copyright (c) 2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

"""API request/response schemas for /api/promotions.

Mirrors the promotion schemas in ``aoi-api/dfw_user_api/openapi.json``.
"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from src.api.promotion.models import PromotionGate, PromotionState

__all__ = [
    "ApproveRequest",
    "PromotionDecisionResponse",
    "PromotionList",
    "PromotionRecordResponse",
    "RejectRequest",
]


class PromotionRecordResponse(BaseModel):
    """A promotion record (wire ``PromotionRecord``)."""

    promotion_id: str
    model_name: str
    # candidate_version/baseline_version are the DFW/Mongo-minted serving integer
    # versions (the version handed to the Model Manager / Triton). deft_run_id is
    # lineage.
    candidate_version: str
    baseline_version: str | None = None
    deft_run_id: str | None = None
    use_case: str | None = None
    state: PromotionState
    pending_gate: PromotionGate | None = None
    # Rollout position — without these an operator cannot see where a live
    # promotion stands (shadow vs canary step vs default) or why it is holding.
    serving_role: str | None = None
    canary_step_index: int = 0
    verdict: str | None = None
    flywheel_run_id: str | None = None
    eval_metrics: dict[str, Any] | None = None
    created_at: datetime | None = None
    decided_by: str | None = None
    decided_at: datetime | None = None


class PromotionList(BaseModel):
    """List of promotions."""

    promotions: list[PromotionRecordResponse]


class ApproveRequest(BaseModel):
    """Approve a candidate at its pending gate."""

    approved_by: str = Field(..., description="Identity of the approver")
    note: str | None = None


class RejectRequest(BaseModel):
    """Reject a candidate at its pending gate."""

    rejected_by: str = Field(..., description="Identity of the rejecter")
    reason: str | None = None


class PromotionDecisionResponse(BaseModel):
    """Result of an approve/reject decision."""

    promotion_id: str
    state: PromotionState
    decided_by: str | None = None
    decided_at: datetime | None = None
    gate: PromotionGate | None = None
