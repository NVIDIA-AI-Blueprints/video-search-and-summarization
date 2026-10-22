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

"""Handlers for customization tasks (LLM and Embedding)."""

import random
import string
from datetime import datetime

from src.api.models import DatasetType, NIMCustomization, TaskResult
from src.backends.factory import get_backend_provider
from src.config import NIMConfig
from src.log_utils import setup_logging
from src.tasks.base import PipelineTaskHandler

logger = setup_logging("data_flywheel.tasks.handlers.customization")


def generate_output_model_name(length: int = 3) -> str:
    """Generate a unique K8s-compliant identifier for customized model.

    K8s has a 63 char limit for resource names/labels. This generates a simple
    random ``length``-character lowercase alphanumeric identifier for each
    customization. The first character is always a letter — NMP 26.03's
    fileset/model-entity naming uses the regex
    ``^[a-z](?!.*--)[a-z0-9\\-@.+_]{1,62}(?<!-)$`` and the customizer's
    auto-derived fileset name (``<output>-<12hex>``) gets rejected with a 422
    if ``<output>`` starts with a digit (e.g. ``9x1``).

    Args:
        length: Length of unique ID (default: 3, must be >= 1)

    Returns:
        Random unique identifier (e.g., "x7k")
    """
    letters = string.ascii_lowercase
    rest = string.ascii_lowercase + string.digits
    output_model_name = random.choice(letters) + "".join(
        random.choice(rest) for _ in range(max(length - 1, 0))
    )

    logger.info(f"Generated unique output model name: {output_model_name}")

    return output_model_name


