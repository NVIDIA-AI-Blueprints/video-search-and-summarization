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

"""Handler for dataset creation task."""

import sys
from datetime import datetime

from src.api.models import NIMRunStatus, TaskResult, WorkloadClassification
from src.backends.factory import get_backend_provider
from src.config import CustomEvaluation, DataSplitConfig
from src.lib.flywheel.cancellation import FlywheelCancelledError
from src.lib.flywheel.util import identify_workload_type
from src.lib.integration.dataset_creator import (
    DatasetCreator,
    DPODatasetCreator,
    EmbeddingDatasetCreator,
)
from src.lib.integration.es_client import (
    ES_DPO_DATA_COLLECTION_NAME,
    ES_EMBEDDINGS_DATA_COLLECTION_NAME,
    delete_embeddings_index,
    get_es_client,
)
from src.lib.integration.record_exporter import RecordExporter
from src.tasks.base import PipelineTaskHandler


class CreateDatasetsHandler(PipelineTaskHandler):
    """Handler for creating datasets for the flywheel workflow."""

    def execute(self, previous_result: TaskResult) -> TaskResult:
        """Create datasets for the given workload.

        Args:
            previous_result: Result from the previous task

        Returns:
            Updated TaskResult with datasets
        """
        previous_result = self.extract_previous_result(previous_result)

        index_name = None

        try:
            self.check_cancellation(previous_result.flywheel_run_id, raise_error=True)

            split_config = previous_result.data_split_config
            if split_config is None:
                raise ValueError("Data split config is required")

            # Export records from the database
            records = RecordExporter().get_records(
                previous_result.client_id, previous_result.workload_id, split_config
            )

            # Identify workload type
            workload_type = identify_workload_type(records)

            # Get icl_config - will be None for embedding jobs
            icl_config = getattr(previous_result.job_config, "icl_config", None)

            # Create datasets
            index_name, datasets = DatasetCreator(
                records,
                previous_result.flywheel_run_id,
                "",
                previous_result.workload_id,
                previous_result.client_id,
                split_config=split_config,
                icl_config=icl_config,
            ).create_datasets(workload_type)

            # Update the existing TaskResult with new data
            previous_result.workload_type = workload_type
            previous_result.datasets = datasets

            return previous_result

        except Exception as e:
            error_msg = f"Error creating datasets: {e!s}"
            self.logger.error(error_msg)

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
            raise

        finally:
            # Cleanup: shutdown the deployment after creating the datasets
            # Only for LLM jobs with semantic similarity ICL selection
            icl_config = getattr(previous_result.job_config, "icl_config", None)
            if (
                previous_result.job_config
                and icl_config is not None
                and icl_config.example_selection == "semantic_similarity"
            ):
                # Remove the embedding index
                if index_name:
                    try:
                        es_client = get_es_client()
                        delete_embeddings_index(es_client, index_name)
                    except Exception as cleanup_e:
                        self.logger.error(f"Error deleting embeddings index: {cleanup_e}")
                        # Only raise if no other exception is being handled
                        if sys.exc_info()[0] is None:
                            raise

                # Shutdown local embedding NIM if used
                if icl_config.similarity_config.embedding_nim_config.deployment_type == "local":
                    nim_config = icl_config.similarity_config.embedding_nim_config
                    dms_client = get_backend_provider().deployment_client(nim=nim_config)
                    self.logger.info(f"Shutting down the embedding NIM: {nim_config.model_name}")
                    try:
                        dms_client.shutdown_deployment()
                    except Exception as e:
                        self.logger.error(
                            f"Error shutting down the embedding NIM {nim_config.model_name}: {e}"
                        )
                        # Only raise if no other exception is being handled
                        if sys.exc_info()[0] is None:
                            previous_result.error = str(e)
                            raise


