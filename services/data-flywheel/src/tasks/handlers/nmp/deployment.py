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

"""Handlers for NIM deployment tasks."""

from datetime import datetime

from src.api.models import LLMJudgeRun, NIMRun, NIMRunStatus, TaskResult
from src.api.schemas import DeploymentStatus
from src.backends.factory import get_backend_provider
from src.config import NIMConfig
from src.lib.flywheel.cancellation import FlywheelCancelledError
from src.tasks.base import PipelineTaskHandler


class WaitForLLMJudgeHandler(PipelineTaskHandler):
    """Handler for waiting for LLM judge to be ready."""

    def execute(self, previous_result: TaskResult) -> TaskResult:
        """Wait for LLM judge to be ready.

        LLM judge could be either local or remote. If it is local, we need to wait
        for the model to be loaded. If it is remote, we can directly continue.

        Args:
            previous_result: Result from the previous task

        Returns:
            Updated TaskResult
        """
        previous_result = self.extract_previous_result(previous_result)

        llm_as_judge_cfg = previous_result.llm_judge_config
        llm_judge_run = LLMJudgeRun(
            **self.db_manager.find_llm_judge_run(previous_result.flywheel_run_id)
        )

        try:
            # Check for cancellation
            self.check_cancellation(previous_result.flywheel_run_id, raise_error=True)

            if llm_as_judge_cfg.is_remote:
                self.logger.info("Remote LLM Judge will be used")
                return previous_result

            # Update LLM judge deployment status to pending
            self.db_manager.update_llm_judge_deployment_status(
                llm_judge_run.id, DeploymentStatus.PENDING
            )

            dms_client = get_backend_provider().deployment_client(nim=llm_as_judge_cfg)

            def progress_callback(status: dict):
                self.db_manager.update_llm_judge_deployment_status(
                    llm_judge_run.id,
                    DeploymentStatus(status.get("status", "unknown")),
                )

            # Wait for the model to be loaded (will return early if already available)
            dms_client.wait_for_deployment(
                flywheel_run_id=previous_result.flywheel_run_id,
                progress_callback=progress_callback,
            )

            # Wait for model sync
            dms_client.wait_for_model_sync(
                flywheel_run_id=previous_result.flywheel_run_id,
                model_name=llm_as_judge_cfg.target_model_for_evaluation(),
            )

        except Exception as e:
            error_msg = f"Error waiting for LLM as judge: {e!s}"
            self.logger.error(error_msg)

            # Handle LLM judge cancellation vs failure
            if isinstance(e, FlywheelCancelledError):
                self.db_manager.mark_llm_judge_cancelled(previous_result.flywheel_run_id, error_msg)
            else:
                self.db_manager.mark_llm_judge_error(llm_judge_run.id, error_msg)

            # Update flywheel run with error
            self.db_manager.mark_flywheel_run_error(
                previous_result.flywheel_run_id, error_msg, finished_at=datetime.utcnow()
            )

            # Update all the NIM runs to error
            status = (
                NIMRunStatus.CANCELLED
                if isinstance(e, FlywheelCancelledError)
                else NIMRunStatus.FAILED
            )
            self.db_manager.mark_all_nims_status(
                previous_result.flywheel_run_id, status, error_msg=str(e)
            )
            raise ValueError(error_msg) from e

        return previous_result


