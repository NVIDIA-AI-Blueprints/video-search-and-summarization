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

"""Custom NIM build-and-push deployment adapter.

Downloads a customized model from the datastore, bakes it into a NIM Docker
image using crane (Google's go-containerregistry), and pushes the image to a
configurable container registry (NGC or JFrog).

Crane operates via pure HTTP registry API calls — truly rootless, no Docker
daemon, no user namespaces, no special SCCs required.  Works on restricted
OpenShift clusters.

This adapter is build-and-push only — it does not manage a running deployment.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time
from collections.abc import Callable
from typing import Any

from src.lib.deployment.adapters.base import (
    BaseDeploymentAdapter,
    DeploymentPlatform,
    DeploymentResult,
    DeploymentStatus,
)
from src.lib.deployment.adapters.custom_nim.config import get_custom_nim_config
from src.lib.deployment.adapters.factory import DeploymentAdapterFactory
from src.log_utils import setup_logging

logger = setup_logging("data_flywheel.deployment.custom_nim")

HF_DATASTORE_PATH = "/v1/hf"


def _hf_endpoint_from_base(base_url: str) -> str:
    """Return HuggingFace-compatible endpoint URL for model download.

    Config and API use the datastore base URL without path.  This helper
    appends /v1/hf — same pattern as the DataRobot adapter.
    """
    url = (base_url or "").rstrip("/")
    if not url:
        return url
    if url.endswith("/v1/hf") or url.endswith("/v1/hf/"):
        return url.rstrip("/")
    return f"{url}{HF_DATASTORE_PATH}"


@DeploymentAdapterFactory.register(DeploymentPlatform.CUSTOM_NIM)
class CustomNIMAdapter(BaseDeploymentAdapter):
    """Adapter that builds a customized NIM image and pushes it to a container registry.

    Uses crane (Google's go-containerregistry) to build via pure HTTP API calls:
        1. Download model weights to a shared PVC
        2. Create a temporary registry-credential Secret (or use an existing one)
        3. Launch a crane Job that appends the model layer and pushes
        4. Poll the Job until completion
        5. Cleanup (Job, Secret, build directory on PVC)
    """

    def __init__(self, config: dict[str, Any]):
        self.config = get_custom_nim_config(config)

    @property
    def platform(self) -> DeploymentPlatform:
        return DeploymentPlatform.CUSTOM_NIM

    # ------------------------------------------------------------------
    # deploy() — entry point
    # ------------------------------------------------------------------

    def deploy(
        self,
        datastore_url: str,
        model_name: str,
        model_version: str,
        deployment_name: str,
        config: dict[str, Any] | None = None,
        progress_callback: Callable[[str, float], None] | None = None,
    ) -> DeploymentResult:
        """Build a customized NIM image and push it to the target registry.

        Args:
            datastore_url: Base URL of the datastore (may be overridden by config)
            model_name: Model repository ID on the data store (e.g., "dfwbp/my-model")
            model_version: Model revision / version to download
            deployment_name: Name for this build (used as deployment_id)
            config: Additional configuration overrides
            progress_callback: Optional callback for progress updates (message, progress 0-1)

        Returns:
            DeploymentResult with the full image tag in metadata
        """
        if config:
            self.config = get_custom_nim_config(config)

        return self._deploy_with_crane(
            datastore_url, model_name, model_version, deployment_name, progress_callback
        )

    def _deploy_with_crane(
        self,
        datastore_url: str,
        model_name: str,
        model_version: str,
        deployment_name: str,
        progress_callback: Callable[[str, float], None] | None = None,
    ) -> DeploymentResult:
        from src.lib.deployment.adapters.custom_nim.crane import CraneJobManager

        effective_datastore_url = self.config.datastore_endpoint or datastore_url
        full_target_image = self.config.full_target_image
        ccfg = self.config.crane

        self._log_build_start(model_name, model_version)

        safe_name = re.sub(r"[^a-z0-9-]", "-", deployment_name.lower()).strip("-")[:40].rstrip("-")
        if not safe_name:
            safe_name = "unnamed"
        build_subdir = f"build-{safe_name}-{int(time.time())}"
        build_dir = os.path.join(ccfg.build_context_dir, build_subdir)
        model_store_dir = os.path.join(build_dir, "model-store")

        job_name = f"crane-{safe_name}"[:63].rstrip("-")
        temp_secret_name = f"crane-creds-{safe_name}"[:63].rstrip("-")
        created_temp_secret = False

        crane = CraneJobManager(ccfg)

        try:
            if progress_callback:
                progress_callback("Validating configuration", 0.02)
            self.validate_config(self.config.model_dump())

            os.makedirs(build_dir, exist_ok=True)

            self._download_step(
                effective_datastore_url,
                model_name,
                model_version,
                model_store_dir,
                progress_callback,
            )

            if progress_callback:
                progress_callback("Preparing registry credentials", 0.35)

            # When pushing to JFrog with explicit jfrog_credentials, always build a
            # combined secret (NGC for base pull + JFrog for push) — even if
            # crane.registry_secret is set, because that secret only has NGC creds.
            needs_combined_secret = (
                self.config.registry_type == "jfrog"
                and self.config.jfrog_username
                and self.config.jfrog_password
            )

            registry_secret = ccfg.registry_secret if not needs_combined_secret else ""
            if not registry_secret:
                registries = self._collect_registries()
                crane.ensure_registry_secret(temp_secret_name, registries)
                registry_secret = temp_secret_name
                created_temp_secret = True

            if progress_callback:
                progress_callback("Launching Crane build job", 0.40)

            crane.create_build_job(
                job_name=job_name,
                destination_image=full_target_image,
                build_context_subdir=build_subdir,
                base_nim_image=self.config.base_nim_image,
                nim_model_name=self.config.nim_model_name or "",
                registry_secret_name=registry_secret,
            )

            if progress_callback:
                progress_callback("Waiting for Crane build to complete", 0.45)

            crane.wait_for_job(job_name, progress_callback=progress_callback)

            if progress_callback:
                progress_callback("Custom NIM image ready", 1.0)

            logger.info("Custom NIM image ready (crane): %s", full_target_image)

            return self._success_result(
                deployment_name,
                model_name,
                model_version,
                full_target_image,
                extra_meta={"build_engine": "crane", "crane_job": job_name},
            )

        except Exception as exc:
            logger.error("Custom NIM (crane) build failed: %s", exc)
            return self._failure_result(deployment_name, model_name, str(exc))

        finally:
            if self.config.cleanup_after_build:
                crane.cleanup(
                    job_name=job_name,
                    secret_name=temp_secret_name if created_temp_secret else None,
                )
                if os.path.exists(build_dir):
                    shutil.rmtree(build_dir, ignore_errors=True)
                    logger.info("Cleaned up Crane build context: %s", build_dir)

    # ==================================================================
    #  SHARED: status, undeploy, validate, etc.
    # ==================================================================

    def get_status(self, deployment_id: str) -> DeploymentResult:
        """Custom NIM builds are synchronous — check the deploy() result."""
        return DeploymentResult(
            deployment_id=deployment_id,
            platform=self.platform,
            status=DeploymentStatus.READY,
            metadata={"note": "Custom NIM builds are synchronous; check deploy() result."},
        )

    def undeploy(
        self,
        deployment_id: str,
        progress_callback: Callable[[str, float], None] | None = None,
    ) -> DeploymentResult:
        """No-op — the artifact is a pushed Docker image, not a running deployment."""
        logger.info(
            "Undeploy is a no-op for Custom NIM adapter (deployment_id=%s). "
            "To remove the image, delete it from the container registry.",
            deployment_id,
        )
        return DeploymentResult(
            deployment_id=deployment_id,
            platform=self.platform,
            status=DeploymentStatus.DELETED,
            metadata={"note": "No running deployment to tear down; image remains in registry."},
        )

    def get_inference_endpoint(self, deployment_id: str) -> str:
        return ""

    def validate_config(self, config: dict[str, Any]) -> bool:
        required_fields = [
            "base_nim_image",
            "target_registry",
            "target_repo",
            "target_image_name",
            "target_tag",
        ]
        missing = [f for f in required_fields if not config.get(f)]
        if missing:
            raise ValueError(
                f"Missing required Custom NIM config fields: {', '.join(missing)}. "
                "Provide them in config/custom_nim_deployment_config.yaml or platform_config."
            )
        return True

    # ==================================================================
    #  SHARED HELPERS
    # ==================================================================

    def _log_build_start(self, model_name: str, model_version: str) -> None:
        logger.info("Starting Custom NIM build (crane): %s", self.config.full_target_image)
        logger.info("Base NIM image  : %s", self.config.base_nim_image)
        logger.info("Model           : %s (version: %s)", model_name, model_version)
        logger.info("Registry type   : %s", self.config.registry_type)

    def _download_step(
        self,
        datastore_url: str,
        model_name: str,
        model_version: str,
        model_store_dir: str,
        progress_callback: Callable[[str, float], None] | None,
    ) -> None:
        if self.config.skip_download:
            logger.info("Skipping model download (skip_download=true)")
            if progress_callback:
                progress_callback("Skipping download", 0.15)
            return
        if progress_callback:
            progress_callback("Downloading model weights from datastore", 0.05)
        self._download_model(
            datastore_url=datastore_url,
            model_name=model_name,
            model_version=model_version,
            local_dir=model_store_dir,
            progress_callback=progress_callback,
        )

    def _success_result(
        self,
        deployment_name: str,
        model_name: str,
        model_version: str,
        full_target_image: str,
        extra_meta: dict[str, Any] | None = None,
    ) -> DeploymentResult:
        meta: dict[str, Any] = {
            "image_tag": full_target_image,
            "base_nim_image": self.config.base_nim_image,
            "registry_type": self.config.registry_type,
            "target_registry": self.config.target_registry,
            "model_version": model_version,
            "nim_model_name": self.config.nim_model_name,
            "build_engine": "crane",
        }
        if extra_meta:
            meta.update(extra_meta)
        return DeploymentResult(
            deployment_id=deployment_name,
            platform=self.platform,
            status=DeploymentStatus.READY,
            model_name=model_name,
            metadata=meta,
        )

    def _failure_result(
        self, deployment_name: str, model_name: str, error: str
    ) -> DeploymentResult:
        return DeploymentResult(
            deployment_id=deployment_name,
            platform=self.platform,
            status=DeploymentStatus.FAILED,
            error=error,
            model_name=model_name,
            metadata={"image_tag": self.config.full_target_image},
        )

    def _collect_registries(self) -> dict[str, tuple[str, str]]:
        """Build a registry -> (user, pass) map for all registries the crane job touches.

        Base image registry (nvcr.io): always uses NGC credentials
        ($oauthtoken / NGC_API_KEY) since NIM base images live on NGC.

        Target registry: uses JFrog credentials when registry_type is "jfrog"
        and jfrog_credentials are provided; otherwise falls back to
        docker_credentials (NGC default).
        """
        registries: dict[str, tuple[str, str]] = {}

        # Base image pull — always NGC ($oauthtoken / NGC_API_KEY)
        ngc_user = "$oauthtoken"
        ngc_pass = os.environ.get("NGC_API_KEY", "") or self.config.docker_password
        if not ngc_pass:
            raise ValueError(
                "NGC_API_KEY is required to pull the base NIM image from nvcr.io. "
                "Set it as an environment variable or via docker_credentials.password."
            )

        base_image = self.config.base_nim_image
        if "/" in base_image:
            base_host = base_image.split("/")[0]
            if "." in base_host or ":" in base_host:
                registries[base_host] = (ngc_user, ngc_pass)

        # Target registry push — JFrog creds if available, else NGC creds
        if (
            self.config.registry_type == "jfrog"
            and self.config.jfrog_username
            and self.config.jfrog_password
        ):
            target_user = self.config.jfrog_username
            target_pass = self.config.jfrog_password
            logger.info(
                "Using JFrog credentials for target registry %s", self.config.target_registry
            )
        else:
            target_user = self.config.docker_username
            target_pass = self.config.docker_password
            if not (target_user and target_pass):
                raise ValueError(
                    "Registry credentials required. For NGC: set NGC_API_KEY. "
                    "For JFrog: set CUSTOM_NIM_JFROG_USERNAME / CUSTOM_NIM_JFROG_PASSWORD."
                )

        registries[self.config.target_registry] = (target_user, target_pass)
        return registries

    # ------------------------------------------------------------------
    # Model download
    # ------------------------------------------------------------------

    def _download_model(
        self,
        datastore_url: str,
        model_name: str,
        model_version: str,
        local_dir: str,
        progress_callback: Callable[[str, float], None] | None = None,
    ) -> None:
        """Download model weights from the Nemo Data Store via a subprocess.

        Runs the download in a child process so that HF_ENDPOINT is set before
        huggingface_hub is imported, ensuring all URLs (including LFS) resolve
        to the datastore rather than huggingface.co.

        The approach mirrors the DataRobot adapter: plain snapshot_download
        with HF_ENDPOINT pointing to {datastore_base}/v1/hf — no monkey patches.
        """
        os.makedirs(local_dir, exist_ok=True)
        hf_cache_dir = tempfile.mkdtemp(prefix="hf_cache_nim_")

        hf_endpoint = _hf_endpoint_from_base(datastore_url)
        logger.info("Downloading model: %s (revision: %s)", model_name, model_version)
        logger.info("From endpoint    : %s", hf_endpoint)
        logger.info("To local dir     : %s", local_dir)

        download_script = textwrap.dedent("""\
            import os
            from huggingface_hub import snapshot_download
            snapshot_download(
                repo_id=os.environ["MODEL_REPO_ID"],
                revision=os.environ["MODEL_VERSION"],
                local_dir=os.environ["LOCAL_DIR"],
                repo_type="model",
                token=os.environ.get("HF_TOKEN") or None,
                force_download=os.environ.get("FORCE_DOWNLOAD") == "1",
                ignore_patterns=[".gitattributes"],
            )
        """)

        env = {
            **os.environ,
            "HF_ENDPOINT": hf_endpoint,
            "HF_HOME": hf_cache_dir,
            "MODEL_REPO_ID": model_name,
            "MODEL_VERSION": model_version,
            "LOCAL_DIR": local_dir,
        }
        if self.config.datastore_token:
            env["HF_TOKEN"] = self.config.datastore_token

        try:
            for attempt in range(1, self.config.download_max_retries + 1):
                if progress_callback:
                    progress_callback(
                        f"Download attempt {attempt}/{self.config.download_max_retries}",
                        0.05 + (0.20 * attempt / self.config.download_max_retries),
                    )

                logger.info("Download attempt %d/%d ...", attempt, self.config.download_max_retries)

                run_env = {**env}
                if attempt > 1:
                    run_env["FORCE_DOWNLOAD"] = "1"

                result = subprocess.run(
                    [sys.executable, "-c", download_script],
                    env=run_env,
                    capture_output=True,
                    text=True,
                    timeout=self.config.download_timeout_seconds,
                )

                if result.returncode == 0:
                    logger.info("Model download complete")
                    if result.stderr:
                        for line in result.stderr.strip().splitlines()[-5:]:
                            logger.debug("[download] %s", line)
                    return

                error_msg = result.stderr.strip() or result.stdout.strip()
                if attempt >= self.config.download_max_retries:
                    raise RuntimeError(
                        f"Model download failed after {self.config.download_max_retries} "
                        f"attempts:\n{error_msg}"
                    )

                logger.warning(
                    "Download attempt %d failed. Retrying in %ds...",
                    attempt,
                    self.config.download_retry_delay,
                )
                logger.debug("Download error output:\n%s", error_msg)
                time.sleep(self.config.download_retry_delay)

        finally:
            shutil.rmtree(hf_cache_dir, ignore_errors=True)
