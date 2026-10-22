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

"""LLM pipeline orchestrator."""

from celery import chain, group

from src.config import DataSplitConfig, EmbeddingJobConfig, LLMJobConfig, NIMConfig
from src.tasks.pipelines.base import BasePipeline


class LLMPipeline(BasePipeline):
    """Pipeline orchestrator for LLM workflows.

    This pipeline handles the standard LLM workflow:
    1. Initialize workflow
    2. Optionally spin up embedding NIM for semantic similarity
    3. Create datasets
    4. Wait for LLM judge
    5. For each NIM:
        a. Spin up NIM
        b. Run base evaluation
        c. Run ICL evaluation
        d. Run customization (if enabled)
        e. Run customization evaluation (if customization succeeded)
        f. Shutdown NIM
    6. Finalize workflow
    """

    def get_supported_job_types(self) -> list[str]:
        """Get the list of job types supported by this pipeline."""
        return ["llm_job"]

    def build_workflow(
        self,
        workload_id: str,
        flywheel_run_id: str,
        client_id: str,
        job_type: str,
        job_config: LLMJobConfig | EmbeddingJobConfig,
        data_split_config: DataSplitConfig,
    ) -> chain:
        """Build the Celery workflow chain for the LLM pipeline.

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
            create_datasets,
            finalize_flywheel_run,
            initialize_workflow,
            prepare_customized_nim,
            run_base_eval,
            run_customization_eval,
            run_icl_eval,
            shutdown_customized_nim,
            shutdown_deployment,
            spin_up_nim,
            start_customization,
            wait_for_llm_as_judge,
        )

        # Validate configuration
        self.validate_config(job_type, job_config)

        # Convert data_split_config to dict for passing to Celery tasks
        split_config_dict = data_split_config.model_dump()

        # Create dataset workflow chain (conditionally includes embedding workflow)
        if (
            job_config.icl_config.example_selection == "semantic_similarity"
            and job_config.icl_config.similarity_config.embedding_nim_config.deployment_type
            == "local"
        ):
            dataset_workflow = chain(
                spin_up_nim.s(
                    nim_config=job_config.icl_config.similarity_config.embedding_nim_config.model_dump(),
                ),
                create_datasets.s(),
            )
        else:
            dataset_workflow = create_datasets.s()

        # Customization sub-chain shape depends on finetuning_type:
        # - ``lora_merged`` / ``all_weights`` produce a standalone top-level
        #   model entity that must be served by a fresh NIM before
        #   ``run_customization_eval`` can hit it (same pattern as DPO).
        # - plain ``lora`` produces an adapter attached to the base entity's
        #   ``adapters[]`` list; on NMP 26.03 the base NIM is expected to
        #   auto-load it, so no extra spin-up is needed (and would fail —
        #   the adapter entity has no full weights).
        ft = getattr(job_config.training_config, "finetuning_type", None)
        needs_customized_nim_deploy = ft in ("lora_merged", "all_weights")

        # Create a group of chains for each NIM
        nim_chains = []
        for nim in job_config.nims:
            assert isinstance(nim, NIMConfig)
            if needs_customized_nim_deploy:
                customization_chain = chain(
                    start_customization.s(),
                    prepare_customized_nim.s(),
                    spin_up_nim.s(use_customized_nim=True),
                    run_customization_eval.s(),
                    shutdown_customized_nim.s(),
                )
            else:
                customization_chain = chain(
                    start_customization.s(),
                    run_customization_eval.s(),
                )

            # For each NIM, create a chain: spin_up_nim -> parallel_evals
            nim_chain = chain(
                spin_up_nim.s(nim_config=nim.model_dump()),
                group(
                    run_base_eval.s(),
                    run_icl_eval.s(),
                    customization_chain,
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
            dataset_workflow,
            wait_for_llm_as_judge.s(),
            chain(*nim_chains),
            finalize_flywheel_run.s(),
        )

        return workflow
