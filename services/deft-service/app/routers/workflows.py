from __future__ import annotations

from fastapi import APIRouter, Depends

from app.auth import Principal, get_principal
from app.data.workflows import WORKFLOWS
from app.errors import workflow_not_found
from app.schemas.workflow_models import WorkflowInfo

router = APIRouter(tags=["workflows"], dependencies=[Depends(get_principal)])


@router.get("/v1/workflows", response_model=list[WorkflowInfo])
async def list_workflows() -> list[WorkflowInfo]:
    return list(WORKFLOWS.values())


@router.get("/v1/workflows/{name}", response_model=WorkflowInfo)
async def get_workflow(name: str) -> WorkflowInfo:
    workflow = WORKFLOWS.get(name)
    if workflow is None:
        raise workflow_not_found(name)
    return workflow
