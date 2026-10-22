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

"""
Common fixtures and utilities for job service tests.
"""

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest
from bson import ObjectId

from src.api.models import TaskResult
from src.api.schemas import DeploymentStatus
from src.config import settings


@pytest.fixture
def test_db_no_llm_judge(mock_db):
    """Fixture to set up test database without LLM Judge data"""
    # Create test flywheel run
    flywheel_run_id = ObjectId()
    mock_db.flywheel_runs.insert_one.return_value = {"inserted_id": flywheel_run_id}

    # Create test NIMs
    nim1_id = ObjectId()
    nim2_id = ObjectId()

    # Mock flywheel run data
    mock_db.flywheel_runs.find_one.return_value = {
        "_id": flywheel_run_id,
        "workload_id": "test_workload",
        "client_id": "test_client",
        "started_at": datetime.utcnow(),
        "num_records": 100,
        "nims": [],
        "datasets": [
            {
                "name": f"test_dataset_{i + 1}",
                "num_records": 100,
                "nmp_uri": f"test_uri_{i + 1}",
            }
            for i in range(4)
        ],
    }

    # Mock NIMs data
    mock_db.nims.find.return_value = [
        {
            "_id": nim1_id,
            "model_name": "test_model_1",
            "flywheel_run_id": flywheel_run_id,
            "deployment_status": DeploymentStatus.PENDING,
            "runtime_seconds": 120.0,
        },
        {
            "_id": nim2_id,
            "model_name": "test_model_2",
            "flywheel_run_id": flywheel_run_id,
            "deployment_status": DeploymentStatus.PENDING,
            "runtime_seconds": 60.0,
        },
    ]

    mock_db.nims.insert_many.return_value = {"inserted_ids": [nim1_id, nim2_id]}

    # No LLM Judge
    mock_db.llm_judge_runs.find_one.return_value = None

    return {
        "flywheel_run_id": str(flywheel_run_id),
        "workload_id": "test_workload",
        "client_id": "test_client",
        "nim1_id": str(nim1_id),
        "nim2_id": str(nim2_id),
    }


@pytest.fixture
def test_db_empty_datasets(mock_db):
    """Fixture to set up test database with empty datasets"""
    # Create test flywheel run
    flywheel_run_id = ObjectId()
    mock_db.flywheel_runs.insert_one.return_value = {"inserted_id": flywheel_run_id}

    # Mock flywheel run data with empty datasets
    mock_db.flywheel_runs.find_one.return_value = {
        "_id": flywheel_run_id,
        "workload_id": "test_workload",
        "client_id": "test_client",
        "started_at": datetime.utcnow(),
        "num_records": 0,
        "nims": [],
        "datasets": [],
    }

    # Empty NIMs data
    mock_db.nims.find.return_value = []

    # No LLM Judge
    mock_db.llm_judge_runs.find_one.return_value = None

    return {
        "flywheel_run_id": str(flywheel_run_id),
        "workload_id": "test_workload",
        "client_id": "test_client",
    }


@pytest.fixture
def task_db_manager_mock():
    """Create a mock TaskDBManager for testing"""
    with patch("src.tasks.tasks.db_manager") as mock_db_manager:
        yield mock_db_manager


@pytest.fixture
def mock_nim():
    """Create a mock NIM configuration"""
    nim_mock = MagicMock()
    nim_mock.model_dump.return_value = {
        "model_name": "test-model",
        "context_length": 8192,
        "customization_enabled": False,
    }
    return nim_mock


def validate_job_response_success(job_json):
    """Validate job response for successful tests"""
    # Validate NIMs in job response
    assert len(job_json["nims"]) == 2
    for nim in job_json["nims"]:
        assert nim["model_name"] in ["test_model_1", "test_model_2"]
        if nim["model_name"] == "test_model_1":
            assert nim["deployment_status"] == DeploymentStatus.COMPLETED
        else:
            assert nim["deployment_status"] == DeploymentStatus.PENDING

    # Validate LLM Judge in job response
    assert job_json["llm_judge"] is not None
    assert job_json["llm_judge"]["model_name"] == "test-llm-judge"
    assert job_json["llm_judge"]["deployment_status"] == DeploymentStatus.READY


def validate_job_response_no_llm_judge(job_json):
    """Validate job response without LLM Judge"""
    # Validate NIMs in job response
    assert len(job_json["nims"]) == 2
    for nim in job_json["nims"]:
        assert nim["model_name"] in ["test_model_1", "test_model_2"]
        assert nim["deployment_status"] == DeploymentStatus.PENDING

    # Validate no LLM Judge in job response
    assert job_json["llm_judge"] is None


def validate_datasets_in_job_response(job_json, expected_count=3, expected_records=100):
    """Validate datasets in job response"""
    assert len(job_json["datasets"]) == expected_count
    for dataset in job_json["datasets"]:
        assert dataset["name"].startswith("test_dataset_")
        assert dataset["num_records"] == expected_records
        assert dataset["nmp_uri"].startswith("test_uri_")


