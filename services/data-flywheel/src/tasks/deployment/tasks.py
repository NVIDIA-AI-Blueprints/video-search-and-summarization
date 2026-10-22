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

"""Celery tasks for executing model deployments asynchronously."""

from celery import shared_task

from src.api.db import get_db, init_db
from src.api.deployment.models import ModelDeployment
from src.lib.deployment.adapters.base import DeploymentPlatform, DeploymentStatus
from src.lib.deployment.adapters.factory import DeploymentAdapterFactory
from src.log_utils import setup_logging

logger = setup_logging("data_flywheel.deployment.tasks")

# MongoDB collection name
DEPLOYMENTS_COLLECTION = "model_deployments"


def _resolve_nmp_platform_config(deployment: ModelDeployment) -> dict:
    """For NMP deployments, ensure platform_config has model_name (required by NIMConfig).

    If not provided, reconstructs it from deployment.model_name (+ model_version if present).
    """
    platform_config = deployment.platform_config or {}
    if deployment.platform == DeploymentPlatform.NMP and "model_name" not in platform_config:
        if deployment.model_version:
            full_model_name = f"{deployment.model_name}@{deployment.model_version}"
        else:
            full_model_name = deployment.model_name
        platform_config["model_name"] = full_model_name
        logger.info(f"Defaulting platform_config.model_name to '{full_model_name}'")
    return platform_config


def _get_db():
    """Get MongoDB database instance, initializing if needed."""
    try:
        return get_db()
    except RuntimeError:
        # Database not initialized yet, initialize it
        init_db()
        return get_db()


def get_deployment(deployment_id: str) -> ModelDeployment | None:
    """Get deployment from MongoDB.

    Args:
        deployment_id: Deployment ID

    Returns:
        ModelDeployment or None
    """
    db = _get_db()
    doc = db[DEPLOYMENTS_COLLECTION].find_one({"_id": deployment_id})
    if doc:
        return ModelDeployment(**doc)
    return None


def update_deployment(
    deployment_id: str,
    status: DeploymentStatus,
    message: str | None = None,
    progress: float | None = None,
    error: str | None = None,
    endpoint_url: str | None = None,
    platform_metadata: dict | None = None,
    platform_deployment_id: str | None = None,
):
    """Update deployment status in MongoDB.

    Args:
        deployment_id: Deployment ID
        status: New status
        message: Status message
        progress: Progress value
        error: Error message
        endpoint_url: Endpoint URL
        platform_metadata: Platform metadata
        platform_deployment_id: Platform deployment ID
    """
    from datetime import datetime

    db = _get_db()
    update: dict = {
        "status": status.value,
        "updated_at": datetime.utcnow(),
    }

    if message:
        update["status_message"] = message
    if progress is not None:
        update["progress"] = progress
    if error:
        update["error"] = error
    if endpoint_url:
        update["endpoint_url"] = endpoint_url
    if platform_metadata:
        update["platform_metadata"] = platform_metadata
    if platform_deployment_id:
        update["platform_deployment_id"] = platform_deployment_id

    # Set timestamps based on status
    if status == DeploymentStatus.DEPLOYING:
        update["started_at"] = datetime.utcnow()
    elif status == DeploymentStatus.READY:
        update["deployed_at"] = datetime.utcnow()
        update["progress"] = 1.0
    elif status == DeploymentStatus.DELETED:
        update["deleted_at"] = datetime.utcnow()

    # Add log entry
    log_entry = {
        "timestamp": datetime.utcnow(),
        "level": "error" if error else "info",
        "message": message or f"Status changed to {status.value}",
        "details": None,
    }

    db[DEPLOYMENTS_COLLECTION].update_one(
        {"_id": deployment_id},
        {
            "$set": update,
            "$push": {"logs": log_entry},
        },
    )


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def execute_deployment(self, deployment_id: str) -> dict:
    """Execute a model deployment asynchronously.

    This task:
    1. Retrieves the deployment record
    2. Creates the appropriate adapter
    3. Downloads the model from datastore
    4. Deploys to the target platform
    5. Updates the deployment record with results

    Args:
        deployment_id: ID of the deployment record

    Returns:
        Dictionary with deployment result
    """
    logger.info(f"Starting deployment task for {deployment_id}")

    try:
        # Get deployment record
        deployment = get_deployment(deployment_id)
        if not deployment:
            logger.error(f"Deployment {deployment_id} not found")
            return {"error": "Deployment not found"}

        # Update status to deploying
        update_deployment(
            deployment_id,
            DeploymentStatus.DEPLOYING,
            message="Starting deployment",
            progress=0.05,
        )

        # Extract model info
        model_name = deployment.model_name
        model_version = deployment.model_version or "v1"

        # Create adapter for the platform
        platform_config = _resolve_nmp_platform_config(deployment)
        adapter = DeploymentAdapterFactory.create(
            deployment.platform,
            platform_config,
        )

        # Progress callback for real-time updates
        def progress_callback(message: str, progress: float):
            update_deployment(
                deployment_id,
                DeploymentStatus.DEPLOYING,
                message=message,
                progress=progress,
            )

        # Deploy model (adapter handles download internally)
        result = adapter.deploy(
            datastore_url=deployment.datastore_url,
            model_name=model_name,
            model_version=model_version,
            deployment_name=deployment.deployment_name,
            config=deployment.platform_config,
            progress_callback=progress_callback,
        )

        # Update with final result
        if result.status == DeploymentStatus.READY:
            update_deployment(
                deployment_id,
                DeploymentStatus.READY,
                message="Deployment successful",
                progress=1.0,
                endpoint_url=result.endpoint_url,
                platform_metadata=result.metadata,
                platform_deployment_id=result.deployment_id,
            )
            logger.info(f"Deployment {deployment_id} completed successfully")
        else:
            update_deployment(
                deployment_id,
                DeploymentStatus.FAILED,
                message="Deployment failed",
                error=result.error,
                platform_metadata=result.metadata,
            )
            logger.error(f"Deployment {deployment_id} failed: {result.error}")

        return {
            "deployment_id": deployment_id,
            "status": result.status.value,
            "endpoint_url": result.endpoint_url,
            "error": result.error,
        }

    except Exception as e:
        logger.exception(f"Deployment task failed for {deployment_id}: {e}")

        update_deployment(
            deployment_id,
            DeploymentStatus.FAILED,
            message="Deployment task failed",
            error=str(e),
        )

        # Retry on transient errors
        if "timeout" in str(e).lower() or "connection" in str(e).lower():
            raise self.retry(exc=e) from e

        return {
            "deployment_id": deployment_id,
            "status": "failed",
            "error": str(e),
        }


