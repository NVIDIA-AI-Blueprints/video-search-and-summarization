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
import time
from collections.abc import Callable
from typing import Any

from nemo_platform import APIStatusError, NeMoPlatform

from src.backends.nmp.workspace import ensure_workspace
from src.config import EmbeddingConfig, NIMConfig, settings
from src.lib.flywheel.cancellation import check_cancellation
from src.log_utils import setup_logging

logger = setup_logging("data_flywheel.nemo.dms_client")

# NMP 26.03 ModelDeploymentStatus is uppercase: UNKNOWN | CREATED | PENDING | READY |
# ERROR | DELETING | DELETED | LOST. The DMSClient surface is kept lowercase for
# backward compatibility with existing DFW callers (NMPAdapter, wait loops).
_LEGACY_STATUS_MAP = {
    "UNKNOWN": "pending",
    "CREATED": "pending",
    "PENDING": "pending",
    "READY": "ready",
    "ERROR": "failed",
    "DELETING": "deleting",
    "DELETED": "deleted",
    "LOST": "failed",
}


def _to_legacy_status(status: Any) -> str:
    if status is None:
        return "unknown"
    s = str(status)
    return _LEGACY_STATUS_MAP.get(s.upper(), s.lower())


class DMSClient:
    """Client wrapping NMP 26.03 ``client.inference`` deployment APIs.

    Args:
        nim: NIMConfig object describing the model to deploy.
    """

    def __init__(self, nim: NIMConfig | EmbeddingConfig):
        self.nmp_config = settings.system_config.nmp_config
        self.workspace = self.nmp_config.workspace
        self.nim = nim
        self.nemo_client = NeMoPlatform(
            base_url=self.nmp_config.nemo_base_url,
            inference_base_url=self.nmp_config.nim_base_url,
            workspace=self.workspace,
        )

    def create_deployment_config(
        self, base_model_for_image: str | None = None
    ) -> dict[str, Any] | None:
        """Create an inference deployment_config in NMP 26.03."""
        # Deployments live under an NMP workspace that must exist first (26.03).
        ensure_workspace(self.nemo_client, self.workspace)
        payload = self.nim.to_customized_dms_config(base_model_for_image=base_model_for_image)

        try:
            config = self.nemo_client.inference.deployment_configs.create(
                workspace=payload["workspace"],
                name=payload["name"],
                description=payload.get("description", ""),
                nim_deployment=payload["nim_deployment"],
            )
            logger.info(f"Created deployment config: {config.name}")
            return config.model_dump() if hasattr(config, "model_dump") else dict(config)
        except APIStatusError as e:
            if getattr(e, "status_code", None) == 409 or (
                e.status_code == 500 and "already exists" in str(e.message)
            ):
                logger.info(f"Deployment config {payload['name']} already exists")
                return None
            logger.error(f"Failed to create deployment config: {e}")
            raise
        except Exception as e:
            logger.error(f"Failed to create deployment config: {e}")
            raise

    def deploy_model(self, config: dict[str, Any] | None = None) -> dict[str, Any] | None:
        """Create an inference deployment in NMP 26.03."""
        # Deployments live under an NMP workspace that must exist first (26.03).
        ensure_workspace(self.nemo_client, self.workspace)
        if config:
            # Customized model path: deployment_config name was created above
            config_name = config["name"]
            deployment_name = config["name"]
        else:
            # Default path: build a deployment config from the NIM, then deploy
            payload = self.nim.to_dms_config()
            try:
                self.nemo_client.inference.deployment_configs.create(
                    workspace=payload["workspace"],
                    name=payload["name"],
                    description=payload.get("description", ""),
                    nim_deployment=payload["nim_deployment"],
                )
            except APIStatusError as e:
                if getattr(e, "status_code", None) not in (409, 500):
                    raise
                logger.info(f"Deployment config {payload['name']} already exists")
            config_name = payload["name"]
            deployment_name = payload["name"]

        try:
            deployment = self.nemo_client.inference.deployments.create(
                workspace=self.workspace,
                name=deployment_name,
                config=config_name,
            )
            logger.info(f"Created deployment: {deployment.name}")
            return (
                deployment.model_dump() if hasattr(deployment, "model_dump") else dict(deployment)
            )
        except APIStatusError as e:
            msg = str(getattr(e, "message", ""))
            if getattr(e, "status_code", None) in (409, 500) and (
                "already exists" in msg
                or "model deployment already exists" in msg
                or "already deployed" in msg
            ):
                logger.info(f"Deployment {deployment_name} already exists")
                return None
            logger.error(f"Failed to deploy model: {e}")
            raise
        except Exception as e:
            logger.error(f"Failed to deploy model: {e}")
            raise

    def is_deployed(self) -> bool:
        try:
            return self.get_deployment_status() in ("ready", "deployed")
        except Exception:
            return False

    def does_deployment_exist(self) -> bool:
        try:
            self.nemo_client.inference.deployments.retrieve(
                name=self.nim.nmp_model_name(), workspace=self.workspace
            )
            return True
        except Exception:
            return False

    def get_deployment_status(self) -> str:
        """Return deployment status translated to legacy DFW vocabulary (lowercase)."""
        try:
            deployment = self.nemo_client.inference.deployments.retrieve(
                name=self.nim.nmp_model_name(), workspace=self.workspace
            )
            return _to_legacy_status(getattr(deployment, "status", None))
        except Exception as e:
            logger.error(f"Failed to get deployment status: {e}")
            raise

    def wait_for_deployment(
        self,
        flywheel_run_id: str | None = None,
        progress_callback: Callable[[str], None] | None = None,
        timeout: int = 3600,
    ):
        """Wait for a deployment to reach ``ready``."""
        target_model = self.nim.target_model_for_evaluation()
        if self.is_model_available(target_model):
            logger.info(f"Model {target_model} is already available, skipping wait")
            if progress_callback is not None:
                progress_callback({"status": "ready"})
            return

        start_time = time.time()
        status = "unknown"

        while time.time() - start_time < timeout:
            if flywheel_run_id:
                try:
                    check_cancellation(flywheel_run_id)
                except Exception as e:
                    logger.info(f"Deployment wait cancelled: {e}")
                    raise e

            status = self.get_deployment_status()

            if progress_callback is not None:
                progress_callback({"status": status})

            logger.info(f"[{time.time() - start_time:.2f}] Deployment status: {status}")
            if status == "ready":
                if progress_callback is not None:
                    progress_callback({"status": status})
                return
            if status == "failed":
                error_message = f"Deployment {self.nim.nmp_model_name()} failed (ERROR status)"
                if progress_callback is not None:
                    progress_callback({"status": status, "error": error_message})
                raise Exception(error_message)
            time.sleep(5)
        error_message = f"Deployment did not complete within {timeout} seconds"
        if progress_callback is not None:
            progress_callback({"status": status, "error": error_message})
        raise TimeoutError(error_message)

    def is_model_available(self, model_name: str) -> bool:
        """Check whether a model is in the inference models list.

        In NMP 26.03 the inference gateway lists models as
        ``{workspace}/{nmp_model_name}`` (slashes/@/dots in ``model_name`` are
        replaced with hyphens by :py:meth:`NIMConfig.nmp_model_name`). DFW
        callers pass the legacy ``namespace/model.version`` form, so we accept
        either form when matching.
        """
        try:
            models = self.nemo_client.inference.models.list(workspace=self.workspace)
            model_iter = getattr(models, "data", None) or list(models)
            model_names = [getattr(m, "id", None) or getattr(m, "name", None) for m in model_iter]
            nmp_name = model_name.replace("/", "-").replace("@", "-").replace(".", "-")
            workspace_qualified = f"{self.workspace}/{nmp_name}"
            return (
                model_name in model_names
                or nmp_name in model_names
                or workspace_qualified in model_names
            )
        except Exception as e:
            logger.error(f"Failed to get models list: {e}")
            raise

    def wait_for_model_sync(
        self,
        model_name: str,
        flywheel_run_id: str,
        check_interval: int = 30,
        timeout: int = 3600,
    ) -> dict[str, Any]:
        """Wait for ``model_name`` to appear in the inference models list."""
        start_time = time.time()

        while True:
            try:
                check_cancellation(flywheel_run_id)
            except Exception as e:
                logger.info(f"Model sync wait cancelled: {e}")
                raise e

            if self.is_model_available(model_name):
                return {"status": "synced", "model_id": model_name}

            if time.time() - start_time > timeout:
                msg = f"Model {model_name} did not sync within {timeout} seconds"
                logger.error(msg)
                raise TimeoutError(msg)
            time.sleep(check_interval)

    def shutdown_deployment(self):
        """Delete an inference deployment in NMP 26.03."""
        try:
            result = self.nemo_client.inference.deployments.delete(
                name=self.nim.nmp_model_name(), workspace=self.workspace
            )
            logger.info(f"Successfully deleted deployment: {self.nim.nmp_model_name()}")
            if result is None:
                return {"status": "deleted"}
            return result.model_dump() if hasattr(result, "model_dump") else {"status": "deleted"}
        except APIStatusError as e:
            if getattr(e, "status_code", None) == 404 or "not found" in str(e.message).lower():
                logger.warning(
                    f"Deployment {self.nim.nmp_model_name()} not found, already deleted or never existed"
                )
                return {"status": "already_deleted"}
            logger.error(f"Failed to delete deployment: {e}")
            raise
        except Exception as e:
            logger.error(f"Failed to delete deployment: {e}")
            raise
