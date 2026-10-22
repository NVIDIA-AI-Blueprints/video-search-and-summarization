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

"""NMP (NVIDIA Model Platform) deployment adapter wrapping existing DMSClient."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from src.config import NIMConfig, settings
from src.lib.deployment.adapters.base import (
    BaseDeploymentAdapter,
    DeploymentPlatform,
    DeploymentResult,
    DeploymentStatus,
)
from src.lib.deployment.adapters.factory import DeploymentAdapterFactory
from src.log_utils import setup_logging

if TYPE_CHECKING:
    # NMP-only; imported lazily at runtime in _get_dms_client so this module
    # loads on any image (e.g. a DEFT build without nemo-platform).
    from src.backends.nmp.dms_client import DMSClient

logger = setup_logging("data_flywheel.deployment.nmp")


@DeploymentAdapterFactory.register(DeploymentPlatform.NMP)
class NMPAdapter(BaseDeploymentAdapter):
    """Adapter for deploying models to NMP (NVIDIA Model Platform).

    This adapter wraps the existing DMSClient to provide a consistent
    interface with other deployment adapters. It accepts the same NIMConfig
    fields used throughout the flywheel pipeline (see config/config.yaml
    and config/embedding_config_example.json for examples).
    """

    def __init__(self, config: dict[str, Any]):
        """Initialize the NMP adapter.

        Args:
            config: Configuration dictionary matching NIMConfig fields
                    (model_name, model_type, gpus, tag, customization_enabled,
                     base_model_for_image, etc.)
        """
        self.config = NIMConfig(**config)
        self._dms_client: DMSClient | None = None

    @property
    def platform(self) -> DeploymentPlatform:
        """Return the platform identifier."""
        return DeploymentPlatform.NMP

    def _get_dms_client(self) -> DMSClient:
        """Get or create the DMSClient instance.

        The NMP SDK is imported lazily here (and guarded) so this adapter module
        loads on any image — including a DEFT build without ``nemo-platform`` —
        and raises a clear error only if NMP deployment is actually attempted
        without the NMP backend installed.

        Returns:
            DMSClient instance
        """
        if self._dms_client is None:
            try:
                from src.backends.nmp.dms_client import DMSClient
            except ImportError as e:
                raise RuntimeError(
                    "NMP deployment requires the NMP backend (nemo-platform), which is "
                    "not installed in this image. Build/run with the NMP backend "
                    "(`uv sync --extra nmp` / `docker build --build-arg BACKEND=nmp`)."
                ) from e
            self._dms_client = DMSClient(nim=self.config)
        return self._dms_client

    def deploy(
        self,
        datastore_url: str,
        model_name: str,
        model_version: str,
        deployment_name: str,
        config: dict[str, Any] | None = None,
        progress_callback: Callable[[str, float], None] | None = None,
    ) -> DeploymentResult:
        """Deploy model to NMP using DMSClient.

        NMP handles model downloading internally during deployment,
        so this method directly triggers deployment with the model info.

        Args:
            datastore_url: URL to the datastore
            model_name: Full name of the model
            model_version: Version/revision of the model
            deployment_name: Name for this deployment
            config: Additional configuration overrides
            progress_callback: Optional callback for progress updates

        Returns:
            DeploymentResult with status and endpoint
        """
        dms_client = self._get_dms_client()

        try:
            if progress_callback:
                progress_callback("Creating deployment configuration", 0.1)

            # Create deployment config for customized model if needed
            deployment_config = None
            if self.config.base_model_for_image:
                deployment_config = dms_client.create_deployment_config(
                    base_model_for_image=self.config.base_model_for_image
                )

            if progress_callback:
                progress_callback("Deploying model to NMP", 0.3)

            # Deploy model
            if not dms_client.is_deployed():
                dms_client.deploy_model(config=deployment_config)
            else:
                logger.info(f"Model {self.config.model_name} is already deployed")

            if progress_callback:
                progress_callback("Waiting for deployment to be ready", 0.5)

            # NMP 26.03 ModelDeploymentStatus is uppercase. DMSClient now returns the
            # legacy lowercase values via translation, but accept uppercase too just in case.
            status_progress_map = {
                "unknown": 0.5,
                "created": 0.55,
                "pending": 0.6,
                "deploying": 0.7,
                "running": 0.8,
                "ready": 0.95,
                "deployed": 0.95,
                "deleting": 0.95,
                "deleted": 1.0,
                "failed": 0.5,
            }

            # Wait for deployment with progress updates
            def status_callback(status: dict):
                if progress_callback:
                    status_str = status.get("status", "unknown")

                    # Try to get explicit progress from status dict
                    progress = status.get("progress") or status.get("percent_complete")
                    if progress is not None:
                        # Normalize to 0.5-0.95 range (deployment phase)
                        if isinstance(progress, int | float):
                            progress = 0.5 + (float(progress) / 100.0) * 0.45
                    else:
                        # Map status string to progress value
                        progress = status_progress_map.get(status_str.lower(), 0.7)

                    progress_callback(f"Deployment status: {status_str}", progress)

            dms_client.wait_for_deployment(
                progress_callback=status_callback,
            )

            if progress_callback:
                progress_callback("Deployment ready", 1.0)

            # Get inference endpoint based on model type
            endpoint_url = self.get_inference_endpoint()
            nmp_config = settings.system_config.nmp_config

            return DeploymentResult(
                deployment_id=self.config.nmp_model_name(),
                platform=self.platform,
                status=DeploymentStatus.READY,
                endpoint_url=endpoint_url,
                model_name=model_name,
                metadata={
                    "nmp_namespace": nmp_config.nmp_namespace,
                    "deployment_config": deployment_config,
                    "datastore_url": datastore_url,
                    "model_version": model_version,
                },
            )

        except Exception as e:
            logger.error(f"NMP deployment failed: {e}")
            return DeploymentResult(
                deployment_id=self.config.nmp_model_name(),
                platform=self.platform,
                status=DeploymentStatus.FAILED,
                error=str(e),
                model_name=model_name,
            )

    def get_status(self, deployment_id: str) -> DeploymentResult:
        """Get current deployment status.

        Args:
            deployment_id: NMP deployment name

        Returns:
            DeploymentResult with current status
        """
        dms_client = self._get_dms_client()

        try:
            if not dms_client.does_deployment_exist():
                return DeploymentResult(
                    deployment_id=deployment_id,
                    platform=self.platform,
                    status=DeploymentStatus.DELETED,
                    error="Deployment not found",
                )

            dms_status = dms_client.get_deployment_status()

            # Map DMS status (legacy lowercase, translated by DMSClient) to DeploymentStatus.
            status_map = {
                "ready": DeploymentStatus.READY,
                "deployed": DeploymentStatus.READY,
                "deploying": DeploymentStatus.DEPLOYING,
                "pending": DeploymentStatus.PENDING,
                "created": DeploymentStatus.PENDING,
                "unknown": DeploymentStatus.PENDING,
                "failed": DeploymentStatus.FAILED,
                "deleting": DeploymentStatus.DELETED,
                "deleted": DeploymentStatus.DELETED,
            }
            status = status_map.get(dms_status.lower(), DeploymentStatus.PENDING)

            # Get endpoint URL based on model type (only if ready)
            endpoint_url = (
                self.get_inference_endpoint() if status == DeploymentStatus.READY else None
            )

            return DeploymentResult(
                deployment_id=deployment_id,
                platform=self.platform,
                status=status,
                endpoint_url=endpoint_url,
                metadata={"dms_status": dms_status},
            )

        except Exception as e:
            logger.error(f"Failed to get NMP deployment status: {e}")
            return DeploymentResult(
                deployment_id=deployment_id,
                platform=self.platform,
                status=DeploymentStatus.FAILED,
                error=str(e),
            )

    def undeploy(
        self,
        deployment_id: str,
        progress_callback: Callable[[str, float], None] | None = None,
    ) -> DeploymentResult:
        """Remove/undeploy a deployment from NMP.

        Args:
            deployment_id: NMP deployment name
            progress_callback: Optional callback for progress updates (message, progress 0-1)

        Returns:
            DeploymentResult confirming deletion
        """
        dms_client = self._get_dms_client()

        try:
            if progress_callback:
                progress_callback("Shutting down NMP deployment", 0.2)

            result = dms_client.shutdown_deployment()
            logger.info(f"Undeployed {deployment_id} from NMP")

            if progress_callback:
                progress_callback("Undeploy complete", 1.0)

            return DeploymentResult(
                deployment_id=deployment_id,
                platform=self.platform,
                status=DeploymentStatus.DELETED,
                metadata=result,
            )

        except Exception as e:
            logger.error(f"Failed to undeploy from NMP: {e}")
            return DeploymentResult(
                deployment_id=deployment_id,
                platform=self.platform,
                status=DeploymentStatus.FAILED,
                error=str(e),
            )

    def get_inference_endpoint(self, deployment_id: str | None = None) -> str:
        """Get NMP inference endpoint URL based on model type.

        Args:
            deployment_id: Not used for NMP (endpoint is the same for all deployments).
                           Kept for interface compatibility with base class.

        Returns:
            Full URL for making inference requests
        """
        _ = deployment_id  # Unused - NMP uses same endpoint regardless of deployment
        nmp_config = settings.system_config.nmp_config

        # Endpoint depends on model type
        if self.config.model_type == "embedding":
            return f"{nmp_config.nim_base_url}/v1/embeddings"
        elif self.config.model_type == "llm":
            return f"{nmp_config.nim_base_url}/v1/chat/completions"
        else:
            # Default to embeddings for unknown model types
            return f"{nmp_config.nim_base_url}/v1/embeddings"

    def validate_config(self, config: dict[str, Any]) -> bool:
        """Validate NMP configuration.

        Args:
            config: Configuration dictionary

        Returns:
            True if valid

        Raises:
            ValueError: If configuration is invalid
        """
        if "model_name" not in config:
            raise ValueError("model_name is required for NMP adapter")
        return True