class CustomizationHandler(PipelineTaskHandler):
    """Handler for LLM NIM customization."""

    def execute(self, previous_result: TaskResult) -> TaskResult:
        """Start customization process for the LLM NIM.

        Args:
            previous_result: Result from the previous task containing workload_id and target_llm_model

        Returns:
            Updated TaskResult with customization results
        """
        # Check for cancellation
        if self.check_cancellation(previous_result.flywheel_run_id, raise_error=False):
            if previous_result and not previous_result.error:
                previous_result.error = (
                    f"Task cancelled for flywheel run {previous_result.flywheel_run_id}"
                )
            return previous_result

        # Skip customization if there is any error in the previous task
        if self.should_skip_stage(previous_result, "start_customization"):
            return previous_result

        # Skip customization if the NIM is not enabled for customization
        if not previous_result.nim.customization_enabled:
            self.logger.info(
                f"Customization skipped for {previous_result.nim.model_name} because it is using an external NIM"
            )
            return previous_result

        workload_id = previous_result.workload_id
        target_llm_model = previous_result.nim.model_name
        self.logger.info(
            f"Starting NIM customization for workload {workload_id} on model {target_llm_model}"
        )

        # Find the NIM run
        nim_run = self.db_manager.find_nim_run(
            previous_result.flywheel_run_id,
            previous_result.nim.model_name,
        )
        if not nim_run:
            msg = f"No NIM run found for model {target_llm_model}"
            self.logger.error(msg)
            raise ValueError(msg)

        start_time = datetime.utcnow()
        customizer = get_backend_provider().training()

        # Create customization document with training tracking fields
        customization = NIMCustomization(
            nim_id=nim_run["_id"],
            workload_id=workload_id,
            base_model=target_llm_model,
            customized_model=None,
            started_at=start_time,
            progress=0.0,
            epochs_completed=0,
            steps_completed=0,
        )

        # Add customization to database
        self.db_manager.insert_customization(customization)

        def progress_callback(update_data):
            """Update customization document with progress"""
            current_time = datetime.utcnow()
            update_data["runtime_seconds"] = (current_time - start_time).total_seconds()
            self.db_manager.update_customization(customization.id, update_data)

        # Generate Kubernetes-compliant output model name
        output_model_name = generate_output_model_name()
        self.logger.info(f"LLM customization output model name: {output_model_name}")

        try:
            # NMP 26.03 prepends ``job-fileset-`` to the customization job name and
            # enforces ``^[a-z][a-z0-9\-@.+_]{1,62}$`` on the resulting Kubernetes
            # object (effectively 63 chars total, leaving us ~51 chars for our suffix).
            # Sanitize the model id (``/`` / ``@`` are not allowed) and trim the
            # workload+model suffix so the final name fits the regex.
            safe_target_llm_model = (
                target_llm_model.replace("/", "-").replace("@", "-").replace(".", "-")
                if target_llm_model
                else ""
            )
            safe_workload_id = (workload_id or "").replace("/", "-").replace("@", "-")
            # Job names must be unique within a workspace (NMP returns 409 if a
            # previous run created the same name). NMP 26.03 also prepends
            # ``job-fileset-`` (12 chars) to the user-provided name before
            # validating against ``^[a-z][a-z0-9\-@.+_]{1,62}$`` (max 63 chars
            # total) with no consecutive ``--``. Cap+strip trailing dashes so
            # truncation doesn't leave the name ending in ``-`` (would produce
            # ``--`` once we re-append the run suffix).
            run_suffix = str(previous_result.flywheel_run_id)[-8:]
            suffix = f"{safe_workload_id}-{safe_target_llm_model}".strip("-")
            suffix = suffix[:37].rstrip("-")
            # Start customization job
            customization_job_id, customized_model = customizer.start_training_job(
                name=f"cust-{suffix}-{run_suffix}".lower(),
                base_model=previous_result.nim.model_name,
                output_model_name=output_model_name,
                dataset_name=previous_result.datasets[DatasetType.TRAIN],
                training_config=previous_result.job_config.training_config,
                nim_config=previous_result.nim,
                flywheel_run_id=previous_result.flywheel_run_id,
            )
            self.logger.info(f"Customization job id: {customization_job_id}")

            # Update uri in customization
            customization.nmp_uri = customizer.get_job_uri(customization_job_id)
            customization.job_id = customization_job_id
            self.db_manager.update_customization(
                customization.id,
                {
                    "nmp_uri": customization.nmp_uri,
                    "job_id": customization_job_id,
                },
            )

            # Update customization with model name
            progress_callback({"customized_model": customized_model})

            # Wait for completion with progress updates
            customizer.wait_for_customization(
                customization_job_id,
                flywheel_run_id=previous_result.flywheel_run_id,
                progress_callback=progress_callback,
            )

            # Wait for model sync only for LoRA fine-tuning
            if previous_result.job_config.training_config.finetuning_type == "lora":
                customizer.wait_for_model_sync(
                    flywheel_run_id=previous_result.flywheel_run_id,
                    customized_model=customized_model,
                )

            # Update completion status
            finished_time = datetime.utcnow()
            final_update = {
                "finished_at": finished_time,
                "runtime_seconds": (finished_time - start_time).total_seconds(),
                "progress": 100.0,
            }
            progress_callback(final_update)

            # Final TaskResult update
            previous_result.update_customization(
                job_id=customization_job_id,
                model_name=customized_model,
                started_at=start_time,
                finished_at=finished_time,
                percent_done=100.0,
            )

        except Exception as e:
            error_msg = f"Error starting customization: {e!s}"
            self.logger.error(error_msg)
            self.db_manager.update_customization(
                customization.id,
                {
                    "error": error_msg,
                    "finished_at": datetime.utcnow(),
                    "progress": 0.0,
                },
            )
            try:
                customizer.cancel_job(customization_job_id)
            except Exception as e:
                self.logger.error(f"Error cancelling customization job: {e}")
            previous_result.error = error_msg

        return previous_result


