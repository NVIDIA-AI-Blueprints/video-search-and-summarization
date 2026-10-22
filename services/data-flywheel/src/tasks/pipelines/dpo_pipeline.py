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

"""DPO (Direct Preference Optimization) pipeline orchestrator."""

from celery import chain, group

from src.config import DataSplitConfig, LLMJobConfig, NIMConfig
from src.tasks.pipelines.base import BasePipeline


class DPOPipeline(BasePipeline):
    """Pipeline orchestrator for DPO workflows.

    DPO is an LLM fine-tuning technique using preference pairs.
    The pipeline follows the same parallel pattern as LLM but with key differences:
    1. Uses DPO dataset creation (prompt, chosen_response, rejected_response)
    2. Customized DPO model needs to be spun up and shut down
    3. Evaluation uses DPO-specific LLM judge

    Workflow:
    1. Initialize workflow
    2. Create DPO datasets
    3. Wait for LLM judge
    4. For each NIM (base model):
        a. Spin up base NIM
        b. Run in parallel:
           - Base DPO evaluation
           - ICL evaluation
           - Customization chain:
             * Start DPO customization (fine-tuning)
             * Prepare customized model
             * Spin up customized DPO model
             * Run customized DPO evaluation
             * Shutdown customized deployment
        c. Shutdown base NIM
    5. Finalize workflow
    """

    def get_supported_job_types(self) -> list[str]:
        """Get the list of job types supported by this pipeline."""
        return ["llm_job"]  # DPO is a type of LLM job

    def build_workflow(
        self,
        workload_id: str,
        flywheel_run_id: str,
        client_id: str,
        job_type: str,
        job_config: LLMJobConfig,
        data_split_config: DataSplitConfig,
    ) -> chain:
        """Build the Celery workflow chain for the DPO pipeline.

        Args:
            workload_id: Unique identifier for this workload
            flywheel_run_id: ID of the FlywheelRun document
            client_id: ID of the client
            job_type: Type of job to run (should be "llm_job")
            job_config: LLM job configuration with training_type='dpo'
            data_split_config: Configuration for data splitting

        Returns:
            Celery chain representing the DPO workflow
        """
        # Import tasks here to avoid circular imports
        from src.tasks.tasks import (
            create_dpo_datasets,
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

        # Verify this is a DPO job
        if job_config.training_config.training_type != "dpo":
            raise ValueError(
                f"DPOPipeline requires training_type='dpo', "
                f"got '{job_config.training_config.training_type}'"
            )

        # Convert data_split_config to dict for passing to Celery tasks
        split_config_dict = data_split_config.model_dump()

        # DPO dataset creation
        if (
            job_config.icl_config.example_selection == "semantic_similarity"
            and job_config.icl_config.similarity_config.embedding_nim_config.deployment_type
            == "local"
        ):
            dataset_workflow = chain(
                spin_up_nim.s(
                    nim_config=job_config.icl_config.similarity_config.embedding_nim_config.model_dump(),
                ),
                create_dpo_datasets.s(),
            )
        else:
            dataset_workflow = create_dpo_datasets.s()

        # Create a group of chains for each NIM
        nim_chains = []
        for nim in job_config.nims:
            assert isinstance(nim, NIMConfig)

            # DPO uses the exact same pattern and tasks as LLM!
            # The only difference is workload_type=DPO set in create_dpo_datasets
            # This makes the evaluator automatically use DPO_JUDGE_PROMPT
            nim_chain = chain(
                # Spin up base model
                spin_up_nim.s(nim_config=nim.model_dump()),
                # Run evaluations and customization in parallel
                group(
                    run_base_eval.s(),  # Reuses LLM task, uses DPO prompt due to workload_type
                    run_icl_eval.s(),
                    chain(
                        # Customization sub-chain
                        start_customization.s(),
                        prepare_customized_nim.s(),
                        spin_up_nim.s(use_customized_nim=True),  # Spin up customized DPO model
                        run_customization_eval.s(),  # Reuses LLM task
                        shutdown_customized_nim.s(),
                    ),
                ),
                # Shutdown base deployment
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