class SpinUpNIMHandler(PipelineTaskHandler):
    """Handler for spinning up a NIM instance."""

    def execute(self, previous_result: TaskResult, nim_config: dict) -> TaskResult:
        """Spin up a NIM instance.

        Args:
            previous_result: Result from the previous task
            nim_config: Full NIM configuration including model_name, context_length, etc.

        Returns:
            Updated TaskResult
        """
        previous_result = self.extract_previous_result(previous_result)

        # Convert nim_config to NIMConfig object
        nim_config_obj = NIMConfig(**nim_config)
        previous_result.nim = nim_config_obj

        # Reset all the previous errors as the errors are for the previous NIMs
        previous_result.error = None

        # Find the nim run for the given model name
        nim_run = self.db_manager.find_nim_run(
            previous_result.flywheel_run_id,
            nim_config_obj.model_name,
        )
        if nim_run:
            nim_run = NIMRun(**nim_run)

        dms_client = get_backend_provider().deployment_client(nim=nim_config_obj)

        try:
            # Check for cancellation
            self.check_cancellation(previous_result.flywheel_run_id, raise_error=True)

            start_time = datetime.utcnow()
            if not dms_client.is_deployed():
                self.logger.info(f"Deploying NIM {nim_config_obj.model_name}")

                try:
                    dms_client.deploy_model()
                except Exception as e:
                    self.logger.error(f"Error deploying NIM {nim_config_obj.model_name}: {e}")
                    if nim_run:
                        self.db_manager.mark_nim_error(nim_run.id, error_msg=str(e))
                    previous_result.error = str(e)
                    return previous_result
            else:
                self.logger.info(f"NIM {nim_config_obj.model_name} is already deployed")

            # Set started_at once deployment has been initiated or NIM was already deployed
            if nim_run:
                self.db_manager.set_nim_started(nim_run.id, start_time)

            # Progress callback for NIM deployment
            def progress_callback(status: dict):
                current_time = datetime.utcnow()
                runtime_seconds = (current_time - start_time).total_seconds()
                self.db_manager.update_nim_deployment_status(
                    nim_run.id,
                    DeploymentStatus(status.get("status", "unknown")),
                    runtime_seconds,
                )

            # Wait for the NIM deployment to be ready (will return early if already available)
            dms_client.wait_for_deployment(
                flywheel_run_id=previous_result.flywheel_run_id,
                progress_callback=progress_callback if nim_run else None,
            )

            # Wait for the NIM model to be synced
            dms_client.wait_for_model_sync(
                flywheel_run_id=previous_result.flywheel_run_id,
                model_name=nim_config_obj.target_model_for_evaluation(),
            )

            # Update the NIM run with the status of the NIM deployment
            if nim_run:
                self.db_manager.set_nim_status(
                    nim_run.id,
                    NIMRunStatus.RUNNING,
                    deployment_status=DeploymentStatus.READY,
                )

            return previous_result

        except Exception as e:
            error_msg = f"Error spinning up NIM: {e!s}"
            self.logger.error(error_msg)

            # Persist error on NIM run
            if isinstance(e, FlywheelCancelledError):
                if nim_run:
                    self.db_manager.mark_nim_cancelled(
                        nim_run.id,
                        error_msg="Flywheel run cancelled",
                    )
                previous_result.error = f"Flywheel run cancelled: {e.message}"
            else:
                if nim_run:
                    self.db_manager.mark_nim_error(nim_run.id, error_msg=str(e))
                previous_result.error = error_msg

            return previous_result