@pytest.fixture()
def tweak_settings(monkeypatch):
    """Provide deterministic test configuration via the global `settings`."""

    # --- Data-split parameters (fields are *not* frozen) --------------------
    monkeypatch.setattr(
        settings.job_configs["llm_job"].data_split_config, "min_total_records", 1, raising=False
    )
    monkeypatch.setattr(
        settings.job_configs["llm_job"].data_split_config, "random_seed", 42, raising=False
    )
    monkeypatch.setattr(
        settings.job_configs["llm_job"].data_split_config, "eval_size", 10, raising=False
    )  # Changed from 1 to 10 to allow validation
    monkeypatch.setattr(
        settings.job_configs["llm_job"].data_split_config, "val_ratio", 0.25, raising=False
    )
    monkeypatch.setattr(
        settings.job_configs["llm_job"].data_split_config, "limit", 100, raising=False
    )

    # --- NMP namespace (field *is* frozen, so create a new object) ----------
    new_nmp_cfg = settings.system_config.nmp_config.model_copy(
        update={"nmp_namespace": "test-namespace"}
    )
    monkeypatch.setattr(settings.system_config, "nmp_config", new_nmp_cfg, raising=True)

    # --- LLM Judge config (field *is* frozen, so create a new object) ----------
    remote_llm_judge_cfg = settings.system_config.llm_judge_config.model_copy(
        update={"deployment_type": "remote", "url": "http://test-llm-judge.com"}
    )
    monkeypatch.setattr(
        settings.system_config, "llm_judge_config", remote_llm_judge_cfg, raising=True
    )

    yield


@pytest.fixture
def nim_status_test_data(mock_db):
    """Fixture providing common test data for NIM status tests."""
    flywheel_run_id = ObjectId()
    nim_id = ObjectId()

    # Mock basic flywheel run
    mock_db.flywheel_runs.find_one.return_value = {
        "_id": flywheel_run_id,
        "workload_id": "test_workload",
        "client_id": "test_client",
        "started_at": datetime.utcnow(),
        "finished_at": None,
        "num_records": 100,
        "datasets": [],
        "error": None,
    }

    # Mock empty evaluations and customizations by default
    mock_db.evaluations.find.return_value = []
    mock_db.customizations.find.return_value = []

    # Mock no LLM judge by default
    mock_db.llm_judge_runs.find_one.return_value = None

    return str(flywheel_run_id), nim_id


@pytest.fixture
def sample_split_config():
    """Fixture providing a sample data split configuration"""
    return {
        "eval_size": 10,
        "val_ratio": 0.3,
        "min_total_records": 20,
        "random_seed": 42,
        "limit": 25,
    }


@pytest.fixture
def mock_dms_client():
    """Fixture to mock DMSClient."""
    with patch("src.backends.nmp.provider.NmpBackendProvider.deployment_client") as mock_deployment:
        mock_instance = MagicMock()
        # Configure the mock instance methods
        mock_instance.is_deployed.return_value = False
        mock_instance.deploy_model.return_value = None
        mock_instance.wait_for_deployment.return_value = None
        mock_instance.wait_for_model_sync.return_value = None
        mock_instance.shutdown_deployment.return_value = None

        mock_deployment.return_value = mock_instance
        yield mock_instance


@pytest.fixture
def llm_job_config():
    """Fixture providing LLM job configuration."""
    llm_job = settings.job_configs.get("llm_job")
    return llm_job.model_dump() if llm_job else {}


@pytest.fixture
def embedding_job_config():
    """Fixture providing embedding job configuration."""
    embedding_job = settings.job_configs.get("embedding_job")
    return embedding_job.model_dump() if embedding_job else {}


@pytest.fixture
def nmp_config_dict():
    """Fixture providing NMP configuration as dict."""
    return settings.system_config.nmp_config.model_dump()


@pytest.fixture
def llm_judge_config_dict():
    """Fixture providing LLM judge configuration as dict."""
    return (
        settings.system_config.llm_judge_config.model_dump()
        if settings.system_config.llm_judge_config
        else None
    )


@pytest.fixture
def mlflow_config_dict():
    """Fixture providing MLflow configuration as dict."""
    return (
        settings.system_config.mlflow_config.model_dump()
        if settings.system_config.mlflow_config
        else None
    )


@pytest.fixture
def task_result_setup(llm_job_config, nmp_config_dict, tweak_settings):
    """Fixture providing a basic TaskResult for testing."""
    from src.api.models import LLMJudgeConfig

    # Get fresh config after tweak_settings is applied
    from src.config import settings

    fresh_llm_config = settings.job_configs.get("llm_job")
    if fresh_llm_config is None:
        pytest.skip("llm_job config is not available for the active backend")
    fresh_nmp_config = settings.system_config.nmp_config

    data_split_config = fresh_llm_config.data_split_config.model_dump()

    return TaskResult(
        workload_id="test-workload",
        flywheel_run_id=str(ObjectId()),
        client_id="test-client",
        llm_judge_config=LLMJudgeConfig(
            **{"deployment_type": "remote", "model_name": "test-judge"}
        ),
        error=None,
        evaluations={},
        job_config=fresh_llm_config,
        nmp_config=fresh_nmp_config,
        job_type="llm_job",
        data_split_config=data_split_config,
    )
