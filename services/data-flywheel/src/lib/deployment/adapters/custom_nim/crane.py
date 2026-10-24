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

"""Crane-based image building for Kubernetes / OpenShift environments.

Uses Google's crane (go-containerregistry) to append model layers to an
existing NIM base image and push the result via pure HTTP registry API
calls.  Truly rootless — no Docker daemon, no user namespaces, no root
access required.  Works on restricted OpenShift clusters.
"""

from __future__ import annotations

import base64
import json
import shlex
import time
from typing import TYPE_CHECKING, Any

from kubernetes import client as k8s_client
from kubernetes import config as k8s_config
from kubernetes.client.rest import ApiException

from src.log_utils import setup_logging

if TYPE_CHECKING:
    from collections.abc import Callable

    from src.lib.deployment.adapters.custom_nim.config import CraneConfig

logger = setup_logging("data_flywheel.deployment.custom_nim.crane")

_NAMESPACE_FILE = "/var/run/secrets/kubernetes.io/serviceaccount/namespace"


class CraneJobManager:
    """Manages the lifecycle of Crane build Jobs on Kubernetes.

    Typical flow:
        1. ``ensure_registry_secret`` — create (or reuse) a docker-config secret
        2. ``create_build_job`` — launch a crane Job that appends & pushes
        3. ``wait_for_job`` — poll until completion
        4. ``cleanup`` — delete the Job and any temporary secret
    """

    def __init__(self, crane_config: CraneConfig) -> None:
        self._cfg = crane_config
        self._batch_v1: k8s_client.BatchV1Api | None = None
        self._core_v1: k8s_client.CoreV1Api | None = None
        self._namespace: str | None = None

    # ------------------------------------------------------------------
    # Kubernetes client bootstrap
    # ------------------------------------------------------------------

    def _init_k8s(self) -> None:
        if self._batch_v1 is not None:
            return
        try:
            k8s_config.load_incluster_config()
        except k8s_config.ConfigException:
            k8s_config.load_kube_config()
        self._batch_v1 = k8s_client.BatchV1Api()
        self._core_v1 = k8s_client.CoreV1Api()
        self._namespace = self._cfg.namespace or self._detect_namespace()

    @staticmethod
    def _detect_namespace() -> str:
        try:
            with open(_NAMESPACE_FILE) as f:
                return f.read().strip()
        except FileNotFoundError:
            return "default"

    @property
    def namespace(self) -> str:
        self._init_k8s()
        assert self._namespace is not None
        return self._namespace

    # ------------------------------------------------------------------
    # Registry secret management
    # ------------------------------------------------------------------

    def ensure_registry_secret(
        self,
        secret_name: str,
        registries: dict[str, tuple[str, str]],
    ) -> str:
        """Create or replace a ``kubernetes.io/dockerconfigjson`` secret.

        Args:
            secret_name: desired K8s Secret name
            registries: mapping of ``{registry_host: (username, password)}``

        Returns:
            The secret name (same as input).
        """
        self._init_k8s()
        assert self._core_v1 is not None

        auths: dict[str, Any] = {}
        for host, (user, pwd) in registries.items():
            auth_b64 = base64.b64encode(f"{user}:{pwd}".encode()).decode()
            auths[host] = {"username": user, "password": pwd, "auth": auth_b64}

        docker_cfg_json = json.dumps({"auths": auths})

        secret = k8s_client.V1Secret(
            api_version="v1",
            kind="Secret",
            metadata=k8s_client.V1ObjectMeta(
                name=secret_name,
                namespace=self.namespace,
                labels={"app.kubernetes.io/managed-by": "data-flywheel"},
            ),
            type="kubernetes.io/dockerconfigjson",
            data={
                ".dockerconfigjson": base64.b64encode(docker_cfg_json.encode()).decode(),
            },
        )

        try:
            self._core_v1.delete_namespaced_secret(secret_name, self.namespace)
        except ApiException as exc:
            if exc.status != 404:
                raise

        self._core_v1.create_namespaced_secret(self.namespace, secret)
        logger.info("Created registry secret %s in %s", secret_name, self.namespace)
        return secret_name

    # ------------------------------------------------------------------
    # Crane Job creation
    # ------------------------------------------------------------------

    def create_build_job(
        self,
        *,
        job_name: str,
        destination_image: str,
        build_context_subdir: str,
        base_nim_image: str,
        nim_model_name: str,
        registry_secret_name: str,
    ) -> str:
        """Create a Crane Job that appends model files and pushes the NIM image.

        The Job runs a shell script that:
        1. Packs model-store/ into an uncompressed tar at /model-store/
        2. crane append — adds the layer to the base image
        3. crane mutate — sets NIM_CUSTOM_MODEL, NIM_FT_MODEL,
           NIM_SERVED_MODEL_NAME, and NIM_MODEL_NAME env vars

        Args:
            job_name: name for the K8s Job
            destination_image: full image tag (registry/repo/name:tag)
            build_context_subdir: subdirectory under the PVC root that contains
                                  ``model-store/``
            base_nim_image: base NIM image reference
            nim_model_name: model name baked into NIM env vars
            registry_secret_name: K8s secret with registry credentials

        Returns:
            The job name.
        """
        self._init_k8s()
        assert self._batch_v1 is not None

        q_dest = shlex.quote(destination_image)
        q_base = shlex.quote(base_nim_image)
        q_model = shlex.quote(nim_model_name)
        q_subdir = shlex.quote(build_context_subdir)

        build_script = f"""\
set -ex

echo "=== Crane build: {q_dest} ==="
echo "Base image: {q_base}"

# Prepare model layer tarball at /model-store/ — a separate path from the
# base image's /opt/nim/model-store/.  NIM_CUSTOM_MODEL and NIM_FT_MODEL
# tell the NIM runtime to load from here, matching the Docker build approach.
mkdir -p /tmp/layer-root
cp -a /workspace/{q_subdir}/model-store /tmp/layer-root/model-store
cd /tmp/layer-root
# Uncompressed tar — avoids BusyBox's slow single-threaded gzip.
# crane auto-detects the format; the registry compresses on storage.
tar cf /tmp/model-layer.tar .
cd /
echo "Layer tarball ready: $(du -h /tmp/model-layer.tar)"

# Append model layer to base image
crane append \
  --base={q_base} \
  --new_layer=/tmp/model-layer.tar \
  --new_tag={q_dest}
echo "Layer appended successfully"

# Set NIM environment variables — mirrors the Dockerfile ENV directives:
#   NIM_CUSTOM_MODEL  → tells NIM runtime where custom weights live
#   NIM_FT_MODEL      → fine-tuned model path (same as custom)
#   NIM_SERVED_MODEL_NAME → model name exposed on the API
#   NIM_MODEL_NAME    → internal model identifier
crane mutate {q_dest} \
  --env NIM_CUSTOM_MODEL=/model-store \
  --env NIM_FT_MODEL=/model-store \
  --env NIM_SERVED_MODEL_NAME={q_model} \
  --env NIM_MODEL_NAME={q_model}
echo "Image mutated with NIM env vars (model={q_model})"

echo "=== Build & push complete: {q_dest} ==="
"""

        container = k8s_client.V1Container(
            name="crane",
            image=self._cfg.image,
            command=["/busybox/sh", "-c", build_script],
            env=[
                k8s_client.V1EnvVar(name="HOME", value="/tmp/crane-home"),
                k8s_client.V1EnvVar(name="DOCKER_CONFIG", value="/auth"),
            ],
            volume_mounts=[
                k8s_client.V1VolumeMount(name="build-context", mount_path="/workspace"),
                k8s_client.V1VolumeMount(name="docker-config", mount_path="/auth"),
                k8s_client.V1VolumeMount(name="tmp-dir", mount_path="/tmp"),
            ],
        )

        volumes = [
            k8s_client.V1Volume(
                name="build-context",
                persistent_volume_claim=k8s_client.V1PersistentVolumeClaimVolumeSource(
                    claim_name=self._cfg.pvc_name,
                ),
            ),
            k8s_client.V1Volume(
                name="docker-config",
                secret=k8s_client.V1SecretVolumeSource(
                    secret_name=registry_secret_name,
                    items=[k8s_client.V1KeyToPath(key=".dockerconfigjson", path="config.json")],
                ),
            ),
            k8s_client.V1Volume(
                name="tmp-dir",
                empty_dir=k8s_client.V1EmptyDirVolumeSource(),
            ),
        ]

        pod_spec = k8s_client.V1PodSpec(
            containers=[container],
            restart_policy="Never",
            volumes=volumes,
        )
        if self._cfg.service_account:
            pod_spec.service_account_name = self._cfg.service_account

        job = k8s_client.V1Job(
            api_version="batch/v1",
            kind="Job",
            metadata=k8s_client.V1ObjectMeta(
                name=job_name,
                namespace=self.namespace,
                labels={
                    "app.kubernetes.io/managed-by": "data-flywheel",
                    "app.kubernetes.io/component": "crane-build",
                },
            ),
            spec=k8s_client.V1JobSpec(
                ttl_seconds_after_finished=self._cfg.job_ttl_seconds_after_finished,
                active_deadline_seconds=self._cfg.job_timeout_seconds,
                backoff_limit=0,
                template=k8s_client.V1PodTemplateSpec(spec=pod_spec),
            ),
        )

        self._delete_job_if_exists(job_name)
        self._batch_v1.create_namespaced_job(self.namespace, job)
        logger.info(
            "Created Crane build job %s (image=%s) in namespace %s",
            job_name,
            destination_image,
            self.namespace,
        )
        return job_name

    # ------------------------------------------------------------------
    # Job monitoring
    # ------------------------------------------------------------------

    def wait_for_job(
        self,
        job_name: str,
        progress_callback: Callable[[str, float], None] | None = None,
    ) -> bool:
        """Block until the Crane Job succeeds or fails.

        Returns:
            ``True`` on success.

        Raises:
            RuntimeError: if the Job fails or times out.
        """
        self._init_k8s()
        assert self._batch_v1 is not None

        poll_interval = self._cfg.poll_interval_seconds
        timeout = self._cfg.job_timeout_seconds
        start = time.monotonic()

        while True:
            elapsed = time.monotonic() - start
            if elapsed > timeout:
                raise RuntimeError(
                    f"Crane build job '{job_name}' timed out after {int(elapsed)}s "
                    f"(limit: {timeout}s)"
                )

            try:
                status = self._batch_v1.read_namespaced_job_status(job_name, self.namespace).status
            except ApiException as exc:
                raise RuntimeError(f"Failed to read job '{job_name}' status: {exc}") from exc

            if status.succeeded and status.succeeded > 0:
                logger.info("Crane build job '%s' succeeded", job_name)
                return True

            if status.failed and status.failed > 0:
                detail = self._get_pod_logs(job_name)
                raise RuntimeError(f"Crane build job '{job_name}' failed.\n{detail}")

            if progress_callback:
                pct = min(0.70 + (elapsed / timeout) * 0.20, 0.90)
                progress_callback(f"Crane build in progress ({int(elapsed)}s elapsed)", pct)

            time.sleep(poll_interval)

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def cleanup(
        self,
        job_name: str | None = None,
        secret_name: str | None = None,
    ) -> None:
        """Best-effort removal of a Crane Job and/or temporary secret."""
        self._init_k8s()

        if job_name:
            self._delete_job_if_exists(job_name)
        if secret_name:
            self._delete_secret_if_exists(secret_name)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _delete_job_if_exists(self, name: str) -> None:
        assert self._batch_v1 is not None
        try:
            self._batch_v1.delete_namespaced_job(
                name, self.namespace, propagation_policy="Foreground"
            )
            logger.info("Deleted job %s", name)
            time.sleep(2)
        except ApiException as exc:
            if exc.status != 404:
                logger.warning("Failed to delete job %s: %s", name, exc)

    def _delete_secret_if_exists(self, name: str) -> None:
        assert self._core_v1 is not None
        try:
            self._core_v1.delete_namespaced_secret(name, self.namespace)
            logger.info("Deleted secret %s", name)
        except ApiException as exc:
            if exc.status != 404:
                logger.warning("Failed to delete secret %s: %s", name, exc)

    def _get_pod_logs(self, job_name: str, tail_lines: int = 80) -> str:
        assert self._core_v1 is not None
        try:
            pods = self._core_v1.list_namespaced_pod(
                self.namespace, label_selector=f"job-name={job_name}"
            )
            if pods.items:
                pod_name = pods.items[0].metadata.name
                logs = self._core_v1.read_namespaced_pod_log(
                    pod_name, self.namespace, tail_lines=tail_lines
                )
                return f"Pod logs ({pod_name}):\n{logs}"
        except ApiException:
            pass
        return "(could not retrieve pod logs)"
