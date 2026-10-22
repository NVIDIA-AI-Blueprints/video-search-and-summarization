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

"""Configuration schema for DataRobot deployments.

Configuration is loaded from config/datarobot_deployment_config.yaml
and can be overridden via the platform_config in API requests.
"""

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

# Path to the YAML config file
CONFIG_DIR = Path(__file__).parent.parent.parent.parent.parent.parent / "config"
DATAROBOT_CONFIG_PATH = CONFIG_DIR / "datarobot_deployment_config.yaml"


def load_yaml_config() -> dict[str, Any]:
    """Load configuration from YAML file.

    Returns:
        Dictionary with configuration values
    """
    config_path = os.environ.get("DATAROBOT_CONFIG_PATH", str(DATAROBOT_CONFIG_PATH))

    if not os.path.exists(config_path):
        # Return empty dict if config file doesn't exist - will use Pydantic defaults
        return {}

    with open(config_path) as f:
        return yaml.safe_load(f) or {}


# Load default config from YAML
_yaml_config = load_yaml_config()


class DataRobotConfig(BaseModel):
    """Configuration for DataRobot deployments.

    This configuration maps to the Kubernetes resources needed
    to deploy a model to DataRobot's LRS (Live Runtime Service).
    The LRS name is resolved at runtime from the DataRobot Deployment ID
    via Kubernetes label lookup (datarobot-deployment-id label).

    Configuration is loaded from config/datarobot_deployment_config.yaml
    and can be overridden via the platform_config in API requests.
    """

    # DataStore Configuration
    datastore_endpoint: str = Field(
        default=_yaml_config.get("datastore", {}).get(
            "endpoint", "https://dataflywheel-data-store.prd.astra.nvidia.com/v1/hf"
        ),
        description="DataStore HuggingFace-compatible endpoint",
    )
    datastore_token_secret: str = Field(
        default=_yaml_config.get("datastore", {}).get("token_secret", "datastore-hf-token"),
        description="Kubernetes secret name containing HF_TOKEN for DataStore authentication",
    )
    puller_image: str = Field(
        default=_yaml_config.get("datastore", {}).get(
            "puller_image", "urm.nvidia.com/nvidia-nvcr-docker-remote/nvidia/pytorch:24.01-py3"
        ),
        description="Model puller image with pip for installing huggingface_hub",
    )

    # Kubernetes Configuration
    k8s_namespace: str = Field(
        default=_yaml_config.get("deployment", {}).get("namespace", "dr-app"),
        description="Kubernetes namespace where NIM/LRS is deployed",
    )
    deployment_id: str = Field(
        ...,
        description="DataRobot Deployment ID (the user-facing platform ID, not the LRS ID) - REQUIRED",
    )

    # Storage Configuration
    storage_class: str = Field(
        default=_yaml_config.get("storage", {}).get("class", "nfs41pnfs-sc"),
        description="Kubernetes storage class for PVC",
    )
    storage_size: str = Field(
        default=_yaml_config.get("storage", {}).get("size", "100Gi"),
        description="PVC size (should be larger than model size)",
    )

    # Image Pull Configuration
    image_pull_secret: str = Field(
        default=_yaml_config.get("image_pull", {}).get("secret", "ngc-docker-pullsecret"),
        description="Kubernetes secret for pulling images",
    )

    # DataRobot API Configuration
    datarobot_api_url: str = Field(
        default=_yaml_config.get("datarobot_api", {}).get(
            "url", "https://datarobot.prd.astra.nvidia.com"
        ),
        description="DataRobot API base URL",
    )

    # Security Context (for ASTRA/OpenShift)
    # Note: dr-app namespace uses UID range [1000990000, 1000999999]
    run_as_user: int = Field(
        default=_yaml_config.get("security_context", {}).get("run_as_user", 1000990000),
        description="UID for pod security context",
    )
    run_as_group: int = Field(
        default=_yaml_config.get("security_context", {}).get("run_as_group", 1000990000),
        description="GID for pod security context",
    )
    fs_group: int = Field(
        default=_yaml_config.get("security_context", {}).get("fs_group", 1000990000),
        description="FSGroup for pod security context",
    )

    # Timeout Configuration
    pvc_bind_timeout_seconds: int = Field(
        default=_yaml_config.get("timeouts", {}).get("pvc_bind", 300),
        description="Timeout for PVC to bind in seconds (5 min default)",
    )
    download_timeout_seconds: int = Field(
        default=_yaml_config.get("timeouts", {}).get("download", 3600),
        description="Timeout for model download job in seconds",
    )
    rollout_timeout_seconds: int = Field(
        default=_yaml_config.get("timeouts", {}).get("rollout", 900),
        description="Timeout for deployment rollout in seconds",
    )
    rollout_max_pod_retries: int = Field(
        default=_yaml_config.get("timeouts", {}).get("rollout_max_pod_retries", 3),
        description=(
            "Max pod restart retries during rollout. If a pod fails to become "
            "healthy within the rollout timeout, it is deleted and a new pod is "
            "created from the same deployment spec. Handles non-deterministic "
            "NIM/Triton startup failures."
        ),
    )
    pod_reschedule_wait_seconds: int = Field(
        default=_yaml_config.get("timeouts", {}).get("pod_reschedule_wait", 30),
        description="Seconds to wait for a new pod to schedule after deleting a stuck one",
    )

    # Model type for endpoint selection
    model_type: str = Field(
        default=_yaml_config.get("model", {}).get("type", "embedding"),
        description="Model type: 'embedding', 'chat', 'reranking', 'vision'",
    )

    def get_inference_endpoint_path(self) -> str:
        """Get the inference endpoint path based on model type.

        Returns:
            The API path for the model type
        """
        endpoint_paths = {
            "embedding": "/directAccess/nim/v1/embeddings",
            "chat": "/chat/completions",
            "reranking": "/directAccess/nim/v1/ranking",
            "vision": "/directAccess/nim/v1/infer",
        }
        return endpoint_paths.get(self.model_type, "/directAccess/nim/v1/embeddings")

    @classmethod
    def from_yaml(cls, overrides: dict[str, Any] | None = None) -> "DataRobotConfig":
        """Create config from YAML file with optional overrides.

        Args:
            overrides: Dictionary of values to override from YAML

        Returns:
            DataRobotConfig instance
        """
        config_dict = {}

        # Flatten YAML structure to match Pydantic model
        yaml_cfg = load_yaml_config()

        if yaml_cfg:
            # Map YAML structure to flat config
            datastore = yaml_cfg.get("datastore", {})
            config_dict["datastore_endpoint"] = datastore.get("endpoint")
            config_dict["datastore_token_secret"] = datastore.get("token_secret")
            config_dict["puller_image"] = datastore.get("puller_image")

            deployment = yaml_cfg.get("deployment", {})
            config_dict["k8s_namespace"] = deployment.get("namespace")
            if "id" in deployment:
                config_dict["deployment_id"] = deployment.get("id")

            storage = yaml_cfg.get("storage", {})
            config_dict["storage_class"] = storage.get("class")
            config_dict["storage_size"] = storage.get("size")

            image_pull = yaml_cfg.get("image_pull", {})
            config_dict["image_pull_secret"] = image_pull.get("secret")

            datarobot_api = yaml_cfg.get("datarobot_api", {})
            config_dict["datarobot_api_url"] = datarobot_api.get("url")

            security = yaml_cfg.get("security_context", {})
            config_dict["run_as_user"] = security.get("run_as_user")
            config_dict["run_as_group"] = security.get("run_as_group")
            config_dict["fs_group"] = security.get("fs_group")

            timeouts = yaml_cfg.get("timeouts", {})
            config_dict["pvc_bind_timeout_seconds"] = timeouts.get("pvc_bind")
            config_dict["download_timeout_seconds"] = timeouts.get("download")
            config_dict["rollout_timeout_seconds"] = timeouts.get("rollout")
            config_dict["rollout_max_pod_retries"] = timeouts.get("rollout_max_pod_retries")
            config_dict["pod_reschedule_wait_seconds"] = timeouts.get("pod_reschedule_wait")

            model = yaml_cfg.get("model", {})
            config_dict["model_type"] = model.get("type")

        # Remove None values
        config_dict = {k: v for k, v in config_dict.items() if v is not None}

        # Apply overrides
        if overrides:
            config_dict.update(overrides)

        return cls(**config_dict)

    model_config = {"extra": "allow"}


def get_datarobot_config(overrides: dict[str, Any] | None = None) -> DataRobotConfig:
    """Get DataRobot configuration with optional overrides.

    This is the recommended way to get configuration, as it properly
    loads from YAML and applies any overrides.

    Args:
        overrides: Dictionary of values to override from YAML/defaults

    Returns:
        DataRobotConfig instance
    """
    return DataRobotConfig.from_yaml(overrides)
