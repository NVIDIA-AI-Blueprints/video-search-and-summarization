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

"""FastAPI endpoints for the promotion (HITL) surface."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pymongo.database import Database

from src.api.db import get_db
from src.api.promotion.models import PromotionRecord, PromotionState
from src.api.promotion.schemas import (
    ApproveRequest,
    PromotionDecisionResponse,
    PromotionList,
    PromotionRecordResponse,
    RejectRequest,
)
from src.api.promotion.service import PromotionConflictError, PromotionService
from src.log_utils import setup_logging

logger = setup_logging("data_flywheel.promotion.endpoints")

router = APIRouter(prefix="/promotions", tags=["promotions"])


def get_promotion_service(db: Database = Depends(get_db)) -> PromotionService:
    """Get a promotion service instance."""
    return PromotionService(db)


def _to_response(record: PromotionRecord) -> PromotionRecordResponse:
    """Convert a promotion record to its API response."""
    return PromotionRecordResponse(
        promotion_id=record.promotion_id,
        model_name=record.model_name,
        candidate_version=record.candidate_version,
        baseline_version=record.baseline_version,
        deft_run_id=record.deft_run_id,
        use_case=record.use_case,
        state=record.state,
        pending_gate=record.pending_gate,
        serving_role=record.serving_role,
        canary_step_index=record.canary_step_index,
        verdict=record.verdict,
        flywheel_run_id=record.flywheel_run_id,
        eval_metrics=record.eval_metrics,
        created_at=record.created_at,
        decided_by=record.decided_by,
        decided_at=record.decided_at,
    )


@router.get("", response_model=PromotionList)
async def list_promotions(
    state: Annotated[PromotionState | None, Query()] = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=1000)] = 50,
    service: PromotionService = Depends(get_promotion_service),
) -> PromotionList:
    """List promotions (HITL)."""
    records = service.list(state=state, skip=offset, limit=limit)
    return PromotionList(promotions=[_to_response(r) for r in records])


@router.get("/{promotion_id}", response_model=PromotionRecordResponse)
async def get_promotion(
    promotion_id: str,
    service: PromotionService = Depends(get_promotion_service),
) -> PromotionRecordResponse:
    """Get a promotion by id."""
    record = service.get(promotion_id)
    if not record:
        raise HTTPException(status_code=404, detail=f"Promotion not found: {promotion_id}")
    return _to_response(record)


@router.post("/{promotion_id}/approve", response_model=PromotionDecisionResponse)
async def approve_promotion(
    promotion_id: str,
    request: ApproveRequest,
    service: PromotionService = Depends(get_promotion_service),
) -> PromotionDecisionResponse:
    """Approve a candidate -> DFW proceeds to pull/apply."""
    try:
        record = service.approve(promotion_id, request.approved_by, note=request.note)
    except PromotionConflictError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    if not record:
        raise HTTPException(status_code=404, detail=f"Promotion not found: {promotion_id}")
    return PromotionDecisionResponse(
        promotion_id=record.promotion_id,
        state=record.state,
        decided_by=record.decided_by,
        decided_at=record.decided_at,
        gate=record.pending_gate,
    )


@router.post("/{promotion_id}/reject", response_model=PromotionDecisionResponse)
async def reject_promotion(
    promotion_id: str,
    request: RejectRequest,
    service: PromotionService = Depends(get_promotion_service),
) -> PromotionDecisionResponse:
    """Reject a candidate -> candidate archived / rolled back."""
    try:
        record = service.reject(promotion_id, request.rejected_by, reason=request.reason)
    except PromotionConflictError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    if not record:
        raise HTTPException(status_code=404, detail=f"Promotion not found: {promotion_id}")
    return PromotionDecisionResponse(
        promotion_id=record.promotion_id,
        state=record.state,
        decided_by=record.decided_by,
        decided_at=record.decided_at,
        gate=record.pending_gate,
    )
