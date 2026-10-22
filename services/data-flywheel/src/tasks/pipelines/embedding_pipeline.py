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

"""Embedding pipeline orchestrator."""

from celery import chain, group

from src.api.models import DatasetType
from src.config import EmbeddingDataSplitConfig, EmbeddingJobConfig, LLMJobConfig, NIMConfig
from src.tasks.pipelines.base import BasePipeline


class EmbeddingPipeline(BasePipeline):
    """Pipeline orchestrator for embedding workflows.

    This pipeline handles the embedding workflow:
    1. Initialize workflow (no LLM judge)
    2. Create datasets (using ES embeddings collection)
    3. For each embedding NIM:
        a. Spin up NIM
        b. Run base evaluation (retriever benchmark)
        c. In parallel:
           - Start customization
           - Prepare customized NIM config
           - Spin up customized NIM
           - Run customization evaluation
           - Shutdown customized NIM
        d. Shutdown base NIM
    4. Finalize workflow
    """

    def get_supported_job_types(self) -> list[str]:
        """Get the list of job types supported by this pipeline."""
        return ["embedding_job"]

    def build_workflow(
        self,
        workload_id: str,
        flywheel_run_id: str,
        client_id: str,
        job_type: str,
        job_config: LLMJobConfig | EmbeddingJobConfig,
        data_split_config: EmbeddingDataSplitConfig,
    ) -> chain:
        """Build the Celery workflow chain for the embedding pipeline.

        Args:
            workload_id: Unique identifier for this workload
            flywheel_run_id: ID of the FlywheelRun document
            client_id: ID of the client
            job_type: Type of job to run
            job_config: Job configuration object
            data_split_config: Configuration for data splitting (required)

        Returns:
            Celery chain representing the workflow
        """
        # Import tasks here to avoid circular imports
        from src.tasks.tasks import (
            create_embedding_datasets,
            finalize_flywheel_run,
            initialize_workflow,
            prepare_customized_nim,
            run_base_embedding_eval,
            run_embedding_customization_eval,
            shutdown_customized_nim,
            shutdown_deployment,
            spin_up_nim,
            start_embedding_customization,
        )

        # Validate configuration
        self.validate_config(job_type, job_config)

        # Convert data_split_config to dict for passing to Celery tasks
        split_config_dict = data_split_config.model_dump()
        custom_eval_datasets = (
            job_config.evaluation_config.custom_evaluations if job_config.evaluation_config else []
        )

        # Build evaluation tasks for custom evaluation datasets
        base_evaluations = [
            run_base_embedding_eval.s(
                dataset_type=DatasetType.CUSTOM, dataset_name=custom_eval.name
            )
            for custom_eval in custom_eval_datasets
        ]
        customized_evaluations = [
            run_embedding_customization_eval.s(
                dataset_type=DatasetType.CUSTOM, dataset_name=custom_eval.name
            )
            for custom_eval in custom_eval_datasets
        ]

        # Create a group of chains for each NIM
        nim_chains = []
        for nim in job_config.nims:
            assert isinstance(nim, NIMConfig)

            # Build customization evaluation group (always include default eval)
            cust_eval_tasks = [run_embedding_customization_eval.s(), *customized_evaluations]

            # Build the complete NIM chain
            nim_chain = chain(
                spin_up_nim.s(nim_config=nim.model_dump()),
                group(
                    chain(run_base_embedding_eval.s(), *base_evaluations),
                    # Customization workflow in parallel
                    chain(
                        start_embedding_customization.s(),
                        prepare_customized_nim.s(),
                        spin_up_nim.s(use_customized_nim=True),
                        *cust_eval_tasks,
                        shutdown_customized_nim.s(),
                    ),
                ),
                shutdown_deployment.s(),
            )
            nim_chains.append(nim_chain)

        # Create the complete workflow
        workflow = chain(
            initialize_workflow.s(
                workload_id=workload_id,
                flywheel_run_id=flywheel_run_id,
                client_id=client_id,
                job_type=job_type,
                job_config=job_config.model_dump(),
                data_split_config=split_config_dict,
            ),
            create_embedding_datasets.s(),
            chain(*nim_chains),
            finalize_flywheel_run.s(),
        )

        return workflow