class SpinUpCustomizedNIMHandler(PipelineTaskHandler):
    """Handler for spinning up a customized NIM instance.

    This handler includes special logic for customized models:
    - Validates customized_nim availability
    - Extracts base_model_for_image
    - Creates deployment config with base model
    - Skips model sync (not needed for customized models)
    - Uses base model name for NIM run lookup
    """

    def execute(self, previous_result: TaskResult) -> TaskResult:
        """Spin up a customized NIM instance.

        Args:
            previous_result: Result from the previous task (must contain customized_nim)

        Returns:
            Updated TaskResult
        """
        previous_result = self.extract_previous_result(previous_result)

        # Validate that customized NIM config is available
        if (
            not hasattr(previous_result, "customized_nim")
            or not previous_result.customized_nim
            or previous_result.error
        ):
            # Skip if customization failed or model not available
            self.logger.warning(
                f"Skipping customized NIM spin-up - customization not available or previous error: {previous_result.error}"
            )
            return previous_result

        # Extract the customized nim config from previous_result
        nim_config = previous_result.customized_nim

        # Extract base model for image if present (for customized models)
        base_model_for_image = nim_config.get("base_model_for_image", None)

        # Convert nim_config to NIMConfig object
        nim_config_obj = NIMConfig(**nim_config)

        # For customized NIMs, don't overwrite previous_result.nim
        # (it still points to the base model for NIM run lookup)

        # Reset all the previous errors as the errors are for the previous NIMs
        previous_result.error = None

        # Find NIM run - use base model for customized deployments
        lookup_model = previous_result.nim.model_name
        nim_run = self.db_manager.find_nim_run(previous_result.flywheel_run_id, lookup_model)
        if nim_run:
            nim_run = NIMRun(**nim_run)

        dms_client = get_backend_provider().deployment_client(nim=nim_config_obj)

        try:
            # Check for cancellation
            self.check_cancellation(previous_result.flywheel_run_id, raise_error=True)

            # Create deployment config for customized model
            try:
                deployment_config = dms_client.create_deployment_config(
                    base_model_for_image=base_model_for_image
                )
                if deployment_config is None:
                    # create_deployment_config returns None when the config already
                    # exists (idempotent reuse — e.g. a re-run whose customizer drew a
                    # recycled output-model name). Reconstruct the payload so the deploy
                    # step references the same config name instead of crashing on a
                    # ``None`` subscript.
                    deployment_config = nim_config_obj.to_customized_dms_config(
                        base_model_for_image=base_model_for_image
                    )
                    self.logger.info(
                        f"Deployment config for customized NIM {nim_config_obj.model_name} "
                        f"already exists; reusing {deployment_config['name']}"
                    )
                else:
                    self.logger.info(
                        f"Created deployment config for customized NIM {nim_config_obj.model_name}: {deployment_config['name']}"
                    )
            except Exception as e:
                error_msg = (
                    f"Customized model config creation failed for {nim_config_obj.model_name}: {e}"
                )
                self.logger.error(error_msg)
                if nim_run:
                    self.db_manager.mark_nim_error(nim_run.id, error_msg=error_msg)
                previous_result.error = error_msg
                return previous_result

            start_time = datetime.utcnow()
            if not dms_client.is_deployed():
                self.logger.info(f"Deploying customized NIM {nim_config_obj.model_name}")

                try:
                    dms_client.deploy_model(config=deployment_config)
                except Exception as e:
                    error_msg = (
                        f"Customized model deployment failed for {nim_config_obj.model_name}: {e}"
                    )
                    self.logger.error(error_msg)
                    if nim_run:
                        self.db_manager.mark_nim_error(nim_run.id, error_msg=error_msg)
                    previous_result.error = error_msg
                    return previous_result
            else:
                self.logger.info(f"Customized NIM {nim_config_obj.model_name} is already deployed")

            # Set started_at once deployment has been initiated or NIM was already deployed
            if nim_run:
                self.db_manager.set_nim_started(nim_run.id, start_time)

            # Progress callback for NIM deployment
            def progress_callback(status: dict):
                current_time = datetime.utcnow()
                runtime_seconds = (current_time - start_time).total_seconds()
                self.db_manager.update_nim_deployment_status(
                    nim_run.id,
                    DeploymentStatus(status.get("status", "unknown")),
                    runtime_seconds,
                )

            # Wait for the NIM deployment to be ready
            dms_client.wait_for_deployment(
                flywheel_run_id=previous_result.flywheel_run_id,
                progress_callback=progress_callback if nim_run else None,
            )

            # Wait for the NIM model to be synced
            ## for SFT deployed model, remove the @cust-xyz suffix if present
            target_model = nim_config_obj.target_model_for_evaluation()
            target_model = target_model.split("@")[0] if "@" in target_model else target_model
            dms_client.wait_for_model_sync(
                flywheel_run_id=previous_result.flywheel_run_id,
                model_name=target_model,
            )

            # Update the NIM run with the status of the NIM deployment
            if nim_run:
                self.db_manager.set_nim_status(
                    nim_run.id,
                    NIMRunStatus.RUNNING,
                    deployment_status=DeploymentStatus.READY,
                )

            return previous_result

        except Exception as e:
            error_msg = f"Error spinning up customized NIM: {e!s}"
            self.logger.error(error_msg)

            try:
                dms_client.shutdown_deployment()
            except Exception as dms_client_err:
                self.logger.error(
                    f"Error shutting down customized NIM {nim_config_obj.model_name}: {dms_client_err!s}"
                )

            # Persist error on NIM run
            if isinstance(e, FlywheelCancelledError):
                if nim_run:
                    self.db_manager.mark_nim_cancelled(
                        nim_run.id,
                        error_msg="Flywheel run cancelled",
                    )
                previous_result.error = f"Flywheel run cancelled: {e.message}"
            else:
                if nim_run:
                    self.db_manager.mark_nim_error(nim_run.id, error_msg=str(e))
                previous_result.error = error_msg

            return previous_result


