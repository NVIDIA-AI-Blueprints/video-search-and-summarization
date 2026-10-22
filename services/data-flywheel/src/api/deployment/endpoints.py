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

"""FastAPI endpoints for model deployment operations."""

from fastapi import APIRouter, Depends, HTTPException, Query
from pymongo.database import Database

from src.api.db import get_db
from src.api.deployment.schemas import (
    DeploymentDeleteResponse,
    DeploymentLogsResponse,
    DeploymentStatusResponse,
    ModelDeploymentListResponse,
    ModelDeploymentRequest,
    ModelDeploymentResponse,
)
from src.api.deployment.service import DeploymentQueueError, DeploymentService
from src.lib.deployment.adapters.base import DeploymentPlatform, DeploymentStatus
from src.lib.deployment.adapters.factory import DeploymentAdapterFactory
from src.log_utils import setup_logging

logger = setup_logging("data_flywheel.deployment.endpoints")

router = APIRouter(prefix="/model-deployments", tags=["Model Deployments"])


def get_deployment_service(db: Database = Depends(get_db)) -> DeploymentService:
    """Get deployment service instance.

    Args:
        db: MongoDB database instance

    Returns:
        DeploymentService instance
    """
    return DeploymentService(db)


@router.post(
    "",
    response_model=ModelDeploymentResponse,
    status_code=201,
    summary="Create a new model deployment",
    description="""
Deploy a trained model from a DFW job to a target platform.

The deployment is executed asynchronously - this endpoint returns immediately
with the deployment record, and the actual deployment happens in the background.

**Supported Platforms:**
- `datarobot`: Deploy to DataRobot via Kubernetes (requires deployment_id — the user-facing DataRobot Deployment ID, not the internal LRS ID)
- `nmp`: Deploy to NVIDIA Model Platform (NMP)

**Platform Configuration:**

For DataRobot:
```json
{
    "platform_config": {
        "deployment_id": "69326f63a1d39690a7ed0d4e",
        "model_type": "embedding"
    }
}
```

For NMP:
```json
{
    "platform_config": {
        "gpus": 1,
        "model_type": "embedding"
    }
}
```
""",
)
async def create_deployment(
    request: ModelDeploymentRequest,
    service: DeploymentService = Depends(get_deployment_service),
) -> ModelDeploymentResponse:
    """Create a new model deployment.

    Args:
        request: Deployment request
        service: Deployment service

    Returns:
        ModelDeploymentResponse with deployment details
    """
    try:
        # Validate platform is supported
        if not DeploymentAdapterFactory.is_platform_supported(request.platform):
            raise HTTPException(
                status_code=400,
                detail=f"Platform '{request.platform}' is not supported. "
                f"Available: {[p.value for p in DeploymentAdapterFactory.get_available_platforms()]}",
            )

        deployment, task_id = service.create_deployment(request)
        logger.info(
            f"Created deployment {deployment.id} for {request.model_name} "
            f"to {request.platform.value}, task_id={task_id}"
        )

        return service.to_response(deployment)

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    except NotImplementedError as e:
        # Platform exists but no adapter implemented yet
        raise HTTPException(status_code=501, detail=str(e)) from None
    except DeploymentQueueError:
        # Deployment was created but queuing the task failed
        # The deployment is already marked as FAILED in the DB
        # Return the deployment so client knows the ID and status
        logger.exception("Failed to queue deployment task")
        return service.to_response(deployment)
    except Exception:
        logger.exception("Failed to create deployment")
        raise HTTPException(status_code=500, detail="Failed to create deployment") from None


