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
Dataset Creation (create_datasets):
   - Successful dataset creation and validate in job response
   - Parameter flow validation from initialize_workflow
   - Empty dataset handling
   - Error scenarios:
     - No records found
     - Unable to reach the NMP endpoint (connection errors)
     - HF endpoint errors (authorization, rate limits, server errors)
     - ES client failures
     - Dataset generation errors
     - Validation errors for parameters
"""

from datetime import datetime

import pytest
import requests
from bson import ObjectId

from src.api.job_service import get_job_details
from src.api.models import DataSplitConfig, LLMJudgeConfig, TaskResult
from src.tasks.tasks import create_datasets


@pytest.fixture()
def test_db(mock_db):
    """Fixture to set up test database with sample data"""
    # Create test flywheel run
    flywheel_run_id = ObjectId()
    mock_db.flywheel_runs.insert_one.return_value = {"inserted_id": flywheel_run_id}

    # Create test NIMs
    nim1_id = ObjectId()
    nim2_id = ObjectId()

    # Create test LLM Judge
    llm_judge_id = ObjectId()

    # Mock flywheel run data
    mock_db.flywheel_runs.find_one.return_value = {
        "_id": flywheel_run_id,
        "workload_id": "test_workload",
        "client_id": "test_client",
        "started_at": datetime.utcnow(),
        "num_records": 0,
        "nims": [],
        "datasets": [],
    }

    # Mock NIMs data
    mock_db.nims.find.return_value = []

    mock_db.nims.insert_many.return_value = {"inserted_ids": [nim1_id, nim2_id]}

    # Mock evaluations data
    mock_db.evaluations.find.return_value = []
    # Mock customizations data
    mock_db.customizations.find.return_value = []

    mock_db.llm_judge_runs.insert_one.return_value = {"inserted_id": llm_judge_id}

    # Mock LLM Judge data
    mock_db.llm_judge_runs.find_one.return_value = None

    return {
        "flywheel_run_id": str(flywheel_run_id),
        "workload_id": "test_workload",
        "client_id": "test_client",
        "nim1_id": str(nim1_id),
        "nim2_id": str(nim2_id),
        "llm_judge_id": str(llm_judge_id),
    }


class TestJobServiceCreateDatasets:
    """Test class for dataset creation API functionality"""

    def _convert_result_to_task_result(self, result):
        """Helper method to convert result to TaskResult if it's a dictionary."""
        if isinstance(result, dict):
            return TaskResult(**result)
        return result

    def test_create_dataset_success(
        self,
        mock_db,
        task_result_setup,
        test_db_success,
        mock_es_client,
        tweak_settings,
        mock_data_uploader,
        sample_es_data,
        monkeypatch,
        mock_dms_client,
    ):
        """Test successful dataset creation and validate in job details"""
        # Adjust settings to match the sample data size
        task_result_setup.job_config.data_split_config.limit = 10
        task_result_setup.job_config.data_split_config.eval_size = 5
        task_result_setup.job_config.icl_config.max_examples = 2

        # Setup mock data
        mock_es_client.search.return_value = sample_es_data

        # Create the dataset
        create_result = create_datasets(task_result_setup)

        create_result = self._convert_result_to_task_result(create_result)

        # Verify create_dataset response
        assert isinstance(create_result, TaskResult)
        assert create_result.workload_id == task_result_setup.workload_id
        assert create_result.flywheel_run_id == task_result_setup.flywheel_run_id
        assert create_result.client_id == task_result_setup.client_id
        assert create_result.error is None
        assert create_result.datasets is not None
        assert len(create_result.datasets) == 3
        assert create_result.workload_type is not None

        # Update mock for get_job_details to return the new datasets
        updated_flywheel_run = mock_db.flywheel_runs.find_one.return_value.copy()
        updated_flywheel_run["datasets"] = [
            {"name": "dataset_1", "num_records": 50, "nmp_uri": "uri_1"},
            {"name": "dataset_2", "num_records": 50, "nmp_uri": "uri_2"},
            {"name": "dataset_3", "num_records": 50, "nmp_uri": "uri_3"},
        ]
        mock_db.flywheel_runs.find_one.return_value = updated_flywheel_run

        # Get job details and validate
        job_result = get_job_details(test_db_success["flywheel_run_id"])
        job_json = job_result.model_dump()

        # Validate datasets in job response
        assert len(job_json["datasets"]) == 3
        for dataset in job_json["datasets"]:
            assert dataset["name"] in ["dataset_1", "dataset_2", "dataset_3"]
            assert dataset["num_records"] == 50
            assert dataset["nmp_uri"] in ["uri_1", "uri_2", "uri_3"]

    def test_create_dataset_empty(
        self,
        mock_db,
        task_result_setup,
        test_db_empty_datasets,
        mock_es_client,
        tweak_settings,
        mock_data_uploader,
        empty_es_data,
        mock_dms_client,
    ):
        """Test dataset creation with empty data"""
        # Setup mock data
        mock_es_client.search.return_value = empty_es_data
        with pytest.raises(ValueError) as exc_info:
            create_datasets(task_result_setup)

        # The error message now comes from DataValidator instead of RecordExporter
        assert "Not enough records found for the given workload" in str(exc_info.value)

        # Get job details and validate empty datasets
        job_result = get_job_details(test_db_empty_datasets["flywheel_run_id"])
        job_json = job_result.model_dump()

        # Validate datasets in job response
        assert len(job_json["datasets"]) == 0

    @pytest.mark.parametrize(
        "error_type,error_message,expected_message",
        [
            (requests.ConnectionError, "Connection refused", "Connection refused"),
            (KeyError, "'request'", "'request'"),
            (Exception, "Rate limit exceeded", "Rate limit exceeded"),
            (ValueError, "Invalid dataset format", "Invalid dataset format"),
            (RuntimeError, "Failed to create dataset", "Failed to create dataset"),
        ],
    )
    def test_create_dataset_errors(
        self,
        mock_db,
        task_result_setup,
        test_db_empty_datasets,
        mock_es_client,
        tweak_settings,
        mock_data_uploader,
        error_type,
        error_message,
        expected_message,
        mock_dms_client,
    ):
        """Test dataset creation error handling"""
        # Mock ES client to raise an error
        mock_es_client.search.side_effect = error_type(error_message)

        # Create the dataset and expect an error
        with pytest.raises(error_type) as exc_info:
            create_datasets(task_result_setup)

        assert expected_message in str(exc_info.value)

        # Get job details and validate empty datasets
        job_result = get_job_details(test_db_empty_datasets["flywheel_run_id"])
        job_json = job_result.model_dump()

        # Validate datasets in job response
        assert len(job_json["datasets"]) == 0

    def test_create_dataset_db_update_failure(
        self,
        mock_db,
        task_result_setup,
        test_db_empty_datasets,
        mock_es_client,
        tweak_settings,
        mock_data_uploader,
        sample_es_data,
        monkeypatch,
        mock_dms_client,
    ):
        """Test dataset creation when database update fails"""
        # Adjust settings to match the sample data size
        task_result_setup.job_config.data_split_config.limit = 10
        task_result_setup.job_config.data_split_config.eval_size = 5
        task_result_setup.job_config.icl_config.max_examples = 2

        mock_es_client.search.return_value = sample_es_data

        mock_data_uploader.upload_data.side_effect = Exception("Database update failed")

        # Create the dataset and expect an error_type since upload fails
        with pytest.raises(Exception) as exc_info:
            create_datasets(task_result_setup)

        # Verify the error message
        assert "Database update failed" in str(exc_info.value)

    def test_create_dataset_preserves_existing_fields(
        self,
        mock_db,
        mock_es_client,
        tweak_settings,
        mock_data_uploader,
        sample_es_data,
        monkeypatch,
        mock_dms_client,
        task_result_setup,
    ):
        """Test that create_datasets preserves existing fields in TaskResult"""
        # Adjust settings to match the sample data size
        task_result_setup.job_config.data_split_config.limit = 10
        task_result_setup.job_config.data_split_config.eval_size = 5
        task_result_setup.job_config.icl_config.max_examples = 2

        # Setup mock data
        mock_es_client.search.return_value = sample_es_data

        result = create_datasets(task_result_setup)

        result = self._convert_result_to_task_result(result)

        # Verify existing fields are preserved
        assert isinstance(result, TaskResult)
        assert result.workload_id == "test-workload"
        assert result.client_id == "test-client"
        assert result.llm_judge_config == task_result_setup.llm_judge_config
        assert result.evaluations is not None

    def test_create_dataset_with_custom_split_config(
        self,
        mock_db,
        task_result_setup,
        test_db_success,
        mock_es_client,
        tweak_settings,
        mock_data_uploader,
        sample_es_data,
        sample_split_config,
        mock_dms_client,
    ):
        """Test dataset creation with custom split configuration"""
        # Setup mock data
        mock_es_client.search.return_value = sample_es_data

        # Convert dict to DataSplitConfig
        from src.config import DataSplitConfig

        split_config = DataSplitConfig(**sample_split_config)

        # Add split config to task result (this should be different from job_config.data_split_config)
        # The job_config should remain unchanged with its original values
        task_result_setup.data_split_config = split_config

        # Create the dataset
        create_result = create_datasets(task_result_setup)
        create_result = self._convert_result_to_task_result(create_result)

        # Verify create_dataset response
        assert isinstance(create_result, TaskResult)
        assert create_result.workload_id == task_result_setup.workload_id
        assert create_result.flywheel_run_id == task_result_setup.flywheel_run_id
        assert create_result.client_id == task_result_setup.client_id
        assert create_result.error is None
        assert create_result.datasets is not None
        assert len(create_result.datasets) == 3
        assert create_result.workload_type is not None
        # Compare base DataSplitConfig fields (may be EmbeddingDataSplitConfig with defaults)
        assert create_result.data_split_config.eval_size == split_config.eval_size
        assert create_result.data_split_config.val_ratio == split_config.val_ratio
        assert create_result.data_split_config.min_total_records == split_config.min_total_records
        assert create_result.data_split_config.random_seed == split_config.random_seed
        assert create_result.data_split_config.limit == split_config.limit

        # Update mock for get_job_details to return the new datasets
        updated_flywheel_run = mock_db.flywheel_runs.find_one.return_value.copy()
        updated_flywheel_run["datasets"] = [
            {
                "name": f"flywheel-eval-{task_result_setup.workload_id}",
                "num_records": 50,
                "nmp_uri": "uri_1",
            },
            {
                "name": f"flywheel-icl-{task_result_setup.workload_id}",
                "num_records": 50,
                "nmp_uri": "uri_2",
            },
            {
                "name": f"flywheel-train-{task_result_setup.workload_id}",
                "num_records": 50,
                "nmp_uri": "uri_3",
            },
        ]
        mock_db.flywheel_runs.find_one.return_value = updated_flywheel_run

        # Get job details and validate
        job_result = get_job_details(test_db_success["flywheel_run_id"])
        job_json = job_result.model_dump()

        # Validate datasets in job response
        assert len(job_json["datasets"]) == 3
        for dataset in job_json["datasets"]:
            assert "flywheel-" in dataset["name"]
            assert task_result_setup.workload_id in dataset["name"]
            assert dataset["num_records"] == 50
            assert dataset["nmp_uri"] in ["uri_1", "uri_2", "uri_3"]

    def test_create_dataset_invalid_split_config(
        self,
        mock_db,
        task_result_setup,
        test_db_success,
        mock_es_client,
        tweak_settings,
        mock_data_uploader,
        sample_es_data,
        mock_dms_client,
    ):
        """Test dataset creation with invalid split configuration"""
        # Setup mock data with only 10 records
        mock_es_data = {
            "_scroll_id": "scroll123",
            "hits": {
                "hits": [
                    {
                        "_source": {
                            "request": {"messages": [{"role": "user", "content": f"Message {i}"}]},
                            "response": {
                                "choices": [
                                    {
                                        "message": {
                                            "role": "assistant",
                                            "content": f"Response {i}",
                                            "tool_calls": None,
                                        }
                                    }
                                ]
                            },
                            "client_id": "test_client",
                            "workload_id": "test_workload",
                            "timestamp": "2024-01-01T00:00:00Z",
                        }
                    }
                    for i in range(10)
                ]
            },
        }
        mock_es_client.search.return_value = mock_es_data

        # Create an invalid split config (eval_size too large)
        invalid_split_config = DataSplitConfig(
            eval_size=20,  # Too large for 10 records
            val_ratio=0.3,
            min_total_records=5,  # Set lower than total records to isolate eval_size validation
            random_seed=42,
            limit=100,
        )

        # Add invalid split config to task result
        task_result_setup.data_split_config = invalid_split_config

        # Expect an error when creating dataset
        with pytest.raises(ValueError) as exc_info:
            create_datasets(task_result_setup)

        assert "eval_size cannot be larger than" in str(exc_info.value)

    def test_create_dataset_preserves_split_config(
        self,
        mock_db,
        mock_es_client,
        tweak_settings,
        mock_data_uploader,
        sample_es_data,
        sample_split_config,
        mock_dms_client,
        job_config,
    ):
        """Test that create_datasets preserves the data_split_config in TaskResult"""
        # Setup mock data
        mock_es_client.search.return_value = sample_es_data

        # Convert dict to DataSplitConfig
        split_config = DataSplitConfig(**sample_split_config)

        task_result = TaskResult(
            workload_id="test-workload",
            flywheel_run_id=str(ObjectId()),
            client_id="test-client",
            llm_judge_config=LLMJudgeConfig(
                **{"deployment_type": "remote", "model_name": "test-judge"}
            ),
            error=None,
            evaluations={},
            data_split_config=split_config,
            job_config=job_config,
        )

        result = create_datasets(task_result)
        result = self._convert_result_to_task_result(result)

        # Verify existing fields are preserved
        assert isinstance(result, TaskResult)
        assert result.workload_id == "test-workload"
        assert result.client_id == "test-client"
        assert result.llm_judge_config == task_result.llm_judge_config
        assert result.evaluations is not None
        # Compare base DataSplitConfig fields (may be EmbeddingDataSplitConfig with defaults)
        assert result.data_split_config.eval_size == split_config.eval_size
        assert result.data_split_config.val_ratio == split_config.val_ratio
        assert result.data_split_config.min_total_records == split_config.min_total_records
        assert result.data_split_config.random_seed == split_config.random_seed
        assert result.data_split_config.limit == split_config.limit
