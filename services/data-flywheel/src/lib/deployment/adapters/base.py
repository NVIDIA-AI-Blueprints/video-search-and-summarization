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

"""Base deployment adapter interface for multi-platform model deployment."""

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class DeploymentPlatform(str, Enum):
    """Supported deployment platforms.

    Note: Not all platforms have adapter implementations yet.
    Use DeploymentAdapterFactory.get_available_platforms() to get
    the list of platforms with working adapters.
    """

    DATAROBOT = "datarobot"
    NMP = "nmp"
    CUSTOM_NIM = "custom_nim"
    # DYNAMO is a placeholder for future NVIDIA Dynamo integration.
    # No adapter implementation exists yet - requests will return a clear error.
    DYNAMO = "dynamo"


class DeploymentStatus(str, Enum):
    """Deployment status enumeration."""

    PENDING = "pending"
    DOWNLOADING = "downloading"
    DEPLOYING = "deploying"
    READY = "ready"
    FAILED = "failed"
    DELETING = "deleting"  # Undeploy in progress
    DELETED = "deleted"


@dataclass
class DeploymentResult:
    """Result of a deployment operation.

    Attributes:
        deployment_id: Platform-specific deployment identifier
        platform: The deployment platform used
        status: Current deployment status
        endpoint_url: Inference endpoint URL (if available)
        model_name: Name of the deployed model
        error: Error message if deployment failed
        metadata: Additional platform-specific metadata
    """

    deployment_id: str
    platform: DeploymentPlatform
    status: DeploymentStatus
    endpoint_url: str | None = None
    model_name: str | None = None
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class BaseDeploymentAdapter(ABC):
    """Abstract base class for deployment adapters.

    This class defines the interface that all deployment adapters must implement.
    Each adapter handles deploying models to a specific platform (DataRobot, NMP, Dynamo, etc.).

    The adapter pattern allows DFW to support multiple deployment targets through
    a unified interface while encapsulating platform-specific logic in each adapter.
    """

    @property
    @abstractmethod
    def platform(self) -> DeploymentPlatform:
        """Return the platform identifier for this adapter.

        Returns:
            The DeploymentPlatform enum value for this adapter
        """
        pass

    @abstractmethod
    def deploy(
        self,
        datastore_url: str,
        model_name: str,
        model_version: str,
        deployment_name: str,
        config: dict[str, Any] | None = None,
        progress_callback: Callable[[str, float], None] | None = None,
    ) -> DeploymentResult:
        """Deploy model to the platform.

        This method handles downloading the model from the datastore and
        deploying it to the target inference platform.

        Args:
            datastore_url: URL to the datastore (e.g., http://nemo-data-store.nemo.svc:3000/v1/hf)
            model_name: Full name of the model in datastore (e.g., namespace/model-name)
            model_version: Version/revision of the model to download
            deployment_name: Name for this deployment
            config: Platform-specific configuration overrides
            progress_callback: Optional callback for progress updates (message, progress 0-1)

        Returns:
            DeploymentResult with status and endpoint URL

        Raises:
            ValueError: If invalid configuration or model not found
            RuntimeError: If deployment fails
        """
        pass

    @abstractmethod
    def get_status(self, deployment_id: str) -> DeploymentResult:
        """Get current deployment status.

        Args:
            deployment_id: Platform-specific deployment identifier

        Returns:
            DeploymentResult with current status

        Raises:
            ValueError: If deployment not found
        """
        pass

    @abstractmethod
    def undeploy(
        self,
        deployment_id: str,
        progress_callback: Callable[[str, float], None] | None = None,
    ) -> DeploymentResult:
        """Remove/undeploy a deployment.

        Args:
            deployment_id: Platform-specific deployment identifier
            progress_callback: Optional callback for progress updates (message, progress 0-1)

        Returns:
            DeploymentResult confirming deletion

        Raises:
            ValueError: If deployment not found
            RuntimeError: If undeploy fails
        """
        pass

    @abstractmethod
    def get_inference_endpoint(self, deployment_id: str) -> str:
        """Get the inference endpoint URL for a deployment.

        Args:
            deployment_id: Platform-specific deployment identifier

        Returns:
            Full URL for making inference requests

        Raises:
            ValueError: If deployment not found or not ready
        """
        pass

    def validate_config(self, config: dict[str, Any]) -> bool:
        """Validate platform-specific configuration.

        Override this method to add platform-specific validation.

        Args:
            config: Platform-specific configuration dictionary

        Returns:
            True if configuration is valid

        Raises:
            ValueError: If configuration is invalid
        """
        return True