class ShutdownDeploymentHandler(PipelineTaskHandler):
    """Handler for shutting down NIM deployment."""

    def execute(self, previous_results: list[TaskResult] | TaskResult) -> TaskResult:
        """Shutdown the NIM deployment.

        Args:
            previous_results: Either a single TaskResult or a list of them produced by a group

        Returns:
            Updated TaskResult
        """
        previous_result: TaskResult | None = None

        try:
            previous_result = self.extract_previous_result(
                previous_results,
                validator=lambda r: getattr(r, "nim", None) is not None,
                error_msg="No valid TaskResult with NIM config found in results",
            )

            # Mark the NIM run as completed first
            try:
                nim_run_doc = self.db_manager.find_nim_run(
                    previous_result.flywheel_run_id,
                    previous_result.nim.model_name,
                )
                if nim_run_doc:
                    if self.check_cancellation(previous_result.flywheel_run_id, raise_error=False):
                        self.db_manager.mark_nim_cancelled(
                            nim_run_doc["_id"],
                            error_msg="Flywheel run cancelled",
                        )
                    else:
                        self.db_manager.mark_nim_completed(
                            nim_run_doc["_id"], nim_run_doc["started_at"]
                        )
            except Exception as update_err:
                self.logger.error("Failed to update NIM run status to COMPLETED: %s", update_err)

            # Skip shutting down if NIM is the same as LLM Judge
            if (
                previous_result.llm_judge_config
                and not previous_result.llm_judge_config.is_remote
                and previous_result.llm_judge_config.model_name == previous_result.nim.model_name
            ):
                self.logger.info(
                    f"Skip shutting down NIM {previous_result.nim.model_name} as it is the same as the LLM Judge"
                )
                return previous_result

            dms_client = get_backend_provider().deployment_client(nim=previous_result.nim)
            dms_client.shutdown_deployment()

            return previous_result

        except Exception as e:
            error_msg = f"Error shutting down NIM deployment: {e!s}"
            self.logger.error(error_msg)

            # previous_result may not be available if extraction failed
            previous_result = locals().get("previous_result")  # type: ignore[arg-type]
            if not previous_result:
                return previous_result  # type: ignore[return-value]

            nim_run_doc = self.db_manager.find_nim_run(
                previous_result.flywheel_run_id,
                previous_result.nim.model_name,
            )
            if not nim_run_doc:
                self.logger.error(
                    f"Could not find NIM run for flywheel_run_id: {previous_result.flywheel_run_id}"
                )
                return previous_result

            # Update nim document with error
            self.db_manager.mark_nim_error(nim_run_doc["_id"], error_msg)
            previous_result.error = error_msg

        return previous_result


class ShutdownCustomizedNIMHandler(PipelineTaskHandler):
    """Handler for shutting down customized NIM deployments.

    Works for both embedding and LLM (DPO) customized models.
    """

    def execute(self, previous_result: TaskResult | list[TaskResult]) -> TaskResult:
        """Shutdown the customized NIM deployment.

        Args:
            previous_result: Result from the previous task (can be a list from group)

        Returns:
            Updated TaskResult
        """
        # Extract the TaskResult from group results if needed
        previous_result = self.extract_previous_result(previous_result)

        if not previous_result.customized_nim:
            self.logger.info("No customized model to shutdown")
            return previous_result

        try:
            customized_nim_config = NIMConfig(**previous_result.customized_nim)
            self.logger.info(
                f"Shutting down customized model: {customized_nim_config.model_name} (type: {customized_nim_config.model_type})"
            )

            # Use DMSClient to shutdown the customized model
            dms_client = get_backend_provider().deployment_client(nim=customized_nim_config)
            dms_client.shutdown_deployment()

            self.logger.info(
                f"Successfully shut down customized model: {customized_nim_config.model_name}"
            )

        except Exception as e:
            error_msg = f"Error shutting down customized NIM: {e!s}"
            self.logger.error(error_msg)
            # Don't fail the whole workflow if cleanup fails
            previous_result.error = None  # Reset error to allow workflow to continue

        return previous_result
