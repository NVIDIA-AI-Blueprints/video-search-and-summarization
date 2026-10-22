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

"""Tests for trigger Pydantic models."""

from datetime import datetime
from typing import get_args

import pytest
from bson import ObjectId
from pydantic import ValidationError

from src.api.triggers.models import NIMExecutionResult, RetrainingTrigger, TriggerExecution
from src.job_types import JobType


class TestRetrainingTrigger:
    """Tests for RetrainingTrigger model."""

    def test_create_valid_data_threshold_trigger(self, sample_data_threshold_trigger):
        """Test creating a valid data threshold trigger."""
        trigger = RetrainingTrigger(**sample_data_threshold_trigger)

        assert trigger.name == "test-data-threshold"
        assert trigger.trigger_type == "data_threshold"
        assert trigger.min_new_records == 100
        assert trigger.enable_data_ingestion is True
        assert trigger.metric_thresholds is None
        assert trigger.consecutive_failures == 0

    def test_create_valid_metric_degradation_trigger(self, sample_metric_degradation_trigger):
        """Test creating a valid metric degradation trigger."""
        trigger = RetrainingTrigger(**sample_metric_degradation_trigger)

        assert trigger.name == "test-metric-degradation"
        assert trigger.trigger_type == "metric_degradation"
        assert trigger.metric_thresholds == {"accuracy": 0.85, "f1_score": 0.80}
        assert trigger.model_source == "base"  # fine_tuned only for embedding_job
        assert trigger.reference_job_id is not None

    def test_data_threshold_requires_min_new_records(self, sample_data_threshold_trigger):
        """Test data_threshold trigger requires min_new_records."""
        sample_data_threshold_trigger["min_new_records"] = None

        with pytest.raises(ValidationError) as exc_info:
            RetrainingTrigger(**sample_data_threshold_trigger)

        assert "min_new_records" in str(exc_info.value).lower()

    def test_metric_degradation_requires_metric_thresholds(self, sample_metric_degradation_trigger):
        """Test metric_degradation trigger requires metric_thresholds."""
        sample_metric_degradation_trigger["metric_thresholds"] = None

        with pytest.raises(ValidationError) as exc_info:
            RetrainingTrigger(**sample_metric_degradation_trigger)

        assert "metric_thresholds" in str(exc_info.value).lower()

    def test_cron_expression_validation(self, sample_data_threshold_trigger):
        """Test cron expression validation."""
        # Invalid: too few fields
        sample_data_threshold_trigger["eval_schedule"] = "0 0 *"

        with pytest.raises(ValidationError) as exc_info:
            RetrainingTrigger(**sample_data_threshold_trigger)

        assert "pattern" in str(exc_info.value).lower() or "5 fields" in str(exc_info.value).lower()

    def test_metric_thresholds_not_empty(self, sample_metric_degradation_trigger):
        """Test metric_thresholds cannot be empty dict."""
        sample_metric_degradation_trigger["metric_thresholds"] = {}

        with pytest.raises(ValidationError) as exc_info:
            RetrainingTrigger(**sample_metric_degradation_trigger)

        # Empty dict is treated as None by model validator
        assert "metric_thresholds" in str(exc_info.value).lower()

    def test_from_mongo_conversion(self, sample_trigger_doc):
        """Test from_mongo() conversion."""
        trigger = RetrainingTrigger.from_mongo(sample_trigger_doc)

        assert trigger is not None
        assert trigger.id == sample_trigger_doc["_id"]
        assert trigger.name == sample_trigger_doc["name"]
        assert trigger.created_at == sample_trigger_doc["created_at"]

    def test_to_mongo_conversion(self, sample_data_threshold_trigger):
        """Test to_mongo() conversion."""
        trigger = RetrainingTrigger(**sample_data_threshold_trigger)
        doc = trigger.to_mongo()

        assert "_id" in doc
        assert doc["name"] == trigger.name
        assert doc["trigger_type"] == trigger.trigger_type
        assert "created_at" in doc

    def test_latest_fine_tuned_models_storage(self):
        """Test latest_fine_tuned_models stores string model names."""
        # Use embedding_job to support fine_tuned model_source
        trigger_config = {
            "name": "test-embedding-trigger",
            "description": "Test trigger",
            "workload_id": "test-workload",
            "client_id": "test-client",
            "job_type": "embedding_job",
            "trigger_type": "data_threshold",
            "eval_schedule": "0 0 * * *",
            "min_new_records": 100,
            "enable_data_ingestion": True,
            "config_name": "test-config",
            "model_source": "fine_tuned",
            "latest_fine_tuned_models": {
                "meta/llama-3.2-1b-instruct": "dfwbp/model@cust-123",
                "meta/llama-3.2-3b-instruct": "dfwbp/model@cust-456",
            },
            "enabled": True,
        }

        trigger = RetrainingTrigger(**trigger_config)

        assert isinstance(trigger.latest_fine_tuned_models, dict)
        assert all(isinstance(v, str) for v in trigger.latest_fine_tuned_models.values())


