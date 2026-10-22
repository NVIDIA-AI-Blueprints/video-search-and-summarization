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

"""Tests for trigger executor."""

from datetime import datetime
from unittest.mock import patch

from bson import ObjectId

from src.api.schemas import FlywheelRunStatus
from src.tasks.triggers.executor import TriggerExecutor


class TestTriggerExecutorBasic:
    """Basic tests for TriggerExecutor."""

    def test_executor_initialization(self, mock_trigger_db):
        """Test executor initializes correctly."""
        executor = TriggerExecutor(mock_trigger_db)

        assert executor.db == mock_trigger_db
        assert executor.model_resolver is not None
        assert executor.date_window_calc is not None

    def test_check_existing_execution_none(
        self, mock_trigger_db, sample_data_threshold_trigger_model
    ):
        """Test no existing execution."""
        executor = TriggerExecutor(mock_trigger_db)

        mock_trigger_db.trigger_executions.find_one.return_value = None

        result = executor._check_existing_execution(str(sample_data_threshold_trigger_model.id))

        assert result is None

    def test_check_existing_execution_running(
        self, mock_trigger_db, sample_data_threshold_trigger_model
    ):
        """Test existing running execution."""
        executor = TriggerExecutor(mock_trigger_db)

        existing = {
            "_id": ObjectId(),
            "trigger_id": str(sample_data_threshold_trigger_model.id),
            "status": "running",
            "execution_time": datetime.utcnow(),
        }

        mock_trigger_db.trigger_executions.find_one.return_value = existing
        mock_trigger_db.flywheel_runs.find_one.return_value = None

        result = executor._check_existing_execution(str(sample_data_threshold_trigger_model.id))

        assert result is not None
        assert result["status"] == "running"


class TestDataThresholdExecution:
    """Tests for data threshold trigger execution."""

    @patch("src.tasks.triggers.executor.get_available_records_count", return_value=200)
    @patch(
        "src.tasks.triggers.executor.TriggerExecutor._get_last_successful_training_date",
        return_value=None,
    )
    @patch("src.tasks.triggers.executor.TriggerExecutor._trigger_data_ingestion", return_value=200)
    @patch("src.tasks.triggers.executor.TriggerExecutor._run_job")
    def test_data_threshold_triggers_training(
        self,
        mock_run_job,
        mock_ingestion,
        mock_last_training,
        mock_records_count,
        mock_trigger_db,
        sample_data_threshold_trigger_model,
        sample_config_with_nims,
    ):
        """Test data threshold triggers training when threshold met."""
        executor = TriggerExecutor(mock_trigger_db)

        # Mock config
        mock_trigger_db.configurations.find_one.return_value = sample_config_with_nims

        # No existing execution
        mock_trigger_db.trigger_executions.find_one.return_value = None

        # Mock job run
        job_id = str(ObjectId())
        mock_run_job.return_value = job_id

        # Mock job completion
        mock_trigger_db.flywheel_runs.find_one.return_value = {
            "_id": ObjectId(job_id),
            "status": FlywheelRunStatus.COMPLETED,
        }

        execution = executor.execute(sample_data_threshold_trigger_model)

        assert execution.status == "completed"
        assert execution.triggered_training is True
        assert mock_run_job.called

    @patch("src.tasks.triggers.executor.get_available_records_count", return_value=200)
    @patch(
        "src.tasks.triggers.executor.TriggerExecutor._get_last_successful_training_date",
        return_value=None,
    )
    @patch("src.tasks.triggers.executor.TriggerExecutor._trigger_data_ingestion", return_value=200)
    @patch("src.tasks.triggers.executor.TriggerExecutor._wait_for_job")
    @patch("src.tasks.triggers.executor.TriggerExecutor._run_job")
    def test_data_threshold_timeout(
        self,
        mock_run_job,
        mock_wait_for_job,
        mock_ingestion,
        mock_last_training,
        mock_records_count,
        mock_trigger_db,
        sample_data_threshold_trigger_model,
        sample_config_with_nims,
    ):
        """Test data threshold with job timeout."""
        executor = TriggerExecutor(mock_trigger_db)

        # Mock config
        mock_trigger_db.configurations.find_one.return_value = sample_config_with_nims

        # No existing execution
        mock_trigger_db.trigger_executions.find_one.return_value = None

        # Mock job run to return a valid job ID
        job_id = str(ObjectId())
        mock_run_job.return_value = job_id

        # Mock wait_for_job to raise timeout
        mock_wait_for_job.side_effect = TimeoutError("Job timed out after 24 hours")

        execution = executor.execute(sample_data_threshold_trigger_model)

        assert execution.status == "failed"
        assert "timed out" in execution.error.lower()


