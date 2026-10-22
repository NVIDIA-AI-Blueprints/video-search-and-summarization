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
"""Unit tests for initialize_workflow.

These tests focus on the different code-paths involved when initializing a workflow.
The scenarios covered are:

Parameter flow test cases:
1. Successful parameter population in TaskResult
2. Parameter validation and error handling
3. Integration with downstream tasks

llm_as_judge_config Setup:
1. Local judge configuration - no outbound request should be issued.
2. Remote judge happy-path - successful health-check call.
3. Remote judge with missing critical configuration values.
4. validation of mandatory arguments

NIMs setup:
1. Single NIM
2. Multiple NIMs
3. No NIMs
In each of these cases verify the result parameters are setup correctly.
"""

import unittest
import unittest.mock
from unittest.mock import patch

import pytest
from bson import ObjectId

from src.api.models import LLMJobConfig, TaskResult
from src.api.schemas import DeploymentStatus, NIMRunStatus
from src.backends.nmp.llm_as_judge import LLMAsJudge
from src.config import DataSplitConfig, ICLConfig, LLMJudgeConfig, NIMConfig, settings
from src.tasks.tasks import initialize_workflow
from tests.unit.tasks.conftest import (
    get_default_job_config,
    get_default_nmp_config,
)


class TestInitializeWorkflow(unittest.TestCase):
    def setUp(self):
        # Common setup for all tests
        self.flywheel_run_id = str(ObjectId())
        self.workload_id = "test-workload"
        self.client_id = "test-client"

        # Mock the LLMAsJudge class and its config
        self.mock_llm_judge_config_local = LLMJudgeConfig(
            model_name="local-judge-model",
            deployment_type="local",
            context_length=8192,
            tag="1.0.0",
            gpus=1,
            pvc_size="10Gi",
            customization_enabled=False,
        )

        self.mock_llm_judge_config_remote = LLMJudgeConfig(
            model_name="remote-judge-model",
            deployment_type="remote",
            url="https://api.example.com/v1/chat/completions",
            api_key="test-api-key",
        )

        # Set up NIM configs for testing
        self.single_nim_config = [
            NIMConfig(
                model_name="single-nim-model",
                context_length=8192,
                tag="1.0.0",
                gpus=1,
                pvc_size="10Gi",
                customization_enabled=False,
            )
        ]

        self.multiple_nim_configs = [
            NIMConfig(
                model_name="nim-model-1",
                context_length=8192,
                tag="1.0.0",
                gpus=1,
                pvc_size="10Gi",
                customization_enabled=False,
            ),
            NIMConfig(
                model_name="nim-model-2",
                context_length=4096,
                tag="2.0.0",
                gpus=2,
                pvc_size="20Gi",
                customization_enabled=False,
            ),
        ]

        self.no_nim_configs = []

    def _convert_result_to_task_result(self, result):
        """Helper method to convert result to TaskResult if it's a dictionary."""
        if isinstance(result, dict):
            # Only add defaults if they're not already present
            if "nmp_config" not in result:
                result["nmp_config"] = get_default_nmp_config()
            if "job_config" not in result:
                result["job_config"] = get_default_job_config()
            if "mlflow_config" not in result:
                result["mlflow_config"] = {"enabled": False}
            if "job_type" not in result:
                result["job_type"] = "llm"
            return TaskResult(**result)
        return result

    def _create_job_config(self, nims_data):
        """Helper method to create a job_config for testing."""

        split_config = DataSplitConfig(
            eval_size=10,
            val_ratio=0.1,
            min_total_records=50,
            limit=100,
            parse_function_arguments=True,
        )
        icl_config = ICLConfig(
            max_examples=3, min_examples=1, example_selection="uniform_distribution"
        )
        job_config = (
            settings.job_configs["llm_job"]
            .model_copy(update={"data_split_config": split_config, "icl_config": icl_config})
            .model_dump()
        )
        job_config["nims"] = nims_data
        return job_config

    @patch("src.tasks.tasks.db_manager")
    def test_parameter_population_success(self, mock_task_db_manager):
        """Test that initialize_workflow properly populates parameters in TaskResult."""
        settings.system_config.llm_judge_config = self.mock_llm_judge_config_local
        mock_task_db_manager.create_llm_judge_run.return_value = ObjectId()
        mock_task_db_manager.create_nim_run.return_value = ObjectId()

        result = initialize_workflow(
            workload_id=self.workload_id,
            flywheel_run_id=self.flywheel_run_id,
            client_id=self.client_id,
            job_config=get_default_job_config(self.single_nim_config),
            job_type="llm_job",
            data_split_config=get_default_job_config(self.single_nim_config)["data_split_config"],
        )
        result = self._convert_result_to_task_result(result)

        self.assertIsInstance(result, TaskResult)
        self.assertEqual(result.workload_id, self.workload_id)
        self.assertEqual(result.flywheel_run_id, self.flywheel_run_id)
        self.assertEqual(result.client_id, self.client_id)
        self.assertIsNone(result.error)
        self.assertEqual(result.llm_judge_config, self.mock_llm_judge_config_local)
        self.assertEqual(result.datasets, {})

        # Verify database operations
        mock_task_db_manager.create_llm_judge_run.assert_called_once()
        mock_task_db_manager.create_nim_run.assert_called_once()

    @patch("src.tasks.tasks.db_manager")
    def test_success_case(self, mock_task_db_manager):
        """Test successful initialization with valid previous results."""
        settings.system_config.llm_judge_config = self.mock_llm_judge_config_local
        mock_task_db_manager.create_llm_judge_run.return_value = ObjectId()
        mock_task_db_manager.create_nim_run.return_value = ObjectId()

        # Create a minimal job_config for testing
        job_config = self._create_job_config(
            [{"model_name": "test-model", "model_type": "llm", "context_length": 4096}]
        )

        result = initialize_workflow(
            workload_id=self.workload_id,
            flywheel_run_id=self.flywheel_run_id,
            client_id=self.client_id,
            job_config=job_config,
            job_type="llm_job",
            data_split_config=job_config["data_split_config"],
        )
        result = self._convert_result_to_task_result(result)

        self.assertTrue(hasattr(result, "workload_id"))
        self.assertTrue(hasattr(result, "flywheel_run_id"))
        self.assertTrue(hasattr(result, "client_id"))
        self.assertIsNotNone(result.workload_id)
        self.assertIsNotNone(result.flywheel_run_id)
        self.assertIsNotNone(result.client_id)

    @patch("src.tasks.tasks.db_manager")
    def test_local_judge_configuration(self, mock_task_db_manager):
        """Test with local judge configuration (no outbound request)."""
        settings.system_config.llm_judge_config = self.mock_llm_judge_config_local
        mock_task_db_manager.create_llm_judge_run.return_value = ObjectId()
        mock_task_db_manager.create_nim_run.return_value = ObjectId()

        # Create a minimal job_config for testing
        job_config = self._create_job_config(
            [{"model_name": "test-model", "model_type": "llm", "context_length": 4096}]
        )

        result = initialize_workflow(
            workload_id=self.workload_id,
            flywheel_run_id=self.flywheel_run_id,
            client_id=self.client_id,
            job_config=job_config,
            job_type="llm_job",
            data_split_config=job_config["data_split_config"],
        )
        result = self._convert_result_to_task_result(result)

        self.assertIsNotNone(result)
        self.assertEqual(result.llm_judge_config, self.mock_llm_judge_config_local)
        mock_task_db_manager.create_llm_judge_run.assert_called_once_with(unittest.mock.ANY)
        # Extract the LLMJudgeRun from the call arguments
        llm_judge_run = mock_task_db_manager.create_llm_judge_run.call_args[0][0]
        self.assertEqual(llm_judge_run.deployment_status, DeploymentStatus.CREATED)
        self.assertEqual(llm_judge_run.model_name, "local-judge-model")
        self.assertEqual(llm_judge_run.deployment_type, "local")

    @patch("src.tasks.tasks.db_manager")
    def test_remote_judge_configuration(self, mock_task_db_manager):
        """Test with remote judge configuration."""
        self.mock_llm_judge_config_remote.url = None
        mock_task_db_manager.create_llm_judge_run.return_value = ObjectId()
        mock_task_db_manager.create_nim_run.return_value = ObjectId()

        # Create a minimal job_config for testing
        job_config = self._create_job_config(
            [{"model_name": "test-model", "model_type": "llm", "context_length": 4096}]
        )

        with pytest.raises(Exception) as e:
            llm_as_judge = LLMAsJudge()
            llm_as_judge.config = self.mock_llm_judge_config_remote
            settings.system_config.llm_judge_config = self.mock_llm_judge_config_remote
            llm_as_judge.validate_llm_judge_availability()
            result = initialize_workflow(
                workload_id=self.workload_id,
                flywheel_run_id=self.flywheel_run_id,
                client_id=self.client_id,
                job_config=job_config,
                job_type="llm_job",
                data_split_config=job_config["data_split_config"],
            )

            assert llm_as_judge.config == self.mock_llm_judge_config_remote
            assert result.llm_judge_config == self.mock_llm_judge_config_remote

        assert "missing 'url'" in str(e.value)

    @patch("src.tasks.tasks.db_manager")
    def test_single_nim(self, mock_task_db_manager):
        """Test with a single NIM configuration."""
        settings.system_config.llm_judge_config = self.mock_llm_judge_config_local
        llm_judge_run_id = ObjectId()
        nim_run_id = ObjectId()
        mock_task_db_manager.create_llm_judge_run.return_value = llm_judge_run_id
        mock_task_db_manager.create_nim_run.return_value = nim_run_id

        # Create a job_config with single NIM for testing
        job_config = self._create_job_config(
            [
                {
                    "model_name": "single-nim-model",
                    "model_type": "llm",
                    "context_length": 8192,
                    "tag": "1.0.0",
                    "gpus": 1,
                    "pvc_size": "10Gi",
                    "customization_enabled": False,
                }
            ]
        )

        result = initialize_workflow(
            workload_id=self.workload_id,
            flywheel_run_id=self.flywheel_run_id,
            client_id=self.client_id,
            job_config=job_config,
            job_type="llm_job",
            data_split_config=job_config["data_split_config"],
        )

        self.assertIsNotNone(result)
        mock_task_db_manager.create_llm_judge_run.assert_called_once()
        # Should create exactly one NIM run
        mock_task_db_manager.create_nim_run.assert_called_once()
        # Verify NIM run details
        nim_run = mock_task_db_manager.create_nim_run.call_args[0][0]
        self.assertEqual(nim_run.model_name, "single-nim-model")
        self.assertEqual(nim_run.flywheel_run_id, ObjectId(self.flywheel_run_id))
        self.assertEqual(nim_run.status, NIMRunStatus.PENDING)

    @patch("src.tasks.tasks.db_manager")
    def test_multiple_nims(self, mock_task_db_manager):
        """Test with multiple NIM configurations."""
        settings.system_config.llm_judge_config = self.mock_llm_judge_config_local
        llm_judge_run_id = ObjectId()
        nim_run_id1 = ObjectId()
        nim_run_id2 = ObjectId()
        mock_task_db_manager.create_llm_judge_run.return_value = llm_judge_run_id
        mock_task_db_manager.create_nim_run.side_effect = [nim_run_id1, nim_run_id2]

        # Create a job_config with multiple NIMs for testing
        job_config = self._create_job_config(
            [
                {
                    "model_name": "nim-model-1",
                    "model_type": "llm",
                    "context_length": 8192,
                    "tag": "1.0.0",
                    "gpus": 1,
                    "pvc_size": "10Gi",
                    "customization_enabled": False,
                },
                {
                    "model_name": "nim-model-2",
                    "model_type": "llm",
                    "context_length": 4096,
                    "tag": "2.0.0",
                    "gpus": 2,
                    "pvc_size": "20Gi",
                    "customization_enabled": False,
                },
            ]
        )

        result = initialize_workflow(
            workload_id=self.workload_id,
            flywheel_run_id=self.flywheel_run_id,
            client_id=self.client_id,
            job_config=job_config,
            job_type="llm_job",
            data_split_config=job_config["data_split_config"],
        )

        self.assertIsNotNone(result)
        mock_task_db_manager.create_llm_judge_run.assert_called_once()
        # Should create two NIM runs
        self.assertEqual(mock_task_db_manager.create_nim_run.call_count, 2)

        # Verify first NIM run details
        first_nim_run = mock_task_db_manager.create_nim_run.call_args_list[0][0][0]
        self.assertEqual(first_nim_run.model_name, "nim-model-1")
        self.assertEqual(first_nim_run.flywheel_run_id, ObjectId(self.flywheel_run_id))
        self.assertEqual(first_nim_run.status, NIMRunStatus.PENDING)

        # Verify second NIM run details
        second_nim_run = mock_task_db_manager.create_nim_run.call_args_list[1][0][0]
        self.assertEqual(second_nim_run.model_name, "nim-model-2")
        self.assertEqual(second_nim_run.flywheel_run_id, ObjectId(self.flywheel_run_id))
        self.assertEqual(second_nim_run.status, NIMRunStatus.PENDING)

    @patch("src.tasks.tasks.db_manager")
    def test_no_nims(self, mock_task_db_manager):
        """Test that initialization fails with no NIM configurations."""

        # LLMJobConfig now requires at least one NIM
        # Test that it raises a validation error
        with pytest.raises(ValueError, match="At least one NIM must be configured"):
            LLMJobConfig(
                nims=[],
                data_split_config={"limit": 100},
                icl_config={
                    "max_context_length": 8192,
                    "reserved_tokens": 2048,
                    "max_examples": 1,
                    "min_examples": 1,
                    "example_selection": "uniform_distribution",
                },
                training_config={
                    "training_type": "sft",
                    "finetuning_type": "lora",
                    "epochs": 1,
                    "batch_size": 8,
                    "learning_rate": 0.0001,
                },
            )

    @patch("src.tasks.tasks.db_manager")
    def test_no_nims_workflow_initialization(self, mock_task_db_manager):
        """Test that workflow initialization fails gracefully with invalid config."""
        job_config = get_default_job_config([])  # Empty NIMs list

        with pytest.raises(ValueError, match="At least one NIM must be configured"):
            initialize_workflow(
                workload_id=self.workload_id,
                flywheel_run_id=self.flywheel_run_id,
                client_id=self.client_id,
                job_config=job_config,
                job_type="llm_job",
                data_split_config=job_config["data_split_config"],
            )


