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

"""Handler for workflow initialization task."""

from bson import ObjectId

from src.api.models import (
    FlywheelRunStatus,
    LLMJudgeRun,
    NIMRun,
    NIMRunStatus,
    TaskResult,
)
from src.api.schemas import DeploymentStatus
from src.config import (
    DataSplitConfig,
    EmbeddingDataSplitConfig,
    EmbeddingJobConfig,
    LLMJobConfig,
    settings,
)
from src.tasks.base import BaseTaskHandler


class InitializeWorkflowHandler(BaseTaskHandler):
    """Handler for initializing the flywheel workflow."""

    def execute(
        self,
        workload_id: str,
        flywheel_run_id: str,
        client_id: str,
        job_type: str,
        job_config: dict,
        output_dataset_prefix: str = "",
        data_split_config: dict | None = None,
    ) -> TaskResult:
        """Initialize the workflow for the given workload.

        This task is the first task in the workflow. It is responsible for:
        - Updating the flywheel run status to running
        - Creating all NIM records and setting them to pending
        - Setting up the LLM judge run

        Args:
            workload_id: Unique identifier for this workload
            flywheel_run_id: ID of the FlywheelRun document
            client_id: ID of the client
            job_type: Type of job to run - either 'llm_job' or 'embedding_job'
            job_config: Job configuration dictionary (mandatory)
            output_dataset_prefix: Optional prefix for dataset names
            data_split_config: Optional configuration for data splitting

        Returns:
            TaskResult with initialized workflow
        """
        # Check for cancellation at the beginning
        self.check_cancellation(flywheel_run_id, raise_error=True)

        # Handle job_config - raise exception if None
        if job_config is None:
            raise ValueError("job_config is required and cannot be None")

        # Convert job_config from dict back to typed object
        if job_type == "llm_job":
            job_config_obj = LLMJobConfig(**job_config)
        elif job_type == "embedding_job":
            job_config_obj = EmbeddingJobConfig(**job_config)
        else:
            raise ValueError(f"Unsupported job config type: {job_type}")

        # Convert data_split_config to object if provided as dict
        if job_type == "embedding_job":
            split_config = (
                EmbeddingDataSplitConfig(**data_split_config) if data_split_config else None
            )
        else:
            split_config = DataSplitConfig(**data_split_config) if data_split_config else None

        # Update flywheel run status to running and set started_at timestamp
        self.db_manager.update_flywheel_run_status(flywheel_run_id, FlywheelRunStatus.RUNNING)
        self.db_manager.set_flywheel_started(flywheel_run_id)

        # Create LLM judge run only for LLM jobs (not for embedding jobs).
        # The judge config is global (read from settings), not backend-specific —
        # the judge itself is an NMP-only concern, validated at startup via
        # get_backend_provider().validate_llm_judge().
        llm_as_judge_cfg = None
        if job_type == "llm_job":
            llm_as_judge_cfg = settings.system_config.llm_judge_config

            # Create LLM judge run
            llm_judge_run = LLMJudgeRun(
                flywheel_run_id=ObjectId(flywheel_run_id),
                model_name=llm_as_judge_cfg.model_name,
                deployment_type=llm_as_judge_cfg.deployment_type,
                deployment_status=(
                    DeploymentStatus.READY
                    if llm_as_judge_cfg.is_remote
                    else DeploymentStatus.CREATED
                ),
            )

            # Insert using TaskDBManager
            llm_judge_run.id = self.db_manager.create_llm_judge_run(llm_judge_run)

        # Create NIM runs for each NIM
        for nim_config in job_config_obj.nims:
            nim_run = NIMRun(
                flywheel_run_id=ObjectId(flywheel_run_id),
                model_name=nim_config.model_name,
                evaluations=[],
                started_at=None,
                finished_at=None,
                runtime_seconds=0,
                status=NIMRunStatus.PENDING,
            )
            nim_run.id = self.db_manager.create_nim_run(nim_run)

        # Return the TaskResult with the initialized workflow
        return TaskResult(
            workload_id=workload_id,
            flywheel_run_id=flywheel_run_id,
            client_id=client_id,
            error=None,
            datasets={},
            llm_judge_config=llm_as_judge_cfg,
            data_split_config=split_config,
            job_config=job_config_obj,
        )
