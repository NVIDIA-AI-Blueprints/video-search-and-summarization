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

"""Configuration schema for Custom NIM build-and-push deployments.

Configuration is loaded from config/custom_nim_deployment_config.yaml
and can be overridden via the platform_config in API requests.
Docker credentials and HF token can also be overridden via environment variables.

Uses crane (Google's go-containerregistry) for Kubernetes Jobs — truly rootless,
works on restricted OpenShift without Docker daemon or elevated privileges.
"""

import os
import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator

# Strict regexes for values that are interpolated into the crane shell script.
# They reject shell metacharacters (;|&$`!(){}'"<>\) while allowing characters
# that appear in Docker image references, OCI digests, and model names.
_SAFE_IMAGE_RE = re.compile(r"^[a-zA-Z0-9._:/@-]+$")
_SAFE_NAME_RE = re.compile(r"^[a-zA-Z0-9._/-]*$")

CONFIG_DIR = Path(__file__).parent.parent.parent.parent.parent.parent / "config"
CUSTOM_NIM_CONFIG_PATH = CONFIG_DIR / "custom_nim_deployment_config.yaml"


def load_yaml_config() -> dict[str, Any]:
    """Load configuration from YAML file.

    Returns:
        Dictionary with configuration values
    """
    config_path = os.environ.get("CUSTOM_NIM_CONFIG_PATH", str(CUSTOM_NIM_CONFIG_PATH))

    if not os.path.exists(config_path):
        return {}

    with open(config_path) as f:
        return yaml.safe_load(f) or {}


_yaml_config = load_yaml_config()


class CraneConfig(BaseModel):
    """Configuration for Crane-based image builds on Kubernetes/OpenShift.

    Uses Google's crane tool (go-containerregistry) to build and push Docker
    images via pure HTTP registry API calls.  Truly rootless — no Docker daemon,
    no user namespaces, no root access required.  Works on restricted OpenShift.
    """

    image: str = Field(
        default=_yaml_config.get("crane", {}).get(
            "image", "gcr.io/go-containerregistry/crane:debug"
        ),
        description="Crane container image",
    )
    namespace: str = Field(
        default=_yaml_config.get("crane", {}).get("namespace", ""),
        description="K8s namespace for Crane Jobs (empty = auto-detect from in-cluster config)",
    )
    service_account: str = Field(
        default=_yaml_config.get("crane", {}).get("service_account", "dfw-crane-builder"),
        description="ServiceAccount for Crane Job pods",
    )
    pvc_name: str = Field(
        default=_yaml_config.get("crane", {}).get("pvc_name", "crane-build-context"),
        description="PVC shared between the celery worker and Crane pods",
    )
    build_context_dir: str = Field(
        default=_yaml_config.get("crane", {}).get("build_context_dir", "/crane-workspace"),
        description="Mount path of the PVC inside the celery worker pod",
    )
    registry_secret: str = Field(
        default=os.environ.get(
            "CUSTOM_NIM_CRANE_REGISTRY_SECRET",
            _yaml_config.get("crane", {}).get("registry_secret", ""),
        ),
        description=(
            "Existing K8s secret (type: kubernetes.io/dockerconfigjson) with registry "
            "credentials. If empty, a temporary secret is created from docker_credentials."
        ),
    )
    job_timeout_seconds: int = Field(
        default=_yaml_config.get("crane", {}).get("job_timeout_seconds", 3600),
        description="activeDeadlineSeconds for the Crane Job",
    )
    job_ttl_seconds_after_finished: int = Field(
        default=_yaml_config.get("crane", {}).get("job_ttl_seconds_after_finished", 300),
        description="Auto-cleanup finished Crane Jobs after this many seconds",
    )
    poll_interval_seconds: int = Field(
        default=_yaml_config.get("crane", {}).get("poll_interval_seconds", 10),
        description="How often to poll the Crane Job status",
    )

    @field_validator("image")
    @classmethod
    def _validate_image(cls, v: str) -> str:
        if v and not _SAFE_IMAGE_RE.match(v):
            raise ValueError(
                f"Unsafe characters in crane image: {v!r}. Only [a-zA-Z0-9._:/@-] are allowed."
            )
        return v

    model_config = {"extra": "allow"}


