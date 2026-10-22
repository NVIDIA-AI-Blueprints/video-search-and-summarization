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

"""DataRobot deployment adapter for deploying models via Kubernetes."""

import datetime
import time
from collections.abc import Callable
from typing import Any

from kubernetes import client
from kubernetes import config as k8s_config
from kubernetes.client.rest import ApiException

from src.lib.deployment.adapters.base import (
    BaseDeploymentAdapter,
    DeploymentPlatform,
    DeploymentResult,
    DeploymentStatus,
)
from src.lib.deployment.adapters.datarobot.config import get_datarobot_config
from src.lib.deployment.adapters.factory import DeploymentAdapterFactory
from src.log_utils import setup_logging

logger = setup_logging("data_flywheel.deployment.datarobot")

# HF-compatible path appended to datastore base URL for model download (huggingface_hub).
HF_DATASTORE_PATH = "/v1/hf"


def _hf_endpoint_from_base(base_url: str) -> str:
    """Return HuggingFace-compatible endpoint URL for model download.

    API and config use the datastore base URL without path. Only DataRobot
    appends /v1/hf when calling the HF-compatible download endpoint.
    """
    url = (base_url or "").rstrip("/")
    if not url:
        return url
    if url.endswith("/v1/hf") or url.endswith("/v1/hf/"):
        return url.rstrip("/")
    return f"{url}{HF_DATASTORE_PATH}"