@shared_task(bind=True, max_retries=2, default_retry_delay=30)
def execute_undeploy(self, deployment_id: str) -> dict:
    """Execute model undeployment asynchronously.

    This task:
    1. Retrieves the deployment record
    2. Creates the appropriate adapter
    3. Calls undeploy on the platform (with progress logging)
    4. Updates the deployment record

    Args:
        deployment_id: ID of the deployment record

    Returns:
        Dictionary with undeploy result
    """
    logger.info(f"Starting undeploy task for {deployment_id}")

    try:
        # Get deployment record
        deployment = get_deployment(deployment_id)
        if not deployment:
            logger.error(f"Deployment {deployment_id} not found")
            return {"error": "Deployment not found"}

        # Update status to DELETING with log entry
        update_deployment(
            deployment_id,
            DeploymentStatus.DELETING,
            message="Undeploy task started",
        )

        # Create adapter for the platform
        platform_config = _resolve_nmp_platform_config(deployment)
        adapter = DeploymentAdapterFactory.create(
            deployment.platform,
            platform_config,
        )

        # Get the platform deployment ID
        platform_id = deployment.platform_deployment_id
        if not platform_id:
            # Fall back to deployment_id from config
            platform_id = platform_config.get("deployment_id", deployment_id)

        # Progress callback for real-time log updates during undeploy
        def progress_callback(message: str, progress: float):
            update_deployment(
                deployment_id,
                DeploymentStatus.DELETING,
                message=message,
                progress=progress,
            )

        # Undeploy (with progress callback for intermediate log updates)
        result = adapter.undeploy(platform_id, progress_callback=progress_callback)

        if result.status == DeploymentStatus.DELETED:
            # Update status
            update_deployment(
                deployment_id,
                DeploymentStatus.DELETED,
                message="Deployment removed successfully",
                platform_metadata=result.metadata,
            )
            logger.info(f"Undeploy {deployment_id} completed")
        else:
            update_deployment(
                deployment_id,
                DeploymentStatus.FAILED,
                message="Undeploy failed",
                error=result.error,
                platform_metadata=result.metadata,
            )
            logger.error(f"Undeploy {deployment_id} failed: {result.error}")

        return {
            "deployment_id": deployment_id,
            "status": result.status.value,
        }

    except Exception as e:
        logger.exception(f"Undeploy task failed for {deployment_id}: {e}")

        update_deployment(
            deployment_id,
            DeploymentStatus.FAILED,
            message="Undeploy failed",
            error=str(e),
        )

        # Retry on transient errors
        if "timeout" in str(e).lower() or "connection" in str(e).lower():
            raise self.retry(exc=e) from e

        return {
            "deployment_id": deployment_id,
            "status": "failed",
            "error": str(e),
        }


@shared_task
def check_deployment_status(deployment_id: str) -> dict:
    """Check and update deployment status from the platform.

    This task can be used to sync the deployment status with
    the actual platform state.

    Args:
        deployment_id: ID of the deployment record

    Returns:
        Dictionary with current status
    """
    logger.info(f"Checking deployment status for {deployment_id}")

    try:
        deployment = get_deployment(deployment_id)
        if not deployment:
            return {"error": "Deployment not found"}

        # Create adapter
        platform_config = _resolve_nmp_platform_config(deployment)
        adapter = DeploymentAdapterFactory.create(
            deployment.platform,
            platform_config,
        )

        # Get platform deployment ID
        platform_id = deployment.platform_deployment_id
        if not platform_id:
            platform_id = platform_config.get("deployment_id", deployment_id)

        # Get status from platform
        result = adapter.get_status(platform_id)

        # Update if status changed
        if result.status != deployment.status:
            update_deployment(
                deployment_id,
                result.status,
                message=f"Status updated from platform: {result.status.value}",
                endpoint_url=result.endpoint_url,
                platform_metadata=result.metadata,
            )

        return {
            "deployment_id": deployment_id,
            "status": result.status.value,
            "endpoint_url": result.endpoint_url,
        }

    except Exception as e:
        logger.error(f"Failed to check deployment status: {e}")
        return {
            "deployment_id": deployment_id,
            "error": str(e),
        }
