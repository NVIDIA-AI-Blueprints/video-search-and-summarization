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

"""Service layer for model deployment operations."""

from datetime import datetime
from typing import Any

from pymongo.database import Database
from pymongo.errors import DuplicateKeyError

from src.api.deployment.models import ModelDeployment
from src.api.deployment.schemas import (
    InferenceExample,
    ModelDeploymentRequest,
    ModelDeploymentResponse,
)
from src.config import settings
from src.lib.deployment.adapters.base import DeploymentPlatform, DeploymentStatus
from src.log_utils import setup_logging

logger = setup_logging("data_flywheel.deployment.service")

# MongoDB collection name for deployments
DEPLOYMENTS_COLLECTION = "model_deployments"


class DeploymentQueueError(Exception):
    """Raised when queuing a deployment task fails."""

    pass


class DeploymentService:
    """Service for managing model deployments."""

    def __init__(self, db: Database):
        """Initialize the deployment service.

        Args:
            db: MongoDB database instance
        """
        self.db = db
        self.collection = db[DEPLOYMENTS_COLLECTION]

    def check_ongoing_deployment(
        self,
        platform: DeploymentPlatform,
        platform_config: dict[str, Any] | None = None,
    ) -> ModelDeployment | None:
        """Check if there's an ongoing deployment for the same target.

        An ongoing deployment is one with status PENDING or DEPLOYING.
        For DataRobot, we check the deployment_id in platform_config.
        For other platforms, we check platform + deployment_name combination.

        Args:
            platform: Target deployment platform
            platform_config: Platform-specific configuration

        Returns:
            The ongoing ModelDeployment if found, None otherwise
        """
        # Build query for ongoing deployments
        ongoing_statuses = [DeploymentStatus.PENDING.value, DeploymentStatus.DEPLOYING.value]
        query: dict[str, Any] = {
            "platform": platform.value,
            "status": {"$in": ongoing_statuses},
        }

        # Add platform-specific filters to identify the deployment target
        if platform_config:
            if platform == DeploymentPlatform.DATAROBOT:
                # DataRobot uses deployment_id (the user-facing DR Deployment ID)
                deployment_id = platform_config.get("deployment_id")
                if deployment_id:
                    query["platform_config.deployment_id"] = deployment_id
            else:
                # Other platforms (NMP, etc.) use deployment_name to identify the target
                deployment_name = platform_config.get("deployment_name")
                if deployment_name:
                    query["platform_config.deployment_name"] = deployment_name

        doc = self.collection.find_one(query)
        if doc:
            return ModelDeployment(**doc)
        return None

    def create_deployment(
        self, request: ModelDeploymentRequest
    ) -> tuple[ModelDeployment, str | None]:
        """Create a new deployment record and queue the deployment task.

        Args:
            request: Deployment request

        Returns:
            Tuple of (ModelDeployment, celery_task_id)

        Raises:
            ValueError: If there's already an ongoing deployment for the same target
        """
        # Check for ongoing deployment to the same target
        ongoing = self.check_ongoing_deployment(request.platform, request.platform_config)
        if ongoing:
            raise ValueError(
                f"Deployment already in progress for this target. "
                f"Existing deployment ID: {ongoing.id}, status: {ongoing.status.value}. "
                f"Please wait for it to complete or cancel it first."
            )

        # Resolve model info and platform config based on platform
        platform_config = request.platform_config or {}
        is_customized = "@" in request.model_name

        if request.platform == DeploymentPlatform.NMP and not is_customized:
            # Base model NMP deployment -- no customization record needed
            model_name = request.model_name
            model_version = None
            datastore_url = (
                request.datastore_url or settings.system_config.nmp_config.datastore_base_url
            )
        elif request.platform == DeploymentPlatform.CUSTOM_NIM:
            # Custom NIM build -- model_name is the datastore repo_id, version from @ suffix
            if is_customized:
                name_part, version = request.model_name.rsplit("@", 1)
                model_name = name_part
                model_version = version
            else:
                model_name = request.model_name
                model_version = "main"
            datastore_url = (
                request.datastore_url or settings.system_config.nmp_config.datastore_base_url
            )
        else:
            # Customized model -- look up from customizations collection
            model_info = self._get_model_info_by_name(request.model_name)
            model_name = model_info.get("model_name")
            model_version = model_info.get("model_version")
            datastore_url = request.datastore_url or model_info.get(
                "datastore_url", settings.system_config.nmp_config.datastore_base_url
            )

        # Create deployment record
        deployment = ModelDeployment(
            job_id=request.job_id,
            model_name=model_name,
            model_version=model_version,
            datastore_url=datastore_url,
            deployment_name=request.deployment_name,
            platform=request.platform,
            platform_config=platform_config,
            status=DeploymentStatus.PENDING,
        )

        deployment.add_log(f"Deployment requested to {request.platform.value}")

        # Save to MongoDB (partial unique index prevents race condition duplicates)
        try:
            self.collection.insert_one(deployment.model_dump(by_alias=True))
        except DuplicateKeyError:
            # Race condition: another deployment was created between our check and insert
            logger.warning(
                f"Duplicate deployment detected for platform {request.platform.value} "
                f"(caught by unique index)"
            )
            raise ValueError(
                "Deployment already in progress for this target (concurrent request detected). "
                "Please wait for the existing deployment to complete or cancel it first."
            ) from None
        logger.info(f"Created deployment record {deployment.id}")

        # Queue the async deployment task
        try:
            task_id = self._queue_deployment_task(deployment)
            deployment.celery_task_id = task_id
            self.collection.update_one(
                {"_id": deployment.id},
                {"$set": {"celery_task_id": task_id}},
            )
            return deployment, task_id

        except DeploymentQueueError:
            # Mark deployment as FAILED since we couldn't queue the task
            deployment.status = DeploymentStatus.FAILED
            deployment.error = "Failed to queue deployment task"
            deployment.add_log("Failed to queue deployment task", level="error")
            self.collection.update_one(
                {"_id": deployment.id},
                {
                    "$set": {
                        "status": DeploymentStatus.FAILED.value,
                        "error": deployment.error,
                        "logs": [log.model_dump() for log in deployment.logs],
                        "updated_at": datetime.utcnow(),
                    }
                },
            )
            raise

    def get_deployment(self, deployment_id: str) -> ModelDeployment | None:
        """Get a deployment by ID.

        Args:
            deployment_id: Deployment ID

        Returns:
            ModelDeployment or None if not found
        """
        doc = self.collection.find_one({"_id": deployment_id})
        if doc:
            return ModelDeployment(**doc)
        return None

    def list_deployments(
        self,
        platform: DeploymentPlatform | None = None,
        status: DeploymentStatus | None = None,
        job_id: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[ModelDeployment], int]:
        """List deployments with optional filters.

        Args:
            platform: Filter by platform
            status: Filter by status
            job_id: Filter by source job ID
            page: Page number (1-indexed)
            page_size: Number of items per page

        Returns:
            Tuple of (list of deployments, total count)
        """
        query: dict[str, Any] = {}

        if platform:
            query["platform"] = platform.value
        if status:
            query["status"] = status.value
        if job_id:
            query["job_id"] = job_id

        # Get total count
        total = self.collection.count_documents(query)

        # Get paginated results
        skip = (page - 1) * page_size
        cursor = self.collection.find(query).sort("created_at", -1).skip(skip).limit(page_size)

        deployments = [ModelDeployment(**doc) for doc in cursor]
        return deployments, total

    def update_deployment_status(
        self,
        deployment_id: str,
        status: DeploymentStatus,
        message: str | None = None,
        progress: float | None = None,
        error: str | None = None,
        endpoint_url: str | None = None,
        platform_metadata: dict[str, Any] | None = None,
    ) -> ModelDeployment | None:
        """Update deployment status.

        Args:
            deployment_id: Deployment ID
            status: New status
            message: Optional status message
            progress: Optional progress value
            error: Optional error message
            endpoint_url: Optional endpoint URL
            platform_metadata: Optional platform metadata

        Returns:
            Updated ModelDeployment or None
        """
        update: dict[str, Any] = {
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

        # Set timestamps based on status
        if status == DeploymentStatus.DEPLOYING:
            update["started_at"] = datetime.utcnow()
        elif status == DeploymentStatus.READY:
            update["deployed_at"] = datetime.utcnow()
            update["progress"] = 1.0
        elif status == DeploymentStatus.DELETED:
            update["deleted_at"] = datetime.utcnow()

        # Add log entry for the status change
        log_entry = {
            "timestamp": datetime.utcnow(),
            "level": "error" if error else "info",
            "message": message or f"Status changed to {status.value}",
            "details": None,
        }

        result = self.collection.find_one_and_update(
            {"_id": deployment_id},
            {
                "$set": update,
                "$push": {"logs": log_entry},
            },
            return_document=True,
        )

        if result:
            return ModelDeployment(**result)
        return None

    def delete_deployment(self, deployment_id: str) -> ModelDeployment | None:
        """Delete/undeploy a deployment.

        Queues an async undeploy task and marks the deployment as DELETING.
        The task will mark it as DELETED when complete.

        Args:
            deployment_id: Deployment ID

        Returns:
            Updated ModelDeployment with DELETING status, or None if not found
            or queuing failed
        """
        deployment = self.get_deployment(deployment_id)
        if not deployment:
            return None

        # Queue the undeploy task
        task_id = self._queue_undeploy_task(deployment)
        if not task_id:
            logger.error(f"Failed to queue undeploy task for deployment {deployment_id}")
            return None

        # Add log entry for the status change
        self.add_deployment_log(
            deployment_id,
            message=f"Undeploy initiated, task queued (task_id: {task_id})",
        )

        # Update status to DELETING (task will set DELETED when complete)
        return self.update_deployment_status(
            deployment_id,
            DeploymentStatus.DELETING,
            message=f"Undeploy task queued (task_id: {task_id})",
        )

    def add_deployment_log(
        self,
        deployment_id: str,
        message: str,
        level: str = "info",
        details: dict[str, Any] | None = None,
    ):
        """Add a log entry to a deployment.

        Args:
            deployment_id: Deployment ID
            message: Log message
            level: Log level
            details: Optional details
        """
        log_entry = {
            "timestamp": datetime.utcnow(),
            "level": level,
            "message": message,
            "details": details,
        }

        self.collection.update_one(
            {"_id": deployment_id},
            {
                "$push": {"logs": log_entry},
                "$set": {"updated_at": datetime.utcnow()},
            },
        )

    def get_deployment_logs(self, deployment_id: str, limit: int = 100) -> list[dict[str, Any]]:
        """Get deployment logs.

        Args:
            deployment_id: Deployment ID
            limit: Maximum number of logs to return

        Returns:
            List of log entries
        """
        deployment = self.get_deployment(deployment_id)
        if not deployment:
            return []

        # Return most recent logs first
        logs = list(reversed(deployment.logs[-limit:]))
        return [log.model_dump() for log in logs]

    def to_response(self, deployment: ModelDeployment) -> ModelDeploymentResponse:
        """Convert deployment to API response.

        Args:
            deployment: ModelDeployment instance

        Returns:
            ModelDeploymentResponse
        """
        # Generate inference examples based on platform and model type
        inference_examples = self._generate_inference_examples(deployment)

        return ModelDeploymentResponse(
            id=deployment.id,
            job_id=deployment.job_id,
            model_name=deployment.model_name,
            model_version=deployment.model_version,
            deployment_name=deployment.deployment_name,
            platform=deployment.platform,
            status=deployment.status,
            endpoint_url=deployment.endpoint_url,
            inference_examples=inference_examples,
            created_at=deployment.created_at,
            started_at=deployment.started_at,
            deployed_at=deployment.deployed_at,
            error=deployment.error,
            platform_metadata=deployment.platform_metadata,
        )

    def _get_model_info_by_name(self, model_name: str) -> dict[str, Any]:
        """Look up model info by model name from the customizations collection.

        Args:
            model_name: Full model name (e.g., namespace/model@version)

        Returns:
            Dictionary with model info including:
            - datastore_url: URL to the datastore
            - model_namespace: Namespace for the model
            - model_name: Full model name
            - model_version: Version of the model

        Raises:
            ValueError: If model not found
        """
        # Query the customizations collection directly by model name
        customization = self.db.customizations.find_one({"customized_model": model_name})

        if customization:
            # Parse model name format: namespace/model@version
            parsed = self._parse_model_name(model_name)
            return {
                "datastore_url": settings.system_config.nmp_config.datastore_base_url,
                "model_namespace": parsed["namespace"],
                "model_name": parsed["full_name"],  # namespace/model (without version)
                "model_version": parsed["version"],
                "base_model": customization.get("base_model"),
                "nmp_uri": customization.get("nmp_uri"),
                "nim_id": customization.get("nim_id"),
            }

        # Model not found in customizations
        raise ValueError(
            f"Model '{model_name}' not found in customizations. "
            "Please ensure the model has been trained and customized."
        )

    def _parse_model_name(self, model_name: str) -> dict[str, str]:
        """Parse model name into components.

        Model name format: namespace/model@version
        Example: dfwbp/0o7@cust-2HGXk3R5s82E3hr6sf5NCf
        - namespace: dfwbp
        - model: 0o7
        - version: cust-2HGXk3R5s82E3hr6sf5NCf

        Args:
            model_name: Full model name string

        Returns:
            Dictionary with:
            - namespace: The namespace part
            - model: The model name part
            - version: The version part
            - full_name: namespace/model (without version)

        Raises:
            ValueError: If model name format is invalid
        """
        # Validate model name format: namespace/model@version
        if (
            model_name.count("@") != 1
            or model_name.count("/") != 1
            or not model_name.index("/") < model_name.index("@")
        ):
            raise ValueError(
                f"Invalid model name '{model_name}'. "
                "Expected format: namespace/model@version (e.g., dfwbp/my-model@cust-v1)"
            )

        # Parse: namespace/model@version
        name_part, version = model_name.rsplit("@", 1)
        namespace, model = name_part.split("/", 1)

        # Validate parts are not empty
        if not namespace or not model or not version:
            raise ValueError(
                f"Invalid model name '{model_name}': namespace, model, and version "
                "must all be non-empty. Expected format: namespace/model@version"
            )

        return {
            "namespace": namespace,
            "model": model,
            "version": version,
            "full_name": name_part,  # namespace/model without version
        }

    def _queue_deployment_task(self, deployment: ModelDeployment) -> str:
        """Queue an async deployment task.

        Args:
            deployment: Deployment record

        Returns:
            Celery task ID

        Raises:
            DeploymentQueueError: If queuing the task fails
        """
        try:
            from src.tasks.deployment.tasks import execute_deployment

            task = execute_deployment.delay(deployment.id)
            logger.info(f"Queued deployment task {task.id} for {deployment.id}")
            return task.id
        except Exception:
            logger.exception(f"Failed to queue deployment task for {deployment.id}")
            raise DeploymentQueueError(
                f"Failed to queue deployment task for {deployment.id}"
            ) from None

    def _queue_undeploy_task(self, deployment: ModelDeployment) -> str | None:
        """Queue an async undeploy task.

        Args:
            deployment: Deployment record

        Returns:
            Celery task ID or None
        """
        try:
            from src.tasks.deployment.tasks import execute_undeploy

            task = execute_undeploy.delay(deployment.id)
            logger.info(f"Queued undeploy task {task.id} for {deployment.id}")
            return task.id
        except Exception:
            logger.exception("Failed to queue undeploy task")
            return None

    def _generate_inference_examples(
        self, deployment: ModelDeployment
    ) -> dict[str, InferenceExample] | None:
        """Generate inference examples for a deployment.

        Args:
            deployment: Deployment record

        Returns:
            Dictionary of inference examples
        """
        if deployment.status != DeploymentStatus.READY or not deployment.endpoint_url:
            return None

        examples: dict[str, InferenceExample] = {}

        if deployment.platform == DeploymentPlatform.DATAROBOT:
            # Get model type from config
            model_type = deployment.platform_config.get("model_type", "embedding")

            if model_type == "embedding":
                examples["embedding"] = InferenceExample(
                    description="Generate embeddings for text input",
                    curl_command=f"""curl -X POST '{deployment.endpoint_url}' \\
  -H 'Authorization: Bearer $API_TOKEN' \\
  -H 'Content-Type: application/json' \\
  -d '{{
    "input": ["What is the capital of France?"],
    "model": "{deployment.model_name}",
    "input_type": "query"
  }}'""",
                    python_example=f"""import requests

response = requests.post(
    "{deployment.endpoint_url}",
    headers={{"Authorization": "Bearer $API_TOKEN", "Content-Type": "application/json"}},
    json={{
        "input": ["What is the capital of France?"],
        "model": "{deployment.model_name}",
        "input_type": "query"
    }}
)
print(response.json())""",
                )
            elif model_type == "chat":
                examples["chat"] = InferenceExample(
                    description="Generate chat completions",
                    curl_command=f"""curl -X POST '{deployment.endpoint_url}' \\
  -H 'Authorization: Bearer $API_TOKEN' \\
  -H 'Content-Type: application/json' \\
  -d '{{
    "model": "{deployment.model_name}",
    "messages": [{{"role": "user", "content": "Hello!"}}],
    "max_tokens": 1024
  }}'""",
                    python_example=f"""import requests

response = requests.post(
    "{deployment.endpoint_url}",
    headers={{"Authorization": "Bearer $API_TOKEN", "Content-Type": "application/json"}},
    json={{
        "model": "{deployment.model_name}",
        "messages": [{{"role": "user", "content": "Hello!"}}],
        "max_tokens": 1024
    }}
)
print(response.json())""",
                )

        elif deployment.platform == DeploymentPlatform.NMP:
            examples["embedding"] = InferenceExample(
                description="Generate embeddings using NMP endpoint",
                curl_command=f"""curl -X POST '{deployment.endpoint_url}' \\
  -H 'Content-Type: application/json' \\
  -d '{{
    "input": ["What is the capital of France?"],
    "model": "{deployment.model_name}",
    "input_type": "query"
  }}'""",
            )

        return examples if examples else None