class CustomNIMConfig(BaseModel):
    """Configuration for building and pushing customized NIM Docker images.

    Supports pushing to NGC (nvcr.io) or JFrog (urm.nvidia.com) registries.
    Configuration is loaded from config/custom_nim_deployment_config.yaml
    and can be overridden via the platform_config in API requests.
    """

    # --- Crane ---
    crane: CraneConfig = Field(
        default_factory=CraneConfig,
        description="Crane-specific configuration (used when build_engine is 'crane')",
    )

    # --- Registry Target ---
    registry_type: str = Field(
        default=_yaml_config.get("registry", {}).get("type", "ngc"),
        description="Registry type: 'ngc' or 'jfrog'",
    )
    target_registry: str = Field(
        default=_yaml_config.get("registry", {}).get("target_registry", "nvcr.io"),
        description="Target registry hostname (e.g., 'nvcr.io' or 'urm.nvidia.com')",
    )
    target_repo: str = Field(
        default=_yaml_config.get("registry", {}).get("target_repo", "nvstaging/blueprint"),
        description="Repository path on the registry",
    )
    target_image_name: str = Field(
        default=_yaml_config.get("registry", {}).get(
            "target_image_name", "dfw-llama-3.2-nv-embedqa-1b-customized"
        ),
        description="Image name within the target repo",
    )
    target_tag: str = Field(
        default=_yaml_config.get("registry", {}).get("target_tag", "0.0.1"),
        description="Image tag for the customized build",
    )

    # --- NGC / Docker Credentials (default for base image pull + NGC push) ---
    # Resolution order: CUSTOM_NIM_DOCKER_* env var → YAML → NGC defaults
    docker_username: str = Field(
        default=os.environ.get(
            "CUSTOM_NIM_DOCKER_USERNAME",
            _yaml_config.get("docker_credentials", {}).get("username", ""),
        )
        or "$oauthtoken",
        description="NGC registry username (defaults to $oauthtoken)",
    )
    docker_password: str = Field(
        default=os.environ.get(
            "CUSTOM_NIM_DOCKER_PASSWORD",
            _yaml_config.get("docker_credentials", {}).get("password", ""),
        )
        or os.environ.get("NGC_API_KEY", ""),
        description="NGC registry password (defaults to NGC_API_KEY env var)",
    )

    # --- JFrog Credentials (only needed when pushing to JFrog registries) ---
    # Resolution order: CUSTOM_NIM_JFROG_* env var → YAML → "" (empty = not used)
    jfrog_username: str = Field(
        default=os.environ.get(
            "CUSTOM_NIM_JFROG_USERNAME",
            _yaml_config.get("jfrog_credentials", {}).get("username", ""),
        ),
        description="JFrog Artifactory username (only needed when target is JFrog)",
    )
    jfrog_password: str = Field(
        default=os.environ.get(
            "CUSTOM_NIM_JFROG_PASSWORD",
            _yaml_config.get("jfrog_credentials", {}).get("password", ""),
        ),
        description="JFrog Artifactory password / API token",
    )

    # --- Base NIM Image ---
    base_nim_image: str = Field(
        default=_yaml_config.get("base_image", {}).get(
            "image",
            "urm.nvidia.com/nvidia-nvcr-docker-remote/nim/nvidia/llama-3.2-nv-embedqa-1b-v2:1.9.0",
        ),
        description="Base NIM Docker image to customize (full reference with tag)",
    )

    # --- DataStore ---
    datastore_endpoint: str = Field(
        default=_yaml_config.get("datastore", {}).get("endpoint", "http://data-store.test"),
        description="DataStore base URL (no path). /v1/hf is appended internally.",
    )
    datastore_token: str = Field(
        default=os.environ.get(
            "HF_TOKEN",
            _yaml_config.get("datastore", {}).get("token", ""),
        ),
        description="HF-compatible auth token for the data store (uses HF_TOKEN env var)",
    )

    # --- NIM Runtime ---
    nim_model_name: str = Field(
        default=_yaml_config.get("nim_runtime", {}).get(
            "model_name", "nvidia/llama-3.2-nv-embedqa-1b-v2"
        ),
        description="Model name NIM will serve (baked into image as NIM_MODEL_NAME env var)",
    )

    @field_validator(
        "target_registry", "target_repo", "target_image_name", "target_tag", "base_nim_image"
    )
    @classmethod
    def _validate_image_fields(cls, v: str) -> str:
        """Reject shell metacharacters in fields interpolated into the crane build script."""
        if v and not _SAFE_IMAGE_RE.match(v):
            raise ValueError(
                f"Unsafe characters in image field: {v!r}. Only [a-zA-Z0-9._:/@-] are allowed."
            )
        return v

    @field_validator("nim_model_name")
    @classmethod
    def _validate_nim_model_name(cls, v: str) -> str:
        if v and not _SAFE_NAME_RE.match(v):
            raise ValueError(
                f"Unsafe characters in nim_model_name: {v!r}. Only [a-zA-Z0-9._/-] are allowed."
            )
        return v

    # --- Build Options ---
    cleanup_after_build: bool = Field(
        default=_yaml_config.get("build_options", {}).get("cleanup_after_build", True),
        description="Remove local model-store directory after a successful build",
    )
    skip_download: bool = Field(
        default=_yaml_config.get("build_options", {}).get("skip_download", False),
        description="Skip the weight download step",
    )
    download_max_retries: int = Field(
        default=_yaml_config.get("build_options", {}).get("download_max_retries", 3),
        description="Max download retry attempts",
    )
    download_retry_delay: int = Field(
        default=_yaml_config.get("build_options", {}).get("download_retry_delay", 10),
        description="Delay between download retries in seconds",
    )

    # --- Timeouts ---
    download_timeout_seconds: int = Field(
        default=_yaml_config.get("timeouts", {}).get("download", 3600),
        description="Timeout for the model download step in seconds",
    )

    @property
    def full_target_image(self) -> str:
        """Build the full target image reference (registry/repo/name:tag)."""
        return (
            f"{self.target_registry}/{self.target_repo}/{self.target_image_name}:{self.target_tag}"
        )

    @classmethod
    def from_yaml(cls, overrides: dict[str, Any] | None = None) -> "CustomNIMConfig":
        """Create config from YAML file with optional overrides.

        Args:
            overrides: Dictionary of values to override from YAML

        Returns:
            CustomNIMConfig instance
        """
        config_dict: dict[str, Any] = {}

        yaml_cfg = load_yaml_config()

        if yaml_cfg:
            crane_yaml = yaml_cfg.get("crane", {})
            if crane_yaml:
                # Env var takes precedence over YAML for registry_secret
                env_secret = os.environ.get("CUSTOM_NIM_CRANE_REGISTRY_SECRET")
                if env_secret:
                    crane_yaml = {**crane_yaml, "registry_secret": env_secret}
                config_dict["crane"] = CraneConfig(**crane_yaml)

            registry = yaml_cfg.get("registry", {})
            config_dict["registry_type"] = registry.get("type")
            config_dict["target_registry"] = registry.get("target_registry")
            config_dict["target_repo"] = registry.get("target_repo")
            config_dict["target_image_name"] = registry.get("target_image_name")
            config_dict["target_tag"] = registry.get("target_tag")

            docker_creds = yaml_cfg.get("docker_credentials", {})
            config_dict["docker_username"] = (
                os.environ.get("CUSTOM_NIM_DOCKER_USERNAME", docker_creds.get("username"))
                or "$oauthtoken"
            )
            config_dict["docker_password"] = os.environ.get(
                "CUSTOM_NIM_DOCKER_PASSWORD", docker_creds.get("password")
            ) or os.environ.get("NGC_API_KEY", "")

            jfrog_creds = yaml_cfg.get("jfrog_credentials", {})
            config_dict["jfrog_username"] = os.environ.get(
                "CUSTOM_NIM_JFROG_USERNAME", jfrog_creds.get("username", "")
            )
            config_dict["jfrog_password"] = os.environ.get(
                "CUSTOM_NIM_JFROG_PASSWORD", jfrog_creds.get("password", "")
            )

            base_image = yaml_cfg.get("base_image", {})
            config_dict["base_nim_image"] = base_image.get("image")

            datastore = yaml_cfg.get("datastore", {})
            config_dict["datastore_endpoint"] = datastore.get("endpoint")
            config_dict["datastore_token"] = os.environ.get("HF_TOKEN", datastore.get("token"))

            nim_runtime = yaml_cfg.get("nim_runtime", {})
            config_dict["nim_model_name"] = nim_runtime.get("model_name")

            build_options = yaml_cfg.get("build_options", {})
            config_dict["cleanup_after_build"] = build_options.get("cleanup_after_build")
            config_dict["skip_download"] = build_options.get("skip_download")
            config_dict["download_max_retries"] = build_options.get("download_max_retries")
            config_dict["download_retry_delay"] = build_options.get("download_retry_delay")

            timeouts = yaml_cfg.get("timeouts", {})
            config_dict["download_timeout_seconds"] = timeouts.get("download")

        config_dict = {k: v for k, v in config_dict.items() if v is not None}

        if overrides:
            _validate_overrides(overrides)
            config_dict.update(overrides)

        return cls(**config_dict)

    model_config = {"extra": "allow"}