class TestMetricDegradationExecution:
    """Tests for metric degradation trigger execution."""

    @patch("src.tasks.triggers.executor.TriggerExecutor._run_job")
    def test_metric_degradation_all_pass(
        self,
        mock_run_job,
        mock_trigger_db,
        sample_metric_degradation_trigger_model,
        sample_config_with_nims,
        sample_evaluation_result,
    ):
        """Test metric degradation when all NIMs pass thresholds."""
        executor = TriggerExecutor(mock_trigger_db)

        # Mock config
        mock_trigger_db.configurations.find_one.return_value = sample_config_with_nims

        # No existing execution
        mock_trigger_db.trigger_executions.find_one.return_value = None

        # Mock eval job
        eval_job_id = str(ObjectId())
        mock_run_job.return_value = eval_job_id

        # Mock eval job completion
        mock_trigger_db.flywheel_runs.find_one.return_value = {
            "_id": ObjectId(eval_job_id),
            "status": FlywheelRunStatus.COMPLETED,
        }

        # Mock NIMs for the job
        nim1_id = ObjectId()
        nim2_id = ObjectId()
        mock_trigger_db.nims.find.return_value = [
            {"_id": nim1_id, "model_name": "meta/llama-3.2-1b-instruct"},
            {"_id": nim2_id, "model_name": "meta/llama-3.2-3b-instruct"},
        ]

        # Mock evaluation results - both pass thresholds
        def mock_eval_find_one(query):
            nim_id = query.get("nim_id")
            if nim_id == nim1_id:
                return {"scores": {"accuracy": 0.90, "f1_score": 0.85}, "error": None}
            elif nim_id == nim2_id:
                return {"scores": {"accuracy": 0.88, "f1_score": 0.82}, "error": None}
            return None

        mock_trigger_db.evaluations.find_one.side_effect = mock_eval_find_one

        execution = executor.execute(sample_metric_degradation_trigger_model)

        assert execution.status == "skipped"  # Skipped because all NIMs passed thresholds
        assert execution.triggered_training is False  # No training needed
        assert execution.nims_failed == 0
        assert all(r.passed_thresholds for r in execution.nim_results)

    @patch("src.tasks.triggers.executor.TriggerExecutor._run_job")
    def test_metric_degradation_selective_retraining(
        self,
        mock_run_job,
        mock_trigger_db,
        sample_metric_degradation_trigger_model,
        sample_config_with_nims,
        sample_evaluation_result,
    ):
        """Test selective retraining: only failed NIMs trigger training."""
        executor = TriggerExecutor(mock_trigger_db)

        # Mock config
        mock_trigger_db.configurations.find_one.return_value = sample_config_with_nims

        # No existing execution
        mock_trigger_db.trigger_executions.find_one.return_value = None

        # Mock eval and training jobs
        eval_job_id = str(ObjectId())
        training_job_id = str(ObjectId())
        mock_run_job.side_effect = [eval_job_id, training_job_id]

        # Mock NIMs for the jobs
        nim1_id = ObjectId()
        nim2_id = ObjectId()

        def nims_find_side_effect(query):
            job_id = query.get("flywheel_run_id")
            if str(job_id) == eval_job_id:
                return [
                    {"_id": nim1_id, "model_name": "meta/llama-3.2-1b-instruct"},
                    {"_id": nim2_id, "model_name": "meta/llama-3.2-3b-instruct"},
                ]
            elif str(job_id) == training_job_id:
                # Only the failed NIM gets retrained
                return [{"_id": nim1_id, "model_name": "meta/llama-3.2-1b-instruct"}]
            return []

        mock_trigger_db.nims.find.side_effect = nims_find_side_effect

        # Mock eval job completion
        def flywheel_find_side_effect(query):
            job_id = query.get("_id")
            if str(job_id) == eval_job_id:
                return {"_id": ObjectId(eval_job_id), "status": FlywheelRunStatus.COMPLETED}
            elif str(job_id) == training_job_id:
                return {"_id": ObjectId(training_job_id), "status": FlywheelRunStatus.COMPLETED}
            return None

        mock_trigger_db.flywheel_runs.find_one.side_effect = flywheel_find_side_effect

        # Mock evaluation results - ONE FAILS, ONE PASSES
        def mock_eval_find_one(query):
            nim_id = query.get("nim_id")
            if nim_id == nim1_id and query.get("eval_type"):
                # Initial eval - fails thresholds
                return {"scores": {"accuracy": 0.82, "f1_score": 0.78}, "error": None}
            elif nim_id == nim2_id and query.get("eval_type"):
                # Initial eval - passes thresholds
                return {"scores": {"accuracy": 0.90, "f1_score": 0.85}, "error": None}
            return None

        mock_trigger_db.evaluations.find_one.side_effect = mock_eval_find_one
        mock_trigger_db.customizations.find_one.return_value = None  # No training results yet

        execution = executor.execute(sample_metric_degradation_trigger_model)

        assert execution.status == "completed"
        assert execution.triggered_training is True
        assert execution.total_nims == 2
        assert execution.nims_failed == 1  # Only 1 NIM failed
        assert execution.nims_retrained == 1  # Only 1 NIM was retrained

        # Verify only the failed NIM triggered training
        failed_nims = [r for r in execution.nim_results if not r.passed_thresholds]
        assert len(failed_nims) == 1
        assert failed_nims[0].model_name == "meta/llama-3.2-1b-instruct"
        assert failed_nims[0].training_triggered is True

        # Verify passed NIM did not trigger training
        passed_nims = [r for r in execution.nim_results if r.passed_thresholds]
        assert len(passed_nims) == 1
        assert passed_nims[0].model_name == "meta/llama-3.2-3b-instruct"
        assert passed_nims[0].training_triggered is False


