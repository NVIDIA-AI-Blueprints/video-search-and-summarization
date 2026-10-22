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

"""MongoDB models for model deployment tracking."""

from datetime import datetime
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field

from src.lib.deployment.adapters.base import DeploymentPlatform, DeploymentStatus


class DeploymentLogEntry(BaseModel):
    """A single log entry for deployment operations."""

    timestamp: datetime = Field(default_factory=datetime.utcnow)
    level: str = Field(default="info", description="Log level: info, warning, error")
    message: str = Field(..., description="Log message")
    details: dict[str, Any] | None = Field(None, description="Additional details")


class ModelDeployment(BaseModel):
    """MongoDB model for deployment records.

    This model tracks the state and history of model deployments
    across different platforms.
    """

    id: str = Field(default_factory=lambda: str(uuid4()), alias="_id")

    # Source reference - where the model came from
    job_id: str | None = Field(None, description="DFW job ID that produced the model (optional)")
    model_name: str = Field(
        ...,
        description="Model name without version (e.g., namespace/model-name). Version is stored in model_version.",
    )
    model_version: str | None = Field(None, description="Model version/revision")
    datastore_url: str | None = Field(
        None,
        description="DataStore URL where model is stored. "
        "If not provided, uses system default from nmp_config.nemo_base_url",
    )

    # Deployment configuration
    deployment_name: str = Field(..., description="User-provided deployment name")
    platform: DeploymentPlatform = Field(..., description="Target deployment platform")
    platform_deployment_id: str | None = Field(
        None, description="Platform-specific deployment identifier"
    )

    # Status tracking
    status: DeploymentStatus = Field(
        default=DeploymentStatus.PENDING, description="Current deployment status"
    )
    endpoint_url: str | None = Field(None, description="Inference endpoint URL (when ready)")
    error: str | None = Field(None, description="Error message if deployment failed")
    progress: float = Field(
        default=0.0, description="Deployment progress (0.0 to 1.0)", ge=0.0, le=1.0
    )
    status_message: str | None = Field(None, description="Current status message")

    # Timestamps
    created_at: datetime = Field(
        default_factory=datetime.utcnow, description="When deployment was requested"
    )
    started_at: datetime | None = Field(None, description="When deployment actually started")
    deployed_at: datetime | None = Field(None, description="When deployment completed successfully")
    deleted_at: datetime | None = Field(None, description="When deployment was deleted")
    updated_at: datetime = Field(
        default_factory=datetime.utcnow, description="Last update timestamp"
    )

    # Platform-specific configuration and metadata
    platform_config: dict[str, Any] = Field(
        default_factory=dict, description="Platform-specific configuration used"
    )
    platform_metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Platform-specific metadata returned from deployment",
    )

    # Deployment logs
    logs: list[DeploymentLogEntry] = Field(
        default_factory=list, description="Deployment operation logs"
    )

    # Celery task tracking
    celery_task_id: str | None = Field(None, description="Celery task ID for async deployment")

    model_config = {
        "populate_by_name": True,
        "extra": "allow",
    }

    def add_log(self, message: str, level: str = "info", details: dict[str, Any] | None = None):
        """Add a log entry to the deployment.

        Args:
            message: Log message
            level: Log level (info, warning, error)
            details: Optional additional details
        """
        self.logs.append(
            DeploymentLogEntry(
                timestamp=datetime.utcnow(),
                level=level,
                message=message,
                details=details,
            )
        )
        self.updated_at = datetime.utcnow()

    def update_status(
        self,
        status: DeploymentStatus,
        message: str | None = None,
        progress: float | None = None,
        error: str | None = None,
    ):
        """Update deployment status.

        Args:
            status: New status
            message: Optional status message
            progress: Optional progress value (0.0 to 1.0)
            error: Optional error message
        """
        self.status = status
        self.updated_at = datetime.utcnow()

        if message:
            self.status_message = message
            self.add_log(message)

        if progress is not None:
            self.progress = progress

        if error:
            self.error = error
            self.add_log(error, level="error")

        # Set timestamps based on status
        if status == DeploymentStatus.DEPLOYING and self.started_at is None:
            self.started_at = datetime.utcnow()
        elif status == DeploymentStatus.READY and self.deployed_at is None:
            self.deployed_at = datetime.utcnow()
            self.progress = 1.0
        elif status == DeploymentStatus.DELETED and self.deleted_at is None:
            self.deleted_at = datetime.utcnow()

    def to_response_dict(self) -> dict[str, Any]:
        """Convert to response dictionary format.

        Returns:
            Dictionary suitable for API response
        """
        return {
            "id": self.id,
            "job_id": self.job_id,
            "model_name": self.model_name,
            "model_version": self.model_version,
            "deployment_name": self.deployment_name,
            "platform": self.platform.value,
            "status": self.status.value,
            "endpoint_url": self.endpoint_url,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "deployed_at": self.deployed_at,
            "error": self.error,
            "platform_metadata": self.platform_metadata,
        }
