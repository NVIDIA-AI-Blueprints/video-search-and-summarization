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

"""Base pipeline orchestrator."""

from abc import ABC, abstractmethod

from celery import chain

from src.api.db_manager import TaskDBManager
from src.config import DataSplitConfig, EmbeddingJobConfig, LLMJobConfig
from src.log_utils import setup_logging

logger = setup_logging("data_flywheel.pipelines.base")


class BasePipeline(ABC):
    """Base class for pipeline orchestrators.

    Each pipeline orchestrator is responsible for:
    1. Defining the workflow structure (DAG of tasks)
    2. Configuring task execution order
    3. Handling pipeline-specific logic

    This allows different pipeline types (LLM, Embedding, DPO) to have
    different task sequences while sharing common infrastructure.
    """

    def __init__(self, db_manager: TaskDBManager):
        """Initialize the pipeline orchestrator.

        Args:
            db_manager: Database manager for MongoDB operations
        """
        self.db_manager = db_manager
        self.logger = setup_logging(f"data_flywheel.pipelines.{self.__class__.__name__}")

    @abstractmethod
    def build_workflow(
        self,
        workload_id: str,
        flywheel_run_id: str,
        client_id: str,
        job_type: str,
        job_config: LLMJobConfig | EmbeddingJobConfig,
        data_split_config: DataSplitConfig,
    ) -> chain:
        """Build the Celery workflow chain for this pipeline.

        Args:
            workload_id: Unique identifier for this workload
            flywheel_run_id: ID of the FlywheelRun document
            client_id: ID of the client
            job_type: Type of job to run
            job_config: Job configuration object
            data_split_config: Configuration for data splitting (required, handled by DAG function)

        Returns:
            Celery chain representing the workflow
        """
        pass

    @abstractmethod
    def get_supported_job_types(self) -> list[str]:
        """Get the list of job types supported by this pipeline.

        Returns:
            List of supported job type strings
        """
        pass

    def validate_config(self, job_type: str, job_config: LLMJobConfig | EmbeddingJobConfig) -> None:
        """Validate the job configuration for this pipeline.

        Args:
            job_type: Type of job to run
            job_config: Job configuration object

        Raises:
            ValueError: If the configuration is invalid
        """
        if job_type not in self.get_supported_job_types():
            raise ValueError(
                f"Job type '{job_type}' is not supported by {self.__class__.__name__}. "
                f"Supported types: {self.get_supported_job_types()}"
            )