class TestErrorTracking:
    """Tests for error tracking in executions."""

    @patch("src.tasks.triggers.executor.TriggerExecutor._run_job")
    def test_eval_error_captured(
        self,
        mock_run_job,
        mock_trigger_db,
        sample_metric_degradation_trigger_model,
        sample_config_with_nims,
    ):
        """Test evaluation error is captured in NIM result."""
        executor = TriggerExecutor(mock_trigger_db)

        # Mock config
        mock_trigger_db.configurations.find_one.return_value = sample_config_with_nims

        # No existing execution
        mock_trigger_db.trigger_executions.find_one.return_value = None

        # Mock eval job
        eval_job_id = str(ObjectId())
        mock_run_job.return_value = eval_job_id

        # Mock eval job completion
        mock_trigger_db.flywheel_runs.find_one.return_value = {
            "_id": ObjectId(eval_job_id),
            "status": FlywheelRunStatus.COMPLETED,
        }

        # Mock NIMs for the job
        nim1_id = ObjectId()
        nim2_id = ObjectId()
        mock_trigger_db.nims.find.return_value = [
            {"_id": nim1_id, "model_name": "meta/llama-3.2-1b-instruct"},
            {"_id": nim2_id, "model_name": "meta/llama-3.2-3b-instruct"},
        ]

        # Mock evaluation results - first NIM has error, second has no data
        def mock_eval_find_one(query):
            nim_id = query.get("nim_id")
            if nim_id == nim1_id:
                return {"error": "Evaluation job timeout", "scores": None}
            elif nim_id == nim2_id:
                return None  # No evaluation data
            return None

        mock_trigger_db.evaluations.find_one.side_effect = mock_eval_find_one
        mock_trigger_db.customizations.find_one.return_value = None

        execution = executor.execute(sample_metric_degradation_trigger_model)

        # Check error is captured for first NIM
        nim_with_error = next(
            r for r in execution.nim_results if r.model_name == "meta/llama-3.2-1b-instruct"
        )
        assert nim_with_error.eval_error == "Evaluation job timeout"
        # Eval error is a complete failure, not incomplete evaluation
        assert nim_with_error.incomplete_evaluation is False
        assert nim_with_error.passed_thresholds is False

    @patch("src.tasks.triggers.executor.TriggerExecutor._run_job")
    def test_customization_error_captured(
        self,
        mock_run_job,
        mock_trigger_db,
        sample_metric_degradation_trigger_model,
        sample_config_with_nims,
        sample_evaluation_result,
        sample_customization_result_with_error,
    ):
        """Test customization error is captured."""
        executor = TriggerExecutor(mock_trigger_db)

        # Mock config
        mock_trigger_db.configurations.find_one.return_value = sample_config_with_nims

        # No existing execution
        mock_trigger_db.trigger_executions.find_one.return_value = None

        # Mock jobs
        eval_job_id = str(ObjectId())
        training_job_id = str(ObjectId())
        mock_run_job.side_effect = [eval_job_id, training_job_id]

        # Mock NIMs
        nim_id = ObjectId()

        def nims_find_side_effect(query):
            return [{"_id": nim_id, "model_name": "meta/llama-3.2-1b-instruct"}]

        mock_trigger_db.nims.find.side_effect = nims_find_side_effect

        # Mock job completions
        def flywheel_find_side_effect(query):
            job_id = query.get("_id")
            if str(job_id) == eval_job_id:
                return {"_id": ObjectId(eval_job_id), "status": FlywheelRunStatus.COMPLETED}
            elif str(job_id) == training_job_id:
                return {"_id": ObjectId(training_job_id), "status": FlywheelRunStatus.COMPLETED}
            return None

        mock_trigger_db.flywheel_runs.find_one.side_effect = flywheel_find_side_effect

        # Mock evaluation result - fails threshold to trigger training
        def mock_eval_find_one(query):
            if query.get("eval_type"):
                # Base evaluation - fails threshold
                return {"scores": {"accuracy": 0.70, "f1_score": 0.65}, "error": None}
            return None

        mock_trigger_db.evaluations.find_one.side_effect = mock_eval_find_one

        # Mock customization result with error
        mock_trigger_db.customizations.find_one.return_value = {
            "_id": ObjectId(),
            "error": "GPU out of memory",
            "customized_model": None,
        }

        execution = executor.execute(sample_metric_degradation_trigger_model)

        # Check customization error is captured
        assert len(execution.nim_results) > 0
        nim_result = execution.nim_results[0]
        assert nim_result.customization_error == "GPU out of memory"