class TestInitializationErrorPaths(unittest.TestCase):
    """Test error handling paths in initialization."""

    def setUp(self):
        """Set up test fixtures."""
        self.workload_id = "test-workload"
        self.flywheel_run_id = str(ObjectId())
        self.client_id = "test-client"

    @patch("src.tasks.tasks.db_manager")
    def test_none_job_config(self, mock_task_db_manager):
        """Test that initialization fails with None job_config."""
        with pytest.raises(ValueError, match="job_config is required"):
            initialize_workflow(
                workload_id=self.workload_id,
                flywheel_run_id=self.flywheel_run_id,
                client_id=self.client_id,
                job_config=None,
                job_type="llm_job",
            )

    @patch("src.tasks.tasks.db_manager")
    def test_unsupported_job_type(self, mock_task_db_manager):
        """Test that initialization fails with unsupported job type."""
        job_config = get_default_job_config([])

        with pytest.raises(ValueError, match="Unsupported job config type"):
            initialize_workflow(
                workload_id=self.workload_id,
                flywheel_run_id=self.flywheel_run_id,
                client_id=self.client_id,
                job_config=job_config,
                job_type="invalid_job_type",
            )

    @patch("src.tasks.tasks.db_manager")
    def test_embedding_job_with_data_split(self, mock_task_db_manager):
        """Test embedding job initialization with data_split_config."""
        from src.config import EmbeddingDataSplitConfig, EmbeddingJobConfig, TrainingConfig

        # Create embedding job config
        embedding_config = EmbeddingJobConfig(
            nims=[
                NIMConfig(
                    model_name="embedding-model",
                    tag="latest",
                    context_length=512,
                    gpus=1,
                    pvc_size="10Gi",
                    registry_base="nvcr.io/nim",
                    model_type="embedding",
                )
            ],
            data_split_config=EmbeddingDataSplitConfig(limit=100),
            training_config=TrainingConfig(
                training_type="sft",
                finetuning_type="lora",
                epochs=1,
                batch_size=8,
                learning_rate=0.0001,
            ),
        )

        # Mock DB manager
        mock_task_db_manager.create_nim_run.return_value = ObjectId()

        result = initialize_workflow(
            workload_id=self.workload_id,
            flywheel_run_id=self.flywheel_run_id,
            client_id=self.client_id,
            job_config=embedding_config.model_dump(),
            job_type="embedding_job",
            data_split_config={"limit": 50},
        )

        # Convert result if it's a dict
        if isinstance(result, dict):
            result = TaskResult(**result)

        # Verify result
        assert result.workload_id == self.workload_id
        assert result.data_split_config is not None
        # For embedding jobs, no LLM judge config should be created
        assert result.llm_judge_config is None