class TestTriggerExecution:
    """Tests for TriggerExecution model."""

    def test_create_valid_execution(self, sample_execution):
        """Test creating a valid execution."""
        # Update summary fields from nim_results
        sample_execution.update_summary_from_nim_results()

        assert sample_execution.trigger_id is not None
        assert sample_execution.status == "completed"
        assert len(sample_execution.nim_results) == 2
        assert sample_execution.total_nims == 2
        assert sample_execution.nims_failed == 1

    def test_execution_with_error(self):
        """Test execution with error message."""
        execution = TriggerExecution(
            trigger_id=str(ObjectId()),
            trigger_name="test-trigger",
            trigger_type="data_threshold",
            execution_time=datetime.utcnow(),
            status="failed",
            error="Test error message",
            completed_at=datetime.utcnow(),
        )

        assert execution.status == "failed"
        assert execution.error == "Test error message"

    def test_execution_status_values(self):
        """Test valid execution status values."""
        valid_statuses = ["pending", "running", "completed", "failed", "skipped"]

        for status in valid_statuses:
            execution = TriggerExecution(
                trigger_id=str(ObjectId()),
                trigger_name="test",
                trigger_type="data_threshold",
                execution_time=datetime.utcnow(),
                status=status,
            )
            assert execution.status == status

    def test_from_mongo_conversion(self, sample_execution_doc):
        """Test from_mongo() conversion for execution."""
        execution = TriggerExecution.from_mongo(sample_execution_doc)

        assert execution is not None
        assert execution.id == sample_execution_doc["_id"]
        assert len(execution.nim_results) > 0


class TestNIMExecutionResult:
    """Tests for NIMExecutionResult model."""

    def test_create_nim_result(self, sample_nim_result):
        """Test creating NIM execution result."""
        assert sample_nim_result.model_name == "meta/llama-3.2-1b-instruct"
        assert sample_nim_result.metrics["accuracy"] == 0.82
        assert sample_nim_result.passed_thresholds is False
        assert sample_nim_result.training_triggered is True

    def test_nim_result_with_errors(self, sample_nim_result_with_error):
        """Test NIM result with error fields."""
        assert sample_nim_result_with_error.eval_error == "Evaluation job failed: timeout"
        assert sample_nim_result_with_error.incomplete_evaluation is True
        assert sample_nim_result_with_error.passed_thresholds is False

    def test_error_fields_default_none(self):
        """Test error fields default to None."""
        result = NIMExecutionResult(
            model_name="test-model",
            metrics={},
            thresholds_checked={},
            passed_thresholds=True,
        )

        assert result.eval_error is None
        assert result.customization_error is None
        assert result.post_eval_error is None
        assert result.incomplete_evaluation is False

    def test_customization_error_captured(self):
        """Test customization error is captured."""
        result = NIMExecutionResult(
            model_name="test-model",
            metrics={"accuracy": 0.82},
            thresholds_checked={"accuracy": {"threshold": 0.85, "actual": 0.82, "passed": False}},
            passed_thresholds=False,
            training_triggered=True,
            customization_error="Training failed: GPU OOM",
        )

        assert result.customization_error == "Training failed: GPU OOM"
        assert result.training_triggered is True

    def test_post_eval_error_captured(self):
        """Test post-evaluation error is captured."""
        result = NIMExecutionResult(
            model_name="test-model",
            metrics={"accuracy": 0.82},
            thresholds_checked={"accuracy": {"threshold": 0.85, "actual": 0.82, "passed": False}},
            passed_thresholds=False,
            training_triggered=True,
            fine_tuned_model="dfwbp/model@cust-123",
            post_eval_error="Post-training evaluation failed",
        )

        assert result.post_eval_error == "Post-training evaluation failed"
        assert result.fine_tuned_model is not None