class TestModelInfoPassing:
    """Tests for ModelInfo passing to pipeline."""

    @patch("src.tasks.tasks.run_nim_workflow_dag.delay")
    def test_model_info_passed_to_pipeline(
        self,
        mock_workflow_dag,
        mock_trigger_db,
        sample_data_threshold_trigger_model,
        sample_config_with_nims,
    ):
        """Test ModelInfo (target and is_fine_tuned) passed via runtime_overrides."""
        executor = TriggerExecutor(mock_trigger_db)

        # Mock config
        mock_trigger_db.configurations.find_one.return_value = sample_config_with_nims

        # Mock flywheel_runs insert
        job_id = ObjectId()
        mock_trigger_db.flywheel_runs.insert_one.return_value.inserted_id = job_id

        # Run job
        target_models = {
            "meta/llama-3.2-1b-instruct": {
                "target": "meta/llama-3.2-1b-instruct@v2.0",
                "is_fine_tuned": False,
            }
        }

        result = executor._run_job(
            sample_data_threshold_trigger_model,
            target_models,
            eval_enabled=False,
            training_enabled=True,
        )

        assert result == str(job_id)

        # Verify Celery task was called with ModelInfo in runtime_overrides
        mock_workflow_dag.assert_called_once()
        call_kwargs = mock_workflow_dag.call_args.kwargs

        assert "runtime_overrides" in call_kwargs
        runtime_overrides = call_kwargs["runtime_overrides"]
        assert "target_models" in runtime_overrides
        assert runtime_overrides["target_models"] == target_models
        assert (
            runtime_overrides["target_models"]["meta/llama-3.2-1b-instruct"]["is_fine_tuned"]
            is False
        )
