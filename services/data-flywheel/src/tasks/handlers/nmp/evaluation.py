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

"""Handler for evaluation tasks."""

from datetime import datetime
from typing import Any

from src.api.db_manager import TaskDBManager
from src.api.models import (
    DatasetType,
    EvalType,
    EvaluationResult,
    NIMEvaluation,
    TaskResult,
    ToolEvalType,
    WorkloadClassification,
)
from src.backends.factory import get_backend_provider
from src.config import settings
from src.lib.integration.mlflow_client import MLflowClient
from src.tasks.base import PipelineTaskHandler


class EvaluationHandler(PipelineTaskHandler):
    """Handler for running evaluations on NIMs."""

    def execute(
        self, previous_result: TaskResult, eval_type: EvalType, dataset_type: DatasetType
    ) -> TaskResult:
        """Run evaluation against the NIM based on the eval_type.

        Args:
            previous_result: Result from the previous task
            eval_type: Type of evaluation (BASE, ICL, CUSTOMIZED)
            dataset_type: Type of dataset to use

        Returns:
            Updated TaskResult with evaluation results
        """
        # Skip if there's any error in the previous task
        if self.should_skip_stage(previous_result, f"run_{eval_type}_eval"):
            return previous_result

        # Check for cancellation
        if self.check_cancellation(previous_result.flywheel_run_id, raise_error=False):
            if previous_result and not previous_result.error:
                previous_result.error = (
                    f"Task cancelled for flywheel run {previous_result.flywheel_run_id}"
                )
            return previous_result

        # For customization evaluation, verify that a customized model exists
        if eval_type == EvalType.CUSTOMIZED:
            if not previous_result.customization or not previous_result.customization.model_name:
                msg = "No customized model available for evaluation"
                self.logger.error(msg)
                previous_result.error = f"Error running {eval_type} evaluation: {msg}"
                return previous_result

        self.logger.info(f"Running {eval_type} evaluation")

        # Get the judge model config from the previous result
        llm_judge_config = previous_result.llm_judge_config
        evaluator = get_backend_provider().evaluator(
            judge_model_config=llm_judge_config.judge_model_config()
        )
        start_time = datetime.utcnow()

        tool_eval_types = [None]
        if previous_result.workload_type == WorkloadClassification.TOOL_CALLING:
            # NMP 26.03 ``metric_jobs.create`` takes exactly one metric per job
            # (the legacy 25.12 task-with-many-metrics shape is gone). Dispatch
            # BOTH the accuracy metric (``function_name_accuracy``,
            # ``function_name_and_args_accuracy``) and the LLM-judge
            # correctness metric (``rating``) so we still surface both
            # families of scores. Each tool_eval_type produces its own
            # evaluation record; ``_extract_scores`` reads only the keys
            # that match the job's metric.
            tool_eval_types = [
                ToolEvalType.TOOL_CALLING_METRIC,
                ToolEvalType.TOOL_CALLING_JUDGE,
            ]

        jobs: list[dict[str, Any]] = []

        # Start evaluation for each tool evaluation type
        for tool_eval_type in tool_eval_types:
            # Find the NIM run for this model
            nim_run = self.db_manager.find_nim_run(
                previous_result.flywheel_run_id,
                previous_result.nim.model_name,
            )
            if not nim_run:
                msg = f"No NIM run found for model {previous_result.nim.model_name}"
                self.logger.error(msg)
                raise ValueError(msg)

            # Create evaluation document first
            evaluation = NIMEvaluation(
                nim_id=nim_run["_id"],
                eval_type=eval_type,
                dataset=previous_result.datasets[dataset_type],
                eval_name=None,  # Custom evaluations not yet supported for LLM
                scores={},
                started_at=start_time,
                finished_at=None,
                runtime_seconds=0.0,
                progress=0.0,
            )

            # Add evaluation to the database
            self.db_manager.insert_evaluation(evaluation)

            # Create progress callback with bound variables
            def make_progress_callback(manager: TaskDBManager, eval_instance):
                def callback(update_data):
                    """Update evaluation document with progress"""
                    current_time = datetime.utcnow()
                    update_data["runtime_seconds"] = (current_time - start_time).total_seconds()
                    manager.update_evaluation(eval_instance.id, update_data)

                return callback

            progress_callback = make_progress_callback(self.db_manager, evaluation)

            # Run the evaluation
            try:
                # Use customized model name for customization evaluation
                if eval_type == EvalType.CUSTOMIZED:
                    target_model = previous_result.customization.model_name
                    if previous_result.workload_type == WorkloadClassification.DPO:
                        ## for SFT deployed model, remove the @cust-xyz suffix if present
                        target_model = (
                            target_model.split("@")[0] if "@" in target_model else target_model
                        )
                else:
                    target_model = previous_result.nim.target_model_for_evaluation()

                # Use data_split_config from previous_result
                split_config = previous_result.data_split_config
                if not split_config:
                    raise ValueError("Data split config is required")

                job_id = evaluator.run_evaluation(
                    dataset_name=previous_result.datasets[dataset_type],
                    workload_type=previous_result.workload_type,
                    target_model=target_model,
                    test_file="eval_data.jsonl",
                    tool_eval_type=tool_eval_type,
                    limit=split_config.limit,
                )
                self.logger.info("Evaluation job id: %s", job_id)

                # Update uri in evaluation
                evaluation.nmp_uri = evaluator.get_job_uri(job_id)
                evaluation.job_id = job_id
                progress_callback({"nmp_uri": evaluation.nmp_uri, "job_id": job_id})

                jobs.append(
                    {
                        "job_id": job_id,
                        "evaluation": evaluation,
                        "progress_callback": progress_callback,
                        "tool_eval_type": tool_eval_type,
                    }
                )
            except Exception as e:
                error_msg = f"Error running {eval_type} evaluation: {e!s}"
                self.logger.error(error_msg)
                self.db_manager.update_evaluation(
                    evaluation.id,
                    {
                        "error": error_msg,
                        "finished_at": datetime.utcnow(),
                        "progress": 0.0,
                    },
                )
                previous_result.error = error_msg
                return previous_result

        # Wait for all jobs to complete
        for job in jobs:
            try:
                # Wait for the evaluation to complete
                evaluator.wait_for_evaluation(
                    job_id=job["job_id"],
                    flywheel_run_id=previous_result.flywheel_run_id,
                    polling_interval=5,
                    timeout=3600,
                    progress_callback=job["progress_callback"],
                )

                # Get final results
                results = evaluator.get_evaluation_results(job["job_id"])
                self.logger.info(results)

                # Extract scores based on workload type
                scores = self._extract_scores(results, previous_result.workload_type)

                # MLflow integration - upload results if enabled
                mlflow_uri = (
                    self._upload_to_mlflow(job, previous_result, eval_type, scores)
                    if settings.system_config.mlflow_config.enabled
                    else None
                )

                # Update evaluation with final results
                finished_time = datetime.utcnow()
                job["progress_callback"](
                    {
                        "scores": scores,
                        "finished_at": finished_time,
                        "runtime_seconds": (finished_time - start_time).total_seconds(),
                        "progress": 100.0,
                        "mlflow_uri": mlflow_uri,
                    }
                )

                previous_result.add_evaluation(
                    eval_type,
                    EvaluationResult(
                        job_id=job["job_id"],
                        scores=scores,
                        started_at=start_time,
                        finished_at=finished_time,
                        percent_done=100.0,
                    ),
                )

            except Exception as e:
                error_msg = f"Error running {eval_type} evaluation: {e!s}"
                self.logger.error(error_msg)
                self.db_manager.update_evaluation(
                    job["evaluation"].id,
                    {
                        "error": error_msg,
                        "finished_at": datetime.utcnow(),
                        "progress": 0.0,
                    },
                )
                previous_result.error = error_msg

        return previous_result

    def _extract_scores(
        self, results: dict, workload_type: WorkloadClassification
    ) -> dict[str, float]:
        """Extract scores from evaluation results based on workload type.

        Args:
            results: Raw evaluation results
            workload_type: Type of workload

        Returns:
            Dictionary of scores
        """
        scores: dict[str, float] = {}

        # NMP 26.03 metric_jobs return ``aggregate-scores`` as a flat list:
        #   {"scores": [{"name": "<metric>", "mean": <float>, ...}, ...]}
        # ``get_evaluation_results`` keeps the raw list under ``raw_aggregate``;
        # use that as the source of truth so we don't have to navigate the
        # synthesized legacy task/metric tree (whose keys differ per metric
        # type). Each name in the flat list is the score name we want.
        raw_scores = (results.get("raw_aggregate") or {}).get("scores") or []
        scores_by_name: dict[str, float | None] = {
            (s.get("name") or ""): (s.get("mean") if s.get("value") is None else s.get("value"))
            for s in raw_scores
            if s.get("name")
        }

        if workload_type == WorkloadClassification.TOOL_CALLING:
            # NMP 26.03 returns ``function_name_accuracy`` /
            # ``function_name_and_args_accuracy`` from the tool-calling metric,
            # and ``rating`` (or similar) from a paired LLM-judge correctness
            # metric. DFW's downstream consumers expect the legacy keys.
            if "function_name_accuracy" in scores_by_name:
                scores["function_name"] = scores_by_name["function_name_accuracy"]
            if "function_name_and_args_accuracy" in scores_by_name:
                scores["function_name_and_args_accuracy"] = scores_by_name[
                    "function_name_and_args_accuracy"
                ]
            # LLM-judge correctness on the same response (only present when
            # the judge metric is wired up).
            for k in ("rating", "correctness", "tool_calling_correctness"):
                if k in scores_by_name:
                    scores["tool_calling_correctness"] = scores_by_name[k]
                    break
        elif workload_type == WorkloadClassification.DPO:
            for k in ("preference_score", "preference-score", "preference_alignment"):
                if k in scores_by_name:
                    scores["preference_score"] = scores_by_name[k]
                    break
        else:
            # Standard LLM (completion) evaluation — LLM-as-judge ``similarity``.
            for k in ("similarity", "rating"):
                if k in scores_by_name:
                    scores["similarity"] = scores_by_name[k]
                    break

        return scores

    def _upload_to_mlflow(
        self, job: dict, previous_result: TaskResult, eval_type: EvalType, scores: dict
    ) -> str | None:
        """Upload evaluation results to MLflow if enabled.

        Args:
            job: Job information dictionary
            previous_result: Task result
            eval_type: Type of evaluation
            scores: Evaluation scores

        Returns:
            MLflow URI if successful, None otherwise
        """
        self.logger.info(
            f"MLflow integration enabled - uploading {eval_type} evaluation results for model {previous_result.nim.model_name}"
        )

        try:
            mlflow_client = MLflowClient(settings.system_config.mlflow_config)

            mlflow_uri = mlflow_client.upload_evaluation_results(
                job_id=job["job_id"],
                nmp_eval_uri=job["evaluation"].nmp_uri,
                flywheel_run_id=previous_result.flywheel_run_id,
                model_name=previous_result.nim.model_name,
                eval_type=eval_type.value,
                workload_type=previous_result.workload_type,
            )

            if mlflow_uri:
                self.logger.info(
                    f"Successfully uploaded {eval_type} evaluation to MLflow: {mlflow_uri}"
                )
            else:
                self.logger.warning(
                    f"MLflow upload completed but no URI returned for {eval_type} evaluation"
                )

            return mlflow_uri

        except Exception as mlflow_error:
            self.logger.error(f"MLflow upload failed for {eval_type} evaluation: {mlflow_error!s}")
            return None