class CreateEmbeddingDatasetsHandler(PipelineTaskHandler):
    """Handler for creating datasets for embedding fine-tuning workflow."""

    def process_custom_eval_datasets(
        self, custom_evaluations: list[CustomEvaluation]
    ) -> list[dict]:
        eval_datasets = []
        self.logger.info(f"Processing {len(custom_evaluations)} custom evaluations")

        for custom_eval in custom_evaluations:
            self.logger.info(f"Loading evaluation dataset: {custom_eval.name}")

            # Pull records and similar records for evaluation dataset
            # pull all records from the evaluation dataset
            eval_records = RecordExporter().get_records(
                custom_eval.client_id,
                custom_eval.workload_id,
                split_config=DataSplitConfig(),
                index_name=ES_EMBEDDINGS_DATA_COLLECTION_NAME,
                multiplier=5,
            )

            eval_similar_records = RecordExporter().get_similar_records(
                custom_eval.client_id,
                custom_eval.workload_id,
            )

            eval_datasets.append(
                {
                    "name": custom_eval.name,
                    "workload_id": custom_eval.workload_id,
                    "client_id": custom_eval.client_id,
                    "records": eval_records,
                    "similar_records": eval_similar_records,
                }
            )
        return eval_datasets

    def execute(self, previous_result: TaskResult) -> TaskResult:
        """Create datasets for embedding fine-tuning.

        Args:
            previous_result: Result from the previous task

        Returns:
            Updated TaskResult with datasets
        """
        previous_result = self.extract_previous_result(previous_result)

        try:
            self.check_cancellation(previous_result.flywheel_run_id, raise_error=True)

            split_config = previous_result.data_split_config
            if split_config is None:
                raise ValueError("Data split config is required")

            self.logger.info(
                f"Creating embedding datasets for workload {previous_result.workload_id}"
            )

            # For embedding jobs, use the embeddings collection
            records = RecordExporter().get_records(
                previous_result.client_id,
                previous_result.workload_id,
                split_config,
                index_name=ES_EMBEDDINGS_DATA_COLLECTION_NAME,
                multiplier=5,
            )

            similar_records = None
            self.logger.info("Retrieving similar records...")
            try:
                similar_records = RecordExporter().get_similar_records(
                    previous_result.client_id,
                    previous_result.workload_id,
                )
                self.logger.info(f"Retrieved {len(similar_records)} similar records")
            except Exception as e:
                self.logger.error(f"Failed to retrieve similar records: {e}")
                raise

            # Process custom evaluations if provided
            evaluation_config = getattr(previous_result.job_config, "evaluation_config", None)
            custom_evaluations = (
                getattr(evaluation_config, "custom_evaluations", []) if evaluation_config else []
            )
            eval_datasets = self.process_custom_eval_datasets(custom_evaluations)

            # Create embedding datasets
            datasets = EmbeddingDatasetCreator(
                records,
                previous_result.flywheel_run_id,
                "",  # Using empty prefix for now
                previous_result.workload_id,
                previous_result.client_id,
                split_config=split_config,
            ).create_embedding_datasets(
                similar_records=similar_records, eval_datasets=eval_datasets
            )

            # Update the existing TaskResult with datasets
            previous_result.datasets = datasets

            self.logger.info("Successfully created embedding datasets")

            return previous_result

        except Exception as e:
            error_msg = f"Error creating embedding datasets: {e!s}"
            self.logger.error(error_msg)

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
            raise e


class CreateDPODatasetsHandler(PipelineTaskHandler):
    """Handler for creating DPO (Direct Preference Optimization) datasets.

    DPO datasets contain preference pairs (prompt, chosen_response, rejected_response)
    and support ICL with DPO-formatted examples.
    """

    def execute(self, previous_result: TaskResult) -> TaskResult:
        """Create DPO datasets for training and evaluation.

        Args:
            previous_result: Result from the previous task

        Returns:
            Updated TaskResult with datasets
        """
        previous_result = self.extract_previous_result(previous_result)
        index_name = None  # Initialize to None for finally block

        try:
            self.check_cancellation(previous_result.flywheel_run_id, raise_error=True)

            split_config = previous_result.data_split_config
            if split_config is None:
                raise ValueError("Data split config is required")

            # Get ICL config from job config
            icl_config = getattr(previous_result.job_config, "icl_config", None)

            # Export DPO records from Elasticsearch using generic RecordExporter
            records = RecordExporter().get_records(
                previous_result.client_id,
                previous_result.workload_id,
                split_config,
                index_name=ES_DPO_DATA_COLLECTION_NAME,
            )

            # Create DPO datasets with ICL support
            index_name, datasets = DPODatasetCreator(
                records,
                previous_result.flywheel_run_id,
                "",
                previous_result.workload_id,
                previous_result.client_id,
                split_config=split_config,
                icl_config=icl_config,
            ).create_dpo_datasets()

            # Update the existing TaskResult with new data
            # Set workload_type to DPO so evaluator automatically uses DPO_JUDGE_PROMPT
            previous_result.workload_type = WorkloadClassification.DPO
            previous_result.datasets = datasets

            return previous_result

        except Exception as e:
            error_msg = f"Error creating DPO datasets: {e!s}"
            self.logger.error(error_msg)

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
            raise

        finally:
            # Cleanup: shutdown the deployment after creating the datasets
            # Only for LLM jobs with semantic similarity ICL selection
            icl_config = getattr(previous_result.job_config, "icl_config", None)
            if (
                previous_result.job_config
                and icl_config is not None
                and icl_config.example_selection == "semantic_similarity"
            ):
                # Remove the embedding index
                if index_name:
                    try:
                        es_client = get_es_client()
                        delete_embeddings_index(es_client, index_name)
                    except Exception as cleanup_e:
                        self.logger.error(f"Error deleting embeddings index: {cleanup_e}")
                        # Only raise if no other exception is being handled
                        if sys.exc_info()[0] is None:
                            raise

                # Shutdown local embedding NIM if used
                if icl_config.similarity_config.embedding_nim_config.deployment_type == "local":
                    nim_config = icl_config.similarity_config.embedding_nim_config
                    dms_client = get_backend_provider().deployment_client(nim=nim_config)
                    self.logger.info(f"Shutting down the embedding NIM: {nim_config.model_name}")
                    try:
                        dms_client.shutdown_deployment()
                    except Exception as e:
                        self.logger.error(
                            f"Error shutting down the embedding NIM {nim_config.model_name}: {e}"
                        )
                        # Only raise if no other exception is being handled
                        if sys.exc_info()[0] is None:
                            previous_result.error = str(e)
                            raise