_SHELL_INTERPOLATED_FIELDS = frozenset(
    {
        "target_registry",
        "target_repo",
        "target_image_name",
        "target_tag",
        "base_nim_image",
        "nim_model_name",
    }
)


def _validate_overrides(overrides: dict[str, Any]) -> None:
    """Pre-screen raw override dicts for shell-unsafe characters.

    Pydantic validators catch these too during model construction, but this
    provides an earlier, clearer error when overrides come from untrusted API
    input.
    """
    for key, value in overrides.items():
        if key in _SHELL_INTERPOLATED_FIELDS and isinstance(value, str):
            if value and not _SAFE_IMAGE_RE.match(value):
                raise ValueError(
                    f"Unsafe characters in override {key!r}: {value!r}. "
                    "Only [a-zA-Z0-9._:/@-] are allowed."
                )
        if key == "crane" and isinstance(value, dict):
            img = value.get("image")
            if isinstance(img, str) and img and not _SAFE_IMAGE_RE.match(img):
                raise ValueError(
                    f"Unsafe characters in crane.image override: {img!r}. "
                    "Only [a-zA-Z0-9._:/@-] are allowed."
                )


def get_custom_nim_config(overrides: dict[str, Any] | None = None) -> CustomNIMConfig:
    """Get Custom NIM configuration with optional overrides.

    This is the recommended way to get configuration, as it properly
    loads from YAML and applies any overrides.

    Args:
        overrides: Dictionary of values to override from YAML/defaults

    Returns:
        CustomNIMConfig instance
    """
    return CustomNIMConfig.from_yaml(overrides)