class EmbeddingEvaluationHandler(PipelineTaskHandler):
    """Handler for running embedding evaluations on NIMs."""

    def _upload_embedding_to_mlflow(
        self, previous_result: TaskResult, eval_type: EvalType, scores: dict
    ) -> str | None:
        """Upload embedding evaluation results to MLflow if enabled.

        Args:
            previous_result: Task result
            eval_type: Type of evaluation
            scores: Evaluation scores

        Returns:
            MLflow URI if successful, None otherwise
        """
        self.logger.info(
            f"MLflow integration enabled - uploading {eval_type} embedding evaluation results for model {previous_result.nim.model_name}"
        )

        try:
            mlflow_client = MLflowClient(settings.system_config.mlflow_config)

            mlflow_uri = mlflow_client.upload_embedding_results(
                flywheel_run_id=previous_result.flywheel_run_id,
                model_name=previous_result.nim.model_name,
                eval_type=eval_type.value,
                scores=scores,
            )

            if mlflow_uri:
                self.logger.info(
                    f"Successfully uploaded {eval_type} embedding evaluation to MLflow: {mlflow_uri}"
                )
            else:
                self.logger.warning(
                    f"MLflow upload completed but no URI returned for {eval_type} embedding evaluation"
                )

            return mlflow_uri

        except Exception as mlflow_error:
            self.logger.error(
                f"MLflow upload failed for {eval_type} embedding evaluation: {mlflow_error!s}"
            )
            return None

    def execute(
        self,
        previous_result: TaskResult,
        eval_type: EvalType,
        use_customized: bool = False,
        dataset_type: DatasetType = DatasetType.BASE,
        dataset_name: str | None = None,
    ) -> TaskResult:
        """Run embedding evaluation using retriever benchmarks.

        Args:
            previous_result: Result from the previous task
            eval_type: Type of evaluation (BASE or CUSTOMIZED)
            use_customized: If True, evaluate the customized model

        Returns:
            Updated TaskResult with evaluation results
        """
        # Skip if there's any error in the previous task
        if self.should_skip_stage(previous_result, f"run_{eval_type}_embedding_eval"):
            return previous_result

        # Check for cancellation
        if self.check_cancellation(previous_result.flywheel_run_id, raise_error=False):
            if previous_result and not previous_result.error:
                previous_result.error = (
                    f"Task cancelled for flywheel run {previous_result.flywheel_run_id}"
                )
            return previous_result

        self.logger.info(f"Running {eval_type} embedding evaluation")

        start_time = datetime.utcnow()

        # Find the NIM run for this model
        nim_run = self.db_manager.find_nim_run(
            previous_result.flywheel_run_id,
            previous_result.nim.model_name,
        )
        if not nim_run:
            msg = f"No NIM run found for model {previous_result.nim.model_name}"
            self.logger.error(msg)
            raise ValueError(msg)

        # Determine which model to evaluate
        if use_customized:
            # Get the customized model details from customized_nim
            if not previous_result.customized_nim:
                self.logger.error("No customized model configuration found")
                previous_result.error = "No customized model configuration found"
                return previous_result
            customized_model_name = previous_result.customized_nim["model_name"]

            # For inference, strip the @cust-xyz suffix if present
            # e.g., "dfwbp/emb@cust-xyz" -> "dfwbp/emb"
            model_name = (
                customized_model_name.split("@")[0]
                if "@" in customized_model_name
                else customized_model_name
            )

            self.logger.info(f"Running customized embedding evaluation for {customized_model_name}")
            self.logger.info(f"Using inference model name: {model_name}")
        else:
            model_name = previous_result.nim.target_model_for_evaluation()

        # incase of custom evaluation, get the dataset name from the custom evaluation datasets
        if dataset_type == DatasetType.CUSTOM:
            eval_dataset_name = previous_result.datasets[dataset_type][dataset_name]
        else:
            eval_dataset_name = previous_result.datasets[dataset_type]

        # Create evaluation document
        evaluation = NIMEvaluation(
            nim_id=nim_run["_id"],
            eval_type=eval_type,
            dataset=eval_dataset_name,
            eval_name=dataset_name if dataset_type == DatasetType.CUSTOM else None,
            scores={},
            started_at=start_time,
            finished_at=None,
            runtime_seconds=0.0,
            progress=0.0,
        )

        # Add evaluation to the database
        self.db_manager.insert_evaluation(evaluation)

        # Create progress callback
        def progress_callback(update_data):
            """Update evaluation document with progress"""
            current_time = datetime.utcnow()
            update_data["runtime_seconds"] = (current_time - start_time).total_seconds()
            self.db_manager.update_evaluation(evaluation.id, update_data)

        # Run the evaluation
        try:
            evaluator = get_backend_provider().evaluator()

            # Get evaluation metrics (use defaults if not configured)
            eval_metrics = None
            if (
                previous_result.job_config.evaluation_config
                and previous_result.job_config.evaluation_config.embedding_eval_metrics
            ):
                eval_metrics = (
                    previous_result.job_config.evaluation_config.embedding_eval_metrics.metrics
                )

            # Start retriever evaluation job using built-in scidocs benchmark
            job_id = evaluator.run_retriever_evaluation(
                dataset_name=eval_dataset_name,
                test_file=None,  # Use benchmark instead of custom dataset
                model_name=model_name,
                namespace=settings.system_config.nmp_config.nmp_namespace,
                # benchmark="scidocs",  # Use scidocs benchmark
                metrics=eval_metrics,  # Will use defaults if None
                top_k=10,
            )

            self.logger.info(f"Embedding evaluation job id: {job_id}")

            # Update evaluation with job info
            evaluation.nmp_uri = evaluator.get_job_uri(job_id)
            evaluation.job_id = job_id
            progress_callback({"nmp_uri": evaluation.nmp_uri, "job_id": job_id})

            # Wait for evaluation to complete with progress updates
            evaluator.wait_for_evaluation(
                job_id=job_id,
                flywheel_run_id=previous_result.flywheel_run_id,
                polling_interval=10,
                timeout=3600,
                progress_callback=progress_callback,
            )

            # Get final results
            results = evaluator.get_evaluation_results(job_id)
            self.logger.info(f"Raw {eval_type} embedding evaluation results: {results}")

            # Parse embedding-specific results
            scores = evaluator.parse_retriever_evaluation_results(results)

            # MLflow integration - upload results if enabled
            mlflow_uri = (
                self._upload_embedding_to_mlflow(previous_result, eval_type, scores)
                if settings.system_config.mlflow_config.enabled
                else None
            )

            # Update final results in database
            finished_time = datetime.utcnow()
            progress_callback(
                {
                    "scores": scores,
                    "finished_at": finished_time,
                    "runtime_seconds": (finished_time - start_time).total_seconds(),
                    "progress": 100.0,
                    "mlflow_uri": mlflow_uri,
                }
            )

            self.logger.info(f"{eval_type} embedding evaluation completed with scores: {scores}")

            # Add evaluation to TaskResult
            previous_result.add_evaluation(
                eval_type,
                EvaluationResult(
                    job_id=job_id,
                    scores=scores,
                    started_at=start_time,
                    finished_at=finished_time,
                    runtime_seconds=(finished_time - start_time).total_seconds(),
                    progress=100.0,
                    nmp_uri=evaluation.nmp_uri,
                ),
            )

        except Exception as e:
            error_msg = f"Error running {eval_type} embedding evaluation: {e!s}"
            self.logger.error(error_msg)
            progress_callback(
                {
                    "error": error_msg,
                    "finished_at": datetime.utcnow(),
                    "progress": 0.0,
                }
            )
            previous_result.error = error_msg

        return previous_result