@DeploymentAdapterFactory.register(DeploymentPlatform.DATAROBOT)
class DataRobotAdapter(BaseDeploymentAdapter):
    """Adapter for deploying models to DataRobot via Kubernetes.

    This adapter implements the deployment workflow:
    1. Create PVC for model storage
    2. Create Kubernetes Job to download model from DataStore
    3. Patch the LRS deployment with volume mounts and env vars
    4. Verify the deployment is serving the model

    Configuration is loaded from config/datarobot_deployment_config.yaml
    and can be overridden via the config parameter.
    """

    def __init__(self, config: dict[str, Any]):
        """Initialize the DataRobot adapter.

        Args:
            config: Configuration dictionary with DataRobot settings.
                    These values override defaults from the YAML config file.
        """
        self.config = get_datarobot_config(config)
        self._init_kubernetes_client()

    @property
    def platform(self) -> DeploymentPlatform:
        """Return the platform identifier."""
        return DeploymentPlatform.DATAROBOT

    def _init_kubernetes_client(self):
        """Initialize Kubernetes client."""
        try:
            k8s_config.load_incluster_config()
            logger.info("Loaded in-cluster Kubernetes config")
        except k8s_config.ConfigException:
            k8s_config.load_kube_config()
            logger.info("Loaded local Kubernetes config")

        self.core_v1 = client.CoreV1Api()
        self.apps_v1 = client.AppsV1Api()
        self.batch_v1 = client.BatchV1Api()

    def _resolve_lrs_id(self, deployment_id: str) -> str:
        """Resolve the internal LRS ID from a DataRobot Deployment ID.

        Queries Kubernetes for a Deployment labelled with
        ``datarobot-deployment-id={deployment_id}`` and extracts the LRS ID
        from the ``datarobot-lrs-name`` label (stripping the ``lrs-`` prefix).

        Args:
            deployment_id: User-facing DataRobot Deployment ID

        Returns:
            The LRS ID (the part after ``lrs-`` in the k8s Deployment name)

        Raises:
            ValueError: If no matching LRS Deployment is found or the label
                is missing
        """
        deployments = self.apps_v1.list_namespaced_deployment(
            namespace=self.config.k8s_namespace,
            label_selector=f"datarobot-deployment-id={deployment_id}",
        )

        if not deployments.items:
            raise ValueError(
                f"No LRS deployment found for DataRobot deployment ID {deployment_id} "
                f"in namespace {self.config.k8s_namespace}"
            )

        lrs_name = deployments.items[0].metadata.labels.get("datarobot-lrs-name", "")
        if not lrs_name:
            raise ValueError(
                f"LRS deployment found but missing 'datarobot-lrs-name' label "
                f"for DataRobot deployment ID {deployment_id}"
            )

        # lrs_name is e.g. "lrs-69326f65a1d39690a7ed0d69"; return just the ID portion
        return lrs_name.removeprefix("lrs-")

    def deploy(
        self,
        datastore_url: str,
        model_name: str,
        model_version: str,
        deployment_name: str,
        config: dict[str, Any] | None = None,
        progress_callback: Callable[[str, float], None] | None = None,
    ) -> DeploymentResult:
        """Deploy model to DataRobot by downloading and patching the LRS deployment.

        This method handles the complete deployment workflow:
        1. Resolve the LRS ID from the DataRobot Deployment ID
        2. Create PVC for model storage
        3. Download model from datastore via Kubernetes Job
        4. Patch the LRS deployment with volume mounts and env vars
        5. Wait for rollout to complete

        Args:
            datastore_url: URL to the datastore
            model_name: Full name of the model (namespace/model-name)
            model_version: Version/revision of the model
            deployment_name: Unused — this adapter uses LRS deployment naming
                resolved from the DataRobot Deployment ID.
            config: Runtime configuration overrides from the request. Values here
                take precedence over self.config. Supported keys:
                - deployment_id: Override the target DataRobot Deployment ID
            progress_callback: Optional callback for progress updates

        Returns:
            DeploymentResult with status and endpoint
        """
        effective_config = config or {}
        deployment_id = effective_config.get("deployment_id", self.config.deployment_id)

        try:
            # Resolve the internal LRS ID from the user-facing DR Deployment ID
            lrs_id = self._resolve_lrs_id(deployment_id)
            logger.info(f"Resolved LRS ID {lrs_id} from DataRobot deployment {deployment_id}")

            pvc_name = f"nim-sft-model-{lrs_id}"
            job_name = f"download-sft-{lrs_id}"
            # Use DataRobot config datastore endpoint for model download. The passed
            # datastore_url is from NMP (nemo_base_url) and is not the HF-compatible
            # model file server; the data-store host serves the model artifacts.
            effective_datastore_url = self.config.datastore_endpoint or datastore_url

            # Step 1: Create PVC for model storage
            if progress_callback:
                progress_callback("Creating PVC for model storage", 0.05)

            self._create_pvc(pvc_name, lrs_id=lrs_id)

            # Step 2: Download model via Kubernetes Job
            if progress_callback:
                progress_callback("Starting model download job", 0.1)

            self._create_download_job(
                job_name=job_name,
                pvc_name=pvc_name,
                datastore_url=_hf_endpoint_from_base(effective_datastore_url),
                model_name=model_name,
                model_version=model_version,
                lrs_id=lrs_id,
            )

            if progress_callback:
                progress_callback("Waiting for download to complete", 0.15)

            self._wait_for_job(job_name, progress_callback)

            if progress_callback:
                progress_callback("Model download complete", 0.6)

            # Step 3: Patch the deployment with volume mounts and env vars
            if progress_callback:
                progress_callback("Patching deployment with model volume", 0.65)

            self._patch_deployment(
                pvc_name=pvc_name,
                model_name=model_name,
                lrs_id=lrs_id,
            )

            # Step 4: Wait for rollout
            if progress_callback:
                progress_callback("Waiting for rollout to complete", 0.7)

            self._wait_for_rollout(
                progress_callback=progress_callback,
                lrs_id=lrs_id,
            )

            if progress_callback:
                progress_callback("Verifying deployment", 0.95)

            endpoint_url = self.get_inference_endpoint(deployment_id)

            if progress_callback:
                progress_callback("Deployment complete", 1.0)

            return DeploymentResult(
                deployment_id=deployment_id,
                platform=self.platform,
                status=DeploymentStatus.READY,
                endpoint_url=endpoint_url,
                model_name=model_name,
                metadata={
                    "pvc_name": pvc_name,
                    "job_name": job_name,
                    "k8s_namespace": self.config.k8s_namespace,
                    "k8s_deployment": f"lrs-{lrs_id}",
                    "lrs_id": lrs_id,
                    "datastore_url": effective_datastore_url,
                    "model_version": model_version,
                },
            )

        except Exception as e:
            logger.error(f"Deployment failed: {e}")
            return DeploymentResult(
                deployment_id=deployment_id,
                platform=self.platform,
                status=DeploymentStatus.FAILED,
                error=str(e),
                model_name=model_name,
            )

    def get_status(self, deployment_id: str) -> DeploymentResult:
        """Get current deployment status.

        Args:
            deployment_id: DataRobot Deployment ID (user-facing)

        Returns:
            DeploymentResult with current status
        """
        try:
            lrs_id = self._resolve_lrs_id(deployment_id)
            deployment = self.apps_v1.read_namespaced_deployment(
                name=f"lrs-{lrs_id}",
                namespace=self.config.k8s_namespace,
            )

            conditions = deployment.status.conditions or []
            ready_replicas = deployment.status.ready_replicas or 0
            replicas = deployment.spec.replicas or 1

            if ready_replicas >= replicas:
                status = DeploymentStatus.READY
            elif any(c.type == "Progressing" and c.status == "True" for c in conditions):
                status = DeploymentStatus.DEPLOYING
            elif any(c.type == "Available" and c.status == "False" for c in conditions):
                status = DeploymentStatus.FAILED
            else:
                status = DeploymentStatus.PENDING

            return DeploymentResult(
                deployment_id=deployment_id,
                platform=self.platform,
                status=status,
                endpoint_url=self.get_inference_endpoint(deployment_id)
                if status == DeploymentStatus.READY
                else None,
                metadata={
                    "ready_replicas": ready_replicas,
                    "total_replicas": replicas,
                    "lrs_id": lrs_id,
                },
            )

        except ApiException as e:
            if e.status == 404:
                return DeploymentResult(
                    deployment_id=deployment_id,
                    platform=self.platform,
                    status=DeploymentStatus.DELETED,
                    error="Deployment not found",
                )
            raise

    def undeploy(
        self,
        deployment_id: str,
        progress_callback: Callable[[str, float], None] | None = None,
    ) -> DeploymentResult:
        """Remove the model from the deployment (rollback).

        This removes the volume mounts and environment variables that
        were added during deployment, then triggers a rollout to apply changes.

        Note: This doesn't delete the LRS deployment itself, just removes
        the custom model configuration.

        Uses read-modify-write pattern because strategic merge patch doesn't
        remove array items that are missing from the patch.

        Args:
            deployment_id: DataRobot Deployment ID (user-facing)
            progress_callback: Optional callback for progress updates (message, progress 0-1)

        Returns:
            DeploymentResult confirming rollback
        """
        try:
            lrs_id = self._resolve_lrs_id(deployment_id)
            logger.info(
                f"Resolved LRS ID {lrs_id} for undeploy of DataRobot deployment {deployment_id}"
            )

            deployment_name = f"lrs-{lrs_id}"
            container_name = f"{deployment_name}-additional-container-0"
            pvc_name = f"nim-sft-model-{lrs_id}"
            job_name = f"download-sft-{lrs_id}"

            if progress_callback:
                progress_callback("Reading current deployment configuration", 0.05)

            deployment = self.apps_v1.read_namespaced_deployment(
                name=deployment_name,
                namespace=self.config.k8s_namespace,
            )

            if progress_callback:
                progress_callback("Removing custom model volumes and env vars", 0.1)

            # Remove only model-store volume; keep dshm because the NIM
            # container needs the enlarged /dev/shm for Triton stub processes
            # even when running the base (non-SFT) model.
            volumes_to_remove = {"model-store"}
            if deployment.spec.template.spec.volumes:
                deployment.spec.template.spec.volumes = [
                    v
                    for v in deployment.spec.template.spec.volumes
                    if v.name not in volumes_to_remove
                ]

            # Remove our volume mounts and env vars from the NIM container
            env_vars_to_remove = {
                "NIM_CUSTOM_MODEL",
                "NIM_FT_MODEL",
                "NIM_SERVED_MODEL_NAME",
                "NIM_MODEL_NAME",
            }
            for container in deployment.spec.template.spec.containers:
                if container.name == container_name:
                    if container.volume_mounts:
                        container.volume_mounts = [
                            m for m in container.volume_mounts if m.name not in volumes_to_remove
                        ]
                    if container.env:
                        container.env = [
                            e for e in container.env if e.name not in env_vars_to_remove
                        ]
                    break

            # Add restart annotation to trigger rollout
            if deployment.spec.template.metadata.annotations is None:
                deployment.spec.template.metadata.annotations = {}
            deployment.spec.template.metadata.annotations["kubectl.kubernetes.io/restartedAt"] = (
                time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            )

            if progress_callback:
                progress_callback("Applying updated deployment spec", 0.15)

            self.apps_v1.replace_namespaced_deployment(
                name=deployment_name,
                namespace=self.config.k8s_namespace,
                body=deployment,
            )
            logger.info(f"Updated deployment {deployment_name} to remove custom model")

            if progress_callback:
                progress_callback("Waiting for rollout to complete", 0.2)

            # Wait for rollout with automatic pod-level retries.
            # Timeout is still non-fatal for undeploy: the custom model config
            # has already been removed from the deployment spec.
            rollout_timed_out = False
            try:
                self._wait_for_rollout(
                    progress_callback=progress_callback,
                    lrs_id=lrs_id,
                )
                logger.info(f"Rollback completed for deployment {deployment_name}")
            except TimeoutError:
                rollout_timed_out = True
                logger.warning(
                    f"Rollout failed for {deployment_name} during undeploy after "
                    f"{self.config.rollout_max_pod_retries} attempt(s). "
                    "Custom model config was removed; LRS pods may still be restarting."
                )
                if progress_callback:
                    progress_callback(
                        "Rollout still in progress (custom model config already removed)", 0.85
                    )

            if progress_callback:
                progress_callback("Cleaning up download job", 0.9)

            try:
                self.batch_v1.delete_namespaced_job(
                    name=job_name,
                    namespace=self.config.k8s_namespace,
                    propagation_policy="Background",
                )
                logger.info(f"Deleted job {job_name}")
            except ApiException as e:
                if e.status == 404:
                    logger.debug(
                        f"Job {job_name} not found in namespace "
                        f"{self.config.k8s_namespace} (already deleted or never created)"
                    )
                else:
                    logger.error(
                        f"Failed to delete job {job_name} in namespace "
                        f"{self.config.k8s_namespace}: {e.status} {e.reason}"
                    )
                    raise

            if progress_callback:
                progress_callback("Undeploy complete", 1.0)

            metadata: dict[str, Any] = {
                "pvc_name": pvc_name,
                "lrs_id": lrs_id,
                "note": "PVC preserved, delete manually if needed",
            }
            if rollout_timed_out:
                metadata["rollout_warning"] = (
                    "Rollout timed out; custom model config removed but "
                    "LRS pods may still be restarting"
                )

            return DeploymentResult(
                deployment_id=deployment_id,
                platform=self.platform,
                status=DeploymentStatus.DELETED,
                metadata=metadata,
            )

        except ApiException as e:
            logger.error(f"Undeploy failed: {e}")
            return DeploymentResult(
                deployment_id=deployment_id,
                platform=self.platform,
                status=DeploymentStatus.FAILED,
                error=str(e),
            )

    def get_inference_endpoint(self, deployment_id: str) -> str:
        """Get DataRobot inference endpoint URL.

        Args:
            deployment_id: DataRobot deployment ID

        Returns:
            Full URL for making inference requests
        """
        base_url = self.config.datarobot_api_url
        endpoint_path = self.config.get_inference_endpoint_path()
        return f"{base_url}/api/v2/deployments/{deployment_id}{endpoint_path}"

    def _create_pvc(self, pvc_name: str, lrs_id: str):
        """Create PersistentVolumeClaim for model storage.

        Args:
            pvc_name: Name for the PVC
            lrs_id: LRS ID for labels (resolved from DR Deployment ID)
        """
        pvc = client.V1PersistentVolumeClaim(
            metadata=client.V1ObjectMeta(
                name=pvc_name,
                namespace=self.config.k8s_namespace,
                labels={
                    "app": "nim-sft-model",
                    "lrs-id": lrs_id,
                },
            ),
            spec=client.V1PersistentVolumeClaimSpec(
                access_modes=["ReadWriteOnce"],
                storage_class_name=self.config.storage_class,
                resources=client.V1ResourceRequirements(
                    requests={"storage": self.config.storage_size}
                ),
            ),
        )

        try:
            self.core_v1.create_namespaced_persistent_volume_claim(
                namespace=self.config.k8s_namespace,
                body=pvc,
            )
            logger.info(f"Created PVC {pvc_name}")

            # Wait for PVC to be bound
            self._wait_for_pvc_bound(pvc_name)

        except ApiException as e:
            if e.status == 409:  # Already exists
                logger.info(f"PVC {pvc_name} already exists")
            else:
                raise

    def _wait_for_pvc_bound(self, pvc_name: str, timeout: int | None = None):
        """Wait for PVC to be bound.

        Args:
            pvc_name: Name of the PVC
            timeout: Timeout in seconds (uses config default if not provided)
        """
        if timeout is None:
            timeout = self.config.pvc_bind_timeout_seconds
        start_time = time.time()
        while time.time() - start_time < timeout:
            pvc = self.core_v1.read_namespaced_persistent_volume_claim(
                name=pvc_name,
                namespace=self.config.k8s_namespace,
            )
            if pvc.status.phase == "Bound":
                logger.info(f"PVC {pvc_name} is bound")
                return
            time.sleep(5)

        raise TimeoutError(f"PVC {pvc_name} did not bind within {timeout} seconds")

    def _create_download_job(
        self,
        job_name: str,
        pvc_name: str,
        datastore_url: str,
        model_name: str,
        model_version: str,
        lrs_id: str,
    ):
        """Create Kubernetes Job to download model from DataStore.

        Args:
            job_name: Name for the job
            pvc_name: Name of the PVC to use
            datastore_url: DataStore endpoint URL
            model_name: Full model name (namespace/model)
            model_version: Model version/revision
            lrs_id: LRS ID for labels (resolved from DR Deployment ID)
        """
        # Delete existing job if present (best-effort cleanup)
        try:
            self.batch_v1.delete_namespaced_job(
                name=job_name,
                namespace=self.config.k8s_namespace,
                propagation_policy="Background",
            )
            logger.debug(f"Deleted existing job {job_name} before creating new one")
        except ApiException as e:
            if e.status != 404:
                # Non-404 error (permissions, network, etc.) - warn but continue
                logger.warning(
                    f"Could not delete existing job {job_name} in namespace "
                    f"{self.config.k8s_namespace}: {e.status} {e.reason} - "
                    "proceeding with job creation anyway"
                )
            # 404 is fine - job doesn't exist
        time.sleep(2)  # Brief wait for cleanup before creating new job

        # Build the download script - values passed via env vars to avoid injection
        download_script = """
set -ex

echo "Installing huggingface-cli to /tmp..."
pip install --target=/tmp/pip_packages huggingface_hub
export PYTHONPATH=/tmp/pip_packages:$PYTHONPATH
export PATH=/tmp/pip_packages/bin:$PATH

echo "Clearing /model-store for fresh download (supports model swapping)..."
rm -rf /model-store/* /model-store/.[!.]* 2>/dev/null || true

echo "Downloading model: $MODEL_NAME"
echo "From: $HF_ENDPOINT"
echo "Revision: $MODEL_VERSION"

python -c "
import os
from huggingface_hub import snapshot_download
snapshot_download(
    repo_id=os.environ['MODEL_NAME'],
    revision=os.environ['MODEL_VERSION'],
    local_dir='/model-store',
    repo_type='model'
)
"

echo "===== Download Complete ====="
echo "Contents:"
ls -la /model-store/
echo ""
echo "Size:"
du -sh /model-store/
"""

        job = client.V1Job(
            metadata=client.V1ObjectMeta(
                name=job_name,
                namespace=self.config.k8s_namespace,
                labels={
                    "app": "nim-sft-download",
                    "lrs-id": lrs_id,
                },
            ),
            spec=client.V1JobSpec(
                ttl_seconds_after_finished=3600,
                backoff_limit=3,
                template=client.V1PodTemplateSpec(
                    spec=client.V1PodSpec(
                        restart_policy="Never",
                        security_context=client.V1PodSecurityContext(
                            run_as_user=self.config.run_as_user,
                            run_as_group=self.config.run_as_group,
                            fs_group=self.config.fs_group,
                            run_as_non_root=True,
                        ),
                        containers=[
                            client.V1Container(
                                name="model-puller",
                                image=self.config.puller_image,
                                security_context=client.V1SecurityContext(
                                    run_as_user=self.config.run_as_user,
                                    run_as_group=self.config.run_as_group,
                                    run_as_non_root=True,
                                    allow_privilege_escalation=False,
                                ),
                                env=[
                                    client.V1EnvVar(
                                        name="HF_TOKEN",
                                        value_from=client.V1EnvVarSource(
                                            secret_key_ref=client.V1SecretKeySelector(
                                                name=self.config.datastore_token_secret,
                                                key="HF_TOKEN",
                                            )
                                        ),
                                    ),
                                    client.V1EnvVar(
                                        name="HF_HUB_ENABLE_HF_TRANSFER",
                                        value="0",
                                    ),
                                    client.V1EnvVar(
                                        name="HF_ENDPOINT",
                                        value=datastore_url,
                                    ),
                                    client.V1EnvVar(
                                        name="HF_HOME",
                                        value="/tmp/hf_cache",
                                    ),
                                    # Model params passed as env vars to avoid shell/Python injection
                                    client.V1EnvVar(
                                        name="MODEL_NAME",
                                        value=model_name,
                                    ),
                                    client.V1EnvVar(
                                        name="MODEL_VERSION",
                                        value=model_version,
                                    ),
                                ],
                                command=["/bin/sh", "-c", download_script],
                                volume_mounts=[
                                    client.V1VolumeMount(
                                        name="model-store",
                                        mount_path="/model-store",
                                    )
                                ],
                                resources=client.V1ResourceRequirements(
                                    requests={"cpu": "1", "memory": "4Gi"},
                                    limits={"cpu": "4", "memory": "16Gi"},
                                ),
                            )
                        ],
                        volumes=[
                            client.V1Volume(
                                name="model-store",
                                persistent_volume_claim=client.V1PersistentVolumeClaimVolumeSource(
                                    claim_name=pvc_name
                                ),
                            )
                        ],
                        image_pull_secrets=[
                            client.V1LocalObjectReference(name=self.config.image_pull_secret)
                        ],
                    )
                ),
            ),
        )

        self.batch_v1.create_namespaced_job(
            namespace=self.config.k8s_namespace,
            body=job,
        )
        logger.info(f"Created download job {job_name}")

    def _wait_for_job(
        self,
        job_name: str,
        progress_callback: Callable[[str, float], None] | None = None,
    ):
        """Wait for job to complete.

        Args:
            job_name: Name of the job
            progress_callback: Optional callback for progress updates
        """
        timeout = self.config.download_timeout_seconds
        start_time = time.time()
        check_interval = 30

        while time.time() - start_time < timeout:
            job = self.batch_v1.read_namespaced_job(
                name=job_name,
                namespace=self.config.k8s_namespace,
            )

            # Check job status
            if job.status.succeeded and job.status.succeeded > 0:
                logger.info(f"Job {job_name} completed successfully")
                return

            if job.status.failed and job.status.failed >= 3:
                raise RuntimeError(f"Job {job_name} failed after {job.status.failed} attempts")

            elapsed = time.time() - start_time
            if progress_callback:
                # Estimate progress (assume ~30 min download)
                progress = min(0.3 + (elapsed / 1800) * 0.6, 0.9)
                progress_callback(f"Downloading model ({int(elapsed)}s elapsed)", progress)

            time.sleep(check_interval)

        raise TimeoutError(f"Job {job_name} did not complete within {timeout} seconds")

    def _patch_deployment(
        self,
        pvc_name: str,
        model_name: str,
        lrs_id: str,
    ):
        """Patch deployment with volume mounts and environment variables.

        Args:
            pvc_name: Name of the PVC with model artifacts
            model_name: Full model name for NIM configuration
            lrs_id: LRS ID (resolved from DR Deployment ID)
        """
        deployment_name = f"lrs-{lrs_id}"
        container_name = f"{deployment_name}-additional-container-0"

        patch = {
            "spec": {
                "template": {
                    "spec": {
                        "volumes": [
                            {
                                "name": "model-store",
                                "persistentVolumeClaim": {"claimName": pvc_name},
                            },
                            {
                                "name": "dshm",
                                "emptyDir": {"medium": "Memory"},
                            },
                        ],
                        "containers": [
                            {
                                "name": container_name,
                                "volumeMounts": [
                                    {"name": "model-store", "mountPath": "/model-store"},
                                    {"name": "dshm", "mountPath": "/dev/shm"},
                                ],
                                "env": [
                                    {"name": "NIM_CUSTOM_MODEL", "value": "/model-store"},
                                    {"name": "NIM_FT_MODEL", "value": "/model-store"},
                                    {"name": "NIM_SERVED_MODEL_NAME", "value": model_name},
                                    {"name": "NIM_MODEL_NAME", "value": model_name},
                                ],
                            }
                        ],
                    }
                }
            }
        }

        self.apps_v1.patch_namespaced_deployment(
            name=deployment_name,
            namespace=self.config.k8s_namespace,
            body=patch,
        )
        logger.info(f"Patched deployment {deployment_name}")

    def _find_lrs_pod(self, lrs_id: str) -> str | None:
        """Find the current pod name for an LRS deployment.

        Returns:
            Pod name or None if no pod exists.
        """
        lrs_name = f"lrs-{lrs_id}"
        pods = self.core_v1.list_namespaced_pod(
            namespace=self.config.k8s_namespace,
            label_selector=f"datarobot-lrs-name={lrs_name}",
        )
        for pod in pods.items:
            if pod.metadata.deletion_timestamp is None:
                return pod.metadata.name
        return None

    def _restart_deployment_for_retry(self, pod_name: str, lrs_id: str, attempt: int) -> str | None:
        """Trigger a rollout restart and wait for a new pod to replace the stuck one.

        Uses the same technique as ``kubectl rollout restart`` — patches the pod
        template with a ``restartedAt`` annotation, which bumps the deployment
        generation and creates a new ReplicaSet + pod.  This only requires
        ``patch deployments`` permission (which we already have), unlike direct
        pod deletion which needs ``delete pods`` in the target namespace.

        Args:
            pod_name: Name of the currently stuck pod.
            lrs_id: LRS ID used to find the replacement pod.
            attempt: Current retry attempt number (for logging).

        Returns:
            Name of the new pod, or None if no new pod appeared.
        """
        deployment_name = f"lrs-{lrs_id}"
        logger.warning(
            f"Rollout attempt {attempt}: restarting deployment {deployment_name} "
            f"(stuck pod: {pod_name})"
        )

        now = datetime.datetime.now(datetime.UTC).isoformat()
        patch_body = {
            "spec": {
                "template": {
                    "metadata": {
                        "annotations": {
                            "kubectl.kubernetes.io/restartedAt": now,
                        }
                    }
                }
            }
        }
        self.apps_v1.patch_namespaced_deployment(
            name=deployment_name,
            namespace=self.config.k8s_namespace,
            body=patch_body,
        )
        logger.info(f"Patched {deployment_name} with restartedAt={now}")

        self._wait_for_rollout_to_begin(lrs_id)

        wait = self.config.pod_reschedule_wait_seconds
        logger.info(f"Waiting {wait}s for new pod to schedule...")
        time.sleep(wait)

        retries = 0
        max_find_retries = 12
        new_pod = self._find_lrs_pod(lrs_id)
        while (new_pod is None or new_pod == pod_name) and retries < max_find_retries:
            time.sleep(5)
            new_pod = self._find_lrs_pod(lrs_id)
            retries += 1

        if new_pod and new_pod != pod_name:
            logger.info(f"New pod scheduled: {new_pod}")
        else:
            logger.error(f"No new pod found after restarting {deployment_name}")
        return new_pod if new_pod != pod_name else None

    def _count_ready_pods_for_deployment(self, deployment: Any) -> int:
        """Count pods that are Running and Ready for this deployment.

        Used as a fallback when deployment.status.ready_replicas is slow to update
        (e.g. on OpenShift or under load). List pods by the deployment's selector
        and count those with Ready condition True.
        """
        match_labels = deployment.spec.selector.match_labels or {}
        if not match_labels:
            return 0
        label_selector = ",".join(f"{k}={v}" for k, v in match_labels.items())
        pod_list = self.core_v1.list_namespaced_pod(
            namespace=self.config.k8s_namespace,
            label_selector=label_selector,
        )
        ready = 0
        for pod in pod_list.items:
            if pod.status.phase != "Running":
                continue
            conditions = pod.status.conditions or []
            if any(c.type == "Ready" and c.status == "True" for c in conditions):
                ready += 1
        return ready

    def _wait_for_rollout_to_begin(self, lrs_id: str, timeout: int = 60):
        """Wait for Kubernetes to actually start the rollout.

        After a patch or replace, the deployment controller takes a moment to
        create the new ReplicaSet and start terminating old pods. If we poll
        immediately, the stale status still shows ready_replicas=1 and we'd
        incorrectly declare the rollout complete.

        Waits until ``observedGeneration`` catches up to ``metadata.generation``
        AND ``updatedReplicas < replicas`` (i.e. the new rollout is in progress),
        or until the deployment is already fully ready with the new generation.
        """
        deployment_name = f"lrs-{lrs_id}"
        start = time.time()

        while time.time() - start < timeout:
            dep = self.apps_v1.read_namespaced_deployment(
                name=deployment_name,
                namespace=self.config.k8s_namespace,
            )
            # Spec version — bumped every time we patch/replace the deployment
            generation = dep.metadata.generation or 0
            # Last spec version the K8s controller has actually processed
            observed = (dep.status.observed_generation or 0) if dep.status else 0
            # How many pods we want (always 1 for LRS)
            replicas = dep.spec.replicas or 1
            # Pods running the NEW spec (0 = still on old spec)
            updated = (dep.status.updated_replicas or 0) if dep.status else 0
            # Pods that are healthy — all containers passing readiness probes (3/3)
            ready = (dep.status.ready_replicas or 0) if dep.status else 0

            # K8s has processed our spec change — the numbers are real, not stale.
            if observed >= generation:
                # No rollout needed: pod is already running the new spec AND healthy.
                # Happens when the patch was a no-op (e.g. deploying the same model twice).
                if updated >= replicas and ready >= replicas:
                    logger.info(
                        f"Deployment {deployment_name} already healthy at generation {generation}"
                    )
                    return
                # Rollout is underway: K8s acknowledged the change, new pod is spinning up.
                # Safe to hand off to _poll_rollout_once which will wait for ready=1.
                logger.info(
                    f"Rollout detected for {deployment_name} "
                    f"(generation={generation}, updated={updated}/{replicas})"
                )
                return

            time.sleep(3)

        logger.warning(
            f"Timed out waiting for rollout to begin for {deployment_name} "
            f"-- proceeding with polling anyway"
        )

    def _poll_rollout_once(
        self,
        lrs_id: str,
        timeout: int,
        progress_callback: Callable[[str, float], None] | None = None,
        attempt: int = 1,
    ) -> bool:
        """Poll deployment status until healthy or timeout.

        Returns:
            True if rollout completed successfully, False if timed out.
        """
        deployment_name = f"lrs-{lrs_id}"
        start_time = time.time()
        check_interval = 10

        # On first attempt, wait for the rollout to actually begin so we don't
        # mistake stale "ready" status for a completed rollout.
        if attempt == 1:
            self._wait_for_rollout_to_begin(lrs_id)
            # If we already consumed significant time, adjust the remaining budget
            elapsed_waiting = time.time() - start_time
            if elapsed_waiting >= timeout:
                return False

        # During the first 30s of a rollout, old pods may still show Ready=True.
        # Only trust the deployment status (which tracks the new ReplicaSet),
        # not the pod-level fallback, until old pods have had time to terminate.
        rollout_grace_period = 30

        while time.time() - start_time < timeout:
            deployment = self.apps_v1.read_namespaced_deployment(
                name=deployment_name,
                namespace=self.config.k8s_namespace,
            )

            replicas = deployment.spec.replicas or 1
            ready_replicas = deployment.status.ready_replicas or 0
            updated_replicas = deployment.status.updated_replicas or 0
            available_replicas = deployment.status.available_replicas or 0

            if (
                ready_replicas >= replicas
                and updated_replicas >= replicas
                and available_replicas >= replicas
            ):
                logger.info(f"Deployment {deployment_name} rollout complete")
                return True

            elapsed = time.time() - start_time

            # Fallback: count Ready pods directly (reliable when status lags).
            # Skip during rollout grace period — old pods may still show Ready
            # while the new pod is being created.
            if elapsed > rollout_grace_period:
                ready_pods = self._count_ready_pods_for_deployment(deployment)
                if ready_pods >= replicas:
                    logger.info(
                        f"Deployment {deployment_name} rollout complete ({ready_pods} pods ready)"
                    )
                    return True
            else:
                ready_pods = 0

            display_ready = max(ready_replicas, ready_pods)
            if progress_callback:
                progress = min(0.5 + (elapsed / timeout) * 0.4, 0.9)
                attempt_label = f" (attempt {attempt})" if attempt > 1 else ""
                progress_callback(
                    f"Rolling out ({display_ready}/{replicas} ready){attempt_label}",
                    progress,
                )

            time.sleep(check_interval)

        return False

    def _wait_for_rollout(
        self,
        progress_callback: Callable[[str, float], None] | None = None,
        lrs_id: str | None = None,
    ):
        """Wait for deployment rollout to complete, retrying on pod startup failures.

        On each attempt, polls until the deployment is healthy or the per-attempt
        timeout expires. If the pod fails to become ready (e.g. Triton stub
        process failure), the stuck pod is deleted and a fresh pod is created
        from the same deployment spec. This handles non-deterministic NIM
        startup failures without re-patching or re-downloading.

        Args:
            progress_callback: Optional callback for progress updates
            lrs_id: LRS ID (resolved from DR Deployment ID)

        Raises:
            TimeoutError: If all retry attempts are exhausted.
        """
        if not lrs_id:
            raise ValueError("lrs_id is required for _wait_for_rollout")

        deployment_name = f"lrs-{lrs_id}"
        timeout = self.config.rollout_timeout_seconds
        max_retries = self.config.rollout_max_pod_retries

        for attempt in range(1, max_retries + 1):
            if attempt > 1:
                logger.info(f"Rollout retry {attempt}/{max_retries} for {deployment_name}")
                if progress_callback:
                    progress_callback(f"Retrying rollout (attempt {attempt}/{max_retries})", 0.5)

            success = self._poll_rollout_once(
                lrs_id=lrs_id,
                timeout=timeout,
                progress_callback=progress_callback,
                attempt=attempt,
            )

            if success:
                if attempt > 1:
                    logger.info(f"Deployment {deployment_name} healthy after {attempt} attempt(s)")
                return

            if attempt >= max_retries:
                break

            pod_name = self._find_lrs_pod(lrs_id)
            if not pod_name:
                logger.error(
                    f"No pod found for {deployment_name} on attempt {attempt} -- cannot retry"
                )
                break

            new_pod = self._restart_deployment_for_retry(pod_name, lrs_id, attempt)
            if not new_pod:
                logger.error(
                    f"No replacement pod appeared after restarting lrs-{lrs_id} -- cannot retry"
                )
                break

        raise TimeoutError(
            f"Deployment {deployment_name} rollout failed after "
            f"{max_retries} attempt(s) ({timeout}s timeout each)"
        )

    def validate_config(self, config: dict[str, Any]) -> bool:
        """Validate DataRobot configuration.

        Args:
            config: Configuration dictionary

        Returns:
            True if valid

        Raises:
            ValueError: If configuration is invalid
        """
        if "deployment_id" not in config:
            raise ValueError(
                "deployment_id (DataRobot Deployment ID) is required for DataRobot adapter"
            )
        return True
