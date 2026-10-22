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

"""Pydantic schemas for model deployment API."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from src.lib.deployment.adapters.base import DeploymentPlatform, DeploymentStatus


class ModelDeploymentRequest(BaseModel):
    """Request to deploy a trained model.

    Example:
        {
            "model_name": "nvidia/nv-embedqa-e5-v5@cust-bugnemo-v1",
            "deployment_name": "bugnemo-embedding-prod",
            "platform": "datarobot",
            "platform_config": {
                "deployment_id": "69326f63a1d39690a7ed0d4e"
            }
        }

    Note: For DataRobot, ``deployment_id`` is the user-facing DataRobot Deployment ID
    (visible in the DataRobot UI), **not** the internal LRS ID. The adapter resolves
    the LRS name automatically via Kubernetes label lookup.
    """

    # Source - identifies the model to deploy
    model_name: str = Field(
        ...,
        description="Customized model name (e.g., nvidia/nv-embedqa-e5-v5@cust-xyz). "
        "The model must exist in the customizations collection.",
        examples=["nvidia/nv-embedqa-e5-v5@cust-bugnemo-v1"],
    )
    job_id: str | None = Field(
        None,
        description="Optional DFW job ID that produced the model (for reference only)",
        examples=["675a1b2c3d4e5f6a7b8c9d0e"],
    )
    datastore_url: str | None = Field(
        None,
        description="Override datastore URL (uses system default if not provided)",
    )

    # Deployment configuration
    deployment_name: str = Field(
        ...,
        description="Name for this deployment",
        examples=["bugnemo-embedding-prod"],
    )
    platform: DeploymentPlatform = Field(
        ...,
        description="Target deployment platform",
        examples=["datarobot", "nmp"],
    )

    # Platform-specific config (optional)
    platform_config: dict[str, Any] | None = Field(
        None,
        description="Platform-specific configuration overrides. "
        "For DataRobot: deployment_id is the DataRobot Deployment ID (not the LRS ID).",
        examples=[{"deployment_id": "69326f63a1d39690a7ed0d4e"}],
    )


class InferenceExample(BaseModel):
    """Example inference request for a deployed model."""

    curl_command: str = Field(..., description="curl command example")
    python_example: str | None = Field(None, description="Python code example")
    description: str = Field(..., description="Description of the example")


class ModelDeploymentResponse(BaseModel):
    """Response for model deployment operations."""

    id: str = Field(..., description="Deployment record ID")
    job_id: str | None = Field(None, description="Source DFW job ID (if provided)")
    model_name: str = Field(..., description="Deployed model name")
    model_version: str | None = Field(None, description="Model version/revision")
    deployment_name: str = Field(..., description="Deployment name")
    platform: DeploymentPlatform = Field(..., description="Deployment platform")
    status: DeploymentStatus = Field(..., description="Current deployment status")

    # Endpoint information
    endpoint_url: str | None = Field(None, description="Inference endpoint URL")
    inference_examples: dict[str, InferenceExample] | None = Field(
        None,
        description="Example inference requests",
    )

    # Timestamps
    created_at: datetime = Field(..., description="When the deployment was created")
    started_at: datetime | None = Field(None, description="When deployment started")
    deployed_at: datetime | None = Field(None, description="When deployment completed")

    # Error handling
    error: str | None = Field(None, description="Error message if deployment failed")

    # Platform-specific metadata
    platform_metadata: dict[str, Any] | None = Field(
        None,
        description="Platform-specific deployment metadata",
    )

    model_config = {"from_attributes": True}


class ModelDeploymentListResponse(BaseModel):
    """Response for listing deployments."""

    deployments: list[ModelDeploymentResponse] = Field(..., description="List of deployments")
    total: int = Field(..., description="Total number of deployments")
    page: int = Field(default=1, description="Current page number")
    page_size: int = Field(default=20, description="Number of items per page")


class DeploymentStatusResponse(BaseModel):
    """Response for deployment status check."""

    id: str = Field(..., description="Deployment record ID")
    status: DeploymentStatus = Field(..., description="Current deployment status")
    endpoint_url: str | None = Field(None, description="Inference endpoint URL")
    progress: float | None = Field(
        None, description="Deployment progress (0.0 to 1.0)", ge=0.0, le=1.0
    )
    message: str | None = Field(None, description="Status message")
    error: str | None = Field(None, description="Error message if failed")
    updated_at: datetime = Field(..., description="Last status update time")


class DeploymentLogsResponse(BaseModel):
    """Response for deployment logs."""

    id: str = Field(..., description="Deployment record ID")
    logs: list[str] = Field(..., description="Log entries")
    has_more: bool = Field(default=False, description="Whether there are more logs available")


class DeploymentDeleteResponse(BaseModel):
    """Response for deployment deletion."""

    id: str = Field(..., description="Deployment record ID")
    status: DeploymentStatus = Field(..., description="Final deployment status")
    message: str = Field(..., description="Deletion status message")