class EmbeddingCustomizationHandler(PipelineTaskHandler):
    """Handler for embedding NIM customization."""

    def execute(self, previous_result: TaskResult | list[TaskResult]) -> TaskResult:
        """Start customization process for the embedding NIM.

        Args:
            previous_result: Result from the previous task (can be a list from group)

        Returns:
            Updated TaskResult with customization results
        """
        # Extract TaskResult from group results if needed
        previous_result = self.extract_previous_result(previous_result)

        # Check for cancellation
        if self.check_cancellation(previous_result.flywheel_run_id, raise_error=False):
            if previous_result and not previous_result.error:
                previous_result.error = (
                    f"Task cancelled for flywheel run {previous_result.flywheel_run_id}"
                )
            return previous_result

        # Skip customization if there is any error in the previous task
        if self.should_skip_stage(previous_result, "start_embedding_customization"):
            return previous_result

        # Skip customization if the NIM is not enabled for customization
        if not previous_result.nim.customization_enabled:
            self.logger.info(
                f"Customization skipped for {previous_result.nim.model_name} because customization is disabled"
            )
            return previous_result

        workload_id = previous_result.workload_id
        target_embedding_model = previous_result.nim.model_name
        self.logger.info(
            f"Starting embedding NIM customization for workload {workload_id} on model {target_embedding_model}"
        )

        # Find the NIM run
        nim_run = self.db_manager.find_nim_run(
            previous_result.flywheel_run_id,
            previous_result.nim.model_name,
        )
        if not nim_run:
            msg = f"No NIM run found for model {target_embedding_model}"
            self.logger.error(msg)
            raise ValueError(msg)

        start_time = datetime.utcnow()
        customizer = get_backend_provider().training()

        # Create customization document
        customization = NIMCustomization(
            nim_id=nim_run["_id"],
            workload_id=workload_id,
            base_model=target_embedding_model,
            customized_model=None,
            started_at=start_time,
            progress=0.0,
            epochs_completed=0,
            steps_completed=0,
        )

        # Add customization to database
        self.db_manager.insert_customization(customization)

        def progress_callback(update_data):
            """Update customization document with progress"""
            current_time = datetime.utcnow()
            update_data["runtime_seconds"] = (current_time - start_time).total_seconds()
            self.db_manager.update_customization(customization.id, update_data)

        # Generate Kubernetes-compliant output model name
        output_model_name = generate_output_model_name()
        self.logger.info(f"Embedding customization output model name: {output_model_name}")

        try:
            # NMP 26.03 prepends ``job-fileset-`` to the customization job name and
            # enforces ``^[a-z][a-z0-9\-@.+_]{1,62}$`` (see LLM branch comment for
            # the budgeting). Sanitize + trim so the final name fits.
            safe_target_embedding_model = (
                target_embedding_model.replace("/", "-").replace("@", "-").replace(".", "-")
                if target_embedding_model
                else ""
            )
            safe_workload_id_emb = (workload_id or "").replace("/", "-").replace("@", "-")
            run_suffix_emb = str(previous_result.flywheel_run_id)[-8:]
            suffix = f"{safe_workload_id_emb}-{safe_target_embedding_model}".strip("-")
            # ``emcust-`` (7) + suffix + ``-`` (1) + run_suffix (8) ≤ 51.
            # Strip trailing ``-`` after truncation so we don't end up with
            # consecutive ``--`` (NMP rejects the resulting K8s object name).
            suffix = suffix[:35].rstrip("-")
            # Start embedding customization job using start_training_job with job_type parameter
            customization_job_id, customized_model = customizer.start_training_job(
                name=f"emcust-{suffix}-{run_suffix_emb}".lower(),
                base_model=previous_result.nim.model_name,
                output_model_name=output_model_name,
                dataset_name=previous_result.datasets[DatasetType.TRAIN],
                training_config=previous_result.job_config.training_config,
                nim_config=previous_result.nim,
                flywheel_run_id=previous_result.flywheel_run_id,
            )

            self.logger.info(f"Embedding customization job id: {customization_job_id}")

            # Update customization with job info
            customization.nmp_uri = customizer.get_job_uri(customization_job_id)
            customization.job_id = customization_job_id
            self.db_manager.update_customization(
                customization.id,
                {
                    "nmp_uri": customization.nmp_uri,
                    "job_id": customization_job_id,
                    "customized_model": customized_model,  # training model name
                },
            )

            # Wait for completion with progress updates
            customizer.wait_for_customization(
                customization_job_id,
                flywheel_run_id=previous_result.flywheel_run_id,
                check_interval=10,  # Check every 10 seconds
                timeout=7200,  # 2 hours for embedding training
                progress_callback=progress_callback,
            )

            # Update completion status
            finished_time = datetime.utcnow()
            final_update = {
                "finished_at": finished_time,
                "runtime_seconds": (finished_time - start_time).total_seconds(),
                "progress": 100.0,
            }
            progress_callback(final_update)

            # Update TaskResult with customization info
            previous_result.update_customization(
                job_id=customization_job_id,
                model_name=customized_model,  # Store training model name
                started_at=start_time,
                finished_at=finished_time,
                percent_done=100.0,
            )

        except Exception as e:
            error_msg = f"Error starting embedding customization: {e!s}"
            self.logger.error(error_msg)
            self.db_manager.update_customization(
                customization.id,
                {
                    "error": error_msg,
                    "finished_at": datetime.utcnow(),
                    "progress": 0.0,
                },
            )
            try:
                customizer.cancel_job(customization_job_id)
            except Exception as cancel_err:
                self.logger.error(f"Error cancelling customization job: {cancel_err}")
            previous_result.error = error_msg

        return previous_result


