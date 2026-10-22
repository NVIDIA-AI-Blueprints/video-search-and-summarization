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

"""Shared fixtures for trigger task tests."""

from unittest.mock import MagicMock

import pytest
from bson import ObjectId

from src.api.schemas import FlywheelRunStatus
from src.api.triggers.models import RetrainingTrigger
from src.lib.ingestion import IngestionConfig


@pytest.fixture
def mock_pipeline_api():
    """Mock the pipeline API calls."""
    mock = MagicMock()
    mock.post.return_value.status_code = 200
    mock.post.return_value.json.return_value = {"id": str(ObjectId())}
    return mock


@pytest.fixture
def sample_target_models():
    """Sample TargetModels (ModelInfo dict)."""
    return {
        "meta/llama-3.2-1b-instruct": {
            "target": "meta/llama-3.2-1b-instruct",
            "is_fine_tuned": False,
        },
        "meta/llama-3.2-3b-instruct": {
            "target": "meta/llama-3.2-3b-instruct",
            "is_fine_tuned": False,
        },
    }


@pytest.fixture
def sample_fine_tuned_models():
    """Sample fine-tuned target models."""
    return {
        "meta/llama-3.2-1b-instruct": {
            "target": "dfwbp/model@cust-123",
            "is_fine_tuned": True,
        },
        "meta/llama-3.2-3b-instruct": {
            "target": "dfwbp/model@cust-456",
            "is_fine_tuned": True,
        },
    }


@pytest.fixture
def mock_job_completion():
    """Mock job completion with evaluations/customizations."""
    return {
        "eval_success": {
            "_id": ObjectId(),
            "flywheel_run_id": ObjectId(),
            "status": FlywheelRunStatus.COMPLETED,
        },
        "eval_failed": {
            "_id": ObjectId(),
            "flywheel_run_id": ObjectId(),
            "status": FlywheelRunStatus.FAILED,
            "error": "Evaluation failed",
        },
        "training_success": {
            "_id": ObjectId(),
            "flywheel_run_id": ObjectId(),
            "status": FlywheelRunStatus.COMPLETED,
        },
    }


@pytest.fixture
def sample_config_with_nims():
    """Sample configuration with multiple NIMs."""
    return {
        "_id": ObjectId(),
        "name": "test-config",
        "config_data": {
            "nims": [
                {
                    "model_name": "meta/llama-3.2-1b-instruct",
                    "context_length": 8192,
                    "customization_enabled": True,
                    "customizer_configs": {
                        "target": "meta/llama-3.2-1b-instruct@v2.0",
                        "gpus": 1,
                    },
                },
                {
                    "model_name": "meta/llama-3.2-3b-instruct",
                    "context_length": 8192,
                    "customization_enabled": True,
                    "customizer_configs": {
                        "target": "meta/llama-3.2-3b-instruct@v2.0",
                        "gpus": 1,
                    },
                },
            ],
        },
    }


@pytest.fixture
def sample_data_threshold_trigger_model():
    """Sample data threshold trigger model."""
    return RetrainingTrigger(
        name="test-data-threshold",
        description="Test trigger",
        workload_id="test-workload",
        client_id="test-client",
        job_type="embedding_job",
        trigger_type="data_threshold",
        eval_schedule="0 0 * * *",
        min_new_records=100,
        enable_data_ingestion=True,
        ingestion_timeout_minutes=60,
        ingestion_config=IngestionConfig(
            plugin_name="bugnemo",
            dataset_type="bug_pairs",
            config={"max_pairs": 1000},
        ),
        config_name="test-config",
        model_source="base",
        enabled=True,
    )


@pytest.fixture
def sample_metric_degradation_trigger_model():
    """Sample metric degradation trigger model."""
    return RetrainingTrigger(
        name="test-metric-degradation",
        description="Test trigger",
        workload_id="test-workload",
        client_id="test-client",
        job_type="llm_job",
        trigger_type="metric_degradation",
        eval_schedule="0 */6 * * *",
        metric_thresholds={"accuracy": 0.85, "f1_score": 0.80},
        enable_data_ingestion=False,
        config_name="test-config",
        model_source="base",  # fine_tuned only supported for embedding_job
        reference_job_id=str(ObjectId()),
        latest_fine_tuned_models={
            "meta/llama-3.2-1b-instruct": "dfwbp/model@cust-123",
        },
        enabled=True,
    )


@pytest.fixture
def sample_embedding_metric_degradation_trigger_model():
    """Sample metric degradation trigger model for embedding jobs."""
    return RetrainingTrigger(
        name="test-embedding-metric-degradation",
        description="Test embedding trigger",
        workload_id="test-workload",
        client_id="test-client",
        job_type="embedding_job",
        trigger_type="metric_degradation",
        eval_schedule="0 */6 * * *",
        metric_thresholds={"accuracy": 0.85, "f1_score": 0.80},
        enable_data_ingestion=False,
        config_name="test-config",
        model_source="fine_tuned",  # fine_tuned supported for embedding_job
        reference_job_id=str(ObjectId()),
        latest_fine_tuned_models={
            "meta/llama-3.2-1b-instruct": "dfwbp/model@cust-123",
        },
        enabled=True,
    )


@pytest.fixture
def sample_evaluation_result():
    """Sample evaluation result from MongoDB."""
    return {
        "_id": ObjectId(),
        "flywheel_run_id": ObjectId(),
        "nim_id": ObjectId(),
        "model_name": "meta/llama-3.2-1b-instruct",
        "status": "completed",
        "metrics": {"accuracy": 0.82, "f1_score": 0.79},
        "error": None,
    }


@pytest.fixture
def sample_evaluation_result_with_error():
    """Sample evaluation result with error."""
    return {
        "_id": ObjectId(),
        "flywheel_run_id": ObjectId(),
        "nim_id": ObjectId(),
        "model_name": "meta/llama-3.2-1b-instruct",
        "status": "failed",
        "metrics": {},
        "error": "Evaluation job timeout",
    }


@pytest.fixture
def sample_customization_result():
    """Sample customization result from MongoDB."""
    return {
        "_id": ObjectId(),
        "flywheel_run_id": ObjectId(),
        "nim_id": ObjectId(),
        "model_name": "meta/llama-3.2-1b-instruct",
        "status": "completed",
        "customized_nim": "dfwbp/model@cust-123",
        "error": None,
    }


@pytest.fixture
def sample_customization_result_with_error():
    """Sample customization result with error."""
    return {
        "_id": ObjectId(),
        "flywheel_run_id": ObjectId(),
        "nim_id": ObjectId(),
        "model_name": "meta/llama-3.2-1b-instruct",
        "status": "failed",
        "customized_nim": None,
        "error": "Training failed: GPU OOM",
    }


@pytest.fixture
def mock_trigger_db():
    """Mock MongoDB for trigger tasks."""
    mock_db = MagicMock()

    # Collections
    mock_db.retraining_triggers = MagicMock()
    mock_db.trigger_executions = MagicMock()
    mock_db.configurations = MagicMock()
    mock_db.flywheel_runs = MagicMock()
    mock_db.evaluations = MagicMock()
    mock_db.customizations = MagicMock()
    mock_db.nims = MagicMock()

    return mock_db