class TestRetrainingTriggerDeftFamily:
    """The persisted model's DEFT-family branches (``aoi_job`` / ``its_job``).

    ``TriggerCreateRequest`` and ``RetrainingTrigger`` must agree: the request
    schema 422s at the API edge, the persisted model 500s on the Mongo write.
    Widening only one of them produces an accepted request that then explodes
    deeper.
    """

    @staticmethod
    def _kwargs(job_type: str, **overrides) -> dict:
        kwargs = {
            "name": "t",
            "workload_id": "w1",
            "client_id": "c1",
            "job_type": job_type,
            "trigger_type": "data_threshold",
            "eval_schedule": "0 0 * * 0",
            "min_new_records": 100,
        }
        kwargs.update(overrides)
        return kwargs

    def test_the_literal_admits_exactly_the_registered_job_types(self):
        """The fourth hand-written job-type registry, guarded against ADDITION.

        ``JobType``, ``JOB_CONFIG_TYPES``, ``TriggerCreateRequest.job_type`` and
        this Literal must all agree. Asserting a hardcoded set only catches a
        REMOVAL; derived from ``JobType`` it also catches the drift mode that
        actually happens — a new member added in one place — whose symptom is
        asymmetric and non-obvious (``/jobs`` accepts it, ``/triggers`` 422s it).
        """
        # Read off the annotation rather than model_json_schema(): the model
        # carries a bson ObjectId field that has no JSON schema.
        allowed = set(get_args(RetrainingTrigger.model_fields["job_type"].annotation))
        assert allowed == {job_type.value for job_type in JobType}
        assert allowed == {"llm_job", "embedding_job", "aoi_job", "its_job"}

    @pytest.mark.parametrize("job_type", ["aoi_job", "its_job"])
    def test_data_threshold_validates_and_forces_ingestion_off(self, job_type):
        """The inseparable pair: the exempt branch SETS enable_data_ingestion=False...

        ...and the data_threshold check below it must therefore EXCLUDE the same
        family. Widen one without the other and every DEFT data_threshold
        trigger is rejected for a value the validator itself just assigned — a
        422 no client can work around.
        """
        trigger = RetrainingTrigger(**self._kwargs(job_type))
        assert trigger.enable_data_ingestion is False
        assert trigger.config_name is None

    @pytest.mark.parametrize("job_type", ["aoi_job", "its_job"])
    def test_ingestion_config_rejected(self, job_type):
        with pytest.raises(ValidationError, match="ingestion_config is not supported"):
            RetrainingTrigger(
                **self._kwargs(
                    job_type,
                    ingestion_config={"plugin_name": "bugnemo", "dataset_type": "bug_pairs"},
                )
            )

    @pytest.mark.parametrize("job_type", ["aoi_job", "its_job"])
    def test_metric_degradation_rejected_naming_the_job_type(self, job_type):
        with pytest.raises(ValidationError, match=f"not supported for {job_type}"):
            RetrainingTrigger(
                **self._kwargs(
                    job_type,
                    trigger_type="metric_degradation",
                    metric_thresholds={"recall": 0.8},
                )
            )

    @pytest.mark.parametrize("job_type", ["aoi_job", "its_job"])
    def test_fine_tuned_model_source_still_rejected(self, job_type):
        """models.py's ``!= "embedding_job"`` is a ONE-MEMBER ALLOWLIST, not an
        AOI exemption — the DEFT family must stay excluded from it. A mechanical
        "widen every job_type comparison" sweep would wrongly open this."""
        with pytest.raises(ValidationError, match="only supported for embedding_job"):
            RetrainingTrigger(
                **self._kwargs(job_type, trigger_type="schedule", model_source="fine_tuned")
            )

    def test_nmp_job_types_keep_their_requirements(self):
        with pytest.raises(ValidationError, match="config_name is required"):
            RetrainingTrigger(**self._kwargs("llm_job"))
        with pytest.raises(ValidationError, match="enable_data_ingestion must be True"):
            RetrainingTrigger(
                **self._kwargs("llm_job", config_name="cfg", enable_data_ingestion=False)
            )