class PrepareCustomizedNIMHandler(PipelineTaskHandler):
    """Handler for preparing a customized NIM for deployment/evaluation.

    This creates the inference-compatible model configuration needed for evaluation.
    Works for both embedding and LLM (DPO) customized models.
    """

    def execute(self, previous_result: TaskResult) -> TaskResult:
        """Prepare customized NIM configuration for deployment.

        Args:
            previous_result: Result from the previous task

        Returns:
            Updated TaskResult with customized NIM config
        """
        # Check for cancellation
        if self.check_cancellation(previous_result.flywheel_run_id, raise_error=False):
            if previous_result and not previous_result.error:
                previous_result.error = (
                    f"Task cancelled for flywheel run {previous_result.flywheel_run_id}"
                )
            return previous_result

        # Skip if there's any error in the previous task
        if self.should_skip_stage(previous_result, "prepare_customized_nim"):
            return previous_result

        # Skip if no customization was performed
        if not previous_result.customization or not previous_result.customization.model_name:
            self.logger.info("No customization available, skipping preparation step")
            return previous_result

        customized_model_name = previous_result.customization.model_name
        base_nim_config = previous_result.nim

        self.logger.info(
            f"Preparing NIMConfig for customized model: {customized_model_name} (type: {base_nim_config.model_type})"
        )

        try:
            # Update the existing NIMConfig with the customized model name
            # Keep all other settings the same (tag, gpus, pvc_size, etc.)
            customized_nim_config = NIMConfig(
                model_name=customized_model_name,
                tag=base_nim_config.tag,
                context_length=base_nim_config.context_length,
                gpus=base_nim_config.gpus,
                pvc_size=base_nim_config.pvc_size,
                registry_base=base_nim_config.registry_base,
                customization_enabled=False,
                model_type=base_nim_config.model_type,
                nim_image_model_name=base_nim_config.nim_image_model_name,
                base_model_for_image=base_nim_config.model_name,
            )

            previous_result.customized_nim = customized_nim_config.model_dump()

            self.logger.info(
                f"Stored customized NIM config for deployment: {customized_model_name}"
            )

        except Exception as e:
            error_msg = f"Error preparing customized NIM: {e!s}"
            self.logger.error(error_msg)
            previous_result.error = error_msg

        return previous_result