@router.get(
    "",
    response_model=ModelDeploymentListResponse,
    summary="List model deployments",
    description="List all model deployments with optional filters.",
)
async def list_deployments(
    platform: DeploymentPlatform | None = Query(None, description="Filter by platform"),
    status: DeploymentStatus | None = Query(None, description="Filter by status"),
    job_id: str | None = Query(None, description="Filter by source job ID"),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page"),
    service: DeploymentService = Depends(get_deployment_service),
) -> ModelDeploymentListResponse:
    """List model deployments with optional filters.

    Args:
        platform: Filter by platform
        status: Filter by status
        job_id: Filter by job ID
        page: Page number
        page_size: Page size
        service: Deployment service

    Returns:
        ModelDeploymentListResponse with deployments
    """
    deployments, total = service.list_deployments(
        platform=platform,
        status=status,
        job_id=job_id,
        page=page,
        page_size=page_size,
    )

    return ModelDeploymentListResponse(
        deployments=[service.to_response(d) for d in deployments],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get(
    "/platforms",
    summary="List available deployment platforms",
    description="Get list of platforms with registered adapters.",
)
async def list_platforms() -> dict:
    """List available deployment platforms.

    Returns:
        Dictionary with available platforms
    """
    platforms = DeploymentAdapterFactory.get_available_platforms()
    return {
        "platforms": [
            {
                "id": p.value,
                "name": p.name,
            }
            for p in platforms
        ]
    }


@router.get(
    "/{deployment_id}",
    response_model=ModelDeploymentResponse,
    summary="Get deployment details",
    description="Get detailed information about a specific deployment.",
)
async def get_deployment(
    deployment_id: str,
    service: DeploymentService = Depends(get_deployment_service),
) -> ModelDeploymentResponse:
    """Get deployment details.

    Args:
        deployment_id: Deployment ID
        service: Deployment service

    Returns:
        ModelDeploymentResponse
    """
    deployment = service.get_deployment(deployment_id)
    if not deployment:
        raise HTTPException(status_code=404, detail="Deployment not found")

    return service.to_response(deployment)


@router.get(
    "/{deployment_id}/status",
    response_model=DeploymentStatusResponse,
    summary="Get deployment status",
    description="Get current status of a deployment.",
)
async def get_deployment_status(
    deployment_id: str,
    service: DeploymentService = Depends(get_deployment_service),
) -> DeploymentStatusResponse:
    """Get deployment status.

    Args:
        deployment_id: Deployment ID
        service: Deployment service

    Returns:
        DeploymentStatusResponse
    """
    deployment = service.get_deployment(deployment_id)
    if not deployment:
        raise HTTPException(status_code=404, detail="Deployment not found")

    return DeploymentStatusResponse(
        id=deployment.id,
        status=deployment.status,
        endpoint_url=deployment.endpoint_url,
        progress=deployment.progress,
        message=deployment.status_message,
        error=deployment.error,
        updated_at=deployment.updated_at,
    )


@router.get(
    "/{deployment_id}/logs",
    response_model=DeploymentLogsResponse,
    summary="Get deployment logs",
    description="Get logs for a deployment operation.",
)
async def get_deployment_logs(
    deployment_id: str,
    limit: int = Query(100, ge=1, le=1000, description="Maximum logs to return"),
    service: DeploymentService = Depends(get_deployment_service),
) -> DeploymentLogsResponse:
    """Get deployment logs.

    Args:
        deployment_id: Deployment ID
        limit: Maximum logs to return
        service: Deployment service

    Returns:
        DeploymentLogsResponse
    """
    deployment = service.get_deployment(deployment_id)
    if not deployment:
        raise HTTPException(status_code=404, detail="Deployment not found")

    logs = service.get_deployment_logs(deployment_id, limit=limit)
    log_strings = [
        f"[{log['timestamp']}] [{log['level'].upper()}] {log['message']}" for log in logs
    ]

    return DeploymentLogsResponse(
        id=deployment_id,
        logs=log_strings,
        has_more=len(deployment.logs) > limit,
    )


@router.delete(
    "/{deployment_id}",
    response_model=DeploymentDeleteResponse,
    summary="Delete/undeploy a deployment",
    description="Remove a model deployment from the target platform.",
)
async def delete_deployment(
    deployment_id: str,
    service: DeploymentService = Depends(get_deployment_service),
) -> DeploymentDeleteResponse:
    """Delete a deployment.

    Args:
        deployment_id: Deployment ID
        service: Deployment service

    Returns:
        DeploymentDeleteResponse
    """
    deployment = service.get_deployment(deployment_id)
    if not deployment:
        raise HTTPException(status_code=404, detail="Deployment not found")

    if deployment.status == DeploymentStatus.DELETED:
        raise HTTPException(status_code=400, detail="Deployment already deleted")

    updated = service.delete_deployment(deployment_id)
    if not updated:
        raise HTTPException(status_code=500, detail="Failed to delete deployment")

    return DeploymentDeleteResponse(
        id=deployment_id,
        status=updated.status,
        message="Deployment deletion initiated",
    )
