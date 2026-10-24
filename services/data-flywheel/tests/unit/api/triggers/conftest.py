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

"""Shared fixtures for trigger API tests."""

from datetime import datetime
from unittest.mock import MagicMock

import pytest
from bson import ObjectId

from src.api.triggers.models import NIMExecutionResult, TriggerExecution


@pytest.fixture
def sample_data_threshold_trigger():
    """Valid data threshold trigger configuration."""
    return {
        "name": "test-data-threshold",
        "description": "Test data threshold trigger",
        "workload_id": "test-workload",
        "client_id": "test-client",
        "job_type": "llm_job",
        "trigger_type": "data_threshold",
        "eval_schedule": "0 0 * * *",
        "min_new_records": 100,
        "enable_data_ingestion": True,
        "ingestion_timeout_minutes": 60,
        "config_name": "test-config",
        "model_source": "base",
        "enabled": True,
        "max_consecutive_failures": 3,
    }


@pytest.fixture
def sample_metric_degradation_trigger():
    """Valid metric degradation trigger configuration."""
    return {
        "name": "test-metric-degradation",
        "description": "Test metric degradation trigger",
        "workload_id": "test-workload",
        "client_id": "test-client",
        "job_type": "llm_job",
        "trigger_type": "metric_degradation",
        "eval_schedule": "0 */6 * * *",
        "metric_thresholds": {"accuracy": 0.85, "f1_score": 0.80},
        "config_name": "test-config",
        "model_source": "base",  # fine_tuned only supported for embedding_job
        "reference_job_id": str(ObjectId()),
        "enabled": True,
        "max_consecutive_failures": 5,
    }


@pytest.fixture
def sample_embedding_trigger():
    """Valid embedding job trigger configuration."""
    return {
        "name": "test-embedding-trigger",
        "description": "Test embedding trigger",
        "workload_id": "test-workload",
        "client_id": "test-client",
        "job_type": "embedding_job",
        "trigger_type": "data_threshold",
        "eval_schedule": "0 2 * * *",
        "min_new_records": 50,
        "enable_data_ingestion": True,
        "ingestion_timeout_minutes": 30,
        "data_window_months": 6,
        "config_name": "test-embedding-config",
        "model_source": "base",
        "enabled": True,
    }


@pytest.fixture
def sample_trigger_doc(sample_data_threshold_trigger):
    """Sample trigger MongoDB document."""
    doc = sample_data_threshold_trigger.copy()
    doc["_id"] = ObjectId()
    doc["created_at"] = datetime.utcnow()
    doc["updated_at"] = datetime.utcnow()
    doc["created_by"] = None
    doc["last_execution_time"] = None
    doc["last_execution_status"] = None
    doc["consecutive_failures"] = 0
    doc["latest_fine_tuned_models"] = {}
    return doc


@pytest.fixture
def sample_nim_result():
    """Sample NIM execution result with metrics and errors."""
    return NIMExecutionResult(
        model_name="meta/llama-3.2-1b-instruct",
        metrics={"accuracy": 0.82, "f1_score": 0.79},
        thresholds_checked={
            "accuracy": {"threshold": 0.85, "actual": 0.82, "passed": False},
            "f1_score": {"threshold": 0.80, "actual": 0.79, "passed": False},
        },
        passed_thresholds=False,
        needs_retraining=True,
        training_triggered=True,
        customization_id=str(ObjectId()),
        fine_tuned_model="dfwbp/test-model@cust-123",
        post_training_metrics={"accuracy": 0.88, "f1_score": 0.85},
        incomplete_evaluation=False,
        eval_error=None,
        customization_error=None,
        post_eval_error=None,
    )


@pytest.fixture
def sample_nim_result_with_error():
    """Sample NIM execution result with evaluation error."""
    return NIMExecutionResult(
        model_name="meta/llama-3.2-1b-instruct",
        metrics={},
        thresholds_checked={},
        passed_thresholds=False,
        needs_retraining=False,
        training_triggered=False,
        incomplete_evaluation=True,
        eval_error="Evaluation job failed: timeout",
        customization_error=None,
        post_eval_error=None,
    )


@pytest.fixture
def sample_execution():
    """Sample trigger execution with NIM results."""
    return TriggerExecution(
        trigger_id=str(ObjectId()),
        trigger_name="test-trigger",
        trigger_type="metric_degradation",
        execution_time=datetime.utcnow(),
        status="completed",
        eval_job_id=str(ObjectId()),
        training_job_id=str(ObjectId()),
        nim_results=[
            NIMExecutionResult(
                model_name="meta/llama-3.2-1b-instruct",
                metrics={"accuracy": 0.82},
                thresholds_checked={
                    "accuracy": {"threshold": 0.85, "actual": 0.82, "passed": False}
                },
                passed_thresholds=False,
                needs_retraining=True,
                training_triggered=True,
            ),
            NIMExecutionResult(
                model_name="meta/llama-3.2-3b-instruct",
                metrics={"accuracy": 0.90},
                thresholds_checked={
                    "accuracy": {"threshold": 0.85, "actual": 0.90, "passed": True}
                },
                passed_thresholds=True,
                needs_retraining=False,
                training_triggered=False,
            ),
        ],
        completed_at=datetime.utcnow(),
        total_duration_seconds=300.5,
    )


@pytest.fixture
def sample_execution_doc(sample_execution):
    """Sample execution MongoDB document."""
    doc = sample_execution.to_mongo()
    doc["_id"] = ObjectId()
    return doc


@pytest.fixture
def mock_trigger_db(mock_db):
    """Mock MongoDB with trigger collections."""
    # Setup trigger collection mock
    mock_db.retraining_triggers = MagicMock()
    mock_db.retraining_triggers.find_one.return_value = None
    mock_db.retraining_triggers.find.return_value = []
    mock_db.retraining_triggers.insert_one.return_value = MagicMock(inserted_id=ObjectId())
    mock_db.retraining_triggers.update_one.return_value = MagicMock(matched_count=1)
    mock_db.retraining_triggers.delete_one.return_value = MagicMock(deleted_count=1)

    # Setup execution collection mock
    mock_db.trigger_executions = MagicMock()
    mock_db.trigger_executions.find_one.return_value = None
    mock_db.trigger_executions.find.return_value = []
    mock_db.trigger_executions.insert_one.return_value = MagicMock(inserted_id=ObjectId())
    mock_db.trigger_executions.update_one.return_value = MagicMock(matched_count=1)

    # Setup configurations collection mock
    mock_db.configurations = MagicMock()
    mock_db.configurations.find_one.return_value = None

    # Setup flywheel_runs collection mock (for stale execution checks)
    mock_db.flywheel_runs = MagicMock()
    mock_db.flywheel_runs.find_one.return_value = None

    return mock_db


@pytest.fixture
def sample_multi_nim_config():
    """Sample configuration with multiple NIMs."""
    return {
        "_id": ObjectId(),
        "name": "test-multi-nim-config",
        "nims": [
            {
                "model_name": "meta/llama-3.2-1b-instruct",
                "customizer_configs": {
                    "target": "meta/llama-3.2-1b-instruct@v2.0",
                },
            },
            {
                "model_name": "meta/llama-3.2-3b-instruct",
                "customizer_configs": {
                    "target": "meta/llama-3.2-3b-instruct@v2.0",
                },
            },
        ],
    }
